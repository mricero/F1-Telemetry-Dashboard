"""Synthetic sessions for the replay model (IMPROVEMENTS.md REPLAY-03).

Shaped like what ``DataSourceManager._load_fastf1_session`` returns: laps
with Timedelta session times, a timing stream in the adapter's columns
(float seconds, acronyms, gap strings in FastF1's real forms), race control
with a wall-clock ``Time`` plus ``SessionTime``, track status and weather on
the session clock, and a 0.5 s position timeline.

The race: three cars, five laps. A leads from pole, B passes A on lap 3,
C pits at the end of lap 4 (in 1350, out 1380, softs -> hards) and A stops
on track during lap 5. B takes the flag at 1450; the result is B, C, A.
"""

import numpy as np
import pandas as pd

from processing.replay import build_position_timeline, replay_clock
from processing.time_utils import parse_gap

LIGHTS_OUT = 1000.0
RACE_END_BY = {"A": 1400.0, "B": 1450.0, "C": 1470.0}  # last position sample

# Lap end times (session seconds); A never completes lap 5.
RACE_LAP_ENDS = {
    "A": [1090.0, 1180.0, 1271.0, 1361.0, np.nan],
    "B": [1091.0, 1181.0, 1270.5, 1360.5, 1450.0],
    "C": [1092.0, 1182.0, 1273.0, 1364.0, 1470.0],
}
C_PIT_IN, C_PIT_OUT = 1350.0, 1380.0
SC_START, SC_END = 1290.0, 1330.0
TEAM = {"A": ("Alpha", "#3671c6"), "B": ("Beta", "#e80020"), "C": ("Gamma", "#229971")}

TIME_COLUMNS = (
    "LapTime",
    "Time",
    "LapStartTime",
    "Sector1Time",
    "Sector2Time",
    "Sector3Time",
    "Sector1SessionTime",
    "Sector2SessionTime",
    "Sector3SessionTime",
    "PitInTime",
    "PitOutTime",
)


def _td(values) -> pd.Series:
    return pd.to_timedelta(pd.Series(values, dtype="float64"), unit="s")


def _drivers(codes) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "driver_number": [str(10 + index) for index, _ in enumerate(codes)],
            "name_acronym": list(codes),
            "team_colour": [TEAM.get(code, ("Delta", "#888888"))[1] for code in codes],
            "team_name": [TEAM.get(code, ("Delta", "#888888"))[0] for code in codes],
            "full_name": [f"Driver {code}" for code in codes],
        }
    )


def _circle(start: float, end: float, phase: float = 0.0) -> pd.DataFrame:
    """Raw 4 Hz position samples going round a circle once every 90 s."""
    times = np.arange(start, end + 1e-9, 0.25)
    angle = (times - start) / 90.0 * 2 * np.pi + phase
    return pd.DataFrame(
        {
            "SessionTime": pd.to_timedelta(times, unit="s"),
            "X": 1000.0 + np.cos(angle) * 4000.0,
            "Y": 1000.0 + np.sin(angle) * 2500.0,
            "Z": 0.0,
            "Status": "OnTrack",
        }
    )


def _outline() -> pd.DataFrame:
    angle = np.linspace(0, 2 * np.pi, 200)
    return pd.DataFrame(
        {
            "Distance": np.linspace(0.0, 5000.0, 200),
            "X": 1000.0 + np.cos(angle) * 4000.0,
            "Y": 1000.0 + np.sin(angle) * 2500.0,
            "Z": 0.0,
        }
    )


