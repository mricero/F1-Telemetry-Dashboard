"""One lap of one driver, ready for a head-to-head comparison (UX-04).

Pure transforms: no Streamlit, no network. A session's telemetry is either the
driver's fastest lap (``telemetry_scope='fastest'``, distance 0 -> lap length)
or every lap (``'session'``, distance accumulating over the run, ``Time``
counting from the first sample). A lap is cut out of the second by its session
time window; the first has only the one lap, so nothing else can be offered.
"""

import numpy as np
import pandas as pd

from processing.telemetry_processor import TelemetryProcessor
from processing.time_utils import seconds_series

SCOPE_SESSION = "session"
MIN_LAP_SAMPLES = 20


def _driver_laps(laps_df: pd.DataFrame | None, driver: str) -> pd.DataFrame:
    if laps_df is None or laps_df.empty or "Driver" not in laps_df.columns:
        return pd.DataFrame()
    laps = laps_df[laps_df["Driver"] == driver].copy()
    if "LapNumber" not in laps.columns:
        return pd.DataFrame()
    laps["_n"] = pd.to_numeric(laps["LapNumber"], errors="coerce")
    laps["_t"] = seconds_series(laps["LapTime"]) if "LapTime" in laps.columns else np.nan
    return laps[laps["_n"].notna()].sort_values("_n")


def lap_times(laps_df: pd.DataFrame | None, driver: str) -> dict[int, float]:
    """``{lap number: lap time (s)}`` for the driver's timed laps."""
    laps = _driver_laps(laps_df, driver)
    if laps.empty:
        return {}
    timed = laps[laps["_t"].notna() & (laps["_t"] > 0)]
    return {int(n): float(t) for n, t in zip(timed["_n"], timed["_t"], strict=False)}


def fastest_lap_number(laps_df: pd.DataFrame | None, driver: str) -> int | None:
    """The driver's quickest timed lap (None when no lap has a time)."""
    times = lap_times(laps_df, driver)
    return min(times, key=lambda n: times[n]) if times else None


def _windows(laps_df: pd.DataFrame | None, driver: str) -> dict[int, tuple[float, float]]:
    """Lap -> (start, end) in seconds from the first lap's start.

    The telemetry ``Time`` of a multi-lap selection counts from its first
    sample, so the lap clocks (session time) are shifted by the first start.
    """
    laps = _driver_laps(laps_df, driver)
    if laps.empty or "Time" not in laps.columns:
        return {}
    end = seconds_series(laps["Time"]).to_numpy(float)
    start = (
        seconds_series(laps["LapStartTime"]).to_numpy(float)
        if "LapStartTime" in laps.columns
        else np.full(len(laps), np.nan)
    )
    duration = laps["_t"].to_numpy(float)
    start = np.where(np.isnan(start), end - duration, start)
    numbers = laps["_n"].to_numpy(float)
    known = ~np.isnan(start) & ~np.isnan(end)
    if not known.any():
        return {}
    origin = start[known][0]
    return {
        int(n): (float(s - origin), float(e - origin))
        for n, s, e, ok in zip(numbers, start, end, known, strict=False)
        if ok
    }


def _slice(telemetry_df: pd.DataFrame, window: tuple[float, float]) -> pd.DataFrame:
    seconds = seconds_series(telemetry_df["Time"]).to_numpy(float)
    inside = (seconds >= window[0]) & (seconds <= window[1])
    lap = telemetry_df[inside]
    if lap.empty:
        return lap
    lap = lap.copy()
    distance = pd.to_numeric(lap["Distance"], errors="coerce")
    lap["Distance"] = distance - distance.iloc[0]
    return lap.reset_index(drop=True)


def available_laps(
    telemetry_df: pd.DataFrame | None, laps_df: pd.DataFrame | None, driver: str, scope: str | None
) -> list[int]:
    """Lap numbers the driver's telemetry can show.

    With ``scope='session'`` that is every timed lap whose window holds enough
    samples; otherwise (fastest-lap scope, live) just the fastest lap, when its
    number is known.
    """
    if telemetry_df is None or telemetry_df.empty or "Distance" not in telemetry_df.columns:
        return []
    if scope != SCOPE_SESSION or "Time" not in telemetry_df.columns:
        fastest = fastest_lap_number(laps_df, driver)
        return [fastest] if fastest is not None else []
    seconds = seconds_series(telemetry_df["Time"]).to_numpy(float)
    seconds = np.sort(seconds[~np.isnan(seconds)])
    found = []
    windows = _windows(laps_df, driver)
    for number in lap_times(laps_df, driver):
        window = windows.get(number)
        if window is None:
            continue
        count = np.searchsorted(seconds, window[1], "right") - np.searchsorted(
            seconds, window[0], "left"
        )
        if count >= MIN_LAP_SAMPLES:
            found.append(number)
    return found


def lap_telemetry(
    telemetry_df: pd.DataFrame | None,
    laps_df: pd.DataFrame | None,
    driver: str,
    lap: int | None,
    scope: str | None,
) -> pd.DataFrame:
    """The telemetry of one lap with Distance restarting at 0 (empty if absent)."""
    if telemetry_df is None or telemetry_df.empty or "Distance" not in telemetry_df.columns:
        return pd.DataFrame()
    if scope != SCOPE_SESSION or "Time" not in telemetry_df.columns:
        return telemetry_df
    window = _windows(laps_df, driver).get(int(lap)) if lap is not None else None
    if window is None:
        return pd.DataFrame()
    return _slice(telemetry_df, window)


def shared_grid(ref_df: pd.DataFrame, cmp_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Both laps on the same 5 m distance grid, cut to the shorter lap.

    Speed/throttle/RPM are interpolated; brake and gear take the nearest
    sample (coded values). Brake and throttle come back as 0-100 and the gear
    as a whole number in ``nGear``.
    """
    processor = TelemetryProcessor()
    frames = []
    for df in (ref_df, cmp_df):
        grid = processor.normalize_units(processor.resample_to_distance_grid(df))
        if "nGear" in grid.columns:
            grid["nGear"] = pd.to_numeric(grid["nGear"], errors="coerce").round()
        frames.append(grid)
    rows = min(len(frames[0]), len(frames[1]))
    return frames[0].iloc[:rows].reset_index(drop=True), frames[1].iloc[:rows].reset_index(
        drop=True
    )


def corner_markers(circuit_info: dict | None, lap_length: float) -> list[tuple[float, str]]:
    """``(distance m, label)`` per corner that lies on the compared distance."""
    corners = (circuit_info or {}).get("corners")
    if corners is None or len(corners) == 0:
        return []
    frame = pd.DataFrame(corners)
    if "Distance" not in frame.columns:
        return []
    found = []
    for _, corner in frame.iterrows():
        distance = pd.to_numeric(corner.get("Distance"), errors="coerce")
        if pd.isna(distance) or not 0 <= distance <= lap_length:
            continue
        number = corner.get("Number")
        letter = corner.get("Letter") or ""
        label = f"{int(number)}{letter}" if pd.notna(number) else str(letter)
        found.append((float(distance), label))
    return found
