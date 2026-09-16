"""Tests for data.source_manager: replay schema, circuit mapping,
live lap numbers, GPS distances and adapter buffer caps."""

import pickle
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from data.live_adapter import SignalRLiveAdapter


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr("data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})())
    from data.source_manager import DataSourceManager

    mgr = DataSourceManager()
    mgr.replay_dir = tmp_path  # keep real ./replay_sessions untouched
    return mgr


def _sample_session() -> dict:
    return {
        "session_info": {"year": 2024, "gp": "Bahrain", "session_type": "R"},
        "telemetry": {"HAM": pd.DataFrame({"Distance": [0.0, 10.0], "Speed": [280.5, 300.0]})},
        "laps": pd.DataFrame(
            {"Driver": ["HAM"], "LapNumber": [1], "LapTime": [timedelta(seconds=91.2)]}
        ),
        "stints": pd.DataFrame({"DriverAcronym": ["HAM"], "Compound": ["SOFT"]}),
        "location": {"HAM": pd.DataFrame({"X": [1.0], "Y": [2.0]})},
        "weather": pd.DataFrame({"AirTemp": [22]}),
        "drivers": pd.DataFrame({"driver_number": ["44"], "name_acronym": ["HAM"]}),
        "source": "fastf1",
        "is_live": False,
    }


class TestReplaySchema:
    def test_roundtrip_with_schema_header(self, manager):
        path = manager.save_replay(_sample_session(), "Bahrain_R")
        raw = pickle.load(open(path, "rb"))
        assert raw["schema"] == manager.REPLAY_SCHEMA_VERSION
        assert "saved_at" in raw and "data" in raw

    def test_load_roundtrip_restores_frames(self, manager):
        path = manager.save_replay(_sample_session(), "Bahrain_R")
        loaded = manager._load_replay(path)
        assert loaded["source"] == "replay"
        assert loaded["telemetry"]["HAM"]["Speed"].iloc[1] == 300.0
        assert not loaded["laps"].empty
        assert "live_client" not in loaded

    def test_legacy_bare_pickle_still_loads(self, manager):
        legacy = {
            k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v)
            for k, v in _sample_session().items()
        }
        legacy["telemetry"] = {k: v.to_dict("records") for k, v in legacy["telemetry"].items()}
        legacy["location"] = {k: v.to_dict("records") for k, v in legacy["location"].items()}
        target = manager.replay_dir / "legacy.pkl"
        with open(target, "wb") as f:
            pickle.dump(legacy, f)

        loaded = manager._load_replay(str(target))
        assert loaded["source"] == "replay"
        assert isinstance(loaded["laps"], pd.DataFrame)

    def test_future_schema_rejected_clearly(self, manager):
        payload = {"schema": 9999, "saved_at": "x", "data": {}}
        target = manager.replay_dir / "future.pkl"
        with open(target, "wb") as f:
            pickle.dump(payload, f)
        with pytest.raises(ValueError, match="schema"):
            manager._load_replay(str(target))


class TestCircuitMapping:
    def test_known_names(self):
        from data.source_manager import DataSourceManager as M

        assert M._gp_to_circuit_short("Bahrain Grand Prix") == "Sakhir"
        assert M._gp_to_circuit_short("Abu Dhabi Grand Prix") == "Yas Marina"
        assert M._gp_to_circuit_short("United States Grand Prix") == "Austin"

    def test_suffix_and_none(self):
        from data.source_manager import DataSourceManager as M

        assert M._gp_to_circuit_short(None) is None
        assert M._gp_to_circuit_short("Miami GP") == "Miami"
        assert M._gp_to_circuit_short("Unknown Circuit") == "Unknown Circuit"


