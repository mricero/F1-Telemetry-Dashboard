"""Opt-in network integration tests.

These exercise the real FastF1/Jolpica endpoints and are skipped unless
``F1_NETWORK_TESTS=1`` is set (keeps the default suite offline/deterministic):

    F1_NETWORK_TESTS=1 .venv/Scripts/python -m pytest -m network
"""

import os
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("F1_NETWORK_TESTS") != "1", reason="opt-in: set F1_NETWORK_TESTS=1"
    ),
]


class TestJolpicaLive:
    def test_schedule_2024(self):
        from data.jolpica_adapter import JolpicaAdapter

        schedule = JolpicaAdapter().get_schedule(2024)
        assert not schedule.empty
        assert "Bahrain Grand Prix" in set(schedule["race_name"])


class TestFastF1Roundtrip:
    def test_available_sessions_2023(self):
        from data.fastf1_adapter import FastF1Adapter

        meetings = FastF1Adapter().get_available_sessions(2023)
        assert not meetings.empty
        assert "Bahrain Grand Prix" in set(meetings["EventName"])

    def test_load_cached_race_and_telemetry(self):
        from data.fastf1_adapter import FastF1Adapter

        adapter = FastF1Adapter()
        session = adapter.load_session(2023, "Bahrain Grand Prix", "R")
        drivers = session.results["Abbreviation"].tolist()
        assert len(drivers) >= 20
        tel = adapter.get_telemetry(session, drivers[0])
        for col in ("Distance", "Speed", "Throttle", "Brake", "RPM"):
            assert col in tel.columns

    def test_location_does_not_raise_on_real_position_data(self):
        """Regression: this raised ValueError and killed every session load.

        ``get_pos_data()`` has no Speed channel, so integrating distance over
        it fails. Only mocks that were not shaped like real FastF1 objects
        ever let this pass.
        """
        from data.fastf1_adapter import FastF1Adapter

        adapter = FastF1Adapter()
        session = adapter.load_session(2023, "Bahrain Grand Prix", "R")
        driver = session.results["Abbreviation"].tolist()[0]

        location = adapter.get_location(session, driver)

        assert not location.empty
        assert {"X", "Y"}.issubset(location.columns)

    def test_fastest_lap_scope_is_lap_relative(self):
        """Default scope must not hand the browser a whole race of points."""
        from data.fastf1_adapter import SCOPE_SESSION, FastF1Adapter

        adapter = FastF1Adapter()
        session = adapter.load_session(2023, "Bahrain Grand Prix", "R")
        driver = session.results["Abbreviation"].tolist()[0]

        one_lap = adapter.get_telemetry(session, driver)
        whole = adapter.get_telemetry(session, driver, scope=SCOPE_SESSION)

        assert one_lap["Distance"].max() < 12_000  # a lap, not a race
        assert whole["Distance"].max() > one_lap["Distance"].max() * 5

    def test_laps_carry_derived_pit_out_flag(self):
        from data.fastf1_adapter import FastF1Adapter

        adapter = FastF1Adapter()
        session = adapter.load_session(2023, "Bahrain Grand Prix", "R")

        laps = adapter.get_laps(session)

        assert "IsPitOutLap" in laps.columns
        assert laps["IsPitOutLap"].any(), "a race always has pit-out laps"


