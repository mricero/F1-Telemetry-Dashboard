"""Tests for the SVG track map and the dashboard HTML builders."""

import itertools
import re

import numpy as np
import pandas as pd
import pytest

from ui.dashboard import header_html, sector_cards_html, tower_html
from ui.theme import team_color
from ui.track_map import (
    DOMINANCE_SEGMENTS,
    VIEW_H,
    VIEW_W,
    build_track_svg,
    dominance_legend,
    dominance_segments,
    rotate_points,
)


def _oval(points: int = 200) -> pd.DataFrame:
    """A closed loop standing in for a circuit outline."""
    angle = np.linspace(0, 2 * np.pi, points)
    return pd.DataFrame({"X": np.cos(angle) * 1000, "Y": np.sin(angle) * 600})


@pytest.fixture
def location():
    return {"VER": _oval()}


class TestRotation:
    def test_zero_angle_is_identity(self):
        points = np.array([[1.0, 0.0], [0.0, 1.0]])

        assert np.allclose(rotate_points(points, 0.0), points)

    def test_ninety_degrees(self):
        # FastF1's convention: xy @ [[cos, sin], [-sin, cos]] turns the point
        # counter-clockwise, so +x maps onto +y.
        rotated = rotate_points(np.array([[1.0, 0.0]]), 90.0)

        assert rotated[0] == pytest.approx([0.0, 1.0], abs=1e-9)

    def test_rotation_preserves_length(self):
        assert np.linalg.norm(rotate_points(np.array([[3.0, 4.0]]), 44.0)) == pytest.approx(5.0)

    def test_none_angle_treated_as_zero(self):
        points = np.array([[2.0, 5.0]])

        assert np.allclose(rotate_points(points, None), points)


class TestTrackSvg:
    def test_renders_svg_with_viewbox(self, location):
        svg = build_track_svg(location)

        assert svg.startswith("<svg")
        assert f'viewBox="0 0 {VIEW_W} {VIEW_H}"' in svg
        assert "</svg>" in svg

    def test_returns_none_without_gps(self):
        assert build_track_svg({}) is None
        assert build_track_svg({"VER": pd.DataFrame()}) is None

    def test_ignores_frames_missing_coordinates(self):
        assert build_track_svg({"VER": pd.DataFrame({"Speed": [1, 2, 3]})}) is None

    def test_corner_markers_are_drawn_and_labelled(self, location):
        corners = pd.DataFrame(
            {"X": [1000.0, -1000.0], "Y": [0.0, 0.0], "Number": [1, 2], "Letter": ["", ""]}
        )

        svg = build_track_svg(location, circuit_info={"corners": corners, "rotation": 0.0})

        assert svg.count('class="corner"') == 2  # numbers, no circles
        assert ">1<" in svg and ">2<" in svg

    def test_dominance_adds_one_path_per_segment(self, location):
        dominance = ["VER"] * DOMINANCE_SEGMENTS
        meta = {"VER": {"team_name": "Red Bull Racing", "team_colour": "#3671c6"}}

        plain = build_track_svg(location)
        tinted = build_track_svg(location, driver_meta=meta, dominance=dominance)

        assert tinted.count("<path") == plain.count("<path") + DOMINANCE_SEGMENTS
        assert "#3671c6" in tinted

    def test_driver_markers_are_placed(self, location):
        markers = [{"code": "VER", "x": 1000.0, "y": 0.0, "team_colour": "#3671c6"}]

        assert ">VER<" in build_track_svg(location, markers=markers)

    def test_marker_labels_are_escaped(self, location):
        svg = build_track_svg(
            location, markers=[{"code": "<script>", "x": 0.0, "y": 0.0, "team_colour": "#fff"}]
        )

        assert "<script>" not in svg and "&lt;script&gt;" in svg

    def test_geometry_stays_inside_the_viewport(self, location):
        svg = build_track_svg(location)
        coords = [float(v) for v in re.findall(r"L (-?\d+\.\d+)", svg)]

        assert coords and min(coords) >= 0 and max(coords) <= VIEW_W


