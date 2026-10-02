"""Shared lap/sector time parsing helpers.

The live timing feed sends strings like ``'1:31.204'`` or ``'31.105'``, while
FastF1 provides :class:`pandas.Timedelta`. ``pd.to_timedelta`` must NOT be used
on these strings: it interprets ``'1:31.20'`` as *hours*:minutes* and triggers
NumPy's deprecated "generic" timedelta unit. Everything routes through
:func:`to_seconds` instead.
"""

import re
from datetime import timedelta as _dt_timedelta

import numpy as np
import pandas as pd

# One grammar for the scalar and the vector parser (CORE-01):
#   [D days ]H:MM:SS[.f]  - the live session clock, str(Timedelta)
#   M:SS[.f]              - live lap times
#   SS[.f]                - sector times, plain seconds
# ASCII digits only. Anything else - unit strings like "1 L" or "5s" included -
# is None: no string ever reaches pd.to_timedelta.
_TIME_PATTERN = (
    r"^(?:([0-9]{1,5}) days? )?"  # days
    r"(?:(?:([0-9]{1,3}):)?([0-9]{1,2}):)?"  # [hours:]minutes:
    r"([0-9]{1,4}(?:\.[0-9]+)?)$"  # seconds
)
_TIME_RE = re.compile(_TIME_PATTERN)
_UNIT_SECONDS = (86400.0, 3600.0, 60.0)


def _round3(value: float) -> float:
    # numpy's rounding, so the scalar and the vector parser agree bit for bit.
    return float(np.round(value, 3))


def _parse_text(text: str) -> float | None:
    match = _TIME_RE.match(text.strip())
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    total = 0.0
    for part, unit in zip((days, hours, minutes), _UNIT_SECONDS, strict=True):
        total = total + (float(part) if part is not None else 0.0) * unit
    return _round3(total + float(seconds))


def to_seconds(value) -> float | None:
    """Coerce lap/sector/clock times to seconds.

    Accepts pandas Timedelta (FastF1), ``'M:SS.mmm'`` strings (live feed),
    ``'H:MM:SS'`` (the live session clock), ``str(Timedelta)``, ``'SS.mmm'``,
    numeric seconds, or None/NaN. Booleans and unit strings (``'1 L'``) are
    None.
    """
    if value is None or isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, np.timedelta64):
        value = pd.Timedelta(value)
    if isinstance(value, pd.Timedelta):
        # From nanoseconds, as ``Series.dt.total_seconds()`` does:
        # ``Timedelta.total_seconds()`` drops below the microsecond, which
        # rounds 1.4105005 s to 1.41 instead of 1.411.
        return None if pd.isna(value) else _round3(value.value / 1e9)
    if isinstance(value, _dt_timedelta):
        return _round3(value.total_seconds())
    if isinstance(value, (int, float, np.integer, np.floating)):
        return None if pd.isna(value) else float(value)
    if isinstance(value, str):
        return _parse_text(value)
    # NaT / pd.NA and anything else: never guessed at.
    return None


def seconds_series(values: pd.Series) -> pd.Series:
    """Vectorised :func:`to_seconds` over a Series -> float Series (NaN-safe).

    ``seconds_series(s)`` equals ``s.map(to_seconds)`` as float64 for every
    input (a property test holds it to that). Timedelta and numeric columns
    convert in one call; all-string columns go through a single
    ``str.extract``; mixed object columns fall back to the scalar parser.
    """
    if values is None or len(values) == 0:
        return pd.Series([], dtype="float64")
    index = values.index

    if pd.api.types.is_bool_dtype(values):
        return pd.Series(np.nan, index=index, dtype="float64")
    if pd.api.types.is_timedelta64_dtype(values):
        return values.dt.total_seconds().round(3).astype("float64")
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce").astype("float64")

    kind = pd.api.types.infer_dtype(values, skipna=True)
    if kind not in ("string", "empty"):
        mapped = [to_seconds(value) for value in values]
        return pd.Series(mapped, index=index, dtype="float64")

    text = values.astype("string").str.strip()
    parts = text.str.extract(_TIME_PATTERN)
    total = pd.Series(0.0, index=index)
    for column, unit in zip((0, 1, 2), _UNIT_SECONDS, strict=True):
        number = pd.to_numeric(parts[column], errors="coerce").astype("float64")
        total = total + number.fillna(0.0) * unit
    seconds = pd.to_numeric(parts[3], errors="coerce").astype("float64")
    return pd.Series((total + seconds).round(3), index=index, dtype="float64")


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
