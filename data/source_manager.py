"""Data Source Manager - Unified interface with automatic fallback"""

import contextlib
import json
import logging
import pickle
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import config
from data.fastf1_adapter import (
    SCOPE_FASTEST,
    FastF1Adapter,
    latest_completed_event,
    live_session_now,
)
from data.jolpica_adapter import JolpicaAdapter
from data.live_adapter import LiveDataProcessor, SignalRLiveAdapter
from data.live_service import get_live_adapter
from data.live_state import as_list
from processing.replay import ReplayClock, replay_clock
from processing.time_utils import to_seconds
from processing.timing import is_race_session

logger = logging.getLogger(__name__)


def extrapolated_remaining(clock: dict, now: pd.Timestamp | None = None) -> str | None:
    """Time left on the session clock, as ``H:MM:SS``.

    ``ExtrapolatedClock`` gives ``Remaining`` at ``Utc``; while
    ``Extrapolating`` is true the clock is running, so the time elapsed since
    ``Utc`` is subtracted - the feed only sends a new value when the clock
    starts, stops or is corrected.
    """
    remaining = to_seconds(clock.get("Remaining")) if clock else None
    if remaining is None:
        return None
    if clock.get("Extrapolating") in (True, "true", "True"):
        stamp = pd.to_datetime(clock.get("Utc"), utc=True, errors="coerce")
        if pd.notna(stamp):
            current = now if now is not None else pd.Timestamp.now(tz="UTC")
            remaining -= max((current - stamp).total_seconds(), 0.0)
    total = max(round(remaining), 0)
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _app_version() -> str:
    try:
        from config import __version__
    except ImportError:
        return "unknown"
    return str(__version__)


def _json_value(value):
    """``json.dumps`` default: numpy scalars and timestamps; anything else is refused.

    ``default=str`` used to turn a DataFrame into its repr, which loaded back
    as a string and crashed the map (REPLAY-19).
    """
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, pd.Timedelta):
        return None if pd.isna(value) else value.total_seconds()
    if value is pd.NaT or value is pd.NA:
        return None
    raise TypeError(f"{type(value).__name__} is not JSON data")


