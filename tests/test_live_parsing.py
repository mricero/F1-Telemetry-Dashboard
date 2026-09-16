"""Tests for live SignalR parsing (livef1 record shapes) + the poll pipeline."""

import base64
import json
import zlib
import pandas as pd
import pytest

from data.live_adapter import (
    LiveDataProcessor,
    decode_zipped,
    decode_topic_payload,
)


def make_zipped_payload(obj: dict) -> str:
    """Build a payload exactly like the F1 feed: base64(raw-deflate(json)).

    The official feed uses raw DEFLATE (no zlib header), which is why
    decoders call zlib.decompress(data, -MAX_WBITS).
    """
    raw = json.dumps(obj).encode("utf-8-sig")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    deflated = compressor.compress(raw) + compressor.flush()
    return base64.b64encode(deflated).decode("ascii")


class TestDecodeZipped:
    def test_roundtrip(self):
        payload = make_zipped_payload({"Entries": [{"Utc": "t", "Cars": {}}]})
        assert decode_zipped(payload) == {"Entries": [{"Utc": "t", "Cars": {}}]}

    def test_plain_json_passthrough(self):
        assert decode_zipped('{"a": 1}') == {"a": 1}

    def test_car_data_topic(self):
        blob = make_zipped_payload(
            {
                "Entries": [
                    {
                        "Utc": "2026-01-01T00:00:00Z",
                        "Cars": {
                            "44": {
                                "Channels": {
                                    "0": 11000,
                                    "2": 250,
                                    "3": 8,
                                    "4": 99,
                                    "5": 0,
                                    "45": 10,
                                }
                            }
                        },
                    }
                ]
            }
        )
        recs = decode_topic_payload("CarData.z", [(123.0, blob)])
        assert len(recs) == 1
        assert recs[0]["DriverNo"] == "44"
        assert recs[0]["rpm"] == 11000
        assert recs[0]["speed"] == 250
        assert recs[0]["drs"] == 10

    def test_car_data_channels_as_positional_list(self):
        blob = make_zipped_payload(
            {"Entries": [{"Utc": "u", "Cars": {"1": {"Channels": [9000, 180]}}}]}
        )
        recs = decode_topic_payload("CarData.z", [(None, blob)])
        # positional list maps onto channel ids in ascending order (0, 2, ...)
        assert str(recs[0]["rpm"]) == "9000"

    def test_position_topic(self):
        blob = make_zipped_payload(
            {"Position": [{"Timestamp": "ts", "Entries": {"16": {"X": -1420, "Y": 5174, "Z": 34}}}]}
        )
        recs = decode_topic_payload("Position.z", [(1.0, blob)])
        assert recs[0]["X"] == -1420
        assert recs[0]["Y"] == 5174

    def test_unknown_topic_raises(self):
        with pytest.raises(ValueError):
            decode_topic_payload("WeatherData.z", [(1.0, "{}")])


