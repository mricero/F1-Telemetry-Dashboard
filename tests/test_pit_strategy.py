"""Live pit strategy: the Strategy tab's rejoin prediction.

The real-race cases read ``tests/fixtures/strategy/sao_paulo_2026_race.json``,
recorded from the SignalR Core feed during the 2026 São Paulo Grand Prix: the
lap-37 order (five lapped cars whose ``GapToLeader`` reads ``1 L``, two
retirements) and the moment VER was in the pit lane with a gap smaller than
the car ahead of him.
"""

import json
from functools import cache
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from f1dash.data.live_adapter import LiveDataProcessor, SignalRLiveAdapter
from f1dash.processing.pit_loss import DEFAULT_NET_PIT_LOSS
from f1dash.processing.pit_strategy import (
    LOSS_MAX_S,
    LOSS_MIN_S,
    OK,
    STALE_AFTER_S,
    UNAVAILABLE,
    UNCERTAIN,
    UNSUPPORTED,
    driver_summary,
    loss_range,
    loss_scenarios,
    net_loss_check,
    observed_pit_lane_median,
    predict_pit_rejoin,
    race_order,
    stale_warning,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "strategy" / "sao_paulo_2026_race.json"


@cache
def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _standings(moment: str) -> pd.DataFrame:
    """The live tower's standings, built by the real parser from the recorded state."""
    data = _fixture()
    return LiveDataProcessor.standings_from_state(
        data[moment]["TimingData"], data["acronyms"], race=True
    )


def _table(rows: list[tuple]) -> pd.DataFrame:
    """Standings rows ``(Driver, GapSeconds, IntervalSeconds, Status)`` in order."""
    return pd.DataFrame(
        [
            {
                "Driver": code,
                "Position": index,
                "GapSeconds": gap,
                "IntervalSeconds": interval,
                "Status": status,
                "LapsDown": 0,
            }
            for index, (code, gap, interval, status) in enumerate(rows, start=1)
        ]
    )


GREEN_FIELD = [
    ("A", 0.0, 0.0, "ON TRACK"),
    ("B", 5.0, 5.0, "ON TRACK"),
    ("C", 12.0, 7.0, "ON TRACK"),
    ("D", 30.0, 18.0, "ON TRACK"),
    ("E", 40.0, 10.0, "ON TRACK"),
]


class TestRaceOrderOnTheRealFeed:
    def test_lapped_cars_are_placed_by_adding_intervals(self):
        order = {e.driver: e for e in race_order(_standings("green"))}

        # COL (P15) +63.785 to the leader, BEA one place back reads "1 L"
        # with an interval of +24.236.
        assert order["COL"].gap == pytest.approx(63.785)
        assert order["BEA"].laps_down == 1
        assert order["BEA"].source == "interval"
        assert order["BEA"].gap == pytest.approx(63.785 + 24.236)

    def test_the_time_axis_never_runs_backwards_on_the_green_snapshot(self):
        gaps = [e.gap for e in race_order(_standings("green")) if e.gap is not None]

        assert gaps == sorted(gaps)
        assert len(gaps) == 20  # 22 cars, two retired

    def test_retired_cars_come_last_without_a_gap(self):
        order = race_order(_standings("green"))

        assert [e.driver for e in order[-2:]] == ["STR", "BOT"]
        assert all(e.gap is None and e.status == "OUT" for e in order[-2:])

    def test_a_car_in_the_pit_lane_keeps_its_place_but_not_its_gap(self):
        ver = next(e for e in race_order(_standings("ver_in_pit")) if e.driver == "VER")

        # The screen showed 11.646 s, less than P2's 12.144 s: a mid-stop gap.
        assert ver.position == 3
        assert ver.gap is None and ver.source == "pit"


class TestPredictionOnTheRealFeed:
    def test_the_leader_drops_behind_the_cars_inside_the_loss(self):
        result = predict_pit_rejoin(_standings("green"), "NOR", 21.0)

        assert result.status == OK
        assert (result.current_position, result.position) == (1, 4)
        assert result.ahead[0].driver == "ANT"
        assert result.behind[0].driver == "PIA"
        assert result.places_lost == 3

    def test_a_stop_can_drop_a_car_behind_lapped_traffic_in_time(self):
        result = predict_pit_rejoin(_standings("green"), "LIN", 21.0)

        assert result.position == 15
        assert result.behind[0].driver == "BEA"  # lapped, placed by the interval chain
        assert any("adding intervals" in w for w in result.warnings)

    def test_a_car_in_the_pit_lane_is_not_moved_ahead_of_the_car_before_it(self):
        result = predict_pit_rejoin(_standings("ver_in_pit"), "NOR", 21.0)

        assert [n.driver for n in result.projected_order[:4]] == ["HAD", "VER", "ANT", "NOR"]
        assert result.position == 4

    def test_a_rejoin_point_beside_the_car_in_the_pit_lane_is_a_range(self):
        # NOR rejoins at 14.0 s, between HAD (12.144) and ANT (16.389), where
        # VER's real gap could be either side.
        result = predict_pit_rejoin(_standings("ver_in_pit"), "NOR", 14.0)

        assert result.status == UNCERTAIN
        assert result.position is None
        assert result.position_range == (2, 3)
        assert any("VER" in w and "pit lane" in w for w in result.warnings)

    def test_the_car_in_the_pit_lane_has_no_prediction(self):
        result = predict_pit_rejoin(_standings("ver_in_pit"), "VER", 21.0)

        assert result.status == UNAVAILABLE
        assert "pit lane" in result.reason
        assert result.position is None

    def test_a_retired_car_has_no_prediction(self):
        result = predict_pit_rejoin(_standings("green"), "STR", 21.0)

        assert result.status == UNAVAILABLE
        assert "out of the race" in result.reason


class TestNeutralisedAndMissing:
    @pytest.mark.parametrize("flag", ["SAFETY CAR", "VSC", "RED"])
    def test_neutralised_running_is_unsupported(self, flag):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, flag=flag)

        assert result.status == UNSUPPORTED
        assert result.position is None
        assert result.current_position == 1

    @pytest.mark.parametrize("flag", ["CHEQUERED", "FINISHED"])
    def test_a_finished_race_has_no_prediction(self, flag):
        assert predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, flag=flag).status == UNAVAILABLE

    def test_a_yellow_flag_is_still_green_flag_pit_loss(self):
        assert predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, flag="YELLOW").status == OK

    def test_qualifying_is_unsupported(self):
        assert predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, race=False).status == UNSUPPORTED

    @pytest.mark.parametrize("standings", [None, pd.DataFrame()])
    def test_no_timing_is_unavailable(self, standings):
        assert predict_pit_rejoin(standings, "A", 21.0).status == UNAVAILABLE

    def test_an_unknown_driver_is_unavailable(self):
        assert predict_pit_rejoin(_table(GREEN_FIELD), "Z", 21.0).status == UNAVAILABLE

    @pytest.mark.parametrize("loss", [float("nan"), -1.0, "fast"])
    def test_a_loss_that_is_not_a_time_is_unavailable(self, loss):
        assert predict_pit_rejoin(_table(GREEN_FIELD), "A", loss).status == UNAVAILABLE

    def test_a_driver_without_a_gap_gets_no_invented_position(self):
        rows = [*GREEN_FIELD[:2], ("C", None, None, "ON TRACK"), *GREEN_FIELD[3:]]

        result = predict_pit_rejoin(_table(rows), "C", 21.0)

        assert result.status == UNAVAILABLE
        assert result.position is None

    def test_an_unbroken_chain_after_a_missing_interval_is_unknown(self):
        rows = [*GREEN_FIELD[:3], ("D", None, None, "ON TRACK"), ("E", None, 10.0, "ON TRACK")]

        result = predict_pit_rejoin(_table(rows), "B", 30.0)  # rejoins at 35.0

        assert result.position is None
        assert result.position_range == (3, 5)  # D and E are somewhere past 12.0


