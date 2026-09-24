"""The Replay page (FEAT-04, REPLAY-01, REPLAY-04).

The page draws the whole dashboard at the cursor. AppTest cannot render
``st.html``, but it does expose the markup (``get("html")``), so the header
and tower are read from there. The session is the synthetic race of
:mod:`tests.replay_fixtures`: A leads, B passes on lap 3, C pits on lap 4,
A retires on lap 5.
"""

import os
import re

import pytest
from streamlit.testing.v1 import AppTest

from processing.replay import ReplayClock
from tests import replay_fixtures as fx
from ui.replay_view import (
    SPEED_OPTIONS,
    advance,
    next_lap_moment,
    previous_lap_moment,
    session_key,
)

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

KEY = "fastf1:2026:Test Grand Prix:R"


@pytest.fixture(autouse=True)
def _server_view(monkeypatch):
    """These tests drive the server-rendered view (REPLAY-04), the fallback
    behind F1_REPLAY_PLAYER=server; the browser player has its own tests."""
    monkeypatch.setenv("F1_REPLAY_PLAYER", "server")


CURSOR = f"replay_cursor:{KEY}"
PLAYING = f"replay_playing:{KEY}"


def _markup(app: AppTest) -> list[str]:
    return [element.proto.body for element in app.get("html")]


def _header(app: AppTest) -> str:
    return next(body for body in _markup(app) if 'class="f1-header"' in body)


def _tower_order(app: AppTest) -> list[str]:
    tower = next(body for body in _markup(app) if 'class="f1-tower"' in body)
    return re.findall(r'<div class="f1-code">([^<]+)</div>', tower)


