"""Recording and replaying a raw live stream (IMPROVEMENTS.md LIVE-12).

"Save Raw Stream" only printed advice, and the live branch returned before
the controls rendered at all, so none of it was reachable. Recording the raw
messages - the format undercut-f1 uses - means a replay feeds exactly the
same handler the live client does.
"""

import json

import pytest

from data.live_adapter import SignalRLiveAdapter
from data.live_recorder import SNAPSHOT_MARKER, LiveRecorder, replay_recording
from tests import live_fixtures

TOPICS = ("SessionInfo", "DriverList", "TimingData", "TyreStintSeries")


def _record_fixture(directory) -> LiveRecorder:
    recorder = LiveRecorder(directory)
    recorder.record_snapshot({"SessionInfo": live_fixtures.first_payload("SessionInfo")})
    for topic in TOPICS:
        for timestamp, payload in live_fixtures.messages(topic):
            recorder.record(topic, payload, timestamp)
    recorder.close()
    return recorder


class TestRecordingFormat:
    def test_it_writes_a_snapshot_and_a_jsonl_stream(self, tmp_path):
        recorder = _record_fixture(tmp_path)

        assert (recorder.directory / "subscribe.json").is_file()
        assert (recorder.directory / "live.jsonl").is_file()

    def test_each_line_is_topic_data_timestamp(self, tmp_path):
        recorder = _record_fixture(tmp_path)

        lines = [
            json.loads(line)
            for line in (recorder.directory / "live.jsonl").read_text("utf-8").splitlines()
        ]

        # The recording opens with the state it started from (LIVE-32) ...
        assert lines[0][0] == SNAPSHOT_MARKER
        # ... then one [topic, data, timestamp] per message.
        assert all(len(line) == 3 for line in lines)
        assert lines[1][0] in TOPICS

    def test_recording_is_append_only(self, tmp_path):
        recorder = LiveRecorder(tmp_path)
        recorder.record("TrackStatus", {"Status": "1"}, "00:00:01")
        recorder.record("TrackStatus", {"Status": "2"}, "00:00:02")
        recorder.close()

        lines = (recorder.directory / "live.jsonl").read_text("utf-8").splitlines()
        assert [json.loads(line)[1]["Status"] for line in lines] == ["1", "2"]

    def test_a_recorder_reports_what_it_captured(self, tmp_path):
        recorder = _record_fixture(tmp_path)

        assert recorder.message_count == sum(len(live_fixtures.messages(t)) for t in TOPICS)


class TestReplay:
    def test_replay_reproduces_the_final_state(self, tmp_path):
        recorder = _record_fixture(tmp_path)

        live = SignalRLiveAdapter()
        for topic in TOPICS:
            for timestamp, payload in live_fixtures.messages(topic):
                live.handle_message(topic, payload, timestamp)

        replayed = replay_recording(recorder.directory)

        for topic in TOPICS:
            assert replayed.state.get(topic) == live.state.get(topic)

    def test_replay_reproduces_the_lap_history(self, tmp_path):
        recorder = _record_fixture(tmp_path)

        replayed = replay_recording(recorder.directory)
        live = SignalRLiveAdapter()
        for topic in TOPICS:
            for timestamp, payload in live_fixtures.messages(topic):
                live.handle_message(topic, payload, timestamp)

        assert replayed.recorded_laps() == live.recorded_laps()

    def test_replay_seeds_the_subscription_snapshot(self, tmp_path):
        directory = tmp_path / "rec"
        recorder = LiveRecorder(directory)
        recorder.record_snapshot({"TrackStatus": {"Status": "1"}})
        recorder.close()

        replayed = replay_recording(directory)

        assert replayed.state.get("TrackStatus") == {"Status": "1"}

    def test_replaying_a_missing_directory_raises_clearly(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="nothing-here"):
            replay_recording(tmp_path / "nothing-here")

    def test_a_partial_line_does_not_abort_the_replay(self, tmp_path):
        recorder = LiveRecorder(tmp_path)
        recorder.record("TrackStatus", {"Status": "1"}, "00:00:01")
        recorder.close()
        with open(recorder.directory / "live.jsonl", "a", encoding="utf-8") as handle:
            handle.write('["TrackStatus", {"Status": "2"\n')  # torn write

        replayed = replay_recording(recorder.directory)

        assert replayed.state.get("TrackStatus") == {"Status": "1"}


class TestAdapterIntegration:
    def test_an_attached_recorder_captures_what_arrives(self, tmp_path):
        adapter = SignalRLiveAdapter()
        adapter.start_recording(tmp_path)
        adapter.handle_message("TrackStatus", {"Status": "2"}, "00:00:05")
        adapter.stop_recording()

        replayed = replay_recording(tmp_path)

        assert replayed.state.get("TrackStatus") == {"Status": "2"}

    def test_recording_is_off_by_default(self, tmp_path):
        adapter = SignalRLiveAdapter()
        adapter.handle_message("TrackStatus", {"Status": "2"}, "00:00:05")

        assert adapter.recorder is None
        assert not list(tmp_path.iterdir())


class TestControlsAreReachable:
    """LIVE-12: main() returned before the live controls ever rendered."""

    def test_the_live_page_renders_the_controls(self):
        import inspect

        from ui.pages import live_page

        lines = [line.strip() for line in inspect.getsource(live_page).splitlines()]

        # Unconditional, and nothing returns before it (UI-03 moved the live
        # branch of main() onto its own page).
        assert "render_live_controls(live_client)" in lines
        assert "return" not in lines

    @staticmethod
    def _controls(tmp_path, monkeypatch, allowed: bool) -> list[str]:
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
        if allowed:
            monkeypatch.setenv("F1_LIVE_CONTROLS", "1")
        else:
            monkeypatch.delenv("F1_LIVE_CONTROLS", raising=False)

        def script():
            import streamlit as st

            from data.live_adapter import SignalRLiveAdapter
            from ui.layout import render_live_controls

            adapter = SignalRLiveAdapter()
            st.session_state["adapter"] = adapter
            render_live_controls(adapter)

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()

        assert not app_test.exception
        return [button.label for button in app_test.button]

    def test_the_controls_are_hidden_from_other_viewers(self, tmp_path, monkeypatch):
        """LIVE-29: without the flag (and with no localhost browser) nobody
        but the person running the app can stop or clear the shared feed."""
        assert self._controls(tmp_path, monkeypatch, allowed=False) == []

    def test_the_controls_offer_recording(self, tmp_path, monkeypatch):
        labels = self._controls(tmp_path, monkeypatch, allowed=True)
        assert "Record raw stream" in labels
        assert "Stop live" in labels


class TestWhoMayControlTheFeed:
    """LIVE-29: the process-level controls are for the machine running the app."""

    def test_the_flag_grants_control(self):
        from ui.layout import live_controls_allowed

        assert live_controls_allowed({"F1_LIVE_CONTROLS": "1"}, url=None, ip="10.0.0.5")

    def test_a_localhost_browser_on_a_loopback_socket_may_control(self):
        from ui.layout import live_controls_allowed

        assert live_controls_allowed({}, url="http://localhost:8501/", ip=None)
        assert live_controls_allowed({}, url="http://127.0.0.1:8501/live", ip=None)

    def test_remote_viewers_only_read(self):
        from ui.layout import live_controls_allowed

        assert not live_controls_allowed({}, url="http://192.168.1.4:8501/", ip=None)
        # A localhost URL through a proxy still arrives from a real address.
        assert not live_controls_allowed({}, url="http://localhost:8501/", ip="203.0.113.9")
        assert not live_controls_allowed({}, url=None, ip=None)
