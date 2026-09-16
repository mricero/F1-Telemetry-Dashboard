"""Tests for the timing-tower data model (``processing.timing``).

Fixtures use analytically known lap and sector times so classification,
gaps and micro-sector colouring can be asserted exactly.
"""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from processing.timing import (
    SEGMENTS_PER_SECTOR,
    TOTAL_SEGMENTS,
    build_timing_rows,
    format_delta,
    format_lap,
    micro_sector_times,
    segment_states,
    sector_leaders,
    theoretical_best,
)


def _lap_rows(driver: str, lap_times, sectors, compound="SOFT", speed=300.0) -> list:
    """Raw lap dicts for one driver; ``sectors`` is (s1, s2, s3) per lap."""
    return [
        {
            "Driver": driver,
            "LapNumber": number,
            "LapTime": timedelta(seconds=seconds) if seconds else pd.NaT,
            "Sector1Time": timedelta(seconds=sectors[0]),
            "Sector2Time": timedelta(seconds=sectors[1]),
            "Sector3Time": timedelta(seconds=sectors[2]),
            "Compound": compound,
            "Stint": 1,
            "SpeedST": speed,
        }
        for number, seconds in enumerate(lap_times, start=1)
    ]


def _laps(*row_groups) -> pd.DataFrame:
    """Assemble lap rows into one frame with FastF1-shaped dtypes.

    Built in a single pass rather than concatenating per-driver frames: the
    pit columns are all-NaT, and concatenating those trips a pandas
    deprecation warning about the generic timedelta unit.
    """
    rows = [row for group in row_groups for row in group]
    frame = pd.DataFrame(rows)
    # Bare NaT would infer datetime64; FastF1 pit fields are durations.
    for column in ("PitInTime", "PitOutTime"):
        frame[column] = pd.Series(pd.NaT, index=frame.index, dtype="timedelta64[ns]")
    return frame


def _drivers(*entries) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "driver_number": str(i),
                "name_acronym": code,
                "team_name": team,
                "team_colour": colour,
                "full_name": code,
            }
            for i, (code, team, colour) in enumerate(entries, start=1)
        ]
    )


@pytest.fixture
def session():
    """Two drivers: VER a clear second quicker than HAM."""
    laps = _laps(
        _lap_rows("VER", [92.0, 90.5], (30.0, 30.0, 30.5), speed=310.0),
        _lap_rows("HAM", [93.0, 92.5], (30.4, 30.6, 31.5), speed=305.0),
    )
    return {
        "laps": laps,
        "drivers": _drivers(("VER", "Red Bull Racing", "#3671c6"), ("HAM", "Ferrari", "#e80020")),
        "telemetry": {},
        "is_live": False,
    }


class TestFormatting:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(93.456, "1:33.456"), (45.5, "45.500"), (None, "—"), (float("nan"), "—")],
    )
    def test_format_lap(self, seconds, expected):
        assert format_lap(seconds) == expected

    @pytest.mark.parametrize(
        "seconds,expected", [(0.102, "+0.102"), (-0.25, "-0.250"), (None, "----")]
    )
    def test_format_delta(self, seconds, expected):
        assert format_delta(seconds) == expected


