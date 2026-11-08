"""Pit rejoin predictor (FEAT-02) and the two pit-stop times it must not confuse.

``pit lane duration``
    Pit entry to pit exit, stationary time included: ``PitInTime`` on the
    in-lap to ``PitOutTime`` on the next lap (FastF1), or the live feed's
    ``PitLaneTimeCollection``. It is **observed**.
``net pit loss``
    How much more time the stop costs than staying out: the pit lane duration
    minus the time the car would have taken over the same stretch of track.
    Neither FastF1 nor the live feed reports it, and this module does not
    derive it, so it is an **assumption** the viewer sets.

Only the net pit loss is added to a car's gap: where would a car come out if it
pitted now? Its gap to the leader grows by the net loss, and the cars it lands
behind are those whose gap is smaller than that. Pure transforms over the laps
frame; no Streamlit, no network.
"""

import numpy as np
import pandas as pd

from f1dash.processing.time_utils import seconds_series
from f1dash.processing.timing import gap_trace
from f1dash.processing.track_periods import lap_states

# The net pit loss the panels start from: a generic, explicitly labelled
# assumption, not a measurement and not specific to any circuit. The viewer is
# expected to set the figure for the circuit (NET_PIT_LOSS_NOTE).
DEFAULT_NET_PIT_LOSS = 20.0
NET_PIT_LOSS_NOTE = (
    "Net pit loss is how much longer a stop takes than staying out: the pit lane "
    "time minus the time the same stretch takes on track. Neither the timing feed "
    "nor FastF1 reports it and it is not measured here, so it is an assumption: "
    f"{DEFAULT_NET_PIT_LOSS:.0f} s is a generic starting point, not a figure for "
    "this circuit."
)
# The pit lane median needs this many green-flag stops before it is shown.
MIN_STOPS_FOR_MEDIAN = 3

# A "stop" outside this range is a drive-through, a repair or a red-flag wait,
# not what a normal stop costs.
MIN_STOP_S = 12.0
MAX_STOP_S = 45.0
MIN_STOPS_FOR_SESSION_MEDIAN = 3

STOP_COLUMNS = ["Driver", "LapNumber", "Duration"]


def pit_lane_durations(
    laps: pd.DataFrame | None, track_status: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Observed pit lane duration of every green-flag stop (not net loss).

    ``Driver, LapNumber`` (the in-lap), ``Duration`` in seconds.
    """
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


def median_pit_lane_duration(
    laps: pd.DataFrame | None, track_status: pd.DataFrame | None = None
) -> tuple[float | None, int]:
    """``(median seconds, stops)`` of the green-flag pit lane durations.

    The median is ``None`` below three stops. It is observed pit lane time,
    shown for reference; it is **not** a net pit loss and is never used as one.
    """
    stops = pit_lane_durations(laps, track_status)
    if len(stops) < MIN_STOPS_FOR_MEDIAN:
        return None, len(stops)
    return round(float(stops["Duration"].median()), 1), len(stops)


def predict_rejoin(gaps: dict[str, float], driver: str, net_pit_loss: float) -> dict | None:
    """Where ``driver`` comes out after a stop with ``net_pit_loss`` seconds of net loss.

    ``gaps`` maps each running car to its gap to the leader (seconds). Returns
    ``position`` (1-based), the car ``ahead`` and ``behind`` with the time to
    each, or ``None`` when ``driver`` is not in ``gaps``. The car ahead or
    behind is ``None`` at the ends of the order.
    """
    if driver not in gaps:
        return None
    rejoin = float(gaps[driver]) + float(net_pit_loss)
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
        "net_pit_loss": float(net_pit_loss),
    }


def rejoin_after_lap(
    laps: pd.DataFrame | None, driver: str, lap: int, net_pit_loss: float
) -> dict | None:
    """:func:`predict_rejoin` from the gaps at the timing line at the end of ``lap``.

    Cars with no time on that lap (retired before it) are not in the order.
    """
    trace = gap_trace(laps)
    if trace.empty:
        return None
    rows = trace[trace["LapNumber"] == lap]
    gaps = {str(code): float(gap) for code, gap in zip(rows["Driver"], rows["Gap"], strict=True)}
    gaps = {code: gap for code, gap in gaps.items() if not np.isnan(gap)}
    return predict_rejoin(gaps, str(driver), net_pit_loss)
