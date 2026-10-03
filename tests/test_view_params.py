"""FEAT-14: the shared-link parts of a view (cursor, section) and FEAT-10 tokens."""

import os

import pytest
from hypothesis import given
from hypothesis import strategies as st
from streamlit.testing.v1 import AppTest

from processing.view_params import format_cursor, parse_cursor

os.environ.setdefault("F1_METRICS_STORE", ":memory:")


class TestCursor:
    def test_a_number_is_read_and_a_list_uses_its_last_value(self):
        assert parse_cursor("120.5", 0, 3000) == 120.5
        assert parse_cursor(["1", "2"], 0, 3000) == 2.0

    @pytest.mark.parametrize("raw", [None, "", "abc", "nan", "inf", "-inf", "1e999x", []])
    def test_anything_unreadable_is_ignored(self, raw):
        assert parse_cursor(raw, 0, 3000) is None

    def test_a_cursor_outside_the_session_is_clamped(self):
        assert parse_cursor("99999", -60, 3000) == 3000
        assert parse_cursor("-500", -60, 3000) == -60

    def test_an_empty_range_reads_nothing(self):
        assert parse_cursor("5", 10, 0) is None

    def test_the_default_is_omitted_and_values_are_short(self):
        assert format_cursor(0.0) is None
        assert format_cursor(0.04) is None
        assert format_cursor(1234.0) == "1234"
        assert format_cursor(1234.56) == "1234.6"
        assert format_cursor(float("nan")) is None

    @given(st.floats(min_value=-100, max_value=5000, allow_nan=False))
    def test_encode_then_decode_lands_within_a_tenth(self, seconds):
        text = format_cursor(seconds)
        back = 0.0 if text is None else parse_cursor(text, -100, 5000)
        assert abs(back - seconds) <= 0.051


def _script():
    from tests.replay_fixtures import race_session
    from ui.replay_view import render_session_replay

    render_session_replay(race_session(), "fastf1:2026:Test Grand Prix:R")


KEY = "replay_cursor:fastf1:2026:Test Grand Prix:R"


class TestReplayLink:
    @pytest.fixture(autouse=True)
    def _server(self, monkeypatch):
        monkeypatch.setenv("F1_REPLAY_PLAYER", "server")

    def _open(self, **params) -> AppTest:
        app = AppTest.from_function(_script, default_timeout=60)
        for name, value in params.items():
            app.query_params[name] = value
        app.run()
        assert not app.exception, app.exception
        return app

    def test_t_opens_the_replay_at_that_moment(self):
        from tests.replay_fixtures import LIGHTS_OUT

        app = self._open(t="125.5")

        assert app.session_state[KEY] == LIGHTS_OUT + 125.5
        assert app.query_params["t"] == ["125.5"]

    def test_a_cursor_past_the_end_is_clamped_to_the_session(self):
        app = self._open(t="999999")

        assert app.session_state[KEY] < 999999

    def test_an_unreadable_cursor_opens_at_lights_out(self):
        from tests.replay_fixtures import LIGHTS_OUT

        app = self._open(t="soon")

        assert app.session_state[KEY] == LIGHTS_OUT

    def test_no_cursor_leaves_the_url_clean(self):
        app = self._open()

        assert "t" not in app.query_params

    def test_moving_the_cursor_updates_the_url(self):
        app = self._open()
        app.session_state[KEY] = app.session_state[KEY] + 60.0
        app.run()

        assert app.query_params["t"] == ["60"]