class TestClassification:
    def test_orders_by_personal_best(self, session):
        rows = build_timing_rows(session)

        assert [r["code"] for r in rows] == ["VER", "HAM"]
        assert rows[0]["best_lap"] == "1:30.500"

    def test_leader_has_blank_gap_and_interval(self, session):
        rows = build_timing_rows(session)

        assert rows[0]["gap"] == "----"
        assert rows[0]["interval"] == "----"
        assert rows[0]["is_overall_best"] is True

    def test_gap_is_to_leader_interval_to_car_ahead(self, session):
        rows = build_timing_rows(session)

        # 92.5 - 90.5 = 2.0; with two drivers gap and interval coincide.
        assert rows[1]["gap"] == "+2.000"
        assert rows[1]["interval"] == "+2.000"

    def test_last_lap_is_the_most_recent_not_the_best(self, session):
        rows = build_timing_rows(session)

        assert rows[0]["last_lap"] == "1:30.500"  # lap 2
        assert rows[1]["last_lap"] == "1:32.500"

    def test_cutoff_marks_knocked_out_rows(self, session):
        rows = build_timing_rows(session, cutoff=1)

        assert rows[0]["knocked_out"] is False
        assert rows[1]["knocked_out"] is True

    def test_completed_session_is_classified(self, session):
        assert {r["status"] for r in build_timing_rows(session)} == {"CLASSIFIED"}

    def test_live_session_reports_pit_state(self, session):
        session["is_live"] = True
        session["laps"].loc[session["laps"].index[-1], "PitInTime"] = timedelta(minutes=5)

        statuses = {r["code"]: r["status"] for r in build_timing_rows(session)}

        assert statuses["HAM"] == "IN PIT"
        assert statuses["VER"] == "ON TRACK"

    def test_driver_without_a_time_sorts_last(self):
        laps = _laps(
            _lap_rows("VER", [90.0], (30.0, 30.0, 30.0)),
            _lap_rows("SAR", [None], (30.0, 30.0, 30.0)),
        )
        rows = build_timing_rows({"laps": laps, "drivers": _drivers(), "is_live": False})

        assert rows[-1]["code"] == "SAR"
        assert rows[-1]["best_lap"] == "—"
        assert rows[-1]["gap"] == "----"

    def test_empty_laps_yield_no_rows(self):
        assert build_timing_rows({"laps": pd.DataFrame()}) == []

    def test_team_metadata_is_attached(self, session):
        rows = build_timing_rows(session)

        assert rows[0]["team_name"] == "Red Bull Racing"
        assert rows[0]["team_colour"] == "#3671c6"

    def test_speed_trap_uses_fastest_reading(self, session):
        assert build_timing_rows(session)[0]["speed_kmh"] == 310.0

    def test_tyre_history_counts_laps_per_stint(self, session):
        history = build_timing_rows(session)[0]["tyre_history"]

        assert history == [{"compound": "SOFT", "laps_used": 2}]


class TestSectors:
    def test_theoretical_best_sums_fastest_sectors(self, session):
        rows = build_timing_rows(session)

        # VER holds S1 (30.0) and S2 (30.0); VER's S3 30.5 also leads.
        assert theoretical_best(rows) == pytest.approx(90.5)

    def test_sector_leaders_rank_by_time(self, session):
        leaders = sector_leaders(build_timing_rows(session))

        assert len(leaders) == 3
        assert leaders[0][0]["code"] == "VER"
        assert leaders[0][1]["code"] == "HAM"
        assert leaders[0][0]["time"] == "30.000"

    def test_sector_leaders_handle_missing_times(self):
        rows = [
            {
                "code": "VER",
                "team_colour": "#3671c6",
                "team_name": "Red Bull Racing",
                "sectors": [{"seconds": None} for _ in range(3)],
            }
        ]

        assert sector_leaders(rows) == [[], [], []]


class TestMicroSectors:
    @staticmethod
    def _trace(speed_kmh: float, length: float = 5000.0, points: int = 400) -> pd.DataFrame:
        distance = np.linspace(0.0, length, points)
        seconds = distance / (speed_kmh / 3.6)
        return pd.DataFrame({"Distance": distance, "Time": pd.to_timedelta(seconds, unit="s")})

    def test_constant_speed_gives_equal_slices(self):
        times = micro_sector_times(self._trace(180.0))

        assert times is not None and len(times) == TOTAL_SEGMENTS
        assert np.allclose(times, times[0])
        # 5 km at 180 km/h = 100 s total.
        assert times.sum() == pytest.approx(100.0, abs=0.1)

    def test_short_trace_returns_none(self):
        tiny = pd.DataFrame({"Distance": [0.0, 1.0], "Time": pd.to_timedelta([0, 1], unit="s")})

        assert micro_sector_times(tiny) is None

    def test_missing_columns_return_none(self):
        assert micro_sector_times(pd.DataFrame({"Distance": [1.0]})) is None

    def test_fastest_driver_is_purple_everywhere(self):
        states = segment_states({"FAST": np.full(15, 1.0), "SLOW": np.full(15, 2.0)})

        assert states["FAST"] == ["PURPLE"] * 15
        assert states["SLOW"] == ["YELLOW"] * 15

    def test_close_driver_is_green_not_yellow(self):
        # 1% off the best, inside the 2% green tolerance.
        states = segment_states({"BEST": np.full(15, 1.0), "CLOSE": np.full(15, 1.01)})

        assert states["CLOSE"] == ["GREEN"] * 15

    def test_states_split_evenly_across_sectors(self):
        session_states = segment_states({"VER": np.arange(1.0, 16.0)})

        assert len(session_states["VER"]) == SEGMENTS_PER_SECTOR * 3

    def test_no_drivers_yields_empty_mapping(self):
        assert segment_states({}) == {}


