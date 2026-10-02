"""The session as it stood at time t (IMPROVEMENTS.md REPLAY-03).

Built on the synthetic sessions in :mod:`tests.replay_fixtures`: a
three-car race where B passes A on lap 3, C pits on lap 4 and A retires on
lap 5; a 22-car qualifying with three segments; a practice session.
"""

import time

import numpy as np
import pandas as pd
import pytest

from processing.replay_model import (
    FINISHED,
    IN_PIT,
    KNOCKED_OUT,
    LEADER,
    MISSING,
    ON_TRACK,
    OUT,
    RACE_OUT_AFTER_SECONDS,
    events,
    flag_state,
    flag_timeline,
    format_gap,
    format_laptime,
    snapshot_at,
    tower_series,
)
from processing.timing import build_timing_rows
from tests import replay_fixtures as fx


@pytest.fixture(scope="module")
def race():
    return fx.race_session()


@pytest.fixture(scope="module")
def race_series(race):
    return tower_series(race)


def _order(session, moment, series=None):
    return [row["code"] for row in build_timing_rows(snapshot_at(session, moment, series))]


def _row(session, moment, code, series=None):
    rows = build_timing_rows(snapshot_at(session, moment, series))
    return next(row for row in rows if row["code"] == code)


class TestFormatting:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(4.2113, "+4.211"), (0.0, "+0.000"), (62.34, "+1:02.3"), (None, MISSING)],
    )
    def test_gaps(self, seconds, expected):
        assert format_gap(seconds) == expected

    def test_lap_times(self):
        assert format_laptime(92.456) == "1:32.456"
        assert format_laptime(float("nan")) == MISSING


class TestRaceOrder:
    def test_at_the_end_the_order_is_the_final_result(self, race, race_series):
        clock = race["session_info"]["replay_clock"]
        final = race["results"].sort_values("Position")["Abbreviation"].tolist()

        assert _order(race, clock["end"], race_series) == final

    def test_at_lights_out_the_order_is_the_grid(self, race, race_series):
        assert _order(race, fx.LIGHTS_OUT + 1, race_series) == ["A", "B", "C"]

    @pytest.mark.parametrize("moment", [1183.0, 1230.0, 1270.0])
    def test_between_laps_two_and_three_a_leads(self, race, race_series, moment):
        assert _order(race, moment, race_series) == ["A", "B", "C"]

    @pytest.mark.parametrize("moment", [1274.0, 1300.0])
    def test_after_lap_three_b_leads(self, race, race_series, moment):
        assert _order(race, moment, race_series) == ["B", "A", "C"]

    def test_the_leader_shows_leader_and_the_rest_a_gap(self, race, race_series):
        rows = build_timing_rows(snapshot_at(race, 1200.0, race_series))

        assert rows[0]["gap"] == LEADER
        assert rows[1]["gap"] == "+1.000"
        assert rows[2]["interval"] == "+1.000"

    def test_a_lapped_car_reads_plus_one_lap(self, race, race_series):
        a = _row(race, 1420.0, "A", race_series)

        assert a["gap"] == "+1 LAP"


class TestRaceOrderWithoutAStream:
    """REPLAY-08 groundwork: timing-line estimates when the stream is empty."""

    @pytest.fixture(scope="class")
    def estimated(self):
        return fx.race_session(with_stream=False)

    def test_the_order_still_follows_the_laps(self, estimated):
        assert _order(estimated, 1230.0) == ["A", "B", "C"]
        assert _order(estimated, 1274.0) == ["B", "A", "C"]

    def test_the_end_order_is_the_result(self, estimated):
        end = estimated["session_info"]["replay_clock"]["end"]

        assert _order(estimated, end) == ["B", "C", "A"]

    def test_gaps_are_measured_at_the_line(self, estimated):
        rows = build_timing_rows(snapshot_at(estimated, 1200.0))

        assert rows[1]["gap"] == "+1.000"  # B crossed 1 s after A

    def test_it_says_the_gaps_are_estimated(self, estimated):
        assert tower_series(estimated).estimated
        assert snapshot_at(estimated, 1200.0)["session_info"]["gaps_estimated"]


class TestPitStop:
    @pytest.mark.parametrize("moment", [fx.C_PIT_IN, 1365.0, fx.C_PIT_OUT - 0.5])
    def test_c_is_in_the_pits_between_entry_and_exit(self, race, race_series, moment):
        assert _row(race, moment, "C", race_series)["status"] == IN_PIT

    def test_c_is_on_track_either_side(self, race, race_series):
        assert _row(race, fx.C_PIT_IN - 1, "C", race_series)["status"] == ON_TRACK
        assert _row(race, fx.C_PIT_OUT + 1, "C", race_series)["status"] == ON_TRACK

    def test_the_tyre_changes_when_c_leaves_the_pits(self, race, race_series):
        before = _row(race, fx.C_PIT_OUT - 1, "C", race_series)["tyre_history"]
        after = _row(race, fx.C_PIT_OUT + 1, "C", race_series)["tyre_history"]

        assert before[-1]["compound"] == "SOFT"
        assert after[-1]["compound"] == "HARD"
        assert race_series.value("C", "tyre", fx.C_PIT_OUT - 1) == "SOFT"
        assert race_series.value("C", "tyre", fx.C_PIT_OUT) == "HARD"

    def test_the_pit_count_goes_from_zero_to_one(self, race, race_series):
        assert race_series.value("C", "pits", fx.C_PIT_IN - 1) == 0
        assert race_series.value("C", "pits", fx.C_PIT_IN) == 1


