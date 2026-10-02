"""Tests for FastF1 Adapter.

The mocks here deliberately mirror the *real* shapes FastF1 3.8 returns -
notably that ``get_pos_data()`` carries no Speed channel (so ``add_distance()``
raises) and that ``session.laps`` has ``PitOutTime`` rather than a boolean
``IsPitOutLap``. Mocks that diverged from those shapes previously let a
production crash pass CI.
"""

from typing import ClassVar
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from data.fastf1_adapter import (
    SCOPE_SESSION,
    FastF1Adapter,
    latest_completed_event,
    session_codes_for_event,
)


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
    def test_init_enables_cache(self, mock_enable_cache, tmp_path):
        """Test that FastF1 cache is enabled"""
        cache_dir = str(tmp_path / "test_cache")
        FastF1Adapter(cache_dir=cache_dir)
        mock_enable_cache.assert_called_once_with(cache_dir)

    @patch("data.fastf1_adapter.fastf1.Cache.enable_cache")
    def test_the_cache_is_enabled_once_per_process(self, mock_enable_cache, tmp_path):
        """HIST-10: every tab's manager used to rebuild FastF1's HTTP session."""
        cache_dir = str(tmp_path / "shared")
        FastF1Adapter(cache_dir=cache_dir)
        FastF1Adapter(cache_dir=cache_dir)
        mock_enable_cache.assert_called_once_with(cache_dir)

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
        assert "Bahrain" in sessions["EventName"].to_numpy()
        assert "Saudi Arabia" in sessions["EventName"].to_numpy()
        assert "Australia" not in sessions["EventName"].to_numpy()

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


class TestSessionCodes:
    """HIST-05: the session list must follow the weekend's own format."""

    @staticmethod
    def _event(names, offsets_days, year=2025):
        """A schedule row: Session1..Session5 names + naive-UTC dates."""
        base = pd.Timestamp("2025-05-02T12:00:00")
        row = {"EventName": "Test GP", "Year": year}
        for i, (name, offset) in enumerate(zip(names, offsets_days, strict=False), start=1):
            row[f"Session{i}"] = name
            row[f"Session{i}DateUtc"] = base + pd.Timedelta(offset, unit="h")
        return pd.Series(row)

    def _now(self, monkeypatch, when="2025-05-10T00:00:00"):
        monkeypatch.setattr("data.fastf1_adapter._utcnow", lambda: pd.Timestamp(when, tz="UTC"))

    def test_conventional_weekend(self, monkeypatch):
        self._now(monkeypatch)
        event = self._event(
            ["Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"],
            [0, 4, 24, 28, 48],
        )

        assert session_codes_for_event(event) == ["FP1", "FP2", "FP3", "Q", "R"]

    def test_sprint_weekend_has_sq_and_no_fp2_fp3(self, monkeypatch):
        self._now(monkeypatch)
        event = self._event(
            ["Practice 1", "Sprint Qualifying", "Sprint", "Qualifying", "Race"],
            [0, 4, 24, 28, 48],
        )

        assert session_codes_for_event(event) == ["FP1", "SQ", "S", "Q", "R"]

    def test_2023_sprint_shootout_maps_to_sq(self, monkeypatch):
        self._now(monkeypatch)
        event = self._event(
            ["Practice 1", "Qualifying", "Sprint Shootout", "Sprint", "Race"],
            [0, 4, 24, 28, 48],
        )

        assert session_codes_for_event(event) == ["FP1", "Q", "SQ", "S", "R"]

    def test_sessions_that_have_not_started_are_excluded(self, monkeypatch):
        # Friday evening: FP1/FP2 are done, the rest of the weekend is not.
        self._now(monkeypatch, "2025-05-02T17:00:00")
        event = self._event(
            ["Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"],
            [0, 4, 24, 28, 48],
        )

        assert session_codes_for_event(event) == ["FP1", "FP2"]

    def test_unknown_session_names_are_skipped(self, monkeypatch):
        self._now(monkeypatch)
        event = self._event(["Practice 1", "Test Day", "Race"], [0, 4, 48])

        assert session_codes_for_event(event) == ["FP1", "R"]

    def test_schedule_without_session_columns_returns_nothing(self):
        assert session_codes_for_event(pd.Series({"EventName": "Test GP"})) == []


