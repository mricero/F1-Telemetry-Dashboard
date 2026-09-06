"""Tests for the analysis helpers behind the UI panels.

These cover the pure computation in ``ui.layout`` - delta integration, grid
sampling, session-time conversion and compound palettes - without needing a
Streamlit runtime.
"""

import numpy as np
import pandas as pd
import pytest

from data.live_adapter import LiveDataProcessor
from ui.layout import (
    COMPOUND_COLORS,
    _elapsed_minutes,
    _speed_on_grid,
    _time_delta,
    compound_palette,
)


def _trace(speed_kmh: float, length_m: float = 5000.0, points: int = 500) -> pd.DataFrame:
    """Constant-speed telemetry trace over a fixed distance."""
    return pd.DataFrame(
        {
            "Distance": np.linspace(0.0, length_m, points),
            "Speed": np.full(points, speed_kmh, dtype=float),
        }
    )


class TestTimeDelta:
    def test_equal_traces_have_zero_delta(self):
        grid, delta = _time_delta(_trace(200.0), _trace(200.0))

        assert grid is not None
        assert np.allclose(delta, 0.0, atol=1e-9)

    def test_slower_compare_lap_loses_time(self):
        # 5 km at 200 km/h = 90 s; at 180 km/h = 100 s -> ~10 s lost.
        _, delta = _time_delta(_trace(200.0), _trace(180.0))

        assert delta[-1] == pytest.approx(10.0, abs=0.1)
        assert np.all(np.diff(delta) >= -1e-9), "constant deficit accumulates monotonically"

    def test_faster_compare_lap_gains_time(self):
        _, delta = _time_delta(_trace(180.0), _trace(200.0))

        assert delta[-1] == pytest.approx(-10.0, abs=0.1)

    def test_delta_is_finite_with_zero_speed(self):
        """A standing car must not produce an infinite step time."""
        stopped = _trace(200.0)
        stopped.loc[:50, "Speed"] = 0.0

        _, delta = _time_delta(_trace(200.0), stopped)

        assert np.isfinite(delta).all()

    def test_too_few_samples_returns_none(self):
        tiny = pd.DataFrame({"Distance": [0.0, 1.0], "Speed": [100.0, 100.0]})

        assert _time_delta(tiny, tiny) == (None, None)

    def test_missing_columns_returns_none(self):
        assert _time_delta(pd.DataFrame({"Distance": [1.0]}), _trace(200.0)) == (None, None)


class TestSpeedOnGrid:
    def test_samples_onto_shared_grid(self):
        grid, speed = _speed_on_grid(_trace(250.0))

        assert grid is not None and len(grid) == len(speed)
        assert np.allclose(speed, 250.0)

    def test_reuses_supplied_grid(self):
        shared = np.linspace(0.0, 1000.0, 50)

        grid, speed = _speed_on_grid(_trace(200.0), grid=shared)

        assert np.array_equal(grid, shared)
        assert len(speed) == 50

    def test_unsorted_distance_is_ordered(self):
        df = _trace(200.0).sample(frac=1.0, random_state=0)

        grid, _ = _speed_on_grid(df)

        assert np.all(np.diff(grid) > 0)

    def test_empty_frame_returns_none(self):
        assert _speed_on_grid(pd.DataFrame()) == (None, None)


class TestElapsedMinutes:
    def test_timedelta_column(self):
        df = pd.DataFrame({"Time": pd.to_timedelta([0, 60, 120], unit="s")})

        assert list(_elapsed_minutes(df)) == [0.0, 1.0, 2.0]

    def test_timestamp_column_is_session_relative(self):
        df = pd.DataFrame(
            {"Time": pd.to_datetime(["2026-03-08T03:00:00Z", "2026-03-08T03:30:00Z"])}
        )

        assert list(_elapsed_minutes(df)) == [0.0, 30.0]

    def test_missing_column_falls_back_to_index(self):
        assert list(_elapsed_minutes(pd.DataFrame({"AirTemp": [1, 2, 3]}))) == [0.0, 1.0, 2.0]


class TestCompoundPalette:
    def test_defaults_used_without_session_colours(self):
        assert compound_palette() == COMPOUND_COLORS
        assert compound_palette(None)["SOFT"].startswith("#")

    def test_session_colours_override_and_extend(self):
        palette = compound_palette({"soft": "#123456", "SUPERSOFT": "#abcdef"})

        assert palette["SOFT"] == "#123456"  # case-insensitive override
        assert palette["SUPERSOFT"] == "#abcdef"
        assert palette["HARD"] == COMPOUND_COLORS["HARD"]  # untouched default


class TestLiveRaceControl:
    def test_parses_messages_into_fastf1_shape(self):
        records = [
            {
                "Utc": "2026-03-08T03:20:00Z",
                "Category": "Flag",
                "Flag": "GREEN",
                "Scope": "Track",
                "Lap": 1,
                "Message": "GREEN LIGHT - PIT EXIT OPEN",
            },
            {"Utc": "2026-03-08T03:21:00Z", "Category": "Other"},  # no message -> skipped
        ]

        df = LiveDataProcessor.parse_race_control(records)

        assert list(df.columns) == ["Time", "Lap", "Category", "Flag", "Scope", "Message"]
        assert len(df) == 1
        assert df["Message"].iloc[0] == "GREEN LIGHT - PIT EXIT OPEN"

    def test_empty_buffer_is_safe(self):
        assert LiveDataProcessor.parse_race_control([]).empty
        assert LiveDataProcessor.parse_race_control(None).empty

    def test_track_status_takes_latest_record(self):
        records = [
            {"Status": "1", "Message": "AllClear"},
            {"Status": "4", "Message": "SafetyCar"},
        ]

        assert LiveDataProcessor.parse_track_status(records) == {
            "status": "4",
            "message": "SafetyCar",
        }

    def test_track_status_none_when_absent(self):
        assert LiveDataProcessor.parse_track_status([]) is None
        assert LiveDataProcessor.parse_track_status([{"Message": "x"}]) is None