class TestRetirementAndFinish:
    def test_a_is_on_track_until_its_last_sample_plus_the_race_silence(self, race, race_series):
        last = fx.RACE_END_BY["A"]

        assert race_series.value("A", "status", fx.LIGHTS_OUT + 10) == ON_TRACK
        assert race_series.value("A", "status", last + RACE_OUT_AFTER_SECONDS - 0.1) == ON_TRACK
        assert race_series.value("A", "status", last + RACE_OUT_AFTER_SECONDS) == OUT

    def test_cars_finish_when_they_cross_after_the_flag(self, race, race_series):
        assert race_series.value("B", "status", 1449.0) == ON_TRACK
        assert race_series.value("B", "status", 1450.0) == FINISHED
        assert race_series.value("C", "status", 1460.0) == ON_TRACK
        assert race_series.value("C", "status", 1470.0) == FINISHED

    def test_the_header_lap_counter(self, race, race_series):
        info = snapshot_at(race, fx.LIGHTS_OUT + 1, race_series)["session_info"]

        assert (info["current_lap"], info["total_laps"]) == (1, 5)
        assert snapshot_at(race, 1455.0, race_series)["session_info"]["current_lap"] == 5


class TestLapsInTheSnapshot:
    def test_completed_laps_only_up_to_t(self, race, race_series):
        laps = snapshot_at(race, 1200.0, race_series)["laps"]
        done = laps[~laps["IsInProgress"].astype(bool)]

        assert set(done.groupby("Driver").size()) == {2}

    def test_the_lap_in_progress_shows_only_revealed_sectors(self, race, race_series):
        laps = snapshot_at(race, 1181.0 + 40.0, race_series)["laps"]
        in_progress = laps["IsInProgress"].astype(bool)
        running = laps[in_progress & (laps["Driver"] == "B")].iloc[0]

        assert pd.notna(running["Sector1Time"])
        assert pd.isna(running["Sector2Time"])
        assert pd.isna(running["LapTime"])

    def test_the_last_lap_is_the_last_completed_one(self, race, race_series):
        b = _row(race, 1200.0, "B", race_series)

        assert b["last_lap"] == format_laptime(90.0)

    def test_sector_cells_follow_the_lap_in_progress(self, race, race_series):
        b = _row(race, 1181.0 + 31.0, "B", race_series)

        assert b["sectors"][0]["seconds"] == pytest.approx(30.0)

    def test_results_never_reach_the_snapshot(self, race, race_series):
        assert snapshot_at(race, 1200.0, race_series)["results"].empty


class TestStreamsInTheSnapshot:
    def test_race_control_only_grows(self, race, race_series):
        counts = [
            len(snapshot_at(race, moment, race_series)["race_control"])
            for moment in np.linspace(900.0, 1500.0, 25)
        ]

        assert counts == sorted(counts)

    def test_the_flag_follows_the_track_status(self, race, race_series):
        assert flag_state(snapshot_at(race, 1200.0, race_series)) == "GREEN"
        assert flag_state(snapshot_at(race, fx.SC_START + 1, race_series)) == "SAFETY CAR"
        assert flag_state(snapshot_at(race, fx.SC_END + 1, race_series)) == "GREEN"
        assert flag_state(snapshot_at(race, 1455.0, race_series)) == "CHEQUERED"

    def test_the_weather_is_the_last_reading_before_t(self, race, race_series):
        weather = snapshot_at(race, 1125.0, race_series)["weather"]

        assert weather["Time"].iloc[-1] == pd.Timedelta(seconds=1120.0)

    def test_the_flag_timeline(self, race):
        states = [state for _, state in flag_timeline(race)]

        assert states == ["GREEN", "SAFETY CAR", "GREEN", "CHEQUERED"]


