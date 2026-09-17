"""Streamlit UI components - canonical rendering module.

All chart builders and panels used by ``app.py`` live here so there is a
single source of truth for the dashboard's visuals (the former
``ui/layout_new.py`` variant was removed).
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import config
from data.fastf1_adapter import session_codes_for_event
from data.live_adapter import TOKEN_ENV_VAR, subscription_token
from processing.telemetry_processor import TelemetryProcessor, max_lap_number
from processing.time_utils import seconds_series
from ui.dashboard import render_dashboard, wind_kmh

# Fallback only. Real sessions carry FastF1's official per-season mapping
# (see FastF1Adapter.compound_colors); these hexes match the 2024+ branding.
COMPOUND_COLORS = {
    "SOFT": "#da291c",
    "MEDIUM": "#ffd12e",
    "HARD": "#f0f0ec",
    "INTERMEDIATE": "#43b02a",
    "WET": "#0067ad",
    "UNKNOWN": "#00ffff",
    "TEST-UNKNOWN": "#434649",
}

# Official F1 TrackStatus codes (SignalR feed).
TRACK_STATUS = {
    "1": ("🟢", "Track clear"),
    "2": ("🟡", "Yellow flag"),
    "4": ("🚗", "Safety car"),
    "5": ("🔴", "Red flag"),
    "6": ("🟠", "Virtual safety car"),
    "7": ("🟠", "VSC ending"),
}

# Race-control flag icons for the message feed.
FLAG_ICONS = {
    "GREEN": "🟢",
    "YELLOW": "🟡",
    "DOUBLE YELLOW": "🟡",
    "RED": "🔴",
    "CHEQUERED": "🏁",
    "BLUE": "🔵",
    "CLEAR": "✅",
}


def compound_palette(compound_colors: dict[str, str] | None = None) -> dict[str, str]:
    """Session compound colours, falling back to the built-in table."""
    palette = dict(COMPOUND_COLORS)
    if compound_colors:
        palette.update({str(k).upper(): v for k, v in compound_colors.items()})
    return palette


# Labels for the telemetry scope control -> DataSourceManager scope values.
SCOPE_LABELS = {
    "Fastest lap (comparable)": "fastest",
    "Full session": "session",
}

# "LiveF1 (Historical)" is deliberately absent: its loader reads attributes
# and column names livef1 does not use, and livef1 itself raises building a
# Session for some seasons. Offering it promised data the app cannot deliver
# (HIST-03); FastF1 covers the same sessions.
SOURCE_MAP = {
    "Auto (Live → Historical)": "auto",
    "FastF1 (Historical)": "fastf1",
    "Live (SignalR)": "live",
    "Replay (Saved)": "replay",
}


def render_header():
    """Render page header."""
    st.set_page_config(page_title="F1 Telemetry Dashboard", layout="wide")
    st.title("🏎️ Formula 1 Telemetry Dashboard")
    st.caption("Historical (FastF1) • Live (SignalR - FREE) • Replay (Local)")


# Both helpers below hit the network. Streamlit re-runs this module top to
# bottom on every widget interaction, so without caching the schedule and the
# race-weekend probe would be re-fetched on every click.
@st.cache_data(ttl=900, show_spinner=False)
def _is_race_weekend_cached(_data_manager) -> bool:
    return _data_manager._is_race_weekend()


@st.cache_data(ttl=3600, show_spinner=False)
def _event_names_cached(_data_manager, year: int) -> list:
    meetings = _data_manager.fastf1.get_available_sessions(year)
    if meetings is None or meetings.empty or "EventName" not in meetings.columns:
        return []
    return sorted(meetings["EventName"].dropna().unique().tolist())


# Sprint weekends have no FP2/FP3 but do have SQ, so the session list comes
# from the event's own schedule rather than a fixed six-entry list.
FALLBACK_SESSION_TYPES = ["FP1", "FP2", "FP3", "Q", "S", "R"]


@st.cache_data(ttl=3600, show_spinner=False)
def _session_codes_cached(_data_manager, year: int, gp: str) -> list:
    meetings = _data_manager.fastf1.get_available_sessions(year)
    if meetings is None or meetings.empty or "EventName" not in meetings.columns:
        return []
    matches = meetings[meetings["EventName"] == gp]
    if matches.empty:
        return []
    return session_codes_for_event(matches.iloc[0])


def render_session_selector(data_manager) -> dict:
    """Session selection with live detection."""
    # Check for live session
    is_race_weekend = _is_race_weekend_cached(data_manager)

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        source = st.selectbox("Data Source", list(SOURCE_MAP), index=0)

    # Live indicator (exact match so "LiveF1 (Historical)" is not treated as live)
    live_session = None
    if "Auto" in source and is_race_weekend:
        # A session really is on air - but Auto no longer switches silently
        # and hides the historical selectors: the user chooses (LIVE-15).
        with col2:
            st.success("🔴 A session is running now")
            go_live = st.button("Go live", key="go_live")
        if go_live or st.session_state.get("go_live_active"):
            st.session_state["go_live_active"] = True
            live_session = True
    elif source == "Live (SignalR)":
        with col2:
            st.warning("🔴 LIVE MODE - Attempting SignalR connection...")
        live_session = True

    telemetry_scope = SCOPE_LABELS["Fastest lap (comparable)"]
    is_replay = source == "Replay (Saved)"

    # A replay carries its own session identity, so Season/GP/Session/Scope
    # would only mislead: the file is the whole selection.
    if is_replay:
        years = None
        gp = None
        session_type = None
        with col2:
            replays = data_manager.get_available_replays()
            if replays:
                replay_file = st.selectbox("Replay File", replays)
            else:
                st.info("No replay files available")
                replay_file = None

    # Historical selection
    elif not live_session:
        with col2:
            this_year = datetime.now(UTC).year
            years = st.selectbox("Season", [this_year, this_year - 1, this_year - 2], index=0)

        with col3:
            # Available GPs for the selected year (FastF1 schedule for all sources)
            gps = _event_names_cached(data_manager, years)
            if gps:
                gp = st.selectbox("Grand Prix", gps)
            else:
                st.selectbox("Grand Prix", ["No completed events"], disabled=True)
                gp = None

        session_types = (gp and _session_codes_cached(data_manager, years, gp)) or (
            FALLBACK_SESSION_TYPES
        )
        session_type = st.selectbox("Session", session_types, index=len(session_types) - 1)

        scope_label = st.radio(
            "Telemetry scope",
            list(SCOPE_LABELS),
            index=0,
            horizontal=True,
            help=(
                "Fastest lap plots each driver's quickest lap on a 0 -> lap-length "
                "distance axis, so drivers are comparable at the same track "
                "position. Full session plots every lap, with distance "
                "accumulating over the whole run (far heavier to render)."
            ),
        )
        telemetry_scope = SCOPE_LABELS[scope_label]

        if gp is None:
            st.warning("No completed events for this season - pick another season.")

        replay_file = None
    else:
        years = None
        gp = None
        session_type = None
        replay_file = None

    return {
        "source": SOURCE_MAP.get(source, "auto"),
        "year": years,
        "gp": gp,
        "session_type": session_type,
        "replay_file": replay_file,
        "telemetry_scope": telemetry_scope,
    }


def create_telemetry_chart(
    telemetry_data: dict[str, pd.DataFrame], config: dict, color_map: dict[str, str]
) -> go.Figure | None:
    """Create multi-driver telemetry line chart."""
    col = config["col"]
    unit = config["unit"]

    fig = go.Figure()
    has_data = False

    for driver, df in telemetry_data.items():
        if df.empty or col not in df.columns or "Distance" not in df.columns:
            continue

        has_data = True
        color = color_map.get(driver, "#888888")

        if col == "Gear":
            fig.add_trace(
                go.Scatter(
                    x=df["Distance"],
                    y=df[col],
                    mode="lines",
                    name=driver,
                    line=dict(color=color, shape="hv"),
                    hovertemplate=f"{driver}: %{{y}}<br>Distance: %{{x}}m<extra></extra>",
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=df["Distance"],
                    y=df[col],
                    mode="lines",
                    name=driver,
                    line=dict(color=color, width=2),
                    hovertemplate=f"{driver}: %{{y}} {unit}<br>Distance: %{{x}}m<extra></extra>",
                )
            )

    if not has_data:
        return None

    layout = dict(
        title=f"{col} by Track Distance",
        xaxis_title="Distance (m)",
        yaxis_title=f"{col} ({unit})" if unit else col,
        hovermode="x unified",
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    if col == "Gear":
        # Gear labels are categorical ('N', '1'..'8'); without an explicit
        # order Plotly sorts them lexically and puts N and 1 in odd places.
        layout["yaxis"] = dict(
            title="Gear",
            type="category",
            categoryorder="array",
            categoryarray=TelemetryProcessor.GEAR_CATEGORIES,
        )
    fig.update_layout(**layout)
    return fig


def render_telemetry_charts(telemetry_data: dict[str, pd.DataFrame], color_map: dict[str, str]):
    """Render speed, throttle, brake, rpm, gear, DRS charts."""
    if not telemetry_data:
        st.info("No telemetry data available")
        return

    tabs = st.tabs(["📈 Speed", "⚡ Throttle", "🛑 Brake", "🔧 RPM", "⚙️ Gear", "🚀 DRS"])

    channel_config = {
        "Speed": {"col": "Speed", "unit": "km/h"},
        "Throttle": {"col": "Throttle", "unit": "%"},
        "Brake": {"col": "Brake", "unit": "%"},
        "RPM": {"col": "RPM", "unit": "RPM"},
        "Gear": {"col": "Gear", "unit": ""},
        "DRS": {"col": "DRS", "unit": ""},
    }

    for i, (_, cfg) in enumerate(channel_config.items()):
        with tabs[i]:
            fig = create_telemetry_chart(telemetry_data, cfg, color_map)
            if fig:
                st.plotly_chart(fig, width="stretch")
            else:
                st.info(f"No {cfg['col']} data available")


def render_lap_times(laps_df: pd.DataFrame, color_map: dict[str, str]):
    """Render lap time chart with pit stop indicators.

    Handles both FastF1 Timedelta lap times and the string values of the
    live timing feed ('M:SS.mmm').
    """
    if laps_df.empty:
        st.warning("No lap data available")
        return

    driver_col = "DriverAcronym" if "DriverAcronym" in laps_df.columns else "Driver"
    if driver_col not in laps_df.columns or "LapTime" not in laps_df.columns:
        st.warning("No driver/lap-time information in lap data")
        return
    if "LapNumber" not in laps_df.columns:
        st.warning("No lap numbers in lap data")
        return

    fig = go.Figure()

    for driver in laps_df[driver_col].dropna().unique():
        driver_laps = laps_df[laps_df[driver_col] == driver].sort_values("LapNumber")
        color = color_map.get(driver, "#888888")

        lap_times_sec = seconds_series(driver_laps["LapTime"])
        if "IsPitOutLap" in driver_laps.columns:
            pit_out = driver_laps["IsPitOutLap"].fillna(False).astype(bool)
        else:
            pit_out = pd.Series(False, index=driver_laps.index, dtype=bool)

        fig.add_trace(
            go.Scatter(
                x=driver_laps["LapNumber"],
                y=lap_times_sec,
                mode="lines+markers",
                name=driver,
                line=dict(color=color),
                marker=dict(
                    color=["red" if p else color for p in pit_out],
                    size=8,
                    symbol=["diamond" if p else "circle" for p in pit_out],
                ),
                hovertemplate=(
                    f"{driver}: Lap %{{x}}<br>"
                    f"Time: %{{customdata}}<br>"
                    f"Pit: %{{text}}<extra></extra>"
                ),
                customdata=driver_laps["LapTime"].astype(str),
                text=["🔧 PIT OUT" if p else "" for p in pit_out],
            )
        )

    fig.update_layout(
        title="Lap Times by Driver",
        xaxis_title="Lap Number",
        yaxis_title="Lap Time (seconds)",
        hovermode="x unified",
        height=500,
    )
    st.plotly_chart(fig, width="stretch")


def _as_lap_number(value, default):
    """Coerce a stint lap boundary to int, falling back when missing/NA."""
    if value is None or value is pd.NA:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def stint_traces(
    stints_df: pd.DataFrame,
    palette: dict,
    driver_col: str = "DriverAcronym",
) -> list:
    """Horizontal bars for a stint chart: one trace per **compound**.

    A trace per stint row meant ~90 Plotly traces for a race, each carrying
    its own marker and hover template; grouping by compound sends the same
    picture as a handful of traces with arrays.
    """
    if stints_df is None or stints_df.empty:
        return []
    if driver_col not in stints_df.columns:
        driver_col = "Driver" if "Driver" in stints_df.columns else driver_col
    if driver_col not in stints_df.columns:
        return []

    frame = stints_df.copy()
    frame["_compound"] = frame.get("Compound", "UNKNOWN").astype("string").str.upper()
    frame["_start"] = [_as_lap_number(value, 1) for value in frame.get("LapStart", 1)]
    frame["_end"] = [
        _as_lap_number(end, start)
        for end, start in zip(frame.get("LapEnd", frame["_start"]), frame["_start"], strict=False)
    ]
    counts = frame.get("LapCount", pd.Series(index=frame.index, dtype="object"))
    frame["_laps"] = [
        _as_lap_number(count, None) or max(end - start + 1, 1)
        for count, start, end in zip(counts, frame["_start"], frame["_end"], strict=False)
    ]

    traces = []
    for compound, group in frame.groupby("_compound", sort=True, dropna=False):
        label = str(compound) if pd.notna(compound) else "UNKNOWN"
        traces.append(
            go.Bar(
                x=group["_laps"].tolist(),
                y=group[driver_col].tolist(),
                base=group["_start"].tolist(),
                orientation="h",
                name=label,
                marker=dict(color=palette.get(label, "gray")),
                customdata=list(zip(group["_start"], group["_end"], strict=False)),
                hovertemplate=(
                    "%{y}: " + label + "<br>Laps: %{x}"
                    "<br>Start: %{customdata[0]}<br>End: %{customdata[1]}<extra></extra>"
                ),
                showlegend=False,
            )
        )
    return traces


def render_tire_strategy(
    stints_df: pd.DataFrame,
    color_map: dict[str, str],
    compound_colors: dict[str, str] | None = None,
):
    """Render horizontal bar chart for tire strategy.

    Tolerates stint rows that lack LapStart/LapEnd/LapCount (live feeds).
    ``compound_colors`` carries FastF1's official per-season tyre colours.
    """
    if stints_df.empty:
        st.warning("No tire stint data available")
        return
    palette = compound_palette(compound_colors)

    driver_col = "DriverAcronym" if "DriverAcronym" in stints_df.columns else "Driver"
    if driver_col not in stints_df.columns:
        st.warning("No driver information in stint data")
        return

    fig = go.Figure()
    for trace in stint_traces(stints_df, palette, driver_col):
        fig.add_trace(trace)

    # Driver labels with team colors
    for driver in stints_df[driver_col].dropna().unique():
        fig.add_annotation(
            x=-2,
            y=driver,
            xref="x",
            yref="y",
            text=f"<b>{driver}</b>",
            showarrow=False,
            font=dict(color=color_map.get(driver, "#AAA"), size=12),
            align="right",
            xanchor="right",
        )

    fig.update_layout(
        title="Tire Strategy by Driver",
        xaxis_title="Lap Number",
        barmode="stack",
        height=max(400, len(stints_df[driver_col].unique()) * 30 + 100),
        margin=dict(l=120),
        yaxis=dict(showticklabels=False),
    )
    st.plotly_chart(fig, width="stretch")


def render_track_map(location_data: dict[str, pd.DataFrame], color_map: dict[str, str]):
    """Render track map with driver positions."""
    if not location_data:
        st.info("GPS data not available for track map")
        return

    # Circuit outline from the first driver with valid GPS data
    valid_driver = None
    for driver, loc_df in location_data.items():
        if not loc_df.empty and {"X", "Y"}.issubset(loc_df.columns):
            valid_driver = driver
            break

    if not valid_driver:
        st.info("Track map requires GPS data (X, Y coordinates)")
        return

    circuit_df = location_data[valid_driver]
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=circuit_df["X"],
            y=circuit_df["Y"],
            mode="lines",
            line=dict(color="#444", width=2),
            name="Circuit",
            showlegend=False,
        )
    )

    for driver, loc_df in location_data.items():
        if loc_df.empty or "X" not in loc_df.columns:
            continue
        last_pos = loc_df.iloc[-1]
        fig.add_trace(
            go.Scatter(
                x=[last_pos["X"]],
                y=[last_pos["Y"]],
                mode="markers+text",
                marker=dict(color=color_map.get(driver, "#888"), size=12),
                text=[driver],
                textposition="top center",
                name=driver,
                showlegend=False,
            )
        )

    fig.update_layout(
        title="Track Map - Driver Positions",
        xaxis=dict(visible=False),
        # Circuits must not be distorted by the container's aspect ratio.
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
        height=500,
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")


@st.fragment(run_every=3)
def render_live_dashboard(data_manager, processor):
    """Auto-refreshing live view: polls the SignalR buffers every 3 s and
    renders telemetry channels, the track map, tyre stints and lap info."""
    snapshot = data_manager.poll_live_data()
    telemetry = snapshot["telemetry"]
    location = snapshot["location"]
    stints_df = processor.process_stints(
        snapshot["stints"], latest_lap=max_lap_number(snapshot["laps"])
    )

    # The spec dashboard, fed from the *polled* snapshot. It used to be
    # rendered once, outside the fragment, with the empty dict a live session
    # starts from - so the tower, sector cards and map read "No timing data"
    # for the whole session (LIVE-10).
    render_dashboard(snapshot)

    # Car telemetry and positions are the only auth-gated parts of the feed.
    # Timing, tyres, race control and weather work without a token, so the
    # view renders whatever arrived instead of waiting for everything.
    has_car_data = bool(telemetry or location)
    if not has_car_data:
        if subscription_token():
            st.info("Waiting for car telemetry and positions from the F1 SignalR feed...")
        else:
            st.warning(
                "Car telemetry and driver positions need an F1TV subscription token "
                f"(set `{TOKEN_ENV_VAR}`). Timing, tyres, race control and weather "
                "below do not need one."
            )

    status = (snapshot.get("session_info") or {}).get("track_status")
    if status:
        icon, label = TRACK_STATUS.get(status.get("status", ""), ("⚪", "Unknown"))
        st.markdown(f"### {icon} {label}")

    st.caption(
        f"🟢 Streaming · {len(telemetry)} driver(s) with telemetry · "
        f"{len(location)} on track · auto-refreshes every 3s"
    )

    color_map = processor.build_driver_color_map(snapshot["drivers"])

    # No "Timing" tab: the dashboard above is the timing view.
    tabs = st.tabs(["📊 Telemetry", "🗺️ Track Map", "🛞 Tyres", "🚩 Race Control", "🌤️ Weather"])
    with tabs[0]:
        render_telemetry_charts(
            {d: processor.normalize_units(df.copy()) for d, df in telemetry.items()}, color_map
        )
    with tabs[1]:
        render_track_map(location, color_map)
    with tabs[2]:
        render_tire_strategy(stints_df, color_map, snapshot.get("compound_colors"))
        if not stints_df.empty:
            st.dataframe(stints_df, width="stretch", height=250)
    with tabs[3]:
        render_race_control(snapshot.get("race_control"), limit=25)
    with tabs[4]:
        render_weather(snapshot.get("weather"))


def render_position_changes(laps_df: pd.DataFrame, color_map: dict[str, str]):
    """Lap-by-lap running order - who gained and lost places, and when."""
    if laps_df.empty or "Position" not in laps_df.columns:
        st.info("No position data available for this session")
        return

    driver_col = "DriverAcronym" if "DriverAcronym" in laps_df.columns else "Driver"
    if driver_col not in laps_df.columns or "LapNumber" not in laps_df.columns:
        st.info("No position data available for this session")
        return

    positions = pd.to_numeric(laps_df["Position"], errors="coerce")
    if positions.notna().sum() == 0:
        st.info("No position data available for this session")
        return

    fig = go.Figure()
    work = laps_df.assign(_pos=positions)
    # Order the legend by final classification rather than alphabetically.
    final = work.dropna(subset=["_pos"]).sort_values("LapNumber").groupby(driver_col)["_pos"].last()
    for driver in final.sort_values().index:
        driver_laps = work[work[driver_col] == driver].sort_values("LapNumber")
        fig.add_trace(
            go.Scatter(
                x=driver_laps["LapNumber"],
                y=driver_laps["_pos"],
                mode="lines",
                name=str(driver),
                line=dict(color=color_map.get(driver, "#888888"), width=2),
                hovertemplate=f"{driver}: P%{{y}}<br>Lap %{{x}}<extra></extra>",
                connectgaps=True,
            )
        )

    fig.update_layout(
        title="Position Changes",
        xaxis_title="Lap Number",
        # P1 belongs at the top.
        yaxis=dict(title="Position", autorange="reversed", dtick=1, tickformat="d"),
        hovermode="closest",
        height=560,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01),
    )
    st.plotly_chart(fig, width="stretch")


def render_weather(weather_df: pd.DataFrame):
    """Track/air temperature, humidity, wind and rainfall over the session."""
    if weather_df is None or weather_df.empty:
        st.info("No weather data available for this session")
        return

    latest = weather_df.iloc[-1]
    cols = st.columns(5)
    # Wind arrives in m/s and is shown in km/h, matching the dashboard header.
    readings = [
        ("🌡️ Air", "AirTemp", "°C", None),
        ("🛣️ Track", "TrackTemp", "°C", None),
        ("💧 Humidity", "Humidity", "%", None),
        ("🌬️ Wind", "WindSpeed", "km/h", wind_kmh),
        ("🔽 Pressure", "Pressure", "mbar", None),
    ]
    for col, (label, key, unit, convert) in zip(cols, readings, strict=False):
        # The live feed sends these as strings ("21.0"); FastF1 sends floats.
        value = pd.to_numeric(latest.get(key), errors="coerce")
        if convert is not None:
            value = convert(value)
        col.metric(label, f"{value:g} {unit}" if pd.notna(value) else "--")

    if "Rainfall" in weather_df.columns and bool(weather_df["Rainfall"].any()):
        st.warning("🌧️ Rainfall recorded during this session")

    x = _elapsed_minutes(weather_df)
    fig = go.Figure()
    for key, label, color in (
        ("TrackTemp", "Track temp (°C)", "#e10600"),
        ("AirTemp", "Air temp (°C)", "#00a0de"),
    ):
        if key in weather_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=weather_df[key],
                    mode="lines",
                    name=label,
                    line=dict(color=color, width=2),
                )
            )
    if "Humidity" in weather_df.columns:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=weather_df["Humidity"],
                mode="lines",
                name="Humidity (%)",
                line=dict(color="#7a7a7a", width=1, dash="dot"),
                yaxis="y2",
            )
        )

    fig.update_layout(
        title="Track Conditions",
        xaxis_title="Session time (min)",
        yaxis=dict(title="Temperature (°C)"),
        yaxis2=dict(title="Humidity (%)", overlaying="y", side="right", showgrid=False),
        hovermode="x unified",
        height=340,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width="stretch")


def _elapsed_minutes(df: pd.DataFrame) -> pd.Series:
    """Session-relative minutes from a Time column (Timedelta or timestamp)."""
    if "Time" not in df.columns:
        return pd.Series(range(len(df)), index=df.index, dtype="float64")
    times = df["Time"]
    if pd.api.types.is_timedelta64_dtype(times):
        return times.dt.total_seconds() / 60.0
    parsed = pd.to_datetime(times, errors="coerce", utc=True)
    if parsed.notna().any():
        return (parsed - parsed.min()).dt.total_seconds() / 60.0
    return pd.Series(range(len(df)), index=df.index, dtype="float64")


def render_race_control(race_control_df: pd.DataFrame, limit: int = 60):
    """Race control feed: flags, safety cars, investigations, penalties."""
    if race_control_df is None or race_control_df.empty:
        st.info("No race control messages for this session")
        return

    df = race_control_df.copy()
    if "Lap" in df.columns:
        df["Lap"] = pd.to_numeric(df["Lap"], errors="coerce").astype("Int64")

    categories = sorted({str(c) for c in df.get("Category", pd.Series(dtype=object)).dropna()})
    if categories:
        chosen = st.multiselect(
            "Filter by category", categories, default=categories, key="rc_categories"
        )
        if chosen:
            df = df[df["Category"].astype(str).isin(chosen)]

    if df.empty:
        st.info("No messages match that filter")
        return

    # Newest first: during a session the latest instruction is what matters.
    df = df.iloc[::-1].head(limit)

    lines = []
    for _, row in df.iterrows():
        flag = str(row.get("Flag") or "").upper()
        icon = FLAG_ICONS.get(flag, "•")
        lap = row.get("Lap")
        lap_text = f"L{int(lap)}" if pd.notna(lap) else "--"
        lines.append(f"{icon} **{lap_text}** · {row.get('Message', '')}")
    st.markdown("\n\n".join(lines))
    if len(race_control_df) > limit:
        st.caption(f"Showing the {limit} most recent of {len(race_control_df)} messages.")


def render_driver_comparison(
    telemetry_data: dict[str, pd.DataFrame], color_map: dict[str, str], key_prefix: str = "cmp"
):
    """Head-to-head speed trace plus cumulative time delta between two drivers.

    The delta is integrated from the speed traces on the shared distance grid
    rather than via ``fastf1.utils.delta_time``, which is deprecated since
    FastF1 3.0 and emits a FutureWarning.
    """
    usable = sorted(
        d
        for d, df in telemetry_data.items()
        if not df.empty and {"Distance", "Speed"}.issubset(df.columns)
    )
    if len(usable) < 2:
        st.info("Need telemetry for at least two drivers to compare")
        return

    col1, col2 = st.columns(2)
    with col1:
        reference = st.selectbox("Reference driver", usable, index=0, key=f"{key_prefix}_ref")
    with col2:
        others = [d for d in usable if d != reference]
        compare = st.selectbox("Compared with", others, index=0, key=f"{key_prefix}_cmp")

    ref_df, cmp_df = telemetry_data[reference], telemetry_data[compare]
    delta_distance, delta_seconds = _time_delta(ref_df, cmp_df)

    fig = go.Figure()
    for driver, df in ((reference, ref_df), (compare, cmp_df)):
        fig.add_trace(
            go.Scatter(
                x=df["Distance"],
                y=df["Speed"],
                mode="lines",
                name=driver,
                line=dict(color=color_map.get(driver, "#888888"), width=2),
                hovertemplate=f"{driver}: %{{y}} km/h<br>%{{x:.0f}} m<extra></extra>",
            )
        )
    fig.update_layout(
        title=f"Speed trace - {reference} vs {compare}",
        xaxis_title="Distance (m)",
        yaxis_title="Speed (km/h)",
        hovermode="x unified",
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width="stretch")

    if delta_seconds is None:
        st.caption("Not enough overlapping distance to compute a time delta.")
        return

    delta_fig = go.Figure()
    delta_fig.add_trace(
        go.Scatter(
            x=delta_distance,
            y=delta_seconds,
            mode="lines",
            name="Delta",
            line=dict(color="#ffffff", width=2),
            hovertemplate="%{y:+.3f} s at %{x:.0f} m<extra></extra>",
        )
    )
    delta_fig.add_hline(y=0, line=dict(color="#888888", width=1, dash="dot"))
    delta_fig.update_layout(
        title=f"Cumulative time delta - {compare} relative to {reference}",
        xaxis_title="Distance (m)",
        yaxis_title=f"Δ time (s) — below 0 = {compare} ahead",
        hovermode="x unified",
        height=320,
        showlegend=False,
    )
    st.plotly_chart(delta_fig, width="stretch")

    gained = float(delta_seconds[-1])
    verdict = f"{compare} is {abs(gained):.3f} s " + ("behind" if gained > 0 else "ahead")
    st.caption(
        f"Over the compared distance, {verdict} {reference}. "
        "Approximate: the delta is integrated from sampled speed traces, so it "
        "typically lands within ~0.1-0.3 s of the true lap-time difference. "
        "Use the lap times themselves for exact gaps."
    )


def _time_delta(ref_df: pd.DataFrame, cmp_df: pd.DataFrame):
    """Cumulative time difference (s) between two speed traces.

    Time to cover each distance step is ``ds / v``; integrating the difference
    of those step times gives how far apart the cars are in time. Returns
    ``(distance_grid, delta_seconds)``; delta is None when the traces do not
    overlap enough.
    """
    grid, ref_speed = _speed_on_grid(ref_df)
    if grid is None:
        return None, None
    cmp_grid, cmp_speed = _speed_on_grid(cmp_df, grid=grid)
    if cmp_grid is None:
        return None, None

    # km/h -> m/s; clamp so a zero speed cannot produce an infinite step time.
    ref_ms = np.clip(ref_speed / 3.6, 1e-3, None)
    cmp_ms = np.clip(cmp_speed / 3.6, 1e-3, None)
    step = np.diff(grid, prepend=grid[0])
    delta = np.cumsum(step / cmp_ms - step / ref_ms)
    return grid, delta


def _speed_on_grid(df: pd.DataFrame, grid: np.ndarray | None = None):
    """Speed sampled onto a uniform distance grid (10 m steps by default)."""
    if df is None or df.empty or not {"Distance", "Speed"}.issubset(df.columns):
        return None, None
    distance = pd.to_numeric(df["Distance"], errors="coerce")
    speed = pd.to_numeric(df["Speed"], errors="coerce")
    ok = distance.notna() & speed.notna()
    if int(ok.sum()) < 10:
        return None, None
    distance, speed = distance[ok].to_numpy(float), speed[ok].to_numpy(float)
    order = np.argsort(distance, kind="stable")
    distance, speed = distance[order], speed[order]
    if grid is None:
        span = distance.max() - distance.min()
        if span <= 0:
            return None, None
        grid = np.arange(distance.min(), distance.max(), max(span / 2000.0, 1.0))
        if grid.size < 10:
            return None, None
    return grid, np.interp(grid, distance, speed)


def render_live_controls(live_client):
    """Render live session controls."""
    if not live_client:
        return

    st.markdown("---")
    st.subheader("🔴 Live Session Controls")

    if live_client.is_recording():
        st.caption(f"🔴 Recording · {live_client.recorder.message_count} messages captured")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("📊 View Buffered Data"):
            st.json(
                {
                    topic: len(live_client.get_buffered_data(topic))
                    for topic in ("CarData.z", "Position.z", "TimingData", "WeatherData")
                }
            )
        if st.button("🧹 Clear Buffers"):
            live_client.clear_buffer()
            st.info("Buffered telemetry and merged state cleared")

    with col2:
        # Records the raw messages, so a replay feeds the same handler the
        # live client does (LIVE-12). Saving the processed session dict for a
        # live session would have saved the empty dict it starts from.
        if live_client.is_recording():
            if st.button("⏹️ Stop Recording"):
                where = live_client.stop_recording()
                st.success(f"Raw stream saved to {where}")
        elif st.button("💾 Record Raw Stream"):
            directory = Path(config.replay_dir) / f"raw_{datetime.now(UTC):%Y%m%d_%H%M%S}"
            live_client.start_recording(directory)
            st.info(f"Recording to {directory}")

    with col3:
        if st.button("⏹️ Stop Live"):
            live_client.stop_recording()
            live_client.stop()
            st.warning("Live session stopped")
