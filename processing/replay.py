"""Session playback over time (IMPROVEMENTS.md FEAT-04, REPLAY-01).

The replay source reloads a saved session, but a session is a *sequence*: to
watch what happened you need to ask "where was everyone at time t", and that
needs position data spanning the whole session on a shared clock.

Positions are resampled onto one regular grid for every driver and packed
into a :class:`PositionCube`, so a lookup is an index computation rather than
a search, and two cars at the same moment really are at the same moment.
:class:`ReplayClock` says where a replay starts, where the racing starts
(lights out) and where it ends. Everything here is pure: no Streamlit, no
network.
"""

import math
import weakref
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from processing.time_utils import seconds_series
from processing.timing import is_race_session

# Position data arrives at roughly 4 Hz. Half-second steps track the cars
# closely while keeping a two-hour race to a few thousand frames per driver.
DEFAULT_STEP_SECONDS = 0.5

# Longer silences than this are not bridged by interpolation: a car in the
# garage (qualifying, practice, a red flag) is absent, not gliding down the
# pit lane between the moment it went in and the moment it came out.
MAX_SAMPLE_GAP_SECONDS = 3.0

# The replay runs on a little past the last lap so the final crossings and
# the chequered flag are on screen, not the last frame.
END_PADDING_SECONDS = 60.0

TIMELINE_COLUMNS = ["Time", "Driver", "X", "Y"]


@dataclass(frozen=True)
class ReplayClock:
    """Where a replay starts, where the racing starts, and where it ends.

    All values are seconds of session time (the origin FastF1's ``Time``,
    ``SessionTime`` and ``LapStartTime`` share).
    """

    start: float
    lights_out: float
    end: float
    step: float = DEFAULT_STEP_SECONDS

    def clamp(self, moment: float) -> float:
        """``moment`` limited to the replay window."""
        return float(min(max(float(moment), self.start), self.end))

    def to_dict(self) -> dict:
        """Plain floats, so the clock can travel in a saved replay's JSON."""
        return {key: float(value) for key, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, value) -> "ReplayClock | None":
        """Inverse of :meth:`to_dict`; None for anything that is not a clock."""
        if isinstance(value, ReplayClock):
            return value
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                start=float(value["start"]),
                lights_out=float(value["lights_out"]),
                end=float(value["end"]),
                step=float(value.get("step", DEFAULT_STEP_SECONDS)),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _lap_seconds(laps: pd.DataFrame | None, column: str) -> pd.Series:
    """One lap column as float seconds (NaN where missing)."""
    if laps is None or laps.empty or column not in laps.columns:
        return pd.Series([], dtype="float64")
    return seconds_series(laps[column].reset_index(drop=True))


def replay_clock(
    laps: pd.DataFrame | None,
    timeline: pd.DataFrame | None,
    session_type: str | None,
    session_start: float | None = None,
) -> ReplayClock:
    """The replay window for one session.

    ``lights_out`` is the start of lap 1 in a race or sprint (the earliest
    non-NaT ``LapStartTime`` of lap 1 - one driver's missing value does not
    move it). Other sessions start at ``session_start``, FastF1's
    ``session_start_time``; without it, at the first lap anyone started.
    ``end`` is the last lap plus :data:`END_PADDING_SECONDS`, capped at the
    end of the position timeline.
    """
    t_start, t_end = timeline_bounds(timeline)
    has_timeline = timeline is not None and not timeline.empty

    lap_starts = _lap_seconds(laps, "LapStartTime")
    lights_out = None
    racing = is_race_session({"session_type": session_type})
    if racing and laps is not None and not lap_starts.empty and "LapNumber" in laps.columns:
        first_laps = (pd.to_numeric(laps["LapNumber"], errors="coerce") == 1).to_numpy()
        candidates = lap_starts[first_laps].dropna()
        if not candidates.empty:
            lights_out = float(candidates.min())
    if lights_out is None and session_start is not None and not pd.isna(session_start):
        lights_out = float(session_start)
    if lights_out is None and not lap_starts.dropna().empty:
        lights_out = float(lap_starts.dropna().min())
    if lights_out is None:
        lights_out = t_start

    lap_ends = _lap_seconds(laps, "Time").dropna()
    if not lap_ends.empty:
        end = float(lap_ends.max()) + END_PADDING_SECONDS
        if has_timeline:
            end = min(end, t_end)
    else:
        end = t_end if has_timeline else lights_out

    start = min(t_start, lights_out) if has_timeline else lights_out
    end = max(end, lights_out)
    return ReplayClock(start=float(start), lights_out=float(lights_out), end=float(end))


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
            "t": seconds_series(frame[time_column].reset_index(drop=True)),
            "X": pd.to_numeric(frame["X"], errors="coerce").reset_index(drop=True),
            "Y": pd.to_numeric(frame["Y"], errors="coerce").reset_index(drop=True),
        }
    )
    if "Status" in frame.columns:
        status = frame["Status"].reset_index(drop=True)
        work = work[status.isna() | (status.astype(str) == "OnTrack")]

    work = work.dropna()
    work = work[~((work["X"] == 0) & (work["Y"] == 0))]
    return work.sort_values("t").drop_duplicates("t").reset_index(drop=True)


