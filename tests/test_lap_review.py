"""Speed-trap and sector rankings (FEAT-09)."""

import numpy as np
import pandas as pd

from f1dash.processing.lap_review import sector_ranking, speed_ranking
from f1dash.ui.lap_panels import ranking_table


def _laps() -> pd.DataFrame:
    """Columns as FastF1's get_laps emits them: timedeltas and a nullable Deleted."""
    rows = [
        # driver, lap, s1, s2, s3, I1, I2, FL, ST, deleted, accurate
        ("VER", 1, 28.1, 30.0, 25.0, 300, 290, 310, 330, False, True),
        ("VER", 2, 27.5, 30.5, 25.2, 305, 292, 311, 335, True, True),  # deleted: ignored
        ("HAM", 1, 28.0, 29.9, 25.4, 298, 295, 309, 328, False, True),
        ("HAM", 2, 28.4, 29.7, 25.1, 299, np.nan, 312, 331, False, True),
        ("LEC", 1, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, False, True),
        ("NOR", 1, 27.0, 29.0, 24.0, 320, 300, 320, 340, False, False),  # inaccurate
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "Driver",
            "LapNumber",
            "S1",
            "S2",
            "S3",
            "SpeedI1",
            "SpeedI2",
            "SpeedFL",
            "SpeedST",
            "Deleted",
            "IsAccurate",
        ],
    )
    for index in (1, 2, 3):
        frame[f"Sector{index}Time"] = pd.to_timedelta(frame.pop(f"S{index}"), unit="s")
    frame["Deleted"] = frame["Deleted"].astype(object)
    return frame


class TestSpeedRanking:
    def test_best_valid_reading_per_driver_fastest_first(self):
        ranking = speed_ranking(_laps(), "SpeedST")
        assert list(ranking["Driver"]) == ["HAM", "VER"]
        assert list(ranking["Value"]) == [331.0, 330.0]
        assert list(ranking["Lap"]) == [2, 1]
        assert list(ranking["Gap"]) == [0.0, 1.0]

    def test_deleted_and_inaccurate_laps_never_rank(self):
        ranking = speed_ranking(_laps(), "SpeedI1")
        assert "NOR" not in set(ranking["Driver"])
        assert ranking.set_index("Driver").loc["VER", "Value"] == 300.0

    def test_missing_readings_are_skipped(self):
        assert "LEC" not in set(speed_ranking(_laps(), "SpeedI2")["Driver"])

    def test_missing_column_or_laps_is_empty(self):
        assert speed_ranking(_laps().drop(columns="SpeedST"), "SpeedST").empty
        assert speed_ranking(None, "SpeedST").empty
        assert speed_ranking(pd.DataFrame(), "SpeedST").empty


class TestSectorRanking:
    def test_quickest_valid_sector_across_laps(self):
        ranking = sector_ranking(_laps(), 2)
        assert list(ranking["Driver"]) == ["HAM", "VER"]
        assert ranking.iloc[0]["Value"] == 29.7
        assert ranking.iloc[0]["Lap"] == 2
        assert ranking.iloc[1]["Gap"] == 0.3

    def test_deleted_lap_does_not_set_a_sector_best(self):
        ranking = sector_ranking(_laps(), 1).set_index("Driver")
        assert ranking.loc["VER", "Value"] == 28.1

    def test_bad_sector_number_is_empty(self):
        assert sector_ranking(_laps(), 4).empty


class TestRankingTable:
    def test_formats_once_per_column(self):
        table = ranking_table(sector_ranking(_laps(), 2), "s")
        assert list(table.columns) == ["Pos", "Driver", "TIME", "Lap", "GAP"]
        assert table.iloc[0]["TIME"] == "29.700"
        assert table.iloc[0]["GAP"] == "\u2013"
        assert table.iloc[1]["GAP"] == "+0.300"

    def test_speed_table(self):
        table = ranking_table(speed_ranking(_laps(), "SpeedST"), "km/h")
        assert list(table["SPEED KM/H"]) == ["331", "330"]
        assert list(table["GAP KM/H"]) == ["\u2013", "-1"]


class TestRankingsInTheApp:
    def test_section_is_listed_and_opens(self):
        from f1dash.ui.pages import ANALYSIS_SECTIONS
        from tests.test_app_sources import _open, _run_for

        assert "Rankings" in ANALYSIS_SECTIONS
        app_test = _open(_run_for("fastf1"), "analysis", analysis_section="Rankings")
        assert not app_test.exception, app_test.exception
