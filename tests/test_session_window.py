"""Detecting an actually-live session (IMPROVEMENTS.md LIVE-15).

`is_race_weekend()` asked whether race day - midnight UTC, no time of day -
was within +/-72 h, i.e. Thursday 00:00 to Wednesday 00:00. For that whole
week the UI announced "LIVE SESSION DETECTED" and `Auto` hid the historical
selectors, on days with no session at all.
"""

import pandas as pd
import pytest

from data.fastf1_adapter import SESSION_DURATIONS, live_session_now


def _schedule() -> pd.DataFrame:
    """A conventional weekend: FP1/2 Friday, FP3+Q Saturday, race Sunday."""
    return pd.DataFrame(
        [
            {
                "Year": 2026,
                "EventName": "Italian Grand Prix",
                "EventFormat": "conventional",
                "EventDate": pd.Timestamp("2026-09-06", tz="UTC"),
                "Session1": "Practice 1",
                "Session1DateUtc": pd.Timestamp("2026-09-04T11:30"),
                "Session2": "Practice 2",
                "Session2DateUtc": pd.Timestamp("2026-09-04T15:00"),
                "Session3": "Practice 3",
                "Session3DateUtc": pd.Timestamp("2026-09-05T10:30"),
                "Session4": "Qualifying",
                "Session4DateUtc": pd.Timestamp("2026-09-05T14:00"),
                "Session5": "Race",
                "Session5DateUtc": pd.Timestamp("2026-09-06T13:00"),
            }
        ]
    )


def _at(when: str):
    return pd.Timestamp(when, tz="UTC")


class TestLiveSessionWindow:
    def test_just_before_fp1_counts_as_live(self):
        assert live_session_now(_schedule(), _at("2026-09-04T11:20")) is not None

    def test_during_the_race_counts_as_live(self):
        session = live_session_now(_schedule(), _at("2026-09-06T14:00"))

        assert session is not None
        assert session["session"] == "R"
        assert session["event"] == "Italian Grand Prix"

    def test_thursday_is_not_live(self):
        assert live_session_now(_schedule(), _at("2026-09-03T12:00")) is None

    def test_the_gap_between_friday_sessions_is_not_live(self):
        # FP1 (11:30 + 90 m + 30 m run-out) is over; FP2's lead-in starts 14:45.
        assert live_session_now(_schedule(), _at("2026-09-04T14:00")) is None

    def test_the_tuesday_after_the_race_is_not_live(self):
        assert live_session_now(_schedule(), _at("2026-09-08T12:00")) is None

    def test_well_after_the_chequered_flag_is_not_live(self):
        # Race start + 4 h: past the window even for a red-flagged race.
        assert live_session_now(_schedule(), _at("2026-09-06T17:00")) is None

    @pytest.mark.parametrize("code", ["FP1", "Q", "R"])
    def test_every_session_type_has_a_duration(self, code):
        assert code in SESSION_DURATIONS

    def test_an_empty_schedule_is_never_live(self):
        assert live_session_now(pd.DataFrame(), _at("2026-09-06T14:00")) is None

    def test_a_schedule_without_session_times_is_never_live(self):
        schedule = pd.DataFrame(
            {"EventName": ["X"], "EventDate": [pd.Timestamp("2026-09-06", tz="UTC")]}
        )

        assert live_session_now(schedule, _at("2026-09-06T14:00")) is None


class TestManagerProbe:
    def test_the_manager_reports_the_session_window(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        manager = DataSourceManager()
        manager.fastf1 = type(
            "Stub", (), {"get_schedule": staticmethod(lambda *a, **kw: _schedule())}
        )()
        monkeypatch.setattr("data.fastf1_adapter._utcnow", lambda: _at("2026-09-06T14:00"))

        assert manager.live_session() is not None
        assert manager._is_race_weekend() is True

    def test_no_session_means_no_live_banner(self, monkeypatch):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        manager = DataSourceManager()
        manager.fastf1 = type(
            "Stub", (), {"get_schedule": staticmethod(lambda *a, **kw: _schedule())}
        )()
        monkeypatch.setattr("data.fastf1_adapter._utcnow", lambda: _at("2026-09-03T12:00"))

        assert manager.live_session() is None
        assert manager._is_race_weekend() is False


class TestLiveDuringTheWeekend:
    """LIVE-28: FP1 and Saturday qualifying are found live, not only the race."""

    @staticmethod
    def _manager(monkeypatch, now: str, tmp_path):
        from unittest.mock import patch

        from data.source_manager import DataSourceManager

        monkeypatch.setattr("data.fastf1_adapter._utcnow", lambda: _at(now))
        schedule = _schedule().assign(RoundNumber=16)
        patcher = patch("data.fastf1_adapter.fastf1.get_event_schedule", return_value=schedule)
        patcher.start()
        manager = DataSourceManager(cache_dir=str(tmp_path / "cache"), replay_dir=str(tmp_path))
        return manager, patcher

    @pytest.mark.parametrize(
        ("now", "session"),
        [
            ("2026-09-04T11:45", "Practice 1"),
            ("2026-09-05T14:30", "Qualifying"),
            ("2026-09-06T13:30", "Race"),
        ],
    )
    def test_the_running_session_is_found(self, monkeypatch, tmp_path, now, session):
        manager, patcher = self._manager(monkeypatch, now, tmp_path)
        try:
            found = manager.live_session()
        finally:
            patcher.stop()

        assert found is not None
        assert session in str(found)
