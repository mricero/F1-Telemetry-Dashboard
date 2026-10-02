"""The session as it stood at any moment (IMPROVEMENTS.md REPLAY-03).

Every renderer used to read end-of-session aggregates: the tower was the
final classification whatever the replay cursor said. This module owns the
time semantics instead.

:func:`tower_series` turns a loaded session into change-point series - for
each driver and each tower field, the times at which the value changed and
the value from then on - so the value at ``t`` is a binary search, not a
recomputation. :func:`snapshot_at` uses those series to produce a
unified-dict-shaped snapshot the existing renderers can draw, and
:func:`events` lists what is worth jumping to.

Ground rule: a snapshot never sees the future. Everything a snapshot shows
at ``t`` is derived from rows stamped at or before ``t`` (lap end times,
sector times, pit entries and exits, stream updates, position samples, race
control, weather, track status); the final ``results`` never leak in. Pure
module: no Streamlit, no network.
"""

import bisect
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from processing.replay import ReplayClock, format_clock, replay_clock
from processing.time_utils import seconds_series, to_seconds
from processing.timing import (
    LEADER,
    MISSING,
    format_lap_gap,
    is_qualifying_session,
    is_race_session,
    qualifying_cutoffs,
)

# Car states in the tower. The UI shows ON TRACK as an empty cell.
ON_TRACK = "ON TRACK"
IN_PIT = "IN PIT"
OUT = "OUT"
FINISHED = "FIN"
KNOCKED_OUT = "KO"

# A car that has sent no position for this long is out of the session.
OUT_AFTER_SECONDS = 5.0

# The stream's positions flicker at the start (several updates within a
# fraction of a second); a value counts once it has held this long.
POSITION_SETTLE_SECONDS = 0.5

# Before any data: far enough back to precede every session clock.
BEFORE_EVERYTHING = -1e12

# Official TrackStatus codes -> flag states (the keys of ui.theme.FLAG_STATES).
TRACK_STATUS_FLAGS = {
    "1": "GREEN",
    "2": "YELLOW",
    "4": "SAFETY CAR",
    "5": "RED",
    "6": "VSC",
    "7": "VSC",
}

SEGMENT_NAMES = ("Q1", "Q2", "Q3")
SPRINT_SEGMENT_NAMES = ("SQ1", "SQ2", "SQ3")
_SPRINT_QUALIFYING = {"sq", "sprint qualifying", "sprint shootout"}


def segment_names(info: dict | None) -> tuple[str, ...]:
    """``SQ1``..``SQ3`` for sprint qualifying, else ``Q1``..``Q3`` (REPLAY-24)."""
    for key in ("session_type", "session_name"):
        value = str((info or {}).get(key) or "").strip().lower()
        if value in _SPRINT_QUALIFYING:
            return SPRINT_SEGMENT_NAMES
    return SEGMENT_NAMES

TOWER_FIELDS = (
    "position",
    "gap",
    "gap_s",
    "laps_down",
    "interval",
    "interval_s",
    "lap",
    "last",
    "last_s",
    "last_flag",
    "last_deleted",
    "last_deleted_reason",
    "best",
    "best_s",
    "s1",
    "s2",
    "s3",
    "tyre",
    "age",
    "new",
    "stint",
    "pits",
    "status",
    "partition",
    "flying",
)

FIELD_DEFAULTS: dict = {
    "position": None,
    "gap": MISSING,
    "gap_s": None,
    "laps_down": None,
    "interval": MISSING,
    "interval_s": None,
    "lap": 0,
    "last": MISSING,
    "last_s": None,
    "last_flag": None,
    "last_deleted": False,
    "last_deleted_reason": None,
    "best": MISSING,
    "best_s": None,
    "s1": None,
    "s2": None,
    "s3": None,
    "tyre": None,
    "age": None,
    "new": None,
    "stint": None,
    "pits": 0,
    "status": ON_TRACK,
    "partition": None,
    "flying": False,
}

STANDINGS_COLUMNS = [
    "Flying",
    "Driver",
    "Position",
    "Gap",
    "GapSeconds",
    "LapsDown",
    "Interval",
    "IntervalSeconds",
    "Status",
    "Pits",
    "Lap",
    "LastLap",
    "LastSeconds",
    "LastFlag",
    "LastDeleted",
    "LastDeletedReason",
    "BestLap",
    "BestSeconds",
    "S1",
    "S2",
    "S3",
    "Tyre",
    "TyreAge",
    "TyreNew",
    "Stint",
    "Partition",
]

# standings column <- tower field
STANDINGS_FIELDS = {
    "Gap": "gap",
    "GapSeconds": "gap_s",
    "LapsDown": "laps_down",
    "Interval": "interval",
    "IntervalSeconds": "interval_s",
    "Status": "status",
    "Pits": "pits",
    "Lap": "lap",
    "LastLap": "last",
    "LastSeconds": "last_s",
    "LastFlag": "last_flag",
    "LastDeleted": "last_deleted",
    "LastDeletedReason": "last_deleted_reason",
    "BestLap": "best",
    "BestSeconds": "best_s",
    "S1": "s1",
    "S2": "s2",
    "S3": "s3",
    "Tyre": "tyre",
    "TyreAge": "age",
    "TyreNew": "new",
    "Stint": "stint",
    "Partition": "partition",
    "Flying": "flying",
}


# --------------------------------------------------------------------------
# Formatting (UI guideline 5.7), computed here so the server view and the
# browser player show identical strings.
# --------------------------------------------------------------------------


def format_gap(seconds: float | None) -> str:
    """``4.2113`` -> ``'+4.211'``; a minute or more -> ``'+1:02.3'``."""
    if seconds is None or pd.isna(seconds):
        return MISSING
    value = max(float(seconds), 0.0)
    if value < 60:
        return f"+{value:.3f}"
    minutes, rest = divmod(value, 60)
    return f"+{int(minutes)}:{rest:04.1f}"


def format_laptime(seconds: float | None) -> str:
    """``92.456`` -> ``'1:32.456'``; missing -> en dash."""
    if seconds is None or pd.isna(seconds):
        return MISSING
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:06.3f}"