def _garbled(session: dict, moment: float, seed: int) -> dict:
    """A copy where everything stamped after ``moment`` is replaced by noise.

    Timestamps stay after ``moment`` (they are the stamps), but every value
    that was only known later is scrambled, and the final results are
    replaced entirely. A snapshot at ``moment`` must not notice.
    """
    rng = np.random.default_rng(seed)
    copy = dict(session)
    later_by = pd.to_timedelta(rng.uniform(0.1, 50, 1000), unit="s")

    laps = session["laps"].copy()
    ends = laps["Time"].dt.total_seconds()
    starts = laps["LapStartTime"].dt.total_seconds()
    future = (starts > moment).to_numpy()
    touched = future | ~(ends <= moment).to_numpy()
    count = len(laps)
    noise = pd.to_timedelta(rng.uniform(60, 200, count), unit="s")
    laps.loc[touched, "LapTime"] = noise[touched]
    for number in (1, 2, 3):
        # A sector already completed by `moment` is known; later ones are not.
        unknown = (laps[f"Sector{number}SessionTime"].dt.total_seconds() > moment).to_numpy()
        unknown |= future
        laps.loc[unknown, f"Sector{number}Time"] = noise[unknown]
    laps.loc[touched, "Position"] = rng.integers(1, 20, count)[touched]
    laps.loc[touched, "SpeedST"] = rng.uniform(100, 400, count)[touched]
    laps.loc[future, "Compound"] = rng.choice(["WET", "INTERMEDIATE"], count)[future]
    laps.loc[future, "TyreLife"] = rng.integers(20, 40, count)[future]
    laps.loc[future, "Deleted"] = rng.choice([True, False], count)[future]
    stamps = ["Time", "PitInTime", "PitOutTime"] + [f"Sector{n}SessionTime" for n in (1, 2, 3)]
    for column in stamps:
        after = (laps[column].dt.total_seconds() > moment).to_numpy()
        laps.loc[after, column] = pd.Timedelta(seconds=moment) + later_by[:count][after]
    # An out-lap whose car has not left the box yet: its new tyre is not known.
    waiting = laps["PitOutTime"].dt.total_seconds() > moment
    laps.loc[waiting.to_numpy() & touched, "Compound"] = "WET"
    copy["laps"] = laps

    stream = session["timing_stream"].copy()
    after = stream["Time"] > moment
    stream.loc[after, "Position"] = rng.integers(1, 4, int(after.sum()))
    stream.loc[after, ["GapToLeader", "IntervalToPositionAhead"]] = "+99.999"
    stream.loc[after, ["GapSeconds", "IntervalSeconds"]] = 99.999
    copy["timing_stream"] = stream

    positions = session["positions"].copy()
    after = positions["Time"] > moment
    positions.loc[after, ["X", "Y"]] = rng.normal(size=(int(after.sum()), 2)) * 1e4
    copy["positions"] = positions

    control = session["race_control"].copy()
    after = control["SessionTime"].dt.total_seconds() > moment
    control.loc[after, ["Flag", "Message", "Scope"]] = ["CHEQUERED", "NOISE", "Track"]
    copy["race_control"] = control

    weather = session["weather"].copy()
    after = weather["Time"].dt.total_seconds() > moment
    weather.loc[after, "AirTemp"] = rng.uniform(-50, 80, int(after.sum()))
    copy["weather"] = weather

    status = session["track_status"].copy()
    status.loc[status["Time"] > moment, "Status"] = "5"
    copy["track_status"] = status

    copy["results"] = session["results"].iloc[::-1].assign(Status="Disqualified")
    return copy


ROW_KEYS = (
    "code",
    "position",
    "gap",
    "interval",
    "status",
    "last_lap",
    "best_lap",
    "tyre_history",
    "pits",
    "partition",
    "speed_kmh",
    "diff",
)


def _comparable(snapshot: dict) -> tuple:
    rows = build_timing_rows(snapshot)
    tower = []
    for row in rows:
        entry = {key: row.get(key) for key in ROW_KEYS}
        entry["sectors"] = [sector["display"] for sector in row["sectors"]]
        tower.append(entry)
    weather = snapshot["weather"]
    last_weather = weather.iloc[-1].to_dict() if not weather.empty else {}
    return tower, flag_state(snapshot), last_weather


class TestNoFutureLeaks:
    """The ground rule: a snapshot at t reads only rows stamped at or before t."""

    @pytest.mark.parametrize("seed", range(20))
    def test_what_happened_later_does_not_change_the_snapshot(self, race, race_series, seed):
        clock = race["session_info"]["replay_clock"]
        moment = float(np.random.default_rng(seed).uniform(clock["start"], clock["end"]))

        garbled = _garbled(race, moment, seed)
        expected = _comparable(snapshot_at(race, moment, race_series))
        actual = _comparable(snapshot_at(garbled, moment, tower_series(garbled)))

        assert actual == expected


@pytest.fixture(scope="module")
def qualifying():
    return fx.qualifying_session()


@pytest.fixture(scope="module")
def q_series(qualifying):
    return tower_series(qualifying)