class TestDominance:
    def test_picks_fastest_driver_per_segment(self):
        micro = {
            "VER": np.array([1.0, 3.0] + [2.0] * 13),
            "HAM": np.array([2.0, 1.0] + [2.0] * 13),
        }

        winners = dominance_segments(micro)

        assert winners[0] == "VER"
        assert winners[1] == "HAM"

    def test_empty_input_yields_no_winners(self):
        assert dominance_segments({}) == [None] * DOMINANCE_SEGMENTS

    def test_mismatched_segment_count_is_rejected(self):
        assert dominance_segments({"VER": np.ones(4)}) == [None] * DOMINANCE_SEGMENTS

    def test_legend_counts_segments_per_driver(self):
        legend = dominance_legend(["VER", "VER", "HAM"], {"VER": {}, "HAM": {}})

        assert "VER" in legend and "HAM" in legend and "<b>2</b>" in legend

    def test_legend_empty_without_winners(self):
        assert dominance_legend([None, None]) == ""


class TestTeamColor:
    def test_session_colour_wins(self):
        assert team_color("McLaren", "#abcdef") == "#abcdef"

    def test_bare_hex_gets_a_hash(self):
        assert team_color("McLaren", "ff8000") == "#ff8000"

    def test_falls_back_to_known_team(self):
        assert team_color("Ferrari") == "#e80020"

    def test_unknown_team_is_grey(self):
        assert team_color("Nonexistent Racing") == "#8a8a8a"

    def test_nan_fallback_ignored(self):
        assert team_color("Ferrari", "nan") == "#e80020"


def _row(code="VER", position=1, **overrides):
    row = {
        "code": code,
        "position": position,
        "team_name": "Red Bull Racing",
        "team_colour": "#3671c6",
        "status": "CLASSIFIED",
        "best_lap": "1:30.500",
        "last_lap": "1:30.500",
        "gap": "----",
        "interval": "----",
        "diff": "+0.000",
        "speed_kmh": 310.0,
        "is_overall_best": True,
        "knocked_out": False,
        "tyre_history": [{"compound": "SOFT", "laps_used": 3}],
        "sectors": [
            {"seconds": 30.0, "display": "30.000", "segments": ["PURPLE"] * 5} for _ in range(3)
        ],
    }
    row.update(overrides)
    return row


class TestTowerHtml:
    def test_renders_a_row_per_driver(self):
        html = tower_html([_row("VER", 1), _row("HAM", 2, is_overall_best=False)])

        assert ">VER<" in html and ">HAM<" in html
        assert html.count('class="f1-pos"') == 2

    def test_marks_the_session_best_lap(self):
        assert 'class="f1-time best' in tower_html([_row()])

    def test_micro_sector_strip_has_five_cells_per_sector(self):
        assert tower_html([_row()]).count('<span style="background') == 15

    def test_knocked_out_partition_is_announced(self):
        html = tower_html(
            [
                _row("VER", 1),
                _row("HAM", 11, knocked_out=True, partition="Eliminated in Q2"),
            ]
        )

        assert "Eliminated in Q2" in html and 'class="ko"' in html

    def test_empty_rows_show_a_notice(self):
        assert "No timing data" in tower_html([])

    def test_driver_code_is_escaped(self):
        html = tower_html([_row(code="<img>")])

        assert "<img>" not in html and "&lt;img&gt;" in html


