"""Recording and replaying a raw live stream (IMPROVEMENTS.md LIVE-12).

"Save Raw Stream" only printed advice, and the live branch returned before
the controls rendered at all, so none of it was reachable. Recording the raw
messages - the format undercut-f1 uses - means a replay feeds exactly the
same handler the live client does.
"""

import json

import pytest

from data.live_adapter import SignalRLiveAdapter
from data.live_recorder import LiveRecorder, replay_recording
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

        first = json.loads((recorder.directory / "live.jsonl").read_text("utf-8").splitlines()[0])

        assert len(first) == 3
        assert first[0] in TOPICS

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

    def test_the_live_branch_renders_the_controls(self):
        import inspect

        import app

        source = inspect.getsource(app.main)
        live_branch = source[source.index('if session_data.get("is_live"):') :]
        lines = [line.strip() for line in live_branch.splitlines()]
        controls = lines.index("render_live_controls(live_client)")
        # The bare `return` that ends the live branch, not the word in a comment.
        returns = lines.index("return")

        assert controls < returns, "the controls are still behind the return"

    def test_the_controls_offer_recording(self, tmp_path, monkeypatch):
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("REPLAY_DIR", str(tmp_path))

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
        labels = [button.label for button in app_test.button]
        assert "Record raw stream" in labels
        assert "Stop live" in labels
