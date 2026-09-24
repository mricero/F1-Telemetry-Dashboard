"""One live ingest service per process (IMPROVEMENTS.md LIVE-09).

`DataSourceManager` lived in `st.session_state`, which is per browser tab, so
every viewer opened its own upstream SignalR connection and kept its own copy
of the buffers - five viewers meant five connections and a much better chance
of F1 rate-limiting or blocking the host.
"""

import pytest
from streamlit.testing.v1 import AppTest


def _adapter_id_script():
    import streamlit as st

    from data.live_service import get_live_adapter

    adapter = get_live_adapter()
    st.session_state["adapter_id"] = id(adapter)
    st.session_state["buffered"] = len(adapter.get_buffered_data("CarData.z"))


def _prime_script():
    import streamlit as st

    from data.live_service import get_live_adapter
    from tests import live_fixtures

    adapter = get_live_adapter()
    # A real recorded CarData.z message: base64 of raw-DEFLATE JSON.
    timestamp, payload = live_fixtures.messages("CarData.z")[0]
    adapter.handle_message("CarData.z", payload, timestamp)
    st.session_state["adapter_id"] = id(adapter)
    st.session_state["buffered"] = len(adapter.get_buffered_data("CarData.z"))


@pytest.fixture(autouse=True)
def _fresh_service():
    from data.live_service import reset_live_service

    reset_live_service()
    yield
    reset_live_service()


class TestProcessWideAdapter:
    def test_two_browser_sessions_share_one_adapter(self):
        first = AppTest.from_function(_adapter_id_script, default_timeout=30)
        first.run()
        second = AppTest.from_function(_adapter_id_script, default_timeout=30)
        second.run()

        assert not first.exception and not second.exception
        assert first.session_state["adapter_id"] == second.session_state["adapter_id"]

    def test_data_started_in_one_session_is_visible_in_the_other(self):
        producer = AppTest.from_function(_prime_script, default_timeout=30)
        producer.run()

        consumer = AppTest.from_function(_adapter_id_script, default_timeout=30)
        consumer.run()

        assert producer.session_state["buffered"] > 0
        assert consumer.session_state["buffered"] == producer.session_state["buffered"]

    def test_repeated_calls_in_one_session_return_the_same_object(self):
        from data.live_service import get_live_adapter

        assert get_live_adapter() is get_live_adapter()

    def test_reset_hands_out_a_new_adapter(self):
        from data.live_service import get_live_adapter, reset_live_service

        first = get_live_adapter()
        reset_live_service()

        assert get_live_adapter() is not first


class TestManagerUsesTheSharedAdapter:
    def test_two_managers_share_the_live_adapter(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        assert DataSourceManager().live is DataSourceManager().live

    def test_an_explicit_adapter_still_wins(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.live_adapter import SignalRLiveAdapter
        from data.source_manager import DataSourceManager

        own = SignalRLiveAdapter()

        assert DataSourceManager(live_adapter=own).live is own