class TestHeaderAndSectors:
    def test_header_shows_event_and_weather(self):
        weather = pd.DataFrame(
            {
                "Time": pd.to_timedelta([0, 3600], unit="s"),
                "AirTemp": [17.5, 18.0],
                "TrackTemp": [29.9, 30.2],
                "Humidity": [59.7, 58.0],
                "Pressure": [1021.2, 1021.0],
                "WindSpeed": [6.5, 6.0],
                "WindDirection": [90, 95],
                "Rainfall": [False, False],
            }
        )

        html = header_html(
            {
                "session_info": {"gp": "Dutch GP", "year": 2026, "session_name": "Qualifying"},
                "weather": weather,
                "is_live": False,
            }
        )

        assert "Dutch GP" in html and "Qualifying" in html
        assert "30.2 &deg;C" in html  # latest track temp
        # FastF1 WindSpeed is m/s; 6.0 m/s = 21.6 km/h (DASH-07).
        assert "21.6 km/h E" in html
        # The clock slot is the session duration, and without laps there is
        # nothing to measure - the weather window is not a session clock.
        assert "\u2013" in html and "Duration" in html

    def test_header_without_weather_uses_placeholders(self):
        html = header_html({"session_info": {"gp": "Test GP"}, "is_live": False})

        assert "\u2013" in html and "ENDED" in html

    def test_header_flags_rain(self):
        weather = pd.DataFrame({"Time": pd.to_timedelta([0], unit="s"), "Rainfall": [True]})

        html = header_html({"session_info": {}, "weather": weather, "is_live": False})

        assert "rain-yes" in html and ">YES<" in html

    def test_sector_cards_render_three_columns(self):
        leaders = [
            [
                {
                    "rank": 1,
                    "code": "RUS",
                    "team_colour": "#00d2be",
                    "team_name": "Mercedes",
                    "time": "24.300",
                }
            ],
            [],
            [],
        ]

        html = sector_cards_html(leaders)

        assert html.count("f1-sector-card") == 3
        assert "Sector 1" in html and "24.300" in html
        assert "No data" in html  # empty sectors still render a card


class TestWindUnits:
    """DASH-07: FastF1 reports WindSpeed in m/s; the header labels it km/h."""

    def test_ms_to_kmh_conversion(self):
        from ui.dashboard import wind_kmh

        assert wind_kmh(0.0) == pytest.approx(0.0)
        assert wind_kmh(1.0) == pytest.approx(3.6)
        assert wind_kmh(6.0) == pytest.approx(21.6)

    def test_missing_wind_speed_is_none(self):
        from ui.dashboard import wind_kmh

        assert wind_kmh(None) is None
        assert wind_kmh(float("nan")) is None

    def test_header_converts_before_labelling_kmh(self):
        weather = pd.DataFrame(
            {
                "Time": pd.to_timedelta([0], unit="s"),
                "WindSpeed": [10.0],  # m/s
                "WindDirection": [180],
            }
        )

        html = header_html({"session_info": {}, "weather": weather, "is_live": False})

        assert "36.0 km/h S" in html
        assert "10.0 km/h" not in html


class TestColourSanitising:
    """REPO-12: feed/replay colours land unescaped in style= and SVG stroke=."""

    @staticmethod
    def _safe_hex():
        from ui.theme import safe_hex

        return safe_hex

    def test_accepts_six_digit_hex_with_and_without_hash(self):
        safe_hex = self._safe_hex()
        assert safe_hex("00d2be") == "#00d2be"
        assert safe_hex("#00D2BE") == "#00D2BE"

    def test_rejects_a_css_injection_payload(self):
        safe_hex = self._safe_hex()
        assert safe_hex("red;background:url(x)") == "#8a8a8a"
        assert safe_hex('" onload="alert(1)') == "#8a8a8a"
        assert safe_hex("</style><script>alert(1)</script>") == "#8a8a8a"

    def test_rejects_empty_and_missing_values(self):
        safe_hex = self._safe_hex()
        for value in (None, "", "   ", float("nan"), "nan", "None", 12345):
            assert safe_hex(value) == "#8a8a8a"

    def test_honours_an_explicit_fallback(self):
        safe_hex = self._safe_hex()
        assert safe_hex("not a colour", fallback="#123456") == "#123456"

    def test_team_color_sanitises_the_session_colour(self):
        assert team_color("Mercedes", "red;background:url(x)") == "#00d2be"
        assert team_color("Nonexistent Team", '"><script>') == "#8a8a8a"

    def test_tower_html_never_emits_a_raw_payload(self):
        rows = [
            {
                "position": 1,
                "code": "VER",
                "team_name": "Red Bull",
                "team_colour": '"><script>alert(1)</script>',
                "status": "CLASSIFIED",
                "last_lap": "1:31.2",
                "best_lap": "1:31.2",
                "interval": "—",
                "gap": "—",
                "diff": "—",
                "speed_kmh": 320.0,
                "sectors": [],
                "tyre_history": [],
            }
        ]

        markup = tower_html(rows)

        assert "<script>" not in markup
        assert "alert(1)" not in markup

    def test_track_map_marker_colour_is_sanitised(self, location):
        markers = [{"code": "VER", "x": 500.0, "y": 100.0, "team_colour": 'red" onload="x'}]

        svg = build_track_svg(location, markers=markers)

        assert 'onload="x' not in svg
        assert "#8a8a8a" in svg


