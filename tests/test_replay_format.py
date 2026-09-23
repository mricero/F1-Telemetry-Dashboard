"""Replays are data, not code (IMPROVEMENTS.md HIST-02).

A replay is the one artefact users share, and `pickle.load` on a shared file
executes whatever it contains. The format is a directory of Parquet tables
plus a JSON manifest; the old pickles still load, but only when the caller
says it trusts the file.
"""

import json
import pickle
from datetime import timedelta

import pandas as pd
import pytest


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr("data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})())
    from data.source_manager import DataSourceManager

    return DataSourceManager(replay_dir=str(tmp_path))


def _session() -> dict:
    """A session dict exercising the dtypes the contract actually carries."""
    return {
        "session_info": {"year": 2026, "gp": "Italian Grand Prix", "session_type": "R"},
        "telemetry": {
            "VER": pd.DataFrame(
                {
                    "Distance": [0.0, 10.0],
                    "Time": pd.to_timedelta([0.0, 0.4], unit="s"),
                    "Speed": [280.5, 300.0],
                    "nGear": pd.array([7, 8], dtype="Int64"),
                }
            )
        },
        "location": {"VER": pd.DataFrame({"X": [1.0], "Y": [2.0], "Z": [0.0]})},
        "laps": pd.DataFrame(
            {
                "Driver": ["VER", "HAM"],
                "LapNumber": pd.array([1, 1], dtype="Int64"),
                "LapTime": pd.to_timedelta([91.2, None], unit="s"),
                "Compound": pd.Categorical(["SOFT", "MEDIUM"]),
                "IsPitOutLap": [False, True],
            }
        ),
        "stints": pd.DataFrame({"Driver": ["VER"], "Compound": ["SOFT"], "LapCount": [12]}),
        "results": pd.DataFrame(
            {"Abbreviation": ["VER"], "Position": [1.0], "Status": ["Finished"]}
        ),
        "weather": pd.DataFrame({"Time": pd.to_timedelta([0], unit="s"), "AirTemp": [21.0]}),
        "race_control": pd.DataFrame({"Message": ["GREEN"], "Lap": [1]}),
        "compound_colors": {"SOFT": "#da291c"},
        "circuit_info": {"rotation": 12.5},
        "drivers": pd.DataFrame({"driver_number": ["1"], "name_acronym": ["VER"]}),
        "source": "fastf1",
        "is_live": False,
    }


class TestRoundTrip:
    def test_a_replay_is_a_directory_of_data_files(self, manager):
        path = manager.save_replay(_session(), "Monza_R")

        from pathlib import Path

        saved = Path(path)
        assert saved.is_dir()
        assert (saved / "meta.json").is_file()
        assert list(saved.glob("*.parquet")), "tables should be Parquet"

    def test_nothing_is_pickled(self, manager):
        from pathlib import Path

        path = Path(manager.save_replay(_session(), "Monza_R"))

        assert not list(path.rglob("*.pkl"))

    def test_every_key_survives(self, manager):
        original = _session()
        loaded = manager.get_session_data(
            source="replay", replay_file=manager.save_replay(original, "Monza_R")
        )

        assert set(original) <= set(loaded)
        assert loaded["session_info"]["gp"] == "Italian Grand Prix"
        assert loaded["compound_colors"] == {"SOFT": "#da291c"}
        assert loaded["circuit_info"] == {"rotation": 12.5}
        assert loaded["source"] == "replay"

    def test_timedelta_and_nat_survive(self, manager):
        loaded = manager.get_session_data(
            source="replay", replay_file=manager.save_replay(_session(), "Monza_R")
        )
        laps = loaded["laps"]

        assert pd.api.types.is_timedelta64_dtype(laps["LapTime"])
        assert laps["LapTime"].iloc[0] == timedelta(seconds=91.2)
        assert pd.isna(laps["LapTime"].iloc[1])

    def test_nullable_and_categorical_dtypes_survive(self, manager):
        loaded = manager.get_session_data(
            source="replay", replay_file=manager.save_replay(_session(), "Monza_R")
        )
        laps = loaded["laps"]

        assert str(laps["LapNumber"].dtype) == "Int64"
        assert isinstance(laps["Compound"].dtype, pd.CategoricalDtype)

    def test_per_driver_frames_survive(self, manager):
        loaded = manager.get_session_data(
            source="replay", replay_file=manager.save_replay(_session(), "Monza_R")
        )

        assert list(loaded["telemetry"]) == ["VER"]
        assert loaded["telemetry"]["VER"]["Speed"].iloc[1] == 300.0
        assert list(loaded["location"]) == ["VER"]

    def test_the_manifest_records_the_schema(self, manager):
        from pathlib import Path

        path = Path(manager.save_replay(_session(), "Monza_R"))
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))

        assert meta["schema"] == manager.REPLAY_SCHEMA_VERSION
        assert meta["app"] == "f1-telemetry-dashboard"
        assert "saved_at" in meta