class TestLapTimeColumns:
    """DASH-09: the header's duration needs the session time at each lap."""

    def test_get_laps_keeps_the_session_time(self):
        session = Mock()
        session.laps = pd.DataFrame(
            {
                "Driver": ["VER", "VER"],
                "LapNumber": [1, 2],
                "LapTime": pd.to_timedelta(["00:01:30", "00:01:29"]),
                "Time": pd.to_timedelta(["00:02:00", "00:03:29"]),
                "LapStartTime": pd.to_timedelta(["00:00:30", "00:02:00"]),
            }
        )

        laps = FastF1Adapter().get_laps(session)

        assert "Time" in laps.columns and "LapStartTime" in laps.columns
        assert laps["Time"].iloc[-1].total_seconds() == pytest.approx(209.0)

    def test_get_laps_without_a_time_column_still_works(self):
        session = Mock()
        session.laps = pd.DataFrame(
            {"Driver": ["VER"], "LapNumber": [1], "LapTime": pd.to_timedelta(["00:01:30"])}
        )

        laps = FastF1Adapter().get_laps(session)

        assert "Time" not in laps.columns


class _Laps(pd.DataFrame):
    """A FastF1 ``Laps`` stand-in: a DataFrame that can slice position data.

    ``get_pos_data()`` mirrors FastF1: it starts at the first *non-NaT*
    ``LapStartTime``, so a qualifying driver whose first lap has none loses
    their out-lap.
    """

    _metadata: ClassVar = ["pos_data"]

    @property
    def _constructor(self):
        return _Laps

    def pick_drivers(self, driver):
        picked = _Laps(self[self["Driver"] == driver])
        picked.pos_data = self.pos_data
        return picked

    def get_pos_data(self):
        raw = self.pos_data[str(self["DriverNumber"].iloc[0])]
        begin = self["LapStartTime"].dropna().min()
        return raw[(raw["SessionTime"] >= begin) & (raw["SessionTime"] <= self["Time"].max())]


def _qualifying_session(first_lap_start):
    seconds = np.arange(0.0, 400.0, 0.25)
    raw = pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta(seconds, unit="s"),
            "X": 100.0 + seconds,
            "Y": 50.0 + seconds,
            "Z": 0.0,
            "Status": "OnTrack",
        }
    )
    laps = _Laps(
        {
            "Driver": ["VER", "VER"],
            "DriverNumber": ["1", "1"],
            "LapNumber": [1.0, 2.0],
            "LapStartTime": pd.to_timedelta([first_lap_start, 200.0], unit="s"),
            "Time": pd.to_timedelta([200.0, 300.0], unit="s"),
        }
    )
    laps.pos_data = {"1": raw}
    session = Mock()
    session.laps = laps
    session.pos_data = {"1": raw}
    session.session_start_time = pd.Timedelta(50, unit="s")
    return session


class TestPositionTimelineOutLap:
    """REPLAY-01: a Q/FP driver's first out-lap is not dropped."""

    def test_a_nat_first_lap_start_reads_from_the_session_start(self):
        timeline = FastF1Adapter().get_position_timeline(_qualifying_session(np.nan), ["VER"])

        assert timeline["Time"].min() == pytest.approx(50.0)
        assert timeline["Time"].max() == pytest.approx(300.0)

    def test_a_timed_first_lap_uses_the_lap_slice(self):
        timeline = FastF1Adapter().get_position_timeline(_qualifying_session(120.0), ["VER"])

        assert timeline["Time"].min() == pytest.approx(120.0)

    def test_pre_race_can_be_asked_for(self):
        timeline = FastF1Adapter().get_position_timeline(
            _qualifying_session(120.0), ["VER"], include_pre_race=True
        )

        assert timeline["Time"].min() == pytest.approx(0.0)