class TestQualifying:
    def test_nobody_is_knocked_out_before_q1_ends(self, qualifying, q_series):
        rows = build_timing_rows(snapshot_at(qualifying, fx.Q_STARTS[1] - 1, q_series))

        assert not any(row["status"] == KNOCKED_OUT for row in rows)
        assert not any(row["partition"] for row in rows)

    def test_after_q1_the_bottom_six_are_out(self, qualifying, q_series):
        rows = build_timing_rows(snapshot_at(qualifying, fx.Q_STARTS[1] + 1, q_series))
        out = [row["code"] for row in rows if row["status"] == KNOCKED_OUT]

        assert out == fx.Q_CODES[16:]
        assert rows[16]["partition"] == "Eliminated in Q1"
        assert not any(row["partition"] == "Eliminated in Q2" for row in rows)

    def test_the_ten_six_six_split_appears_only_after_q2(self, qualifying, q_series):
        during = build_timing_rows(snapshot_at(qualifying, fx.Q_STARTS[2] - 1, q_series))
        after = build_timing_rows(snapshot_at(qualifying, fx.Q_STARTS[2] + 1, q_series))

        assert sum(row["status"] == KNOCKED_OUT for row in during) == 6
        assert sum(row["status"] == KNOCKED_OUT for row in after) == 12
        headings = [
            (index, row["partition"]) for index, row in enumerate(after) if row["partition"]
        ]
        assert headings == [(0, "Q3"), (10, "Eliminated in Q2"), (16, "Eliminated in Q1")]

    def test_q2_is_ordered_by_q2_times(self, qualifying, q_series):
        rows = build_timing_rows(snapshot_at(qualifying, 2000.0, q_series))

        assert [row["code"] for row in rows[:16]] == fx.Q_CODES[:16]

    def test_a_faster_lap_mid_q2_moves_up_at_that_lap(self, qualifying, q_series):
        before = _order(qualifying, fx.Q2_LATE_LAP_END - 0.5, q_series)
        after = _order(qualifying, fx.Q2_LATE_LAP_END, q_series)

        assert before.index(fx.Q2_LATE_DRIVER) == 15
        assert after.index(fx.Q2_LATE_DRIVER) == 0

    def test_the_header_clock_counts_the_segment(self, qualifying, q_series):
        info = snapshot_at(qualifying, fx.Q_STARTS[1] + 65.0, q_series)["session_info"]

        assert info["segment"] == "Q2"
        assert info["segment_elapsed"] == pytest.approx(65.0)

    def test_cars_in_the_garage_are_in_the_pits(self, q_series):
        assert q_series.value("D21", "status", fx.Q_STARTS[0] + 1) == IN_PIT
        assert q_series.value("D00", "status", fx.Q_STARTS[0] + 50) == ON_TRACK


class TestPractice:
    def test_order_by_best_lap_so_far(self):
        session = fx.practice_session()

        assert _order(session, 400.0)[0] == "A"  # A's 91.0 is the best so far
        assert _order(session, 600.0)[0] == "E"  # E's 90.5 comes last


class TestEvents:
    def test_the_race_events(self, race, race_series):
        found = events(race, race_series)
        kinds = [kind for _, kind, _ in found]

        assert found == sorted(found, key=lambda item: item[0])
        assert (fx.SC_START, "sc", "Safety car") in found
        assert (fx.C_PIT_IN, "pit", "Pit stop - C") in found
        assert any(kind == "out" and label.endswith("A") for _, kind, label in found)
        assert "fastest" in kinds
        assert (1450.0, "flag", "Chequered flag") in found


def _big_race() -> dict:
    """22 cars, 57 laps, a stream update every 4 s: the size of a real race."""
    rng = np.random.default_rng(3)
    codes = [f"D{index:02d}" for index in range(22)]
    rows, stream_rows = [], []
    for number, code in enumerate(codes):
        start = 3600.0
        for lap in range(1, 58):
            lap_time = 95.0 + rng.uniform(0, 2) + number * 0.05
            end = start + lap_time
            rows.append(
                {
                    "Driver": code,
                    "DriverNumber": str(number),
                    "LapNumber": float(lap),
                    "LapTime": pd.Timedelta(seconds=lap_time),
                    "Time": pd.Timedelta(seconds=end),
                    "LapStartTime": pd.Timedelta(seconds=start),
                    "Sector1Time": pd.Timedelta(seconds=lap_time / 3),
                    "Sector2Time": pd.Timedelta(seconds=lap_time / 3),
                    "Sector3Time": pd.Timedelta(seconds=lap_time / 3),
                    "PitInTime": end - 5 if lap == 20 else np.nan,
                    "PitOutTime": start + 20 if lap == 21 else np.nan,
                    "Compound": "HARD" if lap > 20 else "MEDIUM",
                    "Stint": 2.0 if lap > 20 else 1.0,
                    "TyreLife": float(lap if lap <= 20 else lap - 20),
                    "FreshTyre": True,
                    "Deleted": False,
                    "IsAccurate": True,
                }
            )
            for tick in np.arange(start, end, 4.0):
                stream_rows.append((tick, code, number + 1, f"+{number * 1.5:.3f}", "+1.500"))
            start = end
    stream = pd.DataFrame(
        stream_rows,
        columns=["Time", "Driver", "Position", "GapToLeader", "IntervalToPositionAhead"],
    )
    stream["GapSeconds"] = stream["GapToLeader"].str.lstrip("+").astype(float)
    stream["GapLapsDown"] = 0
    stream["IntervalSeconds"] = 1.5
    stream["IntervalLapsDown"] = 0
    grid = np.arange(3600.0, 9200.0, 0.5)
    positions = pd.DataFrame(
        {
            "Time": np.tile(grid, len(codes)),
            "Driver": np.repeat(codes, len(grid)),
            "X": rng.normal(size=len(grid) * len(codes)),
            "Y": rng.normal(size=len(grid) * len(codes)),
        }
    )
    laps = pd.DataFrame(rows)
    for column in ("PitInTime", "PitOutTime"):
        laps[column] = pd.to_timedelta(laps[column], unit="s")
    return {
        "session_info": {"session_type": "R", "total_laps": 57, "session_start": 3600.0},
        "laps": laps,
        "timing_stream": stream,
        "positions": positions,
        "drivers": fx._drivers(codes),
        "track_status": pd.DataFrame({"Time": [3500.0], "Status": ["1"], "Message": ["x"]}),
        "weather": pd.DataFrame(),
        "race_control": pd.DataFrame(),
        "stints": pd.DataFrame(),
        "results": pd.DataFrame(),
    }


