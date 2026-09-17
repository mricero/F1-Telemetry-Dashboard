"""Telemetry Processor - Distance alignment, normalization, color mapping"""

import pandas as pd
import numpy as np
from typing import Dict, Optional


def max_lap_number(laps_df: pd.DataFrame) -> Optional[int]:
    """Highest lap number present in a laps DataFrame (None if unavailable)."""
    if laps_df is None or laps_df.empty or "LapNumber" not in laps_df.columns:
        return None
    numbers = pd.to_numeric(laps_df["LapNumber"], errors="coerce").dropna()
    return int(numbers.max()) if not numbers.empty else None


class TelemetryProcessor:
    """Processes raw telemetry into visualization-ready format."""

    DISTANCE_STEP = 5  # meters - uniform distance grid for alignment

    # Channels that vary continuously and may be linearly interpolated.
    CONTINUOUS_CHANNELS = ("Speed", "Throttle", "RPM")
    # Coded/discrete channels. Gear 4.7 or DRS 9.3 do not exist: blending
    # neighbouring samples would invent values the car never reported, so
    # these take the nearest sample instead.
    DISCRETE_CHANNELS = ("Brake", "nGear", "DRS")

    def __init__(self):
        self.driver_color_map = {}

    def build_driver_color_map(self, drivers_df: pd.DataFrame) -> dict:
        """Map driver acronyms to team colors."""
        color_map = {}
        if drivers_df is None or drivers_df.empty:
            self.driver_color_map = color_map
            return color_map
        for _, row in drivers_df.iterrows():
            acronym = self._first_present(row, "name_acronym")
            if not acronym:
                team = self._first_present(row, "TeamName", "team_name")
                acronym = str(team)[:3].upper() if team else None
            if not acronym:
                continue
            color = self._first_present(row, "team_colour", "TeamColour") or "#888888"
            if not str(color).startswith("#"):
                color = f"#{color}"
            color_map[acronym] = color
        self.driver_color_map = color_map
        return color_map

    @staticmethod
    def _first_present(row, *keys):
        """First non-empty, non-NA value among ``keys`` in a row."""
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            try:
                if pd.isna(value):
                    continue
            except (TypeError, ValueError):
                pass
            if value != "":
                return value
        return None

    def resample_to_distance_grid(
        self, telemetry_df: pd.DataFrame, distance_col: str = "Distance"
    ) -> pd.DataFrame:
        """
        Resample telemetry to uniform 5m distance grid.
        Enables accurate multi-driver comparison at same track position.
        """
        if telemetry_df.empty or distance_col not in telemetry_df.columns:
            return telemetry_df

        # Sort by distance
        df = telemetry_df.sort_values(distance_col).reset_index(drop=True)

        distances = pd.to_numeric(df[distance_col], errors="coerce")
        valid = distances.notna()
        if not valid.any():
            return df
        df = df[valid].reset_index(drop=True)
        distances = distances[valid].to_numpy(dtype=float)

        # Create uniform grid
        max_dist = distances.max()
        if pd.isna(max_dist) or max_dist <= 0:
            return df

        grid = np.arange(0, max_dist, self.DISTANCE_STEP)
        if grid.size == 0:
            return df

        result = pd.DataFrame({distance_col: grid})
        for col in self.CONTINUOUS_CHANNELS:
            if col in df.columns:
                values = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
                result[col] = np.interp(grid, distances, values)
        for col in self.DISCRETE_CHANNELS:
            if col in df.columns:
                result[col] = self._nearest_sample(grid, distances, df[col])

        return result

    @staticmethod
    def _nearest_sample(grid: np.ndarray, distances: np.ndarray, values: pd.Series) -> np.ndarray:
        """Nearest-neighbour resample, preserving the source values verbatim."""
        raw = values.to_numpy()
        if len(distances) == 1:
            return np.repeat(raw, len(grid))
        idx = np.clip(np.searchsorted(distances, grid), 1, len(distances) - 1)
        left, right = distances[idx - 1], distances[idx]
        idx = np.where((grid - left) <= (right - grid), idx - 1, idx)
        return raw[idx]

    def align_drivers_by_distance(
        self, telemetry_dict: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        """Align all drivers to same distance grid."""
        aligned = {}
        for driver, df in telemetry_dict.items():
            aligned[driver] = self.resample_to_distance_grid(df)
        return aligned

    # Gear labels in racing order; the chart pins its axis to this so the
    # categorical values do not sort lexically ("10" before "2").
    GEAR_CATEGORIES = ["N"] + [str(g) for g in range(1, 9)]

    def normalize_units(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize units for consistent display."""
        df = df.copy()
        if "Brake" in df.columns:
            # FastF1: boolean → 0/100, LiveF1: already 0/100
            brake = pd.to_numeric(df["Brake"], errors="coerce")
            if pd.notna(brake.max()) and brake.max() <= 1:
                df["Brake"] = brake * 100
        if "Throttle" in df.columns:
            throttle = pd.to_numeric(df["Throttle"], errors="coerce")
            if pd.notna(throttle.max()) and throttle.max() <= 1:
                df["Throttle"] = throttle * 100
        if "nGear" in df.columns:
            df["Gear"] = self._gear_labels(df["nGear"])
        return df

    @staticmethod
    def _gear_labels(gears: pd.Series) -> pd.Series:
        """Gear numbers as display labels ('N' for neutral).

        Gears are rounded to whole numbers first: resampling or averaging can
        leave fractional values, and '3.0000001' would otherwise become its own
        chart category.
        """
        numeric = pd.to_numeric(gears, errors="coerce").round()
        return numeric.map(
            lambda g: "N" if pd.isna(g) or int(g) == 0 else str(int(g)),
        ).astype(str)

    def process_laps(self, laps_df: pd.DataFrame, drivers_df: pd.DataFrame) -> pd.DataFrame:
        """Clean and enrich lap data for visualization."""
        if laps_df.empty:
            return laps_df
        df = laps_df.copy()
        if "Driver" not in df.columns:
            return df
        # Merge driver info. FastF1 laps carry the acronym in 'Driver' directly,
        # while number-keyed lookups apply to API-style driver tables.
        acronym = None
        if "name_acronym" in drivers_df.columns:
            if "driver_number" in drivers_df.columns:
                number_map = pd.Series(
                    drivers_df["name_acronym"].values, index=drivers_df["driver_number"].values
                ).to_dict()
                mapped = df["Driver"].map(number_map)
            else:
                mapped = pd.Series(pd.NA, index=df.index, dtype=object)
            acronym_map = dict(zip(drivers_df["name_acronym"], drivers_df["name_acronym"]))
            direct = df["Driver"].map(acronym_map).astype(object)
            acronym = direct.where(direct.notna(), mapped.astype(object))
        if acronym is None:
            acronym = df["Driver"]
        df["DriverAcronym"] = acronym.fillna(df["Driver"])
        return df

    def process_stints(
        self, stints_df: pd.DataFrame, latest_lap: Optional[int] = None
    ) -> pd.DataFrame:
        """Prepare stint data for the tire strategy chart.

        When LapStart/LapEnd are missing (live TyreStintSeries arrives without
        lap boundaries yet), bounds are derived per driver from stint order:
        stints stack sequentially, unknown ends stop just before the next
        stint starts, and the running last stint extends to ``latest_lap``
        (highest lap seen in the timing feed).
        """
        if stints_df.empty:
            return stints_df
        df = stints_df.copy()

        if "Compound" in df.columns:
            df["Compound"] = df["Compound"].fillna("Unknown").str.upper()
        else:
            df["Compound"] = "UNKNOWN"

        if {"LapStart", "LapEnd"}.issubset(df.columns) and df[["LapStart", "LapEnd"]].notna().all(
            axis=1
        ).all():
            df["LapCount"] = df["LapEnd"] - df["LapStart"] + 1
            if "LapCount" not in df.columns or df["LapCount"].isna().any():
                df["LapCount"] = df["LapCount"].fillna(1)
        else:
            df = self._derive_stint_bounds(df, latest_lap)
        return df

    @staticmethod
    def _derive_stint_bounds(df: pd.DataFrame, latest_lap: Optional[int]) -> pd.DataFrame:
        """Fill missing LapStart/LapEnd/LapCount from stint ordering."""

        def _num(v):
            try:
                v = int(v)
                return None if pd.isna(v) else v
            except (TypeError, ValueError):
                return None

        sort_cols = [c for c in ("DriverAcronym", "Stint") if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols).reset_index(drop=True)

        group_col = "DriverAcronym" if "DriverAcronym" in df.columns else None
        starts, ends = [], []
        groups = df.groupby(group_col, sort=False) if group_col else [(None, df)]
        for _, grp in groups:
            prev_end = 0
            grp_starts = grp.index.tolist()
            for pos, idx in enumerate(grp_starts):
                row = df.loc[idx]
                ls = _num(row.get("LapStart"))
                le = _num(row.get("LapEnd"))
                start = ls if ls is not None else prev_end + 1
                start = max(start, 1)
                if le is None:
                    next_start = None
                    for later_idx in grp_starts[pos + 1 :]:
                        next_start = _num(df.loc[later_idx].get("LapStart"))
                        if next_start is not None:
                            break
                    if next_start is not None:
                        end = next_start - 1
                    else:
                        # No boundary info ahead: earlier stints collapse to
                        # one lap each, the running final stint stretches to
                        # the latest lap seen in the timing feed.
                        end = (latest_lap or start) if pos == len(grp_starts) - 1 else start
                else:
                    end = le
                end = max(end, start)
                starts.append(start)
                ends.append(end)
                prev_end = end
        df["LapStart"] = starts
        df["LapEnd"] = ends
        df["LapCount"] = df["LapEnd"] - df["LapStart"] + 1
        return df
