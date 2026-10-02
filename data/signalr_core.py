"""SignalR Core client for F1's live timing feed (IMPROVEMENTS.md LIVE-01).

F1 moved live timing from classic ASP.NET SignalR (``/signalr/``) to SignalR
Core (``/signalrcore``) in 2025. The classic negotiate now answers **401**, so
LiveF1's ``RealF1Client`` is no longer used (it still targets ``/signalr/``).
This module talks to ``/signalrcore`` directly.

The handshake, as FastF1 3.7+ performs it and as it was confirmed against the
live endpoint during the 2026 season (see IMPROVEMENTS.md section 8):

1. ``OPTIONS /signalrcore/negotiate`` - the reply status does not matter; it
   is made to collect the ``AWSALBCORS`` load-balancer cookie that every later
   request must carry.
2. ``POST /signalrcore/negotiate?negotiateVersion=1`` - returns a
   ``connectionToken``. Works without credentials; with an F1TV subscription
   token it is sent as ``Authorization: Bearer <token>`` (what FastF1's
   ``access_token_factory`` does) and unlocks the gated topics.
3. Open ``wss://livetiming.formula1.com/signalrcore?id=<connectionToken>``.
4. Send the JSON-protocol handshake ``{"protocol":"json","version":1}`` and
   wait for ``{}``.
5. Invoke ``Subscribe`` with the topic list. The **completion** (message type
   3) carries ``{topic: full_state}`` - the snapshot that seeds merged state.
   After that the server calls ``feed`` (type 1) with
   ``[topic, data, timestamp]`` - the same triple the recorder writes.

Frames are JSON separated by the ASCII record separator ``0x1E``; one frame
can hold several messages. Type 6 is a ping (about every 15 s, and the only
traffic between sessions); type 7 is a close. The client sends its own ping
every ``PING_INTERVAL`` seconds so the server does not time it out, treats a
silent socket as dead after ``SILENCE_TIMEOUT``, and reconnects with
exponential backoff. The feed is known to drop long connections (about two
hours in), so reconnecting is the normal case, not an error.

Everything here is synchronous and runs on one background thread; the
adapter hands messages to :class:`data.live_adapter.SignalRLiveAdapter`.
"""

from __future__ import annotations

import base64
import contextlib
import enum
import json
import logging
import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

# websockets logs every handshake header (``Authorization: Bearer <JWT>``), the
# ``?id=<connectionToken>`` path and every frame at DEBUG. The connection gets
# its own logger pinned to INFO, so LOG_LEVEL=DEBUG never writes the token to
# a log (SEC-01); a redacting filter is the second line of defence.
_WS_LOGGER = logging.getLogger(f"{__name__}.ws")
_WS_LOGGER.setLevel(logging.INFO)


