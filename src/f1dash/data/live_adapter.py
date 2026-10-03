"""Live timing adapter: SignalR Core feed -> merged state and bounded buffers.

Message flow
------------
:class:`data.signalr_core.SignalRCoreClient` connects to
``wss://livetiming.formula1.com/signalrcore`` and hands every message to
:meth:`SignalRLiveAdapter.handle_message` as the raw wire triple
``(topic, data, timestamp)``; the subscription snapshot goes to
:meth:`SignalRLiveAdapter.seed_state`. The recorder and the fixture replay use
exactly the same entry points, so recorded and live data share one path.

* Keyframe + delta topics (``TimingData``, ``DriverList``, ``SessionInfo``,
  ``TrackStatus``, ``RaceControlMessages`` ...) are deep-merged into
  :class:`data.live_state.LiveState` (``STATE_TOPICS``).
* True time series are normalised into flat records and kept in bounded
  buffers: ``CarData.z`` -> ``DriverNo, Utc, rpm, speed, n_gear, throttle,
  brake, drs``; ``Position.z`` -> ``DriverNo, Utc, X, Y, Z, Status``;
  ``WeatherData`` -> the sample plus its ``timestamp``.

Compressed topics (``CarData.z``, ``Position.z``) are base64 of **raw**
DEFLATE JSON, decoded by :func:`decode_zipped`.

LiveF1's ``RealF1Client`` is no longer used: it targets the classic
``/signalr/`` hub, whose negotiate answers 401 since F1's 2025 move to
SignalR Core.
"""

import base64
import contextlib
import json
import logging
import os
import threading
import time
import zlib
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np
import pandas as pd

# Position.z shares FastF1's 1/10 m position units - one definition, both paths.
from data.fastf1_adapter import POSITION_UNITS_PER_METRE
from data.live_state import STATE_TOPICS, LiveState, as_list
from data.signalr_core import (
    AUTH_TOPICS,
    STATUS_TEXT,
    FeedStatus,
    SignalRCoreClient,
    token_from_env_value,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from data.live_recorder import LiveRecorder


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


def _normalize_channels(channels: Any) -> dict[str, Any]:
    """Channels arrive as {id: value} dicts; tolerate positional lists."""
    if isinstance(channels, dict):
        return {str(k): v for k, v in channels.items()}
    if isinstance(channels, (list, tuple)):
        ids = sorted(CAR_CHANNELS.keys(), key=int)
        return {ids[i]: v for i, v in enumerate(channels) if i < len(ids)}
    return {}


def decode_topic_payload(topic: str, payload: Any) -> list[dict]:
    """Decode one raw topic payload into flat record dicts.

    Supports CarData.z and Position.z shapes: ``[(timestamp, b64str), ...]``
    or a single b64 string.
    """
    items = payload if isinstance(payload, list) else [(None, payload)]
    records: list[dict] = []
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


def _session_part(timing_state: Any) -> int | None:
    """The running qualifying segment (1-3) from ``TimingData.SessionPart``."""
    raw = (timing_state or {}).get("SessionPart") if isinstance(timing_state, dict) else None
    try:
        part = int(str(raw))
    except (TypeError, ValueError):
        return None
    return part if part >= 1 else None


def format_lap_time(seconds: float) -> str:
    """``91.204`` -> ``1:31.204``."""
    minutes, rest = divmod(float(seconds), 60.0)
    return f"{int(minutes)}:{rest:06.3f}"


def _as_bool(value: Any) -> bool | None:
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

# Where the user's own token is read from. It stays on their machine: the
# sustainable mode for this feed is local, single-connection, own-token use.
TOKEN_ENV_VAR = "F1TV_SUBSCRIPTION_TOKEN"  # noqa: S105 - the variable name, not a token
# Record every live session into the replay directory unless set to 0 (LIVE-20).
AUTORECORD_ENV_VAR = "F1_LIVE_AUTORECORD"

# Numeric WeatherData fields. The live feed sends them as strings ("0" for no
# rain), and bool("0") is True: every dry session read as wet (LIVE-34).
# Per-driver TimingData flags carried onto every lap row of that driver.
DRIVER_FLAGS = ("InPit", "PitOut", "Retired", "Stopped")

WEATHER_NUMERIC = (
    "AirTemp",
    "Humidity",
    "Pressure",
    "Rainfall",
    "TrackTemp",
    "WindDirection",
    "WindSpeed",
)


def subscription_token() -> str | None:
    """The configured F1TV subscription token (the JWT), or None.

    Accepts the JWT or the F1 website's ``login-session`` cookie value; see
    :func:`data.signalr_core.token_from_env_value`.
    """
    return token_from_env_value(os.getenv(TOKEN_ENV_VAR, ""))


def autorecord_enabled() -> bool:
    return os.getenv(AUTORECORD_ENV_VAR, "1").strip().lower() not in ("0", "false", "no", "off")


def _safe_name(value: Any) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value)).strip("_")