def _clean(value):
    """NaN/NA/NaT -> None, numpy scalars -> Python, so values compare plainly."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    if isinstance(value, np.generic):
        return value.item()
    return value


# --------------------------------------------------------------------------
# Change-point series
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSeries:
    """A value that changes at known times: ``v[i]`` holds from ``t[i]`` on."""

    t: np.ndarray
    v: tuple

    def at(self, moment: float, default=None):
        index = int(np.searchsorted(self.t, moment, side="right")) - 1
        return self.v[index] if index >= 0 else default

    def __len__(self) -> int:
        return len(self.v)


def _series(points: Iterable[tuple[float, object]]) -> FieldSeries:
    """Sorted, with consecutive repeats removed (a point only on a change)."""
    ordered = sorted(
        ((float(t), _clean(v)) for t, v in points if t is not None and not pd.isna(t)),
        key=lambda item: item[0],
    )
    times: list[float] = []
    values: list = []
    for moment, value in ordered:
        if times and moment == times[-1]:
            values[-1] = value  # a later write at the same instant wins
            if len(values) > 1 and values[-1] == values[-2]:
                times.pop()
                values.pop()
            continue
        if values and value == values[-1]:
            continue
        times.append(moment)
        values.append(value)
    return FieldSeries(np.asarray(times, dtype=float), tuple(values))


def _combine(
    first: FieldSeries,
    second: FieldSeries,
    merge: Callable[[object, object], object],
    defaults: tuple = (None, None),
) -> FieldSeries:
    """A series derived from two others, evaluated at every change of either."""
    moments = sorted(set(first.t.tolist()) | set(second.t.tolist()))
    return _series((m, merge(first.at(m, defaults[0]), second.at(m, defaults[1]))) for m in moments)


@dataclass
class TowerSeries:
    """Every tower field of every driver as change-point series.

    ``kind`` is ``race``, ``qualifying`` or ``practice``. ``estimated`` is
    True when race gaps come from the timing-line fallback rather than the
    official timing stream.
    """

    kind: str
    drivers: tuple[str, ...]
    fields: dict[str, dict[str, FieldSeries]]
    leader_lap: FieldSeries
    total_laps: int | None
    segment_starts: tuple[float, ...] = ()
    chequered: float | None = None
    estimated: bool = False
    order_hint: dict[str, int] = field(default_factory=dict)
    segment_names: tuple[str, ...] = SEGMENT_NAMES
    # Red-flag (track status 5) spans, for the segment clock (REPLAY-24).
    suspensions: tuple[tuple[float, float], ...] = ()

    def value(self, code: str, name: str, moment: float):
        series = self.fields.get(code, {}).get(name)
        default = FIELD_DEFAULTS.get(name)
        return default if series is None else series.at(moment, default)

    def segment_at(self, moment: float) -> int | None:
        """Index of the qualifying segment running at ``moment`` (None outside)."""
        if not self.segment_starts:
            return None
        index = bisect.bisect_right(list(self.segment_starts), moment) - 1
        return index if index >= 0 else None

    def standings_at(self, moment: float) -> pd.DataFrame:
        """The tower at ``moment``, one row per driver, in running order."""
        rows = []
        for code in self.drivers:
            values = {name: self.value(code, name, moment) for name in TOWER_FIELDS}
            values["code"] = code
            rows.append(values)

        def order(row: dict):
            position = row["position"]
            return (
                position is None,
                position if position is not None else 0,
                self.order_hint.get(row["code"], len(self.drivers)),
            )

        rows.sort(key=order)
        records = []
        for index, row in enumerate(rows, start=1):
            record = {"Driver": row["code"], "Position": index}
            record.update({column: row[name] for column, name in STANDINGS_FIELDS.items()})
            records.append(record)
        return pd.DataFrame(records, columns=STANDINGS_COLUMNS)


# --------------------------------------------------------------------------
# Input normalisation
# --------------------------------------------------------------------------


def _seconds(frame: pd.DataFrame, column: str) -> np.ndarray:
    if column not in frame.columns:
        return np.full(len(frame), np.nan)
    return seconds_series(frame[column].reset_index(drop=True)).to_numpy(float)


def _flags(frame: pd.DataFrame, column: str, default: bool) -> np.ndarray:
    if column not in frame.columns:
        return np.full(len(frame), default)
    values = frame[column].reset_index(drop=True)
    return values.astype("boolean").fillna(default).to_numpy(bool)


LAP_TABLE_COLUMNS = [
    "Driver",
    "LapNumber",
    "end",
    "start",
    "pit_in",
    "pit_out",
    "lap_s",
    "s1",
    "s2",
    "s3",
    "s1_at",
    "s2_at",
    "s3_at",
    "valid",
    "base_valid",
    "del_at",
    "reinst_at",
    "del_reason",
    "Compound",
    "TyreLife",
    "FreshTyre",
    "Stint",
    "row",
]


# FastF1's own patterns (fastf1.core.Session._set_laps_deleted_from_rcm):
# FastF1 sets ``Deleted`` from these race-control messages, which arrive
# minutes after the lap ended (REPLAY-20).
DELETED_MESSAGE = re.compile(r"CAR (\d{1,2}) .* TIME (\d:\d\d\.\d\d\d) DELETED - (.*)")
REINSTATED_MESSAGE = re.compile(r"CAR (\d{1,2}) .* TIME (\d:\d\d\.\d\d\d) .*REINSTATED.*")
_LOCAL_CLOCK = re.compile(r"\d\d:\d\d:\d\d")


def _deletion_messages(
    control: pd.DataFrame | None,
) -> tuple[dict[tuple[str, float], tuple[float, str]], dict[tuple[str, float], list[float]]]:
    """``(car, lap seconds) -> (when, reason)`` for deletions, and the
    reinstatement times per lap, from race control on the session clock."""
    deleted: dict[tuple[str, float], tuple[float, str]] = {}
    reinstated: dict[tuple[str, float], list[float]] = {}
    if control is None or control.empty or "Message" not in control.columns:
        return deleted, reinstated
    stamps = _event_seconds(control)
    for moment, message in zip(stamps.tolist(), control["Message"].tolist(), strict=True):
        if np.isnan(moment) or not isinstance(message, str):
            continue
        match = DELETED_MESSAGE.match(message)
        if match:
            key = (match[1], to_seconds(match[2]) or 0.0)
            reason = " ".join(_LOCAL_CLOCK.sub("", match[3]).split())
            if key not in deleted or moment < deleted[key][0]:
                deleted[key] = (float(moment), reason)
            continue
        match = REINSTATED_MESSAGE.match(message)
        if match:
            key = (match[1], to_seconds(match[2]) or 0.0)
            reinstated.setdefault(key, []).append(float(moment))
    return deleted, reinstated


def _deletion_windows(
    work: pd.DataFrame, end: np.ndarray, lap_s: np.ndarray, control: pd.DataFrame | None
) -> tuple[np.ndarray, np.ndarray, list]:
    """When each lap was deleted and reinstated (``inf`` = never).

    A lap counts as valid from its end until ``del_at`` and again from
    ``reinst_at``. A lap marked ``Deleted`` without a matching message (a
    source without race control) is deleted from the moment it ends, which
    is what the tower showed before REPLAY-20.
    """
    count = len(work)
    del_at = np.full(count, np.inf)
    reinst_at = np.full(count, np.inf)
    reasons: list = [None] * count
    final = _flags(work, "Deleted", False)
    if "DeletedReason" in work.columns:
        for index, reason in enumerate(work["DeletedReason"].tolist()):
            if isinstance(reason, str) and reason.strip():
                reasons[index] = " ".join(reason.split())
    messages, reinstatements = _deletion_messages(control)
    numbers = (
        work["DriverNumber"].astype(str).tolist()
        if messages and "DriverNumber" in work.columns
        else None
    )
    for index in range(count):
        found = None
        if numbers is not None and not np.isnan(lap_s[index]):
            key = (numbers[index], round(float(lap_s[index]), 3))
            found = messages.get(key)
        if found is not None:
            when, reason = found
            if not np.isnan(end[index]):
                when = max(when, float(end[index]))
            del_at[index] = when
            reasons[index] = reasons[index] or reason or None
            later = [m for m in reinstatements.get(key, []) if m >= when]
            if later:
                reinst_at[index] = min(later)
        elif final[index]:
            del_at[index] = float(end[index]) if not np.isnan(end[index]) else -np.inf
    return del_at, reinst_at, reasons


def valid_at(lap, moment: float) -> bool:
    """Whether a lap-table row counts as a valid lap at ``moment``."""
    return bool(lap.base_valid) and not (lap.del_at <= moment < lap.reinst_at)


def session_lap_table(session_data: dict) -> pd.DataFrame:
    """:func:`lap_table` with the deletion times from the session's race control."""
    return lap_table(session_data.get("laps"), session_data.get("race_control"))


