"""Data Source Manager - Unified interface with automatic fallback"""

import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd
from livef1 import get_session

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
        # Last live snapshot and the ingest token it was built from.
        self._live_snapshot: dict | None = None
        self._live_snapshot_token: tuple | None = None

    def get_session_data(
        self,
        source: str = "auto",  # "auto", "fastf1", "livef1", "live", "replay"
        year: int | None = None,
        gp: str | None = None,
        session_type: str | None = None,
        replay_file: str | None = None,
        telemetry_scope: str = SCOPE_FASTEST,
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

        if source in ("auto", "fastf1", "livef1"):
            if year is None or gp is None or session_type is None:
                raise ValueError(
                    f"source={source!r} needs year, gp and session_type "
                    f"(got {year!r}, {gp!r}, {session_type!r})"
                )
            if source == "livef1":
                return self._load_livef1_session(year, gp, session_type)
            return self._load_fastf1_session(year, gp, session_type, telemetry_scope)

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
        except Exception:
            return None

    def _is_race_weekend(self) -> bool:
        """Whether a session is actually running now."""
        return self.live_session() is not None

    def _load_fastf1_session(
        self, year: int, gp: str, session_type: str, telemetry_scope: str = SCOPE_FASTEST
    ) -> dict:
        session = self.fastf1.load_session(year, gp, session_type)
        drivers = session.results["Abbreviation"].tolist()

        # One car/position merge per driver feeds both the channel charts and
        # the track map. Drivers who never set a lap (DNS/withdrawn) come back
        # empty and are dropped so downstream renderers see only real data.
        telemetry, location = {}, {}
        for driver in drivers:
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

        return {
            "session_info": {
                "year": year,
                "gp": gp,
                "session_type": session_type,
                "session_name": session.name,
                "country": self._event_country(session),
                "date": session.date,
                "telemetry_scope": telemetry_scope,
            },
            "telemetry": telemetry,
            "dashboard_telemetry": dashboard_telemetry,
            "dashboard_location": dashboard_location,
            "laps": self.fastf1.get_laps(session),
            "stints": self.fastf1.get_stints(session),
            "results": self.fastf1.get_results(session),
            "location": location,
            "weather": self._get_weather_from_session(session),
            "race_control": self.fastf1.get_race_control(session),
            "compound_colors": self.fastf1.compound_colors(session),
            "circuit_info": self.fastf1.get_circuit_info(session),
            "drivers": self._get_driver_info(session),
            "source": "fastf1",
            "is_live": False,
        }

    # Map FastF1 EventName-style GP names to LiveF1 circuit short names
    CIRCUIT_MAP: ClassVar = {
        "bahrain": "Sakhir",
        "saudi arabia": "Jeddah",
        "australia": "Melbourne",
        "japan": "Suzuka",
        "china": "Shanghai",
        "miami": "Miami",
        "emilia romagna": "Imola",
        "monaco": "Monaco",
        "canada": "Montreal",
        "spain": "Barcelona",
        "austria": "Spielberg",
        "great britain": "Silverstone",
        "hungary": "Hungaroring",
        "belgium": "Spa",
        "netherlands": "Zandvoort",
        "italy": "Monza",
        "azerbaijan": "Baku",
        "singapore": "Singapore",
        "united states": "Austin",
        "usa": "Austin",
        "mexico": "Mexico City",
        "mexico city": "Mexico City",
        "brazil": "Sao Paulo",
        "sao paulo": "Sao Paulo",
        "las vegas": "Las Vegas",
        "vegas": "Las Vegas",
        "qatar": "Lusail",
        "abu dhabi": "Yas Marina",
    }

    @classmethod
    def _gp_to_circuit_short(cls, gp: str) -> str:
        """Normalize a Grand Prix name ('Bahrain Grand Prix') to a circuit
        short name ('Sakhir') accepted by LiveF1's meeting_identifier."""
        if not gp:
            return gp
        normalized = gp.strip().lower()
        for suffix in (" grand prix", " gp", " grand-prix"):
            normalized = normalized.replace(suffix, "")
        normalized = normalized.strip()
        return cls.CIRCUIT_MAP.get(normalized, gp)

    def _load_livef1_session(self, year: int, gp: str, session_type: str) -> dict:
        """Load session using LiveF1 (historical data with full telemetry)."""
        # Map session type to LiveF1 format
        session_map = {
            "R": "Race",
            "Q": "Qualifying",
            "FP1": "Practice 1",
            "FP2": "Practice 2",
            "FP3": "Practice 3",
            "S": "Sprint",
            "SQ": "Sprint Qualifying",
        }
        livef1_session_type = session_map.get(session_type, session_type)

        # LiveF1 uses meeting_identifier and session_identifier.
        # Need to map the GP name to a circuit short name.
        circuit_short = self._gp_to_circuit_short(gp)

        session = get_session(
            season=year, meeting_identifier=circuit_short, session_identifier=livef1_session_type
        )

        # Generate silver tables (processed data)
        session.generate(silver=True)

        # Get processed data
        laps_df = session.get_laps()
        telemetry_df = session.get_car_telemetry()

        # Get driver info from session
        drivers = session.drivers
        drivers_df = pd.DataFrame(
            {
                "driver_number": [d.driver_number for d in drivers.values()],
                "name_acronym": [d.name_acronym for d in drivers.values()],
                "team_colour": [d.team_colour for d in drivers.values()],
                "team_name": [d.team_name for d in drivers.values()],
                "full_name": [f"{d.first_name} {d.last_name}" for d in drivers.values()],
            }
        )

        # Build telemetry dict by driver
        telemetry_dict = {}
        if not telemetry_df.empty and "Driver" in telemetry_df.columns:
            channels = ["Distance", "Speed", "Throttle", "Brake", "RPM", "nGear", "DRS"]
            for driver in drivers_df["name_acronym"].unique():
                driver_telemetry = telemetry_df[telemetry_df["Driver"] == driver]
                if driver_telemetry.empty:
                    continue
                driver_telemetry = driver_telemetry.copy()
                if "Distance" not in driver_telemetry.columns:
                    # LiveF1 silver tables carry no distance channel; index-based
                    # pseudo-metres keep the charts plottable and ordered.
                    driver_telemetry["Distance"] = np.arange(len(driver_telemetry)) * 10
                available = [c for c in channels if c in driver_telemetry.columns]
                telemetry_dict[driver] = driver_telemetry[available]

        # LiveF1 exposes no GPS channel, so the track map has no source here.

        # Process laps data
        if not laps_df.empty:
            # Ensure we have the right columns
            laps_df = laps_df.copy()
            if "IsPitOutLap" not in laps_df.columns and "PitOutLap" in laps_df.columns:
                laps_df["IsPitOutLap"] = laps_df["PitOutLap"]

        return {
            "session_info": {
                "year": year,
                "gp": gp,
                "session_type": session_type,
                "session_name": f"{gp} {session_type}",
                "date": None,  # LiveF1 doesn't expose date easily
            },
            "telemetry": telemetry_dict,
            "laps": laps_df,
            "stints": pd.DataFrame(),  # Could be extracted from LiveF1
            "results": pd.DataFrame(),
            "location": {},
            "weather": pd.DataFrame(),
            "race_control": pd.DataFrame(),
            "compound_colors": {},
            "circuit_info": {},
            "drivers": drivers_df,
            "source": "livef1",
            "is_live": False,
        }

    def _load_live_session(self) -> dict:
        """Start live SignalR client and return initial data structure."""
        return {
            "session_info": {"is_live": True, "source": "signalr_live"},
            "telemetry": {},  # Populated via poll_live_data()
            "laps": pd.DataFrame(),
            "stints": pd.DataFrame(),
            "results": pd.DataFrame(),
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

    def poll_live_data(self) -> dict:
        """Snapshot the buffered SignalR data in the unified session-dict
        shape. Call repeatedly (e.g. on a timer) while live; each call folds
        in whatever arrived since the stream started.

        Returns dict with keys telemetry/laps/stints/location/weather/drivers,
        mirroring get_session_data() so the same renderers work for both.
        """
        adapter = self.live

        # Nothing new since the last poll: hand back the same snapshot rather
        # than reprocessing the buffers for an identical result (LIVE-11).
        token = adapter.change_token()
        if self._live_snapshot is not None and token == self._live_snapshot_token:
            return self._live_snapshot

        car_df = LiveDataProcessor.parse_car_data(adapter.get_buffered_data("CarData.z"))
        pos_df = LiveDataProcessor.parse_position_data(adapter.get_buffered_data("Position.z"))
        weather_df = LiveDataProcessor.parse_weather_data(adapter.get_buffered_data("WeatherData"))

        # Keyframe+delta topics come from the merged state when it has been
        # fed (LIVE-05); the legacy livef1 callback path still delivers
        # pre-parsed records, so those buffers remain the fallback.
        timing_state = adapter.state.get("TimingData")
        driver_state = adapter.state.get("DriverList")
        stint_state = adapter.state.get("TyreStintSeries")

        drivers_df = (
            LiveDataProcessor.drivers_from_state(driver_state)
            if driver_state
            else LiveDataProcessor.parse_driver_list(adapter.get_buffered_data("DriverList"))
        )
        timing_df = (
            LiveDataProcessor.timing_from_state(timing_state)
            if timing_state
            else LiveDataProcessor.parse_timing_data(adapter.get_buffered_data("TimingData"))
        )
        stints_df = (
            LiveDataProcessor.stints_from_state(
                stint_state, LiveDataProcessor.acronyms_from_state(driver_state)
            )
            if stint_state
            else LiveDataProcessor.parse_tyre_stints(adapter.get_buffered_data("TyreStintSeries"))
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
                    Z=d.get("Z"), **({"Distance": d["Distance"]} if "Distance" in d.columns else {})
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

        # --- laps: recorded completions when the state layer is fed, else
        # the legacy reconstruction from buffered TimingData records ---
        if adapter.lap_history or timing_state:
            laps_df = LiveDataProcessor.laps_from_history(
                adapter.recorded_laps(), timing_state, acr_by_num
            )
        else:
            laps_df = self._laps_from_timing(timing_df, acr_by_num)

        # --- ensure stint chart columns exist ---
        if not stints_df.empty:
            for col, default in (("LapStart", 1), ("LapEnd", 1)):
                if col not in stints_df.columns:
                    stints_df[col] = default

        info = self._session_info_from_feed(adapter)
        race_control_df = LiveDataProcessor.parse_race_control(
            adapter.get_buffered_data("RaceControlMessages")
        )
        track_status = LiveDataProcessor.parse_track_status(
            adapter.get_buffered_data("TrackStatus")
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
            "location": location,
            "weather": weather_df,
            "race_control": race_control_df,
            "compound_colors": {},
            "circuit_info": {},
            "drivers": drivers_df,
            "source": "live",
            "is_live": True,
        }
        self._live_snapshot = snapshot
        self._live_snapshot_token = token
        return snapshot

    @staticmethod
    def _ever_true(records: pd.DataFrame, column: str) -> bool:
        """Whether a flag was ever set across a driver's TimingData records."""
        if column not in records.columns:
            return False
        return bool(records[column].fillna(False).astype(bool).any())

    @staticmethod
    def _laps_from_timing(timing_df: pd.DataFrame, acr_by_num: dict) -> pd.DataFrame:
        """Build a laps DataFrame from TimingData records with real lap
        numbers.

        Legacy path, used only when nothing has fed the state layer (the
        livef1 callback client). It cannot resolve the LIVE-04 sector
        ambiguity - livef1 flattens the snapshot list and the 0-based delta
        dict onto the same ``Sectors_N_Value`` names - so prefer
        :meth:`LiveDataProcessor.laps_from_history`. Removed with LIVE-16.

        TimingData snapshots carry ``NumberOfLaps`` (completed-lap counter)
        and, once a lap is completed, ``LastLapTime_Value``. Each completed
        (lap, time) pair becomes one row; the in-progress lap is appended
        with its latest sector values. Falls back to a record-count
        approximation only when NumberOfLaps was never seen.
        """
        rows = []
        if timing_df.empty or "driver_number" not in timing_df.columns:
            return pd.DataFrame()
        for num, grp in timing_df.groupby("driver_number"):
            grp = grp.sort_values("timestamp")
            completed: dict[int, str] = {}
            current_lap = 0
            latest = grp.iloc[-1]
            # Driver state for the tower's badge. Retirement latches: the feed
            # is lossy, so one "Retired" stands even if later partials omit it.
            flags = {
                "InPit": bool(latest.get("InPit")) if pd.notna(latest.get("InPit")) else False,
                "PitOut": bool(latest.get("PitOut")) if pd.notna(latest.get("PitOut")) else False,
                "Retired": DataSourceManager._ever_true(grp, "Retired"),
                "Stopped": DataSourceManager._ever_true(grp, "Stopped"),
            }
            for _, rec in grp.iterrows():
                nol = pd.to_numeric(pd.Series([rec.get("NumberOfLaps")]), errors="coerce").iloc[0]
                if pd.notna(nol):
                    current_lap = max(current_lap, int(nol))
                last_time = rec.get("LastLapTime_Value")
                if pd.notna(last_time) and current_lap >= 1:
                    completed.setdefault(current_lap, last_time)

            if not completed and current_lap == 0:
                # Fallback: no NumberOfLaps in feed - approximate progression
                lap_time = latest.get("BestLapTime_Value")
                if pd.isna(lap_time):
                    lap_time = latest.get("LastLapTime_Value")
                if pd.isna(lap_time):
                    lap_time = None
                rows.append(
                    {
                        "Driver": acr_by_num.get(num, f"#{num}"),
                        "driver_number": num,
                        "LapNumber": len(grp),
                        "LapTime": lap_time,
                        "IsPitOutLap": False,
                        "IsInProgress": False,
                        **flags,
                    }
                )
            else:
                for lap_no, lap_time in sorted(completed.items()):
                    rows.append(
                        {
                            "Driver": acr_by_num.get(num, f"#{num}"),
                            "driver_number": num,
                            "LapNumber": lap_no,
                            "LapTime": lap_time,
                            "IsPitOutLap": False,
                            "IsInProgress": False,
                            **flags,
                        }
                    )
                # In-progress lap
                rows.append(
                    {
                        "Driver": acr_by_num.get(num, f"#{num}"),
                        "driver_number": num,
                        "LapNumber": current_lap + 1,
                        "LapTime": None,
                        "IsPitOutLap": False,
                        # Not a completed lap: laps_completed must skip it.
                        "IsInProgress": True,
                        **flags,
                    }
                )
            row = rows[-1]
            for i in range(1, 4):
                row[f"Sector{i}Time"] = latest.get(f"Sectors_{i}_Value")
        return pd.DataFrame(rows)

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
        # Merged state first (LIVE-05); the legacy buffer is the fallback for
        # the livef1 callback path, which delivers pre-parsed records.
        si = adapter.state.get("SessionInfo") or adapter.get_latest_data("SessionInfo") or {}

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

        status = (
            adapter.state.get("SessionStatus") or adapter.get_latest_data("SessionStatus") or {}
        )
        info["status"] = status.get("Status", "")
        return info

    def _get_most_recent_completed_race(self) -> dict:
        """Most recent *finished* race from the FastF1 schedule.

        Falls back to the configured defaults only when the schedule is
        unavailable - a hardcoded event silently becomes a year stale.
        """
        try:
            schedule = self.fastf1.get_available_sessions()
        except Exception:
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
        except Exception:
            return ""

    def _get_weather_from_session(self, session) -> pd.DataFrame:
        """Extract weather data from FastF1 session."""
        try:
            return (
                session.weather_data.copy() if hasattr(session, "weather_data") else pd.DataFrame()
            )
        except Exception:
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
        except Exception:
            return pd.DataFrame()

    # Bump when the serialized layout changes; _load_replay rejects newer
    # schemas with a clear message instead of failing deep inside pickle.
    # v2 added 'race_control' and 'compound_colors'; v3 added 'circuit_info'
    # (corner markers + track rotation); v4 added 'results' (official
    # classification, needed to order a race by finishing position); v5 added
    # 'dashboard_telemetry'/'dashboard_location' (fastest-lap frames, stored
    # only when the charts use a different scope). Older replays simply lack
    # those keys and load with empty defaults.
    REPLAY_SCHEMA_VERSION = 5

    def save_replay(self, data: dict, name: str) -> str:
        """Save session data for offline replay.

        Files carry a schema header (``schema``/``saved_at``/``data``) so
        future format changes can be detected and old files keep loading.
        """
        filepath = self.replay_dir / f"{name}_{datetime.now(UTC):%Y%m%d_%H%M%S}.pkl"
        save_data = {}
        frame_dicts = ("telemetry", "location", "dashboard_telemetry", "dashboard_location")
        for k, v in data.items():
            # Same objects under fastest scope: no point storing them twice.
            if k == "dashboard_telemetry" and v is data.get("telemetry"):
                continue
            if k == "dashboard_location" and v is data.get("location"):
                continue
            if k in frame_dicts:
                save_data[k] = (
                    {dk: dv.to_dict("records") for dk, dv in v.items()}
                    if isinstance(v, dict)
                    else v
                )
            elif isinstance(v, pd.DataFrame):
                save_data[k] = v.to_dict("records")
            elif k != "live_client":
                save_data[k] = v

        payload = {
            "schema": self.REPLAY_SCHEMA_VERSION,
            "app": "f1-telemetry-dashboard",
            "saved_at": datetime.now(UTC).isoformat(),
            "data": save_data,
        }
        with open(filepath, "wb") as f:
            pickle.dump(payload, f)
        return str(filepath)

    def _resolve_replay(self, replay_file: str) -> Path:
        """Resolve a replay reference to a file inside ``replay_dir``.

        The sidebar offers the bare names from :meth:`get_available_replays`,
        so a relative name must not be opened against the process CWD. Only
        the basename is honoured, which also confines traversal attempts
        (``../secrets.pkl``) to the replay directory.
        """
        candidate = Path(replay_file)
        if candidate.is_absolute() and candidate.is_file():
            return candidate
        path = self.replay_dir / candidate.name
        if not path.is_file():
            raise FileNotFoundError(
                f"Replay file not found: {replay_file} (looked in {self.replay_dir})"
            )
        return path

    def _load_replay(self, filepath: str) -> dict:
        with open(self._resolve_replay(filepath), "rb") as f:
            # Loading a replay runs arbitrary code; replacing this format
            # with Parquet + JSON is HIST-02.
            payload = pickle.load(f)  # noqa: S301

        # Schema header (current) vs bare session dict (legacy replays)
        if isinstance(payload, dict) and "schema" in payload and "data" in payload:
            if payload["schema"] > self.REPLAY_SCHEMA_VERSION:
                raise ValueError(
                    f"Replay {filepath} was saved with schema "
                    f"{payload['schema']} but this app supports up to "
                    f"{self.REPLAY_SCHEMA_VERSION}. Please update the app."
                )
            data = payload["data"]
        else:
            data = payload

        data.setdefault("race_control", [])
        data.setdefault("compound_colors", {})
        data.setdefault("circuit_info", {})
        data.setdefault("results", [])
        for k in ["laps", "stints", "results", "weather", "drivers", "race_control"]:
            if k in data:
                data[k] = pd.DataFrame(data[k])
        for key in ("telemetry", "location", "dashboard_telemetry", "dashboard_location"):
            if key in data:
                data[key] = {k: pd.DataFrame(v) for k, v in data[key].items()}
        data["source"] = "replay"
        return data

    def get_available_replays(self) -> list:
        """List available replay files."""
        files = list(self.replay_dir.glob("*.pkl"))
        return sorted([f.name for f in files], reverse=True)
