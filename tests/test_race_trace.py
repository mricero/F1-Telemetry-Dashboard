"""The race trace: each car's gap at the timing line, lap by lap (FEAT-01)."""

import numpy as np
import pandas as pd
import pytest

from processing.timing import gap_trace, line_times
from tests.replay_fixtures import RACE_LAP_ENDS, race_session


def _laps(ends: dict[str, list[float]], start: float = 1000.0) -> pd.DataFrame:
    """Laps shaped like FastF1's: Timedelta ``Time`` / ``LapStartTime``, float lap numbers."""
    rows = []
    for code, times in ends.items():
        previous = start
        for lap, end in enumerate(times, start=1):
            rows.append((code, float(lap), end, previous, end - previous))
            previous = end
    frame = pd.DataFrame(rows, columns=["Driver", "LapNumber", "Time", "LapStartTime", "LapTime"])
    for column in ("Time", "LapStartTime", "LapTime"):
        frame[column] = pd.to_timedelta(frame[column].astype("float64"), unit="s")
    return frame


class TestLineTimes:
    def test_session_seconds_per_driver_and_lap(self):
        times = line_times(_laps({"VER": [1090.0, 1180.0]}))

        assert list(times.columns) == ["Driver", "LapNumber", "Time"]
        assert times["Time"].tolist() == [1090.0, 1180.0]
        assert times["LapNumber"].tolist() == [1, 2]

    def test_an_unfinished_lap_has_no_line_time(self):
        times = line_times(_laps({"VER": [1090.0, np.nan]}))

        assert times["LapNumber"].tolist() == [1]

    def test_the_acronym_column_wins_when_processed(self):
        laps = _laps({"1": [1090.0]}).assign(DriverAcronym="VER")

        assert line_times(laps)["Driver"].tolist() == ["VER"]

    def test_no_time_column_means_no_trace(self):
        laps = _laps({"VER": [1090.0]}).drop(columns=["Time"])

        assert line_times(laps).empty


class TestGapTrace:
    def test_gap_to_the_leader_at_each_line(self):
        trace = gap_trace(_laps({"VER": [1090.0, 1180.0], "HAM": [1091.5, 1183.0]}))

        ham = trace[trace["Driver"] == "HAM"].set_index("LapNumber")["Gap"]
        ver = trace[trace["Driver"] == "VER"].set_index("LapNumber")["Gap"]
        assert ham.to_dict() == {1: pytest.approx(1.5), 2: pytest.approx(3.0)}
        assert ver.tolist() == [0.0, 0.0]

    def test_the_leader_is_whoever_crossed_first_on_that_lap(self):
        # B passes A on lap 3 of the synthetic race.
        trace = gap_trace(race_session()["laps"])

        lap3 = trace[trace["LapNumber"] == 3].set_index("Driver")["Gap"]
        assert lap3["B"] == 0.0
        assert lap3["A"] == pytest.approx(0.5)

    def test_gap_to_a_reference_driver_is_signed(self):
        trace = gap_trace(
            _laps({"VER": [1090.0, 1180.0], "HAM": [1091.5, 1179.0]}), reference="HAM"
        )

        ver = trace[trace["Driver"] == "VER"].set_index("LapNumber")["Gap"]
        # Behind the reference is positive, ahead of it negative.
        assert ver.to_dict() == {1: pytest.approx(-1.5), 2: pytest.approx(1.0)}

    def test_laps_the_reference_never_completed_are_dropped(self):
        trace = gap_trace(race_session()["laps"], reference="A")

        # A stops on lap 5: there is no line to measure from.
        assert 5 not in set(trace["LapNumber"])

    def test_a_lapped_car_shows_its_time_gap(self):
        laps = _laps({"VER": [1090.0, 1180.0, 1270.0], "SAR": [1180.0, 1275.0]})

        sar = gap_trace(laps).query("Driver == 'SAR'").set_index("LapNumber")["Gap"]
        # One lap down is still a time at the same line, not a lap count.
        assert sar[1] == pytest.approx(90.0)

    def test_every_completed_lap_is_in_the_trace(self):
        trace = gap_trace(race_session()["laps"])

        completed = sum(int(np.isfinite(end)) for ends in RACE_LAP_ENDS.values() for end in ends)
        assert len(trace) == completed

    def test_an_unknown_reference_gives_an_empty_trace(self):
        assert gap_trace(race_session()["laps"], reference="ZZZ").empty

    def test_empty_laps(self):
        assert gap_trace(pd.DataFrame()).empty
        assert gap_trace(None).empty