class TestConfidence:
    def test_a_rival_inside_the_margin_makes_it_too_close_to_call(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 29.6)  # 0.4 s ahead of D

        assert result.status == UNCERTAIN
        assert result.position == 3
        assert any("Too close to call" in w for w in result.warnings)

    def test_old_timing_is_flagged(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, data_age_s=95)

        assert result.status == UNCERTAIN
        assert result.warnings[0].startswith("No update for 95 s")

    def test_gaps_out_of_order_are_flagged(self):
        rows = [
            ("A", 0.0, 0.0, "ON TRACK"),
            ("B", 9.0, 9.0, "ON TRACK"),
            ("C", 6.0, 0.1, "ON TRACK"),
        ]

        result = predict_pit_rejoin(_table(rows), "A", 21.0)

        assert result.status == UNCERTAIN
        assert any("out of order" in w for w in result.warnings)

    def test_every_prediction_states_its_assumptions(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0)

        assert any("keeps its current gap" in a for a in result.assumptions)


class TestMoreLossNeverHelps:
    hypothesis = pytest.importorskip("hypothesis")

    from hypothesis import given, settings
    from hypothesis import strategies as hs

    @settings(max_examples=200, deadline=None)
    @given(
        gaps=hs.lists(hs.floats(0.1, 200.0), min_size=1, max_size=21),
        holes=hs.lists(hs.integers(1, 21), max_size=4),
        pick=hs.integers(0, 21),
        losses=hs.tuples(hs.floats(0.0, 60.0), hs.floats(0.0, 60.0)),
    )
    def test_a_larger_loss_cannot_improve_the_position(self, gaps, holes, pick, losses):
        ordered = [0.0, *sorted(gaps)]
        rows = []
        for index, gap in enumerate(ordered):
            known = index not in holes
            interval = gap - ordered[index - 1] if index else 0.0
            rows.append((f"D{index}", gap if known else None, interval if known else None, ""))
        driver = f"D{pick % len(rows)}"
        small, large = sorted(losses)

        before = predict_pit_rejoin(_table(rows), driver, small)
        after = predict_pit_rejoin(_table(rows), driver, large)

        def best(result):
            if result.position is not None:
                return result.position
            return result.position_range[0] if result.position_range else None

        if best(before) is not None and best(after) is not None:
            assert best(after) >= best(before)


