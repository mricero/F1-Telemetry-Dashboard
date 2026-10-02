"""The browser player's payload (IMPROVEMENTS.md REPLAY-05).

The player only looks values up in this payload, so it must say exactly
what the server-side model says: the tower read from the payload (a Python
mirror of the JavaScript binary search) is compared with ``snapshot_at`` at
random moments, and the decoded car positions with the map's projection.
"""

import json
import re

import numpy as np
import pandas as pd
import pytest

from processing.replay_model import session_clock, snapshot_at, tower_series
from processing.replay_payload import (
    POSITION_ABSENT,
    build_replay_payload,
    decode_positions,
    tower_at,
)
from processing.timing import build_timing_rows
from processing.track_geometry import track_geometry
from tests import replay_fixtures as fx

REQUIRED = {
    "v": int,
    "session_key": str,
    "session": dict,
    "clock": dict,
    "track": dict,
    "drivers": list,
    "pos": dict,
    "tower": dict,
    "defaults": dict,
    "flags": list,
    "rcm": list,
    "weather": list,
    "leader_laps": list,
    "events": list,
    "segments": list,
    "estimated": bool,
}


def _payload(session):
    series = tower_series(session)
    return build_replay_payload(session, series, session_clock(session), "key"), series


@pytest.fixture(scope="module")
def race():
    return fx.race_session()


@pytest.fixture(scope="module")
def built(race):
    return _payload(race)


class TestShape:
    def test_required_keys_and_types(self, built):
        payload, _ = built

        for key, kind in REQUIRED.items():
            assert isinstance(payload[key], kind), key

    def test_it_is_strict_json(self, built):
        payload, _ = built

        json.dumps(payload, allow_nan=False)  # NaN would break JSON.parse

    def test_the_clock_is_the_replay_clock(self, built, race):
        payload, _ = built
        clock = session_clock(race)

        assert payload["clock"]["lights_out"] == clock.lights_out
        assert payload["clock"]["total_laps"] == 5

    def test_flags_use_the_flag_state_keys(self, built):
        payload, _ = built

        states = [state for _, state in payload["flags"]]
        assert states == ["GREEN", "SAFETY CAR", "GREEN", "CHEQUERED"]


class TestPositions:
    def test_decoding_reproduces_the_projected_positions(self, built, race):
        payload, _ = built
        pos = payload["pos"]
        xy = decode_positions(pos)
        geometry = track_geometry(race["location"], race["circuit_info"])
        timeline = race["positions"]

        for code in ("A", "C"):
            index = pos["codes"].index(code)
            own = timeline[timeline["Driver"] == code].iloc[::40]
            frames = np.rint((own["Time"].to_numpy() - pos["t0"]) / pos["step"]).astype(int)
            expected = geometry.project(own[["X", "Y"]].to_numpy())
            assert np.nanmax(np.abs(xy[frames, index] - expected)) <= 0.1

    def test_a_car_without_a_sample_is_marked_absent(self, built):
        payload, _ = built
        xy = decode_positions(payload["pos"])
        a = payload["pos"]["codes"].index("A")
        after_a_stopped = int((fx.RACE_END_BY["A"] + 20 - payload["pos"]["t0"]) / 0.5)

        assert np.isnan(xy[after_a_stopped, a]).all()
        assert payload["pos"]["absent"] == POSITION_ABSENT


class TestTheTowerMatchesTheModel:
    @pytest.mark.parametrize("seed", range(50))
    def test_at_a_random_moment(self, race, built, seed):
        payload, series = built
        clock = session_clock(race)
        moment = float(np.random.default_rng(seed).uniform(clock.start, clock.end))

        from_payload = tower_at(payload, moment)
        rows = build_timing_rows(snapshot_at(race, moment, series))

        assert [row["code"] for row in from_payload] == [row["code"] for row in rows]
        for mirror, row in zip(from_payload, rows, strict=True):
            assert mirror["gap"] == row["gap"]
            assert mirror["int"] == row["interval"]
            assert mirror["status"] == row["status"]
            current = row["tyre_history"][-1]["compound"] if row["tyre_history"] else None
            assert mirror["tyre"] == current

    def test_qualifying_too(self):
        session = fx.qualifying_session()
        payload, series = _payload(session)

        for moment in (fx.Q_STARTS[1] + 1, fx.Q2_LATE_LAP_END + 1, fx.Q_STARTS[2] + 1):
            order = [row["code"] for row in tower_at(payload, moment)]
            rows = build_timing_rows(snapshot_at(session, moment, series))
            assert order == [row["code"] for row in rows]


