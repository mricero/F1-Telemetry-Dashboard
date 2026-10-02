"""scripts/convert_legacy_replay.py turns a trusted pickle into a Parquet replay (REPO-24)."""

import importlib.util
import pickle
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "convert_legacy_replay", PROJECT_ROOT / "scripts" / "convert_legacy_replay.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr("data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})())
    from data.source_manager import DataSourceManager

    return DataSourceManager(replay_dir=str(tmp_path / "replays"))


def _legacy(path: Path) -> Path:
    """The shape pre-HIST-02 save_replay wrote: a schema envelope around plain records."""
    payload = {
        "schema": 3,
        "app": "f1-telemetry-dashboard",
        "saved_at": "2026-07-08T07:22:30",
        "data": {
            "session_info": {"gp": "Abu Dhabi Grand Prix", "year": 2025, "session_type": "R"},
            "laps": [{"Driver": "VER", "LapNumber": 1, "LapTime": 88.1}],
            "telemetry": {"VER": {"Distance": [0.0, 5.0], "Speed": [250.0, 260.0]}},
            "location": {"VER": {"X": [1.0], "Y": [2.0], "Z": [0.0]}},
            "source": "fastf1",
            "is_live": False,
        },
    }
    path.write_bytes(pickle.dumps(payload))
    return path


class TestConvert:
    def test_the_name_drops_the_old_timestamp(self):
        name = _script().replay_name(Path("Abu Dhabi Grand Prix_R_20260708_072230.pkl"))

        assert name == "Abu Dhabi Grand Prix_R"

    def test_the_converted_replay_loads_without_pickle(self, manager, tmp_path):
        legacy = _legacy(tmp_path / "Abu Dhabi Grand Prix_R_20260708_072230.pkl")

        target = _script().convert(legacy, manager=manager)

        assert target.is_dir()
        assert not list(target.rglob("*.pkl"))
        loaded = manager._load_replay(str(target))
        assert loaded["session_info"]["gp"] == "Abu Dhabi Grand Prix"
        assert loaded["laps"]["LapNumber"].tolist() == [1]
        pd.testing.assert_series_equal(
            loaded["telemetry"]["VER"]["Speed"], pd.Series([250.0, 260.0], name="Speed")
        )
        assert loaded["source"] == "replay"

    def test_load_time_keys_are_not_persisted(self, manager, tmp_path):
        import json

        target = _script().convert(_legacy(tmp_path / "x.pkl"), manager=manager)
        meta = json.loads((target / manager.META_FILE).read_text("utf-8"))

        assert not {"source", "is_live", "live_client"} & set(meta["values"])


class TestCommandLine:
    def test_it_refuses_without_trust(self, tmp_path, capsys):
        legacy = _legacy(tmp_path / "x.pkl")

        assert _script().main([str(legacy)]) == 2
        assert "--trust" in capsys.readouterr().err

    def test_a_missing_file_is_an_error(self, tmp_path):
        assert _script().main([str(tmp_path / "absent.pkl"), "--trust"]) == 1

    def test_it_moves_the_original_aside(self, monkeypatch, tmp_path):
        module = _script()
        monkeypatch.setattr(module, "convert", lambda path, replay_dir: tmp_path / "out")
        legacy = _legacy(tmp_path / "x.pkl")

        code = module.main([str(legacy), "--trust", "--move-original-to", str(tmp_path / "aside")])

        assert code == 0
        assert not legacy.exists()
        assert (tmp_path / "aside" / "x.pkl").is_file()
