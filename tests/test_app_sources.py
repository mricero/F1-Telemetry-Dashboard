"""End-to-end UI tests: selector -> loader -> dashboard, one per source.

TEST-02. The Replay path was broken from the sidebar to the loader (HIST-01)
while every unit test stayed green, because nothing drove the real Streamlit
script with a stubbed data manager. These do, offline.
"""

import os

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

# Keeps MetricsStore off disk for the whole module.
os.environ.setdefault("F1_METRICS_STORE", ":memory:")

# Every selection the loader was asked for, newest last.
CALLS: list = []


def _telemetry(distance_m: float = 5000.0, points: int = 60) -> pd.DataFrame:
    distance = np.linspace(0.0, distance_m, points)
    return pd.DataFrame(
        {
            "Distance": distance,
            "Time": pd.to_timedelta(np.linspace(0, 90, points), unit="s"),
            "Speed": np.linspace(120.0, 320.0, points),
            "Throttle": np.linspace(0.0, 100.0, points),
            "Brake": np.zeros(points, dtype=bool),
            "RPM": np.linspace(9000.0, 12000.0, points),
            "nGear": np.full(points, 6),
            "DRS": np.zeros(points),
        }
    )


def _location(points: int = 60) -> pd.DataFrame:
    angle = np.linspace(0, 2 * np.pi, points)
    return pd.DataFrame(
        {
            "Distance": np.linspace(0.0, 5000.0, points),
            "X": np.cos(angle) * 1000,
            "Y": np.sin(angle) * 600,
            "Z": np.zeros(points),
        }
    )


def session_dict(source: str, is_live: bool = False) -> dict:
    """A unified session dict in the shape CLAUDE.md documents."""
    drivers = ["VER", "HAM"]
    return {
        "session_info": {
            "year": 2026,
            "gp": "Italian Grand Prix",
            "session_type": "R",
            "session_name": "Race",
            "telemetry_scope": "fastest",
        },
        "telemetry": {d: _telemetry() for d in drivers},
        "laps": pd.DataFrame(
            {
                "Driver": drivers * 2,
                "LapNumber": [1, 1, 2, 2],
                "LapTime": pd.to_timedelta([91.2, 91.9, 90.8, 91.5], unit="s"),
                "Sector1Time": pd.to_timedelta([31.1, 31.4, 30.9, 31.2], unit="s"),
                "Sector2Time": pd.to_timedelta([35.0, 35.2, 34.9, 35.1], unit="s"),
                "Sector3Time": pd.to_timedelta([25.1, 25.3, 25.0, 25.2], unit="s"),
                "IsPitOutLap": [False] * 4,
                "Position": [1, 2, 1, 2],
                "Compound": ["SOFT"] * 4,
            }
        ),
        "stints": pd.DataFrame(
            {
                "Driver": drivers,
                "DriverAcronym": drivers,
                "Stint": [1, 1],
                "Compound": ["SOFT", "MEDIUM"],
                "LapStart": [1, 1],
                "LapEnd": [2, 2],
                "LapCount": [2, 2],
            }
        ),
        "location": {d: _location() for d in drivers},
        # Two cars going round for a minute, so the Replay page has a timeline.
        "positions": pd.DataFrame(
            {
                "Time": np.tile(np.arange(0.0, 60.0, 0.5), len(drivers)),
                "Driver": np.repeat(drivers, 120),
                "X": np.tile(np.cos(np.arange(120) / 20) * 1000, len(drivers)),
                "Y": np.tile(np.sin(np.arange(120) / 20) * 600, len(drivers)),
            }
        ),
        "weather": pd.DataFrame(
            {
                "Time": pd.to_timedelta([0, 600], unit="s"),
                "AirTemp": [21.0, 21.5],
                "TrackTemp": [33.0, 34.0],
                "Humidity": [45.0, 44.0],
                "Pressure": [1011.0, 1011.2],
                "WindSpeed": [3.0, 3.2],
                "WindDirection": [180, 190],
                "Rainfall": [False, False],
            }
        ),
        "race_control": pd.DataFrame(
            {
                "Time": pd.to_timedelta([60], unit="s"),
                "Lap": [1],
                "Category": ["Flag"],
                "Flag": ["GREEN"],
                "Scope": ["Track"],
                "Message": ["GREEN LIGHT - PIT EXIT OPEN"],
            }
        ),
        "compound_colors": {"SOFT": "#da291c", "MEDIUM": "#ffd12e"},
        "circuit_info": {},
        "drivers": pd.DataFrame(
            {
                "driver_number": ["1", "44"],
                "name_acronym": drivers,
                "team_colour": ["#3671c6", "#e80020"],
                "team_name": ["Red Bull", "Ferrari"],
                "full_name": ["Max Verstappen", "Lewis Hamilton"],
            }
        ),
        "source": source,
        "is_live": is_live,
    }


