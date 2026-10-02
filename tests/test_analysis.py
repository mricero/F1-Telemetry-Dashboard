"""Analysis tables: race trace, tyre pace, speed traps, deleted laps (FEAT-01/03/09/11)."""

import numpy as np
import pandas as pd
import pytest

from processing.analysis import (
    FUEL_SECONDS_PER_LAP,
    default_drivers,
    degradation,
    deleted_laps,
    race_trace,
    speed_trap_ranking,
    stint_pace,
)
from tests import replay_fixtures as fx


@pytest.fixture(scope="module")
def race():
    return fx.race_session()


class TestRaceTrace:
    def test_the_gap_is_to_whoever_completed_the_lap_first(self, race):
        trace = race_trace(race["laps"])
        lap4 = trace[trace["LapNumber"] == 4].set_index("Driver")["Gap"]

        # Lap 4 ends: B 1360.5, A 1361.0, C 1364.0.
        assert lap4.to_dict() == pytest.approx({"A": 0.5, "B": 0.0, "C": 3.5})

    def test_a_reference_driver(self, race):
        trace = race_trace(race["laps"], reference="C")
        lap2 = trace[trace["LapNumber"] == 2].set_index("Driver")["Gap"]

        assert lap2["C"] == 0.0
        assert lap2["A"] == pytest.approx(-2.0)

    def test_laps_the_reference_never_completed_have_no_gap(self, race):
        trace = race_trace(race["laps"], reference="A")

        assert 5 not in set(trace["LapNumber"])

    def test_empty_input(self):
        assert race_trace(pd.DataFrame()).empty
        assert race_trace(None).empty


def _stint_laps(times, compound="MEDIUM", start_lap=2, stint=1.0, driver="VER"):
    count = len(times)
    return pd.DataFrame(
        {
            "Driver": driver,
            "LapNumber": np.arange(start_lap, start_lap + count, dtype=float),
            "LapTime": pd.to_timedelta(times, unit="s"),
            "Stint": stint,
            "Compound": compound,
            "TyreLife": np.arange(1, count + 1, dtype=float),
            "PitInTime": pd.Series([pd.NaT] * count, dtype="timedelta64[ns]"),
            "PitOutTime": pd.Series([pd.NaT] * count, dtype="timedelta64[ns]"),
            "Deleted": False,
            "IsAccurate": True,
        }
    )


class TestTyrePace:
    def test_the_fuel_correction_and_the_slope(self):
        # Raw times rise 0.1 s/lap; the fuel effect hides part of that.
        laps = _stint_laps([90.0 + 0.1 * i for i in range(6)])
        pace = stint_pace(laps, total_laps=7)

        assert pace["Corrected"].iloc[-1] == pytest.approx(90.5 - FUEL_SECONDS_PER_LAP * 0)
        assert pace["Corrected"].iloc[0] == pytest.approx(90.0 - FUEL_SECONDS_PER_LAP * 5)
        slope = degradation(pace)["SecondsPerLap"].iloc[0]
        assert slope == pytest.approx(0.1 + FUEL_SECONDS_PER_LAP, abs=1e-6)

    def test_in_out_neutral_deleted_and_first_laps_are_left_out(self):
        laps = _stint_laps([91.0, 90.0, 90.1, 95.0, 90.2, 90.3, 120.0], start_lap=1)
        laps.loc[3, "Deleted"] = True  # lap 4
        laps.loc[6, "PitInTime"] = pd.Timedelta(700, unit="s")  # lap 7 is an in-lap
        laps["TrackStatus"] = ["1", "1", "1", "1", "1", "4", "1"]  # lap 6 under SC

        kept = stint_pace(laps)["LapNumber"].astype(int).tolist()

        assert kept == [2, 3, 5]

    def test_short_stints_get_no_slope(self):
        pace = stint_pace(_stint_laps([90.0, 90.1, 90.2]))

        assert degradation(pace).empty

    def test_the_race_fixture(self, race):
        pace = stint_pace(race["laps"], race["track_status"], total_laps=5)

        # C's out-lap (lap 5) and its in-lap (lap 4) are not pace laps.
        c_laps = pace[pace["Driver"] == "C"]["LapNumber"].astype(int).tolist()
        assert 4 not in c_laps and 5 not in c_laps


class TestSpeedTraps:
    def test_each_driver_once_fastest_first(self, race):
        ranking = speed_trap_ranking(race["laps"])

        table = ranking["Speed trap"]
        # SpeedST is 300 + the driver's index: C 302, B 301, A 300.
        assert table["Driver"].tolist() == ["C", "B", "A"]
        assert table["Speed"].tolist() == [302.0, 301.0, 300.0]

    def test_absent_traps_are_skipped(self, race):
        assert set(speed_trap_ranking(race["laps"])) == {"Speed trap"}


class TestDeletedLaps:
    def test_lists_deleted_laps_with_the_reason(self):
        laps = _stint_laps([90.0, 89.0, 90.2])
        laps["Deleted"] = [False, True, False]
        laps["DeletedReason"] = [None, "TRACK LIMITS AT TURN 4", None]

        table = deleted_laps(laps)

        assert table.to_dict("records") == [
            {
                "Driver": "VER",
                "LapNumber": 3.0,
                "LapSeconds": 89.0,
                "Reason": "TRACK LIMITS AT TURN 4",
            }
        ]

    def test_none_deleted(self, race):
        assert deleted_laps(race["laps"]).empty


class TestDefaultDrivers:
    def test_the_classification_first(self, race):
        assert default_drivers(race["laps"], race["results"], count=2) == ["B", "C"]

    def test_best_laps_without_results(self, race):
        assert len(default_drivers(race["laps"], None, count=2)) == 2


class TestPanelsRender:
    """The Analysis panels draw the race fixture without falling back to a notice."""

    @pytest.mark.parametrize("panel", ["trace", "pace", "traps", "deleted"])
    def test_each_panel(self, panel):
        from streamlit.testing.v1 import AppTest

        def script(panel):
            from tests import replay_fixtures as fx
            from ui import layout

            race = fx.race_session()
            laps, status = race["laps"], race["track_status"]
            if panel == "trace":
                colors = {"A": "#3671c6", "B": "#ff8000", "C": "#27f4d2"}
                layout.render_race_trace(laps, colors, track_status=status)
            elif panel == "pace":
                layout.render_tyre_pace(laps, status, race["compound_colors"], total_laps=5)
            elif panel == "traps":
                layout.render_speed_traps(laps)
            else:
                layout.render_deleted_laps(laps)

        app_test = AppTest.from_function(script, args=(panel,), default_timeout=30)
        app_test.run()

        assert not app_test.exception, app_test.exception
        notices = [info.value for info in app_test.info]
        if panel == "deleted":
            assert notices == ["No lap times were deleted in this session"]
        else:
            assert notices == []


class TestSections:
    def test_no_race_trace_outside_a_race(self):
        from ui.pages import analysis_sections

        practice = analysis_sections({"session_info": {"session_type": "FP1"}})
        race = analysis_sections({"session_info": {"session_type": "R"}})

        assert "Race trace" not in practice and "Positions" not in practice
        assert "Race trace" in race and "Tyre pace" in race