class TestObservedPitLaneTime:
    """The feed's pit lane times are observed, kept for reference, never used as net loss."""

    def test_three_stops_give_the_race_median(self):
        times = pd.DataFrame({"Duration": [24.1, 24.5, 23.3, 30.7]})

        assert observed_pit_lane_median(times) == (pytest.approx(24.3), 4)

    def test_fewer_stops_give_no_median_and_no_stand_in(self):
        assert observed_pit_lane_median(pd.DataFrame({"Duration": [24.1, 24.5]})) == (None, 2)
        assert observed_pit_lane_median(None) == (None, 0)

    def test_drive_throughs_and_repairs_are_not_stops(self):
        times = pd.DataFrame({"Duration": [8.0, 60.0, 90.0]})

        assert observed_pit_lane_median(times) == (None, 0)

    def test_the_slider_keeps_the_prd_range_around_the_default(self):
        assert loss_range(DEFAULT_NET_PIT_LOSS) == (LOSS_MIN_S, LOSS_MAX_S)
        low, high = loss_range(38.0)
        assert low == LOSS_MIN_S and high >= 43.0


class TestNetPitLossIsWhatMovesTheCar:
    """A 25 s pit lane with a 20 s net loss rejoins on 20, not 25."""

    # A leads; C sits at 22.0, between a 20 s and a 25 s rejoin point.
    FIELD = (
        ("A", 0.0, 0.0, "ON TRACK"),
        ("B", 5.0, 5.0, "ON TRACK"),
        ("C", 22.0, 17.0, "ON TRACK"),
        ("D", 40.0, 18.0, "ON TRACK"),
    )

    def test_the_prediction_adds_the_net_loss(self):
        lane_median, _ = observed_pit_lane_median(pd.DataFrame({"Duration": [25.0] * 3}))
        assert lane_median == 25.0

        result = predict_pit_rejoin(_table(list(self.FIELD)), "A", 20.0)

        assert result.net_pit_loss == 20.0
        assert result.rejoin_gap == 20.0
        assert (result.position, result.behind[0].driver) == (2, "C")

    def test_the_pit_lane_time_would_have_cost_a_place(self):
        result = predict_pit_rejoin(_table(list(self.FIELD)), "A", 25.0)

        assert (result.position, result.ahead[0].driver) == (3, "C")

    def test_a_net_loss_at_or_above_the_pit_lane_time_is_questioned(self):
        assert net_loss_check(20.0, 25.0) is None
        assert net_loss_check(20.0, None) is None
        assert "at or above the observed pit lane time" in net_loss_check(25.0, 25.0)

    def test_the_tab_starts_from_the_assumption_not_the_pit_lane_median(self):
        app = AppTest.from_function(_strategy_script, args=([25.0, 25.0, 25.0],))
        app.run()

        assert not app.exception
        assert app.slider[0].label == "Estimated net pit loss (s)"
        assert app.slider[0].value == DEFAULT_NET_PIT_LOSS
        captions = [c.value for c in app.caption]
        assert any(c.startswith("Assumption.") for c in captions)
        assert any("median pit lane time 25.0 s" in c and "not used" in c for c in captions)


