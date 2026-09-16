"""Tests for the live state layer (IMPROVEMENTS.md LIVE-05).

Most live-timing topics are keyframe + partial update streams: the
subscription returns the full state and later messages carry only what
changed. Treating each message as a standalone record loses the keyframe and
drops every partial, which is why live stints were empty and live timing
fields went stale.
"""

import pandas as pd
import pytest

from data.live_state import LiveState, as_list, deep_merge
from tests import live_fixtures


class TestDeepMerge:
    def test_new_keys_are_added(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_existing_keys_are_replaced(self):
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dicts_merge_rather_than_replace(self):
        base = {"Lines": {"1": {"Position": "1", "GapToLeader": ""}}}
        deep_merge(base, {"Lines": {"1": {"GapToLeader": "+1.2"}}})

        assert base["Lines"]["1"] == {"Position": "1", "GapToLeader": "+1.2"}

    def test_a_delta_addresses_list_items_by_index(self):
        base = {"Sectors": [{"Value": "30.1"}, {"Value": "31.0"}, {"Value": "25.0"}]}

        deep_merge(base, {"Sectors": {"1": {"Value": "30.9"}}})

        assert as_list(base["Sectors"]) == [
            {"Value": "30.1"},
            {"Value": "30.9"},
            {"Value": "25.0"},
        ]

    def test_deleted_removes_keys(self):
        base = {"Lines": {"1": {"x": 1}, "2": {"x": 2}}}

        deep_merge(base, {"Lines": {"_deleted": ["2"]}})

        assert set(base["Lines"]) == {"1"}

    def test_merging_does_not_alias_the_update(self):
        update = {"Lines": {"1": {"Position": "3"}}}
        base = deep_merge({}, update)
        update["Lines"]["1"]["Position"] = "9"

        assert base["Lines"]["1"]["Position"] == "3"


class TestAsList:
    def test_a_list_passes_through(self):
        assert as_list([1, 2]) == [1, 2]

    def test_index_keyed_dicts_become_ordered_lists(self):
        assert as_list({"1": "b", "0": "a", "2": "c"}) == ["a", "b", "c"]

    def test_gaps_are_filled_with_none(self):
        assert as_list({"0": "a", "2": "c"}) == ["a", None, "c"]

    def test_non_index_dicts_are_left_alone(self):
        value = {"Compound": "SOFT"}
        assert as_list(value) == [value]

    def test_missing_values_give_an_empty_list(self):
        assert as_list(None) == []


class TestLiveState:
    def test_the_snapshot_seeds_the_state(self):
        state = LiveState()
        state.update("TimingData", {"Lines": {"1": {"Position": "1"}}})

        assert state.get("TimingData")["Lines"]["1"]["Position"] == "1"

    def test_partials_update_rather_than_replace(self):
        state = LiveState()
        state.update("TimingData", {"Lines": {"1": {"Position": "1", "InPit": False}}})
        state.update("TimingData", {"Lines": {"1": {"InPit": True}}})

        assert state.get("TimingData")["Lines"]["1"] == {"Position": "1", "InPit": True}

    def test_a_later_driver_list_update_is_not_ignored(self):
        state = LiveState()
        state.update("DriverList", {"1": {"Tla": "VER", "Line": 3}})
        state.update("DriverList", {"1": {"Line": 1}})

        assert state.get("DriverList")["1"] == {"Tla": "VER", "Line": 1}

    def test_version_advances_on_every_update(self):
        state = LiveState()
        first = state.version
        state.update("TrackStatus", {"Status": "2"})

        assert state.version > first

    def test_reading_a_missing_topic_is_empty(self):
        assert LiveState().get("Nothing") == {}

    def test_snapshot_returns_a_copy(self):
        state = LiveState()
        state.update("TimingData", {"Lines": {"1": {"Position": "1"}}})

        snapshot = state.snapshot()
        snapshot["TimingData"]["Lines"]["1"]["Position"] = "9"

        assert state.get("TimingData")["Lines"]["1"]["Position"] == "1"


class TestReplayOfRecordedMessages:
    """The real 2023 Bahrain race messages, merged as the feed intends."""

    @staticmethod
    def _replay(topic: str) -> LiveState:
        state = LiveState()
        for _, payload in live_fixtures.messages(topic):
            state.update(topic, payload)
        return state

    def test_stints_survive_the_snapshot_and_its_updates(self):
        state = self._replay("TyreStintSeries")
        stints = state.get("TyreStintSeries")["Stints"]

        assert stints, "the keyframe was dropped"
        assert len(stints) >= 20

    def test_a_partial_stint_update_lands_on_the_keyframe_entry(self):
        state = self._replay("TyreStintSeries")
        stints = state.get("TyreStintSeries")["Stints"]

        with_laps = [
            stint
            for driver in stints.values()
            for stint in as_list(driver)
            if isinstance(stint, dict) and stint.get("TotalLaps")
        ]
        assert with_laps, "TotalLaps updates were dropped instead of merged"

    def test_timing_lines_accumulate_fields_from_many_messages(self):
        state = self._replay("TimingData")
        lines = state.get("TimingData")["Lines"]

        assert lines
        # A driver's row is built from several partial messages, so it should
        # carry more than whatever the last message happened to contain.
        richest = max(lines.values(), key=len)
        assert len(richest) > 3

    def test_driver_list_keeps_the_full_entry(self):
        state = self._replay("DriverList")
        drivers = {k: v for k, v in state.get("DriverList").items() if k.isdigit()}

        assert len(drivers) >= 20
        assert any(entry.get("Tla") for entry in drivers.values())

    @pytest.mark.parametrize("topic", ["TimingData", "TyreStintSeries", "DriverList"])
    def test_replaying_twice_is_idempotent(self, topic):
        assert self._replay(topic).get(topic) == self._replay(topic).get(topic)


class TestStateDerivedFrames:
    """State -> the unified dict's tables."""

    def test_stints_frame_has_the_strategy_chart_columns(self):
        from data.live_adapter import LiveDataProcessor

        state = TestReplayOfRecordedMessages._replay("TyreStintSeries")
        frame = LiveDataProcessor.stints_from_state(
            state.get("TyreStintSeries"), {"1": "VER", "16": "LEC"}
        )

        assert not frame.empty
        for column in ("Driver", "DriverAcronym", "Stint", "Compound", "LapStart", "LapEnd"):
            assert column in frame.columns

    def test_stints_are_keyed_by_acronym_not_racing_number(self):
        from data.live_adapter import LiveDataProcessor

        state = LiveState()
        state.update(
            "TyreStintSeries",
            {"Stints": {"1": [{"Compound": "SOFT", "New": "true", "TotalLaps": 12}]}},
        )
        frame = LiveDataProcessor.stints_from_state(state.get("TyreStintSeries"), {"1": "VER"})

        assert frame["DriverAcronym"].tolist() == ["VER"]
        assert frame["Compound"].tolist() == ["SOFT"]

    def test_stint_lap_bounds_come_from_the_lap_counts(self):
        from data.live_adapter import LiveDataProcessor

        state = LiveState()
        state.update(
            "TyreStintSeries",
            {
                "Stints": {
                    "1": [
                        {"Compound": "SOFT", "TotalLaps": 10, "StartLaps": 0},
                        {"Compound": "HARD", "TotalLaps": 25, "StartLaps": 0},
                    ]
                }
            },
        )
        frame = LiveDataProcessor.stints_from_state(state.get("TyreStintSeries"), {"1": "VER"})

        assert frame["LapStart"].tolist() == [1, 11]
        assert frame["LapEnd"].tolist() == [10, 35]
        assert frame["LapCount"].tolist() == [10, 25]

    def test_drivers_frame_reflects_later_updates(self):
        from data.live_adapter import LiveDataProcessor

        state = LiveState()
        state.update("DriverList", {"1": {"RacingNumber": "1", "Tla": "VER", "TeamName": "RB"}})
        state.update("DriverList", {"1": {"TeamColour": "3671C6"}})

        frame = LiveDataProcessor.drivers_from_state(state.get("DriverList"))

        assert frame["name_acronym"].tolist() == ["VER"]
        assert frame["team_colour"].tolist() == ["#3671C6"]

    def test_timing_frame_has_one_row_per_driver(self):
        from data.live_adapter import LiveDataProcessor

        state = TestReplayOfRecordedMessages._replay("TimingData")
        frame = LiveDataProcessor.timing_from_state(state.get("TimingData"))

        assert not frame.empty
        assert frame["driver_number"].is_unique
        assert isinstance(frame, pd.DataFrame)