class TestLiveLapNumbers:
    def test_numberoflaps_drives_lap_rows(self, manager):
        timing = [
            {
                "driver_number": "44",
                "timestamp": f"t{i}",
                # lap counter climbs over time; LastLapTime lands after each lap
            }
            for i in range(6)
        ]
        values = [
            ("NumberOfLaps", None),
            ("NumberOfLaps", 1),
            ("LastLapTime_Value", "1:33.00"),
            ("NumberOfLaps", 2),
            ("LastLapTime_Value", "1:32.50"),
        ]
        for i, (key, val) in enumerate(values):
            timing[i][key] = val
            timing[i]["Sectors_1_Value"] = "30.00"

        df = manager._laps_from_timing(pd.DataFrame(timing), {"44": "HAM"})
        laps = df[df["Driver"] == "HAM"]
        done = laps[laps["LapTime"].notna()]
        assert sorted(done["LapNumber"]) == [1, 2]
        assert set(done["LapTime"]) == {"1:33.00", "1:32.50"}
        in_progress = laps[laps["LapTime"].isna()]
        assert list(in_progress["LapNumber"]) == [3]

    def test_fallback_without_numberoflaps(self, manager):
        timing = [
            {
                "driver_number": "44",
                "timestamp": "t",
                "BestLapTime_Value": "1:31.20",
                "Sectors_1_Value": "31.10",
            }
        ]
        df = manager._laps_from_timing(pd.DataFrame(timing), {"44": "HAM"})
        assert df["LapTime"].iloc[0] == "1:31.20"


class TestGpsDistances:
    @staticmethod
    def _prime_driver_list(adapter):
        adapter._data_buffer["DriverList"] = [
            {
                "RacingNumber": "44",
                "Tla": "HAM",
                "TeamColour": "00d2be",
                "FirstName": "Lewis",
                "LastName": "Hamilton",
                "TeamName": "Ferrari",
            }
        ]

    def _prime_gps_stream(self, adapter, n=40):
        # Car moves +10 X-units per second; timestamps are ISO so they parse.
        self._prime_driver_list(adapter)
        adapter._data_buffer["CarData.z"] = [
            {
                "DriverNo": "44",
                "Utc": f"2026-05-01T12:00:{i:02d}Z",
                "rpm": 11000,
                "speed": 250 + i,
                "n_gear": 7,
                "throttle": 90,
                "brake": 0,
                "drs": 8,
            }
            for i in range(n)
        ]
        adapter._data_buffer["Position.z"] = [
            {
                "DriverNo": "44",
                "Utc": f"2026-05-01T12:00:{i:02d}Z",
                "X": i * 100.0,
                "Y": 0.0,
                "Z": 0,
            }
            for i in range(n)
        ]

    def test_telemetry_distance_from_trajectory(self, manager):
        self._prime_gps_stream(manager.live)
        snap = manager.poll_live_data()
        tel = snap["telemetry"]["HAM"]
        dist = tel["Distance"].to_numpy()
        assert np.all(np.diff(dist) >= -1e-6)  # monotonic metres
        # 40 samples 100 position-units apart = 3900 units = 390 m (1/10 m feed).
        assert dist[-1] == pytest.approx(390.0)
        assert "Distance" in snap["location"]["HAM"].columns

    def test_gps_distance_is_metres_not_decimetres(self):
        """LIVE-03: Position.z X/Y/Z are in 1/10 m, exactly like FastF1's."""
        from data.live_adapter import LiveDataProcessor

        # A straight 10 000-unit run along X is 1 000 m of track.
        pos = pd.DataFrame(
            {
                "timestamp": [f"2026-05-01T12:00:{i:02d}Z" for i in range(11)],
                "X": np.linspace(0.0, 10000.0, 11),
                "Y": np.zeros(11),
            }
        )
        dist = LiveDataProcessor.distance_at(pos, pos["timestamp"])

        assert dist is not None
        assert dist[-1] == pytest.approx(1000.0)

    def test_live_and_fastf1_gps_distance_agree(self):
        """Both paths read the same feed, so both must use the same scale."""
        from data.fastf1_adapter import FastF1Adapter
        from data.live_adapter import LiveDataProcessor

        xy = {"X": np.array([0.0, 300.0, 300.0]), "Y": np.array([0.0, 0.0, 400.0])}
        pos = pd.DataFrame({"timestamp": [f"2026-05-01T12:00:0{i}Z" for i in range(3)], **xy})

        live = LiveDataProcessor.distance_at(pos, pos["timestamp"])
        historical = FastF1Adapter.distance_from_positions(pd.DataFrame(xy))

        assert np.allclose(live, historical)
        assert live[-1] == pytest.approx(70.0)  # (300 + 400) units / 10

    def test_falls_back_without_gps(self, manager):
        self._prime_driver_list(manager.live)
        manager.live._data_buffer["CarData.z"] = [
            {
                "DriverNo": "44",
                "Utc": f"t{i}",
                "rpm": 1,
                "speed": 200,
                "n_gear": 5,
                "throttle": 10,
                "brake": 0,
                "drs": 0,
            }
            for i in range(20)
        ]
        snap = manager.poll_live_data()
        tel = snap["telemetry"]["HAM"]
        assert np.allclose(tel["Distance"], np.arange(20) * 10)


