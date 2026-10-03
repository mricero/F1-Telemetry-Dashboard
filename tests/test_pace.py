"""Tyre degradation and stint pace (FEAT-03)."""

import numpy as np
import pandas as pd
import pytest

from f1dash.processing.pace import (
    FUEL_S_PER_LAP,
    compound_degradation,
    stint_pace,
    stint_summary,
)


def _stint_laps(n=10, wear=0.1, base=90.0, driver="VER", compound="MEDIUM", first_lap=2):
    """FastF1-shaped laps: a car that loses ``wear`` s a lap to tyres, gains FUEL a lap."""
    rows = []
    for index in range(n):
        lap = first_lap + index
        seconds = base + wear * index - FUEL_S_PER_LAP * (lap - 1)
        rows.append(
            {
                "Driver": driver,
                "LapNumber": float(lap),
                "LapTime": seconds,
                "Compound": compound,
                "Stint": 1.0,
                "TyreLife": float(index + 1),
                "PitInTime": np.nan,
                "PitOutTime": np.nan,
                "IsPitOutLap": False,
                "IsAccurate": True,
                "TrackStatus": "1",
            }
        )
    frame = pd.DataFrame(rows)
    frame["LapTime"] = pd.to_timedelta(frame["LapTime"], unit="s")
    for column in ("PitInTime", "PitOutTime"):
        frame[column] = pd.to_timedelta(frame[column], unit="s")
    return frame


class TestStintPace:
    def test_fuel_correction_leaves_only_the_tyre_trend(self):
        pace = stint_pace(_stint_laps())

        slope = np.polyfit(pace["TyreAge"], pace["FuelCorrected"], 1)[0]
        assert slope == pytest.approx(0.1, abs=1e-3)
        assert pace["LapTime"].iloc[0] == pytest.approx(90.0 - FUEL_S_PER_LAP)

    def test_in_laps_out_laps_sc_laps_and_inaccurate_laps_are_left_out(self):
        laps = _stint_laps()
        laps.loc[1, "PitInTime"] = pd.to_timedelta(1.0, unit="s")
        laps.loc[2, "PitOutTime"] = pd.to_timedelta(1.0, unit="s")
        laps.loc[3, "TrackStatus"] = "14"
        laps.loc[4, "TrackStatus"] = "6"
        laps.loc[5, "IsAccurate"] = False
        laps.loc[6, "LapTime"] = pd.to_timedelta(np.nan, unit="s")

        pace = stint_pace(laps)

        assert pace["LapNumber"].tolist() == [2, 9, 10, 11]

    def test_the_first_lap_is_never_used(self):
        pace = stint_pace(_stint_laps(first_lap=1))

        assert 1 not in pace["LapNumber"].tolist()

    def test_sc_laps_come_from_session_track_status_without_the_column(self):
        laps = _stint_laps().drop(columns=["TrackStatus"])
        laps["Time"] = pd.to_timedelta(np.arange(1, 11) * 100.0, unit="s")
        laps["LapStartTime"] = laps["Time"] - pd.to_timedelta(90.0, unit="s")
        status = pd.DataFrame({"Time": [250.0, 350.0], "Status": ["4", "1"], "Message": ["", ""]})

        pace = stint_pace(laps, status)

        laps_used = pace["LapNumber"].tolist()
        assert 3 in laps_used and 4 not in laps_used and 5 not in laps_used

    def test_without_fuel_correction_the_time_is_untouched(self):
        pace = stint_pace(_stint_laps(), fuel_correct=False)

        assert (pace["FuelCorrected"] == pace["LapTime"].round(3)).all()

    def test_tyre_age_falls_back_to_laps_into_the_stint(self):
        pace = stint_pace(_stint_laps().drop(columns=["TyreLife"]))

        assert pace["TyreAge"].tolist() == list(range(1, 11))

    def test_missing_columns_give_an_empty_frame(self):
        assert stint_pace(None).empty
        assert stint_pace(pd.DataFrame({"Driver": ["A"]})).empty


class TestDegradation:
    def test_stint_slope_is_seconds_per_lap_of_tyre_age(self):
        summary = stint_summary(stint_pace(_stint_laps(wear=0.08)))

        assert summary["Slope"].iloc[0] == pytest.approx(0.08, abs=1e-3)
        assert summary["Laps"].iloc[0] == 10

    def test_short_stints_have_no_slope(self):
        summary = stint_summary(stint_pace(_stint_laps(n=3)))

        assert summary["Slope"].isna().all()

    def test_compound_figure_is_the_median_of_stints(self):
        stints = [
            _stint_laps(wear=0.05, driver="A"),
            _stint_laps(wear=0.10, driver="B"),
            _stint_laps(wear=0.40, driver="C"),
            _stint_laps(wear=0.02, driver="D", compound="HARD"),
        ]
        # All-NaT timedelta columns trip pandas' concat; the pit stamps are not needed here.
        laps = pd.concat([s.drop(columns=["PitInTime", "PitOutTime"]) for s in stints])

        table = compound_degradation(stint_pace(laps)).set_index("Compound")

        assert table.loc["MEDIUM", "Slope"] == pytest.approx(0.10, abs=1e-3)
        assert table.loc["MEDIUM", "Stints"] == 3
        assert table.loc["HARD", "Slope"] == pytest.approx(0.02, abs=1e-3)

    def test_empty_pace_gives_empty_tables(self):
        assert compound_degradation(stint_pace(None)).empty
        assert stint_summary(stint_pace(None)).empty
