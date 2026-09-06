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
import json
import threading
import zlib
import numpy as np
import pandas as pd
from typing import Dict, List, Callable, Optional, Any, Sequence
from collections import defaultdict


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


# Official CarData.z channel ids (verified against LiveF1 channel_name_map)
CAR_CHANNELS = {"0": "rpm", "2": "speed", "3": "n_gear", "4": "throttle", "5": "brake", "45": "drs"}


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
        self._data_buffer: Dict[str, List[Dict]] = defaultdict(list)
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._thread_error: Optional[BaseException] = None

    def _buffer_topic(self, topic: str, records: Any):
        """Append parsed records to a topic buffer, dropping the oldest
        entries beyond ``buffer_limit`` to keep memory bounded."""
        buf = self._data_buffer[topic]
        if isinstance(records, list):
            buf.extend(records)
        else:
            buf.append(records)
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

        topics = topics or self.TELEMETRY_TOPICS
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

        topics = topics or self.TELEMETRY_TOPICS
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

    def get_buffered_data(self, topic: str) -> List[Dict]:
        """Get buffered data for a topic."""
        return self._data_buffer.get(topic, [])

    def get_latest_data(self, topic: str) -> Optional[Dict]:
        """Get most recent record for a topic."""
        data = self._data_buffer.get(topic, [])
        return data[-1] if data else None

    def clear_buffer(self, topic: str = None):
        """Clear buffered data."""
        if topic:
            self._data_buffer[topic] = []
        else:
            self._data_buffer.clear()

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

    @staticmethod
    def parse_car_data(raw_records: List[Dict]) -> pd.DataFrame:
        """CarData.z records -> DataFrame[driver_number, timestamp,
        RPM, Speed, nGear, Throttle, Brake, DRS]."""
        rows = []
        for r in raw_records or []:
            rows.append(
                {
                    "driver_number": r.get("DriverNo"),
                    "timestamp": r.get("Utc", r.get("timestamp")),
                    "RPM": pd.to_numeric(r.get("rpm"), errors="coerce"),
                    "Speed": pd.to_numeric(r.get("speed"), errors="coerce"),
                    "nGear": pd.to_numeric(r.get("n_gear"), errors="coerce"),
                    "Throttle": pd.to_numeric(r.get("throttle"), errors="coerce"),
                    "Brake": pd.to_numeric(r.get("brake"), errors="coerce"),
                    "DRS": pd.to_numeric(r.get("drs"), errors="coerce"),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def parse_position_data(raw_records: List[Dict]) -> pd.DataFrame:
        """Position.z records -> DataFrame[driver_number, timestamp, X, Y, Z]."""
        rows = []
        for r in raw_records or []:
            rows.append(
                {
                    "driver_number": r.get("DriverNo"),
                    "timestamp": r.get("Utc", r.get("timestamp")),
                    "X": pd.to_numeric(r.get("X"), errors="coerce"),
                    "Y": pd.to_numeric(r.get("Y"), errors="coerce"),
                    "Z": pd.to_numeric(r.get("Z"), errors="coerce"),
                }
            )
        return pd.DataFrame(rows)

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

        Returns None when there is not enough usable GPS data.
        """
        if pos_df is None or len(pos_df) < 3 or car_timestamps is None:
            return None
        t = pd.to_datetime(pos_df["timestamp"], utc=True, format="ISO8601", errors="coerce")
        ok = t.notna() & pos_df[["X", "Y"]].notna().all(axis=1)
        if int(ok.sum()) < 3:
            return None
        t_num = t[ok].astype("int64").to_numpy().astype(float)
        order = np.argsort(t_num, kind="stable")
        t_num = t_num[order]
        xy = pos_df.loc[ok, ["X", "Y"]].to_numpy(dtype=float)[order]
        seg = np.hypot(*np.diff(xy, axis=0).T)
        dist = np.concatenate([[0.0], np.cumsum(seg)])

        ct = pd.to_datetime(
            pd.Series(list(car_timestamps)), utc=True, format="ISO8601", errors="coerce"
        )
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
