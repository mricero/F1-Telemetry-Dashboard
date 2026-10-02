"""Page-level behaviour of ``ui.pages`` driven through the real app (AppTest)."""

import os

import pytest
from streamlit.testing.v1 import AppTest

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

from tests.test_app_sources import _app_script, _open, _press_load  # noqa: E402


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
        from ui.replay_view import SEEK_CURSOR_PREFIX, cursor_key

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
        from ui.pages import LAST_PAGE_KEY, PREVIOUS_PAGE_KEY

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
        app_test.segmented_control(key="analysis_section").set_value("Weather").run()
        _open(app_test, "replay")
        _open(app_test, "analysis")

        assert not app_test.exception, app_test.exception
        assert app_test.segmented_control(key="analysis_section").value == "Weather"