class TestPerformance:
    """Budgets from REPLAY-03: snapshot < 150 ms, series < 2 s."""

    @pytest.fixture(scope="class")
    def big_race(self):
        return _big_race()

    def test_tower_series_is_under_two_seconds(self, big_race):
        start = time.perf_counter()
        tower_series(big_race)

        assert time.perf_counter() - start < 2.0

    def test_a_snapshot_is_under_150_ms(self, big_race):
        series = tower_series(big_race)
        snapshot_at(big_race, 5000.0, series)  # warm-up

        start = time.perf_counter()
        build_timing_rows(snapshot_at(big_race, 6000.0, series))

        assert time.perf_counter() - start < 0.15


class TestTheDashboardDrawsAMoment:
    def test_the_header_reads_race_time_and_the_lap(self, race, race_series):
        from ui.dashboard import header_html

        markup = header_html(snapshot_at(race, fx.LIGHTS_OUT, race_series))

        assert "Race time" in markup
        assert "0:00:00" in markup
        assert "1/5" in markup

    def test_the_header_flag_is_the_flag_at_that_moment(self, race, race_series):
        from ui.dashboard import header_html

        assert ">SC<" in header_html(snapshot_at(race, fx.SC_START + 5, race_series))

    def test_the_map_shows_every_car_and_no_dominance(self, race, race_series):
        import base64
        import re

        from ui.dashboard import map_panel_html

        snapshot = snapshot_at(race, 1200.0, race_series)
        markup = map_panel_html(snapshot, build_timing_rows(snapshot))
        encoded = re.search(r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)", markup).group(1)
        svg = base64.b64decode(encoded).decode("utf-8")

        assert svg.count('r="9"') == 3  # one marker per car
        assert "Fastest per mini-sector" not in markup


class TestQualifyingReplay:
    """REPLAY-06: segments, partitions and the flying-lap marker."""

    def test_inside_q2_the_q1_eliminated_sit_below_under_their_heading(self, qualifying, q_series):
        rows = build_timing_rows(snapshot_at(qualifying, 2000.0, q_series))

        assert [row["code"] for row in rows[16:]] == fx.Q_CODES[16:]
        assert rows[16]["partition"] == "Eliminated in Q1"
        assert rows[0]["partition"] == "Q2"

    def test_a_lap_after_the_out_lap_is_marked_flying(self, q_series):
        # D00's Q1 run: out-lap 100-200, timed lap 200-290, then a lap that
        # turns out to be the in-lap only when the car enters the pits (390).
        assert q_series.value("D00", "flying", 150.0) is False
        assert q_series.value("D00", "flying", 250.0) is True
        # Nothing yet says the next lap is an in-lap, so it reads as flying...
        assert q_series.value("D00", "flying", 295.0) is True
        # ...until the pit entry decides it.
        assert q_series.value("D00", "flying", 391.0) is False

    def test_practice_marks_flying_laps_too(self):
        series = tower_series(fx.practice_session())

        assert series.value("A", "flying", 250.0) is True

    def test_races_have_no_flying_marker(self, race_series):
        assert "flying" not in race_series.fields["A"]


class TestNoFinalOrderBeforeTiming:
    """The drivers table follows session.results (finishing order); nothing
    in the opening order may depend on it."""

    @staticmethod
    def _order(session: dict, moment: float) -> list[str]:
        rows = build_timing_rows(snapshot_at(session, moment, tower_series(session)))
        return [row["code"] for row in rows]

    @pytest.mark.parametrize("builder", ["race", "qualifying", "practice"])
    def test_reversing_the_drivers_table_changes_nothing(self, builder):
        session = {
            "race": lambda: fx.race_session(with_stream=False),
            "qualifying": fx.qualifying_session,
            "practice": fx.practice_session,
        }[builder]()
        moment = float(session["session_info"].get("session_start") or 0.0) + 1.0
        before = self._order(session, moment)

        flipped = dict(session)
        flipped["drivers"] = session["drivers"].iloc[::-1].reset_index(drop=True)

        assert self._order(flipped, moment) == before

    def test_a_race_starts_in_grid_order(self):
        session = fx.race_session(with_stream=False)
        session["results"] = session["results"].assign(GridPosition=[3.0, 1.0, 2.0])  # B, C, A

        assert self._order(session, fx.LIGHTS_OUT + 1.0) == ["C", "A", "B"]


# --- REPLAY-20: deletions are known only once the stewards announce them ----


