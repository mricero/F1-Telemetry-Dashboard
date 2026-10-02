"""The live view without an F1TV token (IMPROVEMENTS.md LIVE-02, LIVE-10).

Car telemetry and driver positions have needed a subscription token since the
2025 Dutch GP. The live view returned early when both were empty, so timing,
tyres, race control and weather - none of which are gated - were never shown.
"""

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tests import live_fixtures


def _primed_adapter():
    """An adapter holding only the topics that work without a token."""
    from data.live_adapter import SignalRLiveAdapter

    adapter = SignalRLiveAdapter()
    for topic in ("SessionInfo", "DriverList", "TimingData", "TyreStintSeries"):
        for timestamp, payload in live_fixtures.messages(topic):
            adapter.handle_message(topic, payload, timestamp)
    adapter._buffer_topic(
        "RaceControlMessages",
        [
            {
                "Utc": "2026-09-06T13:00:00",
                "Category": "Flag",
                "Flag": "GREEN",
                "Scope": "Track",
                "Message": "GREEN LIGHT",
                "Lap": 1,
            }
        ],
    )
    adapter._buffer_topic(
        "WeatherData",
        [
            {
                "timestamp": "2026-09-06T13:00:00",
                "AirTemp": "21.0",
                "TrackTemp": "33.0",
                "Humidity": "45.0",
                "Pressure": "1011.0",
                "WindSpeed": "3.0",
                "WindDirection": "180",
                "Rainfall": "0",
            }
        ],
    )
    return adapter


def _live_script():
    import streamlit as st

    from data.source_manager import DataSourceManager
    from processing.telemetry_processor import TelemetryProcessor
    from tests.test_live_view import _primed_adapter
    from ui.layout import render_live_dashboard

    class Stub(DataSourceManager):
        def __init__(self):
            super().__init__(live_adapter=_primed_adapter())

    manager = Stub()
    st.session_state["snapshot"] = manager.poll_live_data()
    render_live_dashboard(manager, TelemetryProcessor())


@pytest.fixture
def live_app(monkeypatch):
    import data.source_manager as source_manager

    monkeypatch.setattr(source_manager, "FastF1Adapter", lambda *a, **kw: type("A", (), {})())
    app = AppTest.from_function(_live_script, default_timeout=60)
    app.run()
    return app


class TestDegradedMode:
    """LIVE-02: no token means no car data - everything else still works."""

    def test_the_view_renders_without_telemetry_or_positions(self, live_app):
        assert not live_app.exception, live_app.exception
        snapshot = live_app.session_state["snapshot"]
        assert not snapshot["telemetry"] and not snapshot["location"]

    def test_it_does_not_stop_at_waiting_for_data(self, live_app):
        notices = " ".join(info.value for info in live_app.info)

        assert "Waiting for live data" not in notices

    def test_the_auth_banner_names_the_token(self, live_app):
        text = " ".join(
            [w.value for w in live_app.warning]
            + [i.value for i in live_app.info]
            + [c.value for c in live_app.caption]
        )

        assert "F1TV_SUBSCRIPTION_TOKEN" in text

    def test_the_unauthenticated_panels_are_present(self, live_app):
        labels = [tab.label for tab in live_app.tabs]

        for expected in ("Race control", "Weather", "Tyres"):
            assert expected in labels

    def test_auth_topics_are_named_explicitly(self):
        from data.live_adapter import AUTH_TOPICS

        assert {"CarData.z", "Position.z"} <= AUTH_TOPICS

    def test_gated_topics_are_only_subscribed_with_a_token(self, monkeypatch):
        from data.live_adapter import AUTH_TOPICS, SignalRLiveAdapter

        monkeypatch.delenv("F1TV_SUBSCRIPTION_TOKEN", raising=False)
        without = set(SignalRLiveAdapter().subscribed_topics())

        monkeypatch.setenv("F1TV_SUBSCRIPTION_TOKEN", "a-token")
        with_token = set(SignalRLiveAdapter().subscribed_topics())

        gated_and_wanted = AUTH_TOPICS & set(SignalRLiveAdapter.TELEMETRY_TOPICS)

        assert gated_and_wanted, "the subscription list should include gated topics"
        assert not (without & AUTH_TOPICS)
        assert gated_and_wanted <= with_token


