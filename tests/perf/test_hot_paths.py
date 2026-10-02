"""Micro-benchmarks for the row-wise hot paths (REPO-08).

Per-record `pd.to_numeric`, `iterrows()` with a `pd.Series` built per row, and
a "vectorised" `seconds_series` that was a Python loop all cost real time at
the sizes a race produces. The correctness checks here are ordinary tests;
the timing budgets carry the ``perf`` marker (TEST-04), which the default run
deselects (``pytest.ini``) and CI runs in a job of its own, so a busy machine
cannot turn the main suite red.

    python -m pytest -m perf
"""

import time

import numpy as np
import pandas as pd
import pytest

from processing.time_utils import seconds_series, to_seconds

ROWS = 20_000


def _elapsed(call, repeat: int = 3) -> float:
    """Best of ``repeat`` runs: the minimum is the least noisy estimate."""
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        best = min(best, time.perf_counter() - start)
    return best


@pytest.fixture(scope="module")
def lap_time_strings() -> pd.Series:
    return pd.Series([f"1:3{index % 10}.{index % 1000:03d}" for index in range(ROWS)])


class TestSecondsSeries:
    def test_it_matches_the_scalar_parser(self, lap_time_strings):
        vectorised = seconds_series(lap_time_strings)
        scalar = pd.Series([to_seconds(value) for value in lap_time_strings])

        pd.testing.assert_series_equal(vectorised, scalar, check_dtype=False)

    def test_it_handles_every_accepted_shape(self):
        values = pd.Series(
            [
                "1:31.204",  # live feed
                "31.105",  # sector
                pd.Timedelta(91.2, unit="s"),  # FastF1
                90.5,  # numeric
                None,
                pd.NaT,
                "",
                "nonsense",
            ]
        )

        result = seconds_series(values)

        assert result.iloc[0] == pytest.approx(91.204)
        assert result.iloc[1] == pytest.approx(31.105)
        assert result.iloc[2] == pytest.approx(91.2)
        assert result.iloc[3] == pytest.approx(90.5)
        assert result.iloc[4:].isna().all()

    @pytest.mark.perf
    def test_a_timedelta_column_converts_without_touching_strings(self):
        values = pd.Series(pd.to_timedelta(np.arange(ROWS), unit="s"))

        elapsed = _elapsed(lambda: seconds_series(values))

        assert elapsed < 0.05, f"{ROWS} timedeltas took {elapsed * 1000:.0f} ms"


class TestDriverMaps:
    @staticmethod
    def _drivers(count: int = 22) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "driver_number": [str(n) for n in range(1, count + 1)],
                "name_acronym": [f"D{n:02d}" for n in range(1, count + 1)],
                "team_colour": ["#3671c6"] * count,
                "team_name": ["Team"] * count,
                "full_name": [f"Driver {n}" for n in range(1, count + 1)],
            }
        )

    def test_the_colour_map_is_built_without_iterrows(self):
        from processing.telemetry_processor import TelemetryProcessor

        colours = TelemetryProcessor().build_driver_color_map(self._drivers())

        assert len(colours) == 22
        assert colours["D01"] == "#3671c6"

    @pytest.mark.perf
    def test_building_it_is_cheap_at_grid_size(self):
        from processing.telemetry_processor import TelemetryProcessor

        processor = TelemetryProcessor()
        drivers = self._drivers()

        elapsed = _elapsed(lambda: [processor.build_driver_color_map(drivers) for _ in range(200)])

        assert elapsed < 1.0, f"200 builds took {elapsed:.2f} s"