def lap_table(laps: pd.DataFrame | None, race_control: pd.DataFrame | None = None) -> pd.DataFrame:
    """Laps with every time as float session seconds, sorted per driver.

    ``row`` is the lap's position in the frame passed in. Sector session
    times fall back to ``LapStartTime`` plus the sector durations when
    FastF1 did not record them. ``valid`` is the final verdict;
    ``base_valid``/``del_at``/``reinst_at`` say when it was known
    (:func:`valid_at`, REPLAY-20).
    """
    if laps is None or laps.empty or "Driver" not in laps.columns:
        return pd.DataFrame(columns=LAP_TABLE_COLUMNS)

    work = laps.reset_index(drop=True)
    missing = pd.Series([None] * len(work), dtype=object)
    table = pd.DataFrame(
        {
            "Driver": work["Driver"].astype(str),
            "LapNumber": pd.to_numeric(work.get("LapNumber", missing), errors="coerce"),
            "end": _seconds(work, "Time"),
            "start": _seconds(work, "LapStartTime"),
            "pit_in": _seconds(work, "PitInTime"),
            "pit_out": _seconds(work, "PitOutTime"),
            "lap_s": _seconds(work, "LapTime"),
            "s1": _seconds(work, "Sector1Time"),
            "s2": _seconds(work, "Sector2Time"),
            "s3": _seconds(work, "Sector3Time"),
            "s1_at": _seconds(work, "Sector1SessionTime"),
            "s2_at": _seconds(work, "Sector2SessionTime"),
            "s3_at": _seconds(work, "Sector3SessionTime"),
            "valid": False,
            "Compound": work.get("Compound", missing),
            "TyreLife": pd.to_numeric(work.get("TyreLife", missing), errors="coerce"),
            "FreshTyre": work.get("FreshTyre", missing),
            "Stint": pd.to_numeric(work.get("Stint", missing), errors="coerce"),
            "row": np.arange(len(work)),
        }
    )
    table["s1_at"] = table["s1_at"].fillna(table["start"] + table["s1"])
    table["s2_at"] = table["s2_at"].fillna(table["s1_at"] + table["s2"])
    table["s3_at"] = table["s3_at"].fillna(table["end"])
    base_valid = _flags(work, "IsAccurate", True) & table["lap_s"].notna().to_numpy()
    del_at, reinst_at, reasons = _deletion_windows(
        work, table["end"].to_numpy(float), table["lap_s"].to_numpy(float), race_control
    )
    table["base_valid"] = base_valid
    table["del_at"] = del_at
    table["reinst_at"] = reinst_at
    table["del_reason"] = pd.Series(reasons, dtype=object)
    table["valid"] = base_valid & ~((del_at < np.inf) & np.isinf(reinst_at))
    return table.sort_values(["Driver", "LapNumber"], kind="stable").reset_index(drop=True)


def _event_seconds(frame: pd.DataFrame | None) -> np.ndarray:
    """Session seconds of each row of a race-control/weather/status frame.

    Race control carries a wall-clock ``Time`` and the adapter adds
    ``SessionTime``; a wall-clock ``Time`` alone cannot be placed on the
    session clock, so such rows count as never issued.
    """
    if frame is None or frame.empty:
        return np.empty(0)
    for column in ("SessionTime", "Time"):
        if column in frame.columns:
            values = frame[column]
            if pd.api.types.is_datetime64_any_dtype(values):
                continue
            return seconds_series(values.reset_index(drop=True)).to_numpy(float)
    return np.full(len(frame), np.nan)


def rows_until(frame: pd.DataFrame | None, moment: float) -> pd.DataFrame:
    """The rows of a timestamped frame issued at or before ``moment``."""
    if frame is None:
        return pd.DataFrame()
    if frame.empty:
        return frame
    stamps = _event_seconds(frame)
    return frame[stamps <= moment].reset_index(drop=True)


def chequered_times(session_data: dict) -> list[float]:
    """When the chequered flag was shown, as far as the data says.

    The first car to complete the scheduled distance, and race control's
    track-wide ``CHEQUERED`` flag; either may come first by a few seconds.
    """
    moments = []
    info = session_data.get("session_info") or {}
    total = info.get("total_laps")
    table = lap_table(session_data.get("laps"))
    if total and not table.empty:
        finishers = table[(table["LapNumber"] >= float(total)) & table["end"].notna()]
        if not finishers.empty:
            moments.append(float(finishers["end"].min()))
    control = session_data.get("race_control")
    if control is not None and not control.empty and "Flag" in control.columns:
        stamps = _event_seconds(control)
        flags = control["Flag"].astype(str).str.upper().to_numpy()
        if "Scope" in control.columns:
            scope = control["Scope"].astype(str).str.lower().to_numpy()
            track_wide = np.isin(scope, ["track", "none", "nan", ""])
        else:
            track_wide = np.full(len(control), True)
        mask = (flags == "CHEQUERED") & track_wide & ~np.isnan(stamps)
        moments.extend(float(value) for value in stamps[mask])
    return sorted(moments)


def flag_state(session_data: dict) -> str:
    """The track's flag at the moment a snapshot shows (``FLAG_STATES`` key).

    CHEQUERED once the flag has fallen, else the latest track status, else
    GREEN.
    """
    info = session_data.get("session_info") or {}
    moment = info.get("replay_time")
    for start, end in chequered_windows(session_data):
        if moment is None and np.isinf(end):
            return "CHEQUERED"
        if moment is not None and start <= moment < end:
            return "CHEQUERED"
    status = session_data.get("track_status")
    if status is not None and not status.empty and "Status" in status.columns:
        return TRACK_STATUS_FLAGS.get(str(status["Status"].iloc[-1]), "GREEN")
    return "GREEN"


def chequered_windows(session_data: dict) -> list[tuple[float, float]]:
    """``[start, end)`` spans in which the chequered flag is the flag state.

    A race ends at its first chequered flag. Qualifying shows a track-wide
    CHEQUERED at the end of every segment (Q1-Q3, SQ1-SQ3), and each one
    lasts only until the next segment starts (REPLAY-17). Practice ends at
    its last one.
    """
    flags = chequered_times(session_data)
    if not flags:
        return []
    info = session_data.get("session_info") or {}
    kind = _session_kind(info)
    if kind == "race":
        return [(flags[0], np.inf)]
    starts = sorted(float(s) for s in (info.get("segment_starts") or []))
    if kind != "qualifying" or not starts:
        return [(flags[-1], np.inf)]
    windows = []
    bounds = [*starts[1:], np.inf]
    for index, segment_start in enumerate(starts):
        upto = bounds[index]
        inside = [f for f in flags if (index == 0 or f >= segment_start) and f < upto]
        if inside:
            windows.append((inside[0], upto))
    return windows


def _status_points(session_data: dict) -> list[tuple[float, str]]:
    points: list[tuple[float, str]] = []
    status = session_data.get("track_status")
    if status is not None and not status.empty and "Status" in status.columns:
        codes = status["Status"].astype(str).tolist()
        for moment, code in zip(_event_seconds(status).tolist(), codes, strict=True):
            if not np.isnan(moment):
                points.append((float(moment), TRACK_STATUS_FLAGS.get(code, "GREEN")))
    return sorted(points, key=lambda point: point[0])


def flag_timeline(session_data: dict) -> list[tuple[float, str]]:
    """Every change of the flag state, for the player's timeline and tint."""
    points = _status_points(session_data)
    status_now = _series(points)
    for start, end in chequered_windows(session_data):
        points = [p for p in points if not start <= p[0] < end]
        points.append((start, "CHEQUERED"))
        if np.isfinite(end):
            # The next segment starts under whatever the track status says.
            points.append((end, status_now.at(end, "GREEN")))
    series = _series(points)
    return list(zip(series.t.tolist(), series.v, strict=True))


# --------------------------------------------------------------------------
# Building the series
# --------------------------------------------------------------------------


def _session_kind(info: dict) -> str:
    if is_race_session(info):
        return "race"
    if is_qualifying_session(info):
        return "qualifying"
    return "practice"


