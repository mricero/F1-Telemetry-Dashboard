"""Timing-tower data model.

Turns a loaded session into the row structure the live-timing leaderboard
renders (see ``layout.md`` section 3). Everything here is pure data: no
Streamlit, no HTML, so the classification and delta logic stays testable.

Ordering depends on the session. A race or sprint is classified by finishing
position, with Gap/Interval as race time behind the leader and the car ahead
(or ``+N LAP`` for lapped cars). Practice and qualifying rank by personal best
lap, where Gap and Interval are lap-time deltas.
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

# Sessions classified by finishing position rather than by best lap. Both the
# short codes the selector uses and FastF1's session names are accepted.
RACE_SESSION_TYPES = {"r", "s", "race", "sprint"}

# Sessions run as knock-out segments. "Sprint Shootout" is the 2023 name.
QUALIFYING_SESSION_TYPES = {"q", "sq", "qualifying", "sprint qualifying", "sprint shootout"}

# Drivers who reach the final segment. Everyone else is eliminated in equal
# halves across Q1 and Q2 (2026: 22 cars -> 6 and 6; 2018-25: 20 -> 5 and 5).
Q3_PLACES = 10

# Segment identifiers, fastest-progressing first, with their split headings.
QUALIFYING_SEGMENTS = ("Q3", "Q2", "Q1")
SEGMENT_HEADINGS = {"Q3": "Q3", "Q2": "Eliminated in Q2", "Q1": "Eliminated in Q1"}


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


def format_lap_gap(laps_down: int) -> str:
    """``1`` -> ``'+1 LAP'``; ``3`` -> ``'+3 LAPS'`` (race convention)."""
    return f"+{laps_down} LAP" if laps_down == 1 else f"+{laps_down} LAPS"


def is_race_session(session_info: Optional[dict]) -> bool:
    """True for races and sprints, which are classified by finishing order."""
    info = session_info or {}
    for key in ("session_type", "session_name"):
        value = info.get(key)
        if value and str(value).strip().lower() in RACE_SESSION_TYPES:
            return True
    return False


def is_qualifying_session(session_info: Optional[dict]) -> bool:
    """True for qualifying and sprint qualifying, which run in segments."""
    info = session_info or {}
    for key in ("session_type", "session_name"):
        value = info.get(key)
        if value and str(value).strip().lower() in QUALIFYING_SESSION_TYPES:
            return True
    return False


def qualifying_cutoffs(car_count: int) -> List[int]:
    """How many cars survive each segment, e.g. 22 cars -> ``[16, 10]``.

    The regulations eliminate the same number after Q1 and Q2, so the count
    follows the entry list rather than a hardcoded top ten.
    """
    eliminated = max((car_count - Q3_PLACES + 1) // 2, 0)
    return [max(car_count - eliminated, Q3_PLACES), Q3_PLACES]


def _results_index(results: Optional[pd.DataFrame]) -> Dict[str, dict]:
    """Abbreviation -> official classification fields, or {} when absent."""
    if results is None or not isinstance(results, pd.DataFrame) or results.empty:
        return {}
    if "Abbreviation" not in results.columns:
        return {}
    index: Dict[str, dict] = {}
    for _, row in results.iterrows():
        code = row.get("Abbreviation")
        if code is None or pd.isna(code):
            continue
        index[str(code)] = row.to_dict()
    return index


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


def _last_position(laps: pd.DataFrame) -> Optional[float]:
    """On-road position at the driver's last lap, when the laps carry it."""
    if laps.empty or "Position" not in laps.columns:
        return None
    positions = pd.to_numeric(laps["Position"], errors="coerce").dropna()
    return float(positions.iloc[-1]) if not positions.empty else None


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


def _classify_by_best_lap(rows: List[dict]) -> List[dict]:
    """Practice / qualifying order: quickest personal best first.

    Gap is the lap-time delta to the session best and Interval the delta to
    the car ahead on the timing screen.
    """
    timed = sorted(
        (r for r in rows if r["best_seconds"] is not None), key=lambda r: r["best_seconds"]
    )
    untimed = [r for r in rows if r["best_seconds"] is None]
    ordered = timed + untimed

    leader = timed[0]["best_seconds"] if timed else None
    previous = None
    for row in ordered:
        if row["best_seconds"] is None or leader is None:
            row["gap"] = "----"
            row["interval"] = "----"
            continue
        row["gap"] = "----" if row is timed[0] else format_delta(row["best_seconds"] - leader)
        row["interval"] = (
            "----" if previous is None else format_delta(row["best_seconds"] - previous)
        )
        previous = row["best_seconds"]
    return ordered


