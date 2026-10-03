"""Speed-trap and sector rankings (FEAT-09).

Pure transforms over the unified session dict's ``laps`` table. Rankings use :func:`processing.timing._valid_laps`, so a lap
the stewards deleted (or FastF1 marks inaccurate) never sets a trap speed or a
sector best - the same rule the tower and the records follow.
"""

import numpy as np
import pandas as pd

from processing.time_utils import seconds_series
from processing.timing import SECTORS, _valid_laps

# FastF1 lap column -> the label the panel shows. Order is the panel order.
SPEED_TRAPS: tuple[tuple[str, str], ...] = (
    ("SpeedI1", "Intermediate 1"),
    ("SpeedI2", "Intermediate 2"),
    ("SpeedFL", "Finish line"),
    ("SpeedST", "Speed trap"),
)

RANKING_COLUMNS = ["Rank", "Driver", "Value", "Lap", "Gap"]


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({column: [] for column in columns})


def _ranked(best: pd.DataFrame, ascending: bool) -> pd.DataFrame:
    """Sort a ``Driver/Value/Lap`` frame, then add Rank and the gap to the best."""
    ordered = best.sort_values(["Value", "Driver"], ascending=[ascending, True], kind="stable")
    ordered = ordered.reset_index(drop=True)
    leader = float(ordered["Value"].iloc[0])
    ordered["Gap"] = (ordered["Value"] - leader).abs().round(3)
    ordered.insert(0, "Rank", np.arange(1, len(ordered) + 1))
    return ordered[RANKING_COLUMNS]


def _best_per_driver(laps: pd.DataFrame, values: pd.Series, ascending: bool) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "Driver": laps["Driver"].astype(str).to_numpy(),
            "Value": values.to_numpy(float),
            "Lap": (
                pd.to_numeric(laps["LapNumber"], errors="coerce").to_numpy(float)
                if "LapNumber" in laps.columns
                else np.full(len(laps), np.nan)
            ),
        }
    ).dropna(subset=["Value"])
    if frame.empty:
        return frame
    grouped = frame.groupby("Driver")["Value"]
    pick = grouped.idxmin() if ascending else grouped.idxmax()
    return frame.loc[pick.to_numpy()]


def speed_ranking(laps: pd.DataFrame | None, column: str) -> pd.DataFrame:
    """Each driver's highest reading at one trap (km/h), fastest first.

    Columns: ``Rank, Driver, Value, Lap, Gap`` (``Gap`` is km/h below the best).
    Empty when the column is missing or holds no reading.
    """
    if laps is None or laps.empty or column not in laps.columns or "Driver" not in laps.columns:
        return _empty(RANKING_COLUMNS)
    valid = _valid_laps(laps)
    values = pd.to_numeric(valid[column], errors="coerce")
    best = _best_per_driver(valid, values, ascending=False)
    return _ranked(best, ascending=False) if not best.empty else _empty(RANKING_COLUMNS)


def sector_ranking(laps: pd.DataFrame | None, sector: int) -> pd.DataFrame:
    """Each driver's quickest valid time through sector ``sector`` (1-3), in seconds.

    Columns: ``Rank, Driver, Value, Lap, Gap`` (``Gap`` is seconds behind the best).
    """
    column = f"Sector{sector}Time"
    if (
        sector not in range(1, SECTORS + 1)
        or laps is None
        or laps.empty
        or column not in laps.columns
        or "Driver" not in laps.columns
    ):
        return _empty(RANKING_COLUMNS)
    valid = _valid_laps(laps).reset_index(drop=True)
    values = seconds_series(valid[column])
    best = _best_per_driver(valid, values, ascending=True)
    return _ranked(best, ascending=True) if not best.empty else _empty(RANKING_COLUMNS)
