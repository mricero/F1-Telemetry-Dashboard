"""SignalR Core client for F1 live timing (IMPROVEMENTS.md LIVE-01, LIVE-08).

The protocol is driven end to end against fakes that behave like the real
endpoint: an ``OPTIONS`` that sets the ``AWSALBCORS`` cookie, a ``POST``
negotiate that returns a ``connectionToken``, and a socket that answers the
JSON handshake with ``{}``, completes ``Subscribe`` with the full state and
then calls ``feed`` with ``[topic, data, timestamp]`` - frames separated by
``0x1E``. The recorded 2023 Bahrain messages are the payloads, so the shapes
are the feed's own.
"""

import json
import threading
import time
from datetime import UTC, datetime

import pandas as pd
import pytest

from f1dash.data.live_adapter import LiveDataProcessor, SignalRLiveAdapter
from f1dash.data.signalr_core import (
    CONNECTION_URL,
    HANDSHAKE,
    RECORD_SEPARATOR,
    FeedStatus,
    NegotiateError,
    SignalRCoreClient,
    negotiate,
    split_frames,
    token_expiry,
    token_from_env_value,
)
from f1dash.data.source_manager import DataSourceManager, extrapolated_remaining
from tests import live_fixtures

RS = RECORD_SEPARATOR


class FakeResponse:
    def __init__(self, status_code=200, body=None, cookies=None):
        self.status_code = status_code
        self._body = body or {}
        self.cookies = cookies or {}

    def json(self):
        return self._body


class FakeHTTP:
    """``requests.Session`` stand-in recording what negotiate sends."""

    def __init__(self, post_status=200, token="conn-token"):
        self.post_status = post_status
        self.token = token
        self.calls = []

    def options(self, url, headers=None, timeout=None):
        self.calls.append(("OPTIONS", url, dict(headers or {}), None))
        return FakeResponse(405, cookies={"AWSALBCORS": "lb-cookie"})

    def post(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("POST", url, dict(headers or {}), params))
        return FakeResponse(self.post_status, {"connectionToken": self.token})

    def close(self):
        pass


class FakeSocket:
    """A websocket that plays scripted frames, then raises ``end``."""

    def __init__(self, frames, end=None):
        self.frames = list(frames)
        self.end = end or ConnectionError("socket closed")
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        if self.closed:
            raise ConnectionError("closed")
        if self.frames:
            return self.frames.pop(0)
        raise self.end

    def close(self):
        self.closed = True


def frame(*messages) -> str:
    return "".join(json.dumps(m) + RS for m in messages)


def feed(topic, data, timestamp="2026-09-26T08:05:00.123Z") -> dict:
    return {"type": 1, "target": "feed", "arguments": [topic, data, timestamp]}


def completion(result, invocation_id="1") -> dict:
    return {"type": 3, "invocationId": invocation_id, "result": result}


def make_client(socket, http=None, token=None, **kwargs):
    received, snapshots, urls = [], [], []
    http = http or FakeHTTP()

    def connect(url, headers):
        urls.append((url, headers))
        return socket

    client = SignalRCoreClient(
        topics=["TimingData", "TrackStatus"],
        on_message=lambda t, d, ts: received.append((t, d, ts)),
        on_snapshot=snapshots.append,
        token_provider=lambda: token,
        http_factory=lambda: http,
        connect=connect,
        **kwargs,
    )
    return client, received, snapshots, urls, http


class TestNegotiate:
    def test_collects_the_load_balancer_cookie_and_posts_version_1(self):
        http = FakeHTTP()
        token, headers = negotiate(http)

        assert token == "conn-token"
        assert headers["Cookie"] == "AWSALBCORS=lb-cookie"
        assert headers["User-Agent"] == "BestHTTP"
        method, url, _, params = http.calls[-1]
        assert method == "POST" and url.endswith("/signalrcore/negotiate")
        assert params == {"negotiateVersion": "1"}

    def test_a_token_is_sent_as_a_bearer_header(self):
        http = FakeHTTP()
        _, headers = negotiate(http, token="a.b.c")

        assert headers["Authorization"] == "Bearer a.b.c"
        assert http.calls[-1][2]["Authorization"] == "Bearer a.b.c"

    @pytest.mark.parametrize("status", [401, 403, 500])
    def test_refusals_raise_with_the_status(self, status):
        with pytest.raises(NegotiateError) as error:
            negotiate(FakeHTTP(post_status=status))
        assert error.value.status_code == status