def _button(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def _replay_script():
    from tests import replay_fixtures
    from ui.replay_view import render_session_replay

    render_session_replay(replay_fixtures.race_session())


@pytest.fixture
def app() -> AppTest:
    test = AppTest.from_function(_replay_script, default_timeout=60)
    test.run()
    assert not test.exception, test.exception
    return test


class TestOpening:
    def test_it_opens_paused_at_lights_out(self, app):
        assert app.session_state[CURSOR] == fx.LIGHTS_OUT
        assert app.session_state[PLAYING] is False

    def test_the_header_shows_race_time_zero_and_lap_one(self, app):
        header = _header(app)

        assert "Race time" in header
        assert "0:00:00" in header
        assert "1/5" in header

    def test_the_tower_is_the_grid_not_the_result(self, app):
        assert _tower_order(app) == ["A", "B", "C"]

    def test_it_offers_the_step_controls(self, app):
        labels = {button.label for button in app.button}

        for expected in ("Play", "Lights out", "-30s", "-5s", "+5s", "+30s"):
            assert expected in labels
        assert {"Previous lap", "Next lap"} <= labels

    def test_the_speeds_are_offered(self, app):
        assert list(app.selectbox(key=f"replay_speed:{KEY}").options) == list(SPEED_OPTIONS)


class TestStepping:
    def test_plus_thirty_moves_the_cursor_by_thirty(self, app):
        _button(app, "+30s").click().run()

        assert app.session_state[CURSOR] == pytest.approx(fx.LIGHTS_OUT + 30)

    def test_stepping_is_clamped_to_the_end(self, app):
        clock = ReplayClock.from_dict(fx.race_session()["session_info"]["replay_clock"])
        app.session_state[CURSOR] = clock.end - 10
        app.run()
        _button(app, "+30s").click().run()

        assert app.session_state[CURSOR] == pytest.approx(clock.end)

    def test_next_lap_lands_just_after_the_leader_crosses(self, app):
        _button(app, "Next lap").click().run()

        leader_lap_one = fx.RACE_LAP_ENDS["A"][0]
        assert app.session_state[CURSOR] == pytest.approx(leader_lap_one + 1)

    def test_previous_lap_goes_back_one_lap(self, app):
        _button(app, "Next lap").click().run()
        _button(app, "Next lap").click().run()
        _button(app, "Previous lap").click().run()

        assert app.session_state[CURSOR] == pytest.approx(fx.RACE_LAP_ENDS["A"][0] + 1)

    def test_lights_out_rewinds(self, app):
        _button(app, "+30s").click().run()
        _button(app, "Lights out").click().run()

        assert app.session_state[CURSOR] == fx.LIGHTS_OUT

    def test_the_scrubber_moves_the_cursor(self, app):
        app.slider(key=f"replay_slider:{KEY}").set_value(300.0).run()

        assert app.session_state[CURSOR] == pytest.approx(fx.LIGHTS_OUT + 300)

    def test_the_tower_follows_the_cursor(self, app):
        app.slider(key=f"replay_slider:{KEY}").set_value(1300.0 - fx.LIGHTS_OUT).run()

        assert _tower_order(app) == ["B", "A", "C"]

    def test_jump_to_a_pit_stop(self, app):
        jump = app.selectbox(key=f"replay_jump:{KEY}")
        label = next(option for option in jump.options if option.endswith("Pit stop - C"))
        jump.set_value(label).run()

        assert app.session_state[CURSOR] == pytest.approx(fx.C_PIT_IN)
        tower = next(body for body in _markup(app) if 'class="f1-tower"' in body)
        c_row = tower[tower.index('<div class="f1-code">C</div>') :]
        assert "IN PIT" in c_row[: c_row.index("</tr>")]


class TestPlayback:
    def test_play_toggles_the_state(self, app):
        _button(app, "Play").click().run()

        assert app.session_state[PLAYING] is True

    def test_the_scrubber_is_disabled_while_playing(self, app):
        _button(app, "Play").click().run()

        assert app.slider(key=f"replay_slider:{KEY}").disabled

    @pytest.mark.parametrize("speed,expected", [("1x", 1.0), ("5x", 5.0), ("60x", 60.0)])
    def test_a_frame_advances_by_the_chosen_speed(self, speed, expected):
        assert advance(0.0, speed, 9_999.0, 1.0) == pytest.approx(expected)

    def test_playback_stops_at_the_flag(self):
        assert advance(59.9, "60x", 60.0, 1.0) == 60.0


class TestHelpers:
    CLOCK = ReplayClock(start=0.0, lights_out=10.0, end=500.0)
    MARKS = (100.0, 200.0, 300.0)

    def test_next_lap_from_before_the_first_crossing(self):
        assert next_lap_moment(self.MARKS, 50.0, self.CLOCK) == 101.0

    def test_next_lap_from_just_after_a_crossing(self):
        assert next_lap_moment(self.MARKS, 101.0, self.CLOCK) == 201.0

    def test_next_lap_after_the_last_goes_to_the_end(self):
        assert next_lap_moment(self.MARKS, 400.0, self.CLOCK) == 500.0

    def test_previous_lap_before_the_first_is_lights_out(self):
        assert previous_lap_moment(self.MARKS, 101.0, self.CLOCK) == 10.0

    def test_the_cursor_is_kept_per_session(self):
        session = fx.race_session()
        other = {**session, "session_info": {**session["session_info"], "gp": "Monaco"}}

        assert session_key(session) == KEY
        assert session_key(other) != KEY
        assert session_key(session, {"source": "replay", "replay_file": "a"}) == "replay:a"


class TestWithoutLaps:
    def test_it_explains_itself_rather_than_failing(self):
        def script():
            from ui.replay_view import render_session_replay

            render_session_replay({"positions": None, "laps": None, "drivers": None})

        test = AppTest.from_function(script, default_timeout=30)
        test.run()

        assert not test.exception
        assert any("no lap data" in info.value for info in test.info)


# --- through the app: the Replay page is where a session opens ------------


class RaceManager:
    """The app's data manager, serving the synthetic race."""

    def __init__(self, *args, **kwargs):
        self.live = None

    class _FastF1:
        @staticmethod
        def get_available_sessions(year=None):
            import pandas as pd

            return pd.DataFrame({"EventName": ["Test Grand Prix"]})

    fastf1 = _FastF1()

    def _is_race_weekend(self):
        return False

    def get_available_replays(self):
        return []

    def get_session_data(self, **selection):
        from tests import replay_fixtures

        return replay_fixtures.race_session()

    def save_replay(self, data, name):
        return name


def _race_app_script():
    import app
    from data.runtime_cache import runtime_cache
    from tests.test_replay_view import RaceManager

    app.DataSourceManager = RaceManager
    runtime_cache.begin_session()
    app.main()


@pytest.fixture
def race_app() -> AppTest:
    import streamlit as st

    st.cache_data.clear()
    test = AppTest.from_function(_race_app_script, default_timeout=60)
    test.query_params.update(year="2026", gp="Test Grand Prix", session="R")
    test.run()
    assert not test.exception, test.exception
    yield test
    st.cache_data.clear()


class TestTheReplayIsTheMainView:
    def test_a_session_opens_on_the_replay_at_lights_out(self, race_app):
        assert race_app.session_state[CURSOR] == fx.LIGHTS_OUT
        assert _tower_order(race_app) == ["A", "B", "C"]
        assert "0:00:00" in _header(race_app)

    def test_final_result_opens_the_results_view(self, race_app):
        from processing.timing import build_timing_rows

        _button(race_app, "Final result").click().run()

        assert not race_app.exception, race_app.exception
        assert any(sub.value == "Tyre strategy" for sub in race_app.subheader)
        final = [row["code"] for row in build_timing_rows(fx.race_session())]
        assert _tower_order(race_app) == final


# --- the browser player (REPLAY-05) -----------------------------------------


def _player_data(app: AppTest) -> dict:
    import json

    (element,) = app.get("bidi_component")
    return json.loads(element.proto.json)


class TestTheBrowserPlayerIsTheDefault:
    @pytest.fixture
    def player_app(self, monkeypatch) -> AppTest:
        monkeypatch.delenv("F1_REPLAY_PLAYER", raising=False)
        test = AppTest.from_function(_replay_script, default_timeout=60)
        test.run()
        assert not test.exception, test.exception
        return test

    def test_the_page_mounts_the_player_under_the_session_key(self, player_app):
        (element,) = player_app.get("bidi_component")

        assert element.proto.component_name == "f1_replay_player"
        assert element.proto.id.endswith(f"replay_player:{KEY}")

    def test_its_cursor_defaults_to_lights_out(self, player_app):
        state = player_app.session_state[f"replay_player:{KEY}"]

        assert state["cursor"] == fx.LIGHTS_OUT
        assert state["focus"] is None

    def test_it_receives_the_payload_with_styling(self, player_app):
        data = _player_data(player_app)

        assert data["session_key"] == KEY
        assert data["cursor"] == fx.LIGHTS_OUT
        assert set(data["style"]) == {"teams", "flags", "compounds"}
        assert data["style"]["flags"]["SAFETY CAR"][2] == "SC"

    def test_a_jump_chosen_outside_the_player_bumps_the_seek_token(self, player_app):
        before = _player_data(player_app)["seek"]
        jump = player_app.selectbox(key=f"replay_jump:{KEY}")
        jump.set_value(next(o for o in jump.options if o.endswith("Pit stop - C"))).run()

        data = _player_data(player_app)
        assert data["seek"] == before + 1
        assert data["cursor"] == pytest.approx(fx.C_PIT_IN)

    def test_the_server_controls_are_not_duplicated(self, player_app):
        assert not any(button.label == "Lights out" for button in player_app.button)
        assert any(button.label == "Final result" for button in player_app.button) is False