def session_identity(info: Any) -> str | None:
    """What identifies one session in ``SessionInfo``: its ``Key`` or ``Path``.

    Partial updates (``ArchiveStatus`` alone, say) carry neither and say
    nothing about a change of session.
    """
    if not isinstance(info, dict):
        return None
    for field in ("Key", "Path"):
        value = info.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return None


class SnapshotCache:
    """The built live snapshot, shared by every browser tab (LIVE-36).

    Each tab's manager used to keep its own change-token cache, so N viewers
    meant N full rebuilds every 3 s competing with the ingest thread. The
    lock also makes concurrent polls build once.

    It also keeps the light part of past snapshots (everything except the
    per-driver telemetry and GPS frames) at most once a second for
    ``history_seconds``, for the broadcast delay (LIVE-21): a delayed view
    reads the order, gaps and messages from that moment, and the time-series
    frames are cut at the same moment.
    """

    HEAVY_KEYS = ("telemetry", "location")

    def __init__(self, history_seconds: float = 300.0):
        self.lock = threading.RLock()
        self.token: tuple | None = None
        self.snapshot: dict | None = None
        self.builds = 0
        self.history_seconds = history_seconds
        self._history: deque[tuple[float, pd.Timestamp, dict]] = deque()

    def store(self, token: tuple, snapshot: dict) -> None:
        self.token, self.snapshot = token, snapshot
        self.builds += 1

    def remember(self, now: float, wall: pd.Timestamp) -> None:
        """Keep the light part of the current snapshot (one per second)."""
        if self.snapshot is None:
            return
        if self._history and now - self._history[-1][0] < 1.0:
            return
        light = {k: v for k, v in self.snapshot.items() if k not in self.HEAVY_KEYS}
        light["session_info"] = dict(self.snapshot.get("session_info", {}))
        self._history.append((now, wall, light))
        while self._history and now - self._history[0][0] > self.history_seconds:
            self._history.popleft()

    def at(self, moment: float) -> tuple[pd.Timestamp, dict] | None:
        """The newest kept snapshot taken at or before ``moment``."""
        chosen = None
        for taken, wall, light in self._history:
            if taken > moment:
                break
            chosen = (wall, light)
        if chosen is None and self._history:
            _, wall, light = self._history[0]  # not that much history yet
            chosen = (wall, light)
        return chosen

    def clear(self) -> None:
        self.token = self.snapshot = None
        self._history.clear()