class TestBudget:
    def test_a_two_hour_22_car_race_fits_in_four_megabytes(self):
        from tests.test_replay_model import _big_race

        session = _big_race()
        angle = np.linspace(0, 2 * np.pi, 300)
        session["location"] = {
            "D00": pd.DataFrame({"X": np.cos(angle) * 5000, "Y": np.sin(angle) * 3000})
        }
        grid = np.arange(3600.0, 3600.0 + 7200.0, 0.5)
        codes = [f"D{index:02d}" for index in range(22)]
        session["positions"] = pd.DataFrame(
            {
                "Time": np.tile(grid, len(codes)),
                "Driver": np.repeat(codes, len(grid)),
                "X": np.tile(np.cos(grid / 90) * 5000, len(codes)),
                "Y": np.tile(np.sin(grid / 90) * 3000, len(codes)),
            }
        )
        session["session_info"]["replay_clock"] = {
            "start": 3600.0,
            "lights_out": 3600.0,
            "end": 3600.0 + 7200.0,
            "step": 0.5,
        }
        payload, _ = _payload(session)

        size = len(json.dumps(payload, allow_nan=False))
        assert payload["pos"]["frames"] >= 14_000
        assert size <= 4 * 1024 * 1024, f"{size / 1e6:.2f} MB"


class TestTheComponentFiles:
    """Guideline 5.12 runs over the component too; these pin its contract."""

    def test_the_tokens_are_defined_once_in_root(self):
        from ui.components.replay_player import component_source

        css = component_source()["css"]
        blocks = re.findall(r":root[^{]*\{([^}]*)\}", css)

        assert len(blocks) == 1
        for token in ("--bg", "--surface", "--surface-2", "--line", "--text", "--text-dim"):
            assert f"{token}:" in blocks[0]
        assert "--accent:" in blocks[0]

    def test_the_three_breakpoints_exist(self):
        from ui.components.replay_player import component_source

        css = component_source()["css"]

        assert "max-width: 1199px" in css
        assert "max-width: 899px" in css
        assert "prefers-reduced-motion" in css

    def test_the_player_never_builds_markup_from_strings(self):
        """Feed strings must not reach innerHTML unescaped."""
        from ui.components.replay_player import component_source

        assert "innerHTML" not in component_source()["js"]


class TestTrackState:
    """REPLAY-07: the safety car period is on the flag timeline the map tints from."""

    def test_the_safety_car_period_is_a_safety_car_state(self, built):
        payload, _ = built
        flags = payload["flags"]

        def state_at(moment):
            current = "GREEN"
            for t, state in flags:
                if t <= moment:
                    current = state
            return current

        assert state_at(fx.SC_START - 1) == "GREEN"
        assert state_at(fx.SC_START + 1) == "SAFETY CAR"
        assert state_at(fx.SC_END - 1) == "SAFETY CAR"
        assert state_at(fx.SC_END + 1) == "GREEN"

    def test_the_player_tints_the_track_and_names_the_state(self):
        from ui.components.replay_player import component_source

        js = component_source()["js"]

        assert (
            "this.tint" in js and 'MAP_CHIPS = { "SAFETY CAR": "SC", VSC: "VSC", RED: "RED" }' in js
        )

    def test_the_server_map_shows_the_chip(self, race):
        from processing.replay_model import snapshot_at
        from ui.dashboard import map_panel_html

        snapshot = snapshot_at(race, fx.SC_START + 5)
        markup = map_panel_html(snapshot, build_timing_rows(snapshot))

        assert ">SC</span>" in markup


