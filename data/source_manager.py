"""Data Source Manager - Unified interface with automatic fallback"""

import pandas as pd
import numpy as np
import pickle
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

from config import config
from data.fastf1_adapter import SCOPE_FASTEST, FastF1Adapter
from data.jolpica_adapter import JolpicaAdapter
from data.live_adapter import SignalRLiveAdapter, LiveDataProcessor
from livef1 import get_session


class DataSourceManager:
    """Unified interface with automatic fallback: Live → Historical → Replay"""

    def __init__(self, cache_dir: str = None, replay_dir: str = None):
        # Defaults come from config (which reads .env), so the documented
        # FASTF1_CACHE_DIR / REPLAY_DIR settings actually take effect.
        self.fastf1 = FastF1Adapter(cache_dir or config.fastf1_cache_dir)
        self.jolpica = JolpicaAdapter()
        self.live = SignalRLiveAdapter(use_livef1=True)
        self.replay_dir = Path(replay_dir or config.replay_dir)
        self.replay_dir.mkdir(parents=True, exist_ok=True)

    def get_session_data(
        self,
        source: str = "auto",  # "auto", "fastf1", "livef1", "live", "replay"
        year: int = None,
        gp: str = None,
        session_type: str = None,
        replay_file: str = None,
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

        if source in ("auto", "fastf1"):
            return self._load_fastf1_session(year, gp, session_type, telemetry_scope)

        if source == "livef1":
            return self._load_livef1_session(year, gp, session_type)

        if source == "live":
            return self._load_live_session()

        raise ValueError(f"Unknown source: {source}")

    def _is_race_weekend(self) -> bool:
        """Check if there's an active F1 session this weekend."""
        try:
            return self.jolpica.is_race_weekend()
        except Exception:
            return False

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

        return {
            "session_info": {
                "year": year,
                "gp": gp,
                "session_type": session_type,
                "session_name": session.name,
                "date": session.date,
                "telemetry_scope": telemetry_scope,
            },
            "telemetry": telemetry,
            "laps": self.fastf1.get_laps(session),
            "stints": self.fastf1.get_stints(session),
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
    CIRCUIT_MAP = {
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

        car_df = LiveDataProcessor.parse_car_data(adapter.get_buffered_data("CarData.z"))
        pos_df = LiveDataProcessor.parse_position_data(adapter.get_buffered_data("Position.z"))
        timing_df = LiveDataProcessor.parse_timing_data(adapter.get_buffered_data("TimingData"))
        weather_df = LiveDataProcessor.parse_weather_data(adapter.get_buffered_data("WeatherData"))
        stints_df = LiveDataProcessor.parse_tyre_stints(
            adapter.get_buffered_data("TyreStintSeries")
        )
        drivers_df = LiveDataProcessor.parse_driver_list(adapter.get_buffered_data("DriverList"))

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
            acr_by_num = dict(zip(drivers_df["driver_number"], drivers_df["name_acronym"]))

        # --- per-driver GPS trails for the track map (with real distances) ---
        location: Dict[str, pd.DataFrame] = {}
        pos_distance_by_num: Dict[Any, pd.DataFrame] = {}
        if not pos_df.empty:
            pos_df = pos_df.dropna(subset=["X", "Y"])
            for num, grp in pos_df.groupby("driver_number"):
                name = acr_by_num.get(num, f"#{num}")
                d = grp.sort_values("timestamp").tail(3000).reset_index(drop=True)
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
        telemetry: Dict[str, pd.DataFrame] = {}
        if not car_df.empty:
            car_df = car_df.dropna(subset=["Speed"])
            for num, grp in car_df.groupby("driver_number"):
                name = acr_by_num.get(num, f"#{num}")
                d = grp.sort_values("timestamp").tail(2000).reset_index(drop=True).copy()
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

        # --- laps from timing feed (true lap numbers via NumberOfLaps) ---
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

        return {
            "session_info": {
                **info,
                "is_live": True,
                "source": "signalr_live",
                "track_status": track_status,
            },
            "telemetry": telemetry,
            "laps": laps_df,
            "stints": stints_df,
            "location": location,
            "weather": weather_df,
            "race_control": race_control_df,
            "compound_colors": {},
            "circuit_info": {},
            "drivers": drivers_df,
            "source": "live",
            "is_live": True,
        }

    @staticmethod
    def _laps_from_timing(timing_df: pd.DataFrame, acr_by_num: Dict) -> pd.DataFrame:
        """Build a laps DataFrame from TimingData records with real lap
        numbers.

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
            completed: Dict[int, str] = {}
            current_lap = 0
            latest = grp.iloc[-1]
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
        info: Dict[str, Any] = {
            "gp": "Live Session",
            "session_type": "",
            "session_name": "",
            "year": None,
            "circuit_key": None,
            "gmt_offset": "",
        }
        si = adapter.get_latest_data("SessionInfo") or {}

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

        status = adapter.get_latest_data("SessionStatus") or {}
        info["status"] = status.get("Status", "")
        return info

    def _get_most_recent_completed_race(self) -> dict:
        """Find most recent completed race from FastF1 schedule."""
        schedule = self.fastf1.get_available_sessions()
        if schedule.empty:
            return {"year": 2024, "gp": "Abu Dhabi", "session_type": "R"}
        last_event = schedule.iloc[-1]
        return {"year": int(last_event["Year"]), "gp": last_event["EventName"], "session_type": "R"}

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
    # (corner markers + track rotation). Older replays simply lack those keys
    # and load with empty defaults.
    REPLAY_SCHEMA_VERSION = 3

    def save_replay(self, data: dict, name: str) -> str:
        """Save session data for offline replay.

        Files carry a schema header (``schema``/``saved_at``/``data``) so
        future format changes can be detected and old files keep loading.
        """
        filepath = self.replay_dir / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.pkl"
        save_data = {}
        for k, v in data.items():
            if k in ("telemetry", "location"):
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
            "saved_at": datetime.now().isoformat(),
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
            payload = pickle.load(f)

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
        for k in ["laps", "stints", "weather", "drivers", "race_control"]:
            if k in data:
                data[k] = pd.DataFrame(data[k])
        data["telemetry"] = {k: pd.DataFrame(v) for k, v in data.get("telemetry", {}).items()}
        data["location"] = {k: pd.DataFrame(v) for k, v in data.get("location", {}).items()}
        data["source"] = "replay"
        return data

    def get_available_replays(self) -> list:
        """List available replay files."""
        files = list(self.replay_dir.glob("*.pkl"))
        return sorted([f.name for f in files], reverse=True)
