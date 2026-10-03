"""Tyre degradation and stint pace from the laps frame (FEAT-03).

Pure transforms: a lap counts toward pace only when it was a clean, timed,
green-flag lap that is neither a pit in-lap nor an out-lap. The remaining lap
times are corrected for the fuel the car had burned, so the trend against tyre
age is the tyre and not the lightening car.
"""

import numpy as np
import pandas as pd

from f1dash.processing.time_utils import seconds_series
from f1dash.processing.track_periods import lap_states

# A race car sheds roughly 1.6 kg of fuel a lap and every kilogram costs about
# 0.03 s a lap, so each lap run is worth about 0.05 s. A single round figure
# is deliberate: the per-circuit value is not published, and the trend it
# removes is the same for every driver in the session.
FUEL_S_PER_LAP = 0.05

# A stint needs this many clean laps before a slope is worth quoting.
MIN_LAPS_FOR_SLOPE = 4

PACE_COLUMNS = [
    "Driver",
    "Stint",
    "Compound",
    "LapNumber",
    "TyreAge",
    "LapTime",
    "FuelCorrected",
]
STINT_COLUMNS = ["Driver", "Stint", "Compound", "Laps", "Slope", "BestLap", "MeanLap"]
COMPOUND_COLUMNS = ["Compound", "Stints", "Laps", "Slope"]


def _flag(laps: pd.DataFrame, column: str) -> pd.Series:
    """A boolean column that defaults to False when absent or missing."""
    if column not in laps.columns:
        return pd.Series(False, index=laps.index)
    return laps[column].fillna(False).astype(bool)


def _stamped(laps: pd.DataFrame, column: str) -> pd.Series:
    """True where a timestamp column holds a value; all False when it is absent."""
    if column not in laps.columns:
        return _flag(laps, "")
    return laps[column].notna()


def stint_pace(
    laps: pd.DataFrame | None,
    track_status: pd.DataFrame | None = None,
    fuel_correct: bool = True,
) -> pd.DataFrame:
    """Clean laps with tyre age and fuel-corrected time, one row per lap.

    Excluded: laps without a time, the first lap, pit in- and out-laps, laps
    under a safety car / VSC / red flag (the laps' ``TrackStatus`` column, else
    the session's track status), and laps FastF1 flags ``IsAccurate == False``.
    ``fuel_correct=False`` leaves ``FuelCorrected`` equal to ``LapTime`` (for
    sessions where fuel loads differ run to run).
    """
    empty = pd.DataFrame(columns=PACE_COLUMNS)
    if laps is None or laps.empty or not {"LapNumber", "LapTime"} <= set(laps.columns):
        return empty
    driver = "DriverAcronym" if "DriverAcronym" in laps.columns else "Driver"
    if driver not in laps.columns or "Compound" not in laps.columns:
        return empty

    number = pd.to_numeric(laps["LapNumber"], errors="coerce")
    seconds = seconds_series(laps["LapTime"])
    neutral = number.map(lap_states(laps, track_status)).notna()
    pit_stamp = _stamped(laps, "PitInTime") | _stamped(laps, "PitOutTime")
    inaccurate = laps["IsAccurate"].eq(False) if "IsAccurate" in laps.columns else _flag(laps, "")
    keep = (
        seconds.notna()
        & number.notna()
        & (number > 1)
        & ~neutral
        & ~_flag(laps, "IsPitOutLap")
        & ~pit_stamp
        & ~inaccurate
        & laps["Compound"].notna()
    )

    stint = pd.to_numeric(laps["Stint"], errors="coerce") if "Stint" in laps.columns else None
    frame = pd.DataFrame(
        {
            "Driver": laps[driver].astype(str),
            "Stint": stint if stint is not None else 1.0,
            "Compound": laps["Compound"].astype(str).str.upper(),
            "LapNumber": number,
            "LapTime": seconds,
        }
    )
    if "TyreLife" in laps.columns:
        frame["TyreAge"] = pd.to_numeric(laps["TyreLife"], errors="coerce")
    else:
        first = frame.groupby(["Driver", "Stint"])["LapNumber"].transform("min")
        frame["TyreAge"] = frame["LapNumber"] - first + 1
    frame = frame[keep & frame["TyreAge"].notna()].copy()
    if frame.empty:
        return empty
    frame["Stint"] = frame["Stint"].fillna(1).astype(int)
    frame["LapNumber"] = frame["LapNumber"].astype(int)
    burned = FUEL_S_PER_LAP * (frame["LapNumber"] - 1) if fuel_correct else 0.0
    frame["FuelCorrected"] = (frame["LapTime"] + burned).round(3)
    return frame[PACE_COLUMNS].sort_values(["Driver", "LapNumber"]).reset_index(drop=True)


def stint_summary(pace: pd.DataFrame) -> pd.DataFrame:
    """Per driver stint: clean laps, fuel-corrected slope (s per lap of tyre age), best, mean.

    ``Slope`` is NaN below ``MIN_LAPS_FOR_SLOPE`` laps; positive means the
    tyre is losing time.
    """
    if pace is None or pace.empty:
        return pd.DataFrame(columns=STINT_COLUMNS)
    rows = []
    for (driver, stint, compound), group in pace.groupby(["Driver", "Stint", "Compound"]):
        slope = np.nan
        ages = group["TyreAge"].to_numpy(dtype=float)
        if len(group) >= MIN_LAPS_FOR_SLOPE and np.ptp(ages) > 0:
            slope = float(np.polyfit(ages, group["FuelCorrected"].to_numpy(dtype=float), 1)[0])
        rows.append(
            {
                "Driver": driver,
                "Stint": int(stint),
                "Compound": compound,
                "Laps": len(group),
                "Slope": slope,
                "BestLap": float(group["FuelCorrected"].min()),
                "MeanLap": float(group["FuelCorrected"].mean()),
            }
        )
    return pd.DataFrame(rows, columns=STINT_COLUMNS)


def compound_degradation(pace: pd.DataFrame) -> pd.DataFrame:
    """Median stint slope per compound (s per lap of tyre age), with stint and lap counts.

    The median over stints keeps one driver's traffic or a lift-and-coast stint
    from setting the compound's figure.
    """
    summary = stint_summary(pace)
    summary = summary[summary["Slope"].notna()]
    if summary.empty:
        return pd.DataFrame(columns=COMPOUND_COLUMNS)
    grouped = summary.groupby("Compound").agg(
        Stints=("Slope", "size"), Laps=("Laps", "sum"), Slope=("Slope", "median")
    )
    return grouped.reset_index()[COMPOUND_COLUMNS]