def _race_laps() -> pd.DataFrame:
    rows = []
    for number, (code, ends) in enumerate(RACE_LAP_ENDS.items()):
        start = LIGHTS_OUT
        for lap, end in enumerate(ends, start=1):
            pitted = code == "C" and lap == 5
            fresh = code != "B"
            rows.append(
                {
                    "Driver": code,
                    "DriverNumber": str(10 + number),
                    "LapNumber": float(lap),
                    "LapTime": end - start,
                    "Time": end,
                    "LapStartTime": start,
                    "Sector1Time": 30.0,
                    "Sector2Time": 30.0,
                    "Sector3Time": end - start - 60.0,
                    "Sector1SessionTime": start + 30.0,
                    "Sector2SessionTime": start + 60.0,
                    "Sector3SessionTime": end,
                    "PitInTime": C_PIT_IN if code == "C" and lap == 4 else np.nan,
                    "PitOutTime": C_PIT_OUT if pitted else np.nan,
                    "Compound": "HARD" if pitted else "SOFT",
                    "Stint": 2.0 if pitted else 1.0,
                    "TyreLife": 1.0 if pitted else float(lap + (0 if fresh else 2)),
                    "FreshTyre": True if pitted else fresh,
                    "SpeedST": 300.0 + number,
                    "Deleted": False,
                    "IsAccurate": True,
                }
            )
            start = end
    laps = pd.DataFrame(rows)
    # A stops on track after the first sector of lap 5.
    stopped = (laps["Driver"] == "A") & (laps["LapNumber"] == 5)
    cleared = ["Sector2Time", "Sector3Time", "Sector2SessionTime", "Sector3SessionTime"]
    laps.loc[stopped, cleared] = np.nan
    positions = {"A": [1, 1, 2, 2, np.nan], "B": [2, 2, 1, 1, 1], "C": [3, 3, 3, 3, 2]}
    laps["Position"] = np.nan
    for code, values in positions.items():
        laps.loc[laps["Driver"] == code, "Position"] = values
    for column in TIME_COLUMNS:
        laps[column] = _td(laps[column])
    laps["IsPitOutLap"] = laps["PitOutTime"].notna()
    return laps


def _race_stream() -> pd.DataFrame:
    stream = pd.DataFrame(
        [
            (1000.0, "A", 1, "LAP 1", "LAP 1"),
            (1000.0, "B", 2, None, None),
            (1000.0, "C", 3, None, None),
            (1090.0, "A", 1, "LAP 2", "LAP 2"),
            (1091.0, "B", 2, "+1.000", "+1.000"),
            (1092.0, "C", 3, "+2.000", "+1.000"),
            (1180.0, "A", 1, "LAP 3", "LAP 3"),
            (1181.0, "B", 2, "+1.000", "+1.000"),
            (1182.0, "C", 3, "+2.000", "+1.000"),
            (1270.5, "B", 1, "LAP 4", "LAP 4"),
            (1270.5, "A", 2, "+0.500", "+0.500"),
            (1271.0, "A", 2, "+0.500", "+0.500"),
            (1273.0, "C", 3, "+2.500", "+2.000"),
            (1360.5, "B", 1, "LAP 5", "LAP 5"),
            (1361.0, "A", 2, "+0.500", "+0.500"),
            (1364.0, "C", 3, "+3.500", "+3.000"),
            (1410.0, "C", 2, "+20.000", "+20.000"),
            (1410.0, "A", 3, "1 L", "1 L"),
            (1450.0, "B", 1, "LAP 5", "LAP 5"),
            (1470.0, "C", 2, "+20.000", "+20.000"),
        ],
        columns=["Time", "Driver", "Position", "GapToLeader", "IntervalToPositionAhead"],
    )
    gaps = [parse_gap(value) for value in stream["GapToLeader"]]
    intervals = [parse_gap(value) for value in stream["IntervalToPositionAhead"]]
    stream["Position"] = stream["Position"].astype("Int64")
    stream["GapSeconds"] = pd.array([g[0] for g in gaps], dtype="Float64")
    stream["GapLapsDown"] = pd.array([g[1] for g in gaps], dtype="Int64")
    stream["IntervalSeconds"] = pd.array([i[0] for i in intervals], dtype="Float64")
    stream["IntervalLapsDown"] = pd.array([i[1] for i in intervals], dtype="Int64")
    return stream


