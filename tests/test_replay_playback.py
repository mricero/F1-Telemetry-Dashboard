"""Session playback: what happened, from lights out to the flag (FEAT-04).

The "Replay (Saved)" source only ever reloaded a session as a static
dashboard. This covers the time dimension: a position timeline sampled across
the whole session, and the helpers that answer "where was everyone, and in
what order, at time t".
"""

import numpy as np
import pandas as pd
import pytest

from processing.replay import (
    DEFAULT_STEP_SECONDS,
    build_position_timeline,
    format_clock,
    lap_at,
    order_at,
    positions_at,
    timeline_bounds,
)


def _driver_positions(offset: float = 0.0, samples: int = 61) -> pd.DataFrame:
    """One driver's raw position data: a lap of a circle, 0.5 s apart."""
    seconds = np.arange(samples) * 0.5
    angle = seconds / 30.0 * 2 * np.pi + offset
    return pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta(seconds, unit="s"),
            "X": np.cos(angle) * 1000,
            "Y": np.sin(angle) * 600,
            "Z": np.zeros(samples),
            "Status": ["OnTrack"] * samples,
        }
    )


@pytest.fixture
def timeline() -> pd.DataFrame:
    return build_position_timeline({"VER": _driver_positions(), "HAM": _driver_positions(0.4)})


class TestBuildPositionTimeline:
    def test_it_produces_a_tidy_frame(self, timeline):
        assert list(timeline.columns) == ["Time", "Driver", "X", "Y"]
        assert set(timeline["Driver"]) == {"VER", "HAM"}

    def test_every_driver_shares_one_time_grid(self, timeline):
        grids = timeline.groupby("Driver")["Time"].apply(list)

        assert grids["VER"] == grids["HAM"], "drivers must be comparable at the same moment"

    def test_the_grid_step_is_regular(self, timeline):
        times = sorted(timeline["Time"].unique())

        assert np.allclose(np.diff(times), DEFAULT_STEP_SECONDS)

    def test_it_covers_the_whole_session(self, timeline):
        assert timeline["Time"].min() == 0.0
        assert timeline["Time"].max() == pytest.approx(30.0, abs=DEFAULT_STEP_SECONDS)

    def test_positions_are_interpolated_onto_the_grid(self):
        # Samples 2 s apart; a 0.5 s grid must fill the gaps.
        raw = pd.DataFrame(
            {
                "SessionTime": pd.to_timedelta([0.0, 2.0], unit="s"),
                # Away from the origin: an exact (0,0) is the garage
                # placeholder the filter drops.
                "X": [100.0, 300.0],
                "Y": [50.0, 50.0],
                "Status": ["OnTrack", "OnTrack"],
            }
        )

        frame = build_position_timeline({"VER": raw})
        at_one_second = frame[np.isclose(frame["Time"], 1.0)]

        assert at_one_second["X"].iloc[0] == pytest.approx(200.0)

    def test_garage_and_off_track_samples_are_dropped(self):
        raw = pd.DataFrame(
            {
                "SessionTime": pd.to_timedelta([0.0, 0.5, 1.0], unit="s"),
                "X": [0.0, 500.0, 600.0],
                "Y": [0.0, 500.0, 600.0],
                "Status": ["OnTrack", "OffTrack", "OnTrack"],
            }
        )

        frame = build_position_timeline({"VER": raw})

        # The first sample is a 0,0 garage placeholder and the second is off
        # track, so only the last one anchors the interpolation.
        assert not ((frame["X"] == 0) & (frame["Y"] == 0)).any()
        assert not (frame["X"] == 500.0).any()

    def test_a_driver_with_no_usable_data_is_skipped(self):
        empty = pd.DataFrame({"SessionTime": [], "X": [], "Y": [], "Status": []})

        frame = build_position_timeline({"VER": _driver_positions(), "NOB": empty})

        assert set(frame["Driver"]) == {"VER"}

    def test_no_frames_gives_an_empty_timeline(self):
        frame = build_position_timeline({})

        assert frame.empty
        assert list(frame.columns) == ["Time", "Driver", "X", "Y"]

    def test_a_car_is_absent_before_it_leaves_the_pits(self):
        """Interpolation must not invent a position outside a driver's window."""
        late = _driver_positions()
        late["SessionTime"] = late["SessionTime"] + pd.Timedelta(10, unit="s")

        frame = build_position_timeline({"VER": _driver_positions(), "HAM": late})
        at_start = frame[np.isclose(frame["Time"], 0.0)]

        assert set(at_start["Driver"]) == {"VER"}