class TestParsersLiveF1Shapes:
    def test_parse_car_data(self):
        records = [
            {
                "DriverNo": "1",
                "Utc": "t1",
                "rpm": 10500,
                "speed": 302,
                "n_gear": 7,
                "throttle": 100,
                "brake": 0,
                "drs": 10,
            },
            {
                "DriverNo": "1",
                "Utc": "t2",
                "rpm": 9800,
                "speed": 180,
                "n_gear": 4,
                "throttle": 20,
                "brake": 1,
                "drs": 8,
            },
        ]
        df = LiveDataProcessor.parse_car_data(records)
        assert list(df.columns) == [
            "driver_number",
            "timestamp",
            "RPM",
            "Speed",
            "nGear",
            "Throttle",
            "Brake",
            "DRS",
        ]
        assert df["Speed"].max() == 302
        assert df["Brake"].max() <= 1  # boolean-ish, scaled later by normalize_units

    def test_parse_position(self):
        records = [{"DriverNo": "55", "Utc": "t", "X": 100, "Y": -200, "Z": 12}]
        df = LiveDataProcessor.parse_position_data(records)
        assert not df.empty and df["Y"].iloc[0] == -200

    def test_parse_tyre_stints(self):
        records = [
            {
                "DriverNo": "14",
                "PitCount": "0",
                "Compound": "SOFT",
                "LapStart": None,
                "LapEnd": None,
            },
            {
                "DriverNo": "14",
                "PitCount": "1",
                "Compound": "MEDIUM",
                "LapStart": None,
                "LapEnd": None,
            },
        ]
        df = LiveDataProcessor.parse_tyre_stints(records)
        assert len(df) == 2
        assert set(df["Compound"]) == {"SOFT", "MEDIUM"}
        for col in ("LapStart", "LapEnd"):
            assert col in df.columns

    def test_parse_driver_list_dedupes(self):
        records = [
            {
                "RacingNumber": "4",
                "Tla": "NOR",
                "TeamColour": "ff8000",
                "FirstName": "Lando",
                "LastName": "Norris",
                "TeamName": "McLaren",
            },
            {"RacingNumber": "4", "Tla": "NOR"},  # duplicate update
        ]
        df = LiveDataProcessor.parse_driver_list(records)
        assert len(df) == 1
        assert df["name_acronym"].iloc[0] == "NOR"
        assert df["team_colour"].iloc[0] == "#ff8000"
        assert df["full_name"].iloc[0] == "Lando Norris"

    def test_brake_scaling_via_normalize_units(self):
        from processing.telemetry_processor import TelemetryProcessor

        df = LiveDataProcessor.parse_car_data(
            [
                {
                    "DriverNo": "1",
                    "rpm": 9000,
                    "speed": 150,
                    "n_gear": 3,
                    "throttle": 0,
                    "brake": 1,
                    "drs": 8,
                }
            ]
        )
        out = TelemetryProcessor().normalize_units(df)
        assert out["Brake"].iloc[0] == 100


class TestPollPipeline:
    @pytest.fixture
    def manager(self, monkeypatch, tmp_path):
        # Avoid touching the real cache dir / network on init
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        return DataSourceManager()

    def _prime_buffers(self, adapter):
        adapter._data_buffer["CarData.z"] = [
            {
                "DriverNo": "44",
                "Utc": f"t{i}",
                "rpm": 11000,
                "speed": 300 + i,
                "n_gear": 8,
                "throttle": 99,
                "brake": 0,
                "drs": 10,
            }
            for i in range(50)
        ]
        adapter._data_buffer["Position.z"] = [
            {"DriverNo": "44", "Utc": f"t{i}", "X": i * 10, "Y": i * 5, "Z": 0} for i in range(50)
        ]
        adapter._data_buffer["TimingData"] = [
            {
                "DriverNo": "44",
                "timestamp": "t",
                "BestLapTime_Value": "1:31.20",
                "Sectors_1_Value": "31.10",
                "Sectors_2_Value": "35.05",
                "Sectors_3_Value": "25.05",
            }
        ]
        adapter._data_buffer["TyreStintSeries"] = [
            {
                "DriverNo": "44",
                "PitCount": "0",
                "Compound": "HARD",
                "LapStart": None,
                "LapEnd": None,
            }
        ]
        adapter._data_buffer["DriverList"] = [
            {
                "RacingNumber": "44",
                "Tla": "HAM",
                "TeamColour": "00d2be",
                "FirstName": "Lewis",
                "LastName": "Hamilton",
                "TeamName": "Ferrari",
            }
        ]
        adapter._data_buffer["SessionInfo"] = [REAL_SESSION_INFO]

    def test_poll_shapes_match_unified_dict(self, manager):
        self._prime_buffers(manager.live)
        snap = manager.poll_live_data()

        assert snap["is_live"] is True and snap["source"] == "live"
        assert "HAM" in snap["telemetry"]
        tel = snap["telemetry"]["HAM"]
        for col in ("Distance", "Speed", "Throttle", "Brake", "RPM", "nGear", "DRS"):
            assert col in tel.columns
        assert "HAM" in snap["location"]
        assert {"X", "Y"} <= set(snap["location"]["HAM"].columns)
        assert snap["stints"]["Compound"].iloc[0] == "HARD"
        assert snap["drivers"]["name_acronym"].iloc[0] == "HAM"
        assert snap["session_info"]["gp"] == "Italian Grand Prix"

    def test_poll_laps_feed_metrics_store(self, manager):
        from processing.metrics_store import MetricsStore

        self._prime_buffers(manager.live)
        snap = manager.poll_live_data()
        store = MetricsStore(path=":memory:")  # never written; label only
        label = MetricsStore.make_label(snap["session_info"])
        store.update_laps(label, snap["laps"])
        rec = store.session_records(label)
        assert rec["fastest_lap"]["display"] == "01:31.200"

    def test_poll_empty_buffers_is_safe(self, manager):
        snap = manager.poll_live_data()
        assert snap["telemetry"] == {}
        assert snap["laps"].empty
        assert isinstance(snap["drivers"], pd.DataFrame)

    def test_adapter_start_async_rejects_double_start(self, manager):
        manager.live._running = True  # pretend a stream is already up
        manager.live.start_async()  # must be a no-op, no crash


