"""The laps-based Analysis sections (FEAT-01, FEAT-02, FEAT-03, FEAT-09, FEAT-11).

Builders are checked directly; each ``render_*`` runs once in a real script
context (``AppTest.from_function``) on the synthetic race, and the app-level
smoke test opens every section through the real Analysis page.
"""

import os

import pytest
from streamlit.testing.v1 import AppTest

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

from tests.replay_fixtures import qualifying_session, race_session

COLOURS = {"A": "#3671c6", "B": "#e80020", "C": "#3671c6"}


@pytest.fixture(autouse=True)
def _isolate_streamlit_caches():
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


class TestRaceTraceFigure:
    def test_one_line_per_driver_in_running_order(self):
        from ui.layout import race_trace_figure

        session = race_session()
        fig = race_trace_figure(session["laps"], COLOURS)

        # B won; A stopped on lap 5, so it ends a lap short and last.
        assert [trace.name for trace in fig.data] == ["B", "C", "A"]

    def test_the_leader_is_at_the_top(self):
        from ui.layout import race_trace_figure

        fig = race_trace_figure(race_session()["laps"], COLOURS)

        assert fig.layout.yaxis.autorange == "reversed"
        assert fig.layout.yaxis.title.text == "Gap to leader (s)"

    def test_the_reference_driver_names_the_axis(self):
        from ui.layout import race_trace_figure

        fig = race_trace_figure(race_session()["laps"], COLOURS, reference="C")

        assert fig.layout.yaxis.title.text == "Gap to C (s)"

    def test_the_safety_car_lap_is_shaded(self):
        from ui.layout import race_trace_figure

        session = race_session()
        fig = race_trace_figure(session["laps"], COLOURS, track_status=session["track_status"])

        assert [shape.x0 for shape in fig.layout.shapes] == [3.5]
        assert [note.text for note in fig.layout.annotations] == ["SC"]

    def test_the_second_car_of_a_team_is_dashed(self):
        from ui.layout import race_trace_figure

        fig = race_trace_figure(race_session()["laps"], COLOURS)
        dashes = {trace.name: trace.line.dash for trace in fig.data}

        # C shares A's colour and comes first in running order.
        assert dashes["C"] is None and dashes["A"] == "dash"

    def test_hover_shows_signed_gaps(self):
        from ui.layout import race_trace_figure

        fig = race_trace_figure(race_session()["laps"], COLOURS)
        c = next(trace for trace in fig.data if trace.name == "C")

        assert c.customdata[0] == "+2.000"

    def test_no_lap_times_no_figure(self):
        from ui.layout import race_trace_figure

        laps = race_session()["laps"].drop(columns=["Time"])

        assert race_trace_figure(laps, COLOURS) is None


def _render_race_trace(session_type):
    from tests.replay_fixtures import qualifying_session, race_session
    from ui.layout import render_race_trace

    session = race_session() if session_type == "R" else qualifying_session()
    render_race_trace(
        session["laps"], {}, session["session_info"], session["track_status"], marker_lap=3
    )


class TestRenderRaceTrace:
    def test_a_race_draws_one_chart(self):
        app_test = AppTest.from_function(_render_race_trace, args=("R",)).run()

        assert not app_test.exception, app_test.exception
        assert len(app_test.get("plotly_chart")) == 1
        assert app_test.selectbox[0].options == ["Leader", "A", "B", "C"]

    def test_another_session_says_why_in_one_sentence(self):
        from ui.layout import NOT_A_RACE

        app_test = AppTest.from_function(_render_race_trace, args=("Q",)).run()

        assert not app_test.get("plotly_chart")
        assert [info.value for info in app_test.info] == [NOT_A_RACE]

    def test_a_reference_driver_can_be_picked(self):
        app_test = AppTest.from_function(_render_race_trace, args=("R",)).run()
        app_test.selectbox[0].set_value("C").run()

        assert not app_test.exception, app_test.exception
        assert len(app_test.get("plotly_chart")) == 1


def test_qualifying_fixture_is_not_a_race():
    from processing.timing import is_race_session

    assert not is_race_session(qualifying_session()["session_info"])


NEW_SECTIONS = ["Race trace"]


class TestSectionsInTheApp:
    def test_the_new_sections_are_listed(self):
        from ui.pages import ANALYSIS_SECTIONS

        assert set(NEW_SECTIONS) <= set(ANALYSIS_SECTIONS)

    @pytest.mark.parametrize("section", NEW_SECTIONS)
    def test_each_section_opens_without_an_exception(self, section):
        from tests.test_app_sources import _open, _run_for

        app_test = _open(_run_for("fastf1"), "analysis", analysis_section=section)

        assert not app_test.exception, app_test.exception