class TestProtocol:
    def test_handshake_subscribe_snapshot_and_feed(self):
        socket = FakeSocket(
            [
                "{}" + RS,  # handshake accepted
                frame(completion({"TrackStatus": {"Status": "1", "Message": "AllClear"}})),
                frame(
                    feed("TrackStatus", {"Status": "4", "Message": "SCDeployed"}),
                    {"type": 6},
                    feed("TimingData", {"Lines": {"1": {"Position": "1"}}}),
                ),
            ]
        )
        client, received, snapshots, urls, _ = make_client(socket)

        with pytest.raises(ConnectionError):
            client._stream_once()

        assert urls[0][0] == f"{CONNECTION_URL}?id=conn-token"
        assert urls[0][1]["Cookie"] == "AWSALBCORS=lb-cookie"
        assert socket.sent[0] == HANDSHAKE
        subscribe = split_frames(socket.sent[1])[0]
        assert subscribe["type"] == 1 and subscribe["target"] == "Subscribe"
        assert subscribe["arguments"] == [["TimingData", "TrackStatus"]]
        assert snapshots == [{"TrackStatus": {"Status": "1", "Message": "AllClear"}}]
        assert [topic for topic, _, _ in received] == ["TrackStatus", "TimingData"]
        assert received[0][2] == "2026-09-26T08:05:00.123Z"
        assert client.status is FeedStatus.LIVE
        assert client.stats.per_topic == {"TrackStatus": 1, "TimingData": 1}

    def test_a_refused_handshake_is_an_error(self):
        socket = FakeSocket(['{"error":"protocol not supported"}' + RS])
        client, *_ = make_client(socket)

        with pytest.raises(ConnectionError, match="handshake refused"):
            client._stream_once()

    def test_a_server_close_message_ends_the_connection(self):
        socket = FakeSocket(["{}" + RS, frame({"type": 7, "error": "Server shutting down"})])
        client, *_ = make_client(socket)

        with pytest.raises(ConnectionError, match="Server shutting down"):
            client._stream_once()

    def test_pings_only_means_connected_but_waiting(self):
        socket = FakeSocket(["{}" + RS, frame({"type": 6}), frame({"type": 6})])
        client, received, *_ = make_client(socket)

        with pytest.raises(ConnectionError):
            client._stream_once()

        assert received == []
        assert client.status is FeedStatus.WAITING

    def test_a_silent_socket_is_treated_as_dead(self):
        now = [0.0]

        class Silent(FakeSocket):
            def recv(self, timeout=None):
                if self.frames:
                    return self.frames.pop(0)
                now[0] += 10.0  # ten seconds pass per empty read
                raise TimeoutError

        client, *_ = make_client(Silent(["{}" + RS]), clock=lambda: now[0], silence_timeout=30)

        with pytest.raises(TimeoutError):
            client._stream_once()
        assert client.status is FeedStatus.STALE

    def test_the_client_pings_the_server(self):
        now = [0.0]

        class Ticking(FakeSocket):
            def recv(self, timeout=None):
                now[0] += 4.0
                return super().recv(timeout)

        frames = ["{}" + RS] + [frame({"type": 6})] * 6
        socket = Ticking(frames)
        client, *_ = make_client(socket, clock=lambda: now[0], ping_interval=10)

        with pytest.raises(ConnectionError):
            client._stream_once()

        pings = [m for raw in socket.sent[2:] for m in split_frames(raw) if m.get("type") == 6]
        assert len(pings) >= 2

    def test_a_handler_bug_does_not_drop_the_connection(self):
        socket = FakeSocket(["{}" + RS, frame(feed("TimingData", {}), feed("TrackStatus", {}))])
        seen = []

        def handler(topic, data, ts):
            seen.append(topic)
            if topic == "TimingData":
                raise ValueError("bad handler")

        client = SignalRCoreClient(
            topics=["TimingData"],
            on_message=handler,
            on_snapshot=lambda r: None,
            http_factory=FakeHTTP,
            connect=lambda url, headers: socket,
        )
        with pytest.raises(ConnectionError):
            client._stream_once()
        assert seen == ["TimingData", "TrackStatus"]