class StubManager:
    """DataSourceManager stand-in: records the selection, returns a dict."""

    def __init__(self, *args, **kwargs):
        self.live = None

    class _FastF1:
        @staticmethod
        def get_available_sessions(year=None):
            return pd.DataFrame(
                {
                    "Year": [2026],
                    "EventName": ["Italian Grand Prix"],
                    "Session1": ["Practice 1"],
                    "Session1DateUtc": [pd.Timestamp("2026-09-04T11:30")],
                    "Session4": ["Qualifying"],
                    "Session4DateUtc": [pd.Timestamp("2026-09-05T14:00")],
                    "Session5": ["Race"],
                    "Session5DateUtc": [pd.Timestamp("2026-09-06T13:00")],
                }
            )

    fastf1 = _FastF1()

    def _is_race_weekend(self):
        return False

    def get_available_replays(self):
        return ["Italian_Grand_Prix_R_20260906_150000.pkl"]

    def get_session_data(self, **selection):
        CALLS.append(selection)
        return session_dict(selection.get("source", "fastf1"))

    def save_replay(self, data, name):
        return f"/tmp/{name}.pkl"


def _app_script():
    """Run the real app with the data manager stubbed out."""
    import app
    from data.runtime_cache import runtime_cache
    from tests.test_app_sources import StubManager

    app.DataSourceManager = StubManager
    runtime_cache.begin_session()  # no cross-test reuse
    app.main()


# LiveF1 (Historical) is no longer offered - see HIST-03.
SOURCE_LABELS = {
    "fastf1": "FastF1 (historical)",
    "replay": "Saved replay",
}


def _press_load(app_test: AppTest) -> AppTest:
    """The picker loads nothing until Load session is pressed (UI-02)."""
    next(button for button in app_test.button if button.label == "Load session").click().run()
    return app_test


def _run_for(source: str) -> AppTest:
    import tests.test_app_sources as module

    module.CALLS.clear()

    app_test = AppTest.from_function(_app_script, default_timeout=60)
    app_test.run()
    assert not app_test.exception, app_test.exception
    app_test.selectbox(key="picker_source").set_value(SOURCE_LABELS[source]).run()
    return _press_load(app_test)


@pytest.fixture(autouse=True)
def _isolate_streamlit_caches():
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


class TestSourcesRenderEndToEnd:
    @pytest.mark.parametrize("source", ["fastf1", "replay"])
    def test_no_exception_and_the_loader_saw_the_source(self, source):
        app_test = _run_for(source)

        assert not app_test.exception, app_test.exception
        assert CALLS, "the loader was never reached"
        assert CALLS[-1]["source"] == source

    def test_replay_passes_the_selected_file(self):
        _run_for("replay")

        assert CALLS[-1]["replay_file"] == "Italian_Grand_Prix_R_20260906_150000.pkl"

    def test_key_panels_render_for_a_historical_session(self):
        app_test = _run_for("fastf1")

        for section, notice in (
            ("Weather", "No weather data"),
            ("Race control", "No race control messages"),
        ):
            _open(app_test, "analysis", analysis_section=section)
            assert not app_test.exception, app_test.exception
            # A panel that fell back to its "no data" notice would show up here.
            notices = " ".join(info.value for info in app_test.info)
            assert notice not in notices

    def test_records_panel_reports_the_session(self):
        app_test = _open(_run_for("fastf1"), "records")

        markdown = " ".join(block.value for block in app_test.markdown)
        assert "Italian Grand Prix" in markdown


def _open(app_test: AppTest, url_path: str, **state) -> AppTest:
    """Switch to one of the navigation's function pages and rerun.

    ``AppTest.switch_page`` only takes file paths; a function page is keyed
    by the hash of its URL path, which is what this sets.
    """
    from streamlit.util import calc_hash

    for key, value in state.items():
        app_test.session_state[key] = value
    app_test._page_hash = calc_hash(url_path)
    app_test.run()
    return app_test


class TestPages:
    """UI-03: a page per job instead of one long scroll."""

    def test_the_page_names_follow_the_guideline(self):
        from tests.replay_fixtures import race_session
        from ui.pages import page_specs

        titles = [title for _, title, _ in page_specs(race_session())]

        assert titles == ["Replay", "Results", "Analysis", "Records"]

    def test_a_live_session_opens_on_the_live_page(self):
        from ui.pages import page_specs

        titles = [title for _, title, _ in page_specs({"is_live": True})]

        assert titles == ["Live", "Records"]

    def test_a_historical_session_opens_on_the_replay(self):
        app_test = _run_for("fastf1")

        assert app_test.get("bidi_component"), "the replay player should be on the page"
        assert not any(exp.label == "Diagnostics" for exp in app_test.expander)

    def test_analysis_draws_only_the_chosen_panel(self):
        app_test = _run_for("fastf1")

        telemetry = _open(app_test, "analysis", analysis_section="Telemetry")
        assert len(telemetry.get("plotly_chart")) == 6  # one per channel
        lap_times = _open(app_test, "analysis", analysis_section="Lap times")
        assert len(lap_times.get("plotly_chart")) == 1

    def test_the_records_and_diagnostics_have_their_own_page(self):
        app_test = _open(_run_for("fastf1"), "records")

        assert any(exp.label == "Diagnostics" for exp in app_test.expander)


