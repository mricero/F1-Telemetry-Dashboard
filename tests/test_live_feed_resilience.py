"""Reconnect, token and stop behaviour of the SignalR Core client.

IMPROVEMENTS.md revision 4: SEC-01 (no token in DEBUG logs), LIVE-25 (backoff
resets after a healthy connection), LIVE-26 (a rejected or expired token
falls back to the free feed), LIVE-30 (refusals at the websocket upgrade wait
like refused negotiates), LIVE-33 (stop is honoured during negotiate/connect).
"""

import base64
import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from data.signalr_core import (
    AUTH_TOPICS,
    RECORD_SEPARATOR,
    TOKEN_EXPIRED,
    TOKEN_REJECTED,
    FeedStatus,
    SignalRCoreClient,
    _default_connect,
    split_frames,
)
from tests.test_signalr_core import FakeHTTP, FakeResponse, FakeSocket, feed, frame

RS = RECORD_SEPARATOR


def _jwt(exp: datetime) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(exp.timestamp())}).encode())
    return f"hdr.{claims.decode().rstrip('=')}.sig"


class _Sleepless:
    """Records every backoff wait instead of sleeping through it."""

    def __init__(self, limit: int):
        self.waits: list[float] = []
        self.limit = limit
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if len(self.waits) >= self.limit:
            self._set = True
        return self._set


def _client(connect, http_factory=FakeHTTP, **kwargs):
    return SignalRCoreClient(
        topics=["TimingData", "TrackStatus", "CarData.z"],
        on_message=lambda *a: None,
        on_snapshot=lambda r: None,
        http_factory=http_factory,
        connect=connect,
        **kwargs,
    )


class TestBackoffResets:
    """LIVE-25: measured before the fix as 1, 2, 4, 8, 16, 32, 60, 60 s."""

    def test_connections_that_delivered_data_always_retry_from_the_start(self):
        def connect(url, headers):
            return FakeSocket(["{}" + RS, frame(feed("TrackStatus", {"Status": "1"}))])

        client = _client(connect, backoff_start=1.0, backoff_max=60.0)
        stop = _Sleepless(limit=8)
        client.run(stop)

        assert stop.waits == [1.0] * 8

    def test_a_socket_that_drops_without_data_still_doubles(self):
        client = _client(lambda url, headers: FakeSocket(["{}" + RS]), backoff_start=1.0)
        stop = _Sleepless(limit=5)
        client.run(stop)

        assert stop.waits == [1.0, 2.0, 4.0, 8.0, 16.0]

    def test_a_long_quiet_connection_counts_as_healthy(self):
        now = [0.0]

        class Quiet(FakeSocket):
            def recv(self, timeout=None):
                now[0] += 30.0  # pings only, for a long time
                return super().recv(timeout)

        def connect(url, headers):
            return Quiet(["{}" + RS] + [frame({"type": 6})] * 4)

        client = _client(connect, backoff_start=1.0, clock=lambda: now[0], silence_timeout=60)
        stop = _Sleepless(limit=4)
        client.run(stop)

        assert stop.waits == [1.0] * 4


class TestTokenFallback:
    """LIVE-26: timing, race control and weather need no token."""

    class AuthRefusingHTTP(FakeHTTP):
        def post(self, url, params=None, headers=None, timeout=None):
            self.calls.append(("POST", url, dict(headers or {}), params))
            if "Authorization" in (headers or {}):
                return FakeResponse(401)
            return FakeResponse(200, {"connectionToken": "conn-token"})

    def test_a_rejected_token_falls_back_at_once_to_the_free_feed(self):
        sockets = []

        def connect(url, headers):
            assert "Authorization" not in headers
            sockets.append(FakeSocket(["{}" + RS, frame(feed("TrackStatus", {"Status": "1"}))]))
            return sockets[-1]

        http = self.AuthRefusingHTTP()
        client = _client(
            connect,
            http_factory=lambda: http,
            token_provider=lambda: "a.b.c",
            backoff_start=1.0,
        )
        stop = _Sleepless(limit=2)
        client.run(stop)

        assert stop.waits[0] == 0.0, "the token-less retry must not wait"
        assert sockets, "it connected without the token"
        subscribe = split_frames(sockets[0].sent[1])[0]["arguments"][0]
        assert "CarData.z" not in subscribe and "TimingData" in subscribe
        assert client.token_notice == TOKEN_REJECTED
        assert client.status is not FeedStatus.AUTH_REQUIRED

    def test_the_fallback_reaches_live_within_the_first_backoff(self):
        http = self.AuthRefusingHTTP()
        seen = threading.Event()

        def connect(url, headers):
            return FakeSocket(
                ["{}" + RS, frame(feed("TrackStatus", {"Status": "1"}))],
                end=ConnectionError("done"),
            )

        client = _client(
            connect,
            http_factory=lambda: http,
            token_provider=lambda: "a.b.c",
            backoff_start=5.0,
        )
        client.on_message = lambda *a: seen.set()
        started = time.time()
        client.start()
        assert seen.wait(2.0)
        elapsed = time.time() - started
        client.stop()

        assert elapsed < client.backoff_start

    def test_an_expired_token_is_not_sent(self):
        posts = []

        class Recording(FakeHTTP):
            def post(self, url, params=None, headers=None, timeout=None):
                posts.append(dict(headers or {}))
                return super().post(url, params, headers, timeout)

        expired = _jwt(datetime.now(UTC) - timedelta(hours=1))
        socket = FakeSocket(["{}" + RS])
        client = _client(
            lambda u, h: socket, http_factory=Recording, token_provider=lambda: expired
        )

        with pytest.raises(ConnectionError):
            client._stream_once()

        assert "Authorization" not in posts[0]
        assert client.token_notice == TOKEN_EXPIRED
        subscribe = split_frames(socket.sent[1])[0]["arguments"][0]
        assert not set(subscribe) & AUTH_TOPICS

    def test_a_valid_token_subscribes_to_the_gated_topics(self):
        valid = _jwt(datetime.now(UTC) + timedelta(days=2))
        socket = FakeSocket(["{}" + RS])
        client = _client(lambda u, h: socket, token_provider=lambda: valid)

        with pytest.raises(ConnectionError):
            client._stream_once()

        assert "CarData.z" in split_frames(socket.sent[1])[0]["arguments"][0]
        assert client.token_notice is None

    def test_a_new_token_is_tried_after_a_rejection(self):
        tokens = ["a.b.c"]
        client = _client(lambda u, h: FakeSocket([]), token_provider=lambda: tokens[0])
        client._rejected_token = "a.b.c"

        assert client._token_to_send() is None
        tokens[0] = "d.e.f"
        assert client._token_to_send() == "d.e.f"


