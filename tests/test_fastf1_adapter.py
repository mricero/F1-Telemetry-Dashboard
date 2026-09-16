"""Tests for FastF1 Adapter.

The mocks here deliberately mirror the *real* shapes FastF1 3.8 returns -
notably that ``get_pos_data()`` carries no Speed channel (so ``add_distance()``
raises) and that ``session.laps`` has ``PitOutTime`` rather than a boolean
``IsPitOutLap``. Mocks that diverged from those shapes previously let a
production crash pass CI.
"""

import numpy as np
import pytest
from unittest.mock import Mock, patch
import pandas as pd
from data.fastf1_adapter import SCOPE_SESSION, FastF1Adapter, latest_completed_event


def _merged_telemetry_frame() -> pd.DataFrame:
    """What FastF1's Lap.get_telemetry() returns: car + position, merged."""
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(["2024-03-02T15:00:00Z"] * 3),
            "SessionTime": pd.to_timedelta([0, 1, 2], unit="s"),
            "Time": pd.to_timedelta([0, 1, 2], unit="s"),
            "RPM": [5000, 10000, 12000],
            "Speed": [100, 200, 300],
            "nGear": [2, 3, 4],
            "Throttle": [50, 80, 100],
            "Brake": [False, False, True],
            "DRS": [0, 1, 1],
            "Source": ["car", "car", "car"],
            "Distance": [0.0, 100.0, 200.0],
            "X": [100, 200, 300],
            "Y": [50, 150, 250],
            "Z": [10, 20, 30],
        }
    )


def _mock_session(lap_telemetry=None, pos_data=None):
    """Session whose laps.pick_drivers(...).pick_fastest() yields one Lap."""
    telemetry = _merged_telemetry_frame() if lap_telemetry is None else lap_telemetry

    lap = Mock()
    lap.get_telemetry.return_value = telemetry
    if pos_data is not None:
        lap.get_pos_data.return_value = pos_data

    laps = Mock()
    laps.empty = False
    laps.pick_drivers.return_value = laps
    laps.pick_fastest.return_value = lap
    laps.get_telemetry.return_value = telemetry

    session = Mock()
    session.laps = laps
    return session, laps, lap


def _no_laps_session():
    laps = Mock()
    laps.empty = True
    laps.pick_drivers.return_value = laps
    session = Mock()
    session.laps = laps
    return session


