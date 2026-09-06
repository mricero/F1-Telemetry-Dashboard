"""Tests for the SVG track map and the dashboard HTML builders."""

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

        assert svg.count("<circle") == 2
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
        html = tower_html([_row("VER", 1), _row("HAM", 11, knocked_out=True)], cutoff=10)

        assert "Outside the top 10" in html and 'class="ko"' in html

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
        assert "6.0 km/h E" in html  # wind speed + cardinal
        assert "60:00" in html  # one-hour session clock

    def test_header_without_weather_uses_placeholders(self):
        html = header_html({"session_info": {"gp": "Test GP"}, "is_live": False})

        assert "--:--" in html and "SESSION ENDED" in html

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