class _InvalidStatus(Exception):
    """Shaped like websockets.exceptions.InvalidStatus."""

    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.response = type("Response", (), {"status_code": status_code})()


class TestUpgradeRefusals:
    """LIVE-30: a 403 on the upgrade used to be retried at 1, 2, 4 ... s."""

    @pytest.mark.parametrize("status", [403, 429])
    def test_a_refused_upgrade_backs_off_like_a_refused_negotiate(self, status):
        attempts = []

        def connect(url, headers):
            attempts.append(time.time())
            raise _InvalidStatus(status)

        client = _client(connect, backoff_start=0.01, blocked_backoff=30.0)
        client.start()
        deadline = time.time() + 2
        while client.status is not FeedStatus.BLOCKED and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
        seen = client.status
        client.stop()

        assert seen is FeedStatus.BLOCKED
        assert len(attempts) == 1

    def test_the_real_websockets_exception_carries_the_status(self):
        from websockets.datastructures import Headers
        from websockets.exceptions import InvalidStatus
        from websockets.http11 import Response

        from data.signalr_core import refusal_status

        exc = InvalidStatus(Response(403, "Forbidden", Headers()))
        assert refusal_status(exc) == 403
        assert refusal_status(ConnectionError("x")) is None


class TestStopDuringConnect:
    """LIVE-33: stop() during a slow negotiate must not open a socket later."""

    def test_stop_during_negotiate_never_connects(self):
        connects = []

        class SlowHTTP(FakeHTTP):
            def post(self, *args, **kwargs):
                time.sleep(1.0)
                return super().post(*args, **kwargs)

        def connect(url, headers):
            connects.append(url)
            return FakeSocket([])

        client = _client(connect, http_factory=SlowHTTP)
        client.start()
        time.sleep(0.2)
        client.stop(timeout=5.0)
        time.sleep(0.2)

        assert connects == []
        assert client.status is FeedStatus.STOPPED
        assert not client.is_running()

    def test_a_late_status_never_overwrites_stopped(self):
        client = _client(lambda u, h: FakeSocket([]))
        client._stop.set()
        client._set_status(FeedStatus.STOPPED)
        client._set_status(FeedStatus.WAITING)

        assert client.status is FeedStatus.STOPPED

    def test_start_after_stop_gets_a_fresh_run(self):
        def connect(url, headers):
            return FakeSocket(["{}" + RS], end=TimeoutError("idle"))

        client = _client(connect, backoff_start=0.05)
        client.start()
        time.sleep(0.1)
        client.stop()
        client.start()
        time.sleep(0.1)

        assert client.is_running()
        client.stop()
        assert not client.is_running()


class TestTokenNeverLogged:
    """SEC-01: websockets logs handshake headers at DEBUG."""

    def test_debug_logging_does_not_capture_the_bearer_token(self, caplog):
        from websockets.sync.server import serve

        secret = "eyJsecret.payload-value.signature"
        # The test server logs what it receives; only the client is under test.
        quiet = logging.getLogger("tests.ws_server")
        quiet.setLevel(logging.CRITICAL)
        with serve(lambda ws: ws.close(), "127.0.0.1", 0, logger=quiet) as server:
            port = server.socket.getsockname()[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            caplog.set_level(logging.DEBUG)
            socket = _default_connect(
                f"ws://127.0.0.1:{port}/signalrcore?id=conn-secret-id",
                {"Authorization": f"Bearer {secret}", "User-Agent": "BestHTTP"},
            )
            socket.close()
            server.shutdown()

        assert secret not in caplog.text
        assert "conn-secret-id" not in caplog.text

    def test_the_redacting_filter_masks_what_gets_through(self):
        from data.signalr_core import _RedactSecrets

        record = logging.LogRecord(
            "websockets",
            logging.WARNING,
            __file__,
            1,
            "> Authorization: Bearer %s",
            ("abc.def",),
            None,
        )
        _RedactSecrets().filter(record)

        assert "abc.def" not in record.getMessage()
        assert "<redacted>" in record.getMessage()
