"""Replay recorded live-timing messages through the real ingest path.

TEST-01. Every assertion here is about the **feed's** shapes, taken from a
real session, so a fixture can no longer agree with a bug: LIVE-03..06 were
all invisible to the hand-written fixtures these complement.
"""

import numpy as np
import pandas as pd
import pytest

from data.live_adapter import LiveDataProcessor, SignalRLiveAdapter, decode_topic_payload
from tests import live_fixtures


@pytest.fixture
def adapter_with_session_info():
    adapter = SignalRLiveAdapter()
    adapter._data_buffer["SessionInfo"] = [live_fixtures.first_payload("SessionInfo")]
    return adapter


class TestFixtureIntegrity:
    def test_the_expected_topics_were_recorded(self):
        topics = set(live_fixtures.available_topics())

        for required in ("SessionInfo", "TimingData", "TyreStintSeries", "Position.z"):
            assert required in topics

    def test_the_manifest_names_its_source(self):
        manifest = live_fixtures.manifest()

        assert "livetiming.formula1.com/static" in manifest["source"]
        assert manifest["year"] == 2023

    def test_fixtures_stay_small(self):
        total = sum(p.stat().st_size for p in live_fixtures.FIXTURE_DIR.glob("*.gz"))

        assert total < 2 * 1024 * 1024, "fixtures are meant to pin shapes, not sessions"


class TestRealSessionInfoShape:
    """LIVE-06: the GP lives in a nested Meeting dict, not in Name."""

    def test_meeting_is_a_nested_dict(self):
        payload = live_fixtures.first_payload("SessionInfo")

        assert isinstance(payload["Meeting"], dict)
        assert payload["Meeting"]["Name"] == "Bahrain Grand Prix"
        assert payload["Name"] == "Race"  # the *session* name

    def test_the_parser_reads_the_grand_prix(self, adapter_with_session_info):
        from data.source_manager import DataSourceManager

        info = DataSourceManager._session_info_from_feed(adapter_with_session_info)

        assert info["gp"] == "Bahrain Grand Prix"
        assert info["session_name"] == "Race"
        assert info["session_type"] == "Race"
        assert info["year"] == 2023
        assert info["circuit_key"] == 63
        assert info["gmt_offset"] == "03:00:00"


class TestRealPositionShape:
    """LIVE-03: Position.z is base64 + raw DEFLATE, reported in 1/10 m."""

    @staticmethod
    def _records() -> pd.DataFrame:
        records = []
        for timestamp, payload in live_fixtures.messages("Position.z"):
            records.extend(decode_topic_payload("Position.z", [(timestamp, payload)]))
        return pd.DataFrame(records)

    def test_payloads_decode_to_driver_positions(self):
        frame = self._records()

        assert not frame.empty
        assert {"DriverNo", "X", "Y", "Z", "Utc"} <= set(frame.columns)

    def test_coordinates_are_in_tenths_of_a_metre(self):
        frame = self._records()

        # A circuit spans kilometres; in 1/10 m that is tens of thousands of
        # units. Coordinates in metres would stay well under 5000.
        assert frame[["X", "Y"]].abs().max().max() > 5000

    def test_distance_comes_out_in_plausible_metres(self):
        frame = self._records()
        one_driver = frame[frame["DriverNo"] == frame["DriverNo"].iloc[0]].copy()
        one_driver["timestamp"] = one_driver["Utc"]

        distance = LiveDataProcessor.distance_at(one_driver, one_driver["timestamp"])

        assert distance is not None
        # These are grid-formation samples: metres of movement, not kilometres.
        assert 0 <= float(np.nanmax(distance)) < 5000


class TestGarageAndOffTrackSamples:
    """LIVE-14 evidence: the feed reports 0,0,0 for cars in the garage."""

    def test_the_recording_starts_with_garage_zeros(self):
        frame = TestRealPositionShape._records()
        zeros = frame[(frame["X"] == 0) & (frame["Y"] == 0) & (frame["Z"] == 0)]

        assert not zeros.empty, "the fixture should keep some garage samples"

    def test_position_entries_carry_a_status(self):
        frame = TestRealPositionShape._records()

        assert "Status" in frame.columns
        assert "OnTrack" in set(frame["Status"].dropna())


