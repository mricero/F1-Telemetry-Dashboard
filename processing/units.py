"""Units and time zones (UX-12).

The data is stored in the feed's own units - km/h, degrees Celsius, UTC
wall-clock stamps - and converted only where it is shown. Everything here is
a pure function; the Settings page and the ``units=`` link parameters decide
which choice applies.

``Units`` is the viewer's choice. The defaults (km/h, Celsius, track time)
are what the app has always shown, so a link only names a unit that differs.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import pandas as pd

KMH_PER_MPH = 1.609344

SPEED_PARAM = "speed"
TEMP_PARAM = "temp"
TIME_PARAM = "tz"

SPEED_CHOICES = (("kmh", "km/h"), ("mph", "mph"))
TEMP_CHOICES = (("c", "\N{DEGREE SIGN}C"), ("f", "\N{DEGREE SIGN}F"))
TIME_CHOICES = (("track", "Track time"), ("local", "Local time"))


@dataclass(frozen=True)
class Units:
    """What the viewer reads: ``speed`` kmh|mph, ``temp`` c|f, ``time`` track|local."""

    speed: str = "kmh"
    temp: str = "c"
    time: str = "track"

    def as_dict(self) -> dict[str, str]:
        return {"speed": self.speed, "temp": self.temp, "time": self.time}


METRIC = Units()


def parse_choice(raw, choices, default: str) -> str:
    """A link's value if it names one of ``choices``, else ``default``."""
    if isinstance(raw, list | tuple):
        raw = raw[-1] if raw else None
    wanted = str(raw).strip().lower() if raw is not None else ""
    return wanted if wanted in {name for name, _ in choices} else default


def non_default_params(units: Units) -> dict[str, str]:
    """The link parameters for ``units``; a default value is omitted."""
    chosen = (
        (SPEED_PARAM, units.speed, METRIC.speed),
        (TEMP_PARAM, units.temp, METRIC.temp),
        (TIME_PARAM, units.time, METRIC.time),
    )
    params = {param: value for param, value, default in chosen if value != default}
    return params


# ------------------------------------------------------------------ values


def speed_from_kmh(value, unit: str = "kmh"):
    """km/h -> ``unit``; missing values stay missing. Works on scalars and Series."""
    if unit == "mph":
        return value / KMH_PER_MPH
    return value


def temp_from_c(value, unit: str = "c"):
    """Celsius -> ``unit``. Works on scalars and Series."""
    if unit == "f":
        return value * 9.0 / 5.0 + 32.0
    return value


def speed_label(unit: str) -> str:
    return dict(SPEED_CHOICES).get(unit, "km/h")


def temp_label(unit: str) -> str:
    return dict(TEMP_CHOICES).get(unit, "\N{DEGREE SIGN}C")


# ------------------------------------------------------------------- times

_OFFSET = re.compile(r"^\s*([+-]?)(\d{1,2}):?(\d{2})(?::?(\d{2}))?\s*$")


def parse_gmt_offset(text) -> int | None:
    """``"03:00:00"``, ``"-05:00:00"`` or ``"+0530"`` -> seconds east of UTC.

    This is the shape of F1's ``SessionInfo.GmtOffset``. ``None`` for
    anything else; offsets beyond +-14 h are refused.
    """
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return None
    match = _OFFSET.match(str(text))
    if not match:
        return None
    sign, hours, minutes, seconds = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds or 0)
    if int(minutes) > 59 or total > 14 * 3600:
        return None
    return -total if sign == "-" else total


def format_offset(seconds: int | None) -> str:
    """``UTC+3``, ``UTC+5:30``, ``UTC-4`` or ``UTC``."""
    if seconds is None:
        return "UTC"
    sign = "-" if seconds < 0 else "+"
    minutes = abs(int(seconds)) // 60
    if minutes == 0:
        return "UTC"
    hours, rest = divmod(minutes, 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def utc_moment(value) -> datetime | None:
    """A timestamp, datetime or ISO string as an aware UTC datetime, else None.

    Naive values are UTC: FastF1's race-control and session times are.
    """
    if value is None:
        return None
    moment = pd.to_datetime(value, errors="coerce", utc=True)
    if moment is None or pd.isna(moment):
        return None
    return moment.to_pydatetime()


def local_offset_seconds(timezone_name: str | None, moment: datetime | None = None) -> int | None:
    """The offset of an IANA zone at ``moment`` (default: now), None if unknown."""
    if not timezone_name:
        return None
    try:
        zone: tzinfo = ZoneInfo(timezone_name)
    except (KeyError, ValueError, OSError):  # unknown name, or no tz database
        return None
    when = (moment or datetime.now(UTC)).astimezone(zone)
    delta = when.utcoffset()
    return None if delta is None else int(delta.total_seconds())


def wall_clock(
    value, units: Units, track_offset: int | None, local_offset: int | None
) -> tuple[str, str] | None:
    """``("15:04:05", "UTC+3")`` for a UTC stamp, or None when it has no time.

    ``track`` time uses the circuit's offset; ``local`` the viewer's. When the
    chosen offset is unknown the stamp is shown in UTC, and says so.
    """
    moment = utc_moment(value)
    if moment is None:
        return None
    chosen = track_offset if units.time == "track" else local_offset
    if chosen is None:
        chosen = 0
    shifted = moment + timedelta(seconds=chosen)
    return shifted.strftime("%H:%M:%S"), format_offset(chosen)
