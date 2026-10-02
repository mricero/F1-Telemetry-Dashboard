"""Neutralised periods (SC, VSC, red flag) on the session clock and per lap.

Pure transforms over the unified session dict's ``track_status`` frame
(``Time`` in session seconds, ``Status`` code) and its ``laps`` frame. Used to
shade the lap-time, position and race-trace charts (UX-06, FEAT-01) and to
drop neutralised laps from the tyre-degradation fit (FEAT-03).

Status codes follow the official feed: 1 green, 2 yellow, 4 safety car,
5 red flag, 6 VSC deployed, 7 VSC ending.
"""

from __future__ import annotations

import pandas as pd

from processing.time_utils import seconds_series

# Status code -> the state a chart shades. "7" (VSC ending) is still a VSC.
NEUTRAL_CODES = {"4": "SC", "5": "RED", "6": "VSC", "7": "VSC"}
# Precedence when one lap saw more than one state.
_RANK = {"RED": 3, "SC": 2, "VSC": 1}


def neutral_periods(track_status: pd.DataFrame | None, end: float | None = None) -> list[dict]:
    """``[{"state", "start", "end"}]`` in session seconds, in time order.

    A period runs from the change into SC/VSC/RED until the next change to a
    different state; one still open at the end of the frame closes at ``end``
    (or at its own start when ``end`` is unknown).
    """
    if track_status is None or track_status.empty:
        return []
    if not {"Time", "Status"} <= set(track_status.columns):
        return []
    frame = pd.DataFrame(
        {
            "Time": seconds_series(track_status["Time"]),
            "Status": track_status["Status"].astype(str).str.strip(),
        }
    ).dropna(subset=["Time"])
    frame = frame.sort_values("Time", kind="stable")

    periods: list[dict] = []
    current: dict | None = None
    for moment, code in zip(frame["Time"], frame["Status"], strict=True):
        state = NEUTRAL_CODES.get(code)
        if current is not None and state != current["state"]:
            current["end"] = float(moment)
            periods.append(current)
            current = None
        if state is not None and current is None:
            current = {"state": state, "start": float(moment), "end": None}
    if current is not None:
        current["end"] = float(end) if end is not None else current["start"]
        periods.append(current)
    return periods


def lap_windows(laps: pd.DataFrame | None) -> pd.DataFrame:
    """Per lap number: when the field started it and when the leader finished it.

    ``Start`` is the earliest ``LapStartTime``, ``End`` the earliest ``Time``
    (lap completion) across drivers, in session seconds.
    """
    columns = ["LapNumber", "Start", "End"]
    if laps is None or laps.empty or "LapNumber" not in laps.columns:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame({"LapNumber": pd.to_numeric(laps["LapNumber"], errors="coerce")})
    frame["Start"] = seconds_series(laps["LapStartTime"]) if "LapStartTime" in laps else None
    frame["End"] = seconds_series(laps["Time"]) if "Time" in laps else None
    frame = frame.dropna(subset=["LapNumber"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    grouped = frame.groupby("LapNumber").agg(Start=("Start", "min"), End=("End", "min"))
    return grouped.reset_index()[columns]


def lap_states_from_column(laps: pd.DataFrame | None) -> dict[int, str]:
    """``{lap: state}`` from FastF1's per-lap ``TrackStatus`` string ("41", "6"...).

    Any driver's lap carrying a neutral code marks that lap for the race.
    """
    if laps is None or laps.empty or not {"TrackStatus", "LapNumber"} <= set(laps.columns):
        return {}
    states: dict[int, str] = {}
    for lap, codes in zip(laps["LapNumber"], laps["TrackStatus"], strict=False):
        number = pd.to_numeric(lap, errors="coerce")
        if pd.isna(number) or codes is None or (isinstance(codes, float) and pd.isna(codes)):
            continue
        for code in str(codes):
            state = NEUTRAL_CODES.get(code)
            if state and _RANK[state] > _RANK.get(states.get(int(number), ""), 0):
                states[int(number)] = state
    return states


def lap_states(laps: pd.DataFrame | None, track_status: pd.DataFrame | None) -> dict[int, str]:
    """``{lap: "SC" | "VSC" | "RED"}`` for every neutralised lap.

    The laps' own ``TrackStatus`` column is used when present; otherwise the
    session's track-status changes are laid over each lap's window.
    """
    from_column = lap_states_from_column(laps)
    if from_column:
        return from_column
    windows = lap_windows(laps)
    if windows.empty:
        return {}
    end = pd.to_numeric(windows["End"], errors="coerce").max()
    periods = neutral_periods(track_status, end=None if pd.isna(end) else float(end))
    states: dict[int, str] = {}
    for lap, start, finish in zip(
        windows["LapNumber"], windows["Start"], windows["End"], strict=True
    ):
        if pd.isna(finish):
            continue
        begin = finish if pd.isna(start) else start
        for period in periods:
            if period["start"] <= finish and period["end"] >= begin:
                state = period["state"]
                if _RANK[state] > _RANK.get(states.get(int(lap), ""), 0):
                    states[int(lap)] = state
    return states


def lap_spans(states: dict[int, str]) -> list[dict]:
    """Consecutive laps in one state -> ``[{"state", "first", "last"}]``."""
    spans: list[dict] = []
    for lap in sorted(states):
        state = states[lap]
        if spans and spans[-1]["state"] == state and spans[-1]["last"] == lap - 1:
            spans[-1]["last"] = lap
        else:
            spans.append({"state": state, "first": lap, "last": lap})
    return spans