def race_session(with_stream: bool = True) -> dict:
    """The three-car race described in the module docstring."""
    laps = _race_laps()
    raw_positions = {
        code: _circle(LIGHTS_OUT, RACE_END_BY[code], phase=index * 0.02)
        for index, code in enumerate(RACE_LAP_ENDS)
    }
    positions = build_position_timeline(raw_positions)

    t0_date = pd.Timestamp("2026-06-07 13:00:00")
    control_times = [LIGHTS_OUT - 60.0, SC_START, 1450.0]
    race_control = pd.DataFrame(
        {
            "Time": t0_date + pd.to_timedelta(control_times, unit="s"),
            "SessionTime": _td(control_times),
            "Lap": [1, 4, 5],
            "Category": ["Flag", "SafetyCar", "Flag"],
            "Flag": ["GREEN", None, "CHEQUERED"],
            "Scope": ["Track", None, "Track"],
            "Message": ["GREEN LIGHT - PIT EXIT OPEN", "SAFETY CAR DEPLOYED", "CHEQUERED FLAG"],
        }
    )
    weather_times = np.arange(LIGHTS_OUT - 60.0, 1500.0, 60.0)
    weather = pd.DataFrame(
        {
            "Time": _td(weather_times),
            "AirTemp": 20.0 + np.arange(len(weather_times)) * 0.1,
            "TrackTemp": 30.0 + np.arange(len(weather_times)) * 0.2,
            "Humidity": 50.0,
            "Pressure": 1010.0,
            "Rainfall": False,
            "WindSpeed": 2.0,
            "WindDirection": 90,
        }
    )
    track_status = pd.DataFrame(
        {
            "Time": [LIGHTS_OUT - 10.0, SC_START, SC_END],
            "Status": ["1", "4", "1"],
            "Message": ["AllClear", "SCDeployed", "AllClear"],
        }
    )
    stints = pd.DataFrame(
        {
            "Driver": ["A", "B", "C", "C"],
            "Stint": pd.array([1, 1, 1, 2], dtype="Int64"),
            "Compound": ["SOFT", "SOFT", "SOFT", "HARD"],
            "LapStart": pd.array([1, 1, 1, 5], dtype="Int64"),
            "LapEnd": pd.array([5, 5, 4, 5], dtype="Int64"),
            "LapCount": pd.array([5, 5, 4, 1], dtype="Int64"),
        }
    )
    results = pd.DataFrame(
        {
            "Abbreviation": ["B", "C", "A"],
            "DriverNumber": ["11", "12", "10"],
            "Position": [1.0, 2.0, 3.0],
            "Status": ["Finished", "Finished", "Retired"],
            "Time": _td([450.0, 20.0, np.nan]),
        }
    )
    session = {
        "session_info": {
            "year": 2026,
            "gp": "Test Grand Prix",
            "session_type": "R",
            "session_name": "Race",
            "telemetry_scope": "fastest",
            "session_start": LIGHTS_OUT,
            "segment_starts": [],
            "total_laps": 5,
        },
        "telemetry": {},
        "laps": laps,
        "stints": stints,
        "results": results,
        "positions": positions,
        "timing_stream": _race_stream() if with_stream else pd.DataFrame(),
        "track_status": track_status,
        "location": {"A": _outline()},
        "weather": weather,
        "race_control": race_control,
        "compound_colors": {"SOFT": "#da291c", "HARD": "#f0f0ec"},
        "circuit_info": {},
        "drivers": _drivers(["A", "B", "C"]),
        "source": "fastf1",
        "is_live": False,
    }
    clock = replay_clock(laps, positions, "R", LIGHTS_OUT)
    session["session_info"]["replay_clock"] = clock.to_dict()
    return session


# --- qualifying ------------------------------------------------------------

Q_STARTS = [100.0, 1500.0, 2700.0]
Q_CODES = [f"D{index:02d}" for index in range(22)]
# D15 is the slowest into Q2, then sets the fastest Q2 lap on a second run.
Q2_LATE_DRIVER = "D15"
Q2_LATE_LAP_END = 2400.0


def _q_run(code: str, number: int, leave: float, flying: list[float]) -> list[tuple]:
    """Out-lap, flying lap(s), in-lap: the laps of one qualifying run."""
    rows = []
    out_end = leave + 100.0
    rows.append((code, number, leave, out_end, 100.0, False, leave, np.nan))
    start = out_end
    for lap_time in flying:
        rows.append((code, number, start, start + lap_time, lap_time, True, np.nan, np.nan))
        start += lap_time
    rows.append((code, number, start, start + 110.0, 110.0, False, np.nan, start + 100.0))
    return rows


