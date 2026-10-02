"""OpenF1 adapter - the team radio list (FEAT-05).

OpenF1 serves historical data free and without a key from 2023 on. Only the
``team_radio`` and ``sessions`` endpoints are used. The F1TV subscription token
is never sent here: it belongs to F1's own servers (see CLAUDE.md). The live
feed's ``TeamRadio`` topic is auth-gated and is not read at all.

A recording is a plain MP3 on F1's static host. This module returns its URL
and never downloads it; the page offers it as a link the viewer opens.
"""

import time
from datetime import timedelta

import pandas as pd
import requests

BASE_URL = "https://api.openf1.org/v1"
FIRST_YEAR = 2023  # OpenF1's free historical coverage starts here
MAX_RETRIES = 3
BACKOFF_BASE = 1.0
# The fetch runs on Streamlit's script thread; longer waits fail fast.
MAX_RETRY_AFTER = 10.0
# FastF1's session date and OpenF1's date_start differ by hours at most, while
# two sessions of one name are a week apart.
DATE_TOLERANCE = timedelta(days=1)

COLUMNS = ["Time", "Driver", "DriverNumber", "Date", "Url"]


def _get(endpoint: str, params: dict) -> list:
    """GET one OpenF1 list, retrying 429s that ask for a short wait."""
    delay = BACKOFF_BASE
    for attempt in range(MAX_RETRIES):
        response = None
        try:
            response = requests.get(
                f"{BASE_URL}/{endpoint}",
                params=params,
                timeout=15,
                headers={"User-Agent": "F1-Telemetry-Dashboard/1.0"},
            )
            if response.status_code == 404:  # OpenF1 answers 404 for "no rows"
                return []
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else []
        except requests.RequestException as e:
            if getattr(response, "status_code", None) != 429 or attempt == MAX_RETRIES - 1:
                raise ConnectionError(f"Failed to fetch {endpoint} from OpenF1: {e}") from e
            try:
                headers = response.headers if response is not None else {}
                wait = max(0.0, float(headers.get("Retry-After", delay)))
            except (TypeError, ValueError):
                wait = delay
            if wait > MAX_RETRY_AFTER:
                raise ConnectionError(
                    f"OpenF1 asked to wait {wait:.0f} s (rate limit). Try again later."
                ) from e
            time.sleep(wait)
            delay *= 2
    raise ConnectionError(f"Failed to fetch {endpoint} from OpenF1")


def find_session_key(
    sessions: list[dict], date: pd.Timestamp | None = None, country: str | None = None
) -> int | None:
    """The OpenF1 session that is the one the dashboard loaded.

    ``sessions`` are OpenF1 rows of one year and session name. With a date the
    nearest ``date_start`` within a day wins; without one the country decides.
    """
    if date is not None and not pd.isna(date):
        target = pd.Timestamp(date)
        target = target.tz_localize("UTC") if target.tzinfo is None else target.tz_convert("UTC")
        best: tuple[pd.Timedelta, int] | None = None
        for row in sessions:
            start = pd.to_datetime(row.get("date_start"), utc=True, errors="coerce")
            if pd.isna(start) or "session_key" not in row:
                continue
            gap = abs(start - target)
            if gap <= DATE_TOLERANCE and (best is None or gap < best[0]):
                best = (gap, int(row["session_key"]))
        return best[1] if best else None
    if country:
        wanted = str(country).strip().lower()
        for row in sessions:
            if str(row.get("country_name", "")).strip().lower() == wanted and "session_key" in row:
                return int(row["session_key"])
    return None


def radio_frame(
    rows: list[dict],
    drivers: pd.DataFrame | None,
    session_start: float | None,
    session_date_start: str | pd.Timestamp | None,
) -> pd.DataFrame:
    """OpenF1 ``team_radio`` rows as ``Time`` (s), ``Driver``, ``Url`` ...

    ``Time`` is seconds on the session clock: the recording's offset from the
    session's ``date_start`` plus ``session_start``. It is approximate (the two
    clocks agree to a few seconds) and NaN when no ``date_start`` is known.
    """
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(rows)
    for column in ("date", "driver_number", "recording_url"):
        if column not in frame:
            frame[column] = None
    frame = frame.dropna(subset=["recording_url"]).copy()
    frame["Date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce", format="ISO8601")
    frame["DriverNumber"] = pd.to_numeric(frame["driver_number"], errors="coerce").astype("Int64")

    acronyms: dict[int, str] = {}
    if drivers is not None and not drivers.empty and "name_acronym" in drivers:
        numbers = pd.to_numeric(drivers["driver_number"], errors="coerce")
        acronyms = {
            int(n): str(a) for n, a in zip(numbers, drivers["name_acronym"], strict=True) if n == n
        }
    frame["Driver"] = [
        acronyms.get(int(n), str(n)) if pd.notna(n) else "" for n in frame["DriverNumber"]
    ]

    start = pd.to_datetime(session_date_start, utc=True, errors="coerce")
    if pd.isna(start):
        frame["Time"] = float("nan")
    else:
        offset = (frame["Date"] - start).dt.total_seconds()
        frame["Time"] = offset + float(session_start or 0.0)
    frame["Url"] = frame["recording_url"].astype(str)
    return frame.sort_values("Date", kind="stable").reset_index(drop=True).reindex(columns=COLUMNS)


def get_team_radio(
    year: int,
    session_name: str,
    date: pd.Timestamp | None = None,
    country: str | None = None,
    drivers: pd.DataFrame | None = None,
    session_start: float | None = None,
) -> pd.DataFrame:
    """Team radio recordings of one session; empty before 2023 or when not found.

    Raises ``ConnectionError`` when OpenF1 cannot be reached, so a caller's
    ``st.cache_data`` does not cache the failure.
    """
    if int(year) < FIRST_YEAR:
        return pd.DataFrame(columns=COLUMNS)
    sessions = _get("sessions", {"year": int(year), "session_name": session_name})
    key = find_session_key(sessions, date, country)
    if key is None:
        return pd.DataFrame(columns=COLUMNS)
    match: dict = next((s for s in sessions if s.get("session_key") == key), {})
    rows = _get("team_radio", {"session_key": key})
    return radio_frame(rows, drivers, session_start, match.get("date_start"))
