"""Per-browser-session behaviour of app.py (CACHE-01).

Streamlit's ``session_state`` is per browser tab, but ``runtime_cache`` is
process-wide: a second viewer must not evict the first viewer's sessions.
"""

from streamlit.testing.v1 import AppTest


def _init_script():
    from f1dash import app
    from f1dash.data.runtime_cache import runtime_cache

    app.init_browser_session()
    runtime_cache.set("session|primed", {"marker": "first viewer"})


def _second_viewer_script():
    import streamlit as st

    from f1dash import app
    from f1dash.data.runtime_cache import runtime_cache

    app.init_browser_session()
    st.session_state["survived"] = runtime_cache.get("session|primed")


class TestBrowserSessions:
    def test_a_second_viewer_does_not_wipe_the_shared_cache(self):
        first = AppTest.from_function(_init_script, default_timeout=30)
        first.run()
        assert not first.exception

        second = AppTest.from_function(_second_viewer_script, default_timeout=30)
        second.run()

        assert not second.exception
        assert second.session_state["survived"] == {"marker": "first viewer"}

    def test_init_is_idempotent_within_one_session(self):
        app_test = AppTest.from_function(_second_viewer_script, default_timeout=30)
        app_test.run()
        first_manager = app_test.session_state["data_manager"]
        app_test.run()

        assert app_test.session_state["data_manager"] is first_manager