class TestTowerPartitions:
    """DASH-02: the split row names the segment instead of 'top 10'."""

    @staticmethod
    def _row(position: int, **overrides) -> dict:
        row = {
            "position": position,
            "code": f"D{position:02d}",
            "team_name": "Team",
            "team_colour": "#3671c6",
            "status": "CLASSIFIED",
            "last_lap": "1:31.2",
            "best_lap": "1:31.2",
            "interval": "—",
            "gap": "—",
            "diff": "—",
            "speed_kmh": 320.0,
            "sectors": [],
            "tyre_history": [],
            "knocked_out": False,
        }
        row.update(overrides)
        return row

    def test_partition_heading_is_used_when_present(self):
        rows = [
            self._row(1),
            self._row(2, knocked_out=True, partition="Eliminated in Q2"),
            self._row(3, knocked_out=True),
        ]

        markup = tower_html(rows)

        assert "Eliminated in Q2" in markup
        assert "Outside the top" not in markup

    def test_no_split_row_without_partitions(self):
        markup = tower_html([self._row(1), self._row(2)])

        assert "f1-split" not in markup


class TestDominancePlacement:
    """DASH-04: dominance slices follow distance, not point index."""

    @staticmethod
    def _skewed_trace() -> pd.DataFrame:
        """A lap whose samples bunch up in its first tenth."""
        dense = np.linspace(0.0, 100.0, 180)
        sparse = np.linspace(100.0, 1000.0, 20)
        distance = np.concatenate([dense, sparse])
        return pd.DataFrame(
            {
                "Distance": distance,
                "X": distance,  # a straight line, so path length == distance
                "Y": np.zeros(len(distance)),
            }
        )

    @staticmethod
    def _path_lengths(svg: str, colour: str) -> float:
        """Total length of the polyline drawn in `colour`."""
        total = 0.0
        for match in re.finditer(r'<path d="([^"]+)" fill="none" stroke="([^"]+)"', svg):
            data, stroke = match.groups()
            if stroke.lower() != colour.lower():
                continue
            points = [
                (float(x), float(y))
                for x, y in re.findall(r"[ML] (-?\d+\.?\d*) (-?\d+\.?\d*)", data)
            ]
            total += sum(
                float(np.hypot(b[0] - a[0], b[1] - a[1])) for a, b in itertools.pairwise(points)
            )
        return total

    def test_first_slice_covers_its_share_of_the_track(self):
        location = {"VER": self._skewed_trace()}
        dominance = ["VER"] + [None] * 9
        meta = {"VER": {"team_name": "Red Bull", "team_colour": "#3671c6"}}

        svg = build_track_svg(
            location, driver_meta=meta, dominance=dominance, circuit_info={"rotation": 0}
        )

        first_slice = self._path_lengths(svg, "#3671c6")
        outline = self._path_lengths(svg, "#07080a")
        # 90 % of the samples sit in the first 10 % of the lap; the slice must
        # follow the distance, so it covers about a tenth of the path.
        assert 0.05 < first_slice / outline < 0.2

    def test_all_slices_together_cover_the_lap(self):
        location = {"VER": self._skewed_trace()}
        meta = {"VER": {"team_name": "Red Bull", "team_colour": "#3671c6"}}

        svg = build_track_svg(
            location,
            driver_meta=meta,
            dominance=["VER"] * 10,
            circuit_info={"rotation": 0},
        )

        covered = self._path_lengths(svg, "#3671c6")
        outline = self._path_lengths(svg, "#07080a")
        assert covered >= outline * 0.95


