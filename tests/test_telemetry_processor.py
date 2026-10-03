"""Tests for processing.telemetry_processor."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from f1dash.processing.telemetry_processor import (
    TelemetryProcessor,
    max_lap_number,
)


@pytest.fixture
def processor():
    return TelemetryProcessor()


class TestResample:
    def test_uniform_grid(self, processor):
        df = pd.DataFrame(
            {
                "Distance": [0, 100, 300],
                "Speed": [100, 200, 400],
            }
        )
        out = processor.resample_to_distance_grid(df)
        # 5 m grid up to 300 -> 60 rows
        assert len(out) == 60
        assert np.allclose(out["Speed"].iloc[20], 200)  # at 100 m

    def test_missing_distance_col_passthrough(self, processor):
        df = pd.DataFrame({"Speed": [1, 2]})
        assert processor.resample_to_distance_grid(df).equals(df)

    def test_discrete_channels_are_not_blended(self, processor):
        """Gear/DRS/Brake are coded values; interpolating invents new ones."""
        df = pd.DataFrame(
            {
                "Distance": [0, 100, 200],
                "Speed": [100.0, 200.0, 300.0],
                "nGear": [2, 6, 8],
                "DRS": [0, 12, 14],
                "Brake": [0, 1, 0],
            }
        )

        out = processor.resample_to_distance_grid(df)

        assert set(out["nGear"]).issubset({2, 6, 8})
        assert set(out["DRS"]).issubset({0, 12, 14})
        assert set(out["Brake"]).issubset({0, 1})
        # Continuous channels still interpolate.
        assert out["Speed"].nunique() > 3

    def test_nearest_sample_picks_closest_distance(self, processor):
        df = pd.DataFrame({"Distance": [0, 10], "nGear": [3, 7]})

        out = processor.resample_to_distance_grid(df)

        by_distance = dict(zip(out["Distance"], out["nGear"], strict=False))
        assert by_distance[0.0] == 3
        assert by_distance[5.0] == 3  # tie -> earlier sample
        # beyond the midpoint the 10 m sample is nearer
        assert all(v == 7 for d, v in by_distance.items() if d > 5)

    def test_non_numeric_distance_is_ignored(self, processor):
        df = pd.DataFrame({"Distance": ["a", "b"], "Speed": [1.0, 2.0]})

        assert processor.resample_to_distance_grid(df).equals(df)

    def test_align_all_drivers(self, processor):
        tel = {
            "HAM": pd.DataFrame({"Distance": [0, 50], "Speed": [10, 20]}),
            "VER": pd.DataFrame({"Distance": [0, 40], "Speed": [30, 60]}),
        }
        aligned = processor.align_drivers_by_distance(tel)
        assert set(aligned) == {"HAM", "VER"}
        assert np.isclose(aligned["VER"]["Distance"].iloc[-1] % 5, 0)


class TestNormalizeUnits:
    def test_bool_brake_and_throttle_scaled(self, processor):
        df = pd.DataFrame(
            {
                "Brake": [0.0, 1.0],
                "Throttle": [0.5, 1.0],
                "nGear": [3, 0],  # 0 == neutral
            }
        )
        out = processor.normalize_units(df)
        assert out["Brake"].max() == 100
        assert out["Throttle"].iloc[0] == 50
        assert list(out["Gear"]) == ["3", "N"]

    def test_fractional_gears_collapse_to_whole_numbers(self, processor):
        """Averaged/interpolated gears must not become their own categories."""
        df = pd.DataFrame({"nGear": [3.0, 3.0000001, 6.4, 0.0, np.nan]})

        out = processor.normalize_units(df)

        assert list(out["Gear"]) == ["3", "3", "6", "N", "N"]
        assert set(out["Gear"]).issubset(set(TelemetryProcessor.GEAR_CATEGORIES))

    def test_empty_frame_does_not_raise(self, processor):
        out = processor.normalize_units(pd.DataFrame({"Brake": [], "Throttle": []}))
        assert out.empty

    def test_already_percent_left_alone(self, processor):
        df = pd.DataFrame({"Brake": [0, 100], "Throttle": [10, 90], "nGear": [1, 2]})
        out = processor.normalize_units(df)
        assert out["Brake"].max() == 100
        assert out["Throttle"].max() == 90


class TestColorMap:
    def test_hash_prefix_added(self, processor):
        drivers = pd.DataFrame(
            [
                {"name_acronym": "HAM", "team_colour": "00d2be"},
                {"name_acronym": "VER", "team_colour": "#3671c6"},
            ]
        )
        cmap = processor.build_driver_color_map(drivers)
        assert cmap["HAM"] == "#00d2be"
        assert cmap["VER"] == "#3671c6"

    def test_missing_acronym_falls_back_to_team(self, processor):
        drivers = pd.DataFrame([{"name_acronym": None, "TeamName": "Ferrari"}])
        cmap = processor.build_driver_color_map(drivers)
        assert "FER" in cmap

    def test_row_without_any_identifier_is_skipped(self, processor):
        """NaN team names used to raise TypeError on the [:3] slice."""
        drivers = pd.DataFrame(
            [
                {"name_acronym": None, "TeamName": np.nan, "team_colour": "abc"},
                {"name_acronym": "NOR", "TeamName": "McLaren", "team_colour": "ff8000"},
            ]
        )

        cmap = processor.build_driver_color_map(drivers)

        assert cmap == {"NOR": "#ff8000"}

    def test_empty_drivers_table(self, processor):
        assert processor.build_driver_color_map(pd.DataFrame()) == {}


class TestProcessLaps:
    def test_direct_acronyms(self, processor):
        laps = pd.DataFrame(
            {
                "Driver": ["HAM", "VER"],
                "LapNumber": [1, 1],
                "LapTime": [timedelta(seconds=91.2), timedelta(seconds=92)],
            }
        )
        drivers = pd.DataFrame({"driver_number": ["44", "1"], "name_acronym": ["HAM", "VER"]})
        out = processor.process_laps(laps, drivers)
        assert set(out["DriverAcronym"]) == {"HAM", "VER"}

    def test_number_keyed_drivers(self, processor):
        laps = pd.DataFrame(
            {"Driver": ["44", "1"], "LapNumber": [1, 1], "LapTime": [timedelta(seconds=91.2)] * 2}
        )
        drivers = pd.DataFrame({"driver_number": ["44", "1"], "name_acronym": ["HAM", "VER"]})
        out = processor.process_laps(laps, drivers)
        assert set(out["DriverAcronym"]) == {"HAM", "VER"}

    def test_unknown_driver_keeps_key(self, processor):
        laps = pd.DataFrame({"Driver": ["99"], "LapNumber": [1], "LapTime": [None]})
        drivers = pd.DataFrame({"driver_number": ["44"], "name_acronym": ["HAM"]})
        out = processor.process_laps(laps, drivers)
        assert out["DriverAcronym"].iloc[0] == "99"


class TestMaxLapNumber:
    def test_basic_and_empty(self):
        df = pd.DataFrame({"LapNumber": ["3", 5, "x", None]})
        assert max_lap_number(df) == 5
        assert max_lap_number(pd.DataFrame()) is None
        assert max_lap_number(None) is None


class TestProcessStints:
    def test_fastf1_style_bounds_kept(self, processor):
        stints = pd.DataFrame(
            [
                {
                    "DriverAcronym": "HAM",
                    "Stint": 1,
                    "Compound": "soft",
                    "LapStart": 1,
                    "LapEnd": 12,
                },
            ]
        )
        out = processor.process_stints(stints)
        assert out["Compound"].iloc[0] == "SOFT"
        assert out["LapCount"].iloc[0] == 12

    def test_live_stints_derive_bounds_with_latest_lap(self, processor):
        stints = pd.DataFrame(
            [
                {"DriverAcronym": "HAM", "Stint": 0, "Compound": "MEDIUM"},
                {"DriverAcronym": "HAM", "Stint": 1, "Compound": "HARD"},
            ]
        )
        out = processor.process_stints(stints, latest_lap=20)
        s0 = out[out["Stint"] == 0].iloc[0]
        s1 = out[out["Stint"] == 1].iloc[0]
        assert s0["LapStart"] == 1
        assert s0["LapEnd"] < s1["LapStart"]  # earlier stints collapse to 1 lap
        assert s1["LapStart"] == 2
        assert s1["LapEnd"] == 20  # running stint stretches to latest lap
        assert s1["LapCount"] == 19

    def test_live_single_stint_no_latest_lap(self, processor):
        stints = pd.DataFrame(
            [
                {"DriverAcronym": "VER", "Stint": 0, "Compound": "SOFT"},
            ]
        )
        out = processor.process_stints(stints)
        assert out["LapCount"].iloc[0] >= 1

    def test_partial_nan_bounds_filled(self, processor):
        stints = pd.DataFrame(
            [
                {
                    "DriverAcronym": "ALB",
                    "Stint": 0,
                    "Compound": "HARD",
                    "LapStart": 1,
                    "LapEnd": None,
                },
            ]
        )
        out = processor.process_stints(stints, latest_lap=8)
        row = out.iloc[0]
        assert row["LapStart"] == 1 and row["LapEnd"] == 8
        assert row["LapCount"] == 8

    def test_compound_column_created_when_absent(self, processor):
        stints = pd.DataFrame([{"DriverAcronym": "LEC", "Stint": 0}])
        out = processor.process_stints(stints)
        assert out["Compound"].iloc[0] == "UNKNOWN"
