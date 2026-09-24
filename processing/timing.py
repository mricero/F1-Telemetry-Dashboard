"""Timing-tower data model.

Turns a loaded session into the row structure the live-timing leaderboard
renders (see ``layout.md`` section 3). Everything here is pure data: no
Streamlit, no HTML, so the classification and delta logic stays testable.

Ordering depends on the session. A race or sprint is classified by finishing
position, with Gap/Interval as race time behind the leader and the car ahead
(or ``+N LAP`` for lapped cars). Practice and qualifying rank by personal best
lap, where Gap and Interval are lap-time deltas.
"""

import itertools
import re
from collections.abc import Sequence

import numpy as np
import pandas as pd

from processing.time_utils import to_seconds

# Missing values in the tower read as an en dash (UI guideline 5.7).
MISSING = "\u2013"
# The race leader's gap and interval cells (UI guideline 5.7).
LEADER = "LEADER"

# Each of the three sectors is split into this many micro-sectors for the
# heat strip under the sector time (spec section 3.8).
SEGMENTS_PER_SECTOR = 5
SECTORS = 3
TOTAL_SEGMENTS = SEGMENTS_PER_SECTOR * SECTORS

# Official timing-screen convention (layout.md section 3.8): purple for the
# session best through a slice, green for the driver's own best, yellow when
# they were slower than their own best, grey when there is no usable time.
SEGMENT_TOLERANCE = 1e-6

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


def format_lap(seconds: float | None) -> str:
    """``93.456`` -> ``'1:33.456'``; missing values render as an en dash."""
    if seconds is None:
        return MISSING
    try:
        if pd.isna(seconds):
            return MISSING
    except (TypeError, ValueError):
        return MISSING
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:06.3f}" if minutes else f"{rest:.3f}"


def format_delta(seconds: float | None) -> str:
    """Signed gap string, e.g. ``'+0.102'``; missing deltas render as an en dash."""
    if seconds is None or pd.isna(seconds):
        return MISSING
    return f"+{seconds:.3f}" if seconds >= 0 else f"{seconds:.3f}"


def format_lap_gap(laps_down: int) -> str:
    """``1`` -> ``'+1 LAP'``; ``3`` -> ``'+3 LAPS'`` (race convention)."""
    return f"+{laps_down} LAP" if laps_down == 1 else f"+{laps_down} LAPS"


def is_race_session(session_info: dict | None) -> bool:
    """True for races and sprints, which are classified by finishing order."""
    info = session_info or {}
    for key in ("session_type", "session_name"):
        value = info.get(key)
        if value and str(value).strip().lower() in RACE_SESSION_TYPES:
            return True
    return False


def is_qualifying_session(session_info: dict | None) -> bool:
    """True for qualifying and sprint qualifying, which run in segments."""
    info = session_info or {}
    for key in ("session_type", "session_name"):
        value = info.get(key)
        if value and str(value).strip().lower() in QUALIFYING_SESSION_TYPES:
            return True
    return False