class TestRealTimingDataShape:
    """LIVE-04: Sectors arrive as a list, then as index-keyed dicts."""

    @staticmethod
    def _sector_updates():
        snapshots, deltas = [], []
        for _, payload in live_fixtures.messages("TimingData"):
            for number, update in (payload.get("Lines") or {}).items():
                sectors = update.get("Sectors")
                if isinstance(sectors, list):
                    snapshots.append((number, sectors))
                elif isinstance(sectors, dict):
                    deltas.append((number, sectors))
        return snapshots, deltas

    def test_the_feed_uses_both_shapes(self):
        snapshots, deltas = self._sector_updates()

        assert snapshots, "no list-shaped Sectors recorded"
        assert deltas, "no dict-shaped Sectors recorded"

    def test_delta_keys_are_zero_based_indices(self):
        _, deltas = self._sector_updates()
        keys = {key for _, sectors in deltas for key in sectors}

        assert keys <= {"0", "1", "2"}, f"unexpected sector keys: {keys}"
        assert "0" in keys, "a 0-based key is what makes Sectors_1_Value ambiguous"


class TestRealTyreStintShape:
    """LIVE-05: stints are keyframe + partial update, not standalone rows."""

    @staticmethod
    def _stint_updates():
        snapshots, deltas = [], []
        for _, payload in live_fixtures.messages("TyreStintSeries"):
            for number, stints in (payload.get("Stints") or {}).items():
                (snapshots if isinstance(stints, list) else deltas).append((number, stints))
        return snapshots, deltas

    def test_the_snapshot_is_a_per_driver_list(self):
        snapshots, _ = self._stint_updates()

        assert snapshots, "no list-shaped Stints recorded"

    def test_updates_are_partial_and_carry_no_compound(self):
        _, deltas = self._stint_updates()
        partial = [
            fields
            for _, stints in deltas
            for fields in stints.values()
            if isinstance(fields, dict) and "Compound" not in fields
        ]

        assert partial, "the feed does send compound-less stint updates"
        assert any("TotalLaps" in fields for fields in partial)

    def test_the_record_parser_cannot_read_these_messages(self):
        """Why LIVE-05 exists: the per-message parser never saw this shape.

        ``parse_tyre_stints`` expects a flat list of records that already
        carry a ``Compound``; handed the feed's actual ``{"Stints": {...}}``
        payload it finds nothing to work with. Kept as a record of what the
        state layer replaced - the parser itself goes with LIVE-16.
        """
        payload = live_fixtures.first_payload("TyreStintSeries")

        with pytest.raises(AttributeError):
            LiveDataProcessor.parse_tyre_stints(payload)

    def test_the_state_layer_keeps_them(self):
        from data.live_state import LiveState

        state = LiveState()
        for _, payload in live_fixtures.messages("TyreStintSeries"):
            state.update("TyreStintSeries", payload)

        frame = LiveDataProcessor.stints_from_state(state.get("TyreStintSeries"), {})

        assert not frame.empty
        assert set(frame["Compound"]) <= {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}


class TestEndToEndReplay:
    """Recorded messages -> adapter -> the unified session dict."""

    @staticmethod
    def _primed_manager(monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        manager = DataSourceManager()
        for topic in ("SessionInfo", "DriverList", "TimingData", "TyreStintSeries"):
            for timestamp, payload in live_fixtures.messages(topic):
                manager.live.handle_message(topic, payload, timestamp)
        return manager

    def test_the_snapshot_names_the_grand_prix(self, monkeypatch):
        snapshot = self._primed_manager(monkeypatch).poll_live_data()

        assert snapshot["session_info"]["gp"] == "Bahrain Grand Prix"

    def test_drivers_come_through_with_acronyms(self, monkeypatch):
        snapshot = self._primed_manager(monkeypatch).poll_live_data()
        drivers = snapshot["drivers"]

        assert len(drivers) >= 20
        assert "VER" in set(drivers["name_acronym"])

    def test_stints_are_no_longer_empty(self, monkeypatch):
        snapshot = self._primed_manager(monkeypatch).poll_live_data()

        assert not snapshot["stints"].empty
        assert set(snapshot["stints"]["DriverAcronym"]) & {"VER", "LEC", "HAM"}

    def test_the_snapshot_keeps_the_unified_shape(self, monkeypatch):
        snapshot = self._primed_manager(monkeypatch).poll_live_data()

        for key in ("session_info", "telemetry", "laps", "stints", "location", "drivers"):
            assert key in snapshot
        assert snapshot["is_live"] is True