class TestFocusedDriverCard:
    """REPLAY-10: what the player's driver card reads."""

    def test_every_completed_lap_is_listed_with_its_flag(self, built):
        payload, _ = built
        laps = payload["laps"]["B"]

        assert [lap[1] for lap in laps] == [1, 2, 3, 4, 5]
        assert laps[0][2] == "1:31.000"
        assert all(lap[3] in (None, "sb", "pb") for lap in laps)

    def test_the_interval_trend_is_sampled_every_five_seconds(self, built, race):
        payload, series = built
        from processing.replay_payload import decode_trend

        trend = payload["trend"]
        values = decode_trend(trend)["C"]  # packed since REPLAY-29
        index = int((1200.0 - trend["t0"]) // trend["step"])

        assert trend["step"] == 5.0
        assert len(values) == trend["samples"]
        assert values[index] == pytest.approx(
            series.value("C", "interval_s", trend["t0"] + index * 5.0), abs=0.005
        )

    def test_the_card_is_in_the_player(self):
        from ui.components.replay_player import component_source

        js = component_source()["js"]

        assert "drawCard" in js and "Analyse this lap" in js


def test_analyse_this_lap_opens_the_lap_chart():
    from streamlit.testing.v1 import AppTest

    def script():
        import streamlit as st

        from ui.replay_view import _analyse_from_player, wants_analysis

        st.session_state["replay_player:k"] = {"cursor": 1.0, "focus": "A", "analyse": 3}
        _analyse_from_player("k")
        st.session_state["asked"] = wants_analysis("k")
        st.session_state["again"] = wants_analysis("k")

    app = AppTest.from_function(script, default_timeout=30)
    app.run()

    assert app.session_state["asked"] == 3
    assert app.session_state["again"] is None  # asked once, then forgotten
    assert app.session_state["analysis_section"] == "Lap times"


class TestCompactEncoding:
    """REPLAY-29: positions and the interval trend are packed, losslessly
    after quantisation, and the JavaScript decoder reads the same bytes."""

    def test_positions_round_trip_through_the_packing(self):
        from processing.replay_payload import _pack_positions

        rng = np.random.default_rng(7)
        frames, drivers = 500, 4
        walk = np.cumsum(rng.integers(-40, 41, size=(frames, drivers, 2)), axis=0) + 2500
        walk[100] = [[0, 0], [5000, 3800], [-6000, 6000], [6000, -6000]]  # jumps
        absent = rng.random((frames, drivers)) < 0.1
        absent[:20, 1] = True  # absent from the start
        absent[-30:, 2] = True  # and to the end
        pos = {"frames": frames, "drivers": drivers, "scale": 5}
        pos["xy_z"] = _pack_positions(walk, absent)

        decoded = decode_positions(pos)

        assert np.isnan(decoded[absent]).all()
        assert np.array_equal(decoded[~absent] * 5, walk[~absent].astype(float))

    def test_quantisation_is_within_a_tenth_of_a_unit(self, built, race):
        payload, _ = built

        assert payload["pos"]["scale"] >= 5  # 0.5 / scale <= 0.1 viewBox units
        assert payload["pos"]["encoding"] == "dd-zigzag-shuffle-deflate"

    def test_a_smooth_race_packs_to_well_under_half(self):
        from tests.test_replay_model import _big_race

        session = _big_race()
        angle = np.linspace(0, 2 * np.pi, 300)
        session["location"] = {
            "D00": pd.DataFrame({"X": np.cos(angle) * 5000, "Y": np.sin(angle) * 3000})
        }
        grid = np.arange(3600.0, 9200.0, 0.5)
        codes = [f"D{index:02d}" for index in range(22)]
        phase = np.repeat(np.arange(22) * 0.05, len(grid))
        session["positions"] = pd.DataFrame(
            {
                "Time": np.tile(grid, len(codes)),
                "Driver": np.repeat(codes, len(grid)),
                "X": np.cos(np.tile(grid, len(codes)) / 14 + phase) * 5000,
                "Y": np.sin(np.tile(grid, len(codes)) / 14 + phase) * 3000,
            }
        )
        payload, _ = _payload(session)
        pos = payload["pos"]
        int16_base64 = 4 * pos["frames"] * pos["drivers"] * 4 / 3  # the old xy_b64

        assert len(pos["xy_z"]) < 0.35 * int16_base64
        assert len(json.dumps(payload["trend"])) < 20_000

    def test_the_trend_marks_a_missing_interval(self):
        from processing.replay import ReplayClock
        from processing.replay_model import _series
        from processing.replay_payload import _interval_trend, decode_trend

        class Series:
            drivers = ["A", "B"]  # noqa: RUF012
            fields = {  # noqa: RUF012
                "A": {"interval_s": _series([(0.0, None), (10.0, 1.234), (20.0, None)])},
                "B": {"interval_s": _series([(0.0, 700.0)])},
            }

        clock = ReplayClock(start=0.0, lights_out=0.0, end=25.0, step=0.5)
        trend = _interval_trend(Series(), clock)
        values = decode_trend(trend)

        assert trend["samples"] == 6
        assert values["A"] == [None, None, 1.23, 1.23, None, None]
        assert values["B"][0] == 655.34  # clipped, never mistaken for missing


class TestTheLeaderIsNotClose:
    """REPLAY-21: the leader's "LAP n" interval cell is not a 0.000 s gap."""

    def test_the_leader_is_never_close_and_has_no_trend_value(self, race, built):
        from processing.replay_payload import decode_trend

        payload, series = built
        trend = decode_trend(payload["trend"])
        clock = session_clock(race)
        for code in series.drivers:
            fields = series.fields[code]
            for t in np.arange(clock.lights_out, clock.end, 5.0):
                if fields["position"].at(float(t)) != 1:
                    continue
                assert fields["interval_s"].at(float(t)) is None, (code, t)
                close = payload["tower"][code].get("close")
                if close:
                    index = np.searchsorted(close[0], t, side="right") - 1
                    assert index < 0 or not close[1][index], (code, t)
                sample = int((t - payload["trend"]["t0"]) // payload["trend"]["step"])
                if code in trend:
                    assert trend[code][sample] is None, (code, t)
