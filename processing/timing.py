"""Timing-tower data model.

Turns a loaded session into the row structure the live-timing leaderboard
renders (see ``layout.md`` section 3). Everything here is pure data: no
Streamlit, no HTML, so the classification and delta logic stays testable.

Ordering follows a qualifying-style classification - fastest personal best
first - because the spec's Gap and Interval columns are lap-time deltas.
That ranking is meaningful for races and practice too (who was quickest).
"""

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from processing.time_utils import to_seconds

# Each of the three sectors is split into this many micro-sectors for the
# heat strip under the sector time (spec section 3.8).
SEGMENTS_PER_SECTOR = 5
SECTORS = 3
TOTAL_SEGMENTS = SEGMENTS_PER_SECTOR * SECTORS

# A driver within this fraction of the best micro-sector time shows green
# rather than yellow. Purple is reserved for the outright fastest.
GREEN_TOLERANCE = 0.02


def format_lap(seconds: Optional[float]) -> str:
    """``93.456`` -> ``'1:33.456'``; missing values render as an em dash."""
    if seconds is None:
        return "—"
    try:
        if pd.isna(seconds):
            return "—"
    except (TypeError, ValueError):
        return "—"
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:06.3f}" if minutes else f"{rest:.3f}"


def format_delta(seconds: Optional[float]) -> str:
    """Signed gap string, e.g. ``'+0.102'``. Missing deltas render ``'----'``."""
    if seconds is None or pd.isna(seconds):
        return "----"
    return f"+{seconds:.3f}" if seconds >= 0 else f"{seconds:.3f}"


def _driver_meta(drivers_df: pd.DataFrame) -> Dict[str, dict]:
    """Acronym -> {team_name, team_colour, full_name, driver_number}."""
    meta: Dict[str, dict] = {}
    if drivers_df is None or drivers_df.empty:
        return meta
    for _, row in drivers_df.iterrows():
        code = row.get("name_acronym")
        if code is None or pd.isna(code):
            continue
        meta[str(code)] = {
            "team_name": row.get("team_name") or "",
            "team_colour": row.get("team_colour") or "",
            "full_name": row.get("full_name") or str(code),
            "driver_number": row.get("driver_number"),
        }
    return meta


def _tyre_history(laps: pd.DataFrame) -> List[dict]:
    """Stint list for one driver: compound plus laps run on it."""
    if laps.empty or "Compound" not in laps.columns:
        return []
    work = laps.copy()
    if "Stint" not in work.columns:
        work["Stint"] = 1
    history = []
    for _, stint in work.dropna(subset=["Compound"]).groupby("Stint", sort=True):
        compound = str(stint["Compound"].iloc[0]).upper()
        history.append({"compound": compound, "laps_used": int(len(stint))})
    return history


def _speed_trap(laps: pd.DataFrame) -> Optional[float]:
    """Best speed-trap reading across the driver's laps (km/h)."""
    for col in ("SpeedST", "SpeedFL", "SpeedI2", "SpeedI1"):
        if col in laps.columns:
            values = pd.to_numeric(laps[col], errors="coerce").dropna()
            if not values.empty:
                return float(values.max())
    return None


def _status(laps: pd.DataFrame, is_live: bool) -> str:
    """Driver state badge: IN PIT / ON TRACK / CLASSIFIED / OUT.

    IN PIT and ON TRACK only mean something while cars are circulating; a
    completed session is uniformly CLASSIFIED, regardless of where a driver
    happened to stop.
    """
    if laps.empty:
        return "OUT"
    if not is_live:
        return "CLASSIFIED"
    last = laps.iloc[-1]
    in_pit = last.get("PitInTime")
    out_pit = last.get("PitOutTime")
    if in_pit is not None and pd.notna(in_pit) and (out_pit is None or pd.isna(out_pit)):
        return "IN PIT"
    return "ON TRACK"


def micro_sector_times(
    telemetry: pd.DataFrame, segments: int = TOTAL_SEGMENTS
) -> Optional[np.ndarray]:
    """Time (s) spent in each equal-length slice of one lap.

    Distance is normalised across the trace so drivers whose fastest laps
    differ slightly in measured length still line up slice for slice.
    """
    if telemetry is None or telemetry.empty:
        return None
    if not {"Distance", "Time"}.issubset(telemetry.columns):
        return None

    distance = pd.to_numeric(telemetry["Distance"], errors="coerce")
    elapsed = telemetry["Time"]
    if pd.api.types.is_timedelta64_dtype(elapsed):
        elapsed = elapsed.dt.total_seconds()
    else:
        elapsed = pd.Series([to_seconds(v) for v in elapsed], index=telemetry.index)
    elapsed = pd.to_numeric(elapsed, errors="coerce")

    ok = distance.notna() & elapsed.notna()
    if int(ok.sum()) < segments * 2:
        return None
    distance, elapsed = distance[ok].to_numpy(float), elapsed[ok].to_numpy(float)
    order = np.argsort(distance, kind="stable")
    distance, elapsed = distance[order], elapsed[order]

    if distance[-1] - distance[0] <= 0:
        return None
    marks = np.interp(np.linspace(distance[0], distance[-1], segments + 1), distance, elapsed)
    times = np.diff(marks)
    return times if np.all(np.isfinite(times)) and np.all(times >= 0) else None