def _classify_qualifying(rows: List[dict], results: Dict[str, dict]) -> List[dict]:
    """Qualifying order: segment reached first, then time within it.

    A driver's row shows the time from the segment they went out in, not
    their session best, and the tower is split at the real elimination
    boundaries rather than at a fixed top ten.
    """
    has_segment_times = any(
        any(seg in entry and pd.notna(entry[seg]) for seg in QUALIFYING_SEGMENTS)
        for entry in results.values()
    )

    if not has_segment_times:
        # No per-segment times (live, or a source without results): fall back
        # to best-lap order and the regulation cut-offs for the entry list.
        ordered = _classify_by_best_lap(rows)
        survivors = qualifying_cutoffs(len(ordered))[-1]
        for index, row in enumerate(ordered):
            row["knocked_out"] = index >= survivors
        return ordered

    q2_places, q3_places = qualifying_cutoffs(len(rows))
    for row in rows:
        entry = results.get(row["code"], {})

        # The time that counts is the last segment the driver actually set
        # one in - a Q2 lap for someone who reached Q3 without improving.
        for segment in QUALIFYING_SEGMENTS:
            seconds = to_seconds(entry.get(segment)) if segment in entry else None
            if seconds is not None and pd.notna(seconds):
                row["best_seconds"] = seconds
                row["best_lap"] = format_lap(seconds)
                break

        # The segment a driver *reached* is what the partition shows, and the
        # official position is the only thing that records it: a driver can
        # make Q3 and set no lap there (2023 Bahrain, HUL).
        position = entry.get("Position")
        if position is not None and pd.notna(position):
            row["official_position"] = float(position)
            row["segment"] = (
                "Q3"
                if row["official_position"] <= q3_places
                else "Q2" if row["official_position"] <= q2_places else "Q1"
            )
        else:
            row["official_position"] = None
            row["segment"] = next(
                (
                    segment
                    for segment in QUALIFYING_SEGMENTS
                    if segment in entry and pd.notna(entry[segment])
                ),
                None,
            )

    rank = {segment: index for index, segment in enumerate(QUALIFYING_SEGMENTS)}

    def sort_key(row: dict):
        """Official order when known, else segment reached then time in it."""
        if row["official_position"] is not None:
            return (0, row["official_position"], 0.0)
        return (
            1,
            rank.get(row["segment"], len(QUALIFYING_SEGMENTS)),
            row["best_seconds"] if row["best_seconds"] is not None else float("inf"),
        )

    ordered = sorted(rows, key=sort_key)

    leader = ordered[0]["best_seconds"] if ordered else None
    previous = None
    seen_segments = set()
    for row in ordered:
        row["knocked_out"] = row["segment"] != "Q3"
        if row["segment"] and row["segment"] not in seen_segments:
            row["partition"] = SEGMENT_HEADINGS[row["segment"]]
            seen_segments.add(row["segment"])
        if row["best_seconds"] is None or leader is None:
            row["gap"] = "----"
            row["interval"] = "----"
            continue
        row["gap"] = "----" if row is ordered[0] else format_delta(row["best_seconds"] - leader)
        row["interval"] = (
            "----" if previous is None else format_delta(row["best_seconds"] - previous)
        )
        previous = row["best_seconds"]
    return ordered


def _race_gap_seconds(row: dict, results: Dict[str, dict]) -> Optional[float]:
    """Race time behind the winner, from results.Time where available.

    FastF1 reports the winner's total race time and everyone else's gap to
    it in the same column, so only non-winners are read here.
    """
    entry = results.get(row["code"], {})
    if entry.get("Position") in (1, 1.0):
        return 0.0
    return to_seconds(entry.get("Time")) if "Time" in entry else None


def _classify_race(rows: List[dict], results: Dict[str, dict]) -> List[dict]:
    """Race / sprint order: finishing position, with race-time gaps.

    Position comes from ``session.results``; without it the last lap's own
    ``Position`` column is used. Gap and Interval are time behind the leader
    and the car ahead, or ``+N LAP(S)`` once a driver is lapped.
    """

    def sort_key(row: dict):
        official = results.get(row["code"], {}).get("Position")
        if official is not None and pd.notna(official):
            return (0, float(official))
        if row["last_position"] is not None:
            return (1, float(row["last_position"]))
        # Nobody classified them: most laps first, then quickest.
        return (2, -row["laps_completed"])

    ordered = sorted(rows, key=sort_key)
    if not ordered:
        return ordered

    leader_laps = ordered[0]["laps_completed"]
    for row in ordered:
        laps_down = leader_laps - row["laps_completed"]
        row["laps_down"] = max(laps_down, 0)
        row["gap_seconds"] = None if laps_down > 0 else _race_gap_seconds(row, results)

    previous = ordered[0]
    for index, row in enumerate(ordered):
        if index == 0:
            row["gap"] = "----"
            row["interval"] = "----"
            continue

        row["gap"] = (
            format_lap_gap(row["laps_down"])
            if row["laps_down"] > 0
            else format_delta(row["gap_seconds"]) if row["gap_seconds"] is not None else "----"
        )

        laps_behind_ahead = row["laps_down"] - previous["laps_down"]
        if laps_behind_ahead > 0:
            row["interval"] = format_lap_gap(laps_behind_ahead)
        elif row["gap_seconds"] is not None and previous["gap_seconds"] is not None:
            row["interval"] = format_delta(row["gap_seconds"] - previous["gap_seconds"])
        else:
            row["interval"] = "----"
        previous = row
    return ordered


def build_timing_rows(session_data: dict) -> List[dict]:
    """Build the leaderboard rows for one session.

    Ordering, gaps and the knock-out partition all depend on the session
    type - see :func:`_classify_race`, :func:`_classify_qualifying` and
    :func:`_classify_by_best_lap`.
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
                "last_position": _last_position(driver_laps),
                # Only a knock-out session sets these; see _classify_qualifying.
                "knocked_out": False,
                "segment": None,
                "partition": None,
            }
        )

    session_info = session_data.get("session_info")
    results = _results_index(session_data.get("results"))
    if is_race_session(session_info):
        ordered = _classify_race(rows, results)
    elif is_qualifying_session(session_info):
        ordered = _classify_qualifying(rows, results)
    else:
        # Practice: everyone is simply ranked, nobody is knocked out.
        ordered = _classify_by_best_lap(rows)

    fastest = min(
        (r for r in ordered if r["best_seconds"] is not None),
        key=lambda r: r["best_seconds"],
        default=None,
    )
    for position, row in enumerate(ordered, start=1):
        row["position"] = position
        row["is_overall_best"] = row is fastest

    best_possible = theoretical_best(ordered)
    for row in ordered:
        if row["best_seconds"] is not None and best_possible is not None:
            row["diff"] = format_delta(row["best_seconds"] - best_possible)
        else:
            row["diff"] = "----"
    return ordered
