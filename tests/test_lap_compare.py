"""UX-04: stacked head-to-head with lap pickers and corner markers."""

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from f1dash.processing.lap_compare import (
    available_laps,
    corner_markers,
    fastest_lap_number,
    lap_telemetry,
    lap_times,
    shared_grid,
)


def _laps(driver="VER", n=3, length=60.0):
    """Laps of ``length`` s starting at session time 100 s."""
    starts = [100.0 + i * length for i in range(n)]
    return pd.DataFrame(
        {
            "Driver": driver,
            "LapNumber": [float(i + 1) for i in range(n)],
            "LapTime": pd.to_timedelta([length - i for i in range(n)], unit="s"),
            "LapStartTime": pd.to_timedelta(starts, unit="s"),
            "Time": pd.to_timedelta([s + length - i for i, s in enumerate(starts)], unit="s"),
        }
    )


def _session_frame(n=3, length=60.0, hz=10):
    """Accumulating-distance telemetry: 50 m/s, Time from the first lap's start."""
    seconds = np.arange(0, n * length, 1 / hz)
    return pd.DataFrame(
        {
            "Time": pd.to_timedelta(seconds, unit="s"),
            "Distance": seconds * 50.0,
            "Speed": 180.0 + 10 * np.sin(seconds / 5),
            "Throttle": np.clip(50 + 50 * np.sin(seconds / 3), 0, 100),
            "Brake": (np.sin(seconds / 3) < -0.9).astype(int),
            "RPM": 10000.0 + seconds,
            "nGear": 1 + (seconds // 10 % 8),
        }
    )


class TestLapSelection:
    def test_fastest_lap_is_the_shortest_time(self):
        assert fastest_lap_number(_laps(), "VER") == 3
        assert lap_times(_laps(), "VER")[1] == 60.0
        assert fastest_lap_number(_laps(), "HAM") is None

    def test_session_scope_offers_every_timed_lap(self):
        assert available_laps(_session_frame(), _laps(), "VER", "session") == [1, 2, 3]

    def test_fastest_scope_offers_only_the_fastest(self):
        assert available_laps(_session_frame(1), _laps(), "VER", "fastest") == [3]
        assert available_laps(_session_frame(1), pd.DataFrame(), "VER", "fastest") == []

    def test_lap_slice_restarts_distance_at_zero(self):
        lap = lap_telemetry(_session_frame(), _laps(), "VER", 2, "session")

        assert lap["Distance"].iloc[0] == 0.0
        # lap 2 is 59 s at 50 m/s
        assert lap["Distance"].iloc[-1] == pytest.approx(59 * 50.0, abs=60)

    def test_unknown_lap_is_empty_and_fastest_scope_passes_through(self):
        frame = _session_frame()
        assert lap_telemetry(frame, _laps(), "VER", 9, "session").empty
        assert lap_telemetry(frame, _laps(), "VER", None, "fastest") is frame


class TestSharedGrid:
    def test_coded_channels_are_never_blended(self):
        a, b = shared_grid(_session_frame(1), _session_frame(1)[::-1].reset_index(drop=True))

        assert len(a) == len(b)
        assert set(a["nGear"].dropna()) <= set(range(0, 9))
        assert set(a["Brake"].unique()) <= {0, 100}

    def test_cut_to_the_shorter_lap(self):
        a, b = shared_grid(_session_frame(1, 60.0), _session_frame(1, 40.0))
        assert len(a) == len(b)
        assert a["Distance"].iloc[-1] <= 40 * 50.0


class TestCornerMarkers:
    def test_corners_inside_the_lap_are_labelled(self):
        info = {
            "corners": pd.DataFrame(
                {"Number": [1, 2, 3], "Letter": ["", "A", ""], "Distance": [100.0, 900.0, 99999.0]}
            )
        }
        assert corner_markers(info, 3000.0) == [(100.0, "1"), (900.0, "2A")]

    def test_no_corners(self):
        assert corner_markers({}, 3000.0) == []
        assert corner_markers({"corners": pd.DataFrame()}, 3000.0) == []


def _comparison_script():
    import numpy as np
    import pandas as pd

    import f1dash.ui.layout as layout

    captured = layout.__dict__.setdefault("_captured", [])
    layout._plot = lambda fig, *a, **k: captured.append(fig)

    grid = np.arange(0, 3000, 5.0)

    def frame(speed):
        return pd.DataFrame(
            {
                "Distance": grid,
                "Speed": speed + 20 * np.sin(grid / 200),
                "Throttle": np.full(len(grid), 80.0),
                "Brake": np.zeros(len(grid)),
                "RPM": np.full(len(grid), 10000.0),
                "nGear": np.full(len(grid), 5.0),
            }
        )

    laps = pd.DataFrame(
        {
            "Driver": ["VER", "HAM"],
            "LapNumber": [1.0, 1.0],
            "LapTime": pd.to_timedelta([80.0, 81.0], unit="s"),
        }
    )
    corners = (
        pd.DataFrame({"Number": [1, 2], "Letter": ["", ""], "Distance": [400.0, 1500.0]})
        if layout.__dict__.get("_with_corners", True)
        else pd.DataFrame()
    )
    data = {
        "session_info": {"telemetry_scope": "fastest", "year": 2024},
        "telemetry": {"VER": frame(200.0), "HAM": frame(195.0)},
        "laps": laps,
        "circuit_info": {"corners": corners},
    }
    layout.render_driver_comparison(
        data["telemetry"], {}, key_prefix="cmp:test", session_data=data, laps=laps
    )


def _run(with_corners: bool, query: dict | None = None):
    import f1dash.ui.layout as layout

    layout.__dict__["_captured"] = []
    layout.__dict__["_with_corners"] = with_corners
    original = layout._plot
    try:
        app = AppTest.from_function(_comparison_script, default_timeout=30)
        for name, value in (query or {}).items():
            app.query_params[name] = value
        app.run()
    finally:
        layout._plot = original
    assert not app.exception
    return app, layout._captured[-1]


class TestStackedComparison:
    def test_five_panels_share_the_x_axis(self):
        app, fig = _run(True)

        # speed, throttle, brake, gear, delta - one trace per driver except delta
        assert len(fig.data) == 4 * 2 + 1
        assert {t.xaxis for t in fig.data} == {"x", "x2", "x3", "x4", "x5"}
        matches = {fig.layout[a].matches for a in ("xaxis", "xaxis2", "xaxis3", "xaxis4")}
        assert matches <= {None, "x5"}
        # one lap picker per driver, keyed by session and driver
        assert {s.key for s in app.selectbox} == {
            "cmp:test_ref",
            "cmp:test_cmp",
            "cmp:test_lap_VER",
            "cmp:test_lap_HAM",
        }
        assert any("Approximate" in c.value for c in app.caption)

    def test_corner_markers_present_only_when_corners_exist(self):
        _, with_corners = _run(True)
        assert [a.text for a in with_corners.layout.annotations] == ["1", "2"]

        app, without = _run(False)
        assert len(without.layout.annotations) == 0
        assert any("No corner positions" in c.value for c in app.caption)


class TestComparisonUnits:
    def test_speed_follows_the_viewer_unit_and_the_delta_does_not(self):
        _, kmh = _run(True)
        _, mph = _run(True, {"speed": "mph"})

        assert kmh.layout.yaxis.title.text == "Speed (km/h)"
        assert mph.layout.yaxis.title.text == "Speed (mph)"
        assert max(mph.data[0].y) == pytest.approx(max(kmh.data[0].y) / 1.609344)
        # The delta is integrated from km/h whatever the display unit.
        assert list(mph.data[-1].y) == pytest.approx(list(kmh.data[-1].y))