# The real SessionInfo payload: Meeting is a nested dict and the top-level
# "Name" is the *session* name, not the Grand Prix (LIVE-06 / TEST-01).
REAL_SESSION_INFO = {
    "Meeting": {
        "Key": 1259,
        "Name": "Italian Grand Prix",
        "OfficialName": "FORMULA 1 PIRELLI GRAN PREMIO D'ITALIA 2026",
        "Location": "Monza",
        "Country": {"Key": 13, "Code": "ITA", "Name": "Italy"},
        "Circuit": {"Key": 39, "ShortName": "Monza"},
    },
    "ArchiveStatus": {"Status": "Complete"},
    "Key": 9693,
    "Type": "Race",
    "Name": "Race",
    "StartDate": "2026-09-06T15:00:00",
    "EndDate": "2026-09-06T17:00:00",
    "GmtOffset": "02:00:00",
    "Path": "2026/2026-09-06_Italian_Grand_Prix/2026-09-06_Race/",
}


class TestSessionInfoParsing:
    """LIVE-06: SessionInfo.Meeting is a dict; Name is the session, not the GP."""

    @pytest.fixture
    def manager(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        return DataSourceManager()

    @staticmethod
    def _info(manager, payload):
        manager.live._data_buffer["SessionInfo"] = [payload]
        return manager._session_info_from_feed(manager.live)

    def test_gp_comes_from_the_nested_meeting_name(self, manager):
        info = self._info(manager, REAL_SESSION_INFO)
        assert info["gp"] == "Italian Grand Prix"

    def test_session_name_and_type_are_distinct_from_the_gp(self, manager):
        info = self._info(manager, REAL_SESSION_INFO)
        assert info["session_type"] == "Race"
        assert info["session_name"] == "Race"

    def test_year_comes_from_start_date(self, manager):
        info = self._info(manager, REAL_SESSION_INFO)
        assert info["year"] == 2026

    def test_circuit_key_and_gmt_offset_are_exposed(self, manager):
        info = self._info(manager, REAL_SESSION_INFO)
        assert info["circuit_key"] == 39
        assert info["gmt_offset"] == "02:00:00"

    def test_partial_payload_does_not_raise(self, manager):
        info = self._info(manager, {"Name": "Practice 1", "Type": "Practice"})
        assert info["session_type"] == "Practice"
        assert info["session_name"] == "Practice 1"
        assert info["gp"] == "Live Session"  # unknown, not the session name

    def test_no_session_info_keeps_the_placeholder(self, manager):
        info = self._info(manager, {})
        assert info["gp"] == "Live Session"