class TestAppSmoke:
    """The whole dashboard, executed the way Streamlit executes it.

    ``python app.py`` cannot catch layout/render regressions: without a script
    run context widgets return defaults and ``st.stop()`` is a no-op.
    """

    def test_app_runs_without_exceptions(self, tmp_path, monkeypatch):
        from streamlit.testing.v1 import AppTest

        # Keep the developer's real records file out of the test.
        monkeypatch.setenv("F1_METRICS_STORE", str(tmp_path / "metrics.json"))

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=600)
        app.run()

        assert not app.exception, [str(e.value) for e in app.exception]
        assert not app.error, [str(e.value) for e in app.error]

        # The timing dashboard renders through st.html, which AppTest does not
        # expose as an element - its markup is covered directly in
        # tests/test_track_map.py. Here we assert the analysis tabs below it.
        labels = [t.label for t in app.tabs]
        for expected in (
            "📊 Telemetry",
            "⚔️ Head-to-Head",
            "⏱️ Lap Times",
            "📈 Positions",
            "🛞 Tyres",
            "🗺️ Track",
            "🌤️ Weather",
            "🚩 Race Control",
        ):
            assert expected in labels, f"missing analysis tab {expected!r}"
        assert any(r.label == "Telemetry scope" for r in app.radio)

    def test_app_renders_panels_from_previously_unused_data(self, tmp_path, monkeypatch):
        """Weather, race control and lap Position were fetched then discarded.

        Each panel reports its own "no data" notice, so an empty render shows
        up here rather than passing silently.
        """
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("F1_METRICS_STORE", str(tmp_path / "metrics.json"))

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=600)
        app.run()

        notices = [i.value for i in app.info] + [w.value for w in app.warning]
        for absent in ("No weather data", "No race control messages", "No position data"):
            assert not any(absent in n for n in notices), f"{absent!r} - panel got no data"

        # Weather readings render as st.metric tiles.
        metric_labels = [m.label for m in app.metric]
        assert "🌡️ Air" in metric_labels and "🛣️ Track" in metric_labels


class TestDashboardOnRealSession:
    """The spec dashboard, built from a real loaded session.

    AppTest cannot see st.html output, so the markup is asserted here against
    genuine FastF1 data rather than through the app harness.
    """

    @staticmethod
    def _session():
        from data.source_manager import DataSourceManager

        return DataSourceManager().get_session_data(
            source="fastf1", year=2023, gp="Bahrain Grand Prix", session_type="Q"
        )

    def test_timing_rows_are_ranked_and_complete(self):
        """Qualifying: the official order, which is by segment then by time.

        It is *not* globally fastest-to-slowest: a driver who reaches Q3 and
        sets no lap there keeps a Q2 time that can beat a Q3 time (2023
        Bahrain, HUL P10). Races are ordered by finishing position instead -
        see ``TestRaceClassificationOnRealSession``.
        """
        from processing.timing import build_timing_rows

        session = self._session()
        rows = build_timing_rows(session)
        official = session["results"].sort_values("Position")["Abbreviation"].astype(str).tolist()

        assert len(rows) >= 15
        assert [r["code"] for r in rows] == official

        # Drivers who actually set a Q3 lap are in ascending Q3 order; one who
        # reached Q3 without setting a time keeps a (possibly faster) Q2 lap
        # and still classifies behind them.
        from processing.time_utils import to_seconds

        q3 = session["results"].set_index("Abbreviation")["Q3"]
        set_a_q3_lap = [
            to_seconds(q3.get(r["code"]))
            for r in rows[:10]
            if r["code"] in q3.index and pd.notna(q3.get(r["code"]))
        ]
        assert set_a_q3_lap == sorted(set_a_q3_lap)
        assert rows[0]["gap"] == "----" and rows[0]["is_overall_best"]
        assert rows[1]["gap"].startswith("+")
        # Every timed row carries a team, a speed trap reading and tyre history.
        leader = rows[0]
        assert leader["team_name"] and leader["speed_kmh"] > 100
        assert leader["tyre_history"]

    def test_qualifying_partitions_follow_the_segments(self):
        """DASH-02: 20 cars in 2023 -> 10 / 5 / 5, with named headings."""
        from processing.timing import build_timing_rows

        rows = build_timing_rows(self._session())
        segments = [r["segment"] for r in rows]

        assert [segments.count(s) for s in ("Q3", "Q2", "Q1")] == [10, 5, 5]
        assert [r["partition"] for r in rows if r.get("partition")] == [
            "Q3",
            "Eliminated in Q2",
            "Eliminated in Q1",
        ]
        assert not any(r["knocked_out"] for r in rows[:10])

    def test_track_map_svg_is_wellformed_with_corners(self):
        import xml.etree.ElementTree as ET

        from processing.timing import build_timing_rows
        from ui.dashboard import map_panel_html
        from ui.track_map import build_track_svg

        session = self._session()
        rows = build_timing_rows(session)
        svg = build_track_svg(
            session["location"],
            circuit_info=session.get("circuit_info"),
            driver_meta={r["code"]: r for r in rows},
        )

        assert svg is not None, "GPS telemetry should yield a track map"
        ET.fromstring(svg)  # raises if the SVG is malformed
        corners = session.get("circuit_info", {}).get("corners")
        if corners is not None and len(corners):
            assert svg.count("<circle") == len(corners)

        panel = map_panel_html(session, rows)
        assert "f1-map-wrap" in panel and "Session best" in panel

    def test_micro_sectors_cover_every_driver_with_telemetry(self):
        from processing.timing import TOTAL_SEGMENTS, build_timing_rows

        session = self._session()
        rows = build_timing_rows(session)

        strips = [
            state for row in rows for sector in row["sectors"] for state in sector["segments"]
        ]
        assert len(strips) == len(rows) * TOTAL_SEGMENTS
        # A real session has an outright fastest driver in at least one slice.
        assert "PURPLE" in strips


