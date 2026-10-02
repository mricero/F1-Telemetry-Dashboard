"""The browser replay player's payload (IMPROVEMENTS.md REPLAY-05).

One JSON-safe dict carries everything the player needs to animate a whole
session without asking Python again: the clock, the track outline, every
car's position (projected into the map's viewBox with the same transform as
the server-drawn map, quantised and packed: ``_pack_positions``), each tower
field as its own change-point series (display strings already formatted),
flags, race control, weather, lap marks and events.

Racing semantics stay in Python: the player only looks values up (binary
search over each series, linear interpolation between position frames) and
draws. Styling (team, flag and compound colours) is added by the UI layer.
Pure: no Streamlit, no network.
"""

import base64
import bisect
import math
import zlib

import numpy as np
import pandas as pd

from processing.replay import ReplayClock, build_position_cube
from processing.replay_model import (
    FIELD_DEFAULTS,
    TowerSeries,
    _event_seconds,
    events,
    flag_timeline,
)
from processing.time_utils import seconds_series
from processing.track_geometry import VIEW_H, VIEW_W, path_from, track_geometry

# 2: packed positions (``xy_z``) and interval trend (``trend.z``), REPLAY-29.
PAYLOAD_VERSION = 2

# Car positions are quantised to 1/POSITION_SCALE viewBox units (0.2 units:
# a fifth of a pixel on a 1000-unit-wide map, under 2 % of a car marker's
# radius), then packed compactly (REPLAY-29, see ``_pack_positions``).
POSITION_SCALE = 5
# Quantised coordinates are clipped to this, so every second difference
# fits a 16-bit zigzag code; the viewBox is 0..1000 x 0..760.
POSITION_LIMIT = 6000
# A car with no sample in the decoded Int16 frames (never a real value).
POSITION_ABSENT = -32768
POSITION_ENCODING = "dd-zigzag-shuffle-deflate"

# Series field name in the model -> short name in the payload.
TOWER_FIELDS = {
    "position": "pos",
    "gap": "gap",
    "interval": "int",
    "lap": "lap",
    "last": "last",
    "last_flag": "last_flag",
    # The last lap was deleted (True) and the stewards' reason (REPLAY-18).
    "last_deleted": "last_del",
    "last_deleted_reason": "last_del_why",
    "best": "best",
    "tyre": "tyre",
    "age": "age",
    "new": "new",
    "pits": "pits",
    "status": "status",
    "s1": "s1",
    "s2": "s2",
    "s3": "s3",
    "partition": "partition",
    "flying": "flying",
}

MS_TO_KMH = 3.6

# An interval under this is overtaking range: the tower shows it brighter.
CLOSE_INTERVAL_SECONDS = 1.0

# The focused-driver card's gap trend: the interval to the car ahead,
# sampled this often (REPLAY-10), in hundredths of a second, with
# TREND_MISSING for "no interval" (REPLAY-29).
TREND_STEP_SECONDS = 5.0
TREND_SCALE = 100
TREND_MISSING = 65535