class TestFastF1Adapter:
    """Test FastF1Adapter class"""

    def test_init_creates_cache_dir(self, tmp_path):
        """Test that cache directory is created"""
        cache_dir = tmp_path / "test_cache"
        FastF1Adapter(cache_dir=str(cache_dir))
        assert cache_dir.exists()

    @patch("data.fastf1_adapter.fastf1.Cache.enable_cache")
    def test_init_enables_cache(self, mock_enable_cache):
        """Test that FastF1 cache is enabled"""
        FastF1Adapter(cache_dir="./test_cache")
        mock_enable_cache.assert_called_once_with("./test_cache")

    @patch("data.fastf1_adapter.fastf1.get_event_schedule")
    def test_get_available_sessions(self, mock_get_schedule):
        """Test getting available sessions"""
        mock_get_schedule.return_value = pd.DataFrame(
            {
                "Year": [2024, 2024, 2024],
                "EventName": ["Bahrain", "Saudi Arabia", "Australia"],
                "EventFormat": ["conventional"] * 3,
                "EventDate": [
                    pd.Timestamp("2024-03-02", tz="UTC"),
                    pd.Timestamp("2024-03-09", tz="UTC"),
                    pd.Timestamp("2100-03-16", tz="UTC"),  # Future race
                ],
            }
        )

        sessions = FastF1Adapter().get_available_sessions([2024])

        assert len(sessions) == 2  # Only completed races
        assert "Bahrain" in sessions["EventName"].values
        assert "Saudi Arabia" in sessions["EventName"].values
        assert "Australia" not in sessions["EventName"].values

    @patch("data.fastf1_adapter.fastf1.get_event_schedule")
    def test_get_available_sessions_drops_testing(self, mock_get_schedule):
        """Pre-season testing has no Race session and must not be offered."""
        mock_get_schedule.return_value = pd.DataFrame(
            {
                "EventName": ["Pre-Season Testing", "Pre-Season Testing", "Bahrain"],
                "EventFormat": ["testing", "testing", "conventional"],
                "RoundNumber": [0, 0, 1],
                "EventDate": [
                    pd.Timestamp("2024-02-21", tz="UTC"),
                    pd.Timestamp("2024-02-23", tz="UTC"),
                    pd.Timestamp("2024-02-28", tz="UTC"),
                ],
            }
        )

        sessions = FastF1Adapter().get_available_sessions([2024])

        assert sessions["EventName"].tolist() == ["Bahrain"]

    @patch("data.fastf1_adapter.fastf1.get_session")
    def test_load_session(self, mock_get_session):
        """Test loading a session"""
        mock_session = Mock()
        mock_session.load = Mock()
        mock_get_session.return_value = mock_session

        FastF1Adapter().load_session(2024, "Bahrain", "R")

        mock_get_session.assert_called_once_with(2024, "Bahrain", "R")
        mock_session.load.assert_called_once_with(
            telemetry=True, laps=True, weather=True, messages=True
        )

    def test_get_telemetry(self):
        """Telemetry is projected onto the unified channel schema."""
        session, _, _ = _mock_session()

        telemetry = FastF1Adapter().get_telemetry(session, "VER")

        assert not telemetry.empty
        assert list(telemetry.columns) == [
            "Distance",
            "Time",
            "Speed",
            "Throttle",
            "Brake",
            "RPM",
            "nGear",
            "DRS",
        ]

    def test_get_telemetry_defaults_to_fastest_lap(self):
        """Default scope is one lap, so Distance stays lap-relative."""
        session, laps, _ = _mock_session()

        FastF1Adapter().get_telemetry(session, "VER")

        laps.pick_fastest.assert_called_once()

    def test_get_telemetry_session_scope_uses_all_laps(self):
        session, laps, _ = _mock_session()

        FastF1Adapter().get_telemetry(session, "VER", scope=SCOPE_SESSION)

        laps.pick_fastest.assert_not_called()
        laps.get_telemetry.assert_called_once()

    def test_get_telemetry_empty_when_driver_has_no_laps(self):
        assert FastF1Adapter().get_telemetry(_no_laps_session(), "VER").empty

    def test_get_telemetry_empty_when_no_valid_fastest_lap(self):
        """pick_fastest() returns None when no lap has a valid time."""
        laps = Mock()
        laps.empty = False
        laps.pick_drivers.return_value = laps
        laps.pick_fastest.return_value = None
        session = Mock()
        session.laps = laps

        assert FastF1Adapter().get_telemetry(session, "VER").empty

    def test_get_location(self):
        """Location comes from the merged telemetry, which carries X/Y/Z."""
        session, _, _ = _mock_session()

        location = FastF1Adapter().get_location(session, "VER")

        assert not location.empty
        assert list(location.columns) == ["Distance", "X", "Y", "Z"]

    def test_get_location_never_integrates_bare_position_data(self):
        """Regression: get_pos_data() has no Speed, so add_distance() raises.

        Calling it produced ``ValueError: Telemetry does not contain required
        channels 'Time' and 'Speed'`` and took down the whole session load.
        """

        class PositionOnly(pd.DataFrame):
            """Mimics fastf1.core.Telemetry without car-data channels."""

            @property
            def _constructor(self):
                return PositionOnly

            def add_distance(self):
                raise ValueError("Telemetry does not contain required channels 'Time' and 'Speed'.")

        pos = PositionOnly(
            {
                "Date": pd.to_datetime(["2024-03-02T15:00:00Z"] * 3),
                "Status": ["OnTrack"] * 3,
                "X": [0.0, 300.0, 300.0],
                "Y": [0.0, 0.0, 400.0],
                "Z": [10.0, 20.0, 30.0],
                "Time": pd.to_timedelta([0, 1, 2], unit="s"),
            }
        )
        # Merged telemetry unavailable -> adapter must fall back to raw positions.
        session, _, _ = _mock_session(lap_telemetry=pd.DataFrame(), pos_data=pos)

        location = FastF1Adapter().get_location(session, "VER")

        assert list(location.columns) == ["Distance", "X", "Y", "Z"]
        # X/Y are 1/10 m: legs of 300 and 400 units -> 30 m then 70 m total.
        assert location["Distance"].tolist() == pytest.approx([0.0, 30.0, 70.0])

    def test_distance_from_positions_converts_to_metres(self):
        pos = pd.DataFrame({"X": [0.0, 30.0, 30.0], "Y": [0.0, 0.0, 40.0]})

        distance = FastF1Adapter.distance_from_positions(pos)

        assert distance == pytest.approx(np.array([0.0, 3.0, 7.0]))

    def test_distance_from_positions_needs_two_points(self):
        assert FastF1Adapter.distance_from_positions(pd.DataFrame({"X": [1.0], "Y": [2.0]})) is None

    def test_get_driver_frames_merges_once(self):
        """Both frames come from a single get_telemetry() call."""
        session, _, lap = _mock_session()

        telemetry, location = FastF1Adapter().get_driver_frames(session, "VER")

        assert lap.get_telemetry.call_count == 1
        assert not telemetry.empty and not location.empty

    def test_get_driver_frames_empty_for_driver_without_laps(self):
        telemetry, location = FastF1Adapter().get_driver_frames(_no_laps_session(), "VER")

        assert telemetry.empty and location.empty

    def test_get_laps_derives_pit_out_from_pit_out_time(self):
        """FastF1 has PitOutTime, not IsPitOutLap - the flag is derived."""
        mock_session = Mock()
        mock_session.laps = pd.DataFrame(
            {
                "Driver": ["VER", "HAM", "VER"],
                "LapNumber": [1, 1, 2],
                "LapTime": pd.to_timedelta(["00:01:30", "00:01:31", "00:01:29"]),
                "Sector1Time": pd.to_timedelta(["00:00:25", "00:00:26", "00:00:24"]),
                "Sector2Time": pd.to_timedelta(["00:00:35", "00:00:36", "00:00:34"]),
                "Sector3Time": pd.to_timedelta(["00:00:30", "00:00:29", "00:00:31"]),
                "PitOutTime": pd.to_timedelta([pd.NaT, pd.NaT, "00:20:00"]),
            }
        )

        laps = FastF1Adapter().get_laps(mock_session)

        assert len(laps) == 3
        assert "Driver" in laps.columns
        assert "LapNumber" in laps.columns
        assert laps["IsPitOutLap"].tolist() == [False, False, True]

    def test_get_laps_without_any_pit_column(self):
        mock_session = Mock()
        mock_session.laps = pd.DataFrame(
            {"Driver": ["VER"], "LapNumber": [1], "LapTime": pd.to_timedelta(["00:01:30"])}
        )

        laps = FastF1Adapter().get_laps(mock_session)

        assert laps["IsPitOutLap"].tolist() == [False]

    def test_get_stints(self):
        """Test getting stint data"""
        mock_session = Mock()
        mock_session.laps = pd.DataFrame(
            {
                "Driver": ["VER", "VER", "HAM"],
                "Stint": [1, 2, 1],
                "Compound": ["SOFT", "MEDIUM", "SOFT"],
                "LapStart": [1, 20, 1],
                "LapEnd": [19, 57, 57],
            }
        )

        stints = FastF1Adapter().get_stints(mock_session)

        assert len(stints) == 3
        assert "Compound" in stints.columns

    def test_get_stints_lap_counters_are_integers(self):
        """Derived stints must not render as 'Laps: 12.0'."""
        mock_session = Mock()
        mock_session.laps = pd.DataFrame(
            {
                "Driver": ["VER"] * 3 + ["HAM"] * 2,
                "Stint": [1.0, 1.0, 2.0, 1.0, 1.0],
                "Compound": ["SOFT", "SOFT", "HARD", "MEDIUM", "MEDIUM"],
                "LapNumber": [1, 2, 3, 1, 2],
            }
        )

        stints = FastF1Adapter().get_stints(mock_session)

        for col in ("Stint", "LapStart", "LapEnd", "LapCount"):
            assert str(stints[col].dtype) == "Int64", col
        ver_first = stints[(stints["Driver"] == "VER") & (stints["Stint"] == 1)].iloc[0]
        assert (ver_first["LapStart"], ver_first["LapEnd"], ver_first["LapCount"]) == (1, 2, 2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestScheduleYears:
    """HIST-04: the default season window must follow the clock, not 2025."""

    @patch("data.fastf1_adapter.fastf1.get_event_schedule")
    def test_default_years_are_relative_to_now(self, mock_get_schedule, monkeypatch):
        monkeypatch.setattr(
            "data.fastf1_adapter._utcnow", lambda: pd.Timestamp("2026-09-16", tz="UTC")
        )
        mock_get_schedule.return_value = pd.DataFrame(
            {
                "EventName": ["Bahrain"],
                "EventFormat": ["conventional"],
                "EventDate": [pd.Timestamp("2026-03-08", tz="UTC")],
            }
        )

        FastF1Adapter().get_available_sessions()

        requested = sorted(call.args[0] for call in mock_get_schedule.call_args_list)
        assert requested == [2025, 2026]

    @patch("data.fastf1_adapter.fastf1.get_event_schedule")
    def test_latest_completed_event_uses_the_race_session_end(self, mock_get_schedule, monkeypatch):
        monkeypatch.setattr(
            "data.fastf1_adapter._utcnow",
            lambda: pd.Timestamp("2026-09-16T12:00:00", tz="UTC"),
        )
        schedule = pd.DataFrame(
            {
                "Year": [2026, 2026, 2026],
                "EventName": ["Zandvoort", "Monza", "Baku"],
                "EventFormat": ["conventional"] * 3,
                "EventDate": [
                    pd.Timestamp("2026-08-30", tz="UTC"),
                    pd.Timestamp("2026-09-06", tz="UTC"),
                    pd.Timestamp("2026-09-20", tz="UTC"),  # still to come
                ],
                "Session5DateUtc": [
                    pd.Timestamp("2026-08-30T13:00:00"),
                    pd.Timestamp("2026-09-06T13:00:00"),
                    pd.Timestamp("2026-09-20T11:00:00"),
                ],
            }
        )

        latest = latest_completed_event(schedule)

        assert latest is not None
        assert latest["EventName"] == "Monza"

    @patch("data.fastf1_adapter.fastf1.get_event_schedule")
    def test_latest_completed_event_ignores_a_race_still_running(
        self, mock_get_schedule, monkeypatch
    ):
        monkeypatch.setattr(
            "data.fastf1_adapter._utcnow",
            lambda: pd.Timestamp("2026-09-06T14:00:00", tz="UTC"),
        )
        schedule = pd.DataFrame(
            {
                "Year": [2026, 2026],
                "EventName": ["Zandvoort", "Monza"],
                "EventFormat": ["conventional"] * 2,
                "EventDate": [
                    pd.Timestamp("2026-08-30", tz="UTC"),
                    pd.Timestamp("2026-09-06", tz="UTC"),
                ],
                "Session5DateUtc": [
                    pd.Timestamp("2026-08-30T13:00:00"),
                    pd.Timestamp("2026-09-06T13:00:00"),  # started an hour ago
                ],
            }
        )

        latest = latest_completed_event(schedule)

        assert latest["EventName"] == "Zandvoort"

    def test_latest_completed_event_on_an_empty_schedule(self):
        assert latest_completed_event(pd.DataFrame()) is None