class TestOutlineDecimation:
    """DASH-05: a full-session trace must not ship a 20 000-point path."""

    @staticmethod
    def _long_trace(points: int = 20000) -> pd.DataFrame:
        angle = np.linspace(0, 2 * np.pi * 50, points)  # 50 laps
        return pd.DataFrame(
            {
                "Distance": np.linspace(0.0, 300_000.0, points),
                "X": np.cos(angle) * 1000,
                "Y": np.sin(angle) * 600,
            }
        )

    def test_svg_stays_small(self):
        svg = build_track_svg({"VER": self._long_trace()})

        assert svg is not None
        assert len(svg.encode("utf-8")) < 150 * 1024

    def test_outline_is_decimated_to_the_cap(self):
        from ui.track_map import MAX_OUTLINE_POINTS

        svg = build_track_svg({"VER": self._long_trace()})
        outline = re.search(r'<path d="([^"]+)" fill="none" stroke="#07080a"', svg).group(1)

        assert outline.count("L") <= MAX_OUTLINE_POINTS

    def test_short_traces_are_untouched(self, location):
        svg = build_track_svg(location)
        outline = re.search(r'<path d="([^"]+)" fill="none" stroke="#07080a"', svg).group(1)

        assert outline.count("L") == len(location["VER"]) - 1


class TestIdealLapDisplay:
    """DASH-06: both ideals are visible - session's on the map, driver's in the row."""

    def test_diff_cell_carries_the_personal_ideal(self):
        row = TestTowerPartitions._row(1, personal_ideal=89.5, diff="+1.000")

        markup = tower_html([row])

        assert "Personal ideal 1:29.500" in markup

    def test_missing_personal_ideal_is_stated(self):
        markup = tower_html([TestTowerPartitions._row(1)])

        assert "No personal ideal lap yet" in markup


class TestMapFollowsRealSectors:
    """DASH-03: the map's slices must line up with the timing strips."""

    @staticmethod
    def _straight_lap(points: int = 600) -> pd.DataFrame:
        distance = np.linspace(0.0, 3000.0, points)
        return pd.DataFrame(
            {
                "Distance": distance,
                "X": distance,  # straight line: path length == distance
                "Y": np.zeros(points),
            }
        )

    def test_uneven_sectors_move_the_slice_boundaries(self):
        from processing.timing import micro_sector_marks

        location = {"VER": self._straight_lap()}
        meta = {"VER": {"team_name": "Red Bull", "team_colour": "#3671c6"}}
        # Sector 1 is only 600 m of a 3000 m lap.
        marks = micro_sector_marks([0.0, 600.0, 2400.0, 3000.0])

        svg = build_track_svg(
            location,
            driver_meta=meta,
            dominance=["VER"] + [None] * 14,
            segment_distances=marks,
            circuit_info={"rotation": 0},
        )

        first_slice = TestDominancePlacement._path_lengths(svg, "#3671c6")
        outline = TestDominancePlacement._path_lengths(svg, "#07080a")
        # One fifth of a 600 m sector = 120 m of a 3000 m lap = 4 %.
        assert 0.02 < first_slice / outline < 0.07

    def test_without_marks_the_split_stays_even(self):
        location = {"VER": self._straight_lap()}
        meta = {"VER": {"team_name": "Red Bull", "team_colour": "#3671c6"}}

        svg = build_track_svg(
            location,
            driver_meta=meta,
            dominance=["VER"] + [None] * 14,
            circuit_info={"rotation": 0},
        )

        first_slice = TestDominancePlacement._path_lengths(svg, "#3671c6")
        outline = TestDominancePlacement._path_lengths(svg, "#07080a")
        assert 0.05 < first_slice / outline < 0.08  # 1/15 of the lap