def _driver_codes(
    session_data: dict, table: pd.DataFrame, stream: pd.DataFrame, kind: str = "practice"
) -> list[str]:
    """Every driver, in the order the tower shows them before anyone is timed.

    The drivers table comes from ``session.results``, which FastF1 sorts by
    **finishing** position - using its order as the tie-break leaked the
    final classification into the opening moments of every replay. Races
    start in grid order (``GridPosition`` is known before lights out; a pit
    lane start, grid 0, goes last); every other session starts in car-number
    order, which says nothing about the result.
    """
    codes: list[str] = []
    drivers = session_data.get("drivers")
    if drivers is not None and not drivers.empty and "name_acronym" in drivers.columns:
        codes.extend(str(code) for code in drivers["name_acronym"].dropna())
    extras = [table["Driver"]]
    if "Driver" in stream.columns:
        extras.append(stream["Driver"])
    for extra in extras:
        for code in extra.dropna().astype(str):
            if code not in codes:
                codes.append(code)

    numbers: dict[str, float] = {}
    if drivers is not None and not drivers.empty and "name_acronym" in drivers.columns:
        for code, number in zip(
            drivers["name_acronym"].astype(str),
            pd.to_numeric(drivers.get("driver_number"), errors="coerce"),
            strict=False,
        ):
            if pd.notna(number):
                numbers[code] = float(number)
    grid: dict[str, float] = {}
    results = session_data.get("results")
    if (
        kind == "race"
        and results is not None
        and not results.empty
        and {"Abbreviation", "GridPosition"} <= set(results.columns)
    ):
        for code, slot in zip(
            results["Abbreviation"].astype(str),
            pd.to_numeric(results["GridPosition"], errors="coerce"),
            strict=False,
        ):
            if pd.notna(slot):
                grid[code] = float(slot) if slot > 0 else 999.0  # pit lane start

    def start_order(code: str) -> tuple:
        return (grid.get(code, 1000.0), numbers.get(code, 10_000.0), code)

    return sorted(codes, key=start_order)


def _sample_times(positions: pd.DataFrame | None) -> dict[str, np.ndarray]:
    if positions is None or positions.empty:
        return {}
    times = pd.to_numeric(positions["Time"], errors="coerce")
    return {
        str(code): np.sort(group.dropna().to_numpy(float))
        for code, group in times.groupby(positions["Driver"].astype(str))
    }


def _pit_windows(own: pd.DataFrame, garage_first: bool) -> list[tuple[float, float]]:
    """Intervals a driver spent in the pit lane: ``[entry, exit)``.

    An entry without a later exit stays open. In qualifying and practice a
    car starts in the garage, so the time before its first exit counts too.
    """
    entries = sorted(own["pit_in"].dropna().tolist())
    exits = sorted(own["pit_out"].dropna().tolist())
    windows: list[tuple[float, float]] = []
    if garage_first and exits and (not entries or exits[0] <= entries[0]):
        windows.append((-np.inf, exits[0]))
    for entry in entries:
        leave = next((e for e in exits if e > entry), np.inf)
        windows.append((entry, leave))
    return windows


def _in_windows(moment: float, windows: list[tuple[float, float]]) -> bool:
    return any(start <= moment < end for start, end in windows)


def _silences(samples: np.ndarray | None) -> list[tuple[float, float]]:
    """``[start, end)`` intervals in which the car had sent no position for
    :data:`OUT_AFTER_SECONDS` - each starts that long after a sample."""
    if samples is None or len(samples) == 0:
        return []
    gaps = np.diff(samples)
    long = gaps > OUT_AFTER_SECONDS
    starts = samples[:-1][long] + OUT_AFTER_SECONDS
    ends = samples[1:][long]
    windows = list(zip(starts.tolist(), ends.tolist(), strict=True))
    windows.append((float(samples[-1]) + OUT_AFTER_SECONDS, np.inf))
    return windows


def _status_series(
    kind: str,
    own: pd.DataFrame,
    samples: np.ndarray | None,
    finish: float | None,
    knocked_out: float | None,
) -> FieldSeries:
    pits = _pit_windows(own, garage_first=kind != "race")
    silences = _silences(samples)
    candidates = {BEFORE_EVERYTHING}
    for window in pits + silences:
        candidates.update(value for value in window if np.isfinite(value))
    for moment in (finish, knocked_out):
        if moment is not None:
            candidates.add(moment)

    def state(moment: float) -> str:
        if kind == "race":
            if finish is not None and moment >= finish:
                return FINISHED
            if _in_windows(moment, silences):
                return OUT
            if _in_windows(moment, pits):
                return IN_PIT
            return ON_TRACK
        if knocked_out is not None and moment >= knocked_out:
            return KNOCKED_OUT
        if _in_windows(moment, pits):
            return IN_PIT
        if _in_windows(moment, silences):
            return OUT
        return ON_TRACK

    return _series((moment, state(moment)) for moment in sorted(candidates))


class _BestTracker:
    """The quickest of a changing set of laps; ties go to the earlier lap."""

    def __init__(self) -> None:
        self.active: dict[int, tuple[float, float, object]] = {}
        self._best: tuple[float, float, object] | None = None
        self._stale = False

    def add(self, lap) -> None:
        key = (float(lap.lap_s), float(lap.end), lap)
        self.active[lap.row] = key
        if not self._stale and (self._best is None or key[:2] < self._best[:2]):
            self._best = key

    def remove(self, lap) -> None:
        if self.active.pop(lap.row, None) is not None and self._best is not None:
            self._stale = self._stale or self._best[2].row == lap.row

    def best(self):
        if self._stale:
            self._best = min(self.active.values(), key=lambda k: k[:2], default=None)
            self._stale = False
        return self._best[2] if self._best is not None else None


def _validity_events(laps: pd.DataFrame) -> list[tuple[float, int, object]]:
    """``(moment, order, lap)``: order 0 = becomes valid, 1 = deleted.

    A lap becomes valid when it ends (unless it is already deleted then) and
    again when reinstated; it stops being valid when the deletion is
    announced. Sorted, so a sweep sees the session as it unfolded.
    """
    found: list[tuple[float, int, object]] = []
    for lap in laps.itertuples():
        if not lap.base_valid or pd.isna(lap.end):
            continue
        if valid_at(lap, lap.end):
            found.append((float(lap.end), 0, lap))
        if np.isfinite(lap.del_at) and lap.del_at > lap.end:
            found.append((float(lap.del_at), 1, lap))
        if np.isfinite(lap.reinst_at):
            found.append((float(lap.reinst_at), 0, lap))
    found.sort(key=lambda item: (item[0], item[1], item[2].row))
    return found


def _running_best(laps: pd.DataFrame) -> list[tuple[float, object]]:
    """Change points of the quickest valid lap among ``laps``, as known."""
    tracker = _BestTracker()
    points: list[tuple[float, object]] = []
    for moment, order, lap in _validity_events(laps):
        if order == 0:
            tracker.add(lap)
        else:
            tracker.remove(lap)
        current = tracker.best()
        if not points or points[-1][1] is not current:
            if points and points[-1][0] == moment:
                points[-1] = (moment, current)
            else:
                points.append((moment, current))
    return points


