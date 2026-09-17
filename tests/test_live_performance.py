"""Live poll performance and history retention (IMPROVEMENTS.md LIVE-11).

Every tick used to re-parse the whole buffer - a per-record `pd.to_numeric`
for each of ten channels - so a full-grid race snapshot cost hundreds of
milliseconds against a 3 s refresh. The telemetry cap also dropped the oldest
records, taking the first laps of a race with it.
"""

import time

import pytest

from data.live_adapter import SignalRLiveAdapter
from data.source_manager import DataSourceManager

DRIVERS = [str(number) for number in range(1, 23)]
SNAPSHOT_BUDGET_SECONDS = 0.15


def _timestamp(sample: int) -> str:
    return f"2026-09-06T13:{sample // 60:02d}:{sample % 60:02d}.000Z"


def _prime(adapter: SignalRLiveAdapter, samples: int = 2400, laps: int = 60) -> None:
    """22 drivers of 4 Hz telemetry plus completed laps."""
    car, position = [], []
    for sample in range(samples):
        stamp = _timestamp(sample)
        for driver in DRIVERS:
            car.append(
                {
                    "DriverNo": driver,
                    "Utc": stamp,
                    "rpm": 11000,
                    "speed": 250,
                    "n_gear": 7,
                    "throttle": 90,
                    "brake": 0,
                    "drs": 8,
                }
            )
            position.append(
                {
                    "DriverNo": driver,
                    "Utc": stamp,
                    "X": sample * 10,
                    "Y": sample * 5,
                    "Z": 0,
                    "Status": "OnTrack",
                }
            )
    adapter._buffer_topic("CarData.z", car)
    adapter._buffer_topic("Position.z", position)

    for lap in range(1, laps + 1):
        adapter.handle_message(
            "TimingData",
            {
                "Lines": {
                    driver: {
                        "NumberOfLaps": lap,
                        "LastLapTime": {"Value": f"1:3{lap % 10}.000"},
                    }
                    for driver in DRIVERS
                }
            },
        )
    adapter.handle_message(
        "DriverList",
        {
            driver: {
                "RacingNumber": driver,
                "Tla": f"D{driver}",
                "TeamName": "Team",
                "TeamColour": "3671C6",
            }
            for driver in DRIVERS
        },
    )


@pytest.fixture
def primed_manager(monkeypatch):
    monkeypatch.setattr("data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})())
    adapter = SignalRLiveAdapter(buffer_limit=20000)
    _prime(adapter)
    return DataSourceManager(live_adapter=adapter)


class TestSnapshotCost:
    def test_a_full_grid_snapshot_fits_the_budget(self, primed_manager):
        primed_manager.poll_live_data()  # warm any caches

        best = min(_time_poll(primed_manager) for _ in range(3))

        assert best < SNAPSHOT_BUDGET_SECONDS, f"snapshot took {best * 1000:.0f} ms"

    def test_an_unchanged_state_is_not_rebuilt(self, primed_manager):
        first = primed_manager.poll_live_data()
        second = primed_manager.poll_live_data()

        assert second is first, "nothing changed, so the snapshot should be reused"

    def test_new_data_invalidates_the_snapshot(self, primed_manager):
        first = primed_manager.poll_live_data()
        primed_manager.live.handle_message("TimingData", {"Lines": {"1": {"NumberOfLaps": 99}}})
        second = primed_manager.poll_live_data()

        assert second is not first

    def test_new_telemetry_invalidates_the_snapshot(self, primed_manager):
        first = primed_manager.poll_live_data()
        primed_manager.live._buffer_topic(
            "CarData.z", [{"DriverNo": "1", "Utc": _timestamp(1), "speed": 300}]
        )
        second = primed_manager.poll_live_data()

        assert second is not first


class TestHistoryRetention:
    def test_lap_one_survives_a_full_race(self, monkeypatch):
        """Two hours of feed must not evict the opening laps."""
        adapter = SignalRLiveAdapter(buffer_limit=5000)
        for lap in range(1, 71):
            adapter.handle_message(
                "TimingData",
                {"Lines": {"1": {"NumberOfLaps": lap, "LastLapTime": {"Value": "1:31.000"}}}},
            )
            # A lap's worth of telemetry, far past the cap in total.
            adapter._buffer_topic(
                "CarData.z",
                [{"DriverNo": "1", "Utc": _timestamp(i), "speed": 250} for i in range(300)],
            )

        laps = adapter.recorded_laps()

        assert len(adapter.get_buffered_data("CarData.z")) == 5000, "telemetry is capped"
        assert laps[0]["LapNumber"] == 1, "lap 1 was evicted with the telemetry"
        assert len(laps) == 70

    def test_the_laps_frame_still_starts_at_lap_one(self, primed_manager):
        snapshot = primed_manager.poll_live_data()
        laps = snapshot["laps"]

        assert int(laps["LapNumber"].min()) == 1


def _time_poll(manager) -> float:
    start = time.perf_counter()
    manager.poll_live_data()
    return time.perf_counter() - start