def qualifying_cutoffs(car_count: int) -> list[int]:
    """How many cars survive each segment, e.g. 22 cars -> ``[16, 10]``.

    The regulations eliminate the same number after Q1 and Q2, so the count
    follows the entry list rather than a hardcoded top ten.
    """
    eliminated = max((car_count - Q3_PLACES + 1) // 2, 0)
    return [max(car_count - eliminated, Q3_PLACES), Q3_PLACES]


def _results_index(results: pd.DataFrame | None) -> dict[str, dict]:
    """Abbreviation -> official classification fields, or {} when absent."""
    if results is None or not isinstance(results, pd.DataFrame) or results.empty:
        return {}
    if "Abbreviation" not in results.columns:
        return {}
    index: dict[str, dict] = {}
    for _, row in results.iterrows():
        code = row.get("Abbreviation")
        if code is None or pd.isna(code):
            continue
        index[str(code)] = row.to_dict()
    return index


def _driver_meta(drivers_df: pd.DataFrame) -> dict[str, dict]:
    """Acronym -> {team_name, team_colour, full_name, driver_number}."""
    meta: dict[str, dict] = {}
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


def _tyre_history(laps: pd.DataFrame, stints: pd.DataFrame | None = None) -> list[dict]:
    """Stint list for one driver: compound, tyre age, and whether it was new.

    ``laps_used`` is the tyre's **age** (FastF1 ``TyreLife``), which is not the
    stint length when a driver starts on a scrubbed set. Falls back to the
    session's stint table when the laps carry no compound - live lap frames
    built from ``TimingData`` never do.
    """
    if laps.empty or "Compound" not in laps.columns:
        return _tyre_history_from_stints(stints)

    work = laps if "Stint" in laps.columns else laps.assign(Stint=1)
    history = []
    for _, stint in work[work["Compound"].notna()].groupby("Stint", sort=True):
        compound = str(stint["Compound"].iloc[0]).upper()
        stint_laps = len(stint)
        age = stint_laps
        if "TyreLife" in stint.columns:
            life = pd.to_numeric(stint["TyreLife"], errors="coerce").dropna()
            if not life.empty:
                age = int(life.max())
        fresh = None
        if "FreshTyre" in stint.columns:
            first = stint["FreshTyre"].iloc[0]
            fresh = bool(first) if pd.notna(first) else None
        history.append(
            {
                "compound": compound,
                "laps_used": age,
                "stint_laps": stint_laps,
                "fresh": fresh,
            }
        )
    return history


def _tyre_history_from_stints(stints: pd.DataFrame | None) -> list[dict]:
    """Tyre history from the session's stint table (the live path)."""
    if stints is None or stints.empty or "Compound" not in stints.columns:
        return []
    history = []
    for _, stint in stints.iterrows():
        compound = stint.get("Compound")
        if compound is None or pd.isna(compound):
            continue
        laps_used = pd.to_numeric(pd.Series([stint.get("LapCount")]), errors="coerce").iloc[0]
        fresh = stint.get("New")
        history.append(
            {
                "compound": str(compound).upper(),
                "laps_used": int(laps_used) if pd.notna(laps_used) else 0,
                "stint_laps": int(laps_used) if pd.notna(laps_used) else 0,
                "fresh": bool(fresh) if fresh is not None and pd.notna(fresh) else None,
            }
        )
    return history


def _driver_stints(stints: pd.DataFrame | None, driver: str) -> pd.DataFrame | None:
    """One driver's rows from the session stint table, whatever it calls them."""
    if stints is None or stints.empty:
        return None
    for column in ("Driver", "DriverAcronym"):
        if column in stints.columns:
            own = stints[stints[column].astype(str) == str(driver)]
            if not own.empty:
                return own
    return None


def _laps_completed(laps: pd.DataFrame) -> int:
    """Laps the driver has finished.

    Live lap frames append a synthetic row for the lap in progress; counting
    it would put every car one lap ahead of where it is.
    """
    if "IsInProgress" in laps.columns:
        return int((~laps["IsInProgress"].fillna(False).astype(bool)).sum())
    return len(laps)


def _last_position(laps: pd.DataFrame) -> float | None:
    """On-road position at the driver's last lap, when the laps carry it."""
    if laps.empty or "Position" not in laps.columns:
        return None
    positions = pd.to_numeric(laps["Position"], errors="coerce").dropna()
    return float(positions.iloc[-1]) if not positions.empty else None


def _valid_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """Laps whose times may be used for records.

    Excludes laps the stewards deleted (FastF1 ``Deleted``) and laps FastF1
    flags as inaccurately timed (``IsAccurate``) - a deleted track-limits lap
    must not set a sector best.
    """
    valid = laps
    if "Deleted" in valid.columns:
        valid = valid[~valid["Deleted"].fillna(False).astype(bool)]
    if "IsAccurate" in valid.columns:
        valid = valid[valid["IsAccurate"].fillna(True).astype(bool)]
    return valid


def _best_sectors(laps: pd.DataFrame) -> list[float | None]:
    """Each sector's quickest time across all of a driver's valid laps.

    The fastest *lap* rarely contains the driver's fastest sectors, so the
    ideal lap has to look wider than one lap.
    """
    valid = _valid_laps(laps)
    bests: list[float | None] = []
    for index in range(1, SECTORS + 1):
        column = f"Sector{index}Time"
        if column not in valid.columns:
            bests.append(None)
            continue
        values = valid[column]
        # Timedelta columns (FastF1) convert in one call; anything else (live
        # strings, objects) goes through to_seconds value by value.
        if pd.api.types.is_timedelta64_dtype(values):
            seconds = values.dt.total_seconds().dropna()
        else:
            seconds = values.map(to_seconds).dropna()
        bests.append(float(seconds.min()) if not seconds.empty else None)
    return bests


def _ideal_lap(best_sectors: Sequence[float | None]) -> float | None:
    """Sum of sector bests, or None when any sector is missing."""
    if any(value is None for value in best_sectors):
        return None
    return round(sum(float(value) for value in best_sectors if value is not None), 3)


def _speed_trap(laps: pd.DataFrame) -> float | None:
    """Best speed-trap reading across the driver's laps (km/h)."""
    for col in ("SpeedST", "SpeedFL", "SpeedI2", "SpeedI1"):
        if col in laps.columns:
            values = pd.to_numeric(laps[col], errors="coerce").dropna()
            if not values.empty:
                return float(values.max())
    return None


# FastF1 result statuses that are not a classified finish. Anything else that
# is not "Finished" or "+N Lap(s)" is a retirement of some kind (Accident,
# Power Unit, Collision damage, ...), which all read as DNF on a timing screen.
DNS_STATUSES = {"withdrew", "did not start", "did not qualify", "not classified"}
DSQ_STATUSES = {"disqualified", "excluded"}
_LAPS_DOWN = re.compile(r"^\+(\d+)\s+laps?$", re.IGNORECASE)


def status_badge(official_status: str | None) -> str | None:
    """``session.results.Status`` -> a tower badge, or None when unknown.

    FIN / +NL / DNF / DSQ / DNS, so a retirement is not presented as a
    classified finish.
    """
    if official_status is None or pd.isna(official_status):
        return None
    status = str(official_status).strip()
    if not status:
        return None
    lowered = status.lower()
    if lowered == "finished":
        return "FIN"
    laps_down = _LAPS_DOWN.match(lowered)
    if laps_down:
        return f"+{int(laps_down.group(1))}L"
    if lowered in DSQ_STATUSES:
        return "DSQ"
    if lowered in DNS_STATUSES:
        return "DNS"
    return "DNF"


def _flag_is_set(laps: pd.DataFrame, column: str) -> bool:
    """Whether a live TimingData flag is set on any of the driver's rows.

    The feed is lossy, so retirement latches: one message saying a car is out
    stands even if a later partial update omits it.
    """
    if column not in laps.columns:
        return False
    values = laps[column]
    return bool(values.fillna(False).astype(bool).any())


def _live_status(laps: pd.DataFrame) -> str:
    """Live driver state from TimingData flags: OUT / IN PIT / ON TRACK."""
    if _flag_is_set(laps, "Retired") or _flag_is_set(laps, "Stopped"):
        return "OUT"
    last = laps.iloc[-1]
    in_pit = last.get("InPit")
    if in_pit is not None and pd.notna(in_pit) and bool(in_pit):
        return "IN PIT"
    if "InPit" not in laps.columns:
        # No live flags at all (e.g. a source without TimingData): FastF1's
        # pit timestamps are the only hint available.
        pit_in, pit_out = last.get("PitInTime"), last.get("PitOutTime")
        if pit_in is not None and pd.notna(pit_in) and (pit_out is None or pd.isna(pit_out)):
            return "IN PIT"
    return "ON TRACK"


def _status(laps: pd.DataFrame, is_live: bool, official_status: str | None = None) -> str:
    """Driver state badge.

    Live sessions report where the car is now; a finished session reports how
    the driver was classified (a DNF is not "CLASSIFIED").
    """
    if laps.empty:
        return "OUT"
    if is_live:
        return _live_status(laps)
    return status_badge(official_status) or "CLASSIFIED"


def fastest_lap_row(laps_df: pd.DataFrame, driver: str) -> pd.Series | None:
    """The driver's quickest lap, or None when they never set a time."""
    if laps_df is None or laps_df.empty or "Driver" not in laps_df.columns:
        return None
    own = laps_df[laps_df["Driver"].astype(str) == str(driver)]
    if own.empty or "LapTime" not in own.columns:
        return None
    seconds = own["LapTime"].map(to_seconds)
    if seconds.dropna().empty:
        return None
    return own.loc[seconds.idxmin()]


def sector_bounds_for_driver(
    laps_df: pd.DataFrame, driver: str, telemetry: pd.DataFrame
) -> list[float] | None:
    """Real sector-boundary distances for the lap the strips display."""
    lap = fastest_lap_row(laps_df, driver)
    if lap is None:
        return None
    return sector_boundary_distances(
        telemetry, [lap.get(f"Sector{i}Time") for i in range(1, SECTORS + 1)]
    )


def dashboard_frames(session_data: dict) -> tuple[dict, dict]:
    """The telemetry and location frames the dashboard should read.

    The charts honour the user's telemetry scope, but the tower's
    micro-sectors and the map's dominance layer are only meaningful over a
    single lap: with ``scope='session'`` a race's Distance runs to ~300 km.
    Sources that can provide them put per-driver fastest-lap frames under
    ``dashboard_telemetry``/``dashboard_location``; everything else falls
    back to the chart frames.
    """
    telemetry = session_data.get("dashboard_telemetry") or session_data.get("telemetry") or {}
    location = session_data.get("dashboard_location") or session_data.get("location") or {}
    return telemetry, location


def segment_boundaries(distance: np.ndarray, segments: int) -> np.ndarray:
    """Sample indices that split a trace into equal-**distance** slices.

    Telemetry is sampled in time, so points bunch up in slow corners: slicing
    by point index puts slice *k* over a different stretch of track than slice
    *k* of the timing data. Returns ``segments + 1`` indices into ``distance``.
    """
    values = np.asarray(distance, dtype=float)
    if len(values) < 2:
        return np.zeros(segments + 1, dtype=int)
    span = values[-1] - values[0]
    if not np.isfinite(span) or span <= 0:
        # Degenerate (constant or unusable) distance: fall back to index split.
        return np.linspace(0, len(values) - 1, segments + 1).astype(int)
    marks = np.linspace(values[0], values[-1], segments + 1)
    bounds = np.searchsorted(values, marks, side="left")
    bounds[0] = 0
    bounds[-1] = len(values) - 1
    return np.maximum.accumulate(bounds)


def _distance_and_elapsed(telemetry: pd.DataFrame) -> tuple[np.ndarray, np.ndarray] | None:
    """Sorted (distance, elapsed-seconds) arrays for one lap trace."""
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
    if int(ok.sum()) < 2:
        return None
    values, seconds = distance[ok].to_numpy(float), elapsed[ok].to_numpy(float)
    order = np.argsort(values, kind="stable")
    values, seconds = values[order], seconds[order]
    if values[-1] - values[0] <= 0:
        return None
    return values, seconds


def sector_boundary_distances(
    telemetry: pd.DataFrame, sector_times: Sequence[float | None]
) -> list[float] | None:
    """Distances (m) where each sector ends on this lap.

    Sector boundaries are nowhere near 1/3 and 2/3 of a lap, so the strip
    under "Sector 1" is only sector 1 if the split comes from the timing: the
    distance reached at ``Sector1Time`` and at ``Sector1Time + Sector2Time``.
    Falls back to equal thirds when the sector times are unusable.
    """
    frames = _distance_and_elapsed(telemetry)
    if frames is None:
        return None
    distance, elapsed = frames
    start, end = float(distance[0]), float(distance[-1])
    thirds = [start, start + (end - start) / 3, start + 2 * (end - start) / 3, end]

    raw = [to_seconds(value) for value in sector_times[:SECTORS]]
    if len(raw) < SECTORS or any(value is None or pd.isna(value) for value in raw):
        return thirds
    seconds = [float(value) for value in raw if value is not None]

    lap_time = float(elapsed[-1] - elapsed[0])
    total = float(sum(seconds))
    if total <= 0 or lap_time <= 0:
        return thirds
    # Sector times are measured from the line; telemetry Time starts at the
    # lap's first sample, so scale to the trace's own elapsed span.
    scale = lap_time / total

    bounds = [start]
    cumulative = 0.0
    for value in seconds[:-1]:
        cumulative += value * scale
        bounds.append(float(np.interp(elapsed[0] + cumulative, elapsed, distance)))
    bounds.append(end)
    return bounds if all(b <= n for b, n in itertools.pairwise(bounds)) else thirds


def micro_sector_marks(
    sector_bounds: Sequence[float] | None, segments: int = TOTAL_SEGMENTS
) -> list[float] | None:
    """Distance marks splitting each real sector into equal mini-sectors.

    Returns ``segments + 1`` distances, or None when the sector boundaries
    are unusable - shared by the timing strips and the map so the two views
    colour the same stretches of track.
    """
    if sector_bounds is None or len(sector_bounds) != SECTORS + 1:
        return None
    per_sector = segments // SECTORS
    marks = [float(sector_bounds[0])]
    for index in range(SECTORS):
        edges = np.linspace(sector_bounds[index], sector_bounds[index + 1], per_sector + 1)
        marks.extend(float(edge) for edge in edges[1:])
    return marks


def micro_sector_times(
    telemetry: pd.DataFrame,
    segments: int = TOTAL_SEGMENTS,
    sector_bounds: Sequence[float] | None = None,
) -> np.ndarray | None:
    """Time (s) spent in each mini-sector of one lap.

    With ``sector_bounds`` (from :func:`sector_boundary_distances`) each real
    sector is split into equal-distance mini-sectors, so slice *k* of the
    strip sits under the sector it belongs to. Without them the whole lap is
    split evenly, which is only an approximation.
    """
    frames = _distance_and_elapsed(telemetry)
    if frames is None:
        return None
    distance, elapsed = frames
    if len(distance) < segments * 2:
        return None

    marks = micro_sector_marks(sector_bounds, segments)
    if marks is None:
        marks = list(np.linspace(distance[0], distance[-1], segments + 1))

    times = np.diff(np.interp(np.asarray(marks, dtype=float), distance, elapsed))
    return times if np.all(np.isfinite(times)) and np.all(times >= 0) else None


def segment_states(
    per_driver_laps: dict[str, np.ndarray | Sequence[np.ndarray]],
) -> dict[str, list[str]]:
    """Colour each driver's displayed mini-sectors, F1 convention.

    PURPLE = the session best through that slice, GREEN = the driver's own
    best, YELLOW = slower than their own best, NONE = no usable time
    (``layout.md`` section 3.8). "Personal best" needs more than one lap, so
    each value may be a sequence of laps: the **first** is the lap on screen
    and the rest only contribute to that driver's own best.
    """
    if not per_driver_laps:
        return {}

    laps_by_driver = {
        driver: ([laps] if isinstance(laps, np.ndarray) else [np.asarray(x) for x in laps])
        for driver, laps in per_driver_laps.items()
    }
    laps_by_driver = {driver: laps for driver, laps in laps_by_driver.items() if laps}
    if not laps_by_driver:
        return {}

    def _column_min(stack: list[np.ndarray]) -> np.ndarray:
        """Per-slice minimum. Non-finite entries are ignored, and a slice with
        no usable time comes back NaN (np.nanmin would warn on an all-NaN
        column)."""
        values = np.vstack(stack).astype(float)
        values = np.where(np.isfinite(values), values, np.inf)
        best = values.min(axis=0)
        return np.where(np.isfinite(best), best, np.nan)

    personal_best = {driver: _column_min(laps) for driver, laps in laps_by_driver.items()}
    session_best = _column_min(list(personal_best.values()))

    states: dict[str, list[str]] = {}
    for driver, laps in laps_by_driver.items():
        shown, own = laps[0], personal_best[driver]
        row = []
        for value, mine, best in zip(shown, own, session_best, strict=False):
            if not np.isfinite(value) or not np.isfinite(best) or best <= 0:
                row.append("NONE")
            elif value <= best + SEGMENT_TOLERANCE:
                row.append("PURPLE")
            elif value <= mine + SEGMENT_TOLERANCE:
                row.append("GREEN")
            else:
                row.append("YELLOW")
        states[driver] = row
    return states


def sector_leaders(rows: Sequence[dict], top_n: int = 3) -> list[list[dict]]:
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


def theoretical_best(rows: Sequence[dict]) -> float | None:
    """Session ideal: each sector's best from any driver, on any lap."""
    session_bests: list[float | None] = []
    for index in range(SECTORS):
        times = [
            row["best_sectors"][index]
            for row in rows
            if row.get("best_sectors") and row["best_sectors"][index] is not None
        ]
        session_bests.append(min(times) if times else None)
    return _ideal_lap(session_bests)


def _classify_by_best_lap(rows: list[dict]) -> list[dict]:
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
            row["gap"] = MISSING
            row["interval"] = MISSING
            continue
        row["gap"] = LEADER if row is timed[0] else format_delta(row["best_seconds"] - leader)
        row["interval"] = (
            LEADER if previous is None else format_delta(row["best_seconds"] - previous)
        )
        previous = row["best_seconds"]
    return ordered


def _classify_qualifying(rows: list[dict], results: dict[str, dict]) -> list[dict]:
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
            row["gap"] = MISSING
            row["interval"] = MISSING
            continue
        row["gap"] = LEADER if row is ordered[0] else format_delta(row["best_seconds"] - leader)
        row["interval"] = (
            LEADER if previous is None else format_delta(row["best_seconds"] - previous)
        )
        previous = row["best_seconds"]
    return ordered


def _race_gap_seconds(row: dict, results: dict[str, dict]) -> float | None:
    """Race time behind the winner, from results.Time where available.

    FastF1 reports the winner's total race time and everyone else's gap to
    it in the same column, so only non-winners are read here.
    """
    entry = results.get(row["code"], {})
    if entry.get("Position") in (1, 1.0):
        return 0.0
    return to_seconds(entry.get("Time")) if "Time" in entry else None


def _classify_race(rows: list[dict], results: dict[str, dict]) -> list[dict]:
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
            row["gap"] = LEADER
            row["interval"] = LEADER
            continue

        row["gap"] = (
            format_lap_gap(row["laps_down"])
            if row["laps_down"] > 0
            else format_delta(row["gap_seconds"]) if row["gap_seconds"] is not None else MISSING
        )

        laps_behind_ahead = row["laps_down"] - previous["laps_down"]
        if laps_behind_ahead > 0:
            row["interval"] = format_lap_gap(laps_behind_ahead)
        elif row["gap_seconds"] is not None and previous["gap_seconds"] is not None:
            row["interval"] = format_delta(row["gap_seconds"] - previous["gap_seconds"])
        else:
            row["interval"] = MISSING
        previous = row
    return ordered


def _in_progress(laps: pd.DataFrame) -> pd.Series:
    """Rows standing for the lap a driver is on, not a completed one."""
    if "IsInProgress" not in laps.columns:
        return pd.Series(False, index=laps.index)
    return laps["IsInProgress"].astype("boolean").fillna(False).astype(bool)


def _value(value):
    """A standings cell as a plain Python value (None for NA)."""
    if value is None:
        return None
    try:
        return None if pd.isna(value) else value
    except (TypeError, ValueError):
        return value


def _rows_from_standings(session_data: dict, standings: pd.DataFrame) -> list[dict]:
    """Tower rows for a replay snapshot, ordered by its ``standings``.

    Order, gap, interval, status, last/best lap and the sector cells come
    from the standings (the replay model's series at that moment), so the
    final ``results`` are never read. The micro-sector strips are left
    empty: they come from fastest-lap telemetry set later in the session.
    """
    laps_df = session_data.get("laps")
    if laps_df is None or laps_df.empty or "Driver" not in laps_df.columns:
        laps_df = pd.DataFrame(columns=["Driver", "LapNumber"])
    meta = _driver_meta(session_data.get("drivers"))
    running = _in_progress(laps_df)
    by_driver = {
        str(code): group.sort_values("LapNumber")
        for code, group in laps_df.groupby(laps_df["Driver"].astype(str), sort=False)
    }
    empty_states = ["NONE"] * SEGMENTS_PER_SECTOR

    rows = []
    for record in standings.to_dict("records"):
        code = str(record["Driver"])
        own = by_driver.get(code, laps_df.iloc[0:0])
        done = own[~running.reindex(own.index, fill_value=False)]
        info = meta.get(code, {})
        sectors = []
        for index in range(1, SECTORS + 1):
            seconds = _value(record.get(f"S{index}"))
            sectors.append(
                {
                    "seconds": float(seconds) if seconds is not None else None,
                    "display": f"{float(seconds):.3f}" if seconds is not None else MISSING,
                    "segments": list(empty_states),
                }
            )
        best_sectors = _best_sectors(done)
        status = _value(record.get("Status")) or "ON TRACK"
        best_seconds = _value(record.get("BestSeconds"))
        last_seconds = _value(record.get("LastSeconds"))
        speed = _speed_trap(done)
        rows.append(
            {
                "code": code,
                "full_name": info.get("full_name", code),
                "team_name": info.get("team_name", ""),
                "team_colour": info.get("team_colour", ""),
                "status": status,
                "position": int(record["Position"]),
                "gap": _value(record.get("Gap")) or MISSING,
                "interval": _value(record.get("Interval")) or MISSING,
                "best_seconds": float(best_seconds) if best_seconds is not None else None,
                "best_lap": _value(record.get("BestLap")) or MISSING,
                "last_lap": _value(record.get("LastLap")) or MISSING,
                "last_seconds": float(last_seconds) if last_seconds is not None else None,
                "last_is_session_best": record.get("LastFlag") == "sb",
                "last_is_personal_best": record.get("LastFlag") == "pb",
                "sectors": sectors,
                "best_sectors": best_sectors,
                "personal_ideal": _ideal_lap(best_sectors),
                "tyre_history": _tyre_history(
                    own, _driver_stints(session_data.get("stints"), code)
                ),
                "speed_kmh": 0.0 if status == "IN PIT" else speed,
                "laps_completed": len(done),
                "last_position": None,
                "pits": _value(record.get("Pits")),
                "knocked_out": status == "KO",
                "segment": None,
                "partition": _value(record.get("Partition")),
            }
        )

    fastest = min(
        (r for r in rows if r["best_seconds"] is not None),
        key=lambda r: r["best_seconds"],
        default=None,
    )
    best_possible = theoretical_best(rows)
    for row in rows:
        row["is_overall_best"] = row is fastest
        if row["best_seconds"] is not None and best_possible is not None:
            row["diff"] = format_delta(row["best_seconds"] - best_possible)
        else:
            row["diff"] = MISSING
    return rows


def build_timing_rows(session_data: dict) -> list[dict]:
    """Build the leaderboard rows for one session.

    Ordering, gaps and the knock-out partition all depend on the session
    type - see :func:`_classify_race`, :func:`_classify_qualifying` and
    :func:`_classify_by_best_lap`. A replay snapshot carries ``standings``
    (the tower as it stood at that moment), which then decide instead.
    """
    standings = session_data.get("standings")
    if isinstance(standings, pd.DataFrame) and not standings.empty:
        return _rows_from_standings(session_data, standings)

    laps_df = session_data.get("laps")
    if laps_df is None or laps_df.empty or "Driver" not in laps_df.columns:
        return []

    is_live = bool(session_data.get("is_live"))
    meta = _driver_meta(session_data.get("drivers"))
    results = _results_index(session_data.get("results"))
    telemetry, _ = dashboard_frames(session_data)

    # Micro-sector heat strips come from each driver's own telemetry trace,
    # split at that lap's real sector boundaries rather than at 1/3 and 2/3.
    per_driver_segments: dict[str, np.ndarray | Sequence[np.ndarray]] = {}
    for code, frame in telemetry.items():
        times = micro_sector_times(
            frame, sector_bounds=sector_bounds_for_driver(laps_df, str(code), frame)
        )
        if times is not None:
            per_driver_segments[str(code)] = times
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
                    "display": f"{seconds:.3f}" if seconds is not None else MISSING,
                    "segments": states.get(str(code), default_states)[
                        start : start + SEGMENTS_PER_SECTOR
                    ],
                }
            )

        best_sectors = _best_sectors(driver_laps)
        info = meta.get(str(code), {})
        rows.append(
            {
                "code": str(code),
                "full_name": info.get("full_name", str(code)),
                "team_name": info.get("team_name", ""),
                "team_colour": info.get("team_colour", ""),
                "status": _status(driver_laps, is_live, results.get(str(code), {}).get("Status")),
                "best_seconds": best_seconds,
                "best_lap": format_lap(best_seconds),
                "last_lap": format_lap(last_seconds),
                "last_seconds": last_seconds,
                # Set once every row is known (spec section 3.4).
                "last_is_session_best": False,
                "sectors": sectors,
                "best_sectors": best_sectors,
                "personal_ideal": _ideal_lap(best_sectors),
                "tyre_history": _tyre_history(
                    driver_laps, _driver_stints(session_data.get("stints"), str(code))
                ),
                "speed_kmh": _speed_trap(driver_laps),
                "laps_completed": _laps_completed(driver_laps),
                "last_position": _last_position(driver_laps),
                # Only a knock-out session sets these; see _classify_qualifying.
                "knocked_out": False,
                "segment": None,
                "partition": None,
            }
        )

    session_info = session_data.get("session_info")
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
    session_best = fastest["best_seconds"] if fastest else None
    for position, row in enumerate(ordered, start=1):
        row["position"] = position
        row["is_overall_best"] = row is fastest
        # A last lap that *is* the session best gets the purple treatment.
        row["last_is_session_best"] = bool(
            session_best is not None
            and row["last_seconds"] is not None
            and abs(row["last_seconds"] - session_best) < 1e-6
        )
        # A car in the pit lane is not doing any speed (spec section 3.13).
        if row["status"] == "IN PIT":
            row["speed_kmh"] = 0.0

    best_possible = theoretical_best(ordered)
    for row in ordered:
        if row["best_seconds"] is not None and best_possible is not None:
            row["diff"] = format_delta(row["best_seconds"] - best_possible)
        else:
            row["diff"] = MISSING
    return ordered