def _lap_fields(kind: str, own: pd.DataFrame, total_laps: int | None) -> dict[str, FieldSeries]:
    """Fields that change only with the driver's own laps."""
    completed = own[own["end"].notna()].sort_values("end", kind="stable")
    lap_points: list[tuple[float, object]] = [(BEFORE_EVERYTHING, 1 if kind == "race" else 0)]
    last: list[tuple[float, object]] = []
    last_s: list[tuple[float, object]] = []
    deleted: list[tuple[float, object]] = []
    reason: list[tuple[float, object]] = []
    ordered = list(completed.itertuples())
    for count, lap in enumerate(ordered, start=1):
        lap_value = count + 1 if kind == "race" else count
        if kind == "race" and total_laps:
            lap_value = min(lap_value, int(total_laps))
        lap_points.append((lap.end, lap_value))
        last.append((lap.end, format_laptime(lap.lap_s)))
        last_s.append((lap.end, lap.lap_s))
        # The last-lap cell is marked deleted from the stewards' message
        # until the next lap replaces it (REPLAY-18, REPLAY-20).
        deleted.append((lap.end, False))
        reason.append((lap.end, None))
        following = ordered[count].end if count < len(ordered) else np.inf
        if np.isfinite(lap.del_at) or lap.del_at == -np.inf:
            start = max(lap.del_at, lap.end)
            if start < following:
                deleted.append((start, True))
                reason.append((start, lap.del_reason))
                if lap.reinst_at < following:
                    deleted.append((lap.reinst_at, False))
                    reason.append((lap.reinst_at, None))

    fields = {
        "lap": _series(lap_points),
        "last": _series(last),
        "last_s": _series(last_s),
        "last_deleted": _series(deleted),
        "last_deleted_reason": _series(reason),
    }
    best: list[tuple[float, object]] = []
    best_s: list[tuple[float, object]] = []
    for moment, lap in _running_best(completed):
        best.append((moment, format_laptime(lap.lap_s) if lap is not None else MISSING))
        best_s.append((moment, float(lap.lap_s) if lap is not None else None))
    if kind != "qualifying":  # qualifying bests are per segment
        fields["best"] = _series(best)
        fields["best_s"] = _series(best_s)

    # Sector cells show the latest time set in each sector: the lap in
    # progress overwrites the previous lap's value as it goes.
    for index in (1, 2, 3):
        value, stamp = own[f"s{index}"], own[f"s{index}_at"]
        fields[f"s{index}"] = _series(
            (at, seconds)
            for at, seconds in zip(stamp.tolist(), value.tolist(), strict=True)
            if pd.notna(at) and pd.notna(seconds)
        )

    # The tyre changes when the car leaves the pits, not when the out-lap
    # started at the pit-lane timing line.
    tyre, age, fresh, stint = [], [], [], []
    previous_pit_in = False
    for lap in own.itertuples():
        out_lap = previous_pit_in or pd.notna(lap.pit_out)
        moment = lap.pit_out if out_lap and pd.notna(lap.pit_out) else lap.start
        if pd.notna(moment):
            compound = str(lap.Compound).upper() if pd.notna(lap.Compound) else None
            tyre.append((moment, compound))
            age.append((moment, int(lap.TyreLife) if pd.notna(lap.TyreLife) else None))
            fresh.append((moment, bool(lap.FreshTyre) if pd.notna(lap.FreshTyre) else None))
            stint.append((moment, int(lap.Stint) if pd.notna(lap.Stint) else None))
        previous_pit_in = pd.notna(lap.pit_in)
    fields.update(tyre=_series(tyre), age=_series(age), new=_series(fresh), stint=_series(stint))

    if kind != "race":
        fields["flying"] = _flying_series(own)

    if kind == "race":
        entries = sorted(own["pit_in"].dropna().tolist())
        pit_points = [(BEFORE_EVERYTHING, 0), *((m, n) for n, m in enumerate(entries, start=1))]
        fields["pits"] = _series(pit_points)
    return fields


def _flying_series(own: pd.DataFrame) -> FieldSeries:
    """On a timed lap: from the start of a lap that is not an out-lap until
    it is completed, or until the car turns into the pit lane.

    Known at every moment from what has happened: the lap has started and
    the car is not in the pits; whether it ends as an in-lap is only
    decided when (and if) it enters the pit lane.
    """
    points: list[tuple[float, object]] = [(BEFORE_EVERYTHING, False)]
    previous_pit_in = False
    for lap in own.itertuples():
        out_lap = previous_pit_in or pd.notna(lap.pit_out)
        previous_pit_in = pd.notna(lap.pit_in)
        if out_lap or pd.isna(lap.start):
            continue
        stop = lap.pit_in if pd.notna(lap.pit_in) else lap.end
        if pd.notna(stop) and stop <= lap.start:
            continue
        points.append((lap.start, True))
        if pd.notna(stop):
            points.append((stop, False))
    return _series(points)


def _last_flags(table: pd.DataFrame) -> dict[str, FieldSeries]:
    """Purple (session best) / green (personal best) on each completed lap."""
    completed = table[table["end"].notna()].sort_values("end", kind="stable")
    points: dict[str, list] = {}
    next_end: dict[int, float] = {}
    for _, own in completed.groupby("Driver", sort=False):
        ends, rows = own["end"].tolist(), own["row"].tolist()
        next_end.update(zip(rows, [*ends[1:], np.inf], strict=True))
    for lap in completed.itertuples():
        points.setdefault(lap.Driver, []).append((lap.end, None))

    # Each flag is decided when the lap ends, against the laps valid then; a
    # deletion announced later clears it while it is still the last lap.
    session = _BestTracker()
    personal: dict[str, _BestTracker] = {}
    for moment, order, lap in _validity_events(completed):
        mine = personal.setdefault(lap.Driver, _BestTracker())
        if order == 1:
            session.remove(lap)
            mine.remove(lap)
            if moment < next_end.get(lap.row, np.inf):
                points[lap.Driver].append((moment, None))
            continue
        if moment == lap.end:
            best, own_best = session.best(), mine.best()
            flag = None
            if best is None or lap.lap_s < best.lap_s:
                flag = "sb"
            elif own_best is None or lap.lap_s < own_best.lap_s:
                flag = "pb"
            points[lap.Driver].append((lap.end, flag))
        session.add(lap)
        mine.add(lap)
    return {code: _series(values) for code, values in points.items()}


def _settled_positions(rows: pd.DataFrame) -> list[tuple[float, int]]:
    """Stream positions that held for :data:`POSITION_SETTLE_SECONDS`.

    A value becomes effective that long after it arrived, and only if no
    newer value arrived in between - so the decision at any moment uses
    only rows stamped before it.
    """
    times = rows["Time"].to_numpy(float)
    values = rows["Position"].tolist()
    points = []
    for index, (moment, value) in enumerate(zip(times, values, strict=True)):
        if value is None or pd.isna(value):
            continue
        following = times[index + 1] if index + 1 < len(times) else np.inf
        if following - moment >= POSITION_SETTLE_SECONDS:
            points.append((moment + POSITION_SETTLE_SECONDS, int(value)))
    return points


def _gap_display(position, seconds, laps_down, lapform) -> str:
    if position == 1:
        return LEADER
    if laps_down:
        return format_lap_gap(int(laps_down))
    if lapform or seconds is None:
        return MISSING
    return format_gap(seconds)


def _stream_display(position: FieldSeries, times, seconds, laps, raw) -> FieldSeries:
    """Display strings for a gap/interval column, kept in step with position."""
    lapform = [str(value or "").upper().startswith("LAP") for value in raw]
    cells = _series(zip(times, zip(seconds, laps, lapform, strict=True), strict=True))

    def merge(place, cell) -> str:
        seconds_now, laps_now, form = cell if isinstance(cell, tuple) else (None, None, False)
        return _gap_display(place, seconds_now, laps_now, form)

    return _combine(position, cells, merge)


def _race_from_stream(stream: pd.DataFrame, codes: list[str]) -> dict[str, dict[str, FieldSeries]]:
    fields: dict[str, dict[str, FieldSeries]] = {}
    for code in codes:
        own = stream[stream["Driver"] == code].sort_values("Time", kind="stable")
        if own.empty:
            continue
        times = own["Time"].to_numpy(float)

        def column(name: str, own=own) -> list:
            if name not in own.columns:
                return [None] * len(own)
            return [_clean(value) for value in own[name].tolist()]

        position = _series(_settled_positions(own))
        gap_s, gap_laps = column("GapSeconds"), column("GapLapsDown")
        int_s, int_laps = column("IntervalSeconds"), column("IntervalLapsDown")
        fields[code] = {
            "position": position,
            "gap": _stream_display(position, times, gap_s, gap_laps, column("GapToLeader")),
            "gap_s": _series(zip(times, gap_s, strict=True)),
            "laps_down": _series(zip(times, gap_laps, strict=True)),
            "interval": _stream_display(
                position, times, int_s, int_laps, column("IntervalToPositionAhead")
            ),
            "interval_s": _series(zip(times, int_s, strict=True)),
        }
    return fields


RACE_ORDER_FIELDS = ("position", "gap", "gap_s", "laps_down", "interval", "interval_s")