class TestHeaderFlagState:
    """DASH-08: a finished session must not report a sector's yellow flag."""

    @staticmethod
    def _messages(*entries) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"Time": pd.to_timedelta(i * 60, unit="s"), "Flag": flag, "Scope": scope}
                for i, (flag, scope) in enumerate(entries)
            ]
        )

    def _flag(self, **session) -> str:
        from ui.dashboard import _flag_state

        return _flag_state({"session_info": {}, "is_live": False, **session})

    def test_sector_yellow_is_not_the_track_state(self):
        state = self._flag(
            race_control=self._messages(("CHEQUERED", "Track"), ("YELLOW", "Sector"))
        )

        assert state == "CHEQUERED"

    def test_driver_scoped_blue_is_ignored(self):
        state = self._flag(race_control=self._messages(("BLUE", "Driver")))

        assert state == "FINISHED"

    def test_track_scoped_red_is_kept(self):
        state = self._flag(race_control=self._messages(("GREEN", "Track"), ("RED", "Track")))

        assert state == "RED"

    def test_messages_without_a_scope_column_are_not_trusted(self):
        messages = pd.DataFrame({"Flag": ["YELLOW"], "Time": pd.to_timedelta([0], unit="s")})

        assert self._flag(race_control=messages) == "FINISHED"

    def test_no_messages_means_the_session_ended(self):
        assert self._flag() == "FINISHED"

    def test_live_uses_track_status_only(self):
        state = self._flag(
            session_info={"track_status": {"status": "2"}},
            is_live=True,
            race_control=self._messages(("CHEQUERED", "Track")),
        )

        assert state == "YELLOW"

    def test_live_without_track_status_is_green_not_a_stale_message(self):
        state = self._flag(is_live=True, race_control=self._messages(("YELLOW", "Sector")))

        assert state == "GREEN"


class TestHeaderClock:
    """DASH-09: the clock slot showed the weather sampling window."""

    @staticmethod
    def _laps(minutes: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Driver": ["VER", "VER"],
                "LapNumber": [1, 2],
                "LapTime": pd.to_timedelta([91.0, 90.5], unit="s"),
                "Time": pd.to_timedelta([60.0, minutes * 60], unit="s"),
            }
        )

    def _clock(self, **session) -> str:
        from ui.dashboard import _session_clock

        return _session_clock({"session_info": {}, "is_live": False, **session})

    def test_duration_comes_from_the_laps_not_the_weather(self):
        weather = pd.DataFrame(
            {"Time": pd.to_timedelta([0, 7200], unit="s"), "AirTemp": [20.0, 21.0]}
        )

        assert self._clock(laps=self._laps(95), weather=weather) == "1:35:00"

    def test_short_sessions_still_show_hours(self):
        assert self._clock(laps=self._laps(42.5)) == "0:42:30"

    def test_no_laps_leaves_a_placeholder(self):
        assert self._clock() == "\u2013"

    def test_live_counts_down_the_extrapolated_clock(self):
        state = self._clock(session_info={"extrapolated_clock": "0:32:15"}, is_live=True)

        assert state == "0:32:15"

    def test_live_without_a_clock_is_a_placeholder(self):
        assert self._clock(is_live=True) == "\u2013"

    def test_header_labels_the_slot(self):
        markup = header_html({"session_info": {}, "is_live": False, "laps": self._laps(60)})

        assert "Duration" in markup
        assert "1:00:00" in markup


class TestStatusBadgeMarkup:
    """DASH-10: the new badges need sensible styling, not the 'out' default."""

    def test_finished_reads_as_on_track_styling(self):
        markup = tower_html([TestTowerPartitions._row(1, status="FIN")])

        assert 'class="f1-badge track">FIN' in markup

    def test_a_lapped_finish_is_still_a_finish(self):
        markup = tower_html([TestTowerPartitions._row(1, status="+1L")])

        # UI-13: the laps down belong in the gap column, not a chip.
        assert 'class="f1-badge track">FIN' in markup and "+1L" not in markup

    def test_retirements_are_marked_out(self):
        for badge in ("DNF", "DSQ", "DNS"):
            markup = tower_html([TestTowerPartitions._row(1, status=badge)])
            assert f'class="f1-badge out">{badge}' in markup


