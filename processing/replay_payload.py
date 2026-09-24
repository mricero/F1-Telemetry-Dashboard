"""The browser replay player's payload (IMPROVEMENTS.md REPLAY-05).

One JSON-safe dict carries everything the player needs to animate a whole
session without asking Python again: the clock, the track outline, every
car's position (projected into the map's viewBox with the same transform as
the server-drawn map, quantised to Int16 and base64-encoded), each tower
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

import numpy as np
import pandas as pd

from processing.replay import ReplayClock, build_position_cube
from processing.replay_model import (
    FIELD_DEFAULTS,
    SEGMENT_NAMES,
    TowerSeries,
    _event_seconds,
    events,
    flag_timeline,
)
from processing.time_utils import seconds_series
from processing.track_geometry import VIEW_H, VIEW_W, path_from, track_geometry

PAYLOAD_VERSION = 1

# Position frames are stored in viewBox units x10 as little-endian Int16, so
# the largest value (10 000) fits comfortably; this marks a car with no sample.
POSITION_SCALE = 10
POSITION_ABSENT = -32768

# Series field name in the model -> short name in the payload.
TOWER_FIELDS = {
    "position": "pos",
    "gap": "gap",
    "interval": "int",
    "lap": "lap",
    "last": "last",
    "last_flag": "last_flag",
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
}

MS_TO_KMH = 3.6

# An interval under this is overtaking range: the tower shows it brighter.
CLOSE_INTERVAL_SECONDS = 1.0


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
    packed = np.clip(np.rint(projected), -32767, 32767).astype("<i2")
    packed[absent] = POSITION_ABSENT
    return {
        "t0": round(cube.t0, 3),
        "step": cube.step,
        "frames": cube.frames,
        "drivers": len(cube.codes),
        "codes": list(cube.codes),
        "scale": POSITION_SCALE,
        "absent": POSITION_ABSENT,
        "xy_b64": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


def decode_positions(pos: dict) -> np.ndarray:
    """``xy_b64`` back to an ``(F, D, 2)`` float array in viewBox units (NaN absent).

    What the player's JavaScript does, for tests and tools.
    """
    raw = np.frombuffer(base64.b64decode(pos["xy_b64"]), dtype="<i2")
    xy = raw.reshape(pos["frames"], pos["drivers"], 2).astype(float)
    xy[xy == pos["absent"]] = np.nan
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
            [round(start, 3), SEGMENT_NAMES[index]]
            for index, start in enumerate(series.segment_starts[: len(SEGMENT_NAMES)])
        ],
        "estimated": bool(series.estimated),
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
