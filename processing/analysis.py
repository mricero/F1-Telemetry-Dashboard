"""Session analysis tables for the Analysis page.

Pure transforms over the unified session dict's ``laps`` frame (FastF1 shape:
``Time`` is the session time at the lap's end) and its ``track_status``:

* :func:`race_trace` - gap to the leader, or to a reference driver, per lap
  (FEAT-01);
* :func:`stint_pace` and :func:`degradation` - fuel-corrected lap time against
  tyre age, and the per-stint slope (FEAT-03);
* :func:`speed_trap_ranking` - the I1/I2/FL/ST ranking (FEAT-09);
* :func:`deleted_laps` - laps the stewards deleted, with the reason (FEAT-11);
* :func:`default_drivers` - the drivers a chart starts with (UX-03).

No Streamlit and no network here; the charts live in ``ui.layout``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from processing.time_utils import seconds_series
from processing.track_periods import lap_states

# Lap time gained per lap of fuel burnt. A commonly used estimate (about
# 0.03 s per kg-ish lap of fuel); the chart says it is an estimate.
FUEL_SECONDS_PER_LAP = 0.03
# A stint needs this many clean laps before a slope means anything.
MIN_LAPS_FOR_SLOPE = 4

SPEED_TRAPS = {
    "SpeedI1": "Intermediate 1",
    "SpeedI2": "Intermediate 2",
    "SpeedFL": "Finish line",
    "SpeedST": "Speed trap",
}


def _driver_column(laps: pd.DataFrame) -> str:
    return "DriverAcronym" if "DriverAcronym" in laps.columns else "Driver"


def _flag(laps: pd.DataFrame, column: str) -> pd.Series:
    """A boolean column that may be missing, object-typed or hold None."""
    if column not in laps.columns:
        return pd.Series(False, index=laps.index, dtype=bool)
    return laps[column].eq(True)


def race_trace(laps: pd.DataFrame | None, reference: str | None = None) -> pd.DataFrame:
    """``Driver, LapNumber, Gap``: seconds behind the leader (or ``reference``) per lap.

    The gap at lap *n* is the difference between the moments two cars
    completed lap *n*, so a lapped car's gap keeps growing past one lap time.
    With ``reference`` the gap is to that driver (negative = ahead of them);
    laps the reference has not completed have no gap.
    """
    columns = ["Driver", "LapNumber", "Gap"]
    if laps is None or laps.empty or not {"LapNumber", "Time"} <= set(laps.columns):
        return pd.DataFrame(columns=columns)
    driver_col = _driver_column(laps)
    frame = pd.DataFrame(
        {
            "Driver": laps[driver_col].astype(str),
            "LapNumber": pd.to_numeric(laps["LapNumber"], errors="coerce"),
            "End": seconds_series(laps["Time"]),
        }
    ).dropna(subset=["LapNumber", "End"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["LapNumber"] = frame["LapNumber"].astype(int)
    if reference is None:
        base = frame.groupby("LapNumber")["End"].min()
    else:
        base = frame[frame["Driver"] == reference].groupby("LapNumber")["End"].min()
    frame["Gap"] = frame["End"] - frame["LapNumber"].map(base)
    frame = frame.dropna(subset=["Gap"])
    return frame.sort_values(["Driver", "LapNumber"], kind="stable")[columns].reset_index(drop=True)


def stint_pace(
    laps: pd.DataFrame | None,
    track_status: pd.DataFrame | None = None,
    total_laps: int | None = None,
    fuel_seconds_per_lap: float = FUEL_SECONDS_PER_LAP,
) -> pd.DataFrame:
    """Clean racing laps with a fuel-corrected time, for tyre degradation.

    Left out: in- and out-laps, laps under SC/VSC/red flag, deleted laps,
    laps FastF1 marks inaccurate, and lap 1. ``Corrected`` removes the
    weight of the fuel still on board: ``fuel_seconds_per_lap`` for every lap
    left to run.
    """
    columns = [
        "Driver",
        "Stint",
        "Compound",
        "LapNumber",
        "TyreLife",
        "LapSeconds",
        "Corrected",
    ]
    if laps is None or laps.empty or not {"LapNumber", "LapTime"} <= set(laps.columns):
        return pd.DataFrame(columns=columns)
    driver_col = _driver_column(laps)
    lap_number = pd.to_numeric(laps["LapNumber"], errors="coerce")
    seconds = seconds_series(laps["LapTime"])
    keep = lap_number.notna() & seconds.notna() & (lap_number > 1)
    for column in ("PitInTime", "PitOutTime"):
        if column in laps.columns:
            keep &= laps[column].isna()
    keep &= ~_flag(laps, "IsPitOutLap")
    keep &= ~_flag(laps, "Deleted")
    if "IsAccurate" in laps.columns:
        keep &= laps["IsAccurate"].ne(False)
    neutral = lap_states(laps, track_status)
    if neutral:
        keep &= ~lap_number.isin(list(neutral))

    frame = pd.DataFrame(
        {
            "Driver": laps[driver_col].astype(str),
            "Stint": pd.to_numeric(laps.get("Stint"), errors="coerce") if "Stint" in laps else 1.0,
            "Compound": (
                laps["Compound"].astype(object).where(laps["Compound"].notna(), "UNKNOWN")
                if "Compound" in laps
                else "UNKNOWN"
            ),
            "LapNumber": lap_number,
            "TyreLife": (
                pd.to_numeric(laps["TyreLife"], errors="coerce") if "TyreLife" in laps else np.nan
            ),
            "LapSeconds": seconds,
        }
    )[keep]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    last = int(total_laps) if total_laps else int(frame["LapNumber"].max())
    frame["Corrected"] = frame["LapSeconds"] - fuel_seconds_per_lap * (last - frame["LapNumber"])
    frame["Compound"] = frame["Compound"].astype(str).str.upper()
    # Without TyreLife, age within the stint stands in for it.
    missing_age = frame["TyreLife"].isna()
    if missing_age.any():
        order = frame.groupby(["Driver", "Stint"], dropna=False)["LapNumber"].rank(method="first")
        frame.loc[missing_age, "TyreLife"] = order[missing_age]
    return frame.sort_values(["Driver", "LapNumber"], kind="stable")[columns].reset_index(drop=True)


def degradation(pace: pd.DataFrame, min_laps: int = MIN_LAPS_FOR_SLOPE) -> pd.DataFrame:
    """Per stint: ``Driver, Stint, Compound, Laps, FirstLap, LastLap, SecondsPerLap``.

    ``SecondsPerLap`` is the least-squares slope of the corrected lap time
    against tyre age - how much slower the car got per lap on that set.
    Stints shorter than ``min_laps`` clean laps are left out.
    """
    columns = ["Driver", "Stint", "Compound", "Laps", "FirstLap", "LastLap", "SecondsPerLap"]
    if pace is None or pace.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for (driver, stint), group in pace.groupby(["Driver", "Stint"], dropna=False, sort=True):
        if len(group) < min_laps or group["TyreLife"].nunique() < 2:
            continue
        slope = float(np.polyfit(group["TyreLife"].astype(float), group["Corrected"], 1)[0])
        rows.append(
            {
                "Driver": driver,
                "Stint": stint,
                "Compound": group["Compound"].mode().iloc[0],
                "Laps": len(group),
                "FirstLap": int(group["LapNumber"].min()),
                "LastLap": int(group["LapNumber"].max()),
                "SecondsPerLap": round(slope, 3),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def speed_trap_ranking(laps: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    """Per trap present in ``laps``: ``Driver, Speed, Lap`` ranked fastest first.

    Each driver's best reading over the session, from every lap (traps are
    not lap times, so deleted laps still count).
    """
    if laps is None or laps.empty:
        return {}
    driver_col = _driver_column(laps)
    ranking: dict[str, pd.DataFrame] = {}
    for column, name in SPEED_TRAPS.items():
        if column not in laps.columns:
            continue
        frame = pd.DataFrame(
            {
                "Driver": laps[driver_col].astype(str),
                "Speed": pd.to_numeric(laps[column], errors="coerce"),
                "Lap": (
                    pd.to_numeric(laps.get("LapNumber"), errors="coerce")
                    if "LapNumber" in laps
                    else np.nan
                ),
            }
        ).dropna(subset=["Speed"])
        if frame.empty:
            continue
        best = frame.loc[frame.groupby("Driver")["Speed"].idxmax()]
        ranking[name] = best.sort_values(
            ["Speed", "Driver"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)
    return ranking


def deleted_laps(laps: pd.DataFrame | None) -> pd.DataFrame:
    """``Driver, LapNumber, LapSeconds, Reason`` for every deleted lap, in lap order."""
    columns = ["Driver", "LapNumber", "LapSeconds", "Reason"]
    if laps is None or laps.empty or "Deleted" not in laps.columns:
        return pd.DataFrame(columns=columns)
    deleted = laps[_flag(laps, "Deleted")]
    if deleted.empty:
        return pd.DataFrame(columns=columns)
    reason = (
        deleted["DeletedReason"].astype(object).where(deleted["DeletedReason"].notna(), "")
        if "DeletedReason" in deleted.columns
        else ""
    )
    frame = pd.DataFrame(
        {
            "Driver": deleted[_driver_column(laps)].astype(str),
            "LapNumber": pd.to_numeric(deleted.get("LapNumber"), errors="coerce"),
            "LapSeconds": (
                seconds_series(deleted["LapTime"]) if "LapTime" in deleted.columns else np.nan
            ),
            "Reason": reason,
        }
    )
    return frame.sort_values(["LapNumber", "Driver"], kind="stable")[columns].reset_index(drop=True)


def default_drivers(
    laps: pd.DataFrame | None, results: pd.DataFrame | None = None, count: int = 5
) -> list[str]:
    """The drivers a chart starts with: the top ``count`` of the session.

    The official classification when there is one, else the order of each
    driver's best lap time.
    """
    if results is not None and not results.empty and "Abbreviation" in results.columns:
        ordered = results
        if "Position" in results.columns:
            ordered = results.assign(
                _pos=pd.to_numeric(results["Position"], errors="coerce")
            ).sort_values("_pos", kind="stable", na_position="last")
        codes = [str(c) for c in ordered["Abbreviation"].dropna().tolist()]
        if codes:
            return codes[:count]
    if laps is None or laps.empty or "LapTime" not in laps.columns:
        return []
    driver_col = _driver_column(laps)
    best = (
        pd.DataFrame({"Driver": laps[driver_col].astype(str), "s": seconds_series(laps["LapTime"])})
        .dropna()
        .groupby("Driver")["s"]
        .min()
        .sort_values(kind="stable")
    )
    return best.index.tolist()[:count]