def _race_from_laps(table: pd.DataFrame, codes: list[str]) -> dict[str, dict[str, list]]:
    """Order and gaps at the timing lines, for sessions without a stream.

    Ranked by laps completed, then by who crossed the line first; the gap is
    the time since the leader crossed the same line (``+N LAP`` once lapped).
    """
    completed = table[table["end"].notna()].sort_values("end", kind="stable")
    laps_done = dict.fromkeys(codes, 0)
    last_cross: dict[str, float] = {}
    first_cross: dict[int, float] = {}
    crossings: dict[str, dict[int, float]] = {code: {} for code in codes}
    gap_at: dict[str, tuple] = {}
    points: dict[str, dict[str, list]] = {
        code: {name: [] for name in RACE_ORDER_FIELDS} for code in codes
    }
    rank_hint = {code: index for index, code in enumerate(codes)}

    for index, code in enumerate(codes, start=1):
        points[code]["position"].append((BEFORE_EVERYTHING, index))

    for lap in completed.itertuples():
        code = lap.Driver
        if code not in laps_done:
            continue
        laps_done[code] += 1
        number = laps_done[code]
        last_cross[code] = lap.end
        crossings[code][number] = lap.end
        first_cross.setdefault(number, lap.end)
        down = max(laps_done.values()) - number
        gap_at[code] = (None, down) if down > 0 else (lap.end - first_cross[number], 0)

        order = sorted(
            codes, key=lambda c: (-laps_done[c], last_cross.get(c, np.inf), rank_hint[c])
        )
        for place, other in enumerate(order, start=1):
            gap_s, laps_down = gap_at.get(other, (None, None))
            if place == 1:
                gap_text, interval_text, interval_s = LEADER, LEADER, None
                gap_s, laps_down = 0.0, 0
            else:
                ahead = order[place - 2]
                gap_text = _gap_display(place, gap_s, laps_down, False)
                own_laps, ahead_laps = laps_done[other], laps_done[ahead]
                if ahead_laps > own_laps:
                    interval_text, interval_s = format_lap_gap(ahead_laps - own_laps), None
                elif own_laps and own_laps in crossings[ahead]:
                    interval_s = crossings[other][own_laps] - crossings[ahead][own_laps]
                    interval_text = format_gap(interval_s)
                else:
                    interval_text, interval_s = MISSING, None
            values = (place, gap_text, gap_s, laps_down, interval_text, interval_s)
            for name, value in zip(RACE_ORDER_FIELDS, values, strict=True):
                points[other][name].append((lap.end, value))
    return points


TIMED_FIELDS = ("position", "gap", "gap_s", "interval", "interval_s", "best", "best_s", "partition")


def _timed_fields(
    kind: str,
    table: pd.DataFrame,
    codes: list[str],
    segment_starts: list[float],
    names: tuple[str, ...] = SEGMENT_NAMES,
) -> tuple[dict[str, dict[str, list]], dict[str, float]]:
    """Qualifying and practice: order by best valid lap, as it stood.

    Qualifying ranks by the best lap *in the running segment*; a driver
    knocked out stays below the cars still running, under the heading of
    the segment they went out in, once that segment has ended. Returns the
    per-field points and each knocked-out driver's KO time.
    """
    completed = table[table["end"].notna() & table["base_valid"]].sort_values("end", kind="stable")
    laps = list(completed.itertuples())
    # A deletion or reinstatement re-ranks the tower when it is announced.
    windows = [completed["del_at"], completed["reinst_at"]]
    announced = {float(t) for column in windows for t in column.tolist() if np.isfinite(t)}
    moments = sorted(set(completed["end"].tolist()) | set(segment_starts) | announced)
    cutoffs = qualifying_cutoffs(len(codes))
    points: dict[str, dict[str, list]] = {
        code: {name: [] for name in TIMED_FIELDS} for code in codes
    }
    knocked_out: dict[str, float] = {}
    rank_hint = {code: index for index, code in enumerate(codes)}

    def segment_of(moment: float) -> int:
        return bisect.bisect_right(segment_starts, moment) - 1

    def bests(until: float, segment: int | None, known: float) -> dict[str, tuple[float, float]]:
        """Best ``(time, when set)`` per driver among laps completed by
        ``until`` and still valid as far as was known at ``known``."""
        found: dict[str, tuple[float, float]] = {}
        for lap in laps:
            if lap.end > until:
                break
            if lap.del_at <= known < lap.reinst_at:
                continue
            if segment is not None and segment_of(lap.end) != segment:
                continue
            current = found.get(lap.Driver)
            if current is None or lap.lap_s < current[0]:
                found[lap.Driver] = (float(lap.lap_s), float(lap.end))
        return found

    def ranked(group: list[str], times: dict, fallback: dict[str, int]) -> list[str]:
        return sorted(
            group,
            key=lambda c: (
                c not in times,
                times.get(c, (np.inf, np.inf)),
                fallback.get(c, len(codes)),
                rank_hint[c],
            ),
        )

    Ranked = list[tuple[str, tuple[float, float] | None, str | None]]

    def qualifying_rows(moment: float) -> Ranked:
        """Running cars first, then each knocked-out group under its heading."""
        segment = segment_of(moment)
        active = list(codes)
        groups: list[tuple[str, list[str], dict]] = []
        previous_rank: dict[str, int] = {}
        for finished in range(max(segment, 0)):
            # Segment `finished` is over: the next one has started.
            times = bests(segment_starts[finished + 1], finished, moment)
            order = ranked(active, times, previous_rank)
            keep = cutoffs[finished] if finished < len(cutoffs) else len(order)
            survivors, out = order[:keep], order[keep:]
            groups.append((f"Eliminated in {names[finished]}", out, times))
            for code in out:
                # When it became known: a later deletion can knock a car out
                # of a segment that is already over (REPLAY-20).
                knocked_out.setdefault(code, moment)
            previous_rank = {code: index for index, code in enumerate(survivors)}
            active = survivors
        current = bests(moment, segment if segment >= 0 else None, moment)
        heading = names[segment] if groups and segment < len(names) else None
        rows: Ranked = [
            (code, current.get(code), heading if index == 0 else None)
            for index, code in enumerate(ranked(active, current, previous_rank))
        ]
        for label, members, times in reversed(groups):
            rows.extend(
                (code, times.get(code), label if index == 0 else None)
                for index, code in enumerate(ranked(members, times, {}))
            )
        return rows

    def record(moment: float, rows: Ranked) -> None:
        """Append every driver's fields as they stand at ``moment``."""
        leader = rows[0][1] if rows else None
        leader_best = leader[0] if leader else None
        previous_best: float | None = None
        for place, (code, best, heading) in enumerate(rows, start=1):
            value = best[0] if best else None
            gap_s = None if value is None or leader_best is None else value - leader_best
            interval_s: float | None = None
            if place == 1 and value is not None:
                gap_text, interval_text = LEADER, LEADER
            else:
                if value is not None and previous_best is not None:
                    interval_s = value - previous_best
                gap_text, interval_text = format_gap(gap_s), format_gap(interval_s)
            if value is not None:
                previous_best = value
            values = (
                place,
                gap_text,
                gap_s,
                interval_text,
                interval_s,
                format_laptime(value),
                value,
                heading,
            )
            for name, item in zip(TIMED_FIELDS, values, strict=True):
                points[code][name].append((moment, item))

    for moment in moments:
        if kind == "qualifying" and segment_starts:
            record(moment, qualifying_rows(moment))
        else:
            current = bests(moment, None, moment)
            record(moment, [(code, current.get(code), None) for code in ranked(codes, current, {})])
    return points, knocked_out


