"""Live telemetry segmented into laps (IMPROVEMENTS.md LIVE-13).

Live Distance was cumulative since the stream started, and each driver's tail
of samples began at a different point, so overlaying drivers, the head-to-head
delta and the dominance map compared nothing meaningful.
"""

import numpy as np
import pandas as pd
import pytest

from data.live_adapter import SignalRLiveAdapter
from data.source_manager import DataSourceManager

LAP_LENGTH_UNITS = 50_000.0  # 5 km in the feed's 1/10 m
LAP_SECONDS = 90
SAMPLE_HZ = 4


def _feed_one_lap(adapter: SignalRLiveAdapter, lap: int, driver: str = "1") -> None:
    """One lap of position samples, then the completion that closes it."""
    samples = LAP_SECONDS * SAMPLE_HZ
    start_second = (lap - 1) * LAP_SECONDS
    for index in range(samples):
        elapsed = start_second + index / SAMPLE_HZ
        along = (index / samples) * LAP_LENGTH_UNITS
        adapter.handle_message(
            "Position.z",
            [
                {
                    "DriverNo": driver,
                    "Utc": _stamp(elapsed),
                    "X": float(along),
                    "Y": 0.0,
                    "Z": 0.0,
                    "Status": "OnTrack",
                }
            ],
        )
        adapter.handle_message(
            "CarData.z",
            [
                {
                    "DriverNo": driver,
                    "Utc": _stamp(elapsed),
                    "rpm": 11000,
                    "speed": 200,
                    "n_gear": 7,
                    "throttle": 90,
                    "brake": 0,
                    "drs": 0,
                }
            ],
        )
    adapter.handle_message(
        "TimingData",
        {"Lines": {driver: {"NumberOfLaps": lap, "LastLapTime": {"Value": "1:30.000"}}}},
        timestamp=_stamp(start_second + LAP_SECONDS),
    )


def _stamp(elapsed_seconds: float) -> str:
    base = pd.Timestamp("2026-09-06T13:00:00Z") + pd.Timedelta(elapsed_seconds, unit="s")
    return base.isoformat().replace("+00:00", "Z")