def _with_control(session: dict, messages: list[tuple[float, str]]) -> dict:
    """A copy whose race control also carries ``(session seconds, text)`` rows."""
    copy = dict(session)
    copy["laps"] = session["laps"].copy()
    added = pd.DataFrame(
        {
            "Time": pd.Timestamp("2026-06-07 13:00:00")
            + pd.to_timedelta([t for t, _ in messages], unit="s"),
            "SessionTime": pd.to_timedelta([t for t, _ in messages], unit="s"),
            "Lap": [None] * len(messages),
            "Category": ["Other"] * len(messages),
            "Flag": [None] * len(messages),
            "Scope": [None] * len(messages),
            "Message": [text for _, text in messages],
        }
    )
    control = session.get("race_control")
    frames = [control, added] if control is not None and not control.empty else [added]
    copy["race_control"] = pd.concat(frames, ignore_index=True)
    return copy


def _deletion_text(number: str, code: str, lap_s: float, lap: int) -> str:
    return (
        f"CAR {number} ({code}) TIME {format_laptime(lap_s)} DELETED - "
        f"TRACK LIMITS AT TURN 4 LAP {lap} 14:07:31"
    )


E_LAP_END = 503.0  # practice: E's 1:30.500, the session best


def _late_deleted_practice(reinstated_at: float | None = None) -> dict:
    session = fx.practice_session()
    messages = [(E_LAP_END + 90.0, _deletion_text("4", "E", 90.5, 2))]
    if reinstated_at is not None:
        messages.append((reinstated_at, "CAR 4 (E) TIME 1:30.500 LAP 2 REINSTATED"))
    session = _with_control(session, messages)
    laps = session["laps"]
    e_two = (laps["Driver"] == "E") & (laps["LapNumber"] == 2.0)
    laps["Deleted"] = e_two if reinstated_at is None else False
    laps["DeletedReason"] = np.where(e_two & (reinstated_at is None), "TRACK LIMITS", "")
    return session


class TestLateDeletions:
    def test_a_lap_is_session_best_until_the_deletion_is_announced(self):
        session = _late_deleted_practice()
        series = tower_series(session)

        before = build_timing_rows(snapshot_at(session, E_LAP_END + 30.0, series))
        after = build_timing_rows(snapshot_at(session, E_LAP_END + 120.0, series))

        assert before[0]["code"] == "E" and before[0]["is_overall_best"]
        assert before[0]["best_lap"] == "1:30.500"
        assert not before[0]["last_deleted"]
        e_after = next(row for row in after if row["code"] == "E")
        assert after[0]["code"] == "A" and after[0]["is_overall_best"]
        assert e_after["best_lap"] == "1:32.500"
        assert e_after["last_lap"] == "1:30.500" and e_after["last_deleted"]
        # FastF1's reason minus the local clock time.
        assert e_after["last_deleted_reason"] == "TRACK LIMITS"

    def test_the_message_reason_is_used_when_the_laps_have_none(self):
        session = _late_deleted_practice()
        session["laps"] = session["laps"].drop(columns=["DeletedReason"])

        row = _row(session, E_LAP_END + 120.0, "E")

        assert row["last_deleted_reason"] == "TRACK LIMITS AT TURN 4 LAP 2"

    def test_the_purple_last_lap_is_cleared_by_the_deletion(self):
        session = _late_deleted_practice()
        series = tower_series(session)

        assert series.value("E", "last_flag", E_LAP_END + 30.0) == "sb"
        assert series.value("E", "last_flag", E_LAP_END + 120.0) is None

    def test_a_reinstated_lap_counts_again(self):
        session = _late_deleted_practice(reinstated_at=E_LAP_END + 150.0)
        series = tower_series(session)

        assert _order(session, E_LAP_END + 120.0, series)[0] == "A"
        assert _order(session, E_LAP_END + 200.0, series)[0] == "E"
        assert not _row(session, E_LAP_END + 200.0, "E", series)["last_deleted"]

    def test_the_fastest_lap_event_is_kept_as_it_happened(self):
        found = events(_late_deleted_practice())

        assert (E_LAP_END, "fastest", "Fastest lap - E 1:30.500") in found

    def test_a_lap_deleted_at_its_end_never_counts(self):
        session = fx.practice_session()
        laps = session["laps"]
        laps["Deleted"] = (laps["Driver"] == "E") & (laps["LapNumber"] == 2.0)
        series = tower_series(session)

        assert _order(session, E_LAP_END + 1.0, series)[0] == "A"
        assert series.value("E", "last_flag", E_LAP_END + 1.0) is None


def _late_deletions(session: dict, moment: float, seed: int) -> dict:
    """A copy where laps completed by ``moment`` are deleted *after* it."""
    rng = np.random.default_rng(seed)
    laps = session["laps"]
    ends = laps["Time"].dt.total_seconds()
    done = laps[(ends <= moment) & laps["LapTime"].notna()]
    if done.empty:
        return session
    picked = done.sample(n=min(2, len(done)), random_state=seed)
    messages = [
        (
            moment + float(rng.uniform(0.5, 40)),
            _deletion_text(
                str(lap.DriverNumber),
                str(lap.Driver),
                lap.LapTime.total_seconds(),
                int(lap.LapNumber),
            ),
        )
        for lap in picked.itertuples()
    ]
    copy = _with_control(session, messages)
    copy["laps"]["Deleted"] = copy["laps"].index.isin(picked.index)
    return copy