def tower_series(session_data: dict) -> TowerSeries:
    """Change-point series for every tower field of every driver.

    Built once per session; :meth:`TowerSeries.standings_at` then reads any
    moment with binary searches.
    """
    info = session_data.get("session_info") or {}
    kind = _session_kind(info)
    table = session_lap_table(session_data)
    stream = session_data.get("timing_stream")
    if stream is None or stream.empty or "Driver" not in stream.columns:
        stream = pd.DataFrame(columns=["Time", "Driver", "Position"])
    else:
        stream = stream.assign(
            Time=pd.to_numeric(stream["Time"], errors="coerce"),
            Driver=stream["Driver"].astype(str),
        )
    codes = _driver_codes(session_data, table, stream, kind)
    total_laps = info.get("total_laps")
    if not total_laps and kind == "race" and table["LapNumber"].notna().any():
        total_laps = int(table["LapNumber"].max())
    segment_starts = [float(s) for s in (info.get("segment_starts") or [])]
    names = segment_names(info)
    samples = _sample_times(session_data.get("positions"))

    fields: dict[str, dict[str, FieldSeries]] = {code: {} for code in codes}
    by_driver = {str(code): group for code, group in table.groupby("Driver", sort=False)}
    empty = table.iloc[0:0]
    for code in codes:
        fields[code].update(_lap_fields(kind, by_driver.get(code, empty), total_laps))
    for code, flags in _last_flags(table).items():
        if code in fields:
            fields[code]["last_flag"] = flags

    estimated = False
    knocked_out: dict[str, float] = {}
    if kind == "race":
        if not stream.empty and stream["Driver"].isin(codes).any():
            for code, values in _race_from_stream(stream, codes).items():
                fields[code].update(values)
        else:
            estimated = True
            for code, lap_points in _race_from_laps(table, codes).items():
                fields[code].update({name: _series(p) for name, p in lap_points.items()})
    else:
        timed, knocked_out = _timed_fields(kind, table, codes, segment_starts, names)
        for code, timed_points in timed.items():
            fields[code].update({name: _series(p) for name, p in timed_points.items()})

    flags_down = chequered_times(session_data) if kind == "race" else []
    chequered = flags_down[0] if flags_down else None
    for code in codes:
        own = by_driver.get(code, empty)
        finish = None
        if chequered is not None:
            after = own[own["end"].notna() & (own["end"] >= chequered)]["end"]
            finish = float(after.min()) if not after.empty else None
        fields[code]["status"] = _status_series(
            kind, own, samples.get(code), finish, knocked_out.get(code)
        )

    # Header lap counter: the leader's lap, in a race.
    leader_points: list[tuple[float, object]] = []
    if kind == "race":
        leader_points.append((BEFORE_EVERYTHING, 1))
        done = dict.fromkeys(codes, 0)
        for lap in table[table["end"].notna()].sort_values("end", kind="stable").itertuples():
            done[lap.Driver] = done.get(lap.Driver, 0) + 1
            lap_now = max(done.values()) + 1
            leader_points.append(
                (lap.end, min(lap_now, int(total_laps)) if total_laps else lap_now)
            )

    return TowerSeries(
        kind=kind,
        drivers=tuple(codes),
        fields=fields,
        leader_lap=_series(leader_points),
        total_laps=int(total_laps) if total_laps else None,
        segment_starts=tuple(segment_starts),
        chequered=chequered,
        estimated=estimated,
        order_hint={code: index for index, code in enumerate(codes)},
        segment_names=names,
        suspensions=tuple(red_flag_windows(session_data)),
    )


# --------------------------------------------------------------------------
# Snapshot and events
# --------------------------------------------------------------------------


def session_clock(session_data: dict) -> ReplayClock:
    """The stored replay clock, or one derived from the laps and positions."""
    info = session_data.get("session_info") or {}
    stored = ReplayClock.from_dict(info.get("replay_clock"))
    if stored is not None:
        return stored
    return replay_clock(
        session_data.get("laps"),
        session_data.get("positions"),
        info.get("session_type"),
        info.get("session_start"),
    )


# Columns of the lap in progress that are only known once it is complete.
HIDDEN_WHILE_RUNNING = (
    "LapTime",
    "Time",
    "Position",
    "SpeedI1",
    "SpeedI2",
    "SpeedFL",
    "SpeedST",
    "IsPersonalBest",
)
TYRE_COLUMNS = (("Compound", "tyre"), ("TyreLife", "age"), ("FreshTyre", "new"), ("Stint", "stint"))


def _set(frame: pd.DataFrame, index, column: str, value) -> None:
    """Write one cell, widening the column's dtype when it cannot hold it."""
    dtype = frame[column].dtype
    holds_missing = (
        pd.api.types.is_float_dtype(dtype)
        or pd.api.types.is_timedelta64_dtype(dtype)
        or pd.api.types.is_datetime64_any_dtype(dtype)
        or pd.api.types.is_object_dtype(dtype)
    )
    narrow = isinstance(dtype, pd.CategoricalDtype) or pd.api.types.is_bool_dtype(dtype)
    if (value is None and not holds_missing) or (value is not None and narrow):
        frame[column] = frame[column].astype(object)
    frame.loc[index, column] = value


def _snapshot_laps(
    laps: pd.DataFrame | None, table: pd.DataFrame, series: TowerSeries, moment: float
) -> pd.DataFrame:
    """Completed laps, plus one ``IsInProgress`` row per running driver."""
    if laps is None or laps.empty or table.empty:
        return laps if laps is not None else pd.DataFrame()
    work = laps.reset_index(drop=True)
    by_row = table.set_index("row")
    ends = by_row["end"].reindex(range(len(work))).to_numpy(float)
    starts = by_row["start"].reindex(range(len(work))).to_numpy(float)
    done = list(np.flatnonzero(ends <= moment))

    running = work[(starts <= moment) & ~(ends <= moment)]
    in_progress = []
    for code, group in running.groupby(running["Driver"].astype(str), sort=False):
        if series.value(code, "status", moment) not in (OUT, FINISHED, KNOCKED_OUT):
            in_progress.append((code, int(group.index[-1])))

    snapshot = work.loc[done + [index for _, index in in_progress]].copy()
    snapshot["IsInProgress"] = False
    # Deleted as far as the stewards had said at `moment` (REPLAY-20).
    del_at = by_row["del_at"].reindex(snapshot.index).to_numpy(float)
    reinst_at = by_row["reinst_at"].reindex(snapshot.index).to_numpy(float)
    deleted_now = (del_at <= moment) & (moment < reinst_at)
    if "Deleted" in snapshot.columns or deleted_now.any():
        snapshot["Deleted"] = deleted_now
    if "DeletedReason" in snapshot.columns:
        reasons = by_row["del_reason"].reindex(snapshot.index)
        snapshot["DeletedReason"] = reasons.where(deleted_now, "").fillna("").to_numpy()
    if not in_progress:
        return snapshot.reset_index(drop=True)

    # Collect every cell to blank or overwrite first, then write each column
    # once: a .loc write per cell cost ~90 ms for a full grid (REPLAY-03's
    # 150 ms snapshot budget).
    hidden_rows: dict[str, list[int]] = {}
    tyre_values: dict[str, dict[int, object]] = {}
    for code, index in in_progress:
        lap = by_row.loc[index]
        hidden = [column for column in HIDDEN_WHILE_RUNNING if column in snapshot.columns]
        for number in (1, 2, 3):
            if not lap[f"s{number}_at"] <= moment:
                hidden += [f"Sector{number}Time", f"Sector{number}SessionTime"]
        for column, stamp in (("PitInTime", "pit_in"), ("PitOutTime", "pit_out")):
            if not lap[stamp] <= moment:
                hidden.append(column)
        for column in hidden:
            if column in snapshot.columns:
                hidden_rows.setdefault(column, []).append(index)
        # The tyre as the model has it: unchanged until the car leaves the box.
        for column, name in TYRE_COLUMNS:
            value = series.value(code, name, moment)
            if column in snapshot.columns and value is not None:
                tyre_values.setdefault(column, {})[index] = value

    snapshot.loc[[index for _, index in in_progress], "IsInProgress"] = True
    for column, rows in hidden_rows.items():
        _set(snapshot, rows, column, None)
    for column, values in tyre_values.items():
        for value in set(map(repr, values.values())):
            rows = [i for i, v in values.items() if repr(v) == value]
            _set(snapshot, rows, column, values[rows[0]])
    return snapshot.reset_index(drop=True)