def segment_states(
    per_driver_times: Dict[str, np.ndarray], tolerance: float = GREEN_TOLERANCE
) -> Dict[str, List[str]]:
    """Colour each driver's micro-sectors against the session best.

    PURPLE for the outright fastest through a slice, GREEN within
    ``tolerance`` of it, YELLOW otherwise - the timing-screen encoding from
    ``layout.md`` section 3.8.
    """
    if not per_driver_times:
        return {}
    stacked = np.vstack(list(per_driver_times.values()))
    best = np.nanmin(stacked, axis=0)

    states: Dict[str, List[str]] = {}
    for driver, times in per_driver_times.items():
        row = []
        for value, fastest in zip(times, best):
            if not np.isfinite(value) or not np.isfinite(fastest) or fastest <= 0:
                row.append("NONE")
            elif np.isclose(value, fastest):
                row.append("PURPLE")
            elif value <= fastest * (1.0 + tolerance):
                row.append("GREEN")
            else:
                row.append("YELLOW")
        states[driver] = row
    return states


def sector_leaders(rows: Sequence[dict], top_n: int = 3) -> List[List[dict]]:
    """Top ``top_n`` drivers per sector (spec section 5)."""
    leaders = []
    for index in range(SECTORS):
        ranked = [r for r in rows if r["sectors"][index]["seconds"] is not None]
        ranked.sort(key=lambda r: r["sectors"][index]["seconds"])
        leaders.append(
            [
                {
                    "rank": position,
                    "code": row["code"],
                    "team_colour": row["team_colour"],
                    "team_name": row["team_name"],
                    "time": f"{row['sectors'][index]['seconds']:.3f}",
                }
                for position, row in enumerate(ranked[:top_n], start=1)
            ]
        )
    return leaders


def theoretical_best(rows: Sequence[dict]) -> Optional[float]:
    """Sum of the fastest sector times set by anyone in the session."""
    total = 0.0
    for index in range(SECTORS):
        times = [
            r["sectors"][index]["seconds"]
            for r in rows
            if r["sectors"][index]["seconds"] is not None
        ]
        if not times:
            return None
        total += min(times)
    return round(total, 3)


def build_timing_rows(session_data: dict, cutoff: int = 10) -> List[dict]:
    """Build the leaderboard rows for one session.

    ``cutoff`` marks where the "knocked out" partition begins (spec section
    3): in a qualifying segment the top 10 advance. Rows below it render
    dimmed with a KO badge.
    """
    laps_df = session_data.get("laps")
    if laps_df is None or laps_df.empty or "Driver" not in laps_df.columns:
        return []

    is_live = bool(session_data.get("is_live"))
    meta = _driver_meta(session_data.get("drivers"))
    telemetry = session_data.get("telemetry") or {}

    # Micro-sector heat strips come from each driver's own telemetry trace.
    per_driver_segments = {}
    for code, frame in telemetry.items():
        times = micro_sector_times(frame)
        if times is not None:
            per_driver_segments[code] = times
    states = segment_states(per_driver_segments)

    rows = []
    for code, driver_laps in laps_df.groupby("Driver", sort=False):
        driver_laps = driver_laps.sort_values("LapNumber")
        lap_seconds = driver_laps["LapTime"].map(to_seconds)
        valid = lap_seconds.dropna()

        best_seconds = float(valid.min()) if not valid.empty else None
        last_value = lap_seconds.iloc[-1]
        last_seconds = float(last_value) if pd.notna(last_value) else None

        # Sector times come from the driver's own quickest lap.
        best_lap_row = (
            driver_laps.loc[lap_seconds.idxmin()]
            if best_seconds is not None
            else driver_laps.iloc[-1]
        )
        default_states = ["NONE"] * TOTAL_SEGMENTS
        sectors = []
        for index in range(1, SECTORS + 1):
            column = f"Sector{index}Time"
            seconds = (
                to_seconds(best_lap_row.get(column)) if column in driver_laps.columns else None
            )
            start = (index - 1) * SEGMENTS_PER_SECTOR
            sectors.append(
                {
                    "seconds": seconds,
                    "display": f"{seconds:.3f}" if seconds is not None else "—",
                    "segments": states.get(str(code), default_states)[
                        start : start + SEGMENTS_PER_SECTOR
                    ],
                }
            )

        info = meta.get(str(code), {})
        rows.append(
            {
                "code": str(code),
                "full_name": info.get("full_name", str(code)),
                "team_name": info.get("team_name", ""),
                "team_colour": info.get("team_colour", ""),
                "status": _status(driver_laps, is_live),
                "best_seconds": best_seconds,
                "best_lap": format_lap(best_seconds),
                "last_lap": format_lap(last_seconds),
                "sectors": sectors,
                "tyre_history": _tyre_history(driver_laps),
                "speed_kmh": _speed_trap(driver_laps),
                "laps_completed": int(len(driver_laps)),
            }
        )

    # Classify: drivers with a time first (fastest to slowest), then the rest.
    timed = [r for r in rows if r["best_seconds"] is not None]
    untimed = [r for r in rows if r["best_seconds"] is None]
    timed.sort(key=lambda r: r["best_seconds"])
    ordered = timed + untimed

    leader = timed[0]["best_seconds"] if timed else None
    previous = None
    for position, row in enumerate(ordered, start=1):
        row["position"] = position
        row["is_overall_best"] = bool(timed) and row is timed[0]
        if row["best_seconds"] is None or leader is None:
            row["gap"] = "----"
            row["interval"] = "----"
        else:
            row["gap"] = "----" if position == 1 else format_delta(row["best_seconds"] - leader)
            row["interval"] = (
                "----" if previous is None else format_delta(row["best_seconds"] - previous)
            )
            previous = row["best_seconds"]
        row["knocked_out"] = position > cutoff

    best_possible = theoretical_best(ordered)
    for row in ordered:
        if row["best_seconds"] is not None and best_possible is not None:
            row["diff"] = format_delta(row["best_seconds"] - best_possible)
        else:
            row["diff"] = "----"
    return ordered
