"""FastF1 Historical Data Adapter"""

import logging
import threading
import warnings
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

import fastf1
import fastf1.core
import numpy as np
import pandas as pd

from processing.replay import build_position_timeline
from processing.time_utils import parse_gap, seconds_series, to_seconds

logger = logging.getLogger(__name__)

# FastF1 position channels (X/Y/Z) are expressed in 1/10 meter.
POSITION_UNITS_PER_METRE = 10.0

# FastF1's timing stream (fastf1._api EMPTY_STREAM) and what get_timing_stream
# returns from it: acronyms instead of racing numbers, seconds instead of
# Timedeltas, and the gap strings parsed alongside the raw text.
RAW_STREAM_COLUMNS = ("Time", "Driver", "Position", "GapToLeader", "IntervalToPositionAhead")
TIMING_STREAM_COLUMNS = [
    "Time",
    "Driver",
    "Position",
    "GapToLeader",
    "IntervalToPositionAhead",
    "GapSeconds",
    "GapLapsDown",
    "IntervalSeconds",
    "IntervalLapsDown",
]
TRACK_STATUS_COLUMNS = ["Time", "Status", "Message"]

# Sessions run as knock-out segments, whose split times are worth keeping.
QUALIFYING_CODES = {"Q", "SQ", "SS"}
SECONDS_PER_DAY = 86_400.0


