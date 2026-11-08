"""Pit rejoin predictor (FEAT-02)."""

from typing import ClassVar

import pandas as pd

from f1dash.processing import pit_loss
from f1dash.processing.pit_loss import (
    DEFAULT_NET_PIT_LOSS,
    median_pit_lane_duration,
    pit_lane_durations,
    predict_rejoin,
    rejoin_after_lap,
)
from tests.replay_fixtures import race_session


def _stops(count=4, duration=19.0):
    rows = []
    for index in range(count):
        driver = f"D{index}"
        rows.append({"Driver": driver, "LapNumber": 10.0, "PitInTime": 100.0 + index})
        rows.append({"Driver": driver, "LapNumber": 11.0, "PitOutTime": 100.0 + index + duration})
    laps = pd.DataFrame(rows)
    laps["PitInTime"] = pd.to_timedelta(laps["PitInTime"], unit="s")
    laps["PitOutTime"] = pd.to_timedelta(laps["PitOutTime"], unit="s")
    return laps


class TestPredictRejoin:
    GAPS: ClassVar[dict[str, float]] = {"A": 0.0, "B": 5.0, "C": 12.0, "D": 30.0, "E": 40.0}

    def test_the_leader_drops_behind_the_cars_inside_the_loss(self):
        result = predict_rejoin(self.GAPS, "A", 21.0)

        assert result["position"] == 3
        assert (result["ahead"], result["gap_ahead"]) == ("C", 9.0)
        assert (result["behind"], result["gap_behind"]) == ("D", 9.0)

    def test_a_loss_that_clears_everyone_keeps_the_lead(self):
        result = predict_rejoin(self.GAPS, "A", 2.0)

        assert result["position"] == 1
        assert result["ahead"] is None and result["gap_ahead"] is None
        assert result["behind"] == "B"

    def test_the_last_car_has_nobody_behind(self):
        result = predict_rejoin(self.GAPS, "E", 20.0)

        assert result["position"] == 5
        assert result["behind"] is None
        assert result["ahead"] == "D"

    def test_a_driver_not_running_has_no_prediction(self):
        assert predict_rejoin(self.GAPS, "Z", 20.0) is None


class TestPitLoss:
    def test_a_stop_lasts_from_pit_entry_to_pit_exit(self):
        stops = pit_lane_durations(_stops())

        assert stops["Duration"].tolist() == [19.0] * 4
        assert stops["LapNumber"].tolist() == [10] * 4

    def test_the_fixture_stop_is_thirty_seconds_but_under_a_safety_car_it_is_dropped(self):
        session = race_session()

        # Lap 4 -> 5 of the fixture is 30 s, over the normal range, so it never counts.
        assert pit_lane_durations(session["laps"], session["track_status"]).empty

    def test_stops_under_a_safety_car_are_left_out(self):
        status = pd.DataFrame(
            {"Time": [0.0, 100.0, 200.0], "Status": ["1", "4", "1"], "Message": ["", "", ""]}
        )
        laps = _stops()
        laps["Time"] = pd.to_timedelta([110.0] * len(laps), unit="s")
        laps["LapStartTime"] = pd.to_timedelta([90.0] * len(laps), unit="s")

        assert len(pit_lane_durations(laps)) == 4
        assert pit_lane_durations(laps, status).empty

    def test_without_pit_columns_there_are_no_stops(self):
        laps = race_session()["laps"].drop(columns=["PitInTime", "PitOutTime"])

        assert pit_lane_durations(laps).empty
        assert pit_lane_durations(None).empty

    def test_the_median_is_observed_pit_lane_time(self):
        assert median_pit_lane_duration(_stops()) == (19.0, 4)

    def test_too_few_stops_give_no_median_and_no_stand_in(self):
        assert median_pit_lane_duration(_stops(count=2)) == (None, 2)
        assert median_pit_lane_duration(None) == (None, 0)


class TestNetPitLossIsNotPitLaneTime:
    """The two quantities stay apart: only the net loss moves a car's gap."""

    GAPS: ClassVar[dict[str, float]] = {"A": 0.0, "B": 5.0, "C": 22.0, "D": 40.0}

    def test_a_25_s_pit_lane_and_a_20_s_net_loss_rejoin_on_20(self):
        lane_median, _ = median_pit_lane_duration(_stops(duration=25.0))
        assert lane_median == 25.0  # observed, and not what the prediction takes

        net = predict_rejoin(self.GAPS, "A", 20.0)
        lane = predict_rejoin(self.GAPS, "A", lane_median)

        assert net["net_pit_loss"] == 20.0
        assert (net["position"], net["behind"]) == (2, "C")  # 20.0 is ahead of C at 22.0
        assert (lane["position"], lane["ahead"]) == (3, "C")  # 25 s would be a place worse

    def test_there_is_no_circuit_table_standing_in_for_a_measurement(self):
        # The old seeds were hand-entered and unverified (tasks.md, FEAT-02),
        # labelled pit lane time yet 3.5 s under the 2026 Sao Paulo median.
        assert not hasattr(pit_loss, "PIT_LOSS_SEEDS")
        assert not hasattr(pit_loss, "pit_loss_for")

    def test_the_default_is_labelled_as_an_assumption(self):
        assert DEFAULT_NET_PIT_LOSS == 20.0
        assert "assumption" in pit_loss.NET_PIT_LOSS_NOTE
        assert "not a figure for this circuit" in pit_loss.NET_PIT_LOSS_NOTE


class TestRejoinAfterLap:
    def test_uses_the_gaps_at_the_line_on_that_lap(self):
        laps = race_session()["laps"]

        result = rejoin_after_lap(laps, "B", 3, 20.0)

        assert result is not None and result["position"] >= 1
        assert result["net_pit_loss"] == 20.0

    def test_a_lap_the_driver_did_not_complete_has_no_prediction(self):
        assert rejoin_after_lap(race_session()["laps"], "A", 99, 20.0) is None

    def test_no_laps_no_prediction(self):
        assert rejoin_after_lap(None, "A", 3, 20.0) is None