class TestPositionsAt:
    def test_it_returns_one_marker_per_driver(self, timeline):
        markers = positions_at(timeline, 5.0)

        assert {marker["code"] for marker in markers} == {"VER", "HAM"}
        assert all({"x", "y"} <= set(marker) for marker in markers)

    def test_it_snaps_to_the_nearest_grid_time(self, timeline):
        exact = positions_at(timeline, 5.0)
        nudged = positions_at(timeline, 5.1)

        assert exact == nudged

    def test_a_time_outside_the_session_gives_nothing(self, timeline):
        assert positions_at(timeline, 9_999.0) == []

    def test_an_empty_timeline_gives_nothing(self):
        assert positions_at(build_position_timeline({}), 1.0) == []


class TestTimelineBounds:
    def test_it_reports_first_and_last_moment(self, timeline):
        start, end = timeline_bounds(timeline)

        assert start == 0.0
        assert end == pytest.approx(30.0, abs=DEFAULT_STEP_SECONDS)

    def test_an_empty_timeline_has_a_zero_window(self):
        assert timeline_bounds(build_position_timeline({})) == (0.0, 0.0)


def _laps() -> pd.DataFrame:
    """Two drivers, three laps, with the session time each lap ended."""
    return pd.DataFrame(
        {
            "Driver": ["VER", "HAM"] * 3,
            "LapNumber": [1, 1, 2, 2, 3, 3],
            "Position": [1.0, 2.0, 2.0, 1.0, 1.0, 2.0],
            "LapTime": pd.to_timedelta([90.0, 91.0, 89.0, 88.0, 90.0, 92.0], unit="s"),
            "Time": pd.to_timedelta([90.0, 91.0, 179.0, 179.0, 269.0, 271.0], unit="s"),
        }
    )


class TestOrderAt:
    def test_before_the_first_lap_nobody_is_classified(self):
        assert order_at(_laps(), 10.0) == []

    def test_it_reports_the_order_as_of_that_moment(self):
        order = order_at(_laps(), 100.0)

        assert [entry["code"] for entry in order] == ["VER", "HAM"]

    def test_the_order_changes_when_the_race_does(self):
        order = order_at(_laps(), 200.0)

        assert [entry["code"] for entry in order] == ["HAM", "VER"]

    def test_it_carries_the_lap_each_driver_is_on(self):
        order = order_at(_laps(), 200.0)

        assert order[0]["lap"] == 2

    def test_laps_without_a_time_column_give_nothing(self):
        laps = _laps().drop(columns=["Time"])

        assert order_at(laps, 100.0) == []


class TestLapAt:
    def test_it_counts_the_leader_lap(self):
        assert lap_at(_laps(), 100.0) == 1
        assert lap_at(_laps(), 200.0) == 2

    def test_before_the_first_lap_it_is_lap_one(self):
        assert lap_at(_laps(), 5.0) == 1

    def test_it_never_exceeds_the_last_lap(self):
        assert lap_at(_laps(), 9_999.0) == 3


class TestFormatClock:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "0:00"), (65, "1:05"), (3661, "1:01:01"), (5400, "1:30:00")],
    )
    def test_it_reads_as_a_session_clock(self, seconds, expected):
        assert format_clock(seconds) == expected