class TestRaceClassificationOnRealSession:
    """DASH-01 acceptance, against the real 2023 Bahrain Grand Prix."""

    @staticmethod
    def _race():
        from data.source_manager import DataSourceManager

        return DataSourceManager().get_session_data(
            source="fastf1", year=2023, gp="Bahrain Grand Prix", session_type="R"
        )

    def test_podium_matches_the_official_result(self):
        from processing.timing import build_timing_rows

        rows = build_timing_rows(self._race())

        assert [r["code"] for r in rows[:3]] == ["VER", "PER", "ALO"]

    def test_gaps_match_the_official_times(self):
        from processing.time_utils import to_seconds
        from processing.timing import build_timing_rows

        session = self._race()
        rows = build_timing_rows(session)
        results = session["results"].set_index("Abbreviation")

        assert rows[0]["gap"] == "----"
        for row in rows[1:4]:
            official = to_seconds(results.loc[row["code"], "Time"])
            assert abs(float(row["gap"].lstrip("+")) - official) < 0.1

    def test_the_fastest_lap_setter_is_not_necessarily_first(self):
        from processing.timing import build_timing_rows

        rows = build_timing_rows(self._race())
        fastest = min(
            (r for r in rows if r["best_seconds"] is not None), key=lambda r: r["best_seconds"]
        )

        assert rows[0]["is_overall_best"] is (rows[0] is fastest)
        assert fastest["is_overall_best"]

    def test_the_unified_dict_carries_official_results(self):
        session = self._race()

        results = session["results"]
        assert not results.empty
        assert {"Abbreviation", "Position", "Status"} <= set(results.columns)


class TestScopeIndependenceOfTheDashboard:
    """DASH-05 acceptance: the dashboard is scope-invariant on a real race."""

    @staticmethod
    def _race(scope: str):
        from data.source_manager import DataSourceManager

        return DataSourceManager().get_session_data(
            source="fastf1",
            year=2023,
            gp="Bahrain Grand Prix",
            session_type="R",
            telemetry_scope=scope,
        )

    def test_same_dominance_and_micro_sectors_in_both_scopes(self):
        from processing.timing import build_timing_rows, dashboard_frames, micro_sector_times
        from ui.track_map import dominance_segments

        def fingerprint(session):
            telemetry, _ = dashboard_frames(session)
            micro = {
                code: times
                for code, frame in telemetry.items()
                if (times := micro_sector_times(frame)) is not None
            }
            rows = build_timing_rows(session)
            return dominance_segments(micro), [r["sectors"][0]["segments"] for r in rows]

        assert fingerprint(self._race("fastest")) == fingerprint(self._race("session"))

    def test_full_session_scope_still_yields_a_small_svg(self):
        from processing.timing import build_timing_rows
        from ui.dashboard import map_panel_html

        session = self._race("session")
        markup = map_panel_html(session, build_timing_rows(session))

        assert len(markup.encode("utf-8")) < 150 * 1024


class TestStatusBadgesOnRealSession:
    """DASH-10: DNFs must not be presented as classified finishes."""

    def test_retirements_are_badged_on_the_2023_bahrain_race(self):
        from data.source_manager import DataSourceManager
        from processing.timing import build_timing_rows

        session = DataSourceManager().get_session_data(
            source="fastf1", year=2023, gp="Bahrain Grand Prix", session_type="R"
        )
        rows = build_timing_rows(session)
        badges = {r["status"] for r in rows}

        assert "FIN" in badges
        assert "CLASSIFIED" not in badges
        # Three cars retired at Bahrain 2023 (LEC, STR's team mate aside).
        assert "DNF" in badges


