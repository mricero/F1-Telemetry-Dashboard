"""Page-level behaviour of ``ui.pages`` driven through the real app (AppTest)."""

import os

import pytest
from streamlit.testing.v1 import AppTest

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

from tests.test_app_sources import _app_script, _open, _press_load


@pytest.fixture(autouse=True)
def _isolate_streamlit_caches():
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _loaded() -> AppTest:
    app_test = AppTest.from_function(_app_script, default_timeout=60)
    app_test.run()
    assert not app_test.exception, app_test.exception
    return _press_load(app_test)


def _session_key(app_test: AppTest) -> str:
    keys = [k for k in app_test.session_state if k.startswith("processed:")]
    assert len(keys) == 1, keys
    return keys[0].split(":", 1)[1]


class TestReplayPositionSurvivesOtherPages:
    """UI-10: Replay -> seek -> Records -> Replay remounts at the reported cursor."""

    @pytest.mark.parametrize("away", ["records", "results", "analysis"])
    def test_the_reported_cursor_comes_back(self, away):
        from f1dash.ui.replay_view import SEEK_CURSOR_PREFIX, cursor_key

        app_test = _loaded()
        key = _session_key(app_test)
        # What the player reports while it plays: the live cursor, with no
        # Python seek behind it.
        app_test.session_state[cursor_key(key)] = 42.5
        app_test.run()

        _open(app_test, away)
        _open(app_test, "replay")

        assert not app_test.exception, app_test.exception
        assert app_test.session_state[f"{SEEK_CURSOR_PREFIX}:{key}"] == 42.5

    def test_the_page_is_tracked_by_main_not_by_each_page(self):
        from f1dash.ui.pages import LAST_PAGE_KEY, PREVIOUS_PAGE_KEY

        app_test = _loaded()
        _open(app_test, "records")

        assert app_test.session_state[LAST_PAGE_KEY] == "Records"
        assert app_test.session_state[PREVIOUS_PAGE_KEY] == "Replay"


class TestOneSessionsWorthOfViews:
    """UI-18: per-session processed views are evicted when another loads."""

    def test_three_loads_leave_one_processed_entry(self):
        app_test = _loaded()
        for session in ("FP1", "Q", "R"):
            app_test.selectbox(key="picker_session").set_value(session).run()
            _press_load(app_test)
            assert not app_test.exception, app_test.exception
        app_test.session_state["replay_sector_cards:stale"] = (0.0, "<div></div>")
        app_test.selectbox(key="picker_session").set_value("Q").run()
        _press_load(app_test)

        names = [k for k in app_test.session_state if isinstance(k, str)]
        processed = [k for k in names if k.startswith("processed:")]
        assert len(processed) == 1, processed
        assert processed[0].endswith(":Q")
        assert "replay_sector_cards:stale" not in names


class TestAnalysisSectionSurvivesPageSwitches:
    """UI-21: Weather -> Replay -> Analysis still shows Weather."""

    def test_the_section_comes_back(self):
        app_test = _loaded()
        _open(app_test, "analysis")
        app_test.segmented_control(key="section").set_value("Weather").run()
        _open(app_test, "replay")
        _open(app_test, "analysis")

        assert not app_test.exception, app_test.exception
        assert app_test.segmented_control(key="section").value == "Weather"


class TestSettingsPage:
    """UI-22: the caches a user could not clear (toolbarMode hides Streamlit's own)."""

    def test_clearing_drops_the_schedules_and_the_loaded_sessions(self, monkeypatch):
        from f1dash.data import fastf1_adapter
        from f1dash.data.runtime_cache import runtime_cache
        from f1dash.ui import layout

        cleared = []
        for name in ("_is_race_weekend_cached", "_event_names_cached", "_session_codes_cached"):
            monkeypatch.setattr(getattr(layout, name), "clear", lambda n=name: cleared.append(n))
        runtime_cache.set("session:test", {"x": 1})
        fastf1_adapter._session_cache[("test",)] = object()

        layout.clear_cached_schedules()
        layout.clear_loaded_sessions()

        assert len(cleared) == 3
        assert runtime_cache.get("session:test") is None
        assert not fastf1_adapter._session_cache

    def test_the_page_shows_the_version_and_the_data_locations(self):
        from streamlit.testing.v1 import AppTest

        def script():
            from f1dash.ui.layout import render_settings

            render_settings()

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()
        assert not app_test.exception
        from f1dash.config import __version__

        captions = " ".join(c.value for c in app_test.caption)
        assert f"F1 Replay {__version__}" in captions
        labels = [b.label for b in app_test.button]
        assert labels == ["Clear cached schedules", "Clear loaded sessions"]
        app_test.button[1].click().run()
        assert not app_test.exception
        assert "Loaded sessions cleared" in app_test.success[0].value

    def test_settings_is_offered_for_every_session(self):
        from f1dash.ui.pages import PAGE_SETTINGS, page_specs

        for session in ({"is_live": True}, {"is_live": False}):
            assert PAGE_SETTINGS in [title for _, title, _ in page_specs(session)]

    def test_about_names_the_version(self):
        from f1dash.config import __version__
        from f1dash.ui.layout import menu_items

        assert __version__ in menu_items()["About"]


class TestSectionInTheLink:
    """FEAT-14: ``?section=`` opens an Analysis section.

    The write-back (widget -> URL) is done by the browser for a bound widget,
    which AppTest does not emulate, so only the read side is tested here.
    """

    def _link(self, **params) -> AppTest:
        from streamlit.util import calc_hash

        from tests.test_preferences import SHARED

        app_test = AppTest.from_function(_app_script, default_timeout=60)
        for name, value in {**SHARED, **params}.items():
            app_test.query_params[name] = value
        app_test._page_hash = calc_hash("analysis")
        app_test.run()
        assert not app_test.exception, app_test.exception
        return app_test

    def test_a_link_opens_that_section(self):
        app_test = self._link(section="Lap times")

        assert app_test.segmented_control(key="section").value == "Lap times"

    def test_an_unknown_section_falls_back_to_the_first(self):
        app_test = self._link(section="<b>Nope</b>")

        assert app_test.segmented_control(key="section").value == "Telemetry"
        assert "section" not in app_test.query_params
