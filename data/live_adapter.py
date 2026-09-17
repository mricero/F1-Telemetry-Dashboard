"""Live Telemetry Adapter - FREE SignalR connection to official F1 feed.

Message flow
------------
LiveF1's RealF1Client runs each incoming SignalR message through its own
``function_map`` parser BEFORE invoking registered callbacks. Therefore the
records buffered by :class:`SignalRLiveAdapter` are already flat dicts whose
key names follow LiveF1's conventions:

* ``CarData.z``   -> DriverNo, Utc, rpm, speed, n_gear, throttle, brake, drs
* ``Position.z``  -> DriverNo, Utc, X, Y, Z
* ``TimingData``  -> DriverNo, Position, BestLapTimeValue, Sectors_1_Value, ...
* ``TyreStintSeries`` -> DriverNo, PitCount, Compound, ...
* ``WeatherData`` -> AirTemp, TrackTemp, Humidity, WindSpeed, Rainfall, ...
* ``DriverList``  -> RacingNumber, Tla, TeamColour, FirstName, LastName, ...

The compressed payload format (base64 of raw-DEFLATE JSON) is handled by
:func:`decode_zipped` / :func:`decode_topic_payload` for replaying
FastF1-style raw recordings.
"""

import asyncio
import base64
import os
import json
import threading
import zlib
import numpy as np
import pandas as pd
from typing import Dict, List, Callable, Optional, Any, Sequence
from collections import defaultdict

# Position.z shares FastF1's 1/10 m position units - one definition, both paths.
from data.fastf1_adapter import POSITION_UNITS_PER_METRE
from data.live_state import STATE_TOPICS, LiveState, as_list


def decode_zipped(text: str) -> Any:
    """Decode an F1 SignalR zipped payload: base64 -> raw-deflate -> JSON.

    The official feed compresses with raw DEFLATE (no zlib header), matching
    LiveF1's ``parse(text, zipped=True)`` so recordings captured by FastF1's
    SignalRClient replay through the same pipeline.
    """
    if not text:
        raise ValueError("Cannot decode an empty SignalR payload")
    if text[0] == "{":
        return json.loads(text)
    if text[0] == '"':
        text = text.strip('"')
    raw = zlib.decompress(base64.b64decode(text), -zlib.MAX_WBITS)
    return json.loads(raw.decode("utf-8-sig"))


def _normalize_channels(channels: Any) -> Dict[str, Any]:
    """Channels arrive as {id: value} dicts; tolerate positional lists."""
    if isinstance(channels, dict):
        return {str(k): v for k, v in channels.items()}
    if isinstance(channels, (list, tuple)):
        ids = sorted(CAR_CHANNELS.keys(), key=int)
        return {ids[i]: v for i, v in enumerate(channels) if i < len(ids)}
    return {}


def decode_topic_payload(topic: str, payload: Any) -> List[Dict]:
    """Decode one raw topic payload into flat record dicts.

    Supports CarData.z and Position.z shapes: ``[(timestamp, b64str), ...]``
    or a single b64 string.
    """
    items = payload if isinstance(payload, list) else [(None, payload)]
    records: List[Dict] = []
    for ts, value in items:
        decoded = value if isinstance(value, dict) else decode_zipped(value)
        if topic == "CarData.z":
            for entry in decoded.get("Entries", []):
                utc = entry.get("Utc")
                for driver_no, car in entry.get("Cars", {}).items():
                    channels = _normalize_channels(car.get("Channels", {}))
                    records.append(
                        {
                            "DriverNo": driver_no,
                            "Utc": utc,
                            "timestamp": ts,
                            "rpm": channels.get("0"),
                            "speed": channels.get("2"),
                            "n_gear": channels.get("3"),
                            "throttle": channels.get("4"),
                            "brake": channels.get("5"),
                            "drs": channels.get("45"),
                        }
                    )
        elif topic == "Position.z":
            for position_entry in decoded.get("Position", []):
                utc = position_entry.get("Timestamp")
                for driver_no, pos in position_entry.get("Entries", {}).items():
                    records.append(
                        {
                            "DriverNo": driver_no,
                            "Utc": utc,
                            "timestamp": ts,
                            **pos,
                        }
                    )
        else:
            raise ValueError(f"No raw decoder for topic: {topic}")
    return records