def _recorded_stream() -> pd.DataFrame:
    """The shape ``fastf1._api._extended_timing_data`` returns as ``stream_data``."""
    return pd.DataFrame(
        {
            "Time": pd.to_timedelta([3600.0, 3601.0, 3700.0, 3700.5], unit="s"),
            "Driver": ["1", "44", "1", "44"],
            "Position": np.array([1, 2, 1, 2], dtype="int64"),
            "GapToLeader": ["LAP 1", "+0.412", "LAP 2", "1 L"],
            "IntervalToPositionAhead": ["LAP 1", "+0.412", "LAP 2", "1 L"],
        }
    )


def _stream_session():
    session = Mock()
    session.api_path = "/static/2023/fake/"
    session.results = pd.DataFrame({"DriverNumber": ["1", "44"], "Abbreviation": ["VER", "HAM"]})
    return session


class TestTimingStream:
    """REPLAY-02: the stream FastF1 parses and then throws away."""

    def test_fastf1_still_emits_the_columns_this_reads(self):
        from fastf1 import _api

        from data.fastf1_adapter import RAW_STREAM_COLUMNS

        assert set(RAW_STREAM_COLUMNS) <= set(_api.EMPTY_STREAM)

    def test_it_maps_numbers_to_acronyms_and_parses_gaps(self, monkeypatch):
        import fastf1._api

        from data.fastf1_adapter import TIMING_STREAM_COLUMNS

        monkeypatch.setattr(
            fastf1._api, "_extended_timing_data", lambda path: (None, _recorded_stream(), [])
        )

        stream = FastF1Adapter.get_timing_stream(_stream_session())

        assert list(stream.columns) == TIMING_STREAM_COLUMNS
        assert stream["Driver"].tolist() == ["VER", "HAM", "VER", "HAM"]
        assert stream["Time"].tolist() == [3600.0, 3601.0, 3700.0, 3700.5]
        assert str(stream["Position"].dtype) == "Int64"
        assert stream["GapSeconds"].iloc[1] == pytest.approx(0.412)
        assert stream["GapLapsDown"].iloc[3] == 1
        assert pd.isna(stream["GapSeconds"].iloc[3])
        assert stream["GapToLeader"].iloc[3] == "1 L"  # raw text kept

    def test_a_failure_degrades_to_an_empty_frame_and_says_so(self, monkeypatch, caplog):
        import fastf1._api

        from data.fastf1_adapter import TIMING_STREAM_COLUMNS

        def broken(path):
            raise KeyError("TimingData")

        monkeypatch.setattr(fastf1._api, "_extended_timing_data", broken)

        with caplog.at_level("WARNING", logger="data.fastf1_adapter"):
            stream = FastF1Adapter.get_timing_stream(_stream_session())

        assert stream.empty
        assert list(stream.columns) == TIMING_STREAM_COLUMNS
        assert "Timing stream unavailable" in caplog.text


class TestSegmentStarts:
    @staticmethod
    def _session(splits, start=1000.0):
        session = Mock()
        session._session_split_times = [pd.Timedelta(s, unit="s") for s in splits]
        session.session_start_time = pd.Timedelta(start, unit="s")
        return session

    def test_a_race_has_no_segments(self):
        session = self._session([0, 86_400, 86_400])

        assert FastF1Adapter.get_segment_starts(session, "R") == []

    def test_qualifying_uses_the_session_start_for_q1(self):
        session = self._session([0, 2758.7, 4138.7])

        assert FastF1Adapter.get_segment_starts(session, "Q") == [1000.0, 2758.7, 4138.7]

    def test_splits_a_day_out_are_dropped(self):
        session = self._session([0, 2758.7, 86_400])

        assert FastF1Adapter.get_segment_starts(session, "SQ") == [1000.0, 2758.7]


