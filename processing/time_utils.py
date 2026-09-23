"""Shared lap/sector time parsing helpers.

The live timing feed sends strings like ``'1:31.204'`` or ``'31.105'``, while
FastF1 provides :class:`pandas.Timedelta`. ``pd.to_timedelta`` must NOT be used
on these strings: it interprets ``'1:31.20'`` as *hours*:minutes* and triggers
NumPy's deprecated "generic" timedelta unit. Everything routes through
:func:`to_seconds` instead.
"""

import re
from datetime import timedelta as _dt_timedelta

import pandas as pd

# 'M:SS.mmm' / 'M:SS.ssss' as emitted by the live timing feed.
_M_S_RE = re.compile(r"^(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")
# Plain seconds with decimals, e.g. sector times like '31.105'.
_SECONDS_RE = re.compile(r"^\d{1,3}\.\d{1,4}$")


def to_seconds(value) -> float | None:
    """Coerce lap/sector times to seconds.

    Accepts pandas Timedelta (FastF1), ``'M:SS.mmm'`` strings (live feed),
    ``'SS.mmm'``, numeric seconds, or None/NaN.
    """
    if value is None:
        return None
    if isinstance(value, pd.Timedelta):
        return None if pd.isna(value) else round(value.total_seconds(), 3)
    if isinstance(value, _dt_timedelta):
        return round(value.total_seconds(), 3)
    if isinstance(value, (int, float)) and pd.notna(value):
        return float(value)

    # NaT / NaN / pd.NA and anything else already ruled out above
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    m = _M_S_RE.match(text)
    if m:
        return round(int(m.group(1)) * 60 + float(m.group(2)), 3)
    if _SECONDS_RE.match(text):
        return round(float(text), 3)
    # Only hand colon/unit-bearing strings to to_timedelta; bare numbers
    # would hit its deprecated "generic" timedelta unit.
    if ":" not in text and not re.search(r"[A-Za-z]", text):
        return None
    try:
        td = pd.to_timedelta(text, errors="coerce")
        if pd.isna(td):
            return None
        return round(float(td.total_seconds()), 3)
    except (ValueError, TypeError):
        return None


# Same shapes as the scalar parser, as one regex for str.extract.
_M_S_EXTRACT = r"^(?:(\d{1,2}):)?(\d{1,3}(?:\.\d{1,4})?)$"


def seconds_series(values: pd.Series) -> pd.Series:
    """Vectorised :func:`to_seconds` over a Series -> float Series (NaN-safe).

    Timedelta and numeric columns convert in one call; string columns go
    through a single ``str.extract`` rather than a Python-level loop, which
    is what this used to be despite the docstring.
    """
    if values is None or len(values) == 0:
        return pd.Series([], dtype="float64")

    if pd.api.types.is_timedelta64_dtype(values):
        return values.dt.total_seconds().round(3).astype("float64")
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce").astype("float64")

    text = values.astype("string").str.strip()
    parts = text.str.extract(_M_S_EXTRACT)
    minutes = pd.to_numeric(parts[0], errors="coerce").fillna(0.0)
    seconds = pd.to_numeric(parts[1], errors="coerce")
    result = (minutes * 60 + seconds).round(3)

    # Anything the pattern did not match (Timedelta objects in an object
    # column, odd strings) falls back to the scalar parser - a handful of
    # values, not the whole column.
    unmatched = result.isna() & values.notna()
    if unmatched.any():
        fallback = [to_seconds(value) for value in values[unmatched]]
        result.loc[unmatched] = pd.Series(fallback, index=values[unmatched].index, dtype="float64")
    return pd.Series(result, index=values.index, dtype="float64")


# Timing-screen gap cells (REPLAY-02). The leader's own cell reads "LAP 23"
# (both gap and interval); a lapped car reads "1 L" in FastF1's stream and
# "+1 LAP" / "2 LAPS" in other feeds; everything else is seconds.
_LEADER_RE = re.compile(r"^LAP\s*\d+$", re.IGNORECASE)
_LAPS_DOWN_RE = re.compile(r"^\+?(\d+)\s*(?:L|LAP|LAPS)$", re.IGNORECASE)
_GAP_SECONDS_RE = re.compile(r"^\+?(\d+(?:\.\d+)?)$")


def parse_gap(value) -> tuple[float | None, int | None]:
    """A gap or interval cell -> ``(seconds, laps_down)``.

    ``"+1.234"`` -> ``(1.234, 0)``; ``"LAP 23"`` (the leader) -> ``(0.0, 0)``;
    ``"1 L"``, ``"+2 LAPS"`` -> ``(None, n)``; ``""``, None, NaN ->
    ``(None, None)``. Strings like ``"1:02.345"`` read as minutes.
    """
    if value is None:
        return (None, None)
    if isinstance(value, (int, float)):
        return (None, None) if pd.isna(value) else (float(value), 0)
    text = str(value).strip()
    if not text:
        return (None, None)
    if _LEADER_RE.match(text):
        return (0.0, 0)
    laps = _LAPS_DOWN_RE.match(text)
    if laps:
        return (None, int(laps.group(1)))
    plain = _GAP_SECONDS_RE.match(text)
    if plain:
        return (float(plain.group(1)), 0)
    seconds = to_seconds(text.lstrip("+"))
    return (seconds, 0) if seconds is not None else (None, None)
