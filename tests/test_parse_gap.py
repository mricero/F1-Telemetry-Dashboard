"""Timing-screen gap cells -> numbers (IMPROVEMENTS.md REPLAY-02).

The values below are the real formats: FastF1's cached stream stores the
leader as ``"LAP 23"`` and a lapped car as ``"1 L"`` (2 865 times in the
2023 Bahrain race), the live feed also sends ``""`` before timing starts and
``"+60.928"`` style gaps, and qualifying streams carry float NaN.
"""

import math

import pytest

from f1dash.processing.time_utils import parse_gap


@pytest.mark.parametrize(
    "value,expected",
    [
        ("+1.234", (1.234, 0)),
        ("12.5", (12.5, 0)),
        ("+60.928", (60.928, 0)),
        ("LAP 23", (0.0, 0)),  # the leader's gap *and* the leader's interval
        ("LAP 1", (0.0, 0)),
        ("1 L", (None, 1)),  # the form FastF1's cache holds
        ("1L", (None, 1)),
        ("2 LAPS", (None, 2)),
        ("+1 LAP", (None, 1)),
        ("", (None, None)),
        (None, (None, None)),
        (math.nan, (None, None)),
        ("+1:02.345", (62.345, 0)),
    ],
)
def test_parse_gap(value, expected):
    assert parse_gap(value) == expected


def test_nonsense_is_not_a_gap():
    assert parse_gap("NOT A GAP") == (None, None)