class TestLiveDashboardIsFed:
    """LIVE-10: the timing tower must see live data, not the empty dict."""

    def test_the_polled_snapshot_carries_timing_rows(self, live_app):
        from processing.timing import build_timing_rows

        rows = build_timing_rows(live_app.session_state["snapshot"])

        assert rows, "the tower would render 'No timing data' for a live session"
        assert len(rows) >= 15

    def test_the_header_names_the_session(self, live_app):
        from ui.dashboard import header_html

        markup = header_html(live_app.session_state["snapshot"])

        assert "Bahrain Grand Prix" in markup

    def test_the_tower_has_a_row_per_driver(self, live_app):
        from processing.timing import build_timing_rows
        from ui.dashboard import tower_html

        snapshot = live_app.session_state["snapshot"]
        markup = tower_html(build_timing_rows(snapshot))

        assert "No timing data" not in markup
        assert isinstance(snapshot["laps"], pd.DataFrame)


def _dashboard_spy_script():
    """Run the live view with render_dashboard replaced by a recorder.

    AppTest does not expose ``st.html`` output, so what the dashboard was
    *given* is asserted instead of its markup.
    """
    import streamlit as st

    import ui.layout as layout
    from data.source_manager import DataSourceManager
    from processing.telemetry_processor import TelemetryProcessor
    from tests.test_live_view import _primed_adapter

    def spy(session_data):
        st.session_state["dashboard_input"] = session_data

    layout.render_dashboard = spy

    class Stub(DataSourceManager):
        def __init__(self):
            super().__init__(live_adapter=_primed_adapter())

    layout.render_live_dashboard(Stub(), TelemetryProcessor())


class TestLiveDashboardRendersInTheFragment:
    """LIVE-10: the tower must be rendered from the polled snapshot."""

    @pytest.fixture
    def spy_app(self, monkeypatch):
        import data.source_manager as source_manager

        monkeypatch.setattr(source_manager, "FastF1Adapter", lambda *a, **kw: type("A", (), {})())
        app = AppTest.from_function(_dashboard_spy_script, default_timeout=60)
        app.run()
        assert not app.exception, app.exception
        return app

    def test_the_dashboard_is_rendered_from_live_data(self, spy_app):
        given = spy_app.session_state["dashboard_input"]

        assert given["is_live"] is True
        assert given["session_info"]["gp"] == "Bahrain Grand Prix"
        assert not given["laps"].empty, "the tower would show 'No timing data'"

    def test_the_dashboard_sees_the_drivers(self, spy_app):
        from processing.timing import build_timing_rows

        rows = build_timing_rows(spy_app.session_state["dashboard_input"])

        assert len(rows) >= 15

    def test_the_duplicate_timing_dataframe_tab_is_gone(self, live_app):
        labels = [tab.label for tab in live_app.tabs]

        assert "Timing" not in labels
        assert "Race control" in labels

    def test_the_app_skips_the_static_dashboard_for_live_sessions(self):
        import inspect

        from ui.pages import live_page, page_specs, results_page

        # The pre-poll dict is empty for a live session, so rendering the
        # dashboard from it is what LIVE-10 removed: a live session has no
        # Results page, and the Live page renders only from the fragment.
        pages = [page for page, _, _ in page_specs({"is_live": True})]
        assert results_page not in pages
        assert "render_dashboard" not in inspect.getsource(live_page)