class TestReconnect:
    def test_it_reconnects_after_a_drop_and_stops_on_request(self):
        sockets = []

        def connect(url, headers):
            sockets.append(FakeSocket(["{}" + RS, frame(feed("TrackStatus", {"Status": "1"}))]))
            return sockets[-1]

        client = SignalRCoreClient(
            topics=["TrackStatus"],
            on_message=lambda *a: None,
            on_snapshot=lambda r: None,
            http_factory=FakeHTTP,
            connect=connect,
            backoff_start=0.01,
            backoff_max=0.02,
        )
        client.start()
        deadline = time.time() + 5
        while len(sockets) < 3 and time.time() < deadline:
            time.sleep(0.01)
        started = time.time()
        client.stop()

        assert len(sockets) >= 3, "it should keep reconnecting after drops"
        assert time.time() - started < 5
        assert not client.is_running()
        assert client.status is FeedStatus.STOPPED

    @pytest.mark.parametrize(
        ("status", "expected"), [(401, FeedStatus.AUTH_REQUIRED), (403, FeedStatus.BLOCKED)]
    )
    def test_refusals_back_off_instead_of_retrying_hot(self, status, expected):
        attempts = []

        class Counting(FakeHTTP):
            def post(self, *args, **kwargs):
                attempts.append(time.time())
                return super().post(*args, **kwargs)

        client = SignalRCoreClient(
            topics=["TrackStatus"],
            on_message=lambda *a: None,
            on_snapshot=lambda r: None,
            http_factory=lambda: Counting(post_status=status),
            connect=lambda url, headers: FakeSocket([]),
            backoff_start=0.01,
            blocked_backoff=30.0,
        )
        client.start()
        deadline = time.time() + 2
        while client.status is not expected and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
        status_seen = client.status
        client.stop()

        assert status_seen is expected
        assert len(attempts) == 1, "a refused client must wait, not hammer the endpoint"


class TestTokens:
    def test_a_bare_jwt_passes_through(self):
        assert token_from_env_value("  aaa.bbb.ccc ") == "aaa.bbb.ccc"

    def test_the_login_session_cookie_yields_the_subscription_token(self):
        cookie = "%7B%22data%22%3A%7B%22subscriptionToken%22%3A%22x.y.z%22%7D%7D"

        assert token_from_env_value(cookie) == "x.y.z"

    def test_empty_values_mean_no_token(self):
        assert token_from_env_value("") is None
        assert token_from_env_value(None) is None

    def test_expiry_is_read_from_the_claims(self):
        import base64

        exp = int(datetime(2026, 9, 30, tzinfo=UTC).timestamp())
        claims = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")

        assert token_expiry(f"h.{claims}.s") == datetime(2026, 9, 30, tzinfo=UTC)
        assert token_expiry("not-a-jwt") is None


class TestSessionClock:
    def test_a_running_clock_counts_down_from_its_stamp(self):
        clock = {"Utc": "2026-09-26T08:00:00Z", "Remaining": "01:00:00", "Extrapolating": True}
        now = pd.Timestamp("2026-09-26T08:10:00Z")

        assert extrapolated_remaining(clock, now) == "0:50:00"

    def test_a_stopped_clock_holds(self):
        clock = {"Utc": "2026-09-26T08:00:00Z", "Remaining": "00:18:00", "Extrapolating": False}

        assert extrapolated_remaining(clock, pd.Timestamp("2026-09-26T09:00:00Z")) == "0:18:00"

    def test_it_never_goes_negative(self):
        clock = {"Utc": "2026-09-26T08:00:00Z", "Remaining": "00:01:00", "Extrapolating": True}

        assert extrapolated_remaining(clock, pd.Timestamp("2026-09-26T10:00:00Z")) == "0:00:00"


class TestStandings:
    def test_race_order_gaps_and_status_come_from_the_timing_screen(self):
        timing = {
            "Lines": {
                "16": {
                    "Position": "1",
                    "GapToLeader": "LAP 23",
                    "IntervalToPositionAhead": {"Value": "LAP 23"},
                    "LastLapTime": {"Value": "1:45.123", "OverallFastest": True},
                    "BestLapTime": {"Value": "1:45.123"},
                    "NumberOfPitStops": 1,
                },
                "1": {
                    "Position": "2",
                    "GapToLeader": "+1.234",
                    "IntervalToPositionAhead": {"Value": "+1.234"},
                    "InPit": True,
                },
                "44": {"Position": "3", "GapToLeader": "1 L", "Retired": True},
            }
        }
        frame = LiveDataProcessor.standings_from_state(
            timing, {"16": "LEC", "1": "VER", "44": "HAM"}, race=True
        )

        assert list(frame["Driver"]) == ["LEC", "VER", "HAM"]
        assert list(frame["Gap"]) == ["LEADER", "+1.234", "+1 LAP"]
        assert list(frame["Status"]) == ["ON TRACK", "IN PIT", "OUT"]
        assert frame.loc[0, "LastFlag"] == "sb"
        assert frame.loc[0, "Pits"] == 1

    def test_practice_reads_the_difference_to_the_fastest_lap(self):
        timing = {
            "Lines": {
                "4": {"Position": "2", "TimeDiffToFastest": "+0.345"},
                "81": {"Position": "1", "TimeDiffToFastest": ""},
            }
        }
        frame = LiveDataProcessor.standings_from_state(
            timing, {"4": "NOR", "81": "PIA"}, race=False
        )

        assert list(frame["Driver"]) == ["PIA", "NOR"]
        assert list(frame["Gap"]) == ["LEADER", "+0.345"]


