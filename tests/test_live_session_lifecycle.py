"""Live state across sessions, recordings and viewers (IMPROVEMENTS.md rev. 4).

LIVE-27 session changes reset state; LIVE-29 Clear keeps the tower; LIVE-31 a
failing recorder cannot freeze ingest; LIVE-32 reconnect snapshots replay in
place; LIVE-34 weather values are numeric; LIVE-20 automatic recording per
session; LIVE-36 one build per change token for every tab; LIVE-21 broadcast
delay; LIVE-23 the heartbeat stays fresh; LIVE-19 live qualifying segments;
LIVE-22 live circuit info; CORE-02 the lap flags are plain bools.
"""

import json
from typing import ClassVar

import pandas as pd
import pytest

from data.live_adapter import LiveDataProcessor, SignalRLiveAdapter
from data.live_recorder import replay_recording
from data.source_manager import DataSourceManager
from tests import live_fixtures

QUALI = {
    "Meeting": {"Name": "Singapore Grand Prix", "Circuit": {"Key": 61}},
    "Key": 9901,
    "Type": "Qualifying",
    "Name": "Qualifying",
    "StartDate": "2026-10-10T21:00:00",
    "Path": "2026/2026-10-11_Singapore_Grand_Prix/2026-10-10_Qualifying/",
}
RACE = {
    **QUALI,
    "Key": 9902,
    "Type": "Race",
    "Name": "Race",
    "StartDate": "2026-10-11T20:00:00",
    "Path": "2026/2026-10-11_Singapore_Grand_Prix/2026-10-11_Race/",
}
DRIVERS = {
    "1": {"RacingNumber": "1", "Tla": "VER", "TeamColour": "3671C6"},
    "4": {"RacingNumber": "4", "Tla": "NOR", "TeamColour": "FF8000"},
    "16": {"RacingNumber": "16", "Tla": "LEC", "TeamColour": "E80020"},
}


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr("data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})())

    def build(adapter=None):
        return DataSourceManager(live_adapter=adapter or SignalRLiveAdapter())

    return build


class TestSessionChange:
    """LIVE-27: qualifying's KO stayed on the race leader."""

    def _qualifying_then_race(self, adapter):
        adapter.seed_state(
            {
                "SessionInfo": QUALI,
                "DriverList": DRIVERS,
                "TimingData": {
                    "Lines": {"1": {"Position": "1", "KnockedOut": True, "NumberOfLaps": 1}}
                },
            }
        )
        adapter.handle_message(
            "TimingData", {"Lines": {"1": {"LastLapTime": {"Value": "1:30.000"}}}}, "t1"
        )
        assert adapter.recorded_laps()
        adapter.seed_state(
            {
                "SessionInfo": RACE,
                "DriverList": DRIVERS,
                "TimingData": {"Lines": {"1": {"Position": "1", "GapToLeader": "LAP 1"}}},
            }
        )

    def test_a_race_snapshot_after_qualifying_has_no_ko_and_no_old_laps(self, manager):
        adapter = SignalRLiveAdapter()
        self._qualifying_then_race(adapter)

        line = adapter.state.get("TimingData")["Lines"]["1"]
        assert "KnockedOut" not in line
        assert adapter.recorded_laps() == []
        standings = manager(adapter).poll_live_data()["standings"]
        assert standings.loc[0, "Status"] != "KO"

    def test_reseeding_the_same_session_keeps_the_lap_history(self):
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": RACE, "TimingData": {"Lines": {}}})
        adapter.handle_message("TimingData", {"Lines": {"4": {"NumberOfLaps": 3}}}, "t")
        adapter.handle_message(
            "TimingData", {"Lines": {"4": {"LastLapTime": {"Value": "1:35.0"}}}}, "t"
        )
        adapter.seed_state({"SessionInfo": RACE, "TimingData": {"Lines": {"4": {"Position": "1"}}}})

        assert len(adapter.recorded_laps()) == 1

    def test_a_snapshot_replaces_entries_deleted_during_an_outage(self):
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"TimingData": {"Lines": {"4": {"Stopped": True, "Position": "3"}}}})
        adapter.seed_state({"TimingData": {"Lines": {"4": {"Position": "3"}}}})

        assert "Stopped" not in adapter.state.get("TimingData")["Lines"]["4"]

    def test_a_lap_repeated_far_apart_is_recorded_once(self):
        adapter = SignalRLiveAdapter()
        adapter.handle_message("TimingData", {"Lines": {"4": {"NumberOfLaps": 2}}}, "t")
        for _ in range(3):
            adapter.handle_message(
                "TimingData", {"Lines": {"4": {"LastLapTime": {"Value": "1:35.0"}}}}, "t"
            )
            for other in range(50):
                line = {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:40"}}
                adapter.handle_message("TimingData", {"Lines": {str(100 + other): line}}, "t")

        assert sum(1 for lap in adapter.recorded_laps() if lap["driver_number"] == "4") == 1


class TestClearKeepsTheTower:
    """LIVE-29: Clear emptied the merged state for every viewer."""

    def test_after_clear_the_standings_remain(self, manager):
        adapter = SignalRLiveAdapter()
        adapter.seed_state(
            {
                "SessionInfo": RACE,
                "DriverList": DRIVERS,
                "TimingData": {
                    "Lines": {
                        "1": {"Position": "1", "GapToLeader": "LAP 5"},
                        "4": {"Position": "2", "GapToLeader": "+1.2"},
                    }
                },
            }
        )
        adapter._buffer_topic("WeatherData", {"AirTemp": "30", "timestamp": "t"})
        adapter.clear_buffer()

        snapshot = manager(adapter).poll_live_data()
        assert list(snapshot["standings"]["Driver"]) == ["VER", "NOR"]
        assert adapter.get_buffered_data("WeatherData") == []


class TestRecorderFailures:
    """LIVE-31: OSError(28) froze the tower for the rest of the session."""

    class FullDisk:
        directory = "full"

        def record(self, *args):
            raise OSError(28, "No space left on device")

        def record_snapshot(self, *args):
            raise OSError(28, "No space left on device")

        def close(self):
            pass

    def test_a_raising_recorder_still_updates_state(self):
        adapter = SignalRLiveAdapter()
        adapter.recorder = self.FullDisk()

        adapter.handle_message("TrackStatus", {"Status": "4", "Message": "SCDeployed"})
        adapter.handle_message("TrackStatus", {"Status": "1", "Message": "AllClear"})

        assert adapter.state.get("TrackStatus")["Status"] == "1"
        assert not adapter.is_recording()
        assert "No space left" in adapter.recorder_error

    def test_a_snapshot_survives_the_recorder_too(self):
        adapter = SignalRLiveAdapter()
        adapter.recorder = self.FullDisk()
        adapter.seed_state({"TrackStatus": {"Status": "2"}})

        assert adapter.state.get("TrackStatus")["Status"] == "2"


class TestReconnectSnapshotsInRecordings:
    """LIVE-32: the replay ended with the SC still deployed."""

    def test_a_snapshot_after_an_outage_replays_in_place(self, tmp_path):
        live = SignalRLiveAdapter()
        live.seed_state({"TrackStatus": {"Status": "1", "Message": "AllClear"}})
        live.start_recording(tmp_path / "rec")
        live.handle_message("TrackStatus", {"Status": "4", "Message": "SCDeployed"}, "t1")
        # Outage: the SC ending is missed; the reconnect snapshot says green.
        live.seed_state({"TrackStatus": {"Status": "1", "Message": "AllClear"}})
        live.handle_message("WeatherData", {"AirTemp": "30"}, "t3")
        live.stop_recording()

        replayed = replay_recording(tmp_path / "rec")

        assert live.state.get("TrackStatus")["Status"] == "1"
        assert replayed.state.get("TrackStatus")["Status"] == "1"

    def test_old_recordings_without_inline_snapshots_still_replay(self, tmp_path):
        folder = tmp_path / "old"
        folder.mkdir()
        (folder / "subscribe.json").write_text(json.dumps({"TrackStatus": {"Status": "2"}}))
        (folder / "live.jsonl").write_text(
            json.dumps(["TrackStatus", {"Status": "4"}, "t"]) + "\n", encoding="utf-8"
        )

        assert replay_recording(folder).state.get("TrackStatus")["Status"] == "4"


class TestWeatherIsNumeric:
    """LIVE-34: Rainfall "0" read as rain in every dry session."""

    def test_the_recorded_weather_parses_to_numbers(self):
        adapter = SignalRLiveAdapter()
        for timestamp, payload in live_fixtures.messages("WeatherData"):
            adapter.handle_message("WeatherData", payload, timestamp)

        frame = LiveDataProcessor.parse_weather_data(adapter.get_buffered_data("WeatherData"))

        for column in ("AirTemp", "TrackTemp", "Rainfall", "WindSpeed"):
            assert pd.api.types.is_numeric_dtype(frame[column]), column
        assert not (frame["Rainfall"] == 1).any()
        assert not frame["Rainfall"].astype(bool).any()


class TestAutoRecording:
    """LIVE-20: one recording directory per session, while connected."""

    class Stub:
        def __init__(self, **kwargs):
            self.alive = False

        def start(self):
            self.alive = True

        def is_running(self):
            return self.alive

        def stop(self):
            self.alive = False

    def test_two_sessions_give_two_directories(self, tmp_path, monkeypatch):
        from config import config

        monkeypatch.setattr(config, "replay_dir", str(tmp_path))
        monkeypatch.delenv("F1_LIVE_AUTORECORD", raising=False)
        adapter = SignalRLiveAdapter(client_factory=self.Stub)
        adapter.start_async()

        adapter.handle_message("SessionInfo", QUALI, "t1")
        adapter.handle_message("TrackStatus", {"Status": "1"}, "t2")
        adapter.handle_message("SessionInfo", RACE, "t3")
        adapter.handle_message("TrackStatus", {"Status": "2"}, "t4")
        adapter.stop_recording()

        folders = sorted(p.name for p in tmp_path.iterdir())
        assert len(folders) == 2
        assert all(name.startswith("raw_Singapore_Grand_Prix_") for name in folders)
        assert any("_Qualifying_" in name for name in folders)
        assert any("_Race_" in name for name in folders)

    def test_with_the_flag_off_nothing_is_written(self, tmp_path, monkeypatch):
        from config import config

        monkeypatch.setattr(config, "replay_dir", str(tmp_path))
        monkeypatch.setenv("F1_LIVE_AUTORECORD", "0")
        adapter = SignalRLiveAdapter(client_factory=self.Stub)
        adapter.start_async()
        adapter.handle_message("SessionInfo", QUALI, "t1")

        assert list(tmp_path.iterdir()) == []

    def test_a_fixture_replay_records_nothing(self, tmp_path, monkeypatch):
        from config import config

        monkeypatch.setattr(config, "replay_dir", str(tmp_path))
        adapter = SignalRLiveAdapter()  # never started: replaying, not live
        adapter.handle_message("SessionInfo", QUALI, "t1")

        assert list(tmp_path.iterdir()) == []


class TestSharedSnapshot:
    """LIVE-36: N tabs used to mean N rebuilds every 3 s."""

    def test_two_managers_on_one_adapter_build_once_per_change(self, manager):
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": RACE, "DriverList": DRIVERS})
        first, second = manager(adapter), manager(adapter)

        one = first.poll_live_data()
        two = second.poll_live_data()
        assert one is two
        assert adapter.snapshots.builds == 1

        adapter.handle_message("TrackStatus", {"Status": "4"})
        first.poll_live_data()
        second.poll_live_data()
        assert adapter.snapshots.builds == 2


class TestHeartbeat:
    """LIVE-23: the cached snapshot froze last_heartbeat."""

    def test_a_heartbeat_reaches_the_next_poll(self, manager):
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": RACE})
        polling = manager(adapter)
        polling.poll_live_data()

        adapter.handle_message("Heartbeat", {"Utc": "2026-10-11T20:15:00.000Z"})
        info = polling.poll_live_data()["session_info"]

        assert info["last_heartbeat"] == "2026-10-11T20:15:00.000Z"
        assert adapter.snapshots.builds == 1, "a heartbeat alone is not a rebuild"


class TestBroadcastDelay:
    """LIVE-21: the tower can trail the feed to match a delayed broadcast."""

    @staticmethod
    def _order(adapter, first, second):
        adapter.handle_message(
            "TimingData",
            {
                "Lines": {
                    first: {"Position": "1", "GapToLeader": "LAP 3"},
                    second: {"Position": "2", "GapToLeader": "+0.5"},
                }
            },
        )

    def test_a_30_second_delay_shows_the_order_from_30_seconds_ago(self, manager):
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": RACE, "DriverList": DRIVERS})
        polling = manager(adapter)

        self._order(adapter, "1", "4")
        polling.poll_live_data(now=1000.0)
        for t in range(1001, 1031):
            polling.poll_live_data(now=float(t))
        self._order(adapter, "4", "1")  # NOR takes the lead at t = 1031
        polling.poll_live_data(now=1031.0)

        live = polling.poll_live_data(now=1032.0)
        delayed = polling.poll_live_data(delay=30, now=1032.0)

        assert list(live["standings"]["Driver"][:2]) == ["NOR", "VER"]
        assert list(delayed["standings"]["Driver"][:2]) == ["VER", "NOR"]
        assert delayed["session_info"]["broadcast_delay"] == 30

    def test_no_delay_returns_the_live_snapshot(self, manager):
        adapter = SignalRLiveAdapter()
        polling = manager(adapter)

        assert polling.poll_live_data() is polling.poll_live_data(delay=0)


class TestLiveQualifying:
    """LIVE-19: segments, knock-outs and the segment clock."""

    TIMING: ClassVar[dict] = {
        "SessionPart": 2,
        "Lines": {
            "1": {
                "Position": "2",
                "BestLapTimes": [{"Value": "1:31.000"}, {"Value": "1:30.500"}],
            },
            "4": {
                "Position": "1",
                "BestLapTimes": [{"Value": "1:30.800"}, {"Value": "1:30.200"}],
            },
            "16": {
                "Position": "3",
                "KnockedOut": True,
                "BestLapTimes": [{"Value": "1:31.900"}, {"Value": ""}],
            },
        },
    }

    def test_q1_eliminated_drivers_sit_under_their_heading(self):
        frame = LiveDataProcessor.standings_from_state(
            self.TIMING, {"1": "VER", "4": "NOR", "16": "LEC"}, race=False
        )

        assert list(frame["Driver"]) == ["NOR", "VER", "LEC"]
        assert frame.loc[0, "Partition"] == "Q2"
        assert frame.loc[2, "Partition"] == "Eliminated in Q1"
        assert frame.loc[2, "Status"] == "KO"
        assert frame.loc[0, "BestLap"] == "1:30.200"
        assert frame.loc[1, "Gap"] == "+0.300"

    def test_sprint_qualifying_uses_sq_headings(self):
        frame = LiveDataProcessor.standings_from_state(
            self.TIMING, {"1": "VER", "4": "NOR", "16": "LEC"}, race=False, segment_prefix="SQ"
        )

        assert frame.loc[0, "Partition"] == "SQ2"
        assert frame.loc[2, "Partition"] == "Eliminated in SQ1"

    def test_the_header_names_the_segment_and_its_clock(self, manager):
        adapter = SignalRLiveAdapter()
        adapter.seed_state(
            {
                "SessionInfo": {**QUALI, "Name": "Sprint Qualifying"},
                "DriverList": DRIVERS,
                "TimingData": self.TIMING,
                "ExtrapolatedClock": {
                    "Utc": "2026-10-10T21:30:00Z",
                    "Remaining": "00:10:00",
                    "Extrapolating": False,
                },
            }
        )
        info = manager(adapter).poll_live_data()["session_info"]

        assert info["segment"] == "SQ2"
        assert info["segment_remaining"] == "0:10:00"

    def test_a_race_has_no_segment(self, manager):
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": RACE, "TimingData": {"SessionPart": 1, "Lines": {}}})

        assert "segment" not in manager(adapter).poll_live_data()["session_info"]


class TestLiveCircuitInfo:
    """LIVE-22: the live map gets corners once the feed names the circuit."""

    def test_circuit_info_is_fetched_once_while_connected(self, manager, monkeypatch):
        calls = []

        class Raw:
            corners = pd.DataFrame({"Number": [1], "X": [0.0], "Y": [0.0]})
            rotation = 44.0

        def fake(*, year, circuit_key):
            calls.append((year, circuit_key))
            return Raw()

        monkeypatch.setattr("fastf1.mvapi.get_circuit_info", fake)
        adapter = SignalRLiveAdapter()
        monkeypatch.setattr(adapter, "is_running", lambda: True)
        adapter.seed_state({"SessionInfo": QUALI})
        polling = manager(adapter)

        info = polling.poll_live_data()["circuit_info"]
        adapter.handle_message("TrackStatus", {"Status": "4"})
        polling.poll_live_data()

        assert info["rotation"] == 44.0 and not info["corners"].empty
        assert calls == [(2026, 61)]

    def test_nothing_is_fetched_from_a_replayed_feed(self, manager, monkeypatch):
        def boom(**kwargs):
            raise AssertionError("no network for a replay")

        monkeypatch.setattr("fastf1.mvapi.get_circuit_info", boom)
        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": QUALI})

        assert manager(adapter).poll_live_data()["circuit_info"] == {}


class TestLapFlags:
    """CORE-02: flag columns are bool, also for a driver missing from Lines."""

    def test_flags_are_bools_for_every_row(self):
        history = [
            {"driver_number": "4", "LapNumber": 1, "LapTime": "1:35.0", "Utc": "t"},
            {"driver_number": "99", "LapNumber": 1, "LapTime": "1:36.0", "Utc": "t"},
        ]
        timing = {"Lines": {"4": {"NumberOfLaps": 1, "Retired": "true"}}}

        with pd.option_context("future.no_silent_downcasting", True):
            frame = LiveDataProcessor.laps_from_history(history, timing, {"4": "NOR"})

        for column in ("InPit", "PitOut", "Retired", "Stopped"):
            assert frame[column].dtype == bool, column
        assert frame.loc[frame["driver_number"] == "4", "Retired"].all()
        assert not frame.loc[frame["driver_number"] == "99", "Retired"].any()
        assert (~frame["Retired"]).any()


class TestLiveScreen:
    """What the live snapshot draws: the segment clock (LIVE-19), dry weather
    (LIVE-34) and the track-state chip on the map (LIVE-22)."""

    def test_the_header_shows_the_running_segment_and_its_clock(self, manager):
        from ui.dashboard import header_html

        adapter = SignalRLiveAdapter()
        adapter.seed_state(
            {
                "SessionInfo": QUALI,
                "DriverList": DRIVERS,
                "TimingData": TestLiveQualifying.TIMING,
                "ExtrapolatedClock": {
                    "Utc": "2026-10-10T13:30:00Z",
                    "Remaining": "00:07:41",
                    "Extrapolating": False,
                },
            }
        )
        header = header_html(manager(adapter).poll_live_data())

        assert "Q2 remaining" in header
        assert "0:07:41" in header

    def test_a_dry_live_session_shows_no_rain(self, manager):
        from ui.dashboard import header_html

        adapter = SignalRLiveAdapter()
        adapter.seed_state({"SessionInfo": RACE, "DriverList": DRIVERS})
        for timestamp, payload in live_fixtures.messages("WeatherData"):
            adapter.handle_message("WeatherData", payload, timestamp)
        header = header_html(manager(adapter).poll_live_data())

        assert "rain-yes" not in header
        assert ">NO<" in header

    def test_a_safety_car_tints_the_live_map_and_shows_the_chip(self, manager):
        from ui.dashboard import track_state_marks
        from ui.theme import FLAG_STATES

        adapter = SignalRLiveAdapter()
        adapter.seed_state(
            {
                "SessionInfo": RACE,
                "DriverList": DRIVERS,
                "TrackStatus": {"Status": "4", "Message": "SCDeployed"},
            }
        )
        chip, tint = track_state_marks(manager(adapter).poll_live_data())

        assert ">SC<" in chip
        assert tint == FLAG_STATES["SAFETY CAR"][0]