@pytest.fixture
def three_lap_manager(monkeypatch):
    monkeypatch.setattr("data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})())
    adapter = SignalRLiveAdapter()
    adapter.handle_message(
        "DriverList", {"1": {"RacingNumber": "1", "Tla": "VER", "TeamName": "RB"}}
    )
    for lap in (1, 2, 3):
        _feed_one_lap(adapter, lap)
    return DataSourceManager(live_adapter=adapter)


class TestLapCompletionTimestamps:
    def test_completions_record_when_they_happened(self):
        adapter = SignalRLiveAdapter()
        adapter.handle_message(
            "TimingData",
            {"Lines": {"1": {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:31.000"}}}},
            timestamp="2026-09-06T13:01:31Z",
        )

        completion = adapter.recorded_laps()[0]

        assert completion["Utc"] == "2026-09-06T13:01:31Z"

    def test_a_completion_without_a_timestamp_still_records(self):
        adapter = SignalRLiveAdapter()
        adapter.handle_message(
            "TimingData",
            {"Lines": {"1": {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:31.000"}}}},
        )

        assert adapter.recorded_laps()[0]["LapNumber"] == 1


class TestLapScopedTelemetry:
    def test_the_trace_starts_at_zero(self, three_lap_manager):
        telemetry = three_lap_manager.poll_live_data()["telemetry"]["VER"]

        assert telemetry["Distance"].iloc[0] == pytest.approx(0.0, abs=1.0)

    def test_the_trace_is_about_one_lap_long(self, three_lap_manager):
        telemetry = three_lap_manager.poll_live_data()["telemetry"]["VER"]

        expected_metres = LAP_LENGTH_UNITS / 10
        assert telemetry["Distance"].iloc[-1] == pytest.approx(expected_metres, rel=0.03)

    def test_the_snapshot_says_which_lap_is_shown(self, three_lap_manager):
        snapshot = three_lap_manager.poll_live_data()
        info = snapshot["session_info"]

        assert info["telemetry_scope"] == "lap"
        assert info["telemetry_lap"]["VER"] == 3

    def test_the_current_lap_is_reported_per_driver(self, three_lap_manager):
        snapshot = three_lap_manager.poll_live_data()

        assert snapshot["session_info"]["current_lap"]["VER"] == 4

    def test_the_location_trail_covers_the_same_lap(self, three_lap_manager):
        location = three_lap_manager.poll_live_data()["location"]["VER"]

        assert location["Distance"].iloc[0] == pytest.approx(0.0, abs=1.0)
        assert len(location) <= LAP_SECONDS * SAMPLE_HZ + 2

    def test_two_drivers_line_up_on_the_same_axis(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        adapter = SignalRLiveAdapter()
        adapter.handle_message(
            "DriverList",
            {
                "1": {"RacingNumber": "1", "Tla": "VER", "TeamName": "RB"},
                "44": {"RacingNumber": "44", "Tla": "HAM", "TeamName": "F"},
            },
        )
        for lap in (1, 2):
            _feed_one_lap(adapter, lap, driver="1")
        for lap in (1, 2, 3):
            _feed_one_lap(adapter, lap, driver="44")

        telemetry = DataSourceManager(live_adapter=adapter).poll_live_data()["telemetry"]

        starts = [frame["Distance"].iloc[0] for frame in telemetry.values()]
        ends = [frame["Distance"].iloc[-1] for frame in telemetry.values()]
        assert np.allclose(starts, 0.0, atol=1.0)
        assert max(ends) - min(ends) < 0.05 * max(ends)


class TestWithoutLapCompletions:
    def test_it_falls_back_to_the_running_tail(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        adapter = SignalRLiveAdapter()
        adapter.handle_message(
            "DriverList", {"1": {"RacingNumber": "1", "Tla": "VER", "TeamName": "RB"}}
        )
        for index in range(50):
            adapter.handle_message(
                "Position.z",
                [
                    {
                        "DriverNo": "1",
                        "Utc": _stamp(index),
                        "X": index * 100.0,
                        "Y": 0.0,
                        "Z": 0.0,
                        "Status": "OnTrack",
                    }
                ],
            )
            adapter.handle_message(
                "CarData.z",
                [
                    {
                        "DriverNo": "1",
                        "Utc": _stamp(index),
                        "rpm": 1,
                        "speed": 200,
                        "n_gear": 5,
                        "throttle": 10,
                        "brake": 0,
                        "drs": 0,
                    }
                ],
            )

        snapshot = DataSourceManager(live_adapter=adapter).poll_live_data()

        assert not snapshot["telemetry"]["VER"].empty
        assert snapshot["session_info"]["telemetry_scope"] == "session"


class TestAgainstTheRecordedFeed:
    """What the real 2023 Bahrain messages can show about segmentation.

    The recording is a 60-message slice, so it holds no *complete* lap of
    position samples - the lap-length property is asserted on the replayed
    feed above. What it does prove is that the feed carries the lap
    completions and message timestamps the segmentation depends on.
    """

    @staticmethod
    def _adapter():
        from tests import live_fixtures

        adapter = SignalRLiveAdapter()
        for timestamp, payload in live_fixtures.messages("TimingData"):
            adapter.handle_message("TimingData", payload, timestamp)
        return adapter

    def test_real_completions_carry_a_timestamp(self):
        completions = self._adapter().recorded_laps()

        assert completions, "no lap completions in the recording"
        assert all(entry["Utc"] for entry in completions)

    def test_boundaries_are_derived_from_them(self):
        from data.live_adapter import LiveDataProcessor

        boundaries = LiveDataProcessor.lap_boundaries(self._adapter().recorded_laps())

        assert boundaries
        for times in boundaries.values():
            assert times == sorted(times)

    def test_a_slice_without_enough_samples_falls_back(self):
        from data.live_adapter import LiveDataProcessor

        adapter = self._adapter()
        boundaries = LiveDataProcessor.lap_boundaries(adapter.recorded_laps())
        driver = next(iter(boundaries))
        frame = pd.DataFrame(
            {"timestamp": [pd.Timestamp("2023-03-05T15:00:00Z")], "X": [1.0], "Y": [2.0]}
        )

        assert LiveDataProcessor.last_completed_lap(frame, boundaries[driver]) is None