class TestNoFutureLeaksFromLateDeletions:
    """REPLAY-20: a deletion announced after t leaves the snapshot at t alone."""

    @pytest.mark.parametrize("seed", range(10))
    @pytest.mark.parametrize("builder", ["race", "practice", "qualifying"])
    def test_a_later_deletion_does_not_change_the_snapshot(self, builder, seed):
        session = {
            "race": fx.race_session,
            "practice": fx.practice_session,
            "qualifying": fx.qualifying_session,
        }[builder]()
        clock = session["session_info"]["replay_clock"]
        moment = float(np.random.default_rng(seed).uniform(clock["start"], clock["end"]))

        deleted = _late_deletions(session, moment, seed)
        expected = _comparable(snapshot_at(session, moment, tower_series(session)))
        actual = _comparable(snapshot_at(deleted, moment, tower_series(deleted)))

        assert actual == expected


# --- REPLAY-17: one chequered flag per qualifying segment -------------------

Q1_FLAG, Q2_FLAG, Q3_FLAG = 1440.0, 2640.0, 3900.0
Q2_RED, Q2_RESTART = 1800.0, 1900.0


def _flagged_qualifying(session_type: str = "Q") -> dict:
    session = fx.qualifying_session()
    session["session_info"] = {**session["session_info"], "session_type": session_type}
    session["track_status"] = pd.DataFrame(
        {
            "Time": [0.0, Q2_RED, Q2_RESTART],
            "Status": ["1", "5", "1"],
            "Message": ["AllClear", "Red", "AllClear"],
        }
    )
    stamps = [Q1_FLAG, Q2_FLAG, Q3_FLAG]
    session["race_control"] = pd.DataFrame(
        {
            "Time": pd.Timestamp("2026-06-06 14:00:00") + pd.to_timedelta(stamps, unit="s"),
            "SessionTime": pd.to_timedelta(stamps, unit="s"),
            "Lap": [None, None, None],
            "Category": ["Flag"] * 3,
            "Flag": ["CHEQUERED"] * 3,
            "Scope": ["Track"] * 3,
            "Message": ["CHEQUERED FLAG"] * 3,
        }
    )
    return session


def _flag_at(session: dict, moment: float) -> str:
    return flag_state(snapshot_at(session, moment, tower_series(session)))


class TestQualifyingChequeredFlags:
    def test_a_q2_red_flag_survives_the_q1_chequered_flag(self):
        timeline = flag_timeline(_flagged_qualifying())

        assert (Q2_RED, "RED") in timeline
        assert [t for t, state in timeline if state == "CHEQUERED"] == [
            Q1_FLAG,
            Q2_FLAG,
            Q3_FLAG,
        ]
        assert (fx.Q_STARTS[1], "GREEN") in timeline

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            (1000.0, "GREEN"),
            (Q1_FLAG + 10.0, "CHEQUERED"),
            (1600.0, "GREEN"),
            (1850.0, "RED"),
            (2000.0, "GREEN"),
            (Q2_FLAG + 10.0, "CHEQUERED"),
            (3000.0, "GREEN"),
            (Q3_FLAG + 10.0, "CHEQUERED"),
        ],
    )
    def test_the_flag_state_returns_to_track_status_each_segment(self, moment, expected):
        assert _flag_at(_flagged_qualifying(), moment) == expected

    def test_one_flag_event_per_segment(self):
        found = [item for item in events(_flagged_qualifying()) if item[1] == "flag"]

        assert found == [
            (Q1_FLAG, "flag", "Chequered flag - Q1"),
            (Q2_FLAG, "flag", "Chequered flag - Q2"),
            (Q3_FLAG, "flag", "Chequered flag - Q3"),
        ]

    def test_sprint_qualifying_labels_its_segments(self):
        found = [label for _, kind, label in events(_flagged_qualifying("SQ")) if kind == "flag"]

        assert found[0] == "Chequered flag - SQ1"

    def test_practice_ends_at_its_last_chequered_flag(self):
        session = _flagged_qualifying("FP2")
        session["session_info"]["segment_starts"] = []

        timeline = flag_timeline(session)

        assert [t for t, state in timeline if state == "CHEQUERED"] == [Q3_FLAG]
        # A snapshot cannot know a later flag exists; after the last it is over.
        assert _flag_at(session, Q3_FLAG + 10.0) == "CHEQUERED"

    def test_a_race_still_ends_at_its_first_chequered_flag(self, race):
        timeline = flag_timeline(race)

        assert timeline[-1] == (1450.0, "CHEQUERED")


# --- REPLAY-24: the segment clock stops under a red flag --------------------


