"""Tests for the persistent MetricsStore (fastest lap / sectors / top speed)."""

import json
import logging
import threading
from datetime import timedelta

import pandas as pd
import pytest

from f1dash.processing.metrics_store import MetricsStore, _to_seconds


@pytest.fixture
def store(tmp_path):
    return MetricsStore(path=str(tmp_path / "metrics.sqlite"))


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

    def test_corrected_laps_overwrite_the_record(self, store):
        """CACHE-05: a session's records are recomputed, so a correction
        (a deleted lap, a FastF1 data fix) can lower *or raise* them."""
        store.update_laps("S", laps_frame([timedelta(seconds=90), None, None]))
        store.update_laps("S", laps_frame([timedelta(seconds=100), None, None]))
        assert store.session_records("S")["fastest_lap"]["seconds"] == 100.0
        store.update_laps("S", laps_frame([timedelta(seconds=89), None, None]))
        assert store.session_records("S")["fastest_lap"]["seconds"] == 89.0

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

    def test_top_speed_from_telemetry_is_the_maximum(self, store):
        tel = {
            "VER": pd.DataFrame({"Speed": [280.0, 315.5]}),
            "PER": pd.DataFrame({"Speed": [300.0]}),
        }
        store.update_telemetry("S", tel)
        assert store.session_records("S")["top_speed"] == {"driver": "VER", "kmh": 315.5}

    def test_top_speed_comes_from_the_speed_traps(self, store):
        """CACHE-02: the laps' speed traps, not scope-limited telemetry."""
        laps = laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), None])
        laps["SpeedST"] = [321.0, 318.0, None]
        laps["SpeedFL"] = [300.0, 325.4, 299.0]
        store.update_laps("S", laps)
        assert store.session_records("S")["top_speed"] == {"driver": "HAM", "kmh": 325.4}

    def test_all_time_across_sessions(self, store):
        store.update_laps("Race A", laps_frame([timedelta(seconds=91), None, None]))
        store.update_laps("Race B", laps_frame([timedelta(seconds=89.9), None, None]))
        at = store.all_time()
        assert at["fastest_lap"]["session"] == "Race B"

    def test_all_time_is_grouped_by_circuit(self, store):
        """CACHE-02: a Monza lap is not a record at Monaco."""
        store.update_laps(
            "Monza R", laps_frame([timedelta(seconds=81), None, None]), circuit="Monza"
        )
        store.update_laps(
            "Monaco R", laps_frame([timedelta(seconds=72), None, None]), circuit="Monaco"
        )
        store.update_laps(
            "Monaco Q", laps_frame([timedelta(seconds=70.3), None, None]), circuit="Monaco"
        )
        assert store.all_time("Monza")["fastest_lap"]["session"] == "Monza R"
        assert store.all_time("Monaco")["fastest_lap"]["session"] == "Monaco Q"

    def test_persistence_across_restart(self, tmp_path):
        path = str(tmp_path / "metrics.sqlite")
        s1 = MetricsStore(path=path)
        s1.update_laps("Keep Me", laps_frame([timedelta(seconds=88.123), None, None]))

        s2 = MetricsStore(path=path)  # simulates app reopen
        rec = s2.session_records("Keep Me")
        assert rec["fastest_lap"]["seconds"] == pytest.approx(88.123)
        assert s2.all_time()["fastest_lap"]["driver"] == "VER"

    def test_corrupt_store_recovers(self, tmp_path):
        path = tmp_path / "metrics.sqlite"
        path.write_text("{not valid json!!", encoding="utf-8")
        store = MetricsStore(path=str(path))
        assert store.session_records("anything") == {}
        assert (tmp_path / "metrics.sqlite.bak").exists()

    def test_a_list_json_store_loads_empty_with_a_warning(self, tmp_path, caplog):
        """CACHE-05: a store holding ``[]`` used to raise AttributeError at start."""
        path = tmp_path / "metrics_store.json"
        path.write_text("[]", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="f1dash.processing.metrics_store"):
            store = MetricsStore(path=str(path))
        assert store.session_records("anything") == {}
        assert "not a records file" in caplog.text
        assert (tmp_path / "metrics_store.json.bak").read_text(encoding="utf-8") == "[]"

    def test_summary_lines_format(self, store):
        store.update_laps("S", laps_frame([timedelta(seconds=90), None, None]))
        store.update_telemetry("S", {"VER": pd.DataFrame({"Speed": [320.0]})})
        lines = store.summary_lines(store.session_records("S"))
        assert any("Fastest lap" in line and "VER" in line for line in lines)
        assert any("Top speed" in line and "320.0" in line for line in lines)

    def test_a_legacy_json_store_is_imported(self, tmp_path):
        legacy = {
            "sessions": {
                "Old R 2023": {
                    "fastest_lap": {"driver": "VER", "seconds": 88.5, "display": "x", "lap": 4},
                    "top_speed": {"driver": "SAI", "kmh": 340.1},
                }
            },
            "updated_at": None,
        }
        (tmp_path / "metrics_store.json").write_text(json.dumps(legacy), encoding="utf-8")
        store = MetricsStore(path=str(tmp_path / "metrics_store.sqlite"))
        records = store.session_records("Old R 2023")
        assert records["fastest_lap"] == {
            "driver": "VER",
            "seconds": 88.5,
            "display": "01:28.500",
            "lap": 4,
        }
        assert records["top_speed"] == {"driver": "SAI", "kmh": 340.1}


class TestDeletedLapsAreNotRecords:
    """REPLAY-18: the store records the fastest *valid* lap."""

    def test_a_deleted_lap_sets_no_record(self, store):
        laps = laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), timedelta(seconds=93)])
        laps["Deleted"] = [False, True, None]
        store.update_laps("S", laps)
        records = store.session_records("S")
        assert records["fastest_lap"]["driver"] == "VER"
        # HAM's 30.9 s first sector was on the deleted lap.
        assert records["fastest_s1"]["driver"] == "VER"


class TestWrites:
    """CACHE-02: write only on change; tabs writing at once keep both records."""

    def test_reruns_with_nothing_changed_write_nothing(self, store):
        laps = laps_frame([timedelta(seconds=92), timedelta(seconds=90.5), None])
        store.update_laps("S", laps)
        before = store.writes
        for _ in range(100):
            store.update_laps("S", laps)
        assert store.writes == before

    def test_two_concurrent_writers_keep_both_records(self, tmp_path):
        path = str(tmp_path / "shared.sqlite")
        first, second = MetricsStore(path=path), MetricsStore(path=path)

        def record(store, label, seconds):
            for step in range(20):
                laps = laps_frame([timedelta(seconds=seconds + step / 100), None, None])
                store.update_laps(label, laps)

        threads = [
            threading.Thread(target=record, args=(first, "Tab A", 90)),
            threading.Thread(target=record, args=(second, "Tab B", 91)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        reopened = MetricsStore(path=path)
        assert reopened.session_records("Tab A")["fastest_lap"]["seconds"] == 90.19
        assert reopened.session_records("Tab B")["fastest_lap"]["seconds"] == 91.19