class SignalRLiveAdapter:
    """The live feed for this process: one SignalR Core connection, merged
    state for delta topics and bounded buffers for time series.

    Created once per process (``data.live_service``); browser sessions only
    read from it.
    """

    # Topics subscribed on /signalrcore. The first group is what FastF1's own
    # client and the community clients that work against the 2026 feed
    # subscribe; the rest feed the tyre panels. An unknown topic simply never
    # delivers anything (SignalR Core does not reject the whole Subscribe).
    # The client leaves out AUTH_TOPICS on a connection made without a token.
    TELEMETRY_TOPICS: ClassVar = [
        "Heartbeat",
        "CarData.z",  # speed/throttle/brake/RPM/gear/DRS - needs a token
        "Position.z",  # GPS X/Y/Z - needs a token
        "ExtrapolatedClock",
        "TimingData",
        "TimingAppData",  # per-driver stints: compound, age, new/used
        "TimingStats",
        "TopThree",
        "WeatherData",
        "TrackStatus",
        "SessionInfo",
        "SessionStatus",
        "SessionData",
        "DriverList",
        "RaceControlMessages",
        "LapCount",
        "TyreStintSeries",
        "PitLaneTimeCollection",
    ]

    def __init__(
        self, buffer_limit: int = 20000, client_factory: Callable | None = None, **_legacy
    ):
        """
        Args:
            buffer_limit: Max records kept per topic. Oldest records are
                dropped first, bounding memory during long sessions
                (~20k CarData messages ≈ several minutes of full-grid data;
                parsers only ever need the tail).
        """
        # Builds the SignalR Core client; injectable so tests run offline.
        self._client_factory = client_factory
        self.buffer_limit = max(int(buffer_limit), 100)
        self.client: Any = None
        # Merged per-topic state for keyframe+delta topics (LIVE-05). Time
        # series still go to _data_buffer.
        self.state = LiveState()
        # Lap completions are a true time series and must outlive the telemetry
        # cap: losing them would erase the first half of a race from the lap
        # chart. Kept separately, and only one small row per completed lap.
        self.lap_history: list[dict] = []
        self._lap_keys: set[tuple[str, int]] = set()
        self._lap_counter: dict[str, int] = {}
        self._data_buffer: dict[str, list[dict]] = defaultdict(list)
        # The client runs on its own thread while Streamlit polls from the
        # script thread: without this, trimming a buffer shifted the reader's
        # slice underneath it and records were skipped or duplicated.
        self._buffer_lock = threading.RLock()
        # Monotonic count of buffered records. Buffer *lengths* stop changing
        # once a topic hits its cap, so they cannot signal new data.
        self._ingested = 0
        # Optional raw-stream recorder; see data/live_recorder.py (LIVE-12).
        self.recorder: LiveRecorder | None = None
        # Why recording stopped by itself (disk full ...), for the Live page.
        self.recorder_error: str | None = None
        # Record each live session automatically while the client runs (LIVE-20).
        self.autorecord = False
        self._auto_recording = False
        # The session the state belongs to (LIVE-27).
        self._session_id: str | None = None
        self._running = False
        self._thread_error: BaseException | None = None
        # Wall-clock time of the last Heartbeat, for the "last update" caption.
        self.last_heartbeat: str | None = None
        # The built snapshot, shared by every tab (LIVE-36, LIVE-21).
        self.snapshots = SnapshotCache()
        self._circuit_info_cache: dict[tuple, dict] = {}

    def handle_message(self, topic: str, payload: Any, timestamp: str | None = None) -> None:
        """Ingest one raw feed message.

        State topics are deep-merged into :attr:`state`; time series are
        appended to the bounded buffers. This is the entry point the SignalR
        Core client (LIVE-01), the recorder replay and the fixture replay all
        use.
        """
        if topic == "SessionInfo":
            self._observe_session(payload)
        self._record("record", topic, payload, timestamp)

        if topic == "Heartbeat":
            if isinstance(payload, dict):
                self.last_heartbeat = payload.get("Utc") or timestamp
            return
        if topic in STATE_TOPICS:
            if topic == "TimingData":
                self._record_lap_progress(payload, timestamp)
            self.state.update(topic, payload)
            return
        records = self.normalise_series(topic, payload, timestamp)
        if records is not None:
            self._buffer_topic(topic, records)

    def _record(self, method: str, *args: Any) -> None:
        """Pass one message or snapshot to the recorder, if there is one.

        A recorder failure (disk full, a file removed under it) must not
        freeze the live state: before LIVE-31 every later message raised here,
        before reaching the state update. The recorder is read once, so a
        stop on the UI thread cannot null it between the check and the call.
        """
        recorder = self.recorder
        if recorder is None:
            return
        try:
            getattr(recorder, method)(*args)
        except Exception as exc:
            if self.recorder is recorder:
                self.recorder = None
                self._auto_recording = False
            self.recorder_error = f"Recording stopped: {type(exc).__name__}: {exc}"
            logger.error("Live recording stopped: %s", exc)
            with contextlib.suppress(Exception):
                recorder.close()

    def _observe_session(self, info: Any) -> None:
        """Reset everything when ``SessionInfo`` names a different session.

        A qualifying snapshot followed by the race's used to leave the race
        leader ``KO`` and qualifying laps in the lap history (LIVE-27). An
        automatic recording rotates at the same moment (LIVE-20).
        """
        identity = session_identity(info)
        if identity is None or identity == self._session_id:
            return
        previous, self._session_id = self._session_id, identity
        if previous is not None:
            logger.info("Live feed: new session, clearing the previous one's state")
            self._reset_session_state()
        if self.autorecord:
            self._rotate_autorecording(info)

    def _reset_session_state(self) -> None:
        with self._buffer_lock:
            self.state.clear()
            self.lap_history.clear()
            self._lap_keys.clear()
            self._lap_counter.clear()
            self._data_buffer.clear()
            self._ingested += 1

    def _rotate_autorecording(self, info: dict) -> None:
        if self.recorder is not None and not self._auto_recording:
            return  # a recording the user started keeps going
        self.stop_recording()
        meeting = info.get("Meeting")
        if not isinstance(meeting, dict):
            meeting = {}
        gp = _safe_name(meeting.get("Name") or "live")
        session = _safe_name(info.get("Name") or info.get("Type") or "session")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        from config import config

        directory = Path(config.replay_dir) / f"raw_{gp}_{session}_{stamp}"
        try:
            self.start_recording(directory)
            self._auto_recording = True
            logger.info("Live feed: recording this session to %s", directory)
        except OSError as exc:
            self.recorder_error = f"Could not start recording: {exc}"
            logger.error("Could not start the automatic recording: %s", exc)

    @staticmethod
    def normalise_series(topic: str, payload: Any, timestamp: str | None) -> Any:
        """Raw time-series payload -> the flat records the parsers read.

        ``CarData.z``/``Position.z`` arrive as a base64 string (raw DEFLATE
        JSON) and become one record per car per sample. ``WeatherData`` is a
        full sample each time and keeps the message timestamp. Anything
        already in record form (lists of dicts) passes through unchanged.
        Undecodable payloads are dropped with a log line, never raised: one
        bad message must not stop the feed.
        """
        if topic in ("CarData.z", "Position.z"):
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                return payload  # already flat records
            try:
                return decode_topic_payload(topic, [(timestamp, payload)])
            except (ValueError, TypeError, zlib.error, json.JSONDecodeError) as exc:
                logger.warning("Could not decode %s payload: %s", topic, exc)
                return None
        if topic == "WeatherData" and isinstance(payload, dict):
            return {**payload, "timestamp": timestamp}
        return payload

    def _record_lap_progress(self, payload: Any, timestamp: str | None = None) -> None:
        """Note completed laps as TimingData messages arrive.

        ``NumberOfLaps`` and ``LastLapTime`` usually come in separate partial
        messages, so the lap counter is tracked per driver and a lap time is
        attributed to whatever lap the driver had reached. Each (driver, lap)
        is recorded once; the old "last 40 rows" check could drop real laps.
        """
        if not isinstance(payload, dict):
            return
        for number, line in (payload.get("Lines") or {}).items():
            if not isinstance(line, dict):
                continue
            driver = str(number)
            laps = line.get("NumberOfLaps")
            if laps is not None:
                with contextlib.suppress(TypeError, ValueError):
                    self._lap_counter[driver] = int(laps)

            last_lap = line.get("LastLapTime")
            value = last_lap.get("Value") if isinstance(last_lap, dict) else None
            lap_number = self._lap_counter.get(driver)
            if not value or not lap_number:
                continue
            with self._buffer_lock:
                if (driver, lap_number) in self._lap_keys:
                    continue  # the same completion repeated in a later message
                self._lap_keys.add((driver, lap_number))
                self.lap_history.append(
                    {
                        "driver_number": driver,
                        "LapNumber": lap_number,
                        "LapTime": value,
                        # When the lap ended: the boundary live telemetry is
                        # segmented at (LIVE-13).
                        "Utc": timestamp,
                    }
                )

    def seed_state(self, snapshot: dict[str, Any]) -> None:
        """Apply a subscription snapshot: ``{topic: full_state}``.

        A snapshot of a different session resets everything first; each
        state topic it carries replaces the held one (LIVE-27).
        """
        snapshot = snapshot or {}
        if "SessionInfo" in snapshot:
            self._observe_session(snapshot["SessionInfo"])
        self._record("record_snapshot", snapshot)
        self.state.seed({k: v for k, v in snapshot.items() if k in STATE_TOPICS})
        # Joining mid-session: the lap counters come from the snapshot, or the
        # next LastLapTime would have no lap number to be filed under.
        timing = snapshot.get("TimingData")
        for number, line in ((timing or {}).get("Lines") or {}).items():
            if isinstance(line, dict) and line.get("NumberOfLaps") is not None:
                with contextlib.suppress(TypeError, ValueError):
                    self._lap_counter[str(number)] = int(line["NumberOfLaps"])
        # The snapshot also carries the latest sample of each time series
        # (car data, positions, weather): keep it rather than wait for the
        # next update.
        for topic, payload in snapshot.items():
            if topic not in STATE_TOPICS and payload not in (None, {}, [], ""):
                records = self.normalise_series(topic, payload, None)
                if records is not None and topic != "Heartbeat":
                    self._buffer_topic(topic, records)

    def start_recording(self, directory) -> "LiveRecorder":
        """Record every message from here on, for later replay."""
        from data.live_recorder import LiveRecorder

        self.stop_recording()
        recorder = LiveRecorder(directory)
        recorder.record_snapshot(self.state.snapshot())
        self.recorder = recorder
        self.recorder_error = None
        self._auto_recording = False
        return recorder

    def stop_recording(self) -> str | None:
        """Stop recording; returns where the recording was written."""
        recorder = self.recorder
        if recorder is None:
            return None
        self.recorder = None
        self._auto_recording = False
        directory = str(recorder.directory)
        with contextlib.suppress(Exception):
            recorder.close()
        return directory

    def is_recording(self) -> bool:
        return self.recorder is not None

    def subscribed_topics(self) -> list[str]:
        """Topics worth subscribing to, given whether a token is configured.

        Auth-gated topics are dropped without one: they would never deliver
        anything, and the rest of the feed works perfectly well alone. The
        client applies the same rule per connection, so a token that expires
        or is refused mid-weekend drops them too (LIVE-26).
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

    def _make_client(self, topics: list[str]):
        factory = self._client_factory or SignalRCoreClient
        return factory(
            topics=topics,
            on_message=self._on_feed_message,
            on_snapshot=self.seed_state,
            token_provider=subscription_token,
        )

    def _on_feed_message(self, topic: str, payload: Any, timestamp: str | None) -> None:
        self.handle_message(topic, payload, timestamp)

    def start_async(self, topics: list[str] | None = None, log_file: str | None = None):
        """Connect to /signalrcore on a background thread (no-op if running).

        Every topic is handed to the client, which leaves out the gated ones
        on a connection without a (valid) token. ``log_file`` is accepted for
        compatibility with older callers and ignored: raw recording is
        :meth:`start_recording`, and automatic per-session recording is on
        unless ``F1_LIVE_AUTORECORD=0``.
        """
        if self.is_running():
            return
        self._thread_error = None
        self.autorecord = autorecord_enabled()
        try:
            self.client = self._make_client(list(topics or self.TELEMETRY_TOPICS))
            self.client.start()
            self._running = True
        except Exception as exc:
            self._running = False
            self._thread_error = exc
            logger.error("Could not start the live client: %s", exc, exc_info=exc)
            return
        if self.autorecord and self._session_id is not None and self.recorder is None:
            self._rotate_autorecording(self.state.get("SessionInfo"))

    def status(self):
        """The connection state (:class:`data.signalr_core.FeedStatus`)."""
        if self.client is None:
            return FeedStatus.IDLE
        return self.client.status

    def status_text(self) -> str:
        """One line for the UI: the state and, when relevant, why."""
        status = self.status()
        text = STATUS_TEXT[status]
        stats = getattr(self.client, "stats", None)
        problem = status in (FeedStatus.RECONNECTING, FeedStatus.BLOCKED, FeedStatus.AUTH_REQUIRED)
        if problem and stats is not None and stats.last_error:
            text += f" - {stats.last_error}"
        notice = getattr(self.client, "token_notice", None)
        if isinstance(notice, str) and notice and status is not FeedStatus.STOPPED:
            text += f". {notice}"
        return text

    def change_token(self) -> tuple:
        """A cheap value that changes whenever ingested data changes.

        Lets a reader skip rebuilding a snapshot when nothing has arrived
        since the last poll - a 3 s fragment otherwise reprocesses the whole
        buffer for an identical result (LIVE-11).
        """
        with self._buffer_lock:
            ingested, laps = self._ingested, len(self.lap_history)
        return (self.state.version, laps, ingested)

    def recorded_laps(self) -> list[dict]:
        """A snapshot copy of the recorded lap completions."""
        with self._buffer_lock:
            return list(self.lap_history)

    def get_buffered_data(self, topic: str) -> list[dict]:
        """A snapshot copy of a topic's buffer.

        A copy, not the live list: the client thread keeps appending and
        trimming, and a reader iterating the real list would see records shift
        under it.
        """
        with self._buffer_lock:
            return list(self._data_buffer.get(topic, []))

    def clear_buffer(self, topic: str | None = None):
        """Empty the time-series buffers (car data, positions, weather).

        The merged state (timing, race control, track status) and the lap
        history are kept: state only comes back with the next Subscribe
        snapshot, possibly hours away, so clearing it emptied the tower for
        every viewer (LIVE-29).
        """
        with self._buffer_lock:
            if topic:
                self._data_buffer[topic] = []
            else:
                self._data_buffer.clear()
            self._ingested += 1  # a clear is a change like any other

    def circuit_info(self, year: Any, circuit_key: Any) -> dict:
        """Corners and rotation for the live map, once per circuit (LIVE-22).

        FastF1 reads them from the MultiViewer circuit API by season and
        ``SessionInfo.Meeting.Circuit.Key``. A failure is remembered for ten
        minutes, so an offline machine does not retry on every poll.
        """
        if year in (None, "") or circuit_key in (None, ""):
            return {}
        try:
            key = (int(year), int(circuit_key))
        except (TypeError, ValueError):
            return {}
        cached = self._circuit_info_cache.get(key)
        now = time.monotonic()
        if cached is not None and (cached["info"] or now - cached["at"] < 600):
            return cached["info"]
        info: dict = {}
        try:
            from fastf1.mvapi import get_circuit_info

            raw = get_circuit_info(year=key[0], circuit_key=key[1])
            if raw is not None:
                info = {"corners": raw.corners, "rotation": float(raw.rotation)}
        except Exception as exc:
            logger.warning("Circuit info unavailable for the live map: %s", exc)
        self._circuit_info_cache[key] = {"info": info, "at": now}
        return info

    def is_running(self) -> bool:
        """Whether the client thread is alive (connected or reconnecting)."""
        client = self.client
        running = bool(client is not None and client.is_running())
        self._running = running
        return running

    def last_error(self) -> BaseException | str | None:
        if self._thread_error is not None:
            return self._thread_error
        stats = getattr(self.client, "stats", None)
        return getattr(stats, "last_error", None)

    def stop(self):
        """Close the connection and join the client thread (within 5 s)."""
        client = self.client
        if client is not None:
            client.stop()
        self._running = False


class LiveDataProcessor:
    """Process the adapter's merged state and normalised records into
    structured DataFrames (see the module docstring for the record shapes).
    """

    # CarData.z field -> unified channel name.
    CAR_CHANNEL_COLUMNS: ClassVar = {
        "rpm": "RPM",
        "speed": "Speed",
        "n_gear": "nGear",
        "throttle": "Throttle",
        "brake": "Brake",
        "drs": "DRS",
    }

    @staticmethod
    def parse_car_data(raw_records: list[dict]) -> pd.DataFrame:
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
    def parse_position_data(raw_records: list[dict]) -> pd.DataFrame:
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
    def parse_weather_data(raw_records: list[dict]) -> pd.DataFrame:
        """WeatherData records -> DataFrame of weather observations."""
        rows = []
        for r in raw_records or []:
            row = {"timestamp": r.get("timestamp")}
            row.update({k: v for k, v in r.items() if k not in ("SessionKey", "timestamp")})
            rows.append(row)
        frame = pd.DataFrame(rows)
        # The feed sends every value as a string; "0" rain read as wet (LIVE-34).
        for column in WEATHER_NUMERIC:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    # -- state-derived frames (LIVE-05) ---------------------------------
    #
    # These read the *merged* LiveState rather than a list of messages, so a
    # field set once in the keyframe survives and a partial update lands on
    # the entry it belongs to.

    @staticmethod
    def timing_from_state(timing_state: dict) -> pd.DataFrame:
        """``TimingData`` state -> one row per driver, current values.

        ``Sectors``, ``Segments`` and ``Speeds`` are index-addressed, so they
        are normalised back to ordered lists before flattening: in a delta,
        key ``"1"`` is sector **2**.
        """
        rows = []
        for number, line in (timing_state or {}).get("Lines", {}).items():
            if not isinstance(line, dict):
                continue
            row: dict[str, Any] = {"driver_number": str(number)}
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
    def standings_from_state(
        timing_state: dict, acronyms: dict[str, str], race: bool, segment_prefix: str = "Q"
    ) -> pd.DataFrame:
        """``TimingData`` state -> the ``standings`` table the tower orders by.

        Same columns the replay model produces (``Driver, Position, Gap,
        GapSeconds, LapsDown, Interval, IntervalSeconds, BestLap, BestSeconds,
        LastLap, LastSeconds, LastFlag, S1..S3, Status, Pits``), so
        ``processing.timing.build_timing_rows`` has one "ordered by the timing
        screen" path for live and replay. Races read ``GapToLeader`` /
        ``IntervalToPositionAhead``; practice and qualifying read
        ``TimeDiffToFastest`` / ``TimeDiffToPositionAhead``.

        In qualifying (``TimingData.SessionPart`` set) the running cars are
        ordered by their best in the running segment and knocked-out cars sit
        under "Eliminated in Q1/Q2" headings, as on the replay (LIVE-19).
        """
        from processing.time_utils import parse_gap, to_seconds
        from processing.timing import LEADER, MISSING, format_delta, format_lap_gap

        def cell(raw: Any, is_first: bool) -> tuple[str, float | None, int]:
            seconds, laps_down = parse_gap(raw)
            if is_first:
                return LEADER, 0.0, 0
            if laps_down:
                return format_lap_gap(laps_down), None, int(laps_down)
            if seconds is None:
                return MISSING, None, 0
            return format_delta(seconds), seconds, 0

        def value_of(entry: Any) -> Any:
            return entry.get("Value") if isinstance(entry, dict) else entry

        rows = []
        for number, line in (timing_state or {}).get("Lines", {}).items():
            if not isinstance(line, dict):
                continue
            raw_position = line.get("Position") or line.get("Line")
            try:
                position = int(str(raw_position))
            except (TypeError, ValueError):
                continue
            first = position == 1
            if race:
                gap_raw = line.get("GapToLeader")
                interval_raw = value_of(line.get("IntervalToPositionAhead"))
            else:
                gap_raw = line.get("TimeDiffToFastest")
                interval_raw = line.get("TimeDiffToPositionAhead")
            gap, gap_seconds, laps_down = cell(gap_raw, first)
            interval, interval_seconds, _ = cell(interval_raw, first)

            best = value_of(line.get("BestLapTime"))
            last_entry = line.get("LastLapTime")
            last = value_of(last_entry)
            flag = None
            if isinstance(last_entry, dict):
                if _as_bool(last_entry.get("OverallFastest")):
                    flag = "sb"
                elif _as_bool(last_entry.get("PersonalFastest")):
                    flag = "pb"
            sectors = [value_of(entry) for entry in as_list(line.get("Sectors"))]

            if _as_bool(line.get("Retired")) or _as_bool(line.get("Stopped")):
                status = "OUT"
            elif _as_bool(line.get("KnockedOut")):
                status = "KO"
            elif _as_bool(line.get("InPit")):
                status = "IN PIT"
            else:
                status = "ON TRACK"
            pits = line.get("NumberOfPitStops")

            row: dict[str, Any] = {
                "Driver": acronyms.get(str(number), f"#{number}"),
                "Position": position,
                "Gap": gap,
                "GapSeconds": gap_seconds,
                "LapsDown": laps_down,
                "Interval": interval,
                "IntervalSeconds": interval_seconds,
                "BestLap": best or MISSING,
                "BestSeconds": to_seconds(best) if best else None,
                "LastLap": last or MISSING,
                "LastSeconds": to_seconds(last) if last else None,
                "LastFlag": flag,
                "Status": status,
                "Pits": int(str(pits)) if pits is not None and str(pits).isdigit() else None,
            }
            for index in range(3):
                raw = sectors[index] if index < len(sectors) else None
                row[f"S{index + 1}"] = to_seconds(raw) if raw else None
            rows.append(row)
        if not rows:
            return pd.DataFrame()
        part = _session_part(timing_state)
        if not race and part:
            return LiveDataProcessor._qualifying_standings(
                rows, (timing_state or {}).get("Lines", {}), acronyms, part, segment_prefix
            )
        frame = pd.DataFrame(rows).sort_values("Position", kind="stable").reset_index(drop=True)
        # The feed briefly repeats a position during overtakes; rank instead.
        frame["Position"] = range(1, len(frame) + 1)
        return frame

    @staticmethod
    def _qualifying_standings(
        rows: list[dict], lines: dict, acronyms: dict[str, str], part: int, prefix: str
    ) -> pd.DataFrame:
        """Running cars by their best this segment, then each knock-out group.

        ``BestLapTimes`` holds one entry per segment (index 0 = Q1). A car
        knocked out in Q1 has no Q2 time; a Q2 time proves the segment was
        reached. The shapes follow f1-dash and the 2023 archive; confirm them
        on a live SQ/Q recording (LIVE-18).
        """
        from processing.time_utils import to_seconds
        from processing.timing import LEADER, MISSING, format_delta

        by_code = {row["Driver"]: row for row in rows}
        segment_times: dict[str, list[float | None]] = {}
        knocked: dict[str, bool] = {}
        for number, line in lines.items():
            if not isinstance(line, dict):
                continue
            code = acronyms.get(str(number), f"#{number}")
            if code not in by_code:
                continue
            times = []
            for entry in as_list(line.get("BestLapTimes")):
                value = entry.get("Value") if isinstance(entry, dict) else entry
                times.append(to_seconds(value) if value else None)
            segment_times[code] = times
            knocked[code] = bool(_as_bool(line.get("KnockedOut")))

        def best_in(code: str, segment: int) -> float | None:
            times = segment_times.get(code, [])
            return times[segment - 1] if 0 < segment <= len(times) else None

        def eliminated_in(code: str) -> int:
            reached = [s for s in range(1, part) if best_in(code, s + 1) is not None]
            return min(max(reached, default=0) + 1, max(part - 1, 1))

        def ordered(codes: list[str], segment: int) -> list[str]:
            return sorted(
                codes,
                key=lambda c: (
                    best_in(c, segment) is None,
                    best_in(c, segment) or 0.0,
                    by_code[c]["Position"],
                ),
            )

        active = [code for code in by_code if not knocked.get(code)]
        groups: dict[int, list[str]] = {}
        for code in by_code:
            if knocked.get(code):
                groups.setdefault(eliminated_in(code), []).append(code)

        sections: list[tuple[str | None, list[str], int]] = []
        heading = f"{prefix}{part}" if groups else None
        sections.append((heading, ordered(active, part), part))
        sections.extend(
            (f"Eliminated in {prefix}{segment}", ordered(groups[segment], segment), segment)
            for segment in sorted(groups, reverse=True)
        )

        out = []
        for heading, codes, segment in sections:
            leader_best = best_in(codes[0], segment) if codes else None
            previous = None
            for index, code in enumerate(codes):
                row = dict(by_code[code])
                best = best_in(code, segment)
                row["Partition"] = heading if index == 0 else None
                if best is not None:
                    row["BestSeconds"] = best
                    row["BestLap"] = format_lap_time(best)
                if index == 0 and best is not None:
                    row["Gap"], row["GapSeconds"] = LEADER, 0.0
                    row["Interval"], row["IntervalSeconds"] = LEADER, 0.0
                elif best is not None and leader_best is not None:
                    row["Gap"], row["GapSeconds"] = (
                        format_delta(best - leader_best),
                        best - leader_best,
                    )
                    if previous is not None:
                        row["Interval"] = format_delta(best - previous)
                        row["IntervalSeconds"] = best - previous
                else:
                    row["Gap"], row["GapSeconds"] = MISSING, None
                    row["Interval"], row["IntervalSeconds"] = MISSING, None
                if knocked.get(code):
                    row["Status"] = "KO"
                previous = best if best is not None else previous
                out.append(row)
        frame = pd.DataFrame(out)
        frame["Position"] = range(1, len(frame) + 1)
        return frame

    @staticmethod
    def stints_from_timing_app(timing_app_state: dict) -> dict:
        """``TimingAppData`` state reshaped as ``TyreStintSeries`` state.

        Both carry ``Stints`` per driver with the same fields (``Compound``,
        ``New``, ``TotalLaps``, ``StartLaps``), keyed differently: this lets
        the stints builder run on whichever topic the feed delivers.
        """
        stints = {}
        for number, line in (timing_app_state or {}).get("Lines", {}).items():
            if isinstance(line, dict) and line.get("Stints") is not None:
                stints[str(number)] = line["Stints"]
        return {"Stints": stints} if stints else {}

    @staticmethod
    def stints_from_state(
        stint_state: dict, acronyms: dict[str, str] | None = None
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
    def lap_boundaries(lap_history: Sequence[dict]) -> dict[str, list[pd.Timestamp]]:
        """When each driver's completed laps ended, oldest first.

        Live ``Distance`` is cumulative since the stream started, so without
        these the traces of two drivers share no axis (LIVE-13).
        """
        boundaries: dict[str, list[pd.Timestamp]] = {}
        for entry in lap_history or []:
            moment = pd.to_datetime(entry.get("Utc"), utc=True, errors="coerce")
            if pd.isna(moment):
                continue
            boundaries.setdefault(str(entry.get("driver_number")), []).append(moment)
        for times in boundaries.values():
            times.sort()
        return boundaries

    @staticmethod
    def last_completed_lap(
        frame: pd.DataFrame, boundaries: Sequence[pd.Timestamp]
    ) -> pd.DataFrame | None:
        """The slice of a driver's samples covering their last full lap.

        Returns None when the lap is not covered by the buffered samples, so
        the caller can fall back to the running tail.
        """
        if frame is None or frame.empty or len(boundaries) < 2:
            return None
        if "timestamp" not in frame.columns:
            return None
        stamps = LiveDataProcessor.parsed_timestamps(frame["timestamp"])
        start, end = boundaries[-2], boundaries[-1]
        window = frame[(stamps >= start) & (stamps <= end)]
        return window.reset_index(drop=True) if len(window) >= 3 else None

    @staticmethod
    def laps_from_history(
        lap_history: Sequence[dict],
        timing_state: dict | None = None,
        acronyms: dict[str, str] | None = None,
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

        flags_by_driver: dict[str, dict[str, bool]] = {}
        for number, line in lines.items():
            driver = str(number)
            if not isinstance(line, dict):
                continue
            flags = {column: bool(_as_bool(line.get(column))) for column in DRIVER_FLAGS}
            flags_by_driver[driver] = flags
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
        # Built per driver as plain bools - filling object columns of NaN and
        # bools relied on pandas' silent downcasting, which pandas 3 drops
        # (and a driver missing from Lines then made ~frame["Retired"] raise),
        # CORE-02.
        for column in DRIVER_FLAGS:
            frame[column] = (
                frame["driver_number"]
                .map(lambda driver, c=column: flags_by_driver.get(driver, {}).get(c, False))
                .astype(bool)
            )
        return frame.sort_values(["Driver", "LapNumber"]).reset_index(drop=True)

    @staticmethod
    def drivers_from_state(driver_state: dict) -> pd.DataFrame:
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
    def acronyms_from_state(driver_state: dict) -> dict[str, str]:
        """Racing number -> three-letter acronym, from merged DriverList."""
        mapping = {}
        for number, entry in (driver_state or {}).items():
            if isinstance(entry, dict) and entry.get("Tla"):
                mapping[str(number)] = str(entry["Tla"])
        return mapping

    @staticmethod
    def parse_race_control(raw_records: list[dict]) -> pd.DataFrame:
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
    def parse_track_status(raw_records: list[dict]) -> dict | None:
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
    def distance_at(pos_df: pd.DataFrame, car_timestamps: Sequence) -> np.ndarray | None:
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
