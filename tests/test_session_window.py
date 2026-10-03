"""Detecting an actually-live session (IMPROVEMENTS.md LIVE-15).

`is_race_weekend()` asked whether race day - midnight UTC, no time of day -
was within +/-72 h, i.e. Thursday 00:00 to Wednesday 00:00. For that whole
week the UI announced "LIVE SESSION DETECTED" and `Auto` hid the historical
selectors, on days with no session at all.
"""

import pandas as pd
import pytest

from f1dash.data.fastf1_adapter import SESSION_DURATIONS, live_session_now


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
            "f1dash.data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from f1dash.data.source_manager import DataSourceManager

        manager = DataSourceManager()
        manager.fastf1 = type(
            "Stub", (), {"get_schedule": staticmethod(lambda *a, **kw: _schedule())}
        )()
        monkeypatch.setattr("f1dash.data.fastf1_adapter._utcnow", lambda: _at("2026-09-06T14:00"))

        assert manager.live_session() is not None
        assert manager._is_race_weekend() is True

    def test_no_session_means_no_live_banner(self, monkeypatch):
        monkeypatch.setattr(
            "f1dash.data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from f1dash.data.source_manager import DataSourceManager

        manager = DataSourceManager()
        manager.fastf1 = type(
            "Stub", (), {"get_schedule": staticmethod(lambda *a, **kw: _schedule())}
        )()
        monkeypatch.setattr("f1dash.data.fastf1_adapter._utcnow", lambda: _at("2026-09-03T12:00"))

        assert manager.live_session() is None
        assert manager._is_race_weekend() is False


def _sprint_weekend() -> pd.DataFrame:
    """Singapore 2026 shape: FP1 + Sprint Qualifying Friday, Sprint + Q Saturday."""
    return pd.DataFrame(
        [
            {
                "RoundNumber": 18,
                "EventName": "Singapore Grand Prix",
                "EventFormat": "sprint_qualifying",
                "EventDate": pd.Timestamp("2026-10-11"),
                "Session1": "Practice 1",
                "Session1DateUtc": pd.Timestamp("2026-10-09T09:30"),
                "Session2": "Sprint Qualifying",
                "Session2DateUtc": pd.Timestamp("2026-10-09T13:30"),
                "Session3": "Sprint",
                "Session3DateUtc": pd.Timestamp("2026-10-10T09:00"),
                "Session4": "Qualifying",
                "Session4DateUtc": pd.Timestamp("2026-10-10T13:00"),
                "Session5": "Race",
                "Session5DateUtc": pd.Timestamp("2026-10-11T12:00"),
            }
        ]
    )


class TestTheCurrentWeekend:
    """LIVE-28 / CACHE-03 through the real adapter, with FastF1 and the clock frozen."""

    @pytest.fixture
    def manager(self, monkeypatch, tmp_path):
        import fastf1

        from f1dash.data import fastf1_adapter
        from f1dash.data.source_manager import DataSourceManager

        monkeypatch.setattr(fastf1, "get_event_schedule", lambda year: _sprint_weekend())
        clock = {"now": _at("2026-10-09T10:00")}
        monkeypatch.setattr(fastf1_adapter, "_utcnow", lambda: clock["now"])
        manager = DataSourceManager(cache_dir=str(tmp_path / "ff1"), replay_dir=str(tmp_path))
        return manager, clock

    @pytest.mark.parametrize(
        ("when", "code"),
        [
            ("2026-10-09T10:00", "FP1"),  # Friday morning: no session has ended yet
            ("2026-10-09T14:00", "SQ"),
            ("2026-10-10T13:30", "Q"),
        ],
    )
    def test_go_live_appears_for_every_session_of_the_weekend(self, manager, when, code):
        manager, clock = manager
        clock["now"] = _at(when)

        live = manager.live_session()

        assert live is not None and live["session"] == code

    def test_on_saturday_the_earlier_sessions_are_selectable(self, manager):
        from f1dash.data.fastf1_adapter import session_codes_for_event

        manager, clock = manager
        clock["now"] = _at("2026-10-10T11:00")  # after the Sprint, before Q

        events = manager.fastf1.get_available_sessions(2026)

        assert list(events["EventName"]) == ["Singapore Grand Prix"]
        assert session_codes_for_event(events.iloc[0]) == ["FP1", "SQ", "S"]