class TestLegacyPickles:
    @staticmethod
    def _write_legacy(manager, name: str = "legacy.pkl"):
        target = manager.replay_dir / name
        payload = {
            "schema": 3,
            "app": "f1-telemetry-dashboard",
            "saved_at": "2026-01-01T00:00:00",
            "data": {
                "session_info": {"gp": "Old GP"},
                "laps": [{"Driver": "VER", "LapNumber": 1}],
                "telemetry": {},
                "location": {},
            },
        }
        with open(target, "wb") as handle:
            pickle.dump(payload, handle)
        return target

    def test_loading_one_is_refused_by_default(self, manager):
        path = self._write_legacy(manager)

        with pytest.raises(ValueError, match="allow_pickle"):
            manager.get_session_data(source="replay", replay_file=str(path))

    def test_the_refusal_explains_the_risk(self, manager):
        path = self._write_legacy(manager)

        with pytest.raises(ValueError, match="arbitrary code"):
            manager.get_session_data(source="replay", replay_file=str(path))

    def test_it_loads_when_the_caller_says_it_trusts_the_file(self, manager):
        path = self._write_legacy(manager)

        loaded = manager._load_replay(str(path), allow_pickle=True)

        assert loaded["session_info"]["gp"] == "Old GP"
        assert loaded["source"] == "replay"

    def test_available_replays_lists_both_formats(self, manager):
        self._write_legacy(manager)
        manager.save_replay(_session(), "Monza_R")

        names = manager.get_available_replays()

        assert any(name.endswith(".pkl") for name in names)
        assert any(not name.endswith(".pkl") for name in names)


class TestSchemaGuard:
    def test_a_newer_schema_is_refused(self, manager):
        from pathlib import Path

        path = Path(manager.save_replay(_session(), "Monza_R"))
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        meta["schema"] = manager.REPLAY_SCHEMA_VERSION + 1
        (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        with pytest.raises(ValueError, match="update the app"):
            manager.get_session_data(source="replay", replay_file=str(path))


class TestReplayStreams:
    """REPLAY-02: the streams the replay model reads survive a save."""

    @staticmethod
    def _with_streams() -> dict:
        session = _session()
        session["session_info"] = {
            **session["session_info"],
            "replay_clock": {"start": 1.0, "lights_out": 2.0, "end": 3.0, "step": 0.5},
            "segment_starts": [],
            "session_start": 2.0,
        }
        session["timing_stream"] = pd.DataFrame(
            {
                "Time": [2.0, 3.0],
                "Driver": ["VER", "VER"],
                "Position": pd.array([1, 1], dtype="Int64"),
                "GapToLeader": ["LAP 1", "LAP 2"],
                "GapSeconds": pd.array([0.0, None], dtype="Float64"),
            }
        )
        session["track_status"] = pd.DataFrame(
            {"Time": [0.5], "Status": ["1"], "Message": ["AllClear"]}
        )
        return session

    def test_streams_and_the_clock_round_trip(self, manager):
        loaded = manager.get_session_data(
            source="replay", replay_file=manager.save_replay(self._with_streams(), "Monza_R")
        )

        assert loaded["timing_stream"]["GapToLeader"].tolist() == ["LAP 1", "LAP 2"]
        assert str(loaded["timing_stream"]["Position"].dtype) == "Int64"
        assert loaded["track_status"]["Status"].tolist() == ["1"]
        assert loaded["session_info"]["replay_clock"]["lights_out"] == 2.0

    def test_a_schema_6_replay_loads_with_empty_defaults(self, manager):
        from pathlib import Path

        path = Path(manager.save_replay(_session(), "Monza_R"))
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        meta["schema"] = 6
        (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        loaded = manager.get_session_data(source="replay", replay_file=str(path))

        assert loaded["timing_stream"].empty
        assert loaded["track_status"].empty
        assert loaded["session_info"]["segment_starts"] == []
        assert loaded["session_info"]["replay_clock"] is None  # no positions to replay
