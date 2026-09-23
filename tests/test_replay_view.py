"""The replay panel itself (FEAT-04): controls, cursor, and what it draws."""

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from ui.replay_view import SPEED_OPTIONS, advance, driver_meta, order_html, session_key

KEY = "fastf1:2026:Italian Grand Prix:R"
CURSOR = f"replay_cursor:{KEY}"
PLAYING = f"replay_playing:{KEY}"


def _session(samples: int = 121) -> dict:
    """A short two-car session with a position timeline."""
    seconds = np.arange(samples) * 0.5
    angle = seconds / 30.0 * 2 * np.pi
    frames = []
    for code, offset in (("VER", 0.0), ("HAM", 0.5)):
        frames.append(
            pd.DataFrame(
                {
                    "Time": seconds,
                    "Driver": code,
                    "X": np.cos(angle + offset) * 1000,
                    "Y": np.sin(angle + offset) * 600,
                }
            )
        )
    positions = pd.concat(frames, ignore_index=True)

    trail = pd.DataFrame(
        {
            "Distance": np.linspace(0, 5000, samples),
            "X": np.cos(angle) * 1000,
            "Y": np.sin(angle) * 600,
            "Z": np.zeros(samples),
        }
    )
    return {
        "session_info": {"gp": "Italian Grand Prix", "year": 2026, "session_type": "R"},
        "positions": positions,
        "location": {"VER": trail},
        "circuit_info": {},
        "laps": pd.DataFrame(
            {
                "Driver": ["VER", "HAM", "VER", "HAM"],
                "LapNumber": [1, 1, 2, 2],
                "Position": [1.0, 2.0, 2.0, 1.0],
                "LapTime": pd.to_timedelta([30.0, 31.0, 29.0, 28.0], unit="s"),
                "Time": pd.to_timedelta([30.0, 31.0, 59.0, 59.0], unit="s"),
                # Lights out 2 s into the timeline: the replay opens there.
                "LapStartTime": pd.to_timedelta([2.0, 2.0, 30.0, 31.0], unit="s"),
            }
        ),
        "drivers": pd.DataFrame(
            {
                "driver_number": ["1", "44"],
                "name_acronym": ["VER", "HAM"],
                "team_colour": ["#3671c6", "#00d2be"],
                "team_name": ["Red Bull", "Mercedes"],
            }
        ),
        "is_live": False,
    }


def _replay_script():
    import streamlit as st

    from tests.test_replay_view import _session
    from ui.replay_view import render_session_replay

    st.session_state["rendered"] = True
    render_session_replay(_session())


def _empty_script():
    from ui.replay_view import render_session_replay

    render_session_replay({"positions": None, "laps": None, "drivers": None})


@pytest.fixture
def app() -> AppTest:
    test = AppTest.from_function(_replay_script, default_timeout=60)
    test.run()
    assert not test.exception, test.exception
    return test


class TestControls:
    def test_it_offers_play_and_a_scrubber(self, app):
        labels = [button.label for button in app.button]

        assert "Play" in labels
        assert "Lights out" in labels
        assert len(app.slider) == 1, "the session needs a time scrubber"

    def test_the_scrubber_spans_the_whole_session(self, app):
        scrubber = app.slider[0]

        assert scrubber.min == 0.0
        assert scrubber.max == pytest.approx(60.0, abs=0.5)

    def test_it_starts_at_lights_out_and_paused(self, app):
        assert app.session_state[CURSOR] == 2.0
        assert app.session_state[PLAYING] is False

    def test_the_cursor_is_kept_per_session(self):
        session = _session()
        other = {**session, "session_info": {**session["session_info"], "gp": "Monaco"}}

        assert session_key(session) == KEY
        assert session_key(other) != KEY
        assert session_key(session, {"source": "replay", "replay_file": "a"}) == "replay:a"

    def test_speeds_are_offered(self, app):
        assert list(app.selectbox[0].options) == list(SPEED_OPTIONS)


class TestScrubbing:
    def test_moving_the_scrubber_moves_the_cursor(self, app):
        app.slider[0].set_value(45.0).run()

        assert app.session_state[CURSOR] == pytest.approx(45.0)

    def test_the_race_clock_counts_from_lights_out(self, app):
        app.slider[0].set_value(47.0).run()
        values = [metric.value for metric in app.metric]

        assert "0:00:45" in values

    def test_the_lap_follows_the_cursor(self, app):
        app.slider[0].set_value(45.0).run()
        values = [str(metric.value) for metric in app.metric]

        assert "1" in values  # one lap completed by 45 s

    def test_it_reports_the_cars_on_track_at_that_moment(self, app):
        app.slider[0].set_value(20.0).run()
        captions = " ".join(caption.value for caption in app.caption)

        assert "2 car(s) on track" in captions

    def test_the_map_places_both_cars_at_that_moment(self):
        """AppTest cannot see st.html, so the markup is built directly."""
        from processing.replay import positions_at
        from ui.track_map import build_track_svg

        session = _session()
        markers = positions_at(session["positions"], 20.0)
        for marker in markers:
            marker["team_colour"] = "#3671c6"

        svg = build_track_svg(session["location"], markers=markers)

        assert svg.count("<circle") >= 2  # one node per car
        assert "VER" in svg and "HAM" in svg

    def test_pressing_lights_out_rewinds(self, app):
        app.slider[0].set_value(45.0).run()
        next(button for button in app.button if button.label == "Lights out").click().run()

        assert app.session_state[CURSOR] == 2.0


class TestPlayback:
    def test_play_toggles_the_state(self, app):
        next(button for button in app.button if button.label == "Play").click().run()

        assert app.session_state[PLAYING] is True

    def test_the_scrubber_is_disabled_while_playing(self, app):
        next(button for button in app.button if button.label == "Play").click().run()

        assert app.slider[0].disabled

    @pytest.mark.parametrize("speed,expected", [("1x", 0.5), ("5x", 2.5), ("60x", 30.0)])
    def test_a_frame_advances_by_the_chosen_speed(self, speed, expected):
        assert advance(0.0, speed, 9_999.0, 0.5) == pytest.approx(expected)

    def test_playback_stops_at_the_flag(self):
        assert advance(59.9, "60x", 60.0, 0.5) == 60.0


class TestWithoutATimeline:
    def test_it_explains_itself_rather_than_failing(self):
        test = AppTest.from_function(_empty_script, default_timeout=30)
        test.run()

        assert not test.exception
        assert any("no position timeline" in info.value for info in test.info)


class TestHelpers:
    def test_driver_meta_maps_acronyms_to_teams(self):
        meta = driver_meta(_session()["drivers"])

        assert meta["VER"]["team_name"] == "Red Bull"

    def test_the_order_list_names_positions_and_laps(self):
        markup = order_html([{"code": "VER", "position": 1, "lap": 12}], {})

        assert "VER" in markup and "L12" in markup

    def test_an_empty_order_says_so(self):
        assert "No completed laps" in order_html([], {})