def _snapshot_stints(stints: pd.DataFrame | None, standings: pd.DataFrame) -> pd.DataFrame:
    """Stints begun by the current lap, the running one clipped to it."""
    if stints is None or stints.empty or "LapStart" not in stints.columns:
        return stints if stints is not None else pd.DataFrame()
    column = "Driver" if "Driver" in stints.columns else "DriverAcronym"
    if column not in stints.columns:
        return stints
    lap_now = dict(zip(standings["Driver"], standings["Lap"], strict=True))
    stint_now = dict(zip(standings["Driver"], standings["Stint"], strict=True))

    work = stints.copy()
    codes = work[column].astype(str)
    current = pd.to_numeric(codes.map(lap_now), errors="coerce").fillna(0).clip(lower=1)
    start = pd.to_numeric(work["LapStart"], errors="coerce")
    keep = start <= current
    if "Stint" in work.columns:
        running = pd.to_numeric(codes.map(stint_now), errors="coerce")
        keep &= running.isna() | (pd.to_numeric(work["Stint"], errors="coerce") <= running)
    work, current = work[keep].copy(), current[keep]
    if "LapEnd" in work.columns:
        end = np.minimum(pd.to_numeric(work["LapEnd"], errors="coerce"), current)
        work["LapEnd"] = pd.array(end.round(), dtype="Int64")
        if "LapCount" in work.columns:
            count = (end - pd.to_numeric(work["LapStart"], errors="coerce") + 1).clip(lower=0)
            work["LapCount"] = pd.array(count.round(), dtype="Int64")
    return work.reset_index(drop=True)


def snapshot_at(session_data: dict, moment: float, series: TowerSeries | None = None) -> dict:
    """A new unified session dict: the session exactly as it stood at ``moment``.

    Drawn by the ordinary renderers. ``results`` is empty (the final
    classification must not leak into the past) and ``standings`` carries
    the tower as it stood. ``session_info["replay_time"]`` tells the
    renderers they are drawing a moment, not a whole session.
    """
    series = series or tower_series(session_data)
    moment = float(moment)
    info = dict(session_data.get("session_info") or {})
    clock = session_clock(session_data)
    standings = series.standings_at(moment)
    table = session_lap_table(session_data)

    snapshot = dict(session_data)
    snapshot["laps"] = _snapshot_laps(session_data.get("laps"), table, series, moment)
    snapshot["standings"] = standings
    snapshot["results"] = pd.DataFrame()
    snapshot["stints"] = _snapshot_stints(session_data.get("stints"), standings)
    for key in ("race_control", "weather", "track_status", "timing_stream"):
        snapshot[key] = rows_until(session_data.get(key), moment)

    segment = series.segment_at(moment)
    info.update(
        replay_time=moment,
        current_lap=series.leader_lap.at(moment) if len(series.leader_lap) else None,
        total_laps=series.total_laps,
        elapsed=moment - clock.lights_out,
        segment=(
            series.segment_names[segment]
            if segment is not None and segment < len(series.segment_names)
            else None
        ),
        segment_elapsed=segment_elapsed(series, moment),
        segment_remaining=segment_remaining(series, moment),
        gaps_estimated=series.estimated,
    )
    snapshot["session_info"] = info
    return snapshot


def red_flag_windows(session_data: dict) -> list[tuple[float, float]]:
    """``[start, end)`` spans under a red flag (track status 5).

    The end is the next status change (``inf`` while still red). Callers
    only ever use the part of a span before the moment they draw.
    """
    windows: list[tuple[float, float]] = []
    start = None
    for moment, state in _status_points(session_data):
        if state == "RED" and start is None:
            start = moment
        elif state != "RED" and start is not None:
            windows.append((start, moment))
            start = None
    if start is not None:
        windows.append((start, np.inf))
    return windows


# Scheduled segment lengths in seconds: Q1-Q3 and SQ1-SQ3.
SEGMENT_SECONDS = {
    SEGMENT_NAMES: (18 * 60.0, 15 * 60.0, 12 * 60.0),
    SPRINT_SEGMENT_NAMES: (12 * 60.0, 10 * 60.0, 8 * 60.0),
}


def segment_elapsed(series: TowerSeries, moment: float) -> float | None:
    """Running time of the current qualifying segment at ``moment``.

    The segment clock stops under a red flag, so the time spent in
    red-flag spans inside the segment is not counted (REPLAY-24).
    """
    segment = series.segment_at(moment)
    if segment is None:
        return None
    start = series.segment_starts[segment]
    stopped = sum(
        max(0.0, min(end, moment) - max(begin, start)) for begin, end in series.suspensions
    )
    return max(moment - start - stopped, 0.0)


def segment_remaining(series: TowerSeries, moment: float) -> float | None:
    """Scheduled time left in the segment (18/15/12 min; sprint 12/10/8)."""
    segment = series.segment_at(moment)
    lengths = SEGMENT_SECONDS.get(series.segment_names)
    elapsed = segment_elapsed(series, moment)
    if segment is None or lengths is None or segment >= len(lengths) or elapsed is None:
        return None
    return max(lengths[segment] - elapsed, 0.0)


def race_clock_text(info: dict) -> tuple[str, str]:
    """Header clock label and value for a snapshot (``Race time``, ``0:41:07``)."""
    if info.get("segment") and info.get("segment_elapsed") is not None:
        return f"{info['segment']} time", format_clock(info["segment_elapsed"])
    label = "Race time" if is_race_session(info) else "Session time"
    return label, format_clock(info.get("elapsed") or 0.0)


TRACK_EVENTS = {
    "4": ("sc", "Safety car"),
    "6": ("vsc", "Virtual safety car"),
    "5": ("red", "Red flag"),
}


def events(session_data: dict, series: TowerSeries | None = None) -> list[tuple[float, str, str]]:
    """Moments worth jumping to: ``(time, kind, label)``, in time order.

    Kinds: ``sc``, ``vsc``, ``red`` (from track status), ``pit``, ``out``
    (a retirement), ``fastest`` (a new fastest lap) and ``flag`` (the
    chequered flag, from race control).
    """
    series = series or tower_series(session_data)
    found: list[tuple[float, str, str]] = []

    status = session_data.get("track_status")
    if status is not None and not status.empty and "Status" in status.columns:
        previous = None
        codes = status["Status"].astype(str).tolist()
        for moment, code in zip(_event_seconds(status).tolist(), codes, strict=True):
            if not np.isnan(moment) and code in TRACK_EVENTS and code != previous:
                kind, label = TRACK_EVENTS[code]
                found.append((float(moment), kind, label))
            previous = code

    table = session_lap_table(session_data)
    if series.kind == "race":
        found.extend(
            (float(lap.pit_in), "pit", f"Pit stop - {lap.Driver}")
            for lap in table[table["pit_in"].notna()].itertuples()
        )

    for code in series.drivers:
        states = series.fields[code]["status"]
        for moment, value in zip(states.t.tolist(), states.v, strict=True):
            if value == OUT:
                found.append((float(moment), "out", f"Retirement - {code}"))
                break

    # A new fastest lap as it happened: one deleted later still was, at the
    # time (REPLAY-20); the deletion itself is in race control.
    tracker = _BestTracker()
    for moment, order, lap in _validity_events(table[table["end"].notna()]):
        if order == 1:
            tracker.remove(lap)
            continue
        best = tracker.best()
        if moment == lap.end and (best is None or lap.lap_s < best.lap_s):
            label = f"Fastest lap - {lap.Driver} {format_laptime(lap.lap_s)}"
            found.append((float(lap.end), "fastest", label))
        tracker.add(lap)

    # One chequered flag per race, per qualifying segment, per practice.
    windows = chequered_windows(session_data)
    names = segment_names(session_data.get("session_info") or {})
    for start, _ in windows:
        label = "Chequered flag"
        segment = series.segment_at(start) if series.kind == "qualifying" else None
        if segment is not None and len(windows) > 1 and segment < len(names):
            label = f"Chequered flag - {names[segment]}"
        found.append((float(start), "flag", label))
    return sorted(found, key=lambda item: item[0])
