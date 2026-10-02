"""`to_seconds` / `seconds_series`: one grammar, no `pd.to_timedelta` (CORE-01)."""

import warnings

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from processing.time_utils import seconds_series, to_seconds


class TestToSeconds:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("02:00:00", 7200.0),  # live ExtrapolatedClock.Remaining
            ("0:59:59.5", 3599.5),
            ("0 days 00:01:31.204000", 91.204),  # str(Timedelta)
            ("1 day 00:00:01", 86401.0),
            ("1:31.204", 91.204),
            ("31.105", 31.105),
            ("91", 91.0),
            (" 1:31.204 ", 91.204),
            (pd.Timedelta(91.2, unit="s"), 91.2),
            (np.timedelta64(5, "s"), 5.0),
            (90.5, 90.5),
            (np.int64(12), 12.0),
        ],
    )
    def test_accepted_shapes(self, value, expected):
        assert to_seconds(value) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "value",
        ["1 L", "5s", "1 min", "", "nonsense", "+1.2", None, pd.NaT, pd.NA, float("nan"), True],
    )
    def test_rejected_shapes(self, value):
        assert to_seconds(value) is None

    def test_no_deprecation_warning_on_the_session_clock(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert to_seconds("02:00:00") == 7200.0
            assert to_seconds("0 days 00:01:31.204000") == 91.204
            assert seconds_series(pd.Series(["02:00:00", "1 L"])).tolist()[0] == 7200.0

    def test_scalar_and_vector_agree_on_bare_integers_and_bools(self):
        assert seconds_series(pd.Series(["91"])).tolist() == [to_seconds("91")]
        assert seconds_series(pd.Series([True, False])).isna().all()


_digits = st.integers(min_value=0, max_value=99)
_fraction = st.one_of(st.just(""), st.integers(0, 999_999).map(lambda n: f".{n}"))
_time_strings = st.one_of(
    st.builds(lambda m, s, f: f"{m}:{s:02d}{f}", _digits, st.integers(0, 59), _fraction),
    st.builds(lambda s, f: f"{s}{f}", st.integers(0, 9999), _fraction),
    st.builds(
        lambda d, h, m, s, f: f"{d} days {h:02d}:{m:02d}:{s:02d}{f}",
        st.integers(0, 3),
        st.integers(0, 23),
        st.integers(0, 59),
        st.integers(0, 59),
        _fraction,
    ),
    st.sampled_from(["", " ", "1 L", "LAP 3", "+1.234", "nan", "5s", "1:2:3:4", "--"]),
    st.text(max_size=8),
)
_values = st.one_of(
    _time_strings,
    st.none(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(-(10**6), 10**6),
    st.booleans(),
    st.floats(0, 10**5).map(lambda s: pd.Timedelta(s, unit="s")),
)


def _same(values: list) -> None:
    series = pd.Series(values, dtype=object)
    expected = series.map(to_seconds).astype("float64")
    assert seconds_series(series).equals(expected), list(
        zip(values, seconds_series(series), strict=True)
    )


class TestSecondsSeriesProperty:
    @settings(max_examples=300, deadline=None)
    @given(st.lists(_time_strings, min_size=1, max_size=20))
    def test_string_columns_match_the_scalar_parser(self, values):
        series = pd.Series(values)
        assert seconds_series(series).equals(series.map(to_seconds).astype("float64"))

    @settings(max_examples=300, deadline=None)
    @given(st.lists(_values, min_size=1, max_size=20))
    def test_mixed_columns_match_the_scalar_parser(self, values):
        _same(values)

    @settings(max_examples=100, deadline=None)
    @given(st.lists(st.floats(0, 10**5), min_size=1, max_size=20))
    def test_timedelta_columns_match_the_scalar_parser(self, seconds):
        series = pd.Series(pd.to_timedelta(seconds, unit="s"))
        assert seconds_series(series).equals(series.map(to_seconds).astype("float64"))