# Paths for the real-manager replay test; set by the fixture below.
REPLAY_DIR: str = ""
CACHE_DIR: str = ""


def _scoped_manager_class():
    """A real DataSourceManager confined to the test's temp directories.

    Only the schedule lookup and the race-weekend probe are stubbed (they hit
    the network); replay saving and loading run for real.
    """
    from data.source_manager import DataSourceManager

    class ScopedManager(DataSourceManager):
        def __init__(self, *args, **kwargs):
            super().__init__(cache_dir=CACHE_DIR, replay_dir=REPLAY_DIR)
            self.fastf1 = StubManager._FastF1()

        def _is_race_weekend(self):
            return False

    return ScopedManager


def _real_replay_script():
    """Same app, but a *real* DataSourceManager reading a real replay file.

    This is the path HIST-01 broke: the selector offers bare filenames and the
    loader has to resolve them itself.
    """
    import app
    from data.runtime_cache import runtime_cache
    from tests.test_app_sources import _scoped_manager_class

    app.DataSourceManager = _scoped_manager_class()
    runtime_cache.begin_session()
    app.main()


class TestReplayEndToEndWithARealManager:
    @pytest.fixture(autouse=True)
    def _replay_on_disk(self, tmp_path):
        import tests.test_app_sources as module

        module.REPLAY_DIR = str(tmp_path / "replays")
        module.CACHE_DIR = str(tmp_path / "cache")
        manager = _scoped_manager_class()()
        self.saved = manager.save_replay(session_dict("fastf1"), "Italian_Grand_Prix_R")
        yield

    def test_a_saved_replay_loads_and_renders(self):
        app_test = AppTest.from_function(_real_replay_script, default_timeout=60)
        # Nothing loads on the first run; the Replay load that follows is
        # the path under test.
        app_test.run()

        app_test.selectbox(key="picker_source").set_value("Saved replay").run()
        _press_load(app_test)

        assert not app_test.exception, app_test.exception
        errors = " ".join(err.value for err in app_test.error)
        assert "Failed to load session" not in errors
        _open(app_test, "records")
        markdown = " ".join(block.value for block in app_test.markdown)
        assert "Italian Grand Prix" in markdown


class ProgressManager(StubManager):
    """A loader that reports its steps, as DataSourceManager does."""

    def get_session_data(self, progress=None, **selection):
        for step in ("Timing and laps", "Telemetry 1/2", "Positions, 2 drivers", "Building replay"):
            if progress is not None:
                progress(step)
        CALLS.append({**selection, "progress_given": progress is not None})
        return session_dict(selection.get("source", "fastf1"))


def _progress_script():
    import app
    from data.runtime_cache import runtime_cache
    from tests.test_app_sources import ProgressManager

    app.DataSourceManager = ProgressManager
    runtime_cache.begin_session()
    app.main()


class TestLoadingAndEmptyStates:
    """UI-06: a load says what it is doing; an empty panel says why."""

    def test_the_load_is_shown_as_a_status_with_its_steps(self):
        import tests.test_app_sources as module

        module.CALLS.clear()
        app_test = AppTest.from_function(_progress_script, default_timeout=60)
        app_test.run()
        _press_load(app_test)

        assert not app_test.exception, app_test.exception
        assert CALLS[-1]["progress_given"] is True
        statuses = [
            block for block in app_test.main.children.values() if type(block).__name__ == "Status"
        ]
        assert [block.label for block in statuses] == ["Loaded 2026 Italian – Race"]

    def test_a_panel_says_why_it_is_empty(self):
        def script():
            from ui.layout import render_weather
            from ui.status import DataStatus

            render_weather(
                None, DataStatus.unavailable("FastF1 has no weather data for this session")
            )

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()

        assert [info.value for info in app_test.info] == [
            "FastF1 has no weather data for this session."
        ]

    def test_the_default_wording_is_literal(self):
        from ui.status import DataStatus

        assert DataStatus.empty("weather data").message == "No weather data for this session."
        assert DataStatus.auth_required("Car positions").message == (
            "Car positions need an F1TV subscription token."
        )