class TestBufferCap:
    def test_list_overflow_drops_oldest(self):
        a = SignalRLiveAdapter(buffer_limit=100)
        a._buffer_topic("TimingData", [{"n": i} for i in range(250)])
        buf = a.get_buffered_data("TimingData")
        assert len(buf) == 100
        assert buf[0]["n"] == 150 and buf[-1]["n"] == 249

    def test_single_append_respects_cap(self):
        a = SignalRLiveAdapter(buffer_limit=100)
        for i in range(105):
            a._buffer_topic("WeatherData", {"n": i})
        assert [r["n"] for r in a.get_buffered_data("WeatherData")][:3] == [5, 6, 7]
        assert len(a.get_buffered_data("WeatherData")) == 100

    def test_minimum_limit_enforced(self):
        a = SignalRLiveAdapter(buffer_limit=-5)
        assert a.buffer_limit >= 100


class TestReplayFileResolution:
    """HIST-01: the sidebar hands the manager a bare filename, not a path."""

    def test_get_session_data_accepts_a_name_from_get_available_replays(self, manager):
        manager.save_replay(_sample_session(), "Bahrain_R")
        name = manager.get_available_replays()[0]
        assert "/" not in name and "\\" not in name  # the selector's value

        loaded = manager.get_session_data(source="replay", replay_file=name)

        assert loaded["source"] == "replay"
        assert loaded["session_info"]["gp"] == "Bahrain"

    def test_full_paths_still_work(self, manager):
        path = manager.save_replay(_sample_session(), "Bahrain_R")
        loaded = manager.get_session_data(source="replay", replay_file=path)
        assert loaded["source"] == "replay"

    def test_missing_replay_reports_the_name_not_a_bare_oserror(self, manager):
        with pytest.raises(FileNotFoundError, match="nope.pkl"):
            manager.get_session_data(source="replay", replay_file="nope.pkl")

    def test_path_traversal_is_confined_to_the_replay_dir(self, manager, tmp_path):
        outside = tmp_path.parent / "outside.pkl"
        outside.write_bytes(pickle.dumps({"session_info": {}}))

        with pytest.raises(FileNotFoundError):
            manager.get_session_data(source="replay", replay_file="../outside.pkl")


class TestMostRecentCompletedRace:
    """HIST-04: 'Auto' must land on the latest finished round, not 2025."""

    @staticmethod
    def _stub_schedule(manager, schedule):
        manager.fastf1 = type(
            "Stub",
            (),
            {"get_available_sessions": staticmethod(lambda *a, **kw: schedule)},
        )()

    def test_picks_the_latest_round_not_the_last_row(self, manager, monkeypatch):
        monkeypatch.setattr(
            "data.fastf1_adapter._utcnow", lambda: pd.Timestamp("2026-09-16", tz="UTC")
        )
        # Seasons are concatenated newest-first, so the last row is a 2025 race.
        self._stub_schedule(
            manager,
            pd.DataFrame(
                {
                    "Year": [2026, 2025],
                    "EventName": ["Monza", "Abu Dhabi"],
                    "EventFormat": ["conventional"] * 2,
                    "EventDate": [
                        pd.Timestamp("2026-09-06", tz="UTC"),
                        pd.Timestamp("2025-12-07", tz="UTC"),
                    ],
                    "Session5DateUtc": [
                        pd.Timestamp("2026-09-06T13:00:00"),
                        pd.Timestamp("2025-12-07T13:00:00"),
                    ],
                }
            ),
        )

        recent = manager._get_most_recent_completed_race()

        assert recent == {"year": 2026, "gp": "Monza", "session_type": "R"}

    def test_falls_back_to_config_when_the_schedule_is_unavailable(self, manager):
        from config import config

        self._stub_schedule(manager, pd.DataFrame())

        recent = manager._get_most_recent_completed_race()

        assert recent == {
            "year": config.default_year,
            "gp": config.default_gp,
            "session_type": config.default_session,
        }