class TestSegmentClock:
    def test_the_segment_clock_is_frozen_under_a_red_flag(self):
        session = _flagged_qualifying()
        series = tower_series(session)

        def elapsed(moment: float) -> float:
            return snapshot_at(session, moment, series)["session_info"]["segment_elapsed"]

        q2 = fx.Q_STARTS[1]
        assert elapsed(Q2_RED) == pytest.approx(Q2_RED - q2)
        assert elapsed(Q2_RED + 50.0) == pytest.approx(Q2_RED - q2)
        assert elapsed(Q2_RESTART) == pytest.approx(Q2_RED - q2)
        assert elapsed(Q2_RESTART + 60.0) == pytest.approx(Q2_RED - q2 + 60.0)

    def test_the_remaining_time_counts_down_from_the_schedule(self):
        session = _flagged_qualifying()
        series = tower_series(session)

        info = snapshot_at(session, fx.Q_STARTS[0] + 60.0, series)["session_info"]

        assert info["segment"] == "Q1"
        assert info["segment_remaining"] == pytest.approx(17 * 60.0)

    def test_sprint_segments_are_named_sq(self):
        session = _flagged_qualifying("SQ")
        series = tower_series(session)

        info = snapshot_at(session, fx.Q_STARTS[1] + 60.0, series)["session_info"]

        assert info["segment"] == "SQ2"
        assert info["segment_remaining"] == pytest.approx(9 * 60.0)


def _drop_samples(session: dict, code: str, start: float, end: float) -> dict:
    positions = session["positions"]
    keep = ~(
        (positions["Driver"] == code) & (positions["Time"] >= start) & (positions["Time"] < end)
    )
    return {**session, "positions": positions[keep].reset_index(drop=True)}


class TestGpsDropouts:
    """REPLAY-23: a short loss of GPS is neither OUT nor a retirement."""

    def test_a_seven_second_gap_is_not_out(self):
        from processing.replay_model import events, tower_series

        session = _drop_samples(fx.race_session(), "B", 1200.0, 1207.0)
        series = tower_series(session)

        for moment in np.arange(1200.0, 1210.0, 0.5):
            assert series.value("B", "status", moment) == ON_TRACK
        assert not [e for e in events(session, series) if e[2] == "Retirement - B"]

    def test_a_long_gap_with_timing_progress_is_not_out(self):
        from processing.replay_model import tower_series

        # 40 s without positions, but B crosses the lap-3 sector lines in it.
        session = _drop_samples(fx.race_session(), "B", 1205.0, 1245.0)
        series = tower_series(session)

        assert series.value("B", "status", 1240.0) == ON_TRACK

    def test_a_car_that_stops_for_good_still_retires_once(self, race, race_series):
        from processing.replay_model import events

        retirements = [e for e in events(race, race_series) if e[1] == "out"]

        assert [label for _, _, label in retirements] == ["Retirement - A"]

    def test_no_out_under_a_red_flag(self):
        from processing.replay_model import tower_series

        session = fx.race_session()
        session["track_status"] = pd.DataFrame(
            {
                "Time": [fx.LIGHTS_OUT - 10.0, 1190.0, 1260.0],
                "Status": ["1", "5", "1"],
                "Message": ["AllClear", "Red", "AllClear"],
            }
        )
        # Positions stop for the whole suspension and no timing line is crossed.
        session = _drop_samples(session, "C", 1190.0, 1265.0)
        series = tower_series(session)

        assert series.value("C", "status", 1240.0) == ON_TRACK
        assert series.value("C", "status", 1262.0) == ON_TRACK


class TestRedFlagPitEntries:
    """REPLAY-25: entering the pit lane under a red flag is not a pit stop."""

    def test_pits_and_events_leave_out_red_flag_entries(self):
        from processing.replay_model import events, tower_series

        session = fx.race_session()
        red_at = fx.C_PIT_IN - 10.0
        session["track_status"] = pd.DataFrame(
            {
                "Time": [fx.LIGHTS_OUT - 10.0, red_at, fx.C_PIT_OUT + 60.0],
                "Status": ["1", "5", "1"],
                "Message": ["AllClear", "Red", "AllClear"],
            }
        )
        series = tower_series(session)

        assert series.value("C", "pits", fx.C_PIT_OUT + 1) == 0
        assert not [e for e in events(session, series) if e[1] == "pit"]

    def test_a_green_flag_stop_still_counts(self, race, race_series):
        assert race_series.value("C", "pits", fx.C_PIT_IN) == 1


class TestPitLaneStart:
    """REPLAY-27: a pit-lane starter waits IN PIT from lights out."""

    def test_a_pit_lane_starter_is_in_the_pits_at_the_start(self):
        from processing.replay_model import tower_series

        session = fx.race_session()
        laps = session["laps"].copy()
        first = (laps["Driver"] == "C") & (laps["LapNumber"] == 1)
        laps.loc[first, "PitOutTime"] = pd.Timedelta(fx.LIGHTS_OUT + 20.0, unit="s")
        session["laps"] = laps
        series = tower_series(session)

        assert series.value("C", "status", fx.LIGHTS_OUT + 5.0) == IN_PIT
        assert series.value("C", "status", fx.LIGHTS_OUT + 21.0) == ON_TRACK

    def test_grid_starters_are_on_track(self, race, race_series):
        assert race_series.value("A", "status", fx.LIGHTS_OUT + 5.0) == ON_TRACK