def _safe_name(value) -> str:
    """A filesystem-safe name for a per-driver Parquet file."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))


class DataSourceManager:
    """Unified interface with automatic fallback: Live → Historical → Replay"""

    def __init__(
        self,
        cache_dir: str | None = None,
        replay_dir: str | None = None,
        live_adapter: SignalRLiveAdapter | None = None,
    ):
        # Defaults come from config (which reads .env), so the documented
        # FASTF1_CACHE_DIR / REPLAY_DIR settings actually take effect.
        self.fastf1 = FastF1Adapter(cache_dir or config.fastf1_cache_dir)
        self.jolpica = JolpicaAdapter()
        # One upstream connection per process, not per browser tab: managers
        # live in st.session_state, so a per-manager adapter meant a new
        # SignalR connection for every viewer (LIVE-09).
        self.live = live_adapter or get_live_adapter()
        self.replay_dir = Path(replay_dir or config.replay_dir)
        self.replay_dir.mkdir(parents=True, exist_ok=True)

    def get_session_data(
        self,
        source: str = "auto",  # "auto", "fastf1", "live", "replay"
        year: int | None = None,
        gp: str | None = None,
        session_type: str | None = None,
        replay_file: str | None = None,
        telemetry_scope: str = SCOPE_FASTEST,
        progress=None,
    ) -> dict:
        """
        Returns unified data dict:
        {
            'session_info': {...},
            'telemetry': {driver: DataFrame[Distance, Speed, Throttle, Brake, RPM, Gear, DRS]},
            'laps': DataFrame,
            'stints': DataFrame,
            'location': {driver: DataFrame[Distance, X, Y, Z]},
            'weather': DataFrame,
            'drivers': DataFrame[driver_number, name_acronym, team_colour, team_name],
            'source': 'fastf1'|'jolpica'|'live'|'replay',
            'is_live': bool,
            'live_client': SignalRLiveAdapter (if live)
        }

        ``telemetry_scope`` controls how much of a driver's running the FastF1
        telemetry covers: ``'fastest'`` (default) uses each driver's fastest
        lap, so Distance runs 0 -> lap length and drivers line up at the same
        track position; ``'session'`` uses every lap, with Distance
        accumulating across the whole session.
        """

        if source == "replay":
            if not replay_file:
                raise ValueError("source='replay' requires a replay_file")
            return self._load_replay(replay_file)

        if source == "auto":
            # Try live first (during race weekends)
            if self._is_race_weekend():
                return self._load_live_session()

            # Fallback: most recent completed race
            if not year:
                recent = self._get_most_recent_completed_race()
                year, gp, session_type = recent["year"], recent["gp"], recent["session_type"]

        if source in ("auto", "fastf1"):
            if year is None or gp is None or session_type is None:
                raise ValueError(
                    f"source={source!r} needs year, gp and session_type "
                    f"(got {year!r}, {gp!r}, {session_type!r})"
                )
            return self._load_fastf1_session(year, gp, session_type, telemetry_scope, progress)

        if source == "live":
            return self._load_live_session()

        raise ValueError(f"Unknown source: {source}")

    def live_session(self) -> dict | None:
        """The F1 session on air right now, or None.

        Reads the event schedule's own session times rather than asking
        whether race day is within three days - that called an entire week
        "live" and hid the historical selectors throughout it.
        """
        try:
            return live_session_now(self.fastf1.get_available_sessions())
        except Exception as exc:
            logger.warning("Could not check for a live session: %s", exc)
            return None

    def _is_race_weekend(self) -> bool:
        """Whether a session is actually running now."""
        return self.live_session() is not None

    def _load_fastf1_session(
        self,
        year: int,
        gp: str,
        session_type: str,
        telemetry_scope: str = SCOPE_FASTEST,
        progress=None,
    ) -> dict:
        """Load one FastF1 session into the unified dict.

        ``progress``, when given, is called with a short description of each
        step ("Timing and laps", "Telemetry 7/20", ...) so the UI can say what
        a slow load is doing (UI-06).
        """
        report = progress or (lambda step: None)
        report("Timing and laps")
        session = self.fastf1.load_session(year, gp, session_type)
        drivers = session.results["Abbreviation"].tolist()

        # One car/position merge per driver feeds both the channel charts and
        # the track map. Drivers who never set a lap (DNS/withdrawn) come back
        # empty and are dropped so downstream renderers see only real data.
        telemetry, location = {}, {}
        for index, driver in enumerate(drivers, start=1):
            report(f"Telemetry {index}/{len(drivers)}")
            channels, trail = self.fastf1.get_driver_frames(session, driver, telemetry_scope)
            if not channels.empty:
                telemetry[driver] = channels
            if not trail.empty:
                location[driver] = trail

        # The dashboard's micro-sectors and dominance map only mean anything
        # over a single lap, so they always read fastest-lap frames - which
        # are the same objects unless the user asked for full-session charts.
        if telemetry_scope == SCOPE_FASTEST:
            dashboard_telemetry, dashboard_location = telemetry, location
        else:
            dashboard_telemetry, dashboard_location = {}, {}
            for driver in drivers:
                channels, trail = self.fastf1.get_driver_frames(session, driver, SCOPE_FASTEST)
                if not channels.empty:
                    dashboard_telemetry[driver] = channels
                if not trail.empty:
                    dashboard_location[driver] = trail

        laps = self.fastf1.get_laps(session)
        # Positions over the whole session, for the replay scrubber.
        report(f"Positions, {len(drivers)} drivers")
        positions = self.fastf1.get_position_timeline(session, drivers)
        report("Building replay")
        session_start = self.fastf1.session_start(session)
        clock = replay_clock(laps, positions, session_type, session_start)

        return {
            "session_info": {
                "year": year,
                "gp": gp,
                "session_type": session_type,
                "session_name": session.name,
                "country": self._event_country(session),
                "date": session.date,
                "telemetry_scope": telemetry_scope,
                # Replay metadata (REPLAY-02), all JSON-safe for saved replays.
                "session_start": session_start,
                "segment_starts": self.fastf1.get_segment_starts(session, session_type),
                "total_laps": self.fastf1.total_laps(session),
                "replay_clock": clock.to_dict(),
            },
            "telemetry": telemetry,
            "dashboard_telemetry": dashboard_telemetry,
            "dashboard_location": dashboard_location,
            "laps": laps,
            "stints": self.fastf1.get_stints(session),
            "results": self.fastf1.get_results(session),
            "positions": positions,
            # The timing screen and track state over time, for the replay.
            "timing_stream": self.fastf1.get_timing_stream(session),
            "track_status": self.fastf1.get_track_status(session),
            "location": location,
            "weather": self._get_weather_from_session(session),
            "race_control": self.fastf1.get_race_control(session),
            "compound_colors": self.fastf1.compound_colors(session),
            "circuit_info": self.fastf1.get_circuit_info(session),
            "drivers": self._get_driver_info(session),
            "source": "fastf1",
            "is_live": False,
            # Ended less than a few hours ago: F1's archive may still be
            # partial, so the app does not runtime-cache it (HIST-09).
            "provisional": self.fastf1.ended_recently(session),
        }

    def _load_live_session(self) -> dict:
        """Start live SignalR client and return initial data structure."""
        return {
            "session_info": {"is_live": True, "source": "signalr_live"},
            "telemetry": {},  # Populated via poll_live_data()
            "laps": pd.DataFrame(),
            "stints": pd.DataFrame(),
            "results": pd.DataFrame(),
            "positions": pd.DataFrame(),
            "timing_stream": pd.DataFrame(),
            "track_status": pd.DataFrame(),
            "location": {},
            "weather": pd.DataFrame(),
            "race_control": pd.DataFrame(),
            "compound_colors": {},
            "circuit_info": {},
            "drivers": pd.DataFrame(),
            "source": "live",
            "is_live": True,
            "live_client": self.live,  # Pass client for UI to use
        }

    def poll_live_data(self, delay: float = 0.0, now: float | None = None) -> dict:
        """Snapshot the buffered SignalR data in the unified session-dict
        shape. Call repeatedly (e.g. on a timer) while live; each call folds
        in whatever arrived since the stream started.

        Returns dict with keys telemetry/laps/stints/location/weather/drivers,
        mirroring get_session_data() so the same renderers work for both.

        The built snapshot is shared by every tab through the adapter
        (LIVE-36): nothing new since the last poll means the same snapshot
        comes back, with only the clock and heartbeat refreshed (LIVE-11,
        LIVE-23). ``delay`` (0 to 300 s) shows the session as it stood that
        long ago, to match a delayed broadcast (LIVE-21).
        """
        adapter = self.live
        cache = adapter.snapshots
        moment = time.monotonic() if now is None else now
        with cache.lock:
            token = adapter.change_token()
            if cache.snapshot is None or token != cache.token:
                cache.store(token, self._build_live_snapshot(adapter))
            else:
                info = cache.snapshot["session_info"]
                remaining = extrapolated_remaining(adapter.state.get("ExtrapolatedClock") or {})
                if remaining is not None:
                    info["extrapolated_clock"] = remaining
                    if info.get("segment"):
                        info["segment_remaining"] = remaining
                # A Heartbeat changes no data, so it never invalidates the
                # snapshot; without this the caption froze (LIVE-23).
                info["last_heartbeat"] = adapter.last_heartbeat
            current = cache.snapshot
            assert current is not None  # stored just above when missing
            cache.remember(moment, pd.Timestamp.now(tz="UTC"))
            if not delay or delay <= 0:
                return current
            past = cache.at(moment - min(float(delay), cache.history_seconds))
        return self._delayed_snapshot(current, past, float(delay))

    @staticmethod
    def _delayed_snapshot(current: dict, past: tuple | None, delay: float) -> dict:
        """``current`` as it stood at the kept moment ``past`` (LIVE-21).

        Order, gaps, laps and messages come from the kept snapshot; the
        per-driver telemetry and GPS frames are cut at the same wall-clock
        moment, since only their light neighbours are kept per second.
        """
        if past is None:
            return current
        wall, light = past
        cut: dict[str, dict] = {}
        for key in ("telemetry", "location"):
            frames = {}
            for driver, frame in (current.get(key) or {}).items():
                if isinstance(frame, pd.DataFrame) and "timestamp" in frame.columns:
                    stamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
                    frame = frame[stamps <= wall]
                frames[driver] = frame
            cut[key] = frames
        info = dict(light.get("session_info", {}))
        info["broadcast_delay"] = delay
        return {**light, **cut, "session_info": info}

    def _build_live_snapshot(self, adapter: SignalRLiveAdapter) -> dict:
        """Build the unified dict from the adapter's state and buffers."""
        car_df = LiveDataProcessor.parse_car_data(adapter.get_buffered_data("CarData.z"))
        pos_df = LiveDataProcessor.parse_position_data(adapter.get_buffered_data("Position.z"))
        weather_df = LiveDataProcessor.parse_weather_data(adapter.get_buffered_data("WeatherData"))

        # Keyframe+delta topics come from the merged state (LIVE-05).
        timing_state = adapter.state.get("TimingData")
        driver_state = adapter.state.get("DriverList")
        # TyreStintSeries when the feed delivers it; TimingAppData carries the
        # same per-driver stints and is the one every client subscribes to.
        stint_state = adapter.state.get("TyreStintSeries") or (
            LiveDataProcessor.stints_from_timing_app(adapter.state.get("TimingAppData"))
        )

        drivers_df = LiveDataProcessor.drivers_from_state(driver_state)
        timing_df = LiveDataProcessor.timing_from_state(timing_state)
        stints_df = LiveDataProcessor.stints_from_state(
            stint_state, LiveDataProcessor.acronyms_from_state(driver_state)
        )

        # Fall back to timing-feed driver numbers if DriverList is empty
        if drivers_df.empty and not timing_df.empty:
            nums = sorted(timing_df["driver_number"].dropna().unique(), key=lambda x: int(x))
            drivers_df = pd.DataFrame(
                {
                    "driver_number": nums,
                    "name_acronym": [f"#{n}" for n in nums],
                    "team_colour": ["#888888"] * len(nums),
                    "team_name": [""] * len(nums),
                    "full_name": [f"Driver {n}" for n in nums],
                }
            )

        acr_by_num = {}
        if not drivers_df.empty:
            acr_by_num = dict(
                zip(drivers_df["driver_number"], drivers_df["name_acronym"], strict=False)
            )

        # Timestamps are parsed once per frame here: every per-driver
        # distance call used to re-parse its slice's ISO strings (LIVE-11).
        for frame in (car_df, pos_df):
            if not frame.empty and "timestamp" in frame.columns:
                frame["timestamp"] = LiveDataProcessor.parsed_timestamps(frame["timestamp"])

        # Lap boundaries let each driver's trace cover one completed lap, so
        # Distance runs 0 -> lap length and drivers are comparable (LIVE-13).
        boundaries = LiveDataProcessor.lap_boundaries(adapter.recorded_laps())
        shown_lap: dict[str, int] = {}
        current_lap: dict[str, int] = {}
        for entry in adapter.recorded_laps():
            driver = str(entry.get("driver_number"))
            lap = entry.get("LapNumber")
            if isinstance(lap, int):
                current_lap[driver] = max(current_lap.get(driver, 0), lap + 1)

        # --- per-driver GPS trails for the track map (with real distances) ---
        location: dict[str, pd.DataFrame] = {}
        pos_distance_by_num: dict[Any, pd.DataFrame] = {}
        if not pos_df.empty:
            pos_df = pos_df.dropna(subset=["X", "Y"])
            for num, grp in pos_df.groupby("driver_number"):
                name = acr_by_num.get(num, f"#{num}")
                ordered = grp.sort_values("timestamp")
                lap_slice = LiveDataProcessor.last_completed_lap(
                    ordered, boundaries.get(str(num), [])
                )
                if lap_slice is not None:
                    d = lap_slice
                    shown_lap[name] = current_lap.get(str(num), 1) - 1
                else:
                    d = ordered.tail(3000).reset_index(drop=True)
                # Cumulative metres travelled along the driver's own GPS path
                dist = LiveDataProcessor.distance_at(d, d["timestamp"])
                if dist is not None:
                    d = d.assign(Distance=dist)
                location[name] = d[["X", "Y"]].assign(
                    Z=d.get("Z"),
                    **({"Distance": d["Distance"]} if "Distance" in d.columns else {}),
                    # When each point was sampled, for the broadcast delay.
                    timestamp=d["timestamp"],
                )
                if "Distance" in d.columns:
                    pos_distance_by_num[num] = d

        # --- per-driver telemetry (tail keeps memory bounded) ---
        telemetry: dict[str, pd.DataFrame] = {}
        if not car_df.empty:
            car_df = car_df.dropna(subset=["Speed"])
            for num, grp in car_df.groupby("driver_number"):
                name = acr_by_num.get(num, f"#{num}")
                ordered = grp.sort_values("timestamp")
                lap_slice = LiveDataProcessor.last_completed_lap(
                    ordered, boundaries.get(str(num), [])
                )
                d = (
                    lap_slice.copy()
                    if lap_slice is not None
                    else ordered.tail(2000).reset_index(drop=True).copy()
                )
                # Real distance interpolated from this driver's GPS trail;
                # fall back to an index-based pseudo-distance without GPS.
                pos_d = pos_distance_by_num.get(num)
                dist = (
                    LiveDataProcessor.distance_at(pos_d, d["timestamp"])
                    if pos_d is not None
                    else None
                )
                if dist is not None:
                    d.insert(0, "Distance", dist)
                else:
                    d.insert(0, "Distance", np.arange(len(d)) * 10)
                d["DriverAcronym"] = name
                keep = [
                    c
                    for c in [
                        "Distance",
                        "timestamp",
                        "DriverAcronym",
                        "Speed",
                        "Throttle",
                        "Brake",
                        "RPM",
                        "nGear",
                        "DRS",
                    ]
                    if c in d.columns
                ]
                telemetry[name] = d[keep]

        # --- laps: recorded completions plus the lap in progress ---
        laps_df = LiveDataProcessor.laps_from_history(
            adapter.recorded_laps(), timing_state, acr_by_num
        )

        # --- ensure stint chart columns exist ---
        if not stints_df.empty:
            for col, default in (("LapStart", 1), ("LapEnd", 1)):
                if col not in stints_df.columns:
                    stints_df[col] = default

        info = self._session_info_from_feed(adapter)
        # Race control and track status are merged state topics.
        rcm_state = adapter.state.get("RaceControlMessages")
        rcm_records = [m for m in as_list(rcm_state.get("Messages")) if isinstance(m, dict)]
        race_control_df = LiveDataProcessor.parse_race_control(rcm_records)
        track_state = adapter.state.get("TrackStatus")
        track_status = LiveDataProcessor.parse_track_status([track_state] if track_state else [])
        standings = (
            LiveDataProcessor.standings_from_state(
                timing_state,
                acr_by_num,
                race=is_race_session(info),
                segment_prefix=info.get("segment_prefix", "Q"),
            )
            if timing_state
            else pd.DataFrame()
        )
        # Corners and rotation for the map, once the feed names the circuit;
        # fetched only while actually connected, never from a replayed feed.
        circuit_info = (
            adapter.circuit_info(info.get("year"), info.get("circuit_key"))
            if adapter.is_running()
            else {}
        )

        snapshot = {
            "session_info": {
                **info,
                "is_live": True,
                "source": "signalr_live",
                "track_status": track_status,
                # "lap" once traces are segmented at lap completions; until
                # then Distance is cumulative since the stream started.
                "telemetry_scope": "lap" if shown_lap else "session",
                "telemetry_lap": shown_lap,
                "current_lap": {
                    acr_by_num.get(num, f"#{num}"): lap for num, lap in current_lap.items()
                },
            },
            "telemetry": telemetry,
            "laps": laps_df,
            "stints": stints_df,
            "results": pd.DataFrame(),  # live order comes from TimingData
            # The timing screen's own order, gaps and status (same shape as a
            # replay snapshot's), so the tower is right during a race.
            "standings": standings,
            "positions": pd.DataFrame(),
            "timing_stream": pd.DataFrame(),
            "track_status": pd.DataFrame(),
            "location": location,
            "weather": weather_df,
            "race_control": race_control_df,
            "compound_colors": {},
            "circuit_info": circuit_info,
            "drivers": drivers_df,
            "source": "live",
            "is_live": True,
        }
        return snapshot

    @staticmethod
    def _session_info_from_feed(adapter: SignalRLiveAdapter) -> dict:
        """Session metadata from SessionInfo / SessionStatus.

        ``SessionInfo.Meeting`` is a **nested dict** holding the Grand Prix
        (``Name``, ``Location``, ``Country``, ``Circuit``); the top-level
        ``Name`` is the *session* name ("Race", "Practice 1"). Reading them
        the other way round put "Race" in the header where the GP belongs.
        """
        info: dict[str, Any] = {
            "gp": "Live Session",
            "session_type": "",
            "session_name": "",
            "year": None,
            "circuit_key": None,
            "gmt_offset": "",
        }
        si = adapter.state.get("SessionInfo")

        meeting = si.get("Meeting")
        if isinstance(meeting, dict):
            if meeting.get("Name"):
                info["gp"] = str(meeting["Name"])
            circuit = meeting.get("Circuit")
            if isinstance(circuit, dict):
                info["circuit_key"] = circuit.get("Key")
        elif meeting:  # defensive: a bare string is not the documented shape
            info["gp"] = str(meeting)

        if si.get("Name"):
            info["session_name"] = str(si["Name"])
        if si.get("Type"):
            info["session_type"] = str(si["Type"])
        if si.get("GmtOffset"):
            info["gmt_offset"] = str(si["GmtOffset"])

        start = si.get("StartDate")
        if start:
            year = pd.to_datetime(start, errors="coerce")
            if pd.notna(year):
                info["year"] = int(year.year)

        status = adapter.state.get("SessionStatus")
        info["status"] = status.get("Status", "")

        clock = adapter.state.get("ExtrapolatedClock") or {}
        remaining = extrapolated_remaining(clock)
        if remaining is not None:
            info["extrapolated_clock"] = remaining
        lap_count = adapter.state.get("LapCount") or {}
        if lap_count.get("CurrentLap") is not None:
            info["current_lap_number"] = lap_count.get("CurrentLap")
            info["total_laps"] = lap_count.get("TotalLaps")
        info["last_heartbeat"] = adapter.last_heartbeat

        # Qualifying: which segment is running, for the header (LIVE-19).
        name = info["session_name"].lower()
        info["segment_prefix"] = "SQ" if ("sprint" in name or "shootout" in name) else "Q"
        part = adapter.state.get("TimingData").get("SessionPart")
        if part not in (None, "") and not is_race_session(info):
            with contextlib.suppress(TypeError, ValueError):
                info["segment"] = f"{info['segment_prefix']}{int(str(part))}"
                if remaining is not None:
                    info["segment_remaining"] = remaining
        return info

    def _get_most_recent_completed_race(self) -> dict:
        """Most recent *finished* race from the FastF1 schedule.

        Falls back to the configured defaults only when the schedule is
        unavailable - a hardcoded event silently becomes a year stale.
        """
        try:
            schedule = self.fastf1.get_available_sessions()
        except Exception as exc:
            logger.warning("Could not read the event schedule: %s", exc)
            schedule = pd.DataFrame()

        event = latest_completed_event(schedule)
        if event is not None:
            return {
                "year": int(event["Year"]),
                "gp": event["EventName"],
                "session_type": "R",
            }
        return {
            "year": config.default_year,
            "gp": config.default_gp,
            "session_type": config.default_session,
        }

    @staticmethod
    def _event_country(session) -> str:
        """Host country for the header badge, or "" when unavailable."""
        try:
            return str(session.event["Country"])
        except Exception as exc:
            logger.debug("No country in the event schedule: %s", exc)
            return ""

    def _get_weather_from_session(self, session) -> pd.DataFrame:
        """Extract weather data from FastF1 session."""
        try:
            return (
                session.weather_data.copy() if hasattr(session, "weather_data") else pd.DataFrame()
            )
        except Exception as exc:
            logger.warning("Weather data unavailable for this session: %s", exc)
            return pd.DataFrame()

    def _get_driver_info(self, session) -> pd.DataFrame:
        """Extract driver info from FastF1 session."""
        try:
            results = session.results
            drivers = []
            for _, row in results.iterrows():
                team_color = row.get("TeamColor", "#FF0000")
                if not str(team_color).startswith("#"):
                    team_color = f"#{team_color}"
                drivers.append(
                    {
                        "driver_number": row["DriverNumber"],
                        "name_acronym": row["Abbreviation"],
                        "team_colour": team_color,
                        "team_name": row["TeamName"],
                        "full_name": f"{row['FirstName']} {row['LastName']}",
                    }
                )
            return pd.DataFrame(drivers)
        except Exception as exc:
            logger.warning("Driver list unavailable for this session: %s", exc)
            return pd.DataFrame()

    # Bump when the serialized layout changes; _load_replay rejects newer
    # schemas with a clear message instead of failing deep inside pickle.
    # v2 added 'race_control' and 'compound_colors'; v3 added 'circuit_info'
    # (corner markers + track rotation); v6 added 'positions' (the replay
    # timeline); v4 added 'results' (official
    # classification, needed to order a race by finishing position); v5 added
    # 'dashboard_telemetry'/'dashboard_location' (fastest-lap frames, stored
    # only when the charts use a different scope); v7 added 'timing_stream'
    # and 'track_status' plus session_info 'replay_clock', 'segment_starts',
    # 'session_start' and 'total_laps' (REPLAY-02). Older replays simply lack
    # those keys and load with empty defaults. v8 stores
    # 'circuit_info.corners' as its own Parquet table (it was written as the
    # DataFrame's repr string, which crashed the map - REPLAY-19), restores
    # session_info 'date' as a timestamp and stamps 'app_version' (REPO-23).
    REPLAY_SCHEMA_VERSION = 8

    # Tables stored as their own Parquet file inside a replay directory.
    FRAME_KEYS = ("laps", "stints", "results", "weather", "race_control", "drivers")
    # Per-driver frames: one Parquet each, under a subdirectory.
    FRAME_DICT_KEYS = (
        "telemetry",
        "location",
        "dashboard_telemetry",
        "dashboard_location",
    )
    META_FILE = "meta.json"
    CORNERS_FILE = "circuit_info.corners.parquet"
    # Whole-session tables the loader also stores as Parquet.
    STREAM_FRAME_KEYS = ("positions", "timing_stream", "track_status")
    # Plain values a replay may carry. Anything else in meta.json - an
    # 'is_live' or 'source' of its own choosing included - is refused (SEC-02).
    VALUE_KEYS = ("session_info", "compound_colors", "circuit_info")
    # Written by older versions and ignored on load: the loader sets both.
    IGNORED_VALUE_KEYS = ("source", "is_live")

    def save_replay(self, data: dict, name: str) -> str:
        """Save session data for offline replay, as data rather than code.

        A directory holding ``meta.json`` (schema, session info and the small
        plain values) plus Parquet for every table - Parquet keeps Timedelta,
        nullable Int64 and categorical dtypes, and loading one cannot execute
        anything. Replays are the artefact users share, so the old pickle
        format is read-only now (HIST-02).
        """
        target = self.replay_dir / f"{name}_{datetime.now(UTC):%Y%m%d_%H%M%S}"
        target.mkdir(parents=True, exist_ok=True)

        meta: dict = {
            "schema": self.REPLAY_SCHEMA_VERSION,
            "app": "f1-telemetry-dashboard",
            "app_version": _app_version(),
            "saved_at": datetime.now(UTC).isoformat(),
            "values": {},
            "frames": [],
            "frame_dicts": {},
        }

        for key, value in data.items():
            if key in ("live_client", "provisional", *self.IGNORED_VALUE_KEYS):
                continue
            # Under fastest scope these are the same objects; storing them
            # twice would double the file for nothing.
            if key == "dashboard_telemetry" and value is data.get("telemetry"):
                continue
            if key == "dashboard_location" and value is data.get("location"):
                continue

            if isinstance(value, pd.DataFrame):
                self._write_frame(value, target / f"{key}.parquet")
                meta["frames"].append(key)
            elif key in self.FRAME_DICT_KEYS and isinstance(value, dict):
                folder = target / key
                folder.mkdir(exist_ok=True)
                written = []
                for driver, frame in value.items():
                    if isinstance(frame, pd.DataFrame):
                        self._write_frame(frame, folder / f"{_safe_name(driver)}.parquet")
                        written.append(driver)
                meta["frame_dicts"][key] = written
            elif key == "circuit_info" and isinstance(value, dict):
                corners = value.get("corners")
                plain = {k: v for k, v in value.items() if k != "corners"}
                if isinstance(corners, pd.DataFrame):
                    self._write_frame(corners, target / self.CORNERS_FILE)
                    plain["corners"] = self.CORNERS_FILE
                meta["values"][key] = plain
            else:
                meta["values"][key] = value

        try:
            text = json.dumps(meta, indent=2, default=_json_value)
        except TypeError as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError(f"Cannot save this session as a replay: {exc}") from exc
        (target / self.META_FILE).write_text(text, encoding="utf-8")
        return str(target)

    @staticmethod
    def _write_frame(frame: pd.DataFrame, path: Path) -> None:
        """One table to Parquet, preserving the dtypes the app relies on."""
        frame.to_parquet(path, index=False)

    def _resolve_replay(self, replay_file: str) -> Path:
        """Resolve a replay reference to a file inside ``replay_dir``.

        The sidebar offers the bare names from :meth:`get_available_replays`,
        so a relative name must not be opened against the process CWD. Only
        the basename is honoured, which also confines traversal attempts
        (``../secrets.pkl``) to the replay directory.
        """
        candidate = Path(replay_file)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        path = self.replay_dir / candidate.name
        if not path.exists():
            raise FileNotFoundError(
                f"Replay file not found: {replay_file} (looked in {self.replay_dir})"
            )
        return path

    def _load_replay(self, filepath: str, allow_pickle: bool = False) -> dict:
        """Load a replay directory, or a legacy pickle if explicitly trusted."""
        path = self._resolve_replay(filepath)
        if path.is_dir():
            return self._load_replay_bundle(path)
        return self._load_legacy_pickle(path, allow_pickle=allow_pickle)

    def _load_replay_bundle(self, path: Path) -> dict:
        meta = json.loads((path / self.META_FILE).read_text(encoding="utf-8"))
        if int(meta.get("schema", 0)) > self.REPLAY_SCHEMA_VERSION:
            raise ValueError(
                f"Replay {path.name} was saved with schema {meta['schema']} but this "
                f"app supports up to {self.REPLAY_SCHEMA_VERSION}. Please update the app."
            )

        self._validate_meta(meta, path.name)

        data: dict = {
            key: value
            for key, value in (meta.get("values") or {}).items()
            if key not in self.IGNORED_VALUE_KEYS
        }
        for key in meta.get("frames", []):
            frame_path = path / f"{key}.parquet"
            data[key] = pd.read_parquet(frame_path) if frame_path.is_file() else pd.DataFrame()
        for key, drivers in (meta.get("frame_dicts") or {}).items():
            folder = path / key
            frames = {}
            for driver in drivers:
                driver_path = folder / f"{_safe_name(driver)}.parquet"
                if driver_path.is_file():
                    frames[driver] = pd.read_parquet(driver_path)
            data[key] = frames

        circuit = data.get("circuit_info")
        if isinstance(circuit, dict) and circuit.get("corners") == self.CORNERS_FILE:
            corners_path = path / self.CORNERS_FILE
            circuit["corners"] = (
                pd.read_parquet(corners_path) if corners_path.is_file() else pd.DataFrame()
            )
        return self._finalise_replay(data)

    def _validate_meta(self, meta, name: str) -> None:
        """Refuse a ``meta.json`` that names anything a replay never holds (SEC-02).

        Frame names become file paths, so ``"../x"`` would read Parquet from
        outside the bundle, and ``values`` could set ``is_live`` or any other
        top-level key of the session dict.
        """

        def refuse(reason: str) -> None:
            raise ValueError(f"Replay {name} is not a valid replay: {reason}")

        if not isinstance(meta, dict):
            refuse("meta.json is not an object")
        values = meta.get("values") or {}
        frames = meta.get("frames") or []
        frame_dicts = meta.get("frame_dicts") or {}
        if not isinstance(values, dict) or not isinstance(frames, list):
            refuse("malformed values or frames")
        if not isinstance(frame_dicts, dict):
            refuse("malformed frame_dicts")
        allowed_frames = (*self.FRAME_KEYS, *self.STREAM_FRAME_KEYS)
        for key in frames:
            if key not in allowed_frames:
                refuse(f"unknown table {key!r}")
        for key, drivers in frame_dicts.items():
            if key not in self.FRAME_DICT_KEYS:
                refuse(f"unknown table group {key!r}")
            if not isinstance(drivers, list):
                refuse(f"malformed driver list for {key!r}")
            for driver in drivers:
                text = str(driver)
                if "/" in text or "\\" in text or ".." in text:
                    refuse(f"driver name {text!r} is a path")
        for key, value in values.items():
            if key in self.IGNORED_VALUE_KEYS:
                if key == "is_live" and value is not False:
                    refuse("a replay cannot be live")
                continue
            if key not in self.VALUE_KEYS:
                refuse(f"unknown value {key!r}")

    def _load_legacy_pickle(self, path: Path, allow_pickle: bool = False) -> dict:
        """Read a pre-HIST-02 ``.pkl`` replay.

        Refused unless the caller passes ``allow_pickle=True``: unpickling
        runs arbitrary code, and replays are exactly the file people share.
        """
        if not allow_pickle:
            raise ValueError(
                f"{path.name} is a legacy pickle replay. Loading one runs arbitrary "
                f"code from the file, so it is only read with allow_pickle=True - "
                f"do that only for files you created yourself."
            )
        with open(path, "rb") as handle:
            # Guarded above; the format is read-only and goes away next release.
            payload = pickle.load(handle)  # noqa: S301

        if isinstance(payload, dict) and "schema" in payload and "data" in payload:
            if payload["schema"] > self.REPLAY_SCHEMA_VERSION:
                raise ValueError(
                    f"Replay {path.name} was saved with schema "
                    f"{payload['schema']} but this app supports up to "
                    f"{self.REPLAY_SCHEMA_VERSION}. Please update the app."
                )
            data = payload["data"]
        else:
            data = payload

        for key in ("laps", "stints", "results", "weather", "drivers", "race_control"):
            if key in data:
                data[key] = pd.DataFrame(data[key])
        for key in self.FRAME_DICT_KEYS:
            if key in data:
                data[key] = {k: pd.DataFrame(v) for k, v in data[key].items()}
        return self._finalise_replay(data)

    @staticmethod
    def _finalise_replay(data: dict) -> dict:
        """Defaults for keys a replay predates, and the source marker."""
        data.setdefault("race_control", pd.DataFrame())
        data.setdefault("compound_colors", {})
        data.setdefault("circuit_info", {})
        data.setdefault("results", pd.DataFrame())
        data.setdefault("positions", pd.DataFrame())
        data.setdefault("timing_stream", pd.DataFrame())
        data.setdefault("track_status", pd.DataFrame())
        data.setdefault("telemetry", {})
        data.setdefault("location", {})

        circuit = data["circuit_info"]
        if isinstance(circuit, dict) and isinstance(circuit.get("corners"), str):
            # Schema <= 7 wrote the corners DataFrame as its repr (REPLAY-19):
            # nothing to recover, so the map goes without corner labels.
            circuit["corners"] = pd.DataFrame()

        info = data.setdefault("session_info", {})
        if isinstance(info.get("date"), str):
            date = pd.to_datetime(info["date"], errors="coerce")
            info["date"] = None if pd.isna(date) else date
        info.setdefault("segment_starts", [])
        info.setdefault("session_start", None)
        info.setdefault("total_laps", None)
        if ReplayClock.from_dict(info.get("replay_clock")) is None:
            # Schema <= 6: derive the clock the loader would have stored.
            laps = data.get("laps")
            info["replay_clock"] = (
                replay_clock(
                    laps if isinstance(laps, pd.DataFrame) else None,
                    data["positions"],
                    info.get("session_type"),
                    info.get("session_start"),
                ).to_dict()
                if not data["positions"].empty
                else None
            )
        data["source"] = "replay"
        data["is_live"] = False
        return data

    def get_available_replays(self) -> list:
        """Saved replays, newest first.

        Legacy pickles are not offered: no UI path may load one (it would run
        code from the file), so listing them only produced an error
        (SEC-02). ``scripts/convert_legacy_replay.py`` converts a trusted one.
        """
        entries = [p.name for p in self.replay_dir.glob("*") if self._is_replay(p)]
        return sorted(entries, reverse=True)

    def _is_replay(self, path: Path) -> bool:
        return path.is_dir() and (path / self.META_FILE).is_file()