def _as_bool(value: Any) -> Optional[bool]:
    """The feed sends booleans as "true"/"false" strings as often as bools."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


# Official CarData.z channel ids (verified against LiveF1 channel_name_map)
CAR_CHANNELS = {"0": "rpm", "2": "speed", "3": "n_gear", "4": "throttle", "5": "brake", "45": "drs"}


# Topics F1 has gated behind an F1TV subscription token since the 2025 Dutch
# GP. Without a token they never produce data, so subscribing to them only
# adds noise - and the panels that do not need one must still render (LIVE-02).
AUTH_TOPICS = frozenset(
    {
        "CarData.z",
        "Position.z",
        "PitStopSeries",
        "ChampionshipPrediction",
        "DriverRaceInfo",
        "TeamRadio",
    }
)

# Where the user's own token is read from. It stays on their machine: the
# sustainable mode for this feed is local, single-connection, own-token use.
TOKEN_ENV_VAR = "F1TV_SUBSCRIPTION_TOKEN"


def subscription_token() -> Optional[str]:
    """The configured F1TV subscription token, or None."""
    token = os.getenv(TOKEN_ENV_VAR, "").strip()
    return token or None


class SignalRLiveAdapter:
    """
    FREE live telemetry via SignalR - connects directly to F1 official feed.
    Two implementations available:
    1. LiveF1 package: livef1.adapters.RealF1Client (async callbacks)
    2. FastF1 built-in: fastf1.livetiming.SignalRClient (saves raw stream to file)
    Both use: wss://livetiming.formula1.com/signalrcore

    NOTE: all topics below have dedicated parsers inside LiveF1's function_map;
    subscribing to unknown topics would raise ParsingError on every message.
    """

    # Topics to subscribe for telemetry dashboard
    TELEMETRY_TOPICS = [
        "CarData.z",  # Speed/Throttle/Brake/RPM/Gear/DRS (~50Hz? ~3.7Hz aggregated)
        "Position.z",  # GPS position X,Y,Z
        "TimingData",  # Lap times, sectors, gaps
        "WeatherData",  # Track temp, air temp, humidity, wind, rain
        "RaceControlMessages",  # Flags, SC, incidents
        "TrackStatus",  # Track conditions (yellow, green, red)
        "SessionInfo",  # Session metadata
        "SessionStatus",  # Session state (racing, stopped, etc.)
        "DriverList",  # Driver info (numbers, names, teams)
        "LapSeries",  # Lap data stream
        "CurrentTyres",  # Current tyre compounds
        "PitLaneTimeCollection",  # Pit lane timing
        "TyreStintSeries",  # Tyre stint data
    ]

    def __init__(self, use_livef1: bool = True, buffer_limit: int = 20000):
        """
        Args:
            use_livef1: If True, use LiveF1 RealF1Client (async callbacks).
                       If False, use FastF1 SignalRClient (file-based).
            buffer_limit: Max records kept per topic. Oldest records are
                dropped first, bounding memory during long sessions
                (~20k CarData messages ≈ several minutes of full-grid data;
                parsers only ever need the tail).
        """
        self.use_livef1 = use_livef1
        self.buffer_limit = max(int(buffer_limit), 100)
        self.client = None
        # Merged per-topic state for keyframe+delta topics (LIVE-05). Time
        # series still go to _data_buffer.
        self.state = LiveState()
        # Lap completions are a true time series and must outlive the telemetry
        # cap: losing them would erase the first half of a race from the lap
        # chart. Kept separately, and only one small row per completed lap.
        self.lap_history: List[Dict] = []
        self._lap_counter: Dict[str, int] = {}
        self._data_buffer: Dict[str, List[Dict]] = defaultdict(list)
        # The client runs on its own thread while Streamlit polls from the
        # script thread: without this, trimming a buffer shifted the reader's
        # slice underneath it and records were skipped or duplicated.
        self._buffer_lock = threading.RLock()
        # Monotonic count of buffered records. Buffer *lengths* stop changing
        # once a topic hits its cap, so they cannot signal new data.
        self._ingested = 0
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._thread_error: Optional[BaseException] = None

    def handle_message(self, topic: str, payload: Any, timestamp: Optional[str] = None) -> None:
        """Ingest one raw feed message.

        State topics are deep-merged into :attr:`state`; time series are
        appended to the bounded buffers. This is the entry point a SignalR
        Core client (LIVE-01) and the fixture replay both use - the legacy
        livef1 callback path still delivers pre-parsed records to
        :meth:`_buffer_topic`.
        """
        if topic in STATE_TOPICS:
            if topic == "TimingData":
                self._record_lap_progress(payload)
            self.state.update(topic, payload)
            return
        self._buffer_topic(topic, payload)

    def _record_lap_progress(self, payload: Any) -> None:
        """Note completed laps as TimingData messages arrive.

        ``NumberOfLaps`` and ``LastLapTime`` usually come in separate partial
        messages, so the lap counter is tracked per driver and a lap time is
        attributed to whatever lap the driver had reached.
        """
        if not isinstance(payload, dict):
            return
        for number, line in (payload.get("Lines") or {}).items():
            if not isinstance(line, dict):
                continue
            driver = str(number)
            laps = line.get("NumberOfLaps")
            if laps is not None:
                try:
                    self._lap_counter[driver] = int(laps)
                except (TypeError, ValueError):
                    pass

            last_lap = line.get("LastLapTime")
            value = last_lap.get("Value") if isinstance(last_lap, dict) else None
            lap_number = self._lap_counter.get(driver)
            if not value or not lap_number:
                continue
            with self._buffer_lock:
                recent = self.lap_history[-40:]
            if any(
                entry["driver_number"] == driver and entry["LapNumber"] == lap_number
                for entry in recent
            ):
                continue  # the same completion repeated in a later message
            with self._buffer_lock:
                self.lap_history.append(
                    {"driver_number": driver, "LapNumber": lap_number, "LapTime": value}
                )

    def seed_state(self, snapshot: Dict[str, Any]) -> None:
        """Apply a subscription snapshot: ``{topic: full_state}``."""
        self.state.seed(snapshot)

    def subscribed_topics(self) -> List[str]:
        """Topics worth subscribing to, given whether a token is configured.

        Auth-gated topics are dropped without one: they would never deliver
        anything, and the rest of the feed works perfectly well alone.
        """
        if subscription_token():
            return list(self.TELEMETRY_TOPICS)
        return [topic for topic in self.TELEMETRY_TOPICS if topic not in AUTH_TOPICS]

    def _buffer_topic(self, topic: str, records: Any):
        """Append parsed records to a topic buffer, dropping the oldest
        entries beyond ``buffer_limit`` to keep memory bounded."""
        with self._buffer_lock:
            buf = self._data_buffer[topic]
            if isinstance(records, list):
                buf.extend(records)
                self._ingested += len(records)
            else:
                buf.append(records)
                self._ingested += 1
            overflow = len(buf) - self.buffer_limit
            if overflow > 0:
                del buf[:overflow]

    def start_livef1_client(self, topics: List[str] = None, log_file: str = None):
        """Start LiveF1 RealF1Client with async callbacks.

        NOTE: RealF1Client.run() manages its own event loop (asyncio.run)
        and blocks until interrupted, so this must NOT be called from
        within a running event loop. Use start_async() to run it in a
        background thread.
        """
        from livef1.adapters import RealF1Client

        topics = topics or self.subscribed_topics()
        self.client = RealF1Client(topics=topics, log_file_name=log_file)
        self._running = True

        # Register callback for all topics.
        # records is a dict of {topic: [parsed_record, ...]}
        @self.client.callback("telemetry_handler")
        async def handle_data(records):
            for topic, data in records.items():
                self._buffer_topic(topic, data)

                # Call registered callbacks
                for cb in self._callbacks.get(topic, []):
                    if asyncio.iscoroutinefunction(cb):
                        await cb(data)
                    else:
                        cb(data)

        # Blocks; RealF1Client creates and owns its own event loop.
        self.client.run()
        self._running = False

    def start_fastf1_client(self, filename: str, topics: List[str] = None, timeout: int = 60):
        """Start FastF1 SignalRClient - saves raw stream to file."""
        from fastf1.livetiming.client import SignalRClient

        topics = topics or self.subscribed_topics()
        self.client = SignalRClient(filename=filename, filemode="w", timeout=timeout, no_auth=False)
        self._running = True
        self.client.start()  # Blocks

    def start_async(self, topics: List[str] = None, log_file: str = None):
        """Start live client in background thread.

        RealF1Client.run() creates its own event loop internally, so the
        background thread must run it directly (no outer asyncio loop).
        """
        if self._running:
            return

        self._running = True
        self._thread_error = None

        def run_client():
            try:
                self.start_livef1_client(topics, log_file)
            except Exception as exc:
                self._running = False
                # Keep a reference for debugging; Streamlit threads are daemonic
                self._thread_error = exc

        self._thread = threading.Thread(target=run_client, daemon=True)
        self._thread.start()

    def register_callback(self, topic: str, callback: Callable):
        """Register callback for a topic."""
        self._callbacks[topic].append(callback)

    def change_token(self) -> tuple:
        """A cheap value that changes whenever ingested data changes.

        Lets a reader skip rebuilding a snapshot when nothing has arrived
        since the last poll - a 3 s fragment otherwise reprocesses the whole
        buffer for an identical result (LIVE-11).
        """
        with self._buffer_lock:
            ingested, laps = self._ingested, len(self.lap_history)
        return (self.state.version, laps, ingested)

    def recorded_laps(self) -> List[Dict]:
        """A snapshot copy of the recorded lap completions."""
        with self._buffer_lock:
            return list(self.lap_history)

    def get_buffered_data(self, topic: str) -> List[Dict]:
        """A snapshot copy of a topic's buffer.

        A copy, not the live list: the client thread keeps appending and
        trimming, and a reader iterating the real list would see records shift
        under it.
        """
        with self._buffer_lock:
            return list(self._data_buffer.get(topic, []))

    def get_latest_data(self, topic: str) -> Optional[Dict]:
        """Get most recent record for a topic."""
        with self._buffer_lock:
            data = self._data_buffer.get(topic, [])
            return data[-1] if data else None

    def clear_buffer(self, topic: str = None):
        """Clear buffered data."""
        with self._buffer_lock:
            if topic:
                self._data_buffer[topic] = []
            else:
                self._data_buffer.clear()
                self.state.clear()
                self.lap_history.clear()
                self._lap_counter.clear()
                self._ingested += 1  # a clear is a change like any other

    def is_running(self) -> bool:
        """Check if client is running."""
        return self._running

    def last_error(self) -> Optional[BaseException]:
        return self._thread_error

    def stop(self):
        """Stop the client."""
        self._running = False
        if self.client and hasattr(self.client, "stop"):
            self.client.stop()
        if self._thread:
            self._thread.join(timeout=5)


class LiveDataProcessor:
    """Process parsed SignalR records into structured DataFrames.

    Input records are the already-parsed dicts emitted by LiveF1's
    MessageHandlerTemplate (see module docstring).
    """

    # CarData.z field -> unified channel name.
    CAR_CHANNEL_COLUMNS = {
        "rpm": "RPM",
        "speed": "Speed",
        "n_gear": "nGear",
        "throttle": "Throttle",
        "brake": "Brake",
        "drs": "DRS",
    }

    @staticmethod
    def parse_car_data(raw_records: List[Dict]) -> pd.DataFrame:
        """CarData.z records -> DataFrame[driver_number, timestamp,
        RPM, Speed, nGear, Throttle, Brake, DRS].

        Built as whole columns: converting each field per record cost one
        ``pd.to_numeric`` call per channel per sample, which dominated the
        live poll at buffer cap (LIVE-11).
        """
        records = raw_records or []
        if not records:
            return pd.DataFrame()
        frame = pd.DataFrame(records)
        out = pd.DataFrame(
            {
                "driver_number": frame.get("DriverNo"),
                "timestamp": LiveDataProcessor._timestamp_column(frame),
            }
        )
        for source, column in LiveDataProcessor.CAR_CHANNEL_COLUMNS.items():
            out[column] = (
                pd.to_numeric(frame[source], errors="coerce")
                if source in frame.columns
                else pd.Series(index=frame.index, dtype="float64")
            )
        return out

    @staticmethod
    def parsed_timestamps(values: Sequence) -> pd.Series:
        """Feed timestamps as UTC datetimes, parsed at most once.

        A series that is already datetime64 is passed straight through: the
        poll parses each frame once and then hands the same column to every
        per-driver call, instead of re-parsing 20 000 ISO strings per driver
        (LIVE-11).
        """
        series = values if isinstance(values, pd.Series) else pd.Series(list(values))
        if pd.api.types.is_datetime64_any_dtype(series):
            return series
        return pd.to_datetime(series, utc=True, format="ISO8601", errors="coerce")

    @staticmethod
    def _timestamp_column(frame: pd.DataFrame) -> pd.Series:
        """``Utc`` when present, else the wire ``timestamp``."""
        if "Utc" in frame.columns:
            if "timestamp" in frame.columns:
                return frame["Utc"].fillna(frame["timestamp"])
            return frame["Utc"]
        if "timestamp" in frame.columns:
            return frame["timestamp"]
        return pd.Series(index=frame.index, dtype="object")

    @staticmethod
    def parse_position_data(raw_records: List[Dict]) -> pd.DataFrame:
        """Position.z records -> DataFrame[driver_number, timestamp, X, Y, Z].

        Samples the car is not actually on track are dropped: entries carry a
        ``Status`` (``OnTrack``/``OffTrack``) and a car in the garage reports
        ``0,0,0``. Keeping them drew a straight line to the origin across the
        map and added a circuit's width to the distance on every pit stop.
        """
        records = raw_records or []
        if not records:
            return pd.DataFrame()
        frame = pd.DataFrame(records)
        out = pd.DataFrame(
            {
                "driver_number": frame.get("DriverNo"),
                "timestamp": LiveDataProcessor._timestamp_column(frame),
            }
        )
        for axis in ("X", "Y", "Z"):
            out[axis] = (
                pd.to_numeric(frame[axis], errors="coerce")
                if axis in frame.columns
                else pd.Series(index=frame.index, dtype="float64")
            )

        keep = pd.Series(True, index=out.index)
        if "Status" in frame.columns:
            status = frame["Status"]
            keep &= status.isna() | (status.astype(str) == "OnTrack")
        garage = (out["X"] == 0) & (out["Y"] == 0) & (out["Z"].isna() | (out["Z"] == 0))
        return out[keep & ~garage].reset_index(drop=True)

    @staticmethod
    def parse_timing_data(raw_records: List[Dict]) -> pd.DataFrame:
        """TimingData records -> one row per driver with flattened fields
        (Position, BestLapTimeValue, Sectors_1_Value, ...)."""
        rows = []
        for r in raw_records or []:
            row = {
                "driver_number": r.get("DriverNo"),
                "timestamp": r.get("timestamp"),
            }
            for k, v in r.items():
                if k in ("SessionKey", "timestamp"):
                    continue
                row[k] = v
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def parse_weather_data(raw_records: List[Dict]) -> pd.DataFrame:
        """WeatherData records -> DataFrame of weather observations."""
        rows = []
        for r in raw_records or []:
            row = {"timestamp": r.get("timestamp")}
            row.update({k: v for k, v in r.items() if k not in ("SessionKey", "timestamp")})
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def parse_tyre_stints(raw_records: List[Dict]) -> pd.DataFrame:
        """TyreStintSeries records -> stints DataFrame compatible with the
        tire strategy chart (Compound, LapStart/LapEnd when available)."""
        rows = {}
        for r in raw_records or []:
            driver = r.get("DriverNo")
            pit = r.get("PitCount")
            compound = r.get("Compound")
            if driver is None or not compound:
                continue
            key = (driver, pit)
            rows[key] = {
                "DriverAcronym": driver,
                "Stint": pit,
                "Compound": compound,
                "LapStart": (
                    r.get("LapStart")
                    if r.get("LapStart") is not None
                    else rows.get(key, {}).get("LapStart")
                ),
                "LapEnd": (
                    r.get("LapEnd")
                    if r.get("LapEnd") is not None
                    else rows.get(key, {}).get("LapEnd")
                ),
            }
        df = pd.DataFrame(list(rows.values()))
        if not df.empty:
            for col in ("LapStart", "LapEnd"):
                if col not in df.columns:
                    df[col] = None
        return df

    # -- state-derived frames (LIVE-05) ---------------------------------
    #
    # These read the *merged* LiveState rather than a list of messages, so a
    # field set once in the keyframe survives and a partial update lands on
    # the entry it belongs to.

    @staticmethod
    def timing_from_state(timing_state: Dict) -> pd.DataFrame:
        """``TimingData`` state -> one row per driver, current values.

        ``Sectors``, ``Segments`` and ``Speeds`` are index-addressed, so they
        are normalised back to ordered lists before flattening: in a delta,
        key ``"1"`` is sector **2**.
        """
        rows = []
        for number, line in (timing_state or {}).get("Lines", {}).items():
            if not isinstance(line, dict):
                continue
            row: Dict[str, Any] = {"driver_number": str(number)}
            for key, value in line.items():
                if key in ("Sectors", "Speeds"):
                    for index, entry in enumerate(as_list(value), start=1):
                        if isinstance(entry, dict):
                            for field, inner in entry.items():
                                if field == "Segments":
                                    continue
                                row[f"{key}_{index}_{field}"] = inner
                elif isinstance(value, dict):
                    for field, inner in value.items():
                        if not isinstance(inner, (dict, list)):
                            row[f"{key}_{field}"] = inner
                elif not isinstance(value, list):
                    row[key] = value
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def stints_from_state(
        stint_state: Dict, acronyms: Optional[Dict[str, str]] = None
    ) -> pd.DataFrame:
        """``TyreStintSeries`` state -> the tyre strategy chart's frame.

        The feed has no LapStart/LapEnd: it reports ``TotalLaps`` (the tyre's
        age) and ``StartLaps`` (laps already on it when fitted), so the lap
        window is accumulated across a driver's stints. Rows are keyed by the
        driver's acronym, which is what the colour maps and charts use - the
        racing number was never going to match them.
        """
        acronyms = acronyms or {}
        rows = []
        for number, stints in (stint_state or {}).get("Stints", {}).items():
            lap_cursor = 1
            for index, stint in enumerate(as_list(stints), start=1):
                if not isinstance(stint, dict):
                    continue
                compound = stint.get("Compound")
                total = pd.to_numeric(pd.Series([stint.get("TotalLaps")]), errors="coerce").iloc[0]
                start = pd.to_numeric(pd.Series([stint.get("StartLaps")]), errors="coerce").iloc[0]
                # TotalLaps counts the tyre's age; laps run this stint is the
                # part of that age accumulated since it was fitted.
                laps_this_stint = 0
                if pd.notna(total):
                    laps_this_stint = int(total) - (int(start) if pd.notna(start) else 0)
                acronym = acronyms.get(str(number), str(number))
                rows.append(
                    {
                        "Driver": acronym,
                        "DriverAcronym": acronym,
                        "driver_number": str(number),
                        "Stint": index,
                        "Compound": str(compound).upper() if compound else None,
                        "New": _as_bool(stint.get("New")),
                        "TyreAge": int(total) if pd.notna(total) else None,
                        "LapStart": lap_cursor,
                        # A 10-lap stint starting at lap 1 ends at lap 10.
                        "LapEnd": lap_cursor + max(laps_this_stint - 1, 0),
                        "LapCount": max(laps_this_stint, 0),
                    }
                )
                lap_cursor += max(laps_this_stint, 0)
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame = frame.dropna(subset=["Compound"]).reset_index(drop=True)
        return frame

    @staticmethod
    def laps_from_history(
        lap_history: Sequence[Dict],
        timing_state: Optional[Dict] = None,
        acronyms: Optional[Dict[str, str]] = None,
    ) -> pd.DataFrame:
        """Completed laps (plus the lap in progress) for the unified dict.

        Lap times come from the recorded completions, not from re-reading a
        buffer of messages, so the start of a long race survives. Sector
        times and the driver-state flags are the *current* values from the
        merged state.
        """
        acronyms = acronyms or {}
        lines = (timing_state or {}).get("Lines", {})
        rows = []
        seen_drivers = set()

        for entry in lap_history or []:
            driver = str(entry.get("driver_number"))
            seen_drivers.add(driver)
            rows.append(
                {
                    "Driver": acronyms.get(driver, f"#{driver}"),
                    "driver_number": driver,
                    "LapNumber": entry.get("LapNumber"),
                    "LapTime": entry.get("LapTime"),
                    "IsPitOutLap": False,
                    "IsInProgress": False,
                }
            )

        for number, line in lines.items():
            driver = str(number)
            if not isinstance(line, dict):
                continue
            flags = {
                "InPit": _as_bool(line.get("InPit")) or False,
                "PitOut": _as_bool(line.get("PitOut")) or False,
                "Retired": _as_bool(line.get("Retired")) or False,
                "Stopped": _as_bool(line.get("Stopped")) or False,
            }
            sectors = {}
            for index, sector in enumerate(as_list(line.get("Sectors")), start=1):
                if isinstance(sector, dict):
                    sectors[f"Sector{index}Time"] = sector.get("Value") or None

            completed = pd.to_numeric(pd.Series([line.get("NumberOfLaps")]), errors="coerce").iloc[
                0
            ]
            current_lap = int(completed) + 1 if pd.notna(completed) else 1
            rows.append(
                {
                    "Driver": acronyms.get(driver, f"#{driver}"),
                    "driver_number": driver,
                    "LapNumber": current_lap,
                    "LapTime": None,
                    "IsPitOutLap": False,
                    "IsInProgress": True,
                    **sectors,
                    **flags,
                }
            )
            seen_drivers.add(driver)

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        # The state flags describe the driver, not one lap: apply them to all
        # of that driver's rows so the tower latches retirement correctly.
        for column in ("InPit", "PitOut", "Retired", "Stopped"):
            if column in frame.columns:
                frame[column] = frame.groupby("driver_number")[column].transform(
                    lambda values: values.ffill().bfill()
                )
        return frame.sort_values(["Driver", "LapNumber"]).reset_index(drop=True)

    @staticmethod
    def drivers_from_state(driver_state: Dict) -> pd.DataFrame:
        """``DriverList`` state -> the unified drivers table.

        Reads the merged entry, so colours and names that arrived in a later
        message are not lost to a first-seen-wins rule.
        """
        rows = []
        for number, entry in (driver_state or {}).items():
            if not isinstance(entry, dict) or not str(number).isdigit():
                continue
            first = entry.get("FirstName") or ""
            last = entry.get("LastName") or ""
            full = entry.get("FullName") or f"{first} {last}".strip()
            colour = entry.get("TeamColour") or "888888"
            rows.append(
                {
                    "driver_number": str(entry.get("RacingNumber") or number),
                    "name_acronym": entry.get("Tla") or str(last)[:3].upper(),
                    "team_colour": str(colour) if str(colour).startswith("#") else f"#{colour}",
                    "team_name": entry.get("TeamName", ""),
                    "full_name": full,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def acronyms_from_state(driver_state: Dict) -> Dict[str, str]:
        """Racing number -> three-letter acronym, from merged DriverList."""
        mapping = {}
        for number, entry in (driver_state or {}).items():
            if isinstance(entry, dict) and entry.get("Tla"):
                mapping[str(number)] = str(entry["Tla"])
        return mapping

    @staticmethod
    def parse_race_control(raw_records: List[Dict]) -> pd.DataFrame:
        """RaceControlMessages records -> flags/SC/incident feed.

        Mirrors the FastF1 historical shape (``Time``/``Lap``/``Category``/
        ``Flag``/``Scope``/``Message``) so one renderer serves both sources.
        """
        rows = []
        for r in raw_records or []:
            message = r.get("Message") or r.get("message")
            if not message:
                continue
            rows.append(
                {
                    "Time": r.get("Utc") or r.get("timestamp"),
                    "Lap": r.get("Lap"),
                    "Category": r.get("Category"),
                    "Flag": r.get("Flag"),
                    "Scope": r.get("Scope"),
                    "Message": message,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def parse_track_status(raw_records: List[Dict]) -> Optional[Dict]:
        """Latest TrackStatus record -> ``{'status': code, 'message': text}``.

        Status codes follow the official feed: 1 green, 2 yellow, 4 safety
        car, 5 red, 6 VSC deployed, 7 VSC ending.
        """
        for r in reversed(raw_records or []):
            status = r.get("Status")
            if status is None:
                continue
            return {"status": str(status), "message": r.get("Message") or ""}
        return None

    @staticmethod
    def parse_driver_list(raw_records: List[Dict]) -> pd.DataFrame:
        """DriverList records -> drivers table matching the unified schema."""
        rows = []
        seen = set()
        for r in raw_records or []:
            number = r.get("RacingNumber", r.get("DriverNo"))
            if number is None or number in seen:
                continue
            seen.add(number)
            first = r.get("FirstName") or ""
            last = r.get("LastName") or r.get("FullName") or ""
            colour = r.get("TeamColour") or "#888888"
            if not str(colour).startswith("#"):
                colour = f"#{colour}"
            rows.append(
                {
                    "driver_number": number,
                    "name_acronym": r.get("Tla") or r.get("name_acronym") or last[:3].upper(),
                    "team_colour": colour,
                    "team_name": r.get("TeamName", ""),
                    "full_name": f"{first} {last}".strip(),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def distance_at(pos_df: pd.DataFrame, car_timestamps: Sequence) -> Optional[np.ndarray]:
        """Interpolate travelled track distance (meters) at given timestamps.

        The distance is the cumulative Euclidean arc length of the driver's
        own GPS trajectory (Position.z X/Y), i.e. real meters driven - a far
        better comparison axis than an index-based pseudo-distance.

        ``Position.z`` reports X/Y/Z in 1/10 m - the same feed and the same
        units FastF1 parses - so the arc length is divided by
        :data:`~data.fastf1_adapter.POSITION_UNITS_PER_METRE`.

        Returns None when there is not enough usable GPS data.
        """
        if pos_df is None or len(pos_df) < 3 or car_timestamps is None:
            return None
        t = LiveDataProcessor.parsed_timestamps(pos_df["timestamp"])
        ok = t.notna() & pos_df[["X", "Y"]].notna().all(axis=1)
        if int(ok.sum()) < 3:
            return None
        t_num = t[ok].astype("int64").to_numpy().astype(float)
        order = np.argsort(t_num, kind="stable")
        t_num = t_num[order]
        xy = pos_df.loc[ok, ["X", "Y"]].to_numpy(dtype=float)[order]
        seg = np.hypot(*np.diff(xy, axis=0).T)
        dist = np.concatenate([[0.0], np.cumsum(seg)]) / POSITION_UNITS_PER_METRE

        ct = LiveDataProcessor.parsed_timestamps(car_timestamps)
        ct_num = ct.astype("int64").to_numpy().astype(float)  # NaT -> huge negative (clamps left)
        result = np.interp(ct_num, t_num, dist)
        result[ct.isna().to_numpy()] = np.nan
        return result


def check_live_session_available() -> bool:
    """Check if there's likely a live F1 session running."""
    try:
        from data.jolpica_adapter import JolpicaAdapter

        return JolpicaAdapter().is_race_weekend()
    except Exception:
        # No connectivity or schedule unavailable - assume no live session
        return False


if __name__ == "__main__":
    import time
    import pandas as pd

    adapter = SignalRLiveAdapter(use_livef1=True)

    def on_car_data(data):
        print(f"CarData: {len(data)} records")

    adapter.register_callback("CarData.z", on_car_data)
    adapter.start_async(topics=["CarData.z", "Position.z"], log_file="test_session.json")

    time.sleep(10)

    print(f"Buffered CarData: {len(adapter.get_buffered_data('CarData.z'))}")
    print(f"Buffered Position: {len(adapter.get_buffered_data('Position.z'))}")

    processor = LiveDataProcessor()
    df = processor.parse_car_data(adapter.get_buffered_data("CarData.z"))
    print(df.tail())

    adapter.stop()