class TestTrackStatus:
    def test_it_is_on_the_session_clock(self):
        session = Mock()
        session.track_status = pd.DataFrame(
            {
                "Time": pd.to_timedelta([328.6, 1319.0], unit="s"),
                "Status": ["1", "4"],
                "Message": ["AllClear", "SCDeployed"],
            }
        )

        status = FastF1Adapter.get_track_status(session)

        assert status["Time"].tolist() == [328.6, 1319.0]
        assert status["Status"].tolist() == ["1", "4"]


class TestRaceControlClock:
    def test_messages_get_a_session_time(self):
        session = Mock()
        session.t0_date = pd.Timestamp("2023-03-05 14:01:01.849")
        session.race_control_messages = pd.DataFrame(
            {
                "Time": pd.to_datetime(["2023-03-05 16:37:31"]),
                "Flag": ["CHEQUERED"],
                "Message": ["CHEQUERED FLAG"],
            }
        )

        messages = FastF1Adapter.get_race_control(session)

        assert messages["SessionTime"].iloc[0] == pd.Timedelta(
            2 * 3600 + 36 * 60 + 29.151, unit="s"
        )


class TestSessionNotArchived:
    """HIST-09: a session F1's archive has not filled in yet."""

    @staticmethod
    def _unloaded_session(date="2024-03-02 15:00"):
        from fastf1.exceptions import DataNotLoadedError

        class Unloaded:
            name = "Race"

            def __init__(self):
                self.date = pd.Timestamp(date)
                self.load = Mock()

            @property
            def laps(self):
                raise DataNotLoadedError(
                    "The data you are trying to access has not been loaded yet."
                )

        return Unloaded()

    @patch("data.fastf1_adapter.fastf1.get_session")
    def test_missing_laps_say_the_archive_is_not_ready(self, mock_get_session, tmp_path):
        from data.fastf1_adapter import SessionNotArchivedError

        mock_get_session.return_value = self._unloaded_session()

        with pytest.raises(SessionNotArchivedError, match="not in F1's archive yet"):
            FastF1Adapter(cache_dir=str(tmp_path)).load_session(2024, "Bahrain", "R")

    def test_a_race_that_ended_an_hour_ago_ended_recently(self):
        session = Mock(spec=["date", "name"])
        session.date, session.name = pd.Timestamp("2026-10-11 12:00"), "Race"

        now = pd.Timestamp("2026-10-11 15:00", tz="UTC")  # 1 h after a 2 h race
        assert FastF1Adapter.ended_recently(session, now=now)
        later = pd.Timestamp("2026-10-11 20:00", tz="UTC")
        assert not FastF1Adapter.ended_recently(session, now=later)

    def test_a_session_without_a_date_is_not_recent(self):
        assert not FastF1Adapter.ended_recently(Mock())

    @patch("data.fastf1_adapter.fastf1.get_session")
    def test_a_recent_session_is_not_kept_in_the_process(self, mock_get_session, tmp_path):
        from data.fastf1_adapter import clear_session_cache

        clear_session_cache()
        laps = pd.DataFrame({"LapNumber": [1]})
        session = Mock(spec=["date", "name", "load", "laps"])
        session.date, session.name, session.laps = pd.Timestamp.now(), "Race", laps
        mock_get_session.return_value = session
        adapter = FastF1Adapter(cache_dir=str(tmp_path))

        adapter.load_session(2026, "Singapore", "R")
        adapter.load_session(2026, "Singapore", "R")

        assert mock_get_session.call_count == 2
        clear_session_cache()


class TestCacheable:
    """HIST-09: only settled, non-live sessions stay in the runtime cache."""

    def test_rules(self):
        from app import cacheable

        assert cacheable({"is_live": False})
        assert not cacheable({"is_live": True})
        assert not cacheable({"is_live": False, "provisional": True})