def qualifying_session() -> dict:
    """22 cars, three segments; order by driver index, except D15 in Q2."""
    raw = []
    for index, code in enumerate(Q_CODES):
        raw += _q_run(code, index, Q_STARTS[0] + 10.0 * index, [90.0 + 0.1 * index])
    for index, code in enumerate(Q_CODES[:16]):
        raw += _q_run(code, index, Q_STARTS[1] + 10.0 * index, [89.5 + 0.1 * index])
        if code == Q2_LATE_DRIVER:
            leave = Q2_LATE_LAP_END - 88.0 - 100.0
            raw += _q_run(code, index, leave, [88.0])
    top_ten = [Q2_LATE_DRIVER, *Q_CODES[:9]]
    for index, code in enumerate(top_ten):
        number = Q_CODES.index(code)
        raw += _q_run(code, number, Q_STARTS[2] + 10.0 * index, [89.0 + 0.1 * index])

    frame = pd.DataFrame(
        raw,
        columns=["Driver", "number", "start", "end", "lap_s", "accurate", "pit_out", "pit_in"],
    )
    frame = frame.sort_values(["Driver", "start"], kind="stable").reset_index(drop=True)
    frame["LapNumber"] = frame.groupby("Driver").cumcount() + 1.0
    laps = pd.DataFrame(
        {
            "Driver": frame["Driver"],
            "DriverNumber": frame["number"].astype(str),
            "LapNumber": frame["LapNumber"],
            "LapTime": _td(frame["lap_s"]),
            "Time": _td(frame["end"]),
            "LapStartTime": _td(frame["start"]),
            "Sector1Time": _td(frame["lap_s"] / 3),
            "Sector2Time": _td(frame["lap_s"] / 3),
            "Sector3Time": _td(frame["lap_s"] / 3),
            "PitOutTime": _td(frame["pit_out"]),
            "PitInTime": _td(frame["pit_in"]),
            "Compound": "SOFT",
            "Stint": frame.groupby("Driver")["pit_out"].transform(lambda s: s.notna().cumsum()),
            "TyreLife": 1.0,
            "FreshTyre": True,
            "Deleted": False,
            "IsAccurate": frame["accurate"],
            "Position": np.nan,
        }
    )
    session = {
        "session_info": {
            "year": 2026,
            "gp": "Test Grand Prix",
            "session_type": "Q",
            "session_name": "Qualifying",
            "session_start": Q_STARTS[0],
            "segment_starts": list(Q_STARTS),
            "total_laps": None,
        },
        "telemetry": {},
        "laps": laps,
        "stints": pd.DataFrame(),
        "results": pd.DataFrame(),
        "positions": pd.DataFrame(columns=["Time", "Driver", "X", "Y"]),
        "timing_stream": pd.DataFrame(),
        "track_status": pd.DataFrame({"Time": [0.0], "Status": ["1"], "Message": ["AllClear"]}),
        "location": {},
        "weather": pd.DataFrame(),
        "race_control": pd.DataFrame(),
        "compound_colors": {},
        "circuit_info": {},
        "drivers": _drivers(Q_CODES),
        "source": "fastf1",
        "is_live": False,
    }
    session["session_info"]["replay_clock"] = {
        "start": Q_STARTS[0],
        "lights_out": Q_STARTS[0],
        "end": float(frame["end"].max()) + 60.0,
        "step": 0.5,
    }
    return session


def practice_session() -> dict:
    """Five cars; E sets the fastest lap last."""
    plan = {"A": [92.0, 91.0], "B": [91.5, 91.2], "C": [93.0], "D": [95.0, 94.0], "E": [92.5, 90.5]}
    rows = []
    for number, (code, lap_times) in enumerate(plan.items()):
        start = 200.0 + number * 30.0
        for lap, lap_time in enumerate(lap_times, start=1):
            rows.append((code, str(number), float(lap), lap_time, start + lap_time, start))
            start += lap_time
    frame = pd.DataFrame(
        rows, columns=["Driver", "DriverNumber", "LapNumber", "lap_s", "end", "start"]
    )
    laps = pd.DataFrame(
        {
            "Driver": frame["Driver"],
            "DriverNumber": frame["DriverNumber"],
            "LapNumber": frame["LapNumber"],
            "LapTime": _td(frame["lap_s"]),
            "Time": _td(frame["end"]),
            "LapStartTime": _td(frame["start"]),
            "Compound": "MEDIUM",
            "Stint": 1.0,
            "TyreLife": frame["LapNumber"],
            "FreshTyre": True,
            "Deleted": False,
            "IsAccurate": True,
        }
    )
    return {
        "session_info": {
            "year": 2026,
            "gp": "Test Grand Prix",
            "session_type": "FP1",
            "session_name": "Practice 1",
            "session_start": 150.0,
            "segment_starts": [],
            "replay_clock": {"start": 150.0, "lights_out": 150.0, "end": 600.0, "step": 0.5},
        },
        "laps": laps,
        "stints": pd.DataFrame(),
        "results": pd.DataFrame(),
        "positions": pd.DataFrame(columns=["Time", "Driver", "X", "Y"]),
        "timing_stream": pd.DataFrame(),
        "track_status": pd.DataFrame(),
        "weather": pd.DataFrame(),
        "race_control": pd.DataFrame(),
        "location": {},
        "drivers": _drivers(list(plan)),
        "source": "fastf1",
        "is_live": False,
    }