def _json(value):
    """A value JSON can carry: NaN/NA -> None, numpy -> Python, floats rounded."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 3)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _series_json(series, floor: float) -> list[list]:
    """``[[t...], [v...]]`` with the before-everything sentinel clamped."""
    times = [round(max(float(t), floor), 3) for t in series.t.tolist()]
    return [times, [_json(value) for value in series.v]]


def _positions(session_data: dict, geometry, clock: ReplayClock) -> dict | None:
    timeline = session_data.get("positions")
    if geometry is None or timeline is None or timeline.empty:
        return None
    times = pd.to_numeric(timeline["Time"], errors="coerce")
    window = timeline[(times >= clock.start - 1) & (times <= clock.end + 1)]
    cube = build_position_cube(window, clock.step)
    if cube.frames == 0:
        return None
    flat = cube.xy.reshape(-1, 2).astype(float)
    absent = np.isnan(flat).any(axis=1)
    projected = geometry.project(np.nan_to_num(flat)) * POSITION_SCALE
    quantised = np.clip(np.rint(projected), -POSITION_LIMIT, POSITION_LIMIT).astype(np.int64)
    frames, drivers = cube.frames, len(cube.codes)
    return {
        "t0": round(cube.t0, 3),
        "step": cube.step,
        "frames": frames,
        "drivers": drivers,
        "codes": list(cube.codes),
        "scale": POSITION_SCALE,
        "absent": POSITION_ABSENT,
        "encoding": POSITION_ENCODING,
        "xy_z": _pack_positions(
            quantised.reshape(frames, drivers, 2), absent.reshape(frames, drivers)
        ),
    }


def _deflate(raw: bytes) -> str:
    """Raw DEFLATE (no zlib header, as ``DecompressionStream("deflate-raw")``
    reads it), base64-encoded for JSON."""
    packer = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS, 9)
    return base64.b64encode(packer.compress(raw) + packer.flush()).decode("ascii")


def _inflate(text: str) -> bytes:
    return zlib.decompress(base64.b64decode(text), -zlib.MAX_WBITS)


def _pack_positions(quantised: np.ndarray, absent: np.ndarray) -> str:
    """``(F, D, 2)`` quantised coordinates -> the compact ``xy_z`` string (REPLAY-29).

    Raw Int16 frames were 1.2-1.6 MB of base64 for a race. Cars move
    smoothly, so the second difference along time of each coordinate is
    small; DEFLATE packs those far better than the coordinates themselves.
    The inflated bytes are, with ``N = drivers * frames``:

    1. ``ceil(N / 8)`` bytes: the absent flags, driver-major (``d * F + f``),
       most significant bit first (``numpy.packbits``);
    2. ``2N`` low bytes, then 3. ``2N`` high bytes, of 16-bit zigzag codes of
       the second differences, ordered driver, coordinate (x then y), frame.

    An absent frame repeats the car's last value, so it costs nothing.
    """
    frames, drivers, _ = quantised.shape
    index = np.where(~absent, np.arange(frames)[:, None], 0)
    held = np.take_along_axis(quantised, np.maximum.accumulate(index, axis=0)[..., None], axis=0)
    planar = np.ascontiguousarray(held.transpose(1, 2, 0))  # driver, coordinate, frame
    second = np.diff(np.diff(planar, axis=2, prepend=0), axis=2, prepend=0)
    codes = ((second << 1) ^ (second >> 63)).astype("<u2").reshape(-1)
    flags = np.packbits(np.ascontiguousarray(absent.T).reshape(-1))
    low = (codes & 0xFF).astype(np.uint8)
    high = (codes >> 8).astype(np.uint8)
    return _deflate(flags.tobytes() + low.tobytes() + high.tobytes())


def decode_positions(pos: dict) -> np.ndarray:
    """``xy_z`` back to an ``(F, D, 2)`` float array in viewBox units (NaN absent).

    What the player's JavaScript does, for tests and tools.
    """
    frames, drivers = pos["frames"], pos["drivers"]
    count = frames * drivers
    raw = np.frombuffer(_inflate(pos["xy_z"]), dtype=np.uint8)
    flag_bytes = (count + 7) // 8
    absent = np.unpackbits(raw[:flag_bytes])[:count].astype(bool).reshape(drivers, frames)
    low = raw[flag_bytes : flag_bytes + 2 * count].astype(np.int64)
    high = raw[flag_bytes + 2 * count : flag_bytes + 4 * count].astype(np.int64)
    codes = low | (high << 8)
    second = (codes >> 1) ^ -(codes & 1)
    planar = np.cumsum(np.cumsum(second.reshape(drivers, 2, frames), axis=2), axis=2)
    xy = planar.transpose(2, 0, 1).astype(float)  # frame, driver, coordinate
    xy[absent.T] = np.nan
    return xy / pos["scale"]


def _race_control(session_data: dict) -> list[list]:
    control = session_data.get("race_control")
    if control is None or control.empty:
        return []
    stamps = _event_seconds(control)
    rows = []
    for moment, (_, row) in zip(stamps.tolist(), control.iterrows(), strict=True):
        if math.isnan(moment):
            continue
        lap = pd.to_numeric(pd.Series([row.get("Lap")]), errors="coerce").iloc[0]
        rows.append(
            [
                round(moment, 3),
                None if pd.isna(lap) else int(lap),
                str(_json(row.get("Category")) or ""),
                str(_json(row.get("Flag")) or ""),
                str(_json(row.get("Message")) or ""),
            ]
        )
    return sorted(rows, key=lambda item: item[0])


def _number(value):
    return _json(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])


def _weather(session_data: dict) -> list[list]:
    weather = session_data.get("weather")
    if weather is None or weather.empty or "Time" not in weather.columns:
        return []
    frame = weather.reset_index(drop=True)
    times = seconds_series(frame["Time"]).tolist()
    rows = []
    for moment, (_, row) in zip(times, frame.iterrows(), strict=True):
        if moment is None or math.isnan(moment):
            continue
        wind = _number(row.get("WindSpeed"))
        rain = row.get("Rainfall")
        rows.append(
            [
                round(float(moment), 3),
                _number(row.get("AirTemp")),
                _number(row.get("TrackTemp")),
                _number(row.get("Humidity")),
                bool(rain) if rain is not None and pd.notna(rain) else False,
                None if wind is None else round(float(wind) * MS_TO_KMH, 1),
                _number(row.get("WindDirection")),
            ]
        )
    return rows


def _track(geometry, circuit_info: dict | None) -> dict | None:
    if geometry is None:
        return None
    return {
        "view": [VIEW_W, VIEW_H],
        "path": path_from(geometry.outline),
        "sf": [round(value, 2) for value in geometry.start_finish()],
        "corners": [
            {"x": round(corner["lx"], 1), "y": round(corner["ly"], 1), "label": corner["label"]}
            for corner in geometry.corners(circuit_info)
        ],
    }


def _drivers(session_data: dict, series: TowerSeries) -> list[dict]:
    table = session_data.get("drivers")
    meta: dict[str, dict] = {}
    if table is not None and not table.empty and "name_acronym" in table.columns:
        for record in table.to_dict("records"):
            meta[str(record.get("name_acronym"))] = record
    drivers = []
    for code in series.drivers:
        record = meta.get(code, {})
        drivers.append(
            {
                "code": code,
                "number": str(_json(record.get("driver_number")) or ""),
                "name": str(_json(record.get("full_name")) or code),
                "team": str(_json(record.get("team_name")) or ""),
                "team_colour": str(_json(record.get("team_colour")) or ""),
            }
        )
    return drivers


def _driver_laps(session_data: dict, series: TowerSeries) -> dict[str, list[list]]:
    """Every completed lap per driver: ``[time, lap, display, flag]``."""
    from processing.replay_model import format_laptime, session_lap_table

    table = session_lap_table(session_data)
    found: dict[str, list[list]] = {code: [] for code in series.drivers}
    completed = table[table["end"].notna()].sort_values("end", kind="stable")
    for lap in completed.itertuples():
        if lap.Driver not in found:
            continue
        flag = series.value(lap.Driver, "last_flag", float(lap.end))
        number = None if pd.isna(lap.LapNumber) else int(lap.LapNumber)
        found[lap.Driver].append(
            [round(float(lap.end), 3), number, format_laptime(lap.lap_s), flag]
        )
    return found


def _interval_trend(series: TowerSeries, clock: ReplayClock) -> dict:
    """The interval to the car ahead every few seconds, for the card's sparkline.

    Sampled in Python (the player only looks the samples up) and packed
    (REPLAY-29): hundredths of a second as little-endian UInt16, one row of
    ``samples`` per driver in ``codes`` order, ``TREND_MISSING`` where there
    is no interval (no timing yet, or leading - REPLAY-21), raw DEFLATE and
    base64 in ``z``. As JSON lists it was ~160-200 KB for a race.
    """
    moments = np.arange(clock.start, clock.end + TREND_STEP_SECONDS, TREND_STEP_SECONDS)
    codes, rows = [], []
    for code in series.drivers:
        field = series.fields.get(code, {}).get("interval_s")
        if field is None or len(field) == 0:
            continue
        index = np.searchsorted(field.t, moments, side="right") - 1
        picked = [field.v[i] if i >= 0 else None for i in index.tolist()]
        values = np.array([np.nan if _json(v) is None else float(v) for v in picked])
        scaled = np.clip(np.rint(values * TREND_SCALE), 0, TREND_MISSING - 1)
        rows.append(np.where(np.isnan(values), TREND_MISSING, scaled).astype("<u2"))
        codes.append(code)
    packed = np.concatenate(rows).tobytes() if rows else b""
    return {
        "t0": round(float(clock.start), 3),
        "step": TREND_STEP_SECONDS,
        "samples": len(moments),
        "codes": codes,
        "scale": TREND_SCALE,
        "missing": TREND_MISSING,
        "z": _deflate(packed),
    }


def decode_trend(trend: dict) -> dict[str, list[float | None]]:
    """``trend["z"]`` back to seconds per driver (None where missing), as the player reads it."""
    raw = np.frombuffer(_inflate(trend["z"]), dtype="<u2")
    rows = raw.reshape(len(trend["codes"]), trend["samples"]) if trend["codes"] else raw
    return {
        code: [None if v == trend["missing"] else v / trend["scale"] for v in row.tolist()]
        for code, row in zip(trend["codes"], rows, strict=True)
    }


def _close_series(interval):
    """Whether the car is within a second of the one ahead, as a series."""
    from processing.replay_model import _series

    return _series(
        (t, value is not None and 0 <= value < CLOSE_INTERVAL_SECONDS)
        for t, value in zip(interval.t.tolist(), interval.v, strict=True)
    )


def build_replay_payload(
    session_data: dict, series: TowerSeries, clock: ReplayClock, session_key: str
) -> dict:
    """Everything the browser player needs for one session, JSON-safe."""
    info = session_data.get("session_info") or {}
    location = session_data.get("dashboard_location") or session_data.get("location") or {}
    geometry = track_geometry(location, session_data.get("circuit_info"))
    floor = clock.start - 1.0

    tower = {
        code: {
            short: _series_json(series.fields[code][name], floor)
            for name, short in TOWER_FIELDS.items()
            if name in series.fields.get(code, {})
        }
        for code in series.drivers
    }
    for code in series.drivers:
        interval = series.fields.get(code, {}).get("interval_s")
        if interval is not None:
            tower[code]["close"] = _series_json(_close_series(interval), floor)
    leader = series.leader_lap
    return {
        "v": PAYLOAD_VERSION,
        "session_key": session_key,
        "session": {
            "event": str(info.get("gp") or "Session"),
            "year": _json(info.get("year")),
            "name": str(info.get("session_name") or info.get("session_type") or ""),
            "kind": series.kind,
        },
        "clock": {
            "start": round(clock.start, 3),
            "lights_out": round(clock.lights_out, 3),
            "end": round(clock.end, 3),
            "step": clock.step,
            "total_laps": series.total_laps,
        },
        "track": _track(geometry, session_data.get("circuit_info")),
        "drivers": _drivers(session_data, series),
        "pos": _positions(session_data, geometry, clock),
        "tower": tower,
        "defaults": {
            **{short: _json(FIELD_DEFAULTS.get(name)) for name, short in TOWER_FIELDS.items()},
            "close": False,
        },
        "flags": [[round(t, 3), state] for t, state in flag_timeline(session_data)],
        "rcm": _race_control(session_data),
        "weather": _weather(session_data),
        "leader_laps": _series_json(leader, floor) if len(leader) else [[], []],
        "events": [[round(t, 3), kind, label] for t, kind, label in events(session_data, series)],
        "segments": [
            [round(start, 3), series.segment_names[index]]
            for index, start in enumerate(series.segment_starts[: len(series.segment_names)])
        ],
        "estimated": bool(series.estimated),
        "laps": _driver_laps(session_data, series),
        "trend": _interval_trend(series, clock),
    }


def tower_at(payload: dict, moment: float) -> list[dict]:
    """The tower as the player computes it at ``moment``, in running order.

    A Python mirror of the JavaScript lookup (binary search per field, then
    sort by position with the payload's driver order breaking ties), so
    tests can hold the two to the same answer.
    """
    rows = []
    for hint, driver in enumerate(payload["drivers"]):
        row = {"code": driver["code"], "hint": hint}
        for short, default in payload["defaults"].items():
            times, values = payload["tower"][driver["code"]].get(short, [[], []])
            index = bisect.bisect_right(times, moment) - 1
            row[short] = values[index] if index >= 0 else default
        rows.append(row)
    rows.sort(key=lambda r: (r["pos"] is None, r["pos"] or 0, r["hint"]))
    return rows