def _fixture_frames() -> list[str]:
    """The recorded 2023 Bahrain messages as SignalR Core frames, in order."""
    messages = []
    for topic in live_fixtures.available_topics():
        for timestamp, payload in live_fixtures.messages(topic):
            messages.append((timestamp, topic, payload))
    messages.sort(key=lambda item: item[0])
    return [frame(feed(topic, payload, stamp)) for stamp, topic, payload in messages]


class TestEndToEnd:
    """Frames -> client -> adapter -> poll_live_data -> the unified dict."""

    @pytest.fixture
    def snapshot(self, monkeypatch):
        monkeypatch.setenv("F1TV_SUBSCRIPTION_TOKEN", "a.b.c")  # subscribe to car data
        socket = FakeSocket(["{}" + RS, *_fixture_frames()])
        adapter = SignalRLiveAdapter(
            client_factory=lambda **kw: SignalRCoreClient(
                **kw, http_factory=FakeHTTP, connect=lambda url, headers: socket
            )
        )
        client = adapter._make_client(adapter.subscribed_topics())
        adapter.client = client
        with pytest.raises(ConnectionError):
            client._stream_once()

        monkeypatch.setattr(
            "f1dash.data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        manager = DataSourceManager(live_adapter=adapter)
        return adapter, manager.poll_live_data()

    def test_session_and_drivers(self, snapshot):
        _, data = snapshot
        assert data["session_info"]["gp"] == "Bahrain Grand Prix"
        assert len(data["drivers"]) >= 20

    def test_the_tower_is_ordered_by_the_timing_screen(self, snapshot):
        _, data = snapshot
        standings = data["standings"]
        assert not standings.empty
        assert list(standings["Position"]) == list(range(1, len(standings) + 1))
        assert standings.loc[0, "Gap"] == "LEADER"

    def test_race_control_and_track_status_arrive(self, snapshot):
        _, data = snapshot
        assert not data["race_control"].empty
        assert data["session_info"]["track_status"]["status"] in {"1", "2", "4", "5", "6", "7"}

    def test_compressed_car_data_and_positions_are_decoded(self, snapshot):
        adapter, data = snapshot
        assert adapter.get_buffered_data("CarData.z"), "CarData.z must be decoded into records"
        assert adapter.get_buffered_data("Position.z")
        assert data["telemetry"], "car telemetry reaches the snapshot"

    def test_weather_samples_keep_their_timestamp(self, snapshot):
        adapter, data = snapshot
        record = adapter.get_buffered_data("WeatherData")[-1]
        assert record["timestamp"] and "AirTemp" in record
        assert not data["weather"].empty


class TestAdapterLifecycle:
    def test_start_status_and_stop(self):
        started = threading.Event()

        class Stub:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.status = FeedStatus.CONNECTING
                self.alive = False

            def start(self):
                self.alive = True
                started.set()

            def is_running(self):
                return self.alive

            def stop(self):
                self.alive = False
                self.status = FeedStatus.STOPPED

        adapter = SignalRLiveAdapter(client_factory=Stub)
        adapter.start_async()

        assert started.is_set() and adapter.is_running()
        assert adapter.status() is FeedStatus.CONNECTING
        assert "TimingData" in adapter.client.kwargs["topics"]
        adapter.stop()
        assert not adapter.is_running()
        assert adapter.status_text() == "Stopped"

    def test_gated_topics_are_only_subscribed_with_a_token(self, monkeypatch):
        monkeypatch.delenv("F1TV_SUBSCRIPTION_TOKEN", raising=False)
        topics = SignalRLiveAdapter().subscribed_topics()
        assert "CarData.z" not in topics and "TimingData" in topics

        monkeypatch.setenv("F1TV_SUBSCRIPTION_TOKEN", "a.b.c")
        assert "CarData.z" in SignalRLiveAdapter().subscribed_topics()

    def test_a_reconnect_snapshot_does_not_duplicate_race_control(self):
        adapter = SignalRLiveAdapter()
        messages = {"Messages": [{"Utc": "2026-09-26T08:00:00", "Message": "GREEN LIGHT"}]}
        adapter.seed_state({"RaceControlMessages": messages})
        adapter.handle_message(
            "RaceControlMessages", {"Messages": {"1": {"Message": "DRS ENABLED"}}}, None
        )
        adapter.seed_state(
            {
                "RaceControlMessages": {
                    "Messages": [
                        {"Utc": "2026-09-26T08:00:00", "Message": "GREEN LIGHT"},
                        {"Message": "DRS ENABLED"},
                    ]
                }
            }
        )

        from f1dash.data.live_state import as_list

        assert len(as_list(adapter.state.get("RaceControlMessages")["Messages"])) == 2