class TestDriverSummary:
    def test_position_gaps_and_tyre(self):
        stints = pd.DataFrame(
            [
                {"Driver": "NOR", "Stint": 1, "Compound": "INTERMEDIATE", "TyreAge": 16},
                {"Driver": "NOR", "Stint": 2, "Compound": "SOFT", "TyreAge": 20, "New": True},
            ]
        )
        drivers = pd.DataFrame(
            [{"driver_number": "1", "name_acronym": "NOR", "team_name": "McLaren"}]
        )

        summary = driver_summary(_standings("green"), "NOR", stints, drivers)

        assert summary is not None
        assert (summary.position, summary.number, summary.ahead) == (1, "1", None)
        assert summary.behind == "HAD" and summary.to_behind == pytest.approx(7.83)
        assert (summary.compound, summary.tyre_age, summary.tyre_new) == ("SOFT", 20, True)

    def test_a_driver_not_timed_has_no_summary(self):
        assert driver_summary(_standings("green"), "XXX") is None


class TestLivePitTimes:
    """PitLaneTimeCollection shows a stop for ~20 s, then deletes it (seen live)."""

    def _adapter(self) -> SignalRLiveAdapter:
        adapter = SignalRLiveAdapter()
        for entry in _fixture()["pit_times_announced"][:2]:
            number = entry["driver_number"]
            payload = {"PitTimes": {number: {"RacingNumber": number, **entry}}}
            adapter.handle_message("PitLaneTimeCollection", payload, entry["Utc"])
            adapter.handle_message(
                "PitLaneTimeCollection", {"PitTimes": {"_deleted": [number]}}, entry["Utc"]
            )
        return adapter

    def test_a_stop_outlives_its_deletion(self):
        adapter = self._adapter()

        assert [p["Duration"] for p in adapter.recorded_pit_times()] == ["24.1", "24.5"]
        assert adapter.state.get("PitLaneTimeCollection").get("PitTimes") == {}

    def test_a_repeated_announcement_is_kept_once(self):
        adapter = self._adapter()
        entry = _fixture()["pit_times_announced"][0]
        number = entry["driver_number"]
        adapter.handle_message(
            "PitLaneTimeCollection", {"PitTimes": {number: {"RacingNumber": number, **entry}}}
        )

        assert len(adapter.recorded_pit_times()) == 2

    def test_a_new_session_forgets_the_stops(self):
        adapter = self._adapter()
        adapter._reset_session_state()

        assert adapter.recorded_pit_times() == []

    def test_a_subscription_snapshot_holding_a_stop_is_kept(self):
        adapter = SignalRLiveAdapter()
        adapter.seed_state(
            {"PitLaneTimeCollection": {"PitTimes": {"44": {"Duration": "24.5", "Lap": "38"}}}}
        )

        assert adapter.recorded_pit_times()[0]["driver_number"] == "44"

    def test_a_stop_changes_the_snapshot_token(self):
        adapter = SignalRLiveAdapter()
        before = adapter.change_token()
        adapter.handle_message(
            "PitLaneTimeCollection", {"PitTimes": {"3": {"Duration": "23.3", "Lap": "45"}}}
        )

        assert adapter.change_token() != before

    def test_the_frame_names_drivers_and_reads_seconds(self):
        frame = LiveDataProcessor.pit_lane_times(
            self._adapter().recorded_pit_times(), _fixture()["acronyms"]
        )

        assert list(frame["Driver"]) == ["GAS", "HAM"]
        assert list(frame["Duration"]) == [24.1, 24.5]


class TestReplaySnapshots:
    """The same function works on a replay snapshot's standings (offline testing)."""

    def test_a_replay_moment_gives_a_prediction(self):
        from f1dash.processing.replay_model import snapshot_at
        from tests.replay_fixtures import race_session

        session = race_session()
        moment = float(session["session_info"]["replay_clock"]["end"]) - 60.0
        standings = snapshot_at(session, moment)["standings"]
        leader = standings.sort_values("Position")["Driver"].iloc[0]

        result = predict_pit_rejoin(standings, leader, 21.0)

        assert result.status in (OK, UNCERTAIN, UNAVAILABLE)
        assert result.current_position == 1


