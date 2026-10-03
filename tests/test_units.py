"""UX-12: speed, temperature and time-of-day units."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from f1dash.processing.units import (
    METRIC,
    SPEED_CHOICES,
    TEMP_CHOICES,
    TIME_CHOICES,
    Units,
    format_offset,
    local_offset_seconds,
    non_default_params,
    parse_choice,
    parse_gmt_offset,
    speed_from_kmh,
    speed_label,
    temp_from_c,
    temp_label,
    wall_clock,
)

os.environ.setdefault("F1_METRICS_STORE", ":memory:")


class TestConversions:
    def test_kmh_to_mph(self):
        assert speed_from_kmh(100.0, "mph") == pytest.approx(62.1371, abs=1e-4)
        assert speed_from_kmh(100.0, "kmh") == 100.0

    def test_celsius_to_fahrenheit(self):
        assert temp_from_c(0.0, "f") == 32.0
        assert temp_from_c(100.0, "f") == 212.0
        assert temp_from_c(-40.0, "f") == -40.0
        assert temp_from_c(25.0, "c") == 25.0

    def test_conversions_work_on_a_series(self):
        converted = speed_from_kmh(pd.Series([160.9344, None]), "mph")

        assert converted.iloc[0] == pytest.approx(100.0)
        assert pd.isna(converted.iloc[1])

    def test_an_unknown_unit_leaves_the_value_alone(self):
        assert speed_from_kmh(50.0, "knots") == 50.0
        assert temp_from_c(20.0, "kelvin") == 20.0

    def test_labels(self):
        assert (speed_label("kmh"), speed_label("mph")) == ("km/h", "mph")
        assert (temp_label("c"), temp_label("f")) == ("\N{DEGREE SIGN}C", "\N{DEGREE SIGN}F")

    @given(st.floats(min_value=0, max_value=450))
    def test_mph_and_back_is_the_identity(self, kmh):
        assert speed_from_kmh(kmh, "mph") * 1.609344 == pytest.approx(kmh)


class TestGmtOffset:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [
            ("03:00:00", 10800),
            ("-05:00:00", -18000),
            ("+05:30", 19800),
            ("0530", 19800),
            ("00:00:00", 0),
        ],
    )
    def test_the_feed_shape_is_read(self, text, seconds):
        assert parse_gmt_offset(text) == seconds

    @pytest.mark.parametrize("text", [None, "", "abc", "25:00:00", "03:75:00", float("nan")])
    def test_anything_else_is_none(self, text):
        assert parse_gmt_offset(text) is None

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [(10800, "UTC+3"), (-14400, "UTC-4"), (19800, "UTC+5:30"), (0, "UTC"), (None, "UTC")],
    )
    def test_offsets_are_formatted(self, seconds, text):
        assert format_offset(seconds) == text


class TestWallClock:
    STAMP = pd.Timestamp("2026-09-06 12:00:00")  # naive: UTC

    def test_track_time_uses_the_circuit_offset(self):
        assert wall_clock(self.STAMP, METRIC, 7200, 3600) == ("14:00:00", "UTC+2")

    def test_local_time_uses_the_viewers_offset(self):
        assert wall_clock(self.STAMP, Units(time="local"), 7200, -18000) == ("07:00:00", "UTC-5")

    def test_an_unknown_offset_falls_back_to_utc_and_says_so(self):
        assert wall_clock(self.STAMP, METRIC, None, None) == ("12:00:00", "UTC")

    def test_a_date_rolls_over_midnight(self):
        stamp = pd.Timestamp("2026-09-06 23:30:00")

        assert wall_clock(stamp, METRIC, 3600, None) == ("00:30:00", "UTC+1")

    def test_strings_and_aware_stamps_are_accepted(self):
        aware = pd.Timestamp("2026-09-06 14:00:00+02:00")

        assert wall_clock("2026-09-06T12:00:00Z", METRIC, 0, None)[0] == "12:00:00"
        assert wall_clock(aware, METRIC, 0, None)[0] == "12:00:00"

    @pytest.mark.parametrize("value", [None, pd.NaT, "", "not a time", float("nan")])
    def test_no_time_is_none(self, value):
        assert wall_clock(value, METRIC, 0, 0) is None

    def test_a_zone_name_gives_the_offset_at_that_moment(self):
        try:
            ZoneInfo("Europe/London")
        except ZoneInfoNotFoundError:
            pytest.skip("no tz database on this machine")
        summer = datetime(2026, 7, 1, tzinfo=UTC)
        winter = datetime(2026, 1, 1, tzinfo=UTC)

        assert local_offset_seconds("Europe/London", summer) == 3600
        assert local_offset_seconds("Europe/London", winter) == 0
        assert local_offset_seconds("Not/AZone") is None
        assert local_offset_seconds(None) is None


class TestLinkParameters:
    def test_defaults_are_omitted(self):
        assert non_default_params(METRIC) == {}

    def test_only_what_differs_is_named(self):
        assert non_default_params(Units(speed="mph", time="local")) == {
            "speed": "mph",
            "tz": "local",
        }

    def test_an_unknown_choice_is_the_default(self):
        assert parse_choice("mph", SPEED_CHOICES, "kmh") == "mph"
        assert parse_choice(["kmh", "MPH"], SPEED_CHOICES, "kmh") == "mph"
        assert parse_choice("<b>", SPEED_CHOICES, "kmh") == "kmh"
        assert parse_choice(None, SPEED_CHOICES, "kmh") == "kmh"

    @given(
        st.sampled_from(["kmh", "mph"]),
        st.sampled_from(["c", "f"]),
        st.sampled_from(["track", "local"]),
    )
    def test_encode_then_decode_round_trips(self, speed, temp, time):
        units = Units(speed, temp, time)
        params = non_default_params(units)

        decoded = Units(
            parse_choice(params.get("speed"), SPEED_CHOICES, METRIC.speed),
            parse_choice(params.get("temp"), TEMP_CHOICES, METRIC.temp),
            parse_choice(params.get("tz"), TIME_CHOICES, METRIC.time),
        )
        assert decoded == units


class TestTrackOffsetFromFastF1:
    def _session(self, local):
        event = SimpleNamespace(get_session_date=lambda name, utc=False: local)
        return SimpleNamespace(name="Race", event=event)

    def test_the_difference_between_local_and_utc_is_the_offset(self):
        from f1dash.data.source_manager import DataSourceManager

        local = pd.Timestamp("2026-09-06 15:00:00+02:00")

        assert DataSourceManager._event_gmt_offset(self._session(local)) == "02:00:00"

    def test_a_negative_offset(self):
        from f1dash.data.source_manager import DataSourceManager

        session = self._session(pd.Timestamp("2026-06-14 14:00:00-04:00"))

        assert DataSourceManager._event_gmt_offset(session) == "-04:00:00"

    def test_a_schedule_without_local_times_gives_nothing(self):
        from f1dash.data.source_manager import DataSourceManager

        assert DataSourceManager._event_gmt_offset(self._session(pd.Timestamp("2026-09-06"))) == ""
        assert DataSourceManager._event_gmt_offset(SimpleNamespace(name="Race")) == ""


class TestHeaderAndTower:
    WEATHER = pd.DataFrame(
        {
            "Time": [0.0],
            "AirTemp": [20.0],
            "TrackTemp": [30.0],
            "Humidity": [50.0],
            "WindSpeed": [10.0],  # m/s = 36 km/h
            "WindDirection": [90.0],
            "Rainfall": [False],
        }
    )

    def _header(self, units=METRIC, start=""):
        from f1dash.ui.dashboard import header_html

        return header_html(
            {"session_info": {"gp": "Test GP"}, "weather": self.WEATHER, "is_live": False},
            units,
            start,
        )

    def test_the_default_is_metric(self):
        markup = self._header()

        assert "30.0 &deg;C" in markup and "36.0 km/h" in markup

    def test_imperial_units_convert_the_temperatures_and_the_wind(self):
        markup = self._header(Units(speed="mph", temp="f"))

        assert "86.0 &deg;F" in markup  # 30 C track
        assert "68.0 &deg;F" in markup  # 20 C air
        assert "22.4 mph" in markup  # 36 km/h

    def test_the_session_start_is_shown_when_given(self):
        assert "Start 15:00:00 UTC+2" in self._header(start="15:00:00 UTC+2")
        assert "Start" not in self._header()

    def test_the_speed_column_names_its_unit(self):
        from f1dash.processing.timing import build_timing_rows
        from f1dash.ui.dashboard import tower_html
        from tests.test_app_sources import session_dict

        rows = build_timing_rows(session_dict("fastf1"))

        assert "Speed km/h" in tower_html(rows)
        assert "Speed mph" in tower_html(rows, units=Units(speed="mph"))

    def test_a_trap_speed_is_converted(self):
        from f1dash.ui.dashboard import _speed_text

        assert _speed_text(321.869, "", "kmh") == "322"
        assert _speed_text(321.869, "", "mph") == "200"
        assert _speed_text(0.0, "PIT", "mph") == "\N{EN DASH}"


class TestChartsAndRaceControl:
    def _frames(self):
        return {
            "VER": pd.DataFrame({"Distance": [0.0, 100.0, 200.0], "Speed": [160.9344] * 3}),
        }

    def test_the_speed_axis_says_mph_and_the_values_follow(self):
        from f1dash.ui.layout import TELEMETRY_CHANNELS, create_telemetry_chart

        figure = create_telemetry_chart(
            self._frames(), TELEMETRY_CHANNELS["Speed"], {"VER": "#ffffff"}, "mph"
        )

        assert figure.layout.yaxis.title.text == "Speed (mph)"
        assert list(figure.data[0].y) == pytest.approx([100.0] * 3)
        assert "mph" in figure.data[0].hovertemplate

    def test_the_default_is_untouched(self):
        from f1dash.ui.layout import TELEMETRY_CHANNELS, create_telemetry_chart

        metric = create_telemetry_chart(
            self._frames(), TELEMETRY_CHANNELS["Speed"], {"VER": "#ffffff"}
        )

        assert metric.layout.yaxis.title.text == "Speed (km/h)"
        assert list(metric.data[0].y) == pytest.approx([160.9344] * 3)

    def test_race_control_lines_carry_the_time_of_day(self):
        from f1dash.ui.layout import race_control_html, race_control_lines

        frame = pd.DataFrame(
            {
                "Time": [pd.Timestamp("2026-09-06 12:00:05")],
                "Lap": [3],
                "Flag": ["YELLOW"],
                "Message": ["YELLOW IN TURN 4"],
            }
        )
        lines = race_control_lines(frame, lambda stamp: wall_clock(stamp, METRIC, 7200, None)[0])
        markup = race_control_html(lines)

        assert lines[0]["time"] == "14:00:05"
        assert "14:00:05" in markup and "f1-rc-timed" in markup

    def test_without_a_clock_the_panel_is_as_before(self):
        from f1dash.ui.layout import race_control_html

        markup = race_control_html([{"lap": "L3", "flag": "", "message": "x"}])

        assert "f1-rc-time" not in markup and "f1-rc-timed" not in markup