def _gap_text(value) -> str | None:
    """The stream's gap cell as text: None where the feed sent nothing.

    Qualifying streams carry float NaN here rather than strings.
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


# Telemetry scopes accepted by get_telemetry()/get_location().
SCOPE_FASTEST = "fastest"
SCOPE_SESSION = "session"

# A Grand Prix is limited to three hours including suspensions, so a race that
# started longer ago than this has certainly finished. The explicit unit avoids
# NumPy's deprecated generic timedelta (see CLAUDE.md "Time parsing").
RACE_MAX_DURATION = pd.Timedelta(3, unit="h")


def _utcnow() -> pd.Timestamp:
    """Current UTC time. Indirection so tests can freeze the clock."""
    return pd.Timestamp.now(tz="UTC")


# FastF1 schedule session names -> the identifiers `fastf1.get_session` takes.
# Sprint weekends have no FP2/FP3 but do have a sprint qualifying session,
# which F1 called "Sprint Shootout" in 2023 and "Sprint Qualifying" since.
SESSION_NAME_TO_CODE = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Sprint Qualifying": "SQ",
    "Sprint Shootout": "SQ",
    "Sprint": "S",
    "Qualifying": "Q",
    "Race": "R",
}

# FastF1 exposes at most five sessions per event.
MAX_SESSIONS_PER_EVENT = 5


# How long each session runs, for deciding whether one is on air now. The
# regulation lengths plus headroom: a race is capped at 3 h including
# suspensions, qualifying runs ~1 h with its three segments.
SESSION_DURATIONS = {
    "FP1": pd.Timedelta(90, unit="m"),
    "FP2": pd.Timedelta(90, unit="m"),
    "FP3": pd.Timedelta(90, unit="m"),
    "SQ": pd.Timedelta(60, unit="m"),
    "S": pd.Timedelta(90, unit="m"),
    "Q": pd.Timedelta(75, unit="m"),
    "R": RACE_MAX_DURATION,
}

# The feed is live a little before a session starts and stays interesting a
# little after it ends (parc ferme, post-session race control).
SESSION_LEAD_IN = pd.Timedelta(15, unit="m")
SESSION_RUN_OUT = pd.Timedelta(30, unit="m")


def live_session_now(schedule: pd.DataFrame, now: pd.Timestamp | None = None) -> dict | None:
    """The session currently on air, or None.

    A session is live when *now* falls in ``[start - 15 min, start + duration
    + 30 min]``. The old test - race day within +/-72 h, with no time of day -
    called an entire week "live", including days with no running at all.
    """
    if schedule is None or schedule.empty:
        return None
    moment = now if now is not None else _utcnow()

    for _, event in schedule.iterrows():
        for index in range(1, MAX_SESSIONS_PER_EVENT + 1):
            name = event.get(f"Session{index}")
            if name is None or pd.isna(name):
                continue
            code = SESSION_NAME_TO_CODE.get(str(name).strip())
            if code is None:
                continue
            start = pd.to_datetime(event.get(f"Session{index}DateUtc"), utc=True, errors="coerce")
            if pd.isna(start):
                continue
            window_start = start - SESSION_LEAD_IN
            window_end = start + SESSION_DURATIONS.get(code, pd.Timedelta(2, unit="h"))
            window_end += SESSION_RUN_OUT
            if window_start <= moment <= window_end:
                return {
                    "event": event.get("EventName"),
                    "session": code,
                    "session_name": str(name),
                    "start": start,
                    "ends_by": window_end,
                }
    return None


def session_codes_for_event(event: pd.Series) -> list[str]:
    """Session identifiers actually held at an event, in weekend order.

    Built from the schedule's ``Session1..Session5`` names rather than a fixed
    list, so sprint weekends offer SQ/S and conventional ones FP2/FP3. Only
    sessions that have already started are included - picking a session that
    has not run fails deep inside FastF1.
    """
    if event is None:
        return []

    now = _utcnow()
    codes: list[str] = []
    for i in range(1, MAX_SESSIONS_PER_EVENT + 1):
        name = event.get(f"Session{i}")
        if name is None or pd.isna(name):
            continue
        code = SESSION_NAME_TO_CODE.get(str(name).strip())
        if code is None:  # testing days and anything F1 renames later
            continue
        start = pd.to_datetime(event.get(f"Session{i}DateUtc"), utc=True, errors="coerce")
        if pd.notna(start) and start > now:
            continue
        codes.append(code)
    return codes


def latest_completed_event(schedule: pd.DataFrame) -> pd.Series | None:
    """The most recently *finished* round in a schedule, or None.

    Rows are ordered by the race session's start (``Session5DateUtc``,
    which FastF1 reports naive-UTC) rather than by position in the frame:
    schedules for several seasons are concatenated, so the last row is not
    the latest race. An event only counts once the race can have ended.
    """
    if schedule is None or schedule.empty:
        return None

    if "Session5DateUtc" in schedule.columns:
        race_start = pd.to_datetime(schedule["Session5DateUtc"], utc=True, errors="coerce")
    else:  # older schedule shapes only carry the event date
        race_start = pd.to_datetime(schedule.get("EventDate"), utc=True, errors="coerce")
    if race_start is None or race_start.isna().all():
        return schedule.iloc[-1]

    finished = schedule[race_start + RACE_MAX_DURATION < _utcnow()]
    if finished.empty:
        return None
    return finished.loc[race_start.loc[finished.index].idxmax()]


# FastF1's cache is process-wide: enabling it again builds a new HTTP cache
# session without closing the old one, possibly in the middle of another
# tab's load (HIST-10). It is enabled once per directory.
_cache_lock = threading.Lock()
_enabled_cache_dir: str | None = None

# Loaded sessions, newest last, shared by every tab (HIST-08): switching the
# telemetry scope or reopening a session re-derives frames instead of
# reloading. Three sessions bound the memory a long-lived process holds.
SESSION_CACHE_SIZE = 3
_session_cache: OrderedDict[tuple, fastf1.core.Session] = OrderedDict()
# Per-driver frames derived from a cached session, keyed (session id, driver,
# scope): the merge is the slow part of a load.
_frame_cache: dict[tuple, tuple[pd.DataFrame, pd.DataFrame]] = {}

# How long after its end a session may still be missing from F1's archive or
# be revised there (HIST-09). Such sessions are not kept in the runtime cache.
ARCHIVE_SETTLE_TIME = pd.Timedelta(3, unit="h")


class SessionNotArchivedError(RuntimeError):
    """The session has run but F1's archive does not have its timing yet."""


def enable_cache_once(cache_dir: str) -> None:
    global _enabled_cache_dir
    resolved = str(Path(cache_dir).resolve())
    with _cache_lock:
        if _enabled_cache_dir == resolved:
            return
        fastf1.Cache.enable_cache(cache_dir)
        _enabled_cache_dir = resolved


def clear_session_cache() -> None:
    """Drop the loaded sessions and their derived frames (tests, Settings)."""
    with _cache_lock:
        _session_cache.clear()
        _frame_cache.clear()


def _is_cached_session(session) -> bool:
    return any(session is held for held in _session_cache.values())


def _schedule_years(years) -> list[int]:
    if years is None:
        # The current season plus the previous one: hardcoding seasons left
        # "most recent completed race" a year behind once the year rolled.
        this_year = _utcnow().year
        return [this_year, this_year - 1]
    if isinstance(years, int):
        return [years]
    return list(years)


