"""Tests for the persistent MetricsStore (fastest lap / sectors / top speed)."""

import json
from datetime import timedelta

import pandas as pd
import pytest

from processing.metrics_store import MetricsStore, _to_seconds


@pytest.fixture
def store(tmp_path):
    return MetricsStore(path=str(tmp_path / "metrics.json"))


def laps_frame(times):
    return pd.DataFrame(
        {
            "Driver": ["VER", "HAM", "LEC"],
            "LapNumber": [10, 12, 7],
            "LapTime": times,
            "Sector1Time": [timedelta(seconds=31.5), timedelta(seconds=30.9), None],
            "Sector2Time": [
                timedelta(seconds=35.2),
                timedelta(seconds=36),
                timedelta(seconds=34.8),
            ],
            "Sector3Time": [
                timedelta(seconds=25.0),
                timedelta(seconds=24.4),
                timedelta(seconds=26),
            ],
        }
    )


class TestConversions:
    def test_timedelta(self):
        assert _to_seconds(timedelta(seconds=91.5)) == pytest.approx(91.5)

    def test_string_m_s(self):
        assert _to_seconds("1:31.502") == pytest.approx(91.502)

    def test_numeric_and_none(self):
        assert _to_seconds(90.1) == 90.1
        assert _to_seconds(None) is None
        assert _to_seconds(float("nan")) is None

    def test_garbage(self):
        assert _to_seconds("nope") is None


class TestMetricsStore:
    def test_fastest_lap_timedeltas(self, store):
        store.update_laps(
            "Test S",
            laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), timedelta(seconds=93)]),
        )
        rec = store.session_records("Test S")
        assert rec["fastest_lap"]["driver"] == "HAM"
        assert rec["fastest_lap"]["seconds"] == pytest.approx(90.5)
        assert rec["fastest_lap"]["display"] == "01:30.500"

    def test_fastest_sectors(self, store):
        store.update_laps(
            "Test S", laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), None])
        )
        rec = store.session_records("Test S")
        assert rec["fastest_s1"]["driver"] == "HAM"
        assert rec["fastest_s2"]["driver"] == "LEC"
        assert rec["fastest_s3"]["driver"] == "HAM"

    def test_only_better_times_replace(self, store):
        store.update_laps("S", laps_frame([timedelta(seconds=90), None, None]))
        # A slower session must not overwrite the record
        store.update_laps("S", laps_frame([timedelta(seconds=100), None, None]))
        assert store.session_records("S")["fastest_lap"]["seconds"] == 90.0

    def test_live_string_laptimes(self, store):
        df = pd.DataFrame(
            {
                "Driver": ["ALB"],
                "LapNumber": [3],
                "LapTime": ["1:44.310"],
                "Sector1Time": ["34.120"],
                "Sector2Time": [None],
                "Sector3Time": [None],
            }
        )
        store.update_laps("Live S", df)
        rec = store.session_records("Live S")
        assert rec["fastest_lap"]["display"] == "01:44.310"
        assert rec["fastest_s1"]["seconds"] == pytest.approx(34.120)

    def test_driver_map_translation(self, store):
        df = laps_frame([timedelta(seconds=89), None, None])
        store.update_laps("S", df, driver_map={"VER": "MAX"})
        assert store.session_records("S")["fastest_lap"]["driver"] == "MAX"

    def test_top_speed_keeps_maximum(self, store):
        tel = {
            "VER": pd.DataFrame({"Speed": [280.0, 315.5]}),
            "PER": pd.DataFrame({"Speed": [300.0]}),
        }
        store.update_telemetry("S", tel)
        assert store.session_records("S")["top_speed"] == {"driver": "VER", "kmh": 315.5}
        store.update_telemetry("S", {"HAM": pd.DataFrame({"Speed": [310.0]})})
        # Slower update does not replace the record
        assert store.session_records("S")["top_speed"]["kmh"] == 315.5

    def test_all_time_across_sessions(self, store):
        store.update_laps("Race A", laps_frame([timedelta(seconds=91), None, None]))
        store.update_laps("Race B", laps_frame([timedelta(seconds=89.9), None, None]))
        at = store.all_time()
        assert at["fastest_lap"]["session"] == "Race B"

    def test_persistence_across_restart(self, tmp_path):
        path = str(tmp_path / "metrics.json")
        s1 = MetricsStore(path=path)
        s1.update_laps("Keep Me", laps_frame([timedelta(seconds=88.123), None, None]))

        s2 = MetricsStore(path=path)  # simulates app reopen
        rec = s2.session_records("Keep Me")
        assert rec["fastest_lap"]["seconds"] == pytest.approx(88.123)
        assert s2.all_time()["fastest_lap"]["driver"] == "VER"

    def test_corrupt_store_recovers(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text("{not valid json!!", encoding="utf-8")
        store = MetricsStore(path=str(path))
        assert store.session_records("anything") == {}

    def test_summary_lines_format(self, store):
        store.update_laps("S", laps_frame([timedelta(seconds=90), None, None]))
        store.update_telemetry("S", {"VER": pd.DataFrame({"Speed": [320.0]})})
        lines = store.summary_lines(store.session_records("S"))
        assert any("Fastest Lap" in line and "VER" in line for line in lines)
        assert any("Top Speed" in line and "320.0" in line for line in lines)

    def test_json_roundtrip_contents(self, tmp_path):
        path = tmp_path / "metrics.json"
        s = MetricsStore(path=str(path))
        s.update_telemetry("X", {"A": pd.DataFrame({"Speed": [250.25]})})
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert (
            raw["sessions"]["X"]["top_speed"]["kmh"] == 250.2
            or raw["sessions"]["X"]["top_speed"]["kmh"] == 250.25
        )