class TestPanelMarkup:
    def test_the_projected_order_outlines_the_car_that_pits(self):
        from f1dash.ui.strategy import order_html

        result = predict_pit_rejoin(_standings("green"), "NOR", 21.0)
        markup = order_html(result, {"NOR": 1, "ANT": 4}, {})

        assert markup.count('class="focus"') == 1
        assert "PITS" in markup
        assert "nan" not in markup.lower() and "None" not in markup

    def test_a_range_is_written_as_a_range(self):
        from f1dash.ui.strategy import headline_html

        result = predict_pit_rejoin(_standings("ver_in_pit"), "NOR", 14.0)

        assert "P2–P3" in headline_html(result)

    def test_the_summary_shows_missing_values_as_a_dash(self):
        from f1dash.ui.strategy import summary_html

        summary = driver_summary(_standings("green"), "NOR")
        assert summary is not None
        markup = summary_html(summary)

        assert "Nobody ahead" in markup and "None" not in markup

    def test_data_age_comes_from_the_heartbeat(self):
        from f1dash.ui.strategy import data_age_seconds

        now = pd.Timestamp("2026-11-08T18:00:30Z")
        assert data_age_seconds("2026-11-08T18:00:00Z", now) == 30.0
        assert data_age_seconds(None, now) is None


def _strategy_script(durations=(24.1, 24.5, 23.3)):
    import pandas as pd
    import streamlit as st

    from f1dash.ui.strategy import render_pit_strategy
    from tests.test_pit_strategy import _standings

    render_pit_strategy(
        {
            "session_info": {"gp": "São Paulo Grand Prix", "session_type": "Race"},
            "standings": _standings("green"),
            "stints": pd.DataFrame(),
            "drivers": pd.DataFrame(),
            "pit_lane_times": pd.DataFrame({"Duration": list(durations)}),
            "is_live": True,
        }
    )
    st.session_state["rendered"] = True


class TestStrategyTab:
    @pytest.fixture
    def app(self):
        app = AppTest.from_function(_strategy_script, default_timeout=60)
        app.run()
        return app

    def test_it_renders_without_errors(self, app):
        assert not app.exception
        assert app.session_state["rendered"]

    def test_the_slider_starts_at_the_net_loss_assumption(self, app):
        assert app.slider[0].value == DEFAULT_NET_PIT_LOSS

    def test_the_driver_and_loss_survive_a_refresh(self, app):
        app.selectbox[0].select("VER").run()
        app.slider[0].set_value(30.0).run()
        app.run()  # the 3 s fragment refresh

        assert app.selectbox[0].value == "VER"
        assert app.slider[0].value == 30.0

    def test_reset_restores_the_assumption(self, app):
        app.slider[0].set_value(30.0).run()
        app.button[0].click().run()

        assert not app.exception
        assert app.slider[0].value == DEFAULT_NET_PIT_LOSS

    def test_the_tab_is_on_the_live_page(self):
        from f1dash.ui.layout import LIVE_TABS

        assert "Strategy" in LIVE_TABS


def _place(standings, driver, loss):
    return predict_pit_rejoin(standings, driver, loss).position


class TestPitLossMargins:
    """Margins come from the rejoin calculation: re-running it either side must agree."""

    def test_the_margin_to_drop_a_place_is_exact(self):
        table = _table(GREEN_FIELD)
        result = predict_pit_rejoin(table, "A", 21.0)  # rejoins 21.0: P3, D at 30.0 behind

        assert (result.loss_to_drop, result.drop_to) == (9.0, "D")
        assert _place(table, "A", 21.0 + 8.999) == 3
        assert _place(table, "A", 21.0 + 9.0) == 4  # a tie goes to D, already there

    def test_the_margin_to_gain_a_place_must_be_exceeded(self):
        table = _table(GREEN_FIELD)
        result = predict_pit_rejoin(table, "A", 21.0)  # C at 12.0 ahead

        assert (result.loss_to_gain, result.gain_on) == (9.0, "C")
        assert _place(table, "A", 21.0 - 9.0) == 3  # level with C: C keeps it
        assert _place(table, "A", 21.0 - 9.001) == 2

    def test_a_tie_now_needs_any_reduction_to_gain(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 12.0)  # level with C

        assert result.position == 3
        assert result.loss_to_gain == 0.0
        assert result.loss_to_drop == 18.0

    def test_the_last_car_has_no_margin_to_drop(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 50.0)

        assert result.position == 5
        assert result.loss_to_drop is None and result.drop_to is None

    def test_the_leader_after_the_stop_has_nothing_to_gain(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 4.0)
        assert result.position == 1
        assert result.loss_to_gain is None

    def test_a_gain_that_needs_a_negative_loss_is_not_offered(self):
        # B rejoins at 5.0 + 3.0 = 8.0 behind A (0.0): passing A needs > 8 s off a 3 s loss.
        result = predict_pit_rejoin(_table(GREEN_FIELD), "B", 3.0)

        assert result.position == 2
        assert result.loss_to_gain is None

    def test_a_neighbour_without_a_gap_has_no_margin(self):
        rows = [*GREEN_FIELD[:3], ("D", None, None, "ON TRACK"), ("E", 40.0, None, "ON TRACK")]

        result = predict_pit_rejoin(_table(rows), "A", 13.0)  # D sits in [12, 40]

        assert result.position is None
        assert (result.loss_to_drop, result.loss_to_gain) == (None, None)

    def test_a_car_in_the_pit_lane_next_door_has_no_margin(self):
        # NOR rejoins at 21.0 behind ANT; VER (in the pit lane) is two places up.
        result = predict_pit_rejoin(_standings("ver_in_pit"), "NOR", 21.0)

        assert result.gain_on == "ANT"
        assert result.loss_to_gain == pytest.approx(21.0 - 16.389)
        assert result.drop_to == "PIA"

    def test_unavailable_predictions_have_no_margins(self):
        result = predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, flag="SAFETY CAR")

        assert (result.loss_to_drop, result.loss_to_gain) == (None, None)