def first_session_end(event: pd.Series) -> pd.Timestamp | None:
    """When an event's first session can have ended, or None if unknown."""
    for index in range(1, MAX_SESSIONS_PER_EVENT + 1):
        name = event.get(f"Session{index}")
        if name is None or pd.isna(name):
            continue
        start = pd.to_datetime(event.get(f"Session{index}DateUtc"), utc=True, errors="coerce")
        if pd.isna(start):
            continue
        code = SESSION_NAME_TO_CODE.get(str(name).strip())
        return start + SESSION_DURATIONS.get(code, pd.Timedelta(2, unit="h"))
    return None


class FastF1Adapter:
    """Loads historical F1 sessions with local caching."""

    def __init__(self, cache_dir: str | None = None):
        if cache_dir is None:
            from config import config

            cache_dir = config.fastf1_cache_dir
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Pass the original string so callers can round-trip the exact path
        enable_cache_once(cache_dir)

    def get_schedule(self, years: list[int] | int | None = None) -> pd.DataFrame:
        """Every championship event of the given seasons, run or not.

        Pre-season testing events are excluded: they appear twice per season,
        carry no Race/Qualifying session, and would break session loading if
        picked from the Grand Prix dropdown. The live check reads this
        unfiltered schedule: the date-filtered one hid every event until its
        last day, so no practice, sprint or qualifying was ever "live"
        (LIVE-28).
        """
        all_schedules = []
        for year in _schedule_years(years):
            schedule = fastf1.get_event_schedule(year)
            # Add Year column for consistency
            schedule["Year"] = year
            all_schedules.append(schedule)
        combined = pd.concat(all_schedules, ignore_index=True) if all_schedules else pd.DataFrame()

        # Drop testing events (EventFormat == 'testing', RoundNumber 0).
        if not combined.empty and "EventFormat" in combined.columns:
            combined = combined[combined["EventFormat"].astype(str) != "testing"]
        elif not combined.empty and "RoundNumber" in combined.columns:
            combined = combined[pd.to_numeric(combined["RoundNumber"], errors="coerce") > 0]
        return combined

    def get_available_sessions(self, years: list[int] | None = None) -> pd.DataFrame:
        """Returns DataFrame of the race weekends that have started running.

        An event is offered once its first session has ended (CACHE-03): the
        old filter on ``EventDate`` (the last session's date at 00:00) hid a
        weekend's practice, sprint and qualifying until race day. Which of an
        event's sessions can be picked is decided per session by
        :func:`session_codes_for_event`.
        """
        combined = self.get_schedule(years)
        if combined.empty:
            return combined
        now = _utcnow()
        ended = [first_session_end(event) for _, event in combined.iterrows()]
        if any(end is not None for end in ended):
            keep = [end is not None and end <= now for end in ended]
            return combined[keep]
        # Older schedule shapes only carry the event date.
        if "EventDate" in combined.columns:
            event_dates = pd.to_datetime(combined["EventDate"], utc=True)
            return combined[event_dates < now]
        return combined

    def load_session(self, year: int, gp: str, session_type: str) -> fastf1.core.Session:
        """Load a session, reusing one already loaded in this process (HIST-08).

        A session that has run but is not in F1's archive yet (usually for an
        hour or two afterwards) loads without laps; FastF1 then raises
        ``DataNotLoadedError`` on the first ``session.laps``. That is turned
        into :class:`SessionNotArchivedError` with a message that says so
        (HIST-09).
        """
        key = (int(year), str(gp).strip().lower(), str(session_type).upper(), str(self.cache_dir))
        with _cache_lock:
            cached = _session_cache.get(key)
            if cached is not None:
                _session_cache.move_to_end(key)
                return cached
        session = fastf1.get_session(year, gp, session_type)
        session.load(telemetry=True, laps=True, weather=True, messages=True)
        self._require_laps(session, year, gp, session_type)
        with _cache_lock:
            _session_cache[key] = session
            while len(_session_cache) > SESSION_CACHE_SIZE:
                _, dropped = _session_cache.popitem(last=False)
                for frame_key in [k for k in _frame_cache if k[0] == id(dropped)]:
                    _frame_cache.pop(frame_key, None)
        return session

    @staticmethod
    def _require_laps(session, year, gp, session_type) -> None:
        from fastf1.exceptions import DataNotLoadedError

        try:
            laps = session.laps
        except DataNotLoadedError as exc:
            raise SessionNotArchivedError(
                f"{year} {gp} {session_type} is not in F1's archive yet "
                "(usually 1-2 h after the session). Try again later."
            ) from exc
        if isinstance(laps, pd.DataFrame) and laps.empty:
            raise SessionNotArchivedError(
                f"{year} {gp} {session_type} has no lap timing in F1's archive yet "
                "(usually 1-2 h after the session). Try again later."
            )

    @staticmethod
    def ended_recently(session, now: pd.Timestamp | None = None) -> bool:
        """Whether the session ended less than ``ARCHIVE_SETTLE_TIME`` ago.

        Such a session may still be partial in the archive, so the app does
        not keep it in the runtime cache (HIST-09).
        """
        start = pd.to_datetime(getattr(session, "date", None), utc=True, errors="coerce")
        if not isinstance(start, pd.Timestamp) or pd.isna(start):
            return False
        name = str(getattr(session, "name", "") or "")
        code = SESSION_NAME_TO_CODE.get(name.strip())
        end = start + SESSION_DURATIONS.get(code, pd.Timedelta(2, unit="h"))
        return bool((now if now is not None else _utcnow()) - end < ARCHIVE_SETTLE_TIME)

    def _pick_laps(self, session: fastf1.core.Session, driver: str, scope: str):
        """Select the laps a telemetry request should cover.

        ``scope='fastest'`` returns the driver's fastest :class:`~fastf1.core.Lap`
        so Distance is lap-relative (0 -> lap length) and drivers are directly
        comparable at the same track position. ``scope='session'`` returns every
        lap, whose Distance accumulates across the whole session.

        Returns None when the driver has no usable laps.
        """
        laps = session.laps.pick_drivers(driver)
        if laps is None or laps.empty:
            return None
        if scope == SCOPE_FASTEST:
            # pick_fastest() returns None when no lap has a valid lap time.
            fastest = laps.pick_fastest()
            if fastest is None:
                return None
            return fastest
        return laps

    @staticmethod
    def _merged_telemetry(lap_selection) -> pd.DataFrame:
        """Car + position telemetry with a Distance channel.

        FastF1's ``get_telemetry()`` merges car data and position data and adds
        Distance itself. ``get_pos_data()`` must NOT be distance-integrated
        directly: it carries no Speed channel, so ``add_distance()`` raises
        ``ValueError: Telemetry does not contain required channels``.
        """
        telemetry = lap_selection.get_telemetry()
        if telemetry is None or len(telemetry) == 0:
            return pd.DataFrame()
        if "Distance" not in telemetry.columns and hasattr(telemetry, "add_distance"):
            telemetry = telemetry.add_distance()
        return telemetry

    def get_driver_frames(
        self,
        session: fastf1.core.Session,
        driver: str,
        scope: str = SCOPE_FASTEST,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Telemetry channels and GPS trail for one driver from a single merge.

        ``get_telemetry()`` is the expensive call (car/position merge plus
        interpolation), and it already yields both the channel data and the
        X/Y/Z trail - so a session load does it once per driver rather than
        twice.
        """
        key = (id(session), driver, scope)
        with _cache_lock:
            cached = _frame_cache.get(key) if _is_cached_session(session) else None
        if cached is not None:
            return cached
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame(), pd.DataFrame()
        merged = self._merged_telemetry(selection)
        if merged.empty:
            return pd.DataFrame(), pd.DataFrame()
        frames = self._telemetry_columns(merged), self._location_columns(merged)
        with _cache_lock:
            # Only frames of a session held in the session cache are kept:
            # id() is unique only while that session object is alive.
            if _is_cached_session(session):
                _frame_cache[key] = frames
        return frames

    @staticmethod
    def _telemetry_columns(telemetry: pd.DataFrame) -> pd.DataFrame:
        """Project merged telemetry onto the unified channel schema."""
        if "Distance" not in telemetry.columns:
            return pd.DataFrame()
        # 'Time' is kept: downstream processing and metrics rely on it.
        cols = ["Distance", "Time", "Speed", "Throttle", "Brake", "RPM", "nGear", "DRS"]
        available = [c for c in cols if c in telemetry.columns]
        return pd.DataFrame(telemetry[available]).reset_index(drop=True)

    @classmethod
    def _location_columns(cls, telemetry: pd.DataFrame) -> pd.DataFrame:
        """Project merged telemetry onto the unified location schema."""
        if not {"X", "Y"}.issubset(telemetry.columns):
            return pd.DataFrame()
        cols = [c for c in ["Distance", "X", "Y", "Z"] if c in telemetry.columns]
        return pd.DataFrame(telemetry[cols]).reset_index(drop=True)

    def get_telemetry(
        self, session: fastf1.core.Session, driver: str, scope: str = SCOPE_FASTEST
    ) -> pd.DataFrame:
        """Get car telemetry (speed, throttle, brake, rpm, gear, drs)."""
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame()
        merged = self._merged_telemetry(selection)
        if merged.empty:
            return pd.DataFrame()
        return self._telemetry_columns(merged)

    def get_location(
        self, session: fastf1.core.Session, driver: str, scope: str = SCOPE_FASTEST
    ) -> pd.DataFrame:
        """Get GPS location data (X/Y/Z plus travelled Distance in metres)."""
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame()

        merged = self._merged_telemetry(selection)
        if not merged.empty and {"X", "Y"}.issubset(merged.columns):
            return self._location_columns(merged)

        # Fallback: raw position data only. It has no Speed channel, so
        # distance comes from the GPS arc length instead of integration.
        pos_data = selection.get_pos_data()
        if pos_data is None or len(pos_data) == 0:
            return pd.DataFrame()
        pos_data = pd.DataFrame(pos_data)
        if "Distance" not in pos_data.columns:
            distance = self.distance_from_positions(pos_data)
            if distance is not None:
                pos_data = pos_data.assign(Distance=distance)
        cols = [c for c in ["Distance", "X", "Y", "Z"] if c in pos_data.columns]
        return pos_data[cols].reset_index(drop=True)

    @staticmethod
    def distance_from_positions(pos_data: pd.DataFrame) -> np.ndarray | None:
        """Cumulative travelled distance (metres) along an X/Y GPS trail.

        FastF1 position coordinates are in 1/10 m, so the raw arc length is
        divided by :data:`POSITION_UNITS_PER_METRE`.
        """
        if pos_data is None or not {"X", "Y"}.issubset(pos_data.columns):
            return None
        xy = pos_data[["X", "Y"]].to_numpy(dtype=float)
        if len(xy) < 2 or np.isnan(xy).all():
            return None
        steps = np.hypot(*np.diff(xy, axis=0).T)
        steps = np.nan_to_num(steps, nan=0.0)
        return np.concatenate([[0.0], np.cumsum(steps)]) / POSITION_UNITS_PER_METRE

    # Columns of session.results the dashboard classifies with. Q1/Q2/Q3 are
    # only present for qualifying sessions.
    RESULT_COLUMNS = (
        "Abbreviation",
        "DriverNumber",
        "TeamName",
        "Position",
        "ClassifiedPosition",
        "GridPosition",
        "Status",
        "Time",
        "Points",
        "Q1",
        "Q2",
        "Q3",
    )

    def get_results(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Official classification for the session.

        A race is ordered by finishing position, not by best lap, and only
        ``session.results`` carries that (plus Status for DNF/DSQ and the
        Q1/Q2/Q3 segment times). Missing or unloadable results degrade to an
        empty frame - the tower then falls back to lap data.
        """
        try:
            results = session.results
        except Exception as exc:
            logger.warning("No results for this session: %s", exc)
            return pd.DataFrame()
        if results is None or len(results) == 0:
            return pd.DataFrame()
        present = [c for c in self.RESULT_COLUMNS if c in results.columns]
        return pd.DataFrame(results[present]).reset_index(drop=True)

    def get_position_timeline(
        self,
        session: fastf1.core.Session,
        drivers: Sequence[str],
        include_pre_race: bool = False,
    ) -> pd.DataFrame:
        """Every driver's position across the whole session, on one clock.

        Uses ``get_pos_data()`` directly - position data alone, no car-data
        merge - because this needs the *whole* session rather than one lap,
        and the merge is what makes telemetry loading expensive. Note that
        this data must never be `.add_distance()`d: it carries no Speed
        channel (see CLAUDE.md).

        ``Laps.get_pos_data()`` starts at the first ``LapStartTime``. In
        qualifying and practice that can be NaT for a driver's first lap,
        which would drop their first out-lap, so those drivers are read from
        the raw ``session.pos_data`` from the session start instead.
        ``include_pre_race`` does the same for every driver, adding the grid
        and formation lap; it is off by default to keep the payload small.
        """
        frames = {}
        for driver in drivers:
            try:
                laps = session.laps.pick_drivers(driver)
                if laps is None or laps.empty:
                    continue
                raw = self._raw_positions(session, laps, include_pre_race)
                positions = raw if raw is not None else laps.get_pos_data()
            except Exception as exc:
                logger.warning("No position data for %s: %s", driver, exc)
                continue
            if positions is not None and len(positions) > 0:
                frames[driver] = pd.DataFrame(positions)

        return build_position_timeline(frames)

    @staticmethod
    def _raw_positions(session, laps, include_pre_race: bool) -> pd.DataFrame | None:
        """Raw position samples from the session start to the driver's last lap.

        None when the lap-sliced data is complete (the first lap has a start
        time) and the pre-race was not asked for. Position data only: never
        distance-integrate it (no Speed channel).
        """
        ordered = laps.sort_values("LapNumber") if "LapNumber" in laps.columns else laps
        has_start = "LapStartTime" in ordered.columns
        first_start = ordered["LapStartTime"].iloc[0] if has_start else pd.NaT
        if not include_pre_race and pd.notna(first_start):
            return None

        if "DriverNumber" not in ordered.columns:
            return None
        number = str(ordered["DriverNumber"].iloc[0])
        pos_data = getattr(session, "pos_data", None) or {}
        if number not in pos_data:
            return None
        raw = pd.DataFrame(pos_data[number])
        if "SessionTime" not in raw.columns:
            return None

        times = raw["SessionTime"]
        keep = times.notna()
        if "Time" in ordered.columns and ordered["Time"].notna().any():
            keep &= times <= ordered["Time"].max()
        begin = getattr(session, "session_start_time", None)
        if not include_pre_race and begin is not None and pd.notna(begin):
            keep &= times >= begin
        return raw[keep]

    def get_laps(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Get lap timing data with a boolean pit-out flag.

        FastF1 exposes pit activity as ``PitOutTime``/``PitInTime`` timestamps,
        not as a boolean column, so ``IsPitOutLap`` is derived here - the lap
        chart marks those laps.
        """
        laps = session.laps.copy()
        # 'Position' drives the lap-by-lap position chart; 'Compound'/'Stint'
        # let the lap view be read alongside tyre choice without a second query.
        cols = [
            "Driver",
            "LapNumber",
            "LapTime",
            # Session time at the lap's end: the header's session duration and
            # the race-gap fallback both measure from it.
            "Time",
            "LapStartTime",
            "Sector1Time",
            "Sector2Time",
            "Sector3Time",
            # When each sector was completed, on the session clock: the replay
            # reveals a sector time only once it has been set (REPLAY-03).
            "Sector1SessionTime",
            "Sector2SessionTime",
            "Sector3SessionTime",
            "Position",
            "Compound",
            "Stint",
            # Tyre age and whether the set was new: a stint's length is not
            # the tyre's age when a driver starts on a scrubbed set.
            "TyreLife",
            "FreshTyre",
            # Speed-trap readings feed the timing tower's Speed column.
            "SpeedI1",
            "SpeedI2",
            "SpeedFL",
            "SpeedST",
            # Pit timestamps drive the IN PIT status badge.
            "PitInTime",
            "PitOutTime",
            # Lap validity: a deleted or inaccurately timed lap must not set
            # a sector best (see processing.timing._valid_laps).
            "Deleted",
            "DeletedReason",
            "IsAccurate",
            # FastF1's per-lap track status string ("1", "24", "4" ...): pace
            # analysis must leave out SC/VSC laps (FEAT-03).
            "TrackStatus",
        ]
        available = [c for c in cols if c in laps.columns]
        result = pd.DataFrame(laps[available]).reset_index(drop=True)

        result["IsPitOutLap"] = self._pit_out_flags(laps).to_numpy()
        return result

    @staticmethod
    def get_race_control(session: fastf1.core.Session) -> pd.DataFrame:
        """Race control messages: flags, safety cars, incidents, penalties.

        FastF1 stamps these with a wall-clock ``Time``; ``SessionTime`` puts
        them on the clock laps, positions and weather share (``Time`` minus
        ``session.t0_date``), which is what the replay needs to know whether
        a message had been issued by a given moment.
        """
        try:
            messages = session.race_control_messages
        except Exception as exc:
            logger.warning("Race control messages unavailable: %s", exc)
            return pd.DataFrame()
        if messages is None or len(messages) == 0:
            return pd.DataFrame()
        cols = ["Time", "Lap", "Category", "Flag", "Scope", "Sector", "Message"]
        available = [c for c in cols if c in messages.columns]
        result = pd.DataFrame(messages[available]).reset_index(drop=True)
        if "Time" in result.columns and pd.api.types.is_datetime64_any_dtype(result["Time"]):
            try:
                result["SessionTime"] = result["Time"] - session.t0_date
            except Exception as exc:
                logger.warning(
                    "Race control messages cannot be placed on the session clock: %s", exc
                )
        return result

    @staticmethod
    def get_timing_stream(session: fastf1.core.Session) -> pd.DataFrame:
        """The timing screen over time: position, gap and interval per update.

        FastF1 parses this stream while loading laps and then discards it
        (``Session._load_laps_data`` keeps only the lap table). It is read
        back through the private ``fastf1._api._extended_timing_data``, which
        hits FastF1's own cache (``_extended_timing_data.ff1pkl``), so a
        session that is already loaded costs no download. Being private, any
        failure degrades to an empty frame - the replay then estimates gaps
        at the timing lines instead.
        """
        try:
            from fastf1 import _api as ff1_api  # private; columns pinned by tests

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _, stream, _ = ff1_api._extended_timing_data(session.api_path)
        except Exception as exc:
            logger.warning("Timing stream unavailable: %s", exc)
            return pd.DataFrame(columns=TIMING_STREAM_COLUMNS)
        if stream is None or len(stream) == 0:
            return pd.DataFrame(columns=TIMING_STREAM_COLUMNS)

        missing = set(RAW_STREAM_COLUMNS) - set(stream.columns)
        if missing:
            logger.warning("Timing stream lacks %s; FastF1 may have changed", sorted(missing))
            return pd.DataFrame(columns=TIMING_STREAM_COLUMNS)

        try:
            results = session.results
            acronyms = dict(
                zip(
                    results["DriverNumber"].astype(str),
                    results["Abbreviation"].astype(str),
                    strict=False,
                )
            )
        except Exception as exc:
            logger.warning("Timing stream cannot be matched to drivers: %s", exc)
            return pd.DataFrame(columns=TIMING_STREAM_COLUMNS)

        frame = pd.DataFrame(
            {
                "Time": seconds_series(stream["Time"].reset_index(drop=True)),
                "Driver": stream["Driver"].astype(str).map(acronyms).reset_index(drop=True),
                "Position": pd.to_numeric(stream["Position"], errors="coerce")
                .astype("Int64")
                .reset_index(drop=True),
                "GapToLeader": [_gap_text(v) for v in stream["GapToLeader"]],
                "IntervalToPositionAhead": [
                    _gap_text(v) for v in stream["IntervalToPositionAhead"]
                ],
            }
        )
        frame = frame.dropna(subset=["Time", "Driver"])
        gap = [parse_gap(value) for value in frame["GapToLeader"]]
        interval = [parse_gap(value) for value in frame["IntervalToPositionAhead"]]
        frame["GapSeconds"] = pd.array([g[0] for g in gap], dtype="Float64")
        frame["GapLapsDown"] = pd.array([g[1] for g in gap], dtype="Int64")
        frame["IntervalSeconds"] = pd.array([i[0] for i in interval], dtype="Float64")
        frame["IntervalLapsDown"] = pd.array([i[1] for i in interval], dtype="Int64")
        return frame.sort_values("Time", kind="stable").reset_index(drop=True)[
            TIMING_STREAM_COLUMNS
        ]

    @staticmethod
    def get_track_status(session: fastf1.core.Session) -> pd.DataFrame:
        """Track state changes (green, yellow, SC, red, VSC) on the session clock."""
        try:
            status = session.track_status
        except Exception as exc:
            logger.warning("Track status unavailable: %s", exc)
            return pd.DataFrame(columns=TRACK_STATUS_COLUMNS)
        if status is None or len(status) == 0 or "Time" not in status.columns:
            return pd.DataFrame(columns=TRACK_STATUS_COLUMNS)
        return pd.DataFrame(
            {
                "Time": seconds_series(status["Time"].reset_index(drop=True)),
                "Status": status["Status"].astype(str).reset_index(drop=True),
                "Message": status.get("Message", pd.Series([""] * len(status)))
                .astype(str)
                .reset_index(drop=True),
            }
        )

    @staticmethod
    def get_segment_starts(session: fastf1.core.Session, session_type: str) -> list[float]:
        """Start of Q1/Q2/Q3 (or SQ1..SQ3) in session seconds; ``[]`` otherwise.

        FastF1 keeps the split points in the private ``_session_split_times``:
        element 0 is always 0 (not the Q1 start) and a race carries
        ``[0, 1 day, 1 day]``. So element 0 is replaced with the session
        start and anything a day or more out is dropped.
        """
        if str(session_type).upper() not in QUALIFYING_CODES:
            return []
        splits = getattr(session, "_session_split_times", None)
        if not splits:
            return []
        starts = []
        for index, split in enumerate(splits):
            if index == 0:
                split = getattr(session, "session_start_time", None)
            seconds = to_seconds(split)
            if seconds is None or pd.isna(seconds) or seconds >= SECONDS_PER_DAY:
                continue
            starts.append(float(seconds))
        return starts

    @staticmethod
    def session_start(session: fastf1.core.Session) -> float | None:
        """When the session went green ("Started"), in session seconds."""
        try:
            return to_seconds(session.session_start_time)
        except Exception as exc:
            logger.warning("No session start time: %s", exc)
            return None

    @staticmethod
    def total_laps(session: fastf1.core.Session) -> int | None:
        """Scheduled race distance in laps, or None outside races."""
        try:
            laps = session.total_laps
        except Exception as exc:
            logger.debug("No scheduled lap count: %s", exc)
            return None
        if laps is None or pd.isna(laps):
            return None
        return int(laps)

    @staticmethod
    def get_circuit_info(session: fastf1.core.Session) -> dict:
        """Corner markers and track rotation for the map.

        FastF1 sources this from the MultiViewer API, so it needs network
        access and is absent for some circuits; the map degrades to an
        unlabelled outline when this returns an empty dict.
        """
        try:
            info = session.get_circuit_info()
        except Exception as exc:
            logger.warning("No circuit info: the map loses its corner markers (%s)", exc)
            return {}
        if info is None:
            return {}
        corners = getattr(info, "corners", None)
        has_corners = corners is not None and len(corners) > 0
        return {
            "corners": (
                pd.DataFrame(corners).reset_index(drop=True) if has_corners else pd.DataFrame()
            ),
            "rotation": float(getattr(info, "rotation", 0.0) or 0.0),
        }

    @staticmethod
    def compound_colors(session: fastf1.core.Session) -> dict:
        """Official tyre compound colours for the session's season.

        FastF1 tracks the real branding per season, so this beats a hardcoded
        table (and covers compounds a given year may add or drop).
        """
        try:
            import fastf1.plotting

            mapping = fastf1.plotting.get_compound_mapping(session)
        except Exception as exc:
            logger.warning("No compound colours for this season, using defaults: %s", exc)
            return {}
        return {str(k).upper(): v for k, v in (mapping or {}).items()}

    @staticmethod
    def _pit_out_flags(laps: pd.DataFrame) -> pd.Series:
        """Boolean pit-out flag per lap, from whichever column the source has."""
        for col in ("IsPitOutLap", "PitOutLap"):
            if col in laps.columns:
                # .eq(True), not fillna(False).astype(bool): the silent
                # object-to-bool downcast is gone in pandas 3 (CORE-02).
                return laps[col].eq(True)
        if "PitOutTime" in laps.columns:
            return laps["PitOutTime"].notna()
        return pd.Series(False, index=laps.index, dtype=bool)

    def get_stints(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Get tyre stint data derived from laps."""
        laps = session.laps
        if "Stint" not in laps.columns or "Compound" not in laps.columns:
            return pd.DataFrame()

        required = {"Driver", "Stint", "Compound"}
        if not required.issubset(set(laps.columns)):
            return pd.DataFrame()

        if {"LapStart", "LapEnd"}.issubset(set(laps.columns)):
            # Stint boundaries already present (e.g. pre-aggregated data)
            stints = laps[["Driver", "Stint", "Compound", "LapStart", "LapEnd"]].drop_duplicates()
        elif "LapNumber" in laps.columns:
            # Derive stint boundaries from per-lap data
            stints = (
                laps.groupby(["Driver", "Stint"])
                .agg(
                    LapStart=("LapNumber", "min"),
                    LapEnd=("LapNumber", "max"),
                    Compound=("Compound", "first"),
                )
                .reset_index()
            )
        else:
            return pd.DataFrame()

        stints = pd.DataFrame(stints).reset_index(drop=True)
        stints["LapCount"] = stints["LapEnd"] - stints["LapStart"] + 1
        # Lap/stint counters are whole numbers; groupby leaves them as floats
        # (NaN-capable), which would render as "Laps: 12.0" in the chart.
        for col in ("Stint", "LapStart", "LapEnd", "LapCount"):
            if col in stints.columns:
                stints[col] = pd.to_numeric(stints[col], errors="coerce").astype("Int64")
        return stints
