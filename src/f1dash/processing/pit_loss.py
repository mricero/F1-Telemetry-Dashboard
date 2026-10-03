"""Pit rejoin predictor (FEAT-02).

Where would a car come out if it pitted now? Its gap to the leader grows by the
time a stop costs, and the cars it lands behind are those whose gap is smaller
than that. Pure transforms over the laps frame; no Streamlit, no network.

The cost of a stop is the pit lane time, ``PitInTime`` on the in-lap to
``PitOutTime`` on the next lap. It is measured from the session's own green-flag
stops when there are enough of them; otherwise a per-circuit seed table, then a
default, stands in.
"""

import numpy as np
import pandas as pd

from f1dash.processing.time_utils import seconds_series
from f1dash.processing.timing import gap_trace
from f1dash.processing.track_periods import lap_states

DEFAULT_PIT_LOSS = 21.0

# Typical green-flag pit lane time in seconds (pit entry on the in-lap to pit
# exit on the out-lap, stationary time included) by a word of the event name.
# Rounded figures of the order FastF1's ``PitInTime -> PitOutTime`` medians
# give; a session with enough stops of its own replaces the seed
# (``pit_loss_for``).
PIT_LOSS_SEEDS = {
    "bahrain": 22.5,
    "saudi": 20.5,
    "australia": 21.5,
    "japan": 22.5,
    "china": 23.0,
    "miami": 21.0,
    "emilia": 27.0,
    "monaco": 20.0,
    "canada": 18.5,
    "spain": 22.0,
    "barcelona": 22.0,
    "austria": 20.0,
    "british": 28.0,
    "belgian": 18.5,
    "hungar": 20.5,
    "dutch": 22.0,
    "italian": 24.0,
    "azerbaijan": 19.5,
    "singapore": 28.0,
    "united states": 21.5,
    "mexic": 22.0,
    "sao paulo": 21.0,
    "brazil": 21.0,
    "las vegas": 20.0,
    "qatar": 25.5,
    "abu dhabi": 21.5,
}

# A "stop" outside this range is a drive-through, a repair or a red-flag wait,
# not what a normal stop costs.
MIN_STOP_S = 12.0
MAX_STOP_S = 45.0
MIN_STOPS_FOR_SESSION_MEDIAN = 3

STOP_COLUMNS = ["Driver", "LapNumber", "Duration"]


def pit_stop_durations(
    laps: pd.DataFrame | None, track_status: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Pit lane time of every green-flag stop: ``Driver, LapNumber`` (the in-lap), ``Duration``."""
    empty = pd.DataFrame(columns=STOP_COLUMNS)
    needed = {"LapNumber", "PitInTime", "PitOutTime"}
    if laps is None or laps.empty or not needed <= set(laps.columns):
        return empty
    driver = "DriverAcronym" if "DriverAcronym" in laps.columns else "Driver"
    if driver not in laps.columns:
        return empty
    frame = pd.DataFrame(
        {
            "Driver": laps[driver].astype(str),
            "LapNumber": pd.to_numeric(laps["LapNumber"], errors="coerce"),
            "In": seconds_series(laps["PitInTime"]),
            "Out": seconds_series(laps["PitOutTime"]),
        }
    ).dropna(subset=["LapNumber"])
    out = frame.dropna(subset=["Out"]).copy()
    out["LapNumber"] = out["LapNumber"] - 1
    joined = (
        frame.dropna(subset=["In"])
        .drop(columns="Out")
        .merge(out[["Driver", "LapNumber", "Out"]], on=["Driver", "LapNumber"], how="inner")
    )
    if joined.empty:
        return empty
    joined["Duration"] = (joined["Out"] - joined["In"]).round(3)
    neutral = lap_states(laps, track_status)
    in_lap = joined["LapNumber"].astype(int)
    green = ~(in_lap.isin(neutral) | (in_lap + 1).isin(neutral))
    joined = joined[green & joined["Duration"].between(MIN_STOP_S, MAX_STOP_S)].copy()
    if joined.empty:
        return empty
    joined["LapNumber"] = joined["LapNumber"].astype(int)
    return joined[STOP_COLUMNS].sort_values(["LapNumber", "Driver"]).reset_index(drop=True)


def seeded_pit_loss(event_name: str | None) -> float | None:
    """The seed-table value for an event name ("Bahrain Grand Prix"), or ``None``."""
    name = str(event_name or "").lower()
    for word, seconds in PIT_LOSS_SEEDS.items():
        if word in name:
            return seconds
    return None


def pit_loss_for(
    event_name: str | None,
    laps: pd.DataFrame | None = None,
    track_status: pd.DataFrame | None = None,
) -> tuple[float, str]:
    """``(seconds, source)``: the session's own median, else the circuit seed, else the default."""
    stops = pit_stop_durations(laps, track_status)
    if len(stops) >= MIN_STOPS_FOR_SESSION_MEDIAN:
        return round(float(stops["Duration"].median()), 1), f"median of {len(stops)} stops"
    seeded = seeded_pit_loss(event_name)
    if seeded is not None:
        return seeded, "typical for this circuit"
    return DEFAULT_PIT_LOSS, "default"


def predict_rejoin(gaps: dict[str, float], driver: str, loss: float) -> dict | None:
    """Where ``driver`` comes out after a stop costing ``loss`` seconds.

    ``gaps`` maps each running car to its gap to the leader (seconds). Returns
    ``position`` (1-based), the car ``ahead`` and ``behind`` with the time to
    each, or ``None`` when ``driver`` is not in ``gaps``. The car ahead or
    behind is ``None`` at the ends of the order.
    """
    if driver not in gaps:
        return None
    rejoin = float(gaps[driver]) + float(loss)
    others = {code: float(gap) for code, gap in gaps.items() if code != driver}
    ahead = {code: gap for code, gap in others.items() if gap <= rejoin}
    behind = {code: gap for code, gap in others.items() if gap > rejoin}
    nearest_ahead = max(ahead, key=lambda code: ahead[code]) if ahead else None
    nearest_behind = min(behind, key=lambda code: behind[code]) if behind else None
    return {
        "position": len(ahead) + 1,
        "ahead": nearest_ahead,
        "gap_ahead": None if nearest_ahead is None else round(rejoin - ahead[nearest_ahead], 3),
        "behind": nearest_behind,
        "gap_behind": None if nearest_behind is None else round(behind[nearest_behind] - rejoin, 3),
        "loss": float(loss),
    }


def rejoin_after_lap(laps: pd.DataFrame | None, driver: str, lap: int, loss: float) -> dict | None:
    """:func:`predict_rejoin` from the gaps at the timing line at the end of ``lap``.

    Cars with no time on that lap (retired before it) are not in the order.
    """
    trace = gap_trace(laps)
    if trace.empty:
        return None
    rows = trace[trace["LapNumber"] == lap]
    gaps = {str(code): float(gap) for code, gap in zip(rows["Driver"], rows["Gap"], strict=True)}
    gaps = {code: gap for code, gap in gaps.items() if not np.isnan(gap)}
    return predict_rejoin(gaps, str(driver), loss)