class TestCarDataParsing:
    @staticmethod
    def _per_record_parse(records):
        """What parse_car_data used to do: one to_numeric call per field."""
        return pd.DataFrame(
            [
                {
                    "driver_number": r.get("DriverNo"),
                    "timestamp": r.get("Utc"),
                    "RPM": pd.to_numeric(r.get("rpm"), errors="coerce"),
                    "Speed": pd.to_numeric(r.get("speed"), errors="coerce"),
                    "nGear": pd.to_numeric(r.get("n_gear"), errors="coerce"),
                    "Throttle": pd.to_numeric(r.get("throttle"), errors="coerce"),
                    "Brake": pd.to_numeric(r.get("brake"), errors="coerce"),
                    "DRS": pd.to_numeric(r.get("drs"), errors="coerce"),
                }
                for r in records
            ]
        )

    @pytest.mark.perf
    def test_column_conversion_beats_per_record_conversion(self):
        """REPO-08's 5x, where it is real: 120 000 scalar calls become six."""
        from data.live_adapter import LiveDataProcessor

        records = self._records()

        old = _elapsed(lambda: self._per_record_parse(records))
        new = _elapsed(lambda: LiveDataProcessor.parse_car_data(records))

        assert new * 5 < old, f"new {new * 1000:.0f} ms vs old {old * 1000:.0f} ms"

    @staticmethod
    def _records():
        return [
            {
                "DriverNo": str(index % 22 + 1),
                "Utc": "2026-09-06T13:00:00.000Z",
                "rpm": 11000,
                "speed": 250,
                "n_gear": 7,
                "throttle": 90,
                "brake": 0,
                "drs": 8,
            }
            for index in range(ROWS)
        ]

    @pytest.mark.perf
    def test_parsing_a_full_buffer_is_quick(self):
        from data.live_adapter import LiveDataProcessor

        elapsed = _elapsed(lambda: LiveDataProcessor.parse_car_data(self._records()))

        assert elapsed < 0.5, f"{ROWS} records took {elapsed * 1000:.0f} ms"

    def test_the_parsed_frame_is_numeric(self):
        from data.live_adapter import LiveDataProcessor

        frame = LiveDataProcessor.parse_car_data(
            [{"DriverNo": "1", "Utc": "t", "speed": "250", "rpm": "11000"}]
        )

        # Numeric, whichever width pandas picks for the column.
        assert frame["Speed"].dtype.kind in "if"
        assert frame["Speed"].iloc[0] == 250.0
        assert frame["RPM"].iloc[0] == 11000


class TestStintChartTraces:
    def test_one_trace_per_compound_not_per_stint(self):
        from ui.layout import stint_traces

        stints = pd.DataFrame(
            {
                "DriverAcronym": ["VER", "VER", "HAM", "HAM"],
                "Compound": ["SOFT", "HARD", "SOFT", "MEDIUM"],
                "LapStart": [1, 20, 1, 25],
                "LapEnd": [19, 58, 24, 58],
                "LapCount": [19, 39, 24, 34],
            }
        )

        traces = stint_traces(stints, {})

        assert len(traces) == 3  # SOFT, HARD, MEDIUM
        assert sum(len(trace.x) for trace in traces) == 4

    @pytest.mark.perf
    def test_a_race_of_stints_builds_quickly(self):
        from ui.layout import stint_traces

        rows = 22 * 4
        stints = pd.DataFrame(
            {
                "DriverAcronym": [f"D{i % 22:02d}" for i in range(rows)],
                "Compound": ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE"] * 22,
                "LapStart": np.arange(rows) % 50 + 1,
                "LapEnd": np.arange(rows) % 50 + 10,
                "LapCount": [10] * rows,
            }
        )

        elapsed = _elapsed(lambda: stint_traces(stints, {}))

        assert elapsed < 0.5, f"{rows} stints took {elapsed * 1000:.0f} ms"


class TestReplayPositionLookup:
    """REPLAY-01: a replay frame must not scan the whole timeline."""

    @pytest.mark.perf
    def test_a_lookup_on_a_full_race_cube_is_under_two_milliseconds(self):
        from processing.replay import PositionCube, positions_at

        frames, drivers = 14_000, 22
        rng = np.random.default_rng(1)
        cube = PositionCube(
            t0=3600.0,
            step=0.5,
            codes=tuple(f"D{index:02d}" for index in range(drivers)),
            xy=rng.normal(size=(frames, drivers, 2)).astype("float32"),
        )
        moments = 3600.0 + rng.uniform(0, (frames - 1) * 0.5, size=200)

        positions_at(cube, float(moments[0]))  # warm-up
        start = time.perf_counter()
        for moment in moments:
            markers = positions_at(cube, float(moment))
        per_call = (time.perf_counter() - start) / len(moments)

        assert len(markers) == drivers
        assert per_call < 0.002, f"{per_call * 1000:.2f} ms per lookup"


class TestTowerSeries:
    @pytest.mark.perf
    def test_a_full_race_builds_in_under_150_milliseconds(self):
        """REPLAY-28: 22 cars, 57 laps, ~31 k stream rows took ~0.45 s."""
        from processing.replay_model import tower_series
        from tests.test_replay_model import _big_race

        race = _big_race()
        tower_series(race)  # warm imports and caches

        assert _elapsed(lambda: tower_series(race), repeat=5) < 0.15
