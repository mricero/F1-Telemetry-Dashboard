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


class TestSettingsPage:
    """UI-22: caches, file locations and the version."""

    def test_settings_is_offered_before_and_after_a_load(self):
        from ui.pages import PAGE_SETTINGS, page_specs, start_page_specs

        assert PAGE_SETTINGS in [title for _, title, _ in start_page_specs()]
        assert PAGE_SETTINGS in [title for _, title, _ in page_specs({"is_live": False})]

    def test_the_buttons_clear_the_caches(self):
        def script():
            from ui.pages import settings_page

            settings_page()

        from data.runtime_cache import runtime_cache

        runtime_cache.set("session:test", {"x": 1})
        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()
        assert not app_test.exception, app_test.exception

        labels = [button.label for button in app_test.button]
        assert "Clear cached schedules" in labels
        assert "Clear loaded sessions" in labels

        app_test.button(key="settings_clear_sessions").click().run()
        assert runtime_cache.get("session:test") is None
        app_test.button(key="settings_clear_schedules").click().run()
        assert not app_test.exception
        assert any("Schedules cleared" in s.value for s in app_test.success)

    def test_the_version_is_shown(self):
        def script():
            from ui.pages import settings_page

            settings_page()

        from ui.layout import app_version

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()
        assert any(app_version() in md.value for md in app_test.markdown)

    def test_the_cache_delete_needs_a_confirmation(self):
        def script():
            from ui.pages import settings_page

            settings_page()

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()
        assert app_test.button(key="settings_delete_cache").disabled


class TestDirectorySize:
    def test_counts_files_and_tolerates_a_missing_folder(self, tmp_path):
        from ui.layout import directory_size

        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "f.bin").write_bytes(b"x" * 10)
        (tmp_path / "g.bin").write_bytes(b"y" * 5)

        assert directory_size(tmp_path) == 15
        assert directory_size(tmp_path / "missing") == 0


class TestSidebarFooter:
    """DIST-05: the version, and a plain-text update note when one exists."""

    def _run(self, monkeypatch, notice):
        import data.update_check as update_check

        monkeypatch.setattr(update_check, "update_notice", lambda version: notice)

        def script():
            from ui.layout import render_sidebar_footer

            render_sidebar_footer()

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()
        assert not app_test.exception, app_test.exception
        return [caption.value for caption in app_test.sidebar.caption]

    def test_a_newer_release_is_mentioned(self, monkeypatch):
        captions = self._run(monkeypatch, "Update available: v9.0.0 \u2013 run f1dash update")

        assert any(caption.startswith("F1 Replay ") for caption in captions)
        assert "Update available: v9.0.0 \u2013 run f1dash update" in captions

    def test_no_release_says_nothing(self, monkeypatch):
        captions = self._run(monkeypatch, None)

        assert len(captions) == 1
