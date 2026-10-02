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

    def test_a_session_is_recomputed_not_only_lowered(self, store):
        """CACHE-05: corrected laps lower or raise the stored record."""
        store.update_laps("S", laps_frame([timedelta(seconds=90), None, None]))
        store.update_laps("S", laps_frame([timedelta(seconds=100), None, None]))
        assert store.session_records("S")["fastest_lap"]["seconds"] == 100.0
        store.update_laps("S", laps_frame([timedelta(seconds=95), None, None]))
        assert store.session_records("S")["fastest_lap"]["seconds"] == 95.0

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
        assert (tmp_path / "metrics.json.bak").exists()

    def test_a_list_json_file_loads_empty_with_a_warning(self, tmp_path, caplog):
        """CACHE-05: ``[]`` used to raise AttributeError at app start."""
        path = tmp_path / "metrics_store.sqlite"
        path.write_text("[]", encoding="utf-8")

        with caplog.at_level("WARNING"):
            store = MetricsStore(path=str(path))

        assert store.session_records("anything") == {}
        assert "not a records store" in caplog.text
        assert (tmp_path / "metrics_store.sqlite.bak").read_text(encoding="utf-8") == "[]"

    def test_an_old_json_store_is_imported(self, tmp_path):
        legacy = {
            "sessions": {
                "Old GP R 2024": {
                    "fastest_lap": {"driver": "VER", "seconds": 88.5, "display": "01:28.500"},
                    "top_speed": {"driver": "HAM", "kmh": 330.1},
                }
            }
        }
        (tmp_path / "metrics_store.json").write_text(json.dumps(legacy), encoding="utf-8")

        store = MetricsStore(path=str(tmp_path / "metrics_store.sqlite"))

        records = store.session_records("Old GP R 2024")
        assert records["fastest_lap"]["seconds"] == 88.5
        assert records["top_speed"] == {"driver": "HAM", "kmh": 330.1}
        assert (tmp_path / "metrics_store.json.bak").exists()

    def test_summary_lines_format(self, store):
        store.update_laps("S", laps_frame([timedelta(seconds=90), None, None]))
        store.update_telemetry("S", {"VER": pd.DataFrame({"Speed": [320.0]})})
        lines = store.summary_lines(store.session_records("S"))
        assert any("Fastest lap" in line and "VER" in line for line in lines)
        assert any("Top speed" in line and "320.0" in line for line in lines)

    def test_nothing_changed_writes_nothing(self, store):
        """CACHE-02: reruns with the same laps do not touch the file."""
        frame = laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), None])
        store.update_laps("S", frame)
        writes = store.writes
        for _ in range(100):
            store.update_laps("S", frame)
        assert store.writes == writes

    def test_two_stores_on_one_file_keep_both_records(self, tmp_path):
        """CACHE-02: each tab's store used to wipe the other's on save."""
        path = str(tmp_path / "records.sqlite")
        first, second = MetricsStore(path=path), MetricsStore(path=path)

        first.update_laps("A", laps_frame([timedelta(seconds=91), None, None]))
        second.update_laps("B", laps_frame([timedelta(seconds=92), None, None]))

        reopened = MetricsStore(path=path)
        assert reopened.session_records("A")["fastest_lap"]["seconds"] == 91.0
        assert reopened.session_records("B")["fastest_lap"]["seconds"] == 92.0

    def test_all_time_is_per_circuit(self, store):
        store.update_laps(
            "Monaco R 2024",
            laps_frame([timedelta(seconds=74)] * 1 + [None, None]),
            circuit="Monaco",
        )
        store.update_laps(
            "Monza R 2024", laps_frame([timedelta(seconds=81), None, None]), circuit="Monza"
        )
        store.update_laps(
            "Monza R 2025", laps_frame([timedelta(seconds=80), None, None]), circuit="Monza"
        )

        monza = store.all_time(circuit="Monza")
        assert monza["fastest_lap"]["session"] == "Monza R 2025"
        assert store.all_time(circuit="Monaco")["fastest_lap"]["seconds"] == 74.0

    def test_deleted_laps_do_not_set_records(self, store):
        """REPLAY-18: the store records the fastest *valid* lap."""
        frame = laps_frame([timedelta(seconds=89), timedelta(seconds=90.5), timedelta(seconds=93)])
        frame["Deleted"] = [True, False, False]

        store.update_laps("S", frame)

        assert store.session_records("S")["fastest_lap"]["driver"] == "HAM"

    def test_top_speed_comes_from_the_speed_traps(self, store):
        frame = laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), None])
        frame["SpeedST"] = [331.0, 325.0, None]
        frame["SpeedFL"] = [300.0, 334.5, 310.0]

        store.update_laps("S", frame)

        assert store.session_records("S")["top_speed"] == {"driver": "HAM", "kmh": 334.5}
        assert store.has_top_speed("S")