class TestReplayTimelineOnRealSession:
    """FEAT-04: the playback timeline, built from a real race."""

    @staticmethod
    def _race():
        from data.source_manager import DataSourceManager

        return DataSourceManager().get_session_data(
            source="fastf1", year=2023, gp="Bahrain Grand Prix", session_type="R"
        )

    def test_the_session_carries_a_position_timeline(self):
        positions = self._race()["positions"]

        assert not positions.empty
        assert list(positions.columns) == ["Time", "Driver", "X", "Y"]
        assert positions["Driver"].nunique() >= 15

    def test_it_spans_the_whole_race(self):
        from processing.replay import timeline_bounds

        start, end = timeline_bounds(self._race()["positions"])

        # Bahrain 2023 ran about 1 h 33 m; the stream covers rather more.
        assert (end - start) > 3600

    def test_the_grid_moves_between_moments(self):
        from processing.replay import positions_at, timeline_bounds

        positions = self._race()["positions"]
        # FastF1's SessionTime counts from the start of the recording, not
        # from zero, so moments are taken relative to the timeline itself.
        start, end = timeline_bounds(positions)
        middle = start + (end - start) / 2

        early = {m["code"]: (m["x"], m["y"]) for m in positions_at(positions, middle)}
        later = {m["code"]: (m["x"], m["y"]) for m in positions_at(positions, middle + 30)}

        shared = set(early) & set(later)
        assert shared, "no driver present at both moments"
        assert any(early[code] != later[code] for code in shared), "cars did not move"

    def test_the_order_at_a_moment_matches_the_laps(self):
        from processing.replay import lap_at, order_at, timeline_bounds

        session = self._race()
        start, end = timeline_bounds(session["positions"])
        middle = start + (end - start) / 2

        order = order_at(session["laps"], middle)

        assert len(order) >= 15
        assert order[0]["position"] == 1
        assert lap_at(session["laps"], middle) > 1


_SESSIONS: dict = {}


def _bahrain(session_type: str) -> dict:
    """2023 Bahrain, loaded once per test run (FastF1 caches it on disk)."""
    if session_type not in _SESSIONS:
        from data.source_manager import DataSourceManager

        _SESSIONS[session_type] = DataSourceManager().get_session_data(
            source="fastf1", year=2023, gp="Bahrain Grand Prix", session_type=session_type
        )
    return _SESSIONS[session_type]


class TestReplayStreamsOnRealSessions:
    """REPLAY-02: the timing stream, track status and segment starts."""

    def test_the_race_stream_covers_the_grid(self):
        race = _bahrain("R")
        stream = race["timing_stream"]

        assert stream["Driver"].nunique() >= 20
        assert len(stream) >= 20_000
        assert stream[stream["Driver"] == "VER"]["Position"].iloc[-1] == 1
        assert race["session_info"]["segment_starts"] == []
        assert not race["track_status"].empty

    def test_qualifying_has_three_segments(self):
        info = _bahrain("Q")["session_info"]
        starts = info["segment_starts"]

        assert len(starts) == 3
        assert starts == sorted(starts) and len(set(starts)) == 3
        assert starts[0] == info["session_start"]


class TestReplayModelOnARealRace:
    """REPLAY-03 acceptance 9: the model agrees with FastF1's own lap data."""

    def test_the_tower_after_lap_ten(self):
        from processing.replay_model import snapshot_at, tower_series
        from processing.timing import build_timing_rows

        race = _bahrain("R")
        laps = race["laps"]
        tenth = laps[laps["LapNumber"] == 10].copy()
        tenth["end"] = tenth["Time"].dt.total_seconds()
        leader_end = float(tenth["end"].min())

        rows = build_timing_rows(snapshot_at(race, leader_end + 1, tower_series(race)))
        expected = tenth.sort_values("Position")["Driver"].head(3).tolist()

        assert [row["code"] for row in rows[:3]] == expected
        ver = tenth.set_index("Driver").loc["VER", "end"]
        per = tenth.set_index("Driver").loc["PER", "end"]
        gap = float(next(row["gap"] for row in rows if row["code"] == "PER").lstrip("+"))
        assert abs(gap - (per - ver)) < 0.5
