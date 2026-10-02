"""Pit rejoin predictor (FEAT-02)."""

from typing import ClassVar

import pandas as pd

from processing.pit_loss import (
    DEFAULT_PIT_LOSS,
    pit_loss_for,
    pit_stop_durations,
    predict_rejoin,
    rejoin_after_lap,
    seeded_pit_loss,
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
        stops = pit_stop_durations(_stops())

        assert stops["Duration"].tolist() == [19.0] * 4
        assert stops["LapNumber"].tolist() == [10] * 4

    def test_the_fixture_stop_is_thirty_seconds_but_under_a_safety_car_it_is_dropped(self):
        session = race_session()

        # Lap 4 -> 5 of the fixture is 30 s, over the normal range, so it never counts.
        assert pit_stop_durations(session["laps"], session["track_status"]).empty

    def test_stops_under_a_safety_car_are_left_out(self):
        status = pd.DataFrame(
            {"Time": [0.0, 100.0, 200.0], "Status": ["1", "4", "1"], "Message": ["", "", ""]}
        )
        laps = _stops()
        laps["Time"] = pd.to_timedelta([110.0] * len(laps), unit="s")
        laps["LapStartTime"] = pd.to_timedelta([90.0] * len(laps), unit="s")

        assert len(pit_stop_durations(laps)) == 4
        assert pit_stop_durations(laps, status).empty

    def test_without_pit_columns_there_are_no_stops(self):
        laps = race_session()["laps"].drop(columns=["PitInTime", "PitOutTime"])

        assert pit_stop_durations(laps).empty
        assert pit_stop_durations(None).empty

    def test_a_known_circuit_uses_its_seed_when_the_session_has_too_few_stops(self):
        seconds, source = pit_loss_for("Singapore Grand Prix", None)

        assert seconds == seeded_pit_loss("Singapore Grand Prix")
        assert source == "typical for this circuit"

    def test_an_unknown_circuit_falls_back_to_the_default(self):
        assert pit_loss_for("Test Grand Prix", None) == (DEFAULT_PIT_LOSS, "default")

    def test_enough_session_stops_override_the_seed(self):
        assert pit_loss_for("Singapore Grand Prix", _stops()) == (19.0, "median of 4 stops")


class TestRejoinAfterLap:
    def test_uses_the_gaps_at_the_line_on_that_lap(self):
        laps = race_session()["laps"]

        result = rejoin_after_lap(laps, "B", 3, 20.0)

        assert result is not None and result["position"] >= 1
        assert result["loss"] == 20.0

    def test_a_lap_the_driver_did_not_complete_has_no_prediction(self):
        assert rejoin_after_lap(race_session()["laps"], "A", 99, 20.0) is None

    def test_no_laps_no_prediction(self):
        assert rejoin_after_lap(None, "A", 3, 20.0) is None
