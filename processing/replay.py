"""Session playback over time (IMPROVEMENTS.md FEAT-04).

The replay source reloads a saved session, but a session is a *sequence*: to
watch what happened you need to ask "where was everyone at time t", and that
needs position data spanning the whole session on a shared clock.

Positions are resampled onto one regular grid for every driver, so scrubbing
is an index lookup rather than a search, and two cars at the same slider
position really are at the same moment. Everything here is pure: no
Streamlit, no network.
"""

import numpy as np
import pandas as pd

from processing.time_utils import to_seconds

# Position data arrives at roughly 4 Hz. Half-second steps track the cars
# closely while keeping a two-hour race to a few thousand frames per driver.
DEFAULT_STEP_SECONDS = 0.5

TIMELINE_COLUMNS = ["Time", "Driver", "X", "Y"]


def _usable_positions(frame: pd.DataFrame) -> pd.DataFrame:
    """On-track samples with a parsable time, sorted.

    Cars in the garage report ``0,0,0`` and entries carry a ``Status``;
    keeping either drags the marker to the origin (same rule as LIVE-14).
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    if not {"X", "Y"}.issubset(frame.columns):
        return pd.DataFrame()

    time_column = "SessionTime" if "SessionTime" in frame.columns else "Time"
    if time_column not in frame.columns:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "t": pd.Series([to_seconds(value) for value in frame[time_column]], dtype="float64"),
            "X": pd.to_numeric(frame["X"], errors="coerce").reset_index(drop=True),
            "Y": pd.to_numeric(frame["Y"], errors="coerce").reset_index(drop=True),
        }
    )
    if "Status" in frame.columns:
        status = frame["Status"].reset_index(drop=True)
        work = work[status.isna() | (status.astype(str) == "OnTrack")]

    work = work.dropna()
    work = work[~((work["X"] == 0) & (work["Y"] == 0))]
    return work.sort_values("t").reset_index(drop=True)


def build_position_timeline(
    per_driver: dict[str, pd.DataFrame], step_seconds: float = DEFAULT_STEP_SECONDS
) -> pd.DataFrame:
    """Every driver's position on one shared time grid.

    ``per_driver`` maps a driver code to raw position data carrying
    ``SessionTime`` and ``X``/``Y``. Returns a tidy frame of
    ``Time, Driver, X, Y``; a driver contributes no rows outside their own
    first and last sample, so a car that has not left the pits is simply
    absent rather than parked at an interpolated guess.
    """
    cleaned = {}
    for code, frame in (per_driver or {}).items():
        usable = _usable_positions(frame)
        if len(usable) >= 2:
            cleaned[code] = usable
    if not cleaned:
        return pd.DataFrame(columns=TIMELINE_COLUMNS)

    start = min(frame["t"].iloc[0] for frame in cleaned.values())
    end = max(frame["t"].iloc[-1] for frame in cleaned.values())
    if end <= start:
        return pd.DataFrame(columns=TIMELINE_COLUMNS)

    steps = int(np.floor((end - start) / step_seconds)) + 1
    grid = start + np.arange(steps) * step_seconds

    parts = []
    for code, frame in cleaned.items():
        times = frame["t"].to_numpy()
        # Only the span this driver actually reported; np.interp would
        # otherwise hold the first and last sample flat across the session.
        covered = (grid >= times[0]) & (grid <= times[-1])
        if not covered.any():
            continue
        window = grid[covered]
        parts.append(
            pd.DataFrame(
                {
                    "Time": window,
                    "Driver": code,
                    "X": np.interp(window, times, frame["X"].to_numpy()),
                    "Y": np.interp(window, times, frame["Y"].to_numpy()),
                }
            )
        )

    if not parts:
        return pd.DataFrame(columns=TIMELINE_COLUMNS)
    timeline = pd.concat(parts, ignore_index=True)
    return timeline[TIMELINE_COLUMNS].sort_values(["Time", "Driver"]).reset_index(drop=True)


def timeline_bounds(timeline: pd.DataFrame) -> tuple[float, float]:
    """First and last moment the timeline covers, in seconds."""
    if timeline is None or timeline.empty:
        return (0.0, 0.0)
    return (float(timeline["Time"].min()), float(timeline["Time"].max()))


def positions_at(timeline: pd.DataFrame, moment: float) -> list[dict]:
    """Every car's position at the grid time nearest ``moment``.

    Returns marker dicts (``code``/``x``/``y``) in the shape
    :func:`ui.track_map.build_track_svg` expects.
    """
    if timeline is None or timeline.empty:
        return []

    times = timeline["Time"].to_numpy()
    nearest = times[np.abs(times - float(moment)).argmin()]
    if abs(nearest - float(moment)) > 5.0:
        return []  # asking well outside the session

    frame = timeline[timeline["Time"] == nearest]
    return [
        {"code": str(row.Driver), "x": float(row.X), "y": float(row.Y)}
        for row in frame.itertuples()
    ]


def _laps_completed_by(laps: pd.DataFrame, moment: float) -> pd.DataFrame:
    """Laps whose end time has passed at ``moment``."""
    if laps is None or laps.empty or "Time" not in laps.columns:
        return pd.DataFrame()
    seconds = laps["Time"].map(to_seconds)
    return laps[seconds.notna() & (seconds <= float(moment))]


def order_at(laps: pd.DataFrame, moment: float) -> list[dict]:
    """The running order as it stood at ``moment``.

    Taken from each driver's most recently completed lap, which is how a
    timing screen knows the order between two timing lines.
    """
    done = _laps_completed_by(laps, moment)
    if done.empty or "Driver" not in done.columns:
        return []

    latest = done.sort_values("LapNumber").groupby("Driver", sort=False).last().reset_index()
    if "Position" in latest.columns:
        latest = latest.sort_values("Position", na_position="last")
    else:  # no on-road position recorded: most laps first
        latest = latest.sort_values("LapNumber", ascending=False)

    order = []
    for index, row in enumerate(latest.itertuples(), start=1):
        position = getattr(row, "Position", None)
        order.append(
            {
                "code": str(row.Driver),
                "position": int(position) if position is not None and pd.notna(position) else index,
                "lap": int(row.LapNumber) if pd.notna(row.LapNumber) else None,
            }
        )
    return order


def lap_at(laps: pd.DataFrame, moment: float) -> int:
    """The leader's lap number at ``moment`` (1 before anyone has finished one)."""
    done = _laps_completed_by(laps, moment)
    if done.empty or "LapNumber" not in done.columns:
        return 1
    return int(pd.to_numeric(done["LapNumber"], errors="coerce").max())


def format_clock(seconds: float) -> str:
    """Session clock: ``M:SS`` under an hour, ``H:MM:SS`` beyond it."""
    total = round(float(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