class TestTyreBadges:
    """DASH-11: the badge shows tyre age, and marks a scrubbed set."""

    @staticmethod
    def _markup(**stint) -> str:
        base = {"compound": "SOFT", "laps_used": 14, "stint_laps": 9, "fresh": False}
        base.update(stint)
        return tower_html([TestTowerPartitions._row(1, tyre_history=[base])])

    def test_age_is_shown_not_the_stint_length(self):
        markup = self._markup()

        assert "<em>14</em>" in markup
        assert "14 laps old" in markup and "9 this stint" in markup

    def test_a_used_set_gets_a_dashed_ring(self):
        assert 'class="f1-tyre used"' in self._markup(fresh=False)

    def test_a_new_set_is_not_marked_used(self):
        markup = self._markup(fresh=True, laps_used=9)

        assert 'class="f1-tyre"' in markup and "(new)" in markup

    def test_unknown_condition_is_stated(self):
        assert "condition unknown" in self._markup(fresh=None)


class TestSpecGapMarkup:
    """DASH-12: purple last lap, zero speed in the pits, wind arrow."""

    def test_session_best_last_lap_is_highlighted(self):
        markup = tower_html(
            [TestTowerPartitions._row(1, last_lap="1:30.500", last_is_session_best=True)]
        )

        assert 'class="f1-time best f1-num">1:30.500' in markup

    def test_an_ordinary_last_lap_is_not_highlighted(self):
        markup = tower_html([TestTowerPartitions._row(1, last_lap="1:32.000")])

        assert 'class="f1-time f1-num">1:32.000' in markup

    def test_a_car_in_the_pits_has_no_trap_speed(self):
        markup = tower_html([TestTowerPartitions._row(1, status="IN PIT", speed_kmh=0.0)])

        # UI-13: a pit-lane car shows the missing-value dash, not 0 km/h.
        assert '<span class="f1-time f1-num">\u2013</span>' in markup
        assert 'class="f1-badge pit">PIT<' in markup

    def test_wind_shows_a_compass_arrow(self):
        weather = pd.DataFrame(
            {
                "Time": pd.to_timedelta([0], unit="s"),
                "WindSpeed": [5.0],
                "WindDirection": [90],
            }
        )

        markup = header_html({"session_info": {}, "weather": weather, "is_live": False})

        assert "→" in markup and "18.0 km/h E" in markup

    def test_header_names_the_country(self):
        markup = header_html(
            {
                "session_info": {"gp": "Dutch Grand Prix", "country": "Netherlands"},
                "is_live": False,
            }
        )

        assert "Netherlands" in markup


class TestResultsPolish:
    """UI-05: accessible map, sticky tower, low-priority columns that fold."""

    def test_the_map_is_an_image_with_a_title(self):
        svg = build_track_svg({"VER": _oval()}, title="Bahrain Grand Prix 2023 Race")

        assert 'role="img"' in svg
        assert "<title>Bahrain Grand Prix 2023 Race</title>" in svg

    def test_the_tower_header_and_first_two_columns_are_sticky(self):
        from ui.theme import DASHBOARD_CSS

        assert "position: sticky; top: 0" in DASHBOARD_CSS
        assert ".f1-tower td:nth-child(1)" in DASHBOARD_CSS
        assert ".f1-tower td:nth-child(2)" in DASHBOARD_CSS
        assert ".f1-tower .col-compact { display: none; }" in DASHBOARD_CSS

    def test_mini_sector_cells_say_what_their_colour_means(self):
        from ui.dashboard import _segments_html

        markup = _segments_html(["PURPLE", "GREEN"], sector=2)

        assert "Sector 2" in markup and "mini 1" in markup and "session best" in markup
        assert "personal best" in markup

    def test_an_on_track_car_has_an_empty_status_cell(self):
        from ui.dashboard import _status_html

        assert _status_html("ON TRACK") == ""
        assert "PIT" in _status_html("IN PIT")


class TestSectorCardsOnPhones:
    """UI-20: the three sector cards wrap instead of clipping sector 3."""

    def test_the_grid_wraps_below_three_card_widths(self):
        from ui.theme import DASHBOARD_CSS

        assert "repeat(auto-fit, minmax(150px, 1fr))" in DASHBOARD_CSS
        assert "repeat(3, 1fr)" not in DASHBOARD_CSS