class TestScenarios:
    def test_three_scenarios_two_seconds_apart(self):
        scenarios = loss_scenarios(_table(GREEN_FIELD), "A", 21.0)

        assert [s.net_pit_loss for s in scenarios] == [19.0, 21.0, 23.0]
        assert [s.position for s in scenarios] == [3, 3, 3]

    def test_scenarios_cross_a_threshold(self):
        # A rejoins at 29.0 (P3); +2 s puts it at 31.0, behind D at 30.0.
        scenarios = loss_scenarios(_table(GREEN_FIELD), "A", 29.0)

        assert [s.position for s in scenarios] == [3, 3, 4]

    def test_the_quicker_scenario_stops_at_zero(self):
        assert loss_scenarios(_table(GREEN_FIELD), "A", 1.0)[0].net_pit_loss == 0.0

    def test_the_middle_scenario_is_the_prediction(self):
        table = _table(GREEN_FIELD)

        assert loss_scenarios(table, "A", 21.0)[1] == predict_pit_rejoin(table, "A", 21.0)

    def test_scenarios_carry_the_flag(self):
        scenarios = loss_scenarios(_table(GREEN_FIELD), "A", 21.0, flag="VSC")

        assert {s.status for s in scenarios} == {UNSUPPORTED}

    def test_an_open_place_stays_a_range_in_the_scenarios(self):
        from f1dash.ui.strategy import scenarios_html

        markup = scenarios_html(loss_scenarios(_standings("ver_in_pit"), "NOR", 14.0))

        assert "P2–P3" in markup and "19.0" not in markup and "12.0 s net" in markup


class TestStaleData:
    def test_the_threshold_is_the_live_pages(self):
        from f1dash.ui.layout import STALE_AFTER_S as LIVE_PAGE_STALE_AFTER_S

        assert STALE_AFTER_S == LIVE_PAGE_STALE_AFTER_S

    @pytest.mark.parametrize("age", [None, 0.0, 30.0])
    def test_fresh_timing_has_no_warning(self, age):
        assert stale_warning(age) is None
        assert predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0, data_age_s=age).status == OK

    def test_stale_timing_uses_the_freshness_wording(self):
        assert stale_warning(31.0).startswith("No update for 31 s")

    def test_stale_timing_is_flagged_on_every_scenario(self):
        scenarios = loss_scenarios(_table(GREEN_FIELD), "A", 21.0, data_age_s=120.0)

        assert all(s.status == UNCERTAIN for s in scenarios)
        assert all(s.warnings[0].startswith("No update for 120 s") for s in scenarios)


class TestMarginCopy:
    def test_the_lines_name_both_cars(self):
        from f1dash.ui.strategy import margin_lines

        lines = margin_lines(predict_pit_rejoin(_table(GREEN_FIELD), "A", 21.0))

        assert lines[0].startswith("9.000 s more net pit loss and D is ahead")
        assert lines[1] == "More than 9.000 s less net pit loss passes C for P2."

    def test_a_range_has_no_margin_lines(self):
        from f1dash.ui.strategy import margin_lines

        assert margin_lines(predict_pit_rejoin(_standings("ver_in_pit"), "NOR", 14.0)) == []
