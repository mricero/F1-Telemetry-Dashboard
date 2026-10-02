"""Deleted laps and track-limit counts (FEAT-11).

Pure transforms over the unified session dict's ``laps``, ``race_control`` and
``drivers`` tables. A lap's deletion comes from FastF1's ``Deleted`` and
``DeletedReason``; race control supplies when it was announced and the
per-driver warning counts.
"""

import re

import numpy as np
import pandas as pd

from processing.replay_model import _event_seconds
from processing.time_utils import to_seconds
from processing.timing import _deletion

DELETED_COLUMNS = ["Driver", "LapNumber", "LapTime", "Reason", "Announced"]
COUNT_COLUMNS = ["Driver", "Warnings", "Deletions"]

# "CAR 44 (HAM) TIME 1:31.204 DELETED - TRACK LIMITS AT TURN 4 LAP 12 14:07:31"
_DELETED = re.compile(
    r"CAR (?P<number>\d{1,2})(?: \((?P<code>[A-Z]{3})\))? .*?TIME (?P<time>\d:\d\d\.\d\d\d) "
    r"DELETED - (?P<reason>.*)",
    re.IGNORECASE,
)
_CAR = re.compile(r"CAR (?P<number>\d{1,2})(?: \((?P<code>[A-Z]{3})\))?", re.IGNORECASE)
_CLOCK = re.compile(r"\d\d:\d\d:\d\d")


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({column: [] for column in columns})


def _number_to_code(drivers: pd.DataFrame | None) -> dict[str, str]:
    """Car number (as text, no leading zero) -> three-letter code."""
    if drivers is None or drivers.empty:
        return {}
    if not {"driver_number", "name_acronym"} <= set(drivers.columns):
        return {}
    mapping: dict[str, str] = {}
    for number, code in zip(drivers["driver_number"], drivers["name_acronym"], strict=True):
        if pd.isna(number) or pd.isna(code):
            continue
        try:
            mapping[str(int(float(number)))] = str(code)
        except (TypeError, ValueError):
            mapping[str(number)] = str(code)
    return mapping


def _message_driver(match: re.Match, numbers: dict[str, str]) -> str | None:
    code = match.groupdict().get("code")
    if code:
        return code.upper()
    return numbers.get(str(int(match.group("number"))))


def _messages(race_control: pd.DataFrame | None) -> list[tuple[float, str]]:
    """``(session seconds, message)`` for every text row; NaN when not on the clock."""
    if race_control is None or race_control.empty or "Message" not in race_control.columns:
        return []
    stamps = _event_seconds(race_control)
    return [
        (float(moment), message)
        for moment, message in zip(stamps.tolist(), race_control["Message"].tolist(), strict=True)
        if isinstance(message, str)
    ]


def deleted_laps(
    laps: pd.DataFrame | None,
    race_control: pd.DataFrame | None = None,
    drivers: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Every lap the stewards deleted: who, which lap, the time, why and when.

    ``Announced`` is the race-control message's session time (seconds) when a
    ``CAR n (XXX) TIME m:ss.mmm DELETED - ...`` message matches the lap by
    driver and lap time; NaN when none does (a source without race control).
    The reason is FastF1's ``DeletedReason``, else the message's.
    """
    if laps is None or laps.empty or "Deleted" not in laps.columns:
        return _empty(DELETED_COLUMNS)
    numbers = _number_to_code(drivers)
    announced: dict[tuple[str, float], tuple[float, str]] = {}
    for moment, message in _messages(race_control):
        match = _DELETED.match(message)
        if not match or np.isnan(moment):
            continue
        code = _message_driver(match, numbers)
        if code is None:
            continue
        key = (code, round(to_seconds(match["time"]) or 0.0, 3))
        if key not in announced or moment < announced[key][0]:
            text = " ".join(_CLOCK.sub("", match["reason"]).split())
            announced[key] = (moment, text)
    rows = []
    for _, lap in laps.iterrows():
        deleted, reason = _deletion(lap)
        if not deleted:
            continue
        seconds = to_seconds(lap.get("LapTime"))
        key = (str(lap["Driver"]), round(seconds, 3) if seconds is not None else float("nan"))
        found = announced.get(key)
        rows.append(
            {
                "Driver": str(lap["Driver"]),
                "LapNumber": lap.get("LapNumber"),
                "LapTime": seconds,
                "Reason": reason or (found[1] if found else None),
                "Announced": found[0] if found else np.nan,
            }
        )
    if not rows:
        return _empty(DELETED_COLUMNS)
    frame = pd.DataFrame(rows, columns=DELETED_COLUMNS)
    frame["LapNumber"] = pd.to_numeric(frame["LapNumber"], errors="coerce")
    return frame.sort_values(["LapNumber", "Driver"], kind="stable").reset_index(drop=True)


def track_limit_counts(
    race_control: pd.DataFrame | None, drivers: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Per driver: track-limit warnings and track-limit deletions, from race control.

    A message that names a car and says ``TRACK LIMITS`` is a deletion when it
    reads ``DELETED`` and a warning otherwise (black and white flag, formal
    warning); ``REINSTATED`` messages count as neither. Columns ``Driver,
    Warnings, Deletions``, most deletions first.
    """
    numbers = _number_to_code(drivers)
    counts: dict[str, list[int]] = {}
    for _, message in _messages(race_control):
        upper = message.upper()
        if "TRACK LIMITS" not in upper or "REINSTATED" in upper:
            continue
        match = _CAR.search(message)
        code = _message_driver(match, numbers) if match else None
        if code is None:
            continue
        counts.setdefault(code, [0, 0])[1 if "DELETED" in upper else 0] += 1
    if not counts:
        return _empty(COUNT_COLUMNS)
    frame = pd.DataFrame(
        [(code, warn, dele) for code, (warn, dele) in counts.items()], columns=COUNT_COLUMNS
    )
    frame["Total"] = frame["Warnings"] + frame["Deletions"]
    frame = frame.sort_values(["Deletions", "Total", "Driver"], ascending=[False, False, True])
    return frame[COUNT_COLUMNS].reset_index(drop=True)