def _full_feed_script():
    import streamlit as st

    from data.live_adapter import SignalRLiveAdapter
    from data.source_manager import DataSourceManager
    from processing.telemetry_processor import TelemetryProcessor
    from tests import live_fixtures
    from ui.layout import render_live_dashboard

    adapter = SignalRLiveAdapter()
    messages = []
    for topic in live_fixtures.available_topics():
        for timestamp, payload in live_fixtures.messages(topic):
            messages.append((timestamp, topic, payload))
    for timestamp, topic, payload in sorted(messages, key=lambda m: m[0]):
        adapter.handle_message(topic, payload, timestamp)

    manager = DataSourceManager(live_adapter=adapter)
    st.session_state["snapshot"] = manager.poll_live_data()
    render_live_dashboard(manager, TelemetryProcessor())


class TestFullFeedThroughTheRealIngestPath:
    """Every recorded topic, raw, through handle_message (the SignalR Core path)."""

    @pytest.fixture
    def app(self, monkeypatch):
        import data.source_manager as source_manager

        monkeypatch.setattr(source_manager, "FastF1Adapter", lambda *a, **kw: type("A", (), {})())
        app = AppTest.from_function(_full_feed_script, default_timeout=60)
        app.run()
        return app

    def test_it_renders_without_errors(self, app):
        assert not app.exception

    def test_the_tower_follows_the_timing_screen(self, app):
        from processing.timing import build_timing_rows

        snapshot = app.session_state["snapshot"]
        rows = build_timing_rows(snapshot)

        assert not snapshot["standings"].empty
        assert [row["code"] for row in rows] == list(snapshot["standings"]["Driver"])
        assert rows[0]["gap"] == "LEADER"

    def test_the_feed_state_is_shown(self, app):
        html = " ".join(str(element.proto) for element in app.get("html"))

        assert "OFFLINE" in html  # no client started in this test


class TestOnlyTheOpenTabIsDrawn:
    """LIVE-35: with the Weather tab open, the telemetry figures are not rebuilt."""

    def test_the_weather_tab_draws_one_chart(self, live_app):
        from ui.layout import LIVE_TAB_KEY

        live_app.session_state[LIVE_TAB_KEY] = "Weather"
        live_app.run()

        assert not live_app.exception, live_app.exception
        charts = live_app.get("plotly_chart")
        assert len(charts) == 1


class TestTokenHelper:
    """LIVE-24: the token line and the paste box."""

    @staticmethod
    def _jwt(exp: int) -> str:
        import base64
        import json

        def part(data: dict) -> str:
            raw = json.dumps(data).encode()
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        return f"{part({'alg': 'none'})}.{part({'exp': exp})}.sig"

    def test_an_expired_token_says_so(self):
        from datetime import UTC, datetime

        from ui.layout import token_line

        now = datetime(2026, 10, 2, tzinfo=UTC)
        expired = self._jwt(int(datetime(2026, 9, 30, tzinfo=UTC).timestamp()))
        valid = self._jwt(int(datetime(2026, 10, 5, 12, tzinfo=UTC).timestamp()))

        assert "expired" in token_line(expired, now=now)
        assert "3 more day" in token_line(valid, now=now)
        assert token_line(None).startswith("No subscription token")

    def test_saving_writes_exactly_one_line(self, tmp_path, monkeypatch):
        from data.token_store import save_subscription_token

        env = tmp_path / ".env"
        env.write_text(
            "LOG_LEVEL=INFO\nF1TV_SUBSCRIPTION_TOKEN=old\nexport F1TV_SUBSCRIPTION_TOKEN=older\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("F1TV_SUBSCRIPTION_TOKEN", raising=False)

        save_subscription_token("new-token", env)

        lines = env.read_text(encoding="utf-8").splitlines()
        assert lines == ["LOG_LEVEL=INFO", "F1TV_SUBSCRIPTION_TOKEN=new-token"]

    def test_a_multi_line_value_is_refused(self, tmp_path):
        from data.token_store import save_subscription_token

        with pytest.raises(ValueError):
            save_subscription_token("a\nb", tmp_path / ".env")