def _race_laps(driver: str, lap_times, cumulative_start=0.0, positions=None) -> list:
    """Race laps for one driver: LapTime plus the session Time at lap end."""
    rows = []
    elapsed = cumulative_start
    for number, seconds in enumerate(lap_times, start=1):
        elapsed += seconds
        rows.append(
            {
                "Driver": driver,
                "LapNumber": number,
                "LapTime": timedelta(seconds=seconds),
                "Time": timedelta(seconds=elapsed),
                "Position": (positions or [1] * len(lap_times))[number - 1],
                "Sector1Time": timedelta(seconds=seconds / 3),
                "Sector2Time": timedelta(seconds=seconds / 3),
                "Sector3Time": timedelta(seconds=seconds / 3),
                "Compound": "SOFT",
                "Stint": 1,
                "SpeedST": 300.0,
            }
        )
    return rows


def _results(*entries) -> pd.DataFrame:
    """FastF1-shaped session.results: Abbreviation/Position/Status/Time."""
    return pd.DataFrame(
        [
            {
                "Abbreviation": code,
                "Position": float(position),
                "ClassifiedPosition": str(position),
                "Status": status,
                "Time": time if time is None else timedelta(seconds=time),
                "TeamName": "",
                "GridPosition": float(position),
            }
            for code, position, status, time in entries
        ]
    )


@pytest.fixture
def race_session():
    """The winner is NOT the fastest-lap setter - the DASH-01 case.

    PER laps quicker than VER but finishes second; ALO is a lap down.
    """
    laps = _laps(
        _race_laps("VER", [90.0, 90.0, 90.0], positions=[1, 1, 1]),
        _race_laps("PER", [92.0, 89.0, 88.0], positions=[2, 2, 2]),
        _race_laps("ALO", [95.0, 95.0], positions=[3, 3]),
    )
    return {
        "session_info": {"session_type": "R", "gp": "Bahrain", "year": 2023},
        "laps": laps,
        "results": _results(
            ("VER", 1, "Finished", 270.0),
            ("PER", 2, "Finished", 5.0),
            ("ALO", 3, "+1 Lap", None),
        ),
        "drivers": _drivers(
            ("VER", "Red Bull Racing", "#3671c6"),
            ("PER", "Red Bull Racing", "#3671c6"),
            ("ALO", "Aston Martin", "#229971"),
        ),
        "telemetry": {},
        "is_live": False,
    }


class TestRaceClassification:
    """DASH-01: a race is ordered by finishing position, not by best lap."""

    def test_race_is_ordered_by_finishing_position(self, race_session):
        rows = build_timing_rows(race_session)

        assert [r["code"] for r in rows] == ["VER", "PER", "ALO"]

    def test_the_fastest_lap_setter_is_not_promoted(self, race_session):
        rows = build_timing_rows(race_session)

        # PER holds the fastest lap (88.0) but finished second.
        assert rows[0]["code"] == "VER"
        assert rows[1]["code"] == "PER"
        assert rows[1]["best_seconds"] < rows[0]["best_seconds"]

    def test_gap_is_race_time_behind_the_leader(self, race_session):
        rows = build_timing_rows(race_session)

        assert rows[0]["gap"] == "----"
        assert rows[1]["gap"] == "+5.000"

    def test_lapped_cars_show_a_lap_gap(self, race_session):
        rows = build_timing_rows(race_session)

        assert rows[2]["gap"] == "+1 LAP"

    def test_interval_is_to_the_car_ahead(self, race_session):
        rows = build_timing_rows(race_session)

        assert rows[1]["interval"] == "+5.000"
        assert rows[2]["interval"] == "+1 LAP"

    def test_sprint_uses_race_semantics(self, race_session):
        race_session["session_info"]["session_type"] = "S"

        rows = build_timing_rows(race_session)

        assert [r["code"] for r in rows] == ["VER", "PER", "ALO"]

    def test_falls_back_to_the_last_lap_position_without_results(self, race_session):
        race_session["results"] = pd.DataFrame()

        rows = build_timing_rows(race_session)

        assert [r["code"] for r in rows] == ["VER", "PER", "ALO"]
        assert rows[2]["gap"] == "+1 LAP"

    def test_practice_still_ranks_by_best_lap(self, race_session):
        race_session["session_info"]["session_type"] = "FP1"

        rows = build_timing_rows(race_session)

        assert [r["code"] for r in rows] == ["PER", "VER", "ALO"]

    def test_qualifying_still_ranks_by_best_lap(self, race_session):
        race_session["session_info"]["session_type"] = "Q"

        rows = build_timing_rows(race_session)

        assert rows[0]["code"] == "PER"

    def test_a_session_without_a_type_keeps_best_lap_ordering(self, session):
        rows = build_timing_rows(session)

        assert [r["code"] for r in rows] == ["VER", "HAM"]