class _RedactSecrets(logging.Filter):
    """Masks bearer tokens and connection ids in any record that gets through."""

    PATTERNS = (
        (re.compile(r"(Bearer\s+)[\w.\-~+/=]+", re.IGNORECASE), r"\1<redacted>"),
        (re.compile(r"([?&]id=)[^&\s'\"]+"), r"\1<redacted>"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        redacted = text
        for pattern, replacement in self.PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != text:
            record.msg, record.args = redacted, None
        return True


_REDACTOR = _RedactSecrets()
for _name in (_WS_LOGGER.name, "websockets", "websockets.client"):
    logging.getLogger(_name).addFilter(_REDACTOR)

NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
CONNECTION_URL = "wss://livetiming.formula1.com/signalrcore"
RECORD_SEPARATOR = "\x1e"
HANDSHAKE = '{"protocol":"json","version":1}' + RECORD_SEPARATOR

# F1's endpoint is picky about unfamiliar clients; these are the headers its
# own apps send and the ones the community clients that still work use.
BASE_HEADERS = {
    "User-Agent": "BestHTTP",
    "Accept-Encoding": "gzip, identity",
}

# SignalR message types (JSON hub protocol).
MSG_INVOCATION = 1
MSG_COMPLETION = 3
MSG_PING = 6
MSG_CLOSE = 7

# The server pings about every 15 s. Four missed pings means the connection is
# gone even when the socket has not noticed.
SILENCE_TIMEOUT = 60.0
# Keep the server's client-timeout (30 s by default in ASP.NET Core) happy.
PING_INTERVAL = 10.0
# Backoff between reconnect attempts; blocked/forbidden answers wait longer
# so a refused client never hammers the endpoint.
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
BLOCKED_BACKOFF = 120.0
HTTP_TIMEOUT = 15.0

# Topics F1 has gated behind an F1TV subscription token since the 2025 Dutch
# GP. A connection made without a token does not subscribe to them: they would
# never deliver anything, and the rest of the feed works perfectly well alone.
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


class FeedStatus(enum.Enum):
    """What the connection is doing, for the status chip in the UI."""

    IDLE = "idle"  # never started
    CONNECTING = "connecting"  # negotiating / opening the socket
    WAITING = "waiting"  # connected, only pings: no session on air
    LIVE = "live"  # data arriving
    STALE = "stale"  # connected but silent for too long; about to reconnect
    RECONNECTING = "reconnecting"  # waiting out a backoff
    AUTH_REQUIRED = "auth_required"  # 401 on negotiate (token missing/expired)
    BLOCKED = "blocked"  # 403: F1 refused this client or IP
    STOPPED = "stopped"  # stopped by the user


# Why a configured token is not being sent; the free feed carries on (LIVE-26).
TOKEN_REJECTED = "Token rejected – timing only"  # noqa: S105 - UI copy
TOKEN_EXPIRED = "Token expired – timing only"  # noqa: S105 - UI copy


# Plain words for each state, shown next to the chip.
STATUS_TEXT = {
    FeedStatus.IDLE: "Not connected",
    FeedStatus.CONNECTING: "Connecting to F1 live timing",
    FeedStatus.WAITING: "Connected, waiting for a session to start",
    FeedStatus.LIVE: "Live",
    FeedStatus.STALE: "No data for a while, reconnecting",
    FeedStatus.RECONNECTING: "Connection dropped, reconnecting",
    FeedStatus.AUTH_REQUIRED: "F1 asked for a subscription token (HTTP 401)",
    FeedStatus.BLOCKED: "F1 refused the connection (HTTP 403)",
    FeedStatus.STOPPED: "Stopped",
}


class NegotiateError(RuntimeError):
    """The negotiate step failed with an HTTP status worth reporting."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"negotiate failed with HTTP {status_code}")
        self.status_code = status_code


@dataclass
class FeedStats:
    """Counters the UI and the smoke test read. Updated on the client thread."""

    connected_at: float | None = None
    last_message_at: float | None = None
    last_data_at: float | None = None
    reconnects: int = 0
    messages: int = 0
    snapshots: int = 0
    per_topic: dict[str, int] = field(default_factory=dict)
    last_error: str | None = None
    retry_at: float | None = None


def token_from_env_value(value: str | None) -> str | None:
    """The subscription JWT from whatever the user pasted.

    Accepts the JWT itself, or the F1 website's ``login-session`` cookie
    (URL-encoded JSON holding ``data.subscriptionToken``), which is what
    people usually find when they go looking for "the token".
    """
    if not value:
        return None
    text = value.strip().strip('"').strip("'")
    if not text:
        return None
    if text.count(".") == 2 and not text.startswith(("{", "%7B")):
        return text
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        decoded = json.loads(urllib.parse.unquote(text))
        token = (decoded.get("data") or {}).get("subscriptionToken")
        if token:
            return str(token)
    return text


def token_expiry(token: str | None) -> datetime | None:
    """The ``exp`` claim of a JWT, without verifying it (display only)."""
    if not token or token.count(".") != 2:
        return None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, TypeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


def split_frames(raw: str | bytes) -> list[dict]:
    """Every JSON message in one websocket frame."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    messages = []
    for part in raw.split(RECORD_SEPARATOR):
        if not part.strip():
            continue
        try:
            message = json.loads(part)
        except json.JSONDecodeError:
            logger.debug("Undecodable SignalR frame: %.120s", part)
            continue
        if isinstance(message, dict):
            messages.append(message)
    return messages


def refusal_status(exc: BaseException) -> int | None:
    """The HTTP status of a refused websocket upgrade, else None.

    ``websockets`` raises ``InvalidStatus`` (``exc.response.status_code``)
    when the upgrade itself answers 401/403/429; that is a refusal like a
    refused negotiate, not a dropped connection (LIVE-30).
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) and status >= 400 else None


def negotiate(http: requests.Session, token: str | None = None) -> tuple[str, dict[str, str]]:
    """Run the two-step negotiate; return the connection token and headers."""
    headers = dict(BASE_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Only the cookie matters here; the endpoint may answer 405 to OPTIONS.
    with contextlib.suppress(requests.RequestException):
        options = http.options(NEGOTIATE_URL, headers=headers, timeout=HTTP_TIMEOUT)
        cookie = options.cookies.get("AWSALBCORS")
        if cookie:
            headers["Cookie"] = f"AWSALBCORS={cookie}"

    response = http.post(
        NEGOTIATE_URL,
        params={"negotiateVersion": "1"},
        headers=headers,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code >= 400:
        raise NegotiateError(response.status_code, f"negotiate answered {response.status_code}")
    body = response.json()
    connection_token = body.get("connectionToken") or body.get("connectionId")
    if not connection_token:
        raise NegotiateError(response.status_code, "negotiate returned no connection token")
    if "Cookie" not in headers:
        cookie = response.cookies.get("AWSALBCORS")
        if cookie:
            headers["Cookie"] = f"AWSALBCORS={cookie}"
    return str(connection_token), headers


def _default_connect(url: str, headers: dict[str, str]):
    """Open the websocket (``websockets`` sync client, proxy-aware)."""
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers=headers,
        logger=_WS_LOGGER,  # never DEBUG: it would log the bearer token (SEC-01)
        user_agent_header=None,  # the User-Agent above is already in headers
        max_size=None,  # snapshots of a full session are large
        open_timeout=HTTP_TIMEOUT,
        # Transport pings on top of SignalR's own; harmless and they catch
        # half-open TCP connections quickly.
        ping_interval=20,
        ping_timeout=20,
    )


class SignalRCoreClient:
    """Connect, subscribe, dispatch, and keep reconnecting until stopped.

    ``on_snapshot(result)`` receives the subscription completion
    (``{topic: full_state}``); ``on_message(topic, data, timestamp)`` receives
    every feed update. Both run on the client thread and must be quick.
    ``http_factory`` and ``connect`` are injectable so tests can drive the
    whole protocol without a network.

    ``auth_topics`` are subscribed only on a connection that sends a token.
    A token that has expired is not sent, and one the endpoint refuses (401)
    is dropped at once in favour of the free feed (LIVE-26): timing, race
    control and weather need no subscription.
    """

    def __init__(
        self,
        topics: Sequence[str],
        on_message: Callable[[str, Any, str | None], None],
        on_snapshot: Callable[[dict], None],
        token_provider: Callable[[], str | None] | None = None,
        *,
        auth_topics: frozenset[str] = AUTH_TOPICS,
        http_factory: Callable[[], requests.Session] = requests.Session,
        connect: Callable[[str, dict[str, str]], Any] = _default_connect,
        silence_timeout: float = SILENCE_TIMEOUT,
        ping_interval: float = PING_INTERVAL,
        backoff_start: float = BACKOFF_START,
        backoff_max: float = BACKOFF_MAX,
        blocked_backoff: float = BLOCKED_BACKOFF,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.topics = list(topics)
        self.auth_topics = frozenset(auth_topics)
        self.on_message = on_message
        self.on_snapshot = on_snapshot
        self.token_provider = token_provider or (lambda: None)
        self._http_factory = http_factory
        self._connect = connect
        self.silence_timeout = silence_timeout
        self.ping_interval = ping_interval
        self.backoff_start = backoff_start
        self.backoff_max = backoff_max
        self.blocked_backoff = blocked_backoff
        self._clock = clock
        self._wall_clock = wall_clock

        self.stats = FeedStats()
        self._status = FeedStatus.IDLE
        self._status_lock = threading.Lock()
        # One Event per run: a thread that outlives stop()'s join still holds
        # its own (set) event and exits, while a new start() gets a fresh one.
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._invocation = 0
        # Set by the current connection once feed data arrived (LIVE-25).
        self._had_data = False
        # The token the endpoint refused, so it is not sent again (LIVE-26).
        self._rejected_token: str | None = None
        # Why the configured token is not in use (expired/rejected), or None.
        self.token_notice: str | None = None
        # Whether the current connection was made with a token.
        self.sent_token = False

    # ------------------------------------------------------------------ state
    @property
    def status(self) -> FeedStatus:
        with self._status_lock:
            return self._status

    def _set_status(self, status: FeedStatus, stop: threading.Event | None = None) -> None:
        with self._status_lock:
            # Nothing a lingering connection does may overwrite STOPPED (LIVE-33).
            if (stop or self._stop).is_set() and status is not FeedStatus.STOPPED:
                return
            if self._status is not status:
                logger.info("Live feed: %s", STATUS_TEXT[status])
            self._status = status

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Start the background thread (no-op when already running)."""
        if self.is_running() and not self._stop.is_set():
            return
        lingering = self._thread
        if lingering is not None and lingering.is_alive():
            lingering.join(timeout=1.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self.run, args=(self._stop,), name="f1-signalr", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Close the socket and join the thread within ``timeout`` seconds."""
        self._stop.set()
        socket = self._socket
        if socket is not None:
            with contextlib.suppress(Exception):
                socket.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._set_status(FeedStatus.STOPPED)

    def run(self, stop: threading.Event | None = None) -> None:
        """Connect-and-stream loop with backoff. Blocks until stopped."""
        stop = stop or self._stop
        backoff = self.backoff_start
        while not stop.is_set():
            wait = backoff
            started = self._clock()
            try:
                self._stream_once(stop)
                # Only a stop ends a connection without raising.
                wait = self.backoff_start
            except NegotiateError as exc:
                self.stats.last_error = str(exc)
                if exc.status_code == 401 and self.sent_token:
                    # The token was refused: drop it and reconnect at once on
                    # the free feed instead of retrying it for ever (LIVE-26).
                    self._rejected_token = self._current_token()
                    self.token_notice = TOKEN_REJECTED
                    logger.warning("Live feed: token rejected, continuing without it")
                    wait = 0.0
                elif exc.status_code == 401:
                    self._set_status(FeedStatus.AUTH_REQUIRED, stop)
                    wait = self.blocked_backoff
                elif exc.status_code in (403, 429):
                    self._set_status(FeedStatus.BLOCKED, stop)
                    wait = self.blocked_backoff
                else:
                    wait, backoff = self._next_backoff(backoff, started)
                logger.warning("Live feed refused: %s", exc)
            except Exception as exc:  # network errors, closed sockets, bad frames
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Live feed dropped: %s", self.stats.last_error)
                wait, backoff = self._next_backoff(backoff, started)
            finally:
                self._socket = None

            if stop.is_set():
                break
            if self.status not in (FeedStatus.AUTH_REQUIRED, FeedStatus.BLOCKED):
                self._set_status(FeedStatus.RECONNECTING, stop)
            self.stats.reconnects += 1
            self.stats.retry_at = time.time() + wait
            stop.wait(wait)
        self.stats.retry_at = None
        self._set_status(FeedStatus.STOPPED, stop)

    def _next_backoff(self, backoff: float, started: float) -> tuple[float, float]:
        """``(wait now, backoff for next time)`` after a dropped connection.

        A connection that delivered data, or stayed up longer than the
        silence timeout, was healthy: the drop is F1 recycling it (it cuts
        long connections about every two hours), so the next attempt starts
        from ``backoff_start`` again. Before LIVE-25 every real drop raised
        past the reset, the backoff only ever doubled, and late in a weekend
        every reconnect cost a minute of timing.
        """
        healthy = self._had_data or self._clock() - started >= self.silence_timeout
        if healthy:
            backoff = self.backoff_start
        return backoff, min(backoff * 2, self.backoff_max)

    # --------------------------------------------------------------- protocol
    def _next_invocation(self) -> str:
        self._invocation += 1
        return str(self._invocation)

    def _send(self, socket: Any, message: dict) -> None:
        socket.send(json.dumps(message, separators=(",", ":")) + RECORD_SEPARATOR)

    def _current_token(self) -> str | None:
        try:
            return self.token_provider()
        except Exception:
            logger.warning("Live feed: could not read the subscription token")
            return None

    def _token_to_send(self) -> str | None:
        """The configured token, unless it has expired or was refused."""
        token = self._current_token()
        if not token:
            self.token_notice = None
            return None
        if token == self._rejected_token:
            self.token_notice = TOKEN_REJECTED
            return None
        expiry = token_expiry(token)
        if expiry is not None and expiry <= self._wall_clock():
            self.token_notice = TOKEN_EXPIRED
            return None
        self.token_notice = None
        return token

    def subscribe_topics(self, with_token: bool) -> list[str]:
        """The topics one connection subscribes to."""
        if with_token:
            return list(self.topics)
        return [topic for topic in self.topics if topic not in self.auth_topics]

    def _stream_once(self, stop: threading.Event | None = None) -> bool:
        """One connection: negotiate, handshake, subscribe, read until it ends.

        Returns whether any feed data arrived. A stop during negotiate or the
        socket upgrade is honoured as soon as that step returns (LIVE-33):
        nothing is opened, subscribed or seeded after it.
        """
        stop = stop or self._stop
        self._had_data = False
        self._set_status(FeedStatus.CONNECTING, stop)
        token = self._token_to_send()
        self.sent_token = token is not None
        http = self._http_factory()
        try:
            connection_token, headers = negotiate(http, token)
        finally:
            with contextlib.suppress(Exception):
                http.close()
        if stop.is_set():
            return False

        url = f"{CONNECTION_URL}?{urllib.parse.urlencode({'id': connection_token})}"
        try:
            socket = self._connect(url, headers)
        except Exception as exc:
            status = refusal_status(exc)
            if status is not None:
                raise NegotiateError(status, f"websocket upgrade answered {status}") from exc
            raise
        if stop.is_set():
            with contextlib.suppress(Exception):
                socket.close()
            return False
        self._socket = socket
        got_data = False
        try:
            socket.send(HANDSHAKE)
            reply = socket.recv(timeout=HTTP_TIMEOUT)
            for message in split_frames(reply):
                if message.get("error"):
                    raise ConnectionError(f"handshake refused: {message['error']}")

            subscribe_id = self._next_invocation()
            self._send(
                socket,
                {
                    "type": MSG_INVOCATION,
                    "invocationId": subscribe_id,
                    "target": "Subscribe",
                    "arguments": [self.subscribe_topics(self.sent_token)],
                },
            )
            now = self._clock()
            self.stats.connected_at = time.time()
            last_heard = now
            last_ping = now
            last_data = None
            self._set_status(FeedStatus.WAITING, stop)

            while not stop.is_set():
                now = self._clock()
                if now - last_ping >= self.ping_interval:
                    self._send(socket, {"type": MSG_PING})
                    last_ping = now
                if now - last_heard > self.silence_timeout:
                    self._set_status(FeedStatus.STALE, stop)
                    raise TimeoutError(f"no message for {self.silence_timeout:.0f} s")
                try:
                    raw = socket.recv(timeout=1.0)
                except TimeoutError:
                    continue
                last_heard = self._clock()
                self.stats.last_message_at = time.time()
                if self._handle_frame(raw, subscribe_id, stop):
                    got_data = True
                    self._had_data = True
                    last_data = last_heard
                elif (
                    last_data is not None
                    and last_heard - last_data > self.silence_timeout
                    and self.status is FeedStatus.LIVE
                ):
                    # Pings still arrive but the session has gone quiet
                    # (it ended, or is suspended): connected, not live.
                    self._set_status(FeedStatus.WAITING, stop)
            return got_data
        finally:
            with contextlib.suppress(Exception):
                socket.close()

    def _handle_frame(
        self, raw: str | bytes, subscribe_id: str, stop: threading.Event | None = None
    ) -> bool:
        """Dispatch one frame. Returns True when it carried race data."""
        carried = False
        for message in split_frames(raw):
            kind = message.get("type")
            if kind == MSG_INVOCATION and message.get("target") == "feed":
                arguments = message.get("arguments") or []
                if len(arguments) < 2:
                    continue
                topic = str(arguments[0])
                timestamp = str(arguments[2]) if len(arguments) > 2 and arguments[2] else None
                self._count(topic)
                self._dispatch(self.on_message, topic, arguments[1], timestamp)
                carried = True
            elif kind == MSG_COMPLETION:
                if message.get("error"):
                    raise ConnectionError(f"Subscribe failed: {message['error']}")
                result = message.get("result")
                if message.get("invocationId") == subscribe_id and isinstance(result, dict):
                    self.stats.snapshots += 1
                    self._dispatch(self.on_snapshot, result)
                    # Between sessions the snapshot is the last session's
                    # final state; data "arrived", but nothing is live yet.
            elif kind == MSG_CLOSE:
                raise ConnectionError(f"server closed: {message.get('error') or 'no reason'}")
            # MSG_PING needs no reply beyond our own periodic pings.
        if carried:
            self.stats.last_data_at = time.time()
            self._set_status(FeedStatus.LIVE, stop)
        return carried

    def _count(self, topic: str) -> None:
        self.stats.messages += 1
        self.stats.per_topic[topic] = self.stats.per_topic.get(topic, 0) + 1

    @staticmethod
    def _dispatch(callback: Callable, *args: Any) -> None:
        """A handler bug must not kill the connection; it is logged instead."""
        try:
            callback(*args)
        except Exception:
            logger.exception("Live feed handler failed")