def build_position_timeline(
    per_driver: dict[str, pd.DataFrame], step_seconds: float = DEFAULT_STEP_SECONDS
) -> pd.DataFrame:
    """Every driver's position on one shared time grid.

    ``per_driver`` maps a driver code to raw position data carrying
    ``SessionTime`` and ``X``/``Y``. Returns a tidy frame of
    ``Time, Driver, X, Y``; a driver contributes no rows outside their own
    first and last sample, nor inside a silence longer than
    :data:`MAX_SAMPLE_GAP_SECONDS`, so a car that has not left the pits is
    simply absent rather than parked at an interpolated guess.
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
        # ... and not across a long silence (the garage).
        following = np.searchsorted(times, grid, side="right")
        before = np.clip(following - 1, 0, len(times) - 1)
        after = np.clip(following, 0, len(times) - 1)
        on_a_sample = np.abs(times[before] - grid) < 1e-6
        covered &= on_a_sample | (times[after] - times[before] <= MAX_SAMPLE_GAP_SECONDS)
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


def timeline_bounds(timeline: pd.DataFrame | None) -> tuple[float, float]:
    """First and last moment the timeline covers, in seconds."""
    if timeline is None or timeline.empty:
        return (0.0, 0.0)
    return (float(timeline["Time"].min()), float(timeline["Time"].max()))


@dataclass(frozen=True)
class PositionCube:
    """Every car's position on a regular grid, as one ``(F, D, 2)`` array.

    ``xy[i, d]`` is driver ``codes[d]`` at ``t0 + i * step``; NaN where the
    driver has no sample. Built once per session, so looking a moment up is
    two row reads and a blend rather than a scan of the tidy timeline.
    """

    t0: float
    step: float
    codes: tuple[str, ...]
    xy: np.ndarray

    @property
    def frames(self) -> int:
        return int(self.xy.shape[0])


def build_position_cube(
    timeline: pd.DataFrame | None, step_seconds: float = DEFAULT_STEP_SECONDS
) -> PositionCube:
    """Pack a tidy timeline (``Time, Driver, X, Y``) into a :class:`PositionCube`."""
    if timeline is None or timeline.empty:
        return PositionCube(0.0, step_seconds, (), np.empty((0, 0, 2), dtype="float32"))

    times = pd.to_numeric(timeline["Time"], errors="coerce").to_numpy(float)
    t0 = float(np.nanmin(times))
    frame_index = np.rint((times - t0) / step_seconds).astype(int)
    drivers = pd.Categorical(timeline["Driver"].astype(str))
    codes = tuple(str(code) for code in drivers.categories)

    xy = np.full((int(frame_index.max()) + 1, len(codes), 2), np.nan, dtype="float32")
    xy[frame_index, drivers.codes, 0] = pd.to_numeric(timeline["X"], errors="coerce")
    xy[frame_index, drivers.codes, 1] = pd.to_numeric(timeline["Y"], errors="coerce")
    return PositionCube(t0, float(step_seconds), codes, xy)


# Tidy timelines handed to positions_at() directly get their cube built once
# and remembered for as long as the frame itself lives.
_CUBES: "weakref.WeakKeyDictionary[pd.DataFrame, PositionCube]" = weakref.WeakKeyDictionary()


def position_cube(source: "PositionCube | pd.DataFrame | None") -> PositionCube:
    """The cube for ``source``: itself, or the (cached) cube of a timeline."""
    if isinstance(source, PositionCube):
        return source
    if source is None or source.empty:
        return build_position_cube(None)
    try:
        cached = _CUBES.get(source)
    except TypeError:  # unhashable frame subclass
        return build_position_cube(source)
    if cached is None:
        cached = build_position_cube(source)
        _CUBES[source] = cached
    return cached


def positions_at(source: "PositionCube | pd.DataFrame | None", moment: float) -> list[dict]:
    """Every car's position at ``moment``, interpolated between grid frames.

    Returns marker dicts (``code``/``x``/``y``) in the shape
    :func:`ui.track_map.build_track_svg` expects. Accepts a cube or the tidy
    timeline (whose cube is built once and cached).
    """
    cube = position_cube(source)
    if cube.frames == 0 or not cube.codes:
        return []

    index = (float(moment) - cube.t0) / cube.step
    if index < -1e-9 or index > cube.frames - 1 + 1e-9:
        return []  # asking outside the session
    index = min(max(index, 0.0), cube.frames - 1)
    low = math.floor(index)
    high = min(low + 1, cube.frames - 1)
    weight = index - low

    before, after = cube.xy[low], cube.xy[high]
    blended = before * (1.0 - weight) + after * weight
    # At the edge of a driver's window one side is missing: use the nearer
    # real sample rather than dropping the car for half a frame.
    nearest = before if weight < 0.5 else after
    point = np.where(np.isnan(blended), nearest, blended)

    return [
        {"code": code, "x": float(point[d, 0]), "y": float(point[d, 1])}
        for d, code in enumerate(cube.codes)
        if not np.isnan(point[d, 0])
    ]


def format_clock(seconds: float) -> str:
    """Session clock ``H:MM:SS`` (UI guideline 5.7); negative values clamp to 0."""
    total = max(round(float(seconds)), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"
