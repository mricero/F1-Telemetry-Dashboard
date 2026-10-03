"""Deleted laps and track-limit counts (FEAT-11)."""

import numpy as np
import pandas as pd

from f1dash.processing.deleted_laps import deleted_laps, track_limit_counts
from f1dash.ui.lap_panels import deleted_table


def _laps() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "Driver": ["VER", "VER", "HAM", "HAM"],
            "LapNumber": [1.0, 2.0, 1.0, 2.0],
            "LapTime": pd.to_timedelta([91.204, 90.5, 92.0, 91.1], unit="s"),
            "Deleted": [False, True, None, True],
            "DeletedReason": ["", "TRACK LIMITS AT TURN 4 LAP 2 14:07:31", "", None],
        }
    )
    frame["Deleted"] = frame["Deleted"].astype(object)
    return frame


def _control() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta([3000, 3100, 3200, 3300], unit="s"),
            "Category": ["Other", "Flag", "Other", "Other"],
            "Message": [
                "CAR 1 (VER) TIME 1:30.500 DELETED - TRACK LIMITS AT TURN 4 LAP 2 14:07:31",
                "BLACK AND WHITE FLAG FOR CAR 44 (HAM) - TRACK LIMITS",
                "CAR 44 (HAM) TIME 1:31.100 DELETED - TRACK LIMITS AT TURN 9 LAP 2 14:09:00",
                "CAR 1 (VER) TIME 1:30.500 LAP 2 REINSTATED - TRACK LIMITS",
            ],
        }
    )


def _drivers() -> pd.DataFrame:
    return pd.DataFrame({"driver_number": ["1", "44"], "name_acronym": ["VER", "HAM"]})


class TestDeletedLaps:
    def test_lists_each_deleted_lap_with_reason_and_announcement(self):
        out = deleted_laps(_laps(), _control(), _drivers())
        assert len(out) == 2
        ver = out[out["Driver"] == "VER"].iloc[0]
        assert ver["LapNumber"] == 2
        assert ver["LapTime"] == 90.5
        assert ver["Reason"] == "TRACK LIMITS AT TURN 4 LAP 2 14:07:31"
        assert ver["Announced"] == 3000.0

    def test_reason_falls_back_to_the_message(self):
        ham = deleted_laps(_laps(), _control(), _drivers()).query("Driver == 'HAM'").iloc[0]
        assert ham["Reason"] == "TRACK LIMITS AT TURN 9 LAP 2"
        assert ham["Announced"] == 3200.0

    def test_without_race_control_announcement_is_missing(self):
        out = deleted_laps(_laps(), None, None)
        assert len(out) == 2
        assert out["Announced"].isna().all()

    def test_number_in_message_maps_through_the_drivers_table(self):
        control = _control().assign(
            Message=lambda f: f["Message"].str.replace(" (VER)", "", regex=False)
        )
        ver = deleted_laps(_laps(), control, _drivers()).query("Driver == 'VER'").iloc[0]
        assert ver["Announced"] == 3000.0

    def test_no_deleted_laps_is_empty(self):
        laps = _laps().assign(Deleted=False)
        assert deleted_laps(laps, _control(), _drivers()).empty
        assert deleted_laps(None).empty
        assert deleted_laps(laps.drop(columns="Deleted")).empty


class TestTrackLimitCounts:
    def test_warnings_and_deletions_per_driver(self):
        counts = track_limit_counts(_control(), _drivers()).set_index("Driver")
        assert counts.loc["VER", "Deletions"] == 1  # the reinstatement is not counted
        assert counts.loc["VER", "Warnings"] == 0
        assert counts.loc["HAM", "Deletions"] == 1
        assert counts.loc["HAM", "Warnings"] == 1

    def test_ignores_unrelated_and_missing_messages(self):
        control = pd.DataFrame({"Message": ["YELLOW IN TRACK SECTOR 7", np.nan]})
        assert track_limit_counts(control).empty
        assert track_limit_counts(None).empty


class TestDeletedTable:
    def test_formats_times_and_gaps(self):
        table = deleted_table(deleted_laps(_laps(), _control(), _drivers()))
        ver = table[table["Driver"] == "VER"].iloc[0]
        assert ver["LAP TIME"] == "1:30.500"
        assert ver["ANNOUNCED"] == "0:50:00"
        assert ver["Lap"] == "2"

    def test_missing_announcement_is_a_dash(self):
        table = deleted_table(deleted_laps(_laps(), None))
        assert set(table["ANNOUNCED"]) == {"–"}


class TestDeletedLapsInTheApp:
    def test_section_opens(self):
        from f1dash.ui.pages import ANALYSIS_SECTIONS
        from tests.test_app_sources import _open, _run_for

        assert "Deleted laps" in ANALYSIS_SECTIONS
        app_test = _open(_run_for("fastf1"), "analysis", analysis_section="Deleted laps")
        assert not app_test.exception, app_test.exception
