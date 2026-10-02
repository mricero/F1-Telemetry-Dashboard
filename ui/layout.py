"""Streamlit UI components - canonical rendering module.

All chart builders and panels used by ``app.py`` live here so there is a
single source of truth for the dashboard's visuals (the former
``ui/layout_new.py`` variant was removed).
"""

import html
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import config
from data.fastf1_adapter import session_codes_for_event
from data.live_adapter import TOKEN_ENV_VAR, subscription_token
from processing.telemetry_processor import TelemetryProcessor, max_lap_number
from processing.time_utils import seconds_series
from processing.timing import MISSING, format_lap
from processing.track_periods import lap_spans, lap_states
from ui import units
from ui.dashboard import render_dashboard, wind_kmh
from ui.fonts import font_face_css
from ui.status import DataStatus, show
from ui.theme import (
    ACCENT,
    APP_CSS,
    CHART_COOL,
    CHART_WARM,
    COMPOUND_RING,
    CSS_TOKENS,
    FLAG_STATES,
    NEUTRAL_GREY,
    TEXT,
    TEXT_DIM,
    chart_layout,
    status_chip,
)

logger = logging.getLogger(__name__)

# Fallback only. Real sessions carry FastF1's official per-season mapping
# (see FastF1Adapter.compound_colors); the defaults are the theme's.
COMPOUND_COLORS = dict(COMPOUND_RING)

# Official F1 TrackStatus codes (SignalR feed) -> (flag state, label). The
# state picks the chip colour from ui.theme.FLAG_STATES; the label is the
# chip word of guideline 5.6, so the state never depends on telling colours
# apart (UI-13: no sentence-case "Track clear" chips).
TRACK_STATUS = {
    "1": ("GREEN", "GREEN"),
    "2": ("YELLOW", "YELLOW"),
    "4": ("SAFETY CAR", "SC"),
    "5": ("RED", "RED"),
    "6": ("VSC", "VSC"),
    "7": ("VSC", "VSC"),
}


def styled_figure(fig: go.Figure, uirevision: str | None = None) -> go.Figure:
    """Apply the shared chart style *under* the chart's own layout (UI-12).

    The template goes first and whatever the chart set itself goes back on
    top, so a chart that asks for ``hovermode="closest"`` or a vertical legend
    keeps it. ``uirevision`` (UX-02) keeps a zoomed range across reruns and
    live fragment updates as long as it stays the same.
    """
    own = {key: value for key, value in fig.layout.to_plotly_json().items() if key != "template"}
    fig.update_layout(**chart_layout(len(fig.data)))
    fig.update_layout(own)
    if uirevision is not None:
        fig.update_layout(uirevision=str(uirevision))
    return fig


def _plot(fig: go.Figure, *args, uirevision: str | None = None, **kwargs):
    """Draw a chart in the shared style (guideline 5.6)."""
    return st.plotly_chart(styled_figure(fig, uirevision), *args, **kwargs)


# Shaded lap spans: the chip words, in the flag colours at low opacity.
SPAN_FLAGS = {"SC": "SAFETY CAR", "VSC": "VSC", "RED": "RED"}


def shade_neutral_laps(fig: go.Figure, laps_df: pd.DataFrame, track_status=None) -> list[dict]:
    """Shade SC, VSC and red-flag laps behind a lap-axis chart (UX-06).

    Each span carries its word (``SC``/``VSC``/``RED``) at the top, so the
    shading never depends on telling the colours apart. Returns the spans.
    """
    spans = lap_spans(lap_states(laps_df, track_status))
    for span in spans:
        colour = FLAG_STATES[SPAN_FLAGS[span["state"]]][0]
        fig.add_vrect(
            x0=span["first"] - 0.5,
            x1=span["last"] + 0.5,
            fillcolor=colour,
            opacity=0.12,
            line_width=0,
            layer="below",
            annotation_text=span["state"],
            annotation_position="top left",
            annotation_font={"size": 11, "color": TEXT_DIM},
        )
    return spans


def _mark_lap(fig: go.Figure, lap: int | None) -> None:
    """A 1 px accent line at the replay's current lap (UI-07)."""
    if lap is not None:
        fig.add_vline(x=lap, line={"color": ACCENT, "width": 1})


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

# FastF1 covers every historical session ("LiveF1 (Historical)" was removed
# with the livef1 dependency, REPO-18). "Auto" is gone too: a running session
# is offered through the explicit "Go live" button (LIVE-15), so the picker
# never switches source behind the user's back.
SOURCE_MAP = {
    "FastF1 (historical)": "fastf1",
    "Saved replay": "replay",
    "Live timing (SignalR)": "live",
}

# FastF1 has timing and telemetry from 2018 on.
FIRST_SEASON = 2018

SESSION_NAMES = {
    "FP1": "Practice 1",
    "FP2": "Practice 2",
    "FP3": "Practice 3",
    "SQ": "Sprint qualifying",
    "S": "Sprint",
    "Q": "Qualifying",
    "R": "Race",
}

SELECTION_KEY = "selection"
RECENT_KEY = "recent_sessions"
RECENT_LIMIT = 5


def app_version() -> str:
    """The app version (REPO-23), or ``unknown`` on a config without one."""
    try:
        from config import __version__
    except ImportError:
        return "unknown"
    return str(__version__)


def menu_items() -> dict:
    """The app menu's About entry, with the version (UI-22)."""
    return {
        "About": (
            f"F1 Replay {app_version()}\n\n"
            "Timing, replay and telemetry from FastF1 and the F1 live timing feed. "
            "Not affiliated with Formula 1."
        ),
    }


def directory_size(path) -> int:
    """Bytes under ``path`` (0 when it does not exist)."""
    root = Path(path)
    if not root.exists():
        return 0
    total = 0
    for item in root.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:  # removed while walking, or unreadable: skip it
            continue
    return total


def clear_schedule_caches() -> None:
    """Forget the cached schedules and race-weekend probe (UI-22).

    They live for up to an hour, which on a race weekend hides a session that
    has just ended.
    """
    for cached in (_is_race_weekend_cached, _event_names_cached, _session_codes_cached):
        cached.clear()


@st.cache_data(ttl=3600, show_spinner=False)
def _update_notice_cached(version: str) -> str | None:
    from data.update_check import update_notice

    return update_notice(version)


def render_sidebar_footer() -> None:
    """The version, and a plain-text note when a newer release exists (DIST-05)."""
    version = app_version()
    with st.sidebar:
        st.caption(f"F1 Replay {version}")
        notice = _update_notice_cached(version)
        if notice:
            st.caption(notice)


def sidebar_state(selection, query_params) -> str:
    """``initial_sidebar_state`` for this run (UI-19).

    Expanded only while there is nothing to show but the picker. Once a
    session is selected - or a shared link is about to select one - it is
    "auto": collapsed on a phone, where the open sidebar covered the data on
    every first load, and open on a desktop.
    """
    if selection is not None or any(name in query_params for name in ("year", "gp", "session")):
        return "auto"
    return "expanded"


def render_header():
    """Configure the page and inject the shared styles, before any content.

    No title or caption: the session header bar is the title (guideline
    5.2). The fonts are embedded here, in the main document, because a
    browser ignores ``@font-face`` inside a component's shadow root.
    """
    st.set_page_config(
        page_title="F1 Replay",
        layout="wide",
        initial_sidebar_state=sidebar_state(st.session_state.get(SELECTION_KEY), st.query_params),
        menu_items=menu_items(),
    )
    st.html(f"<style>{font_face_css()}{CSS_TOKENS}{APP_CSS}</style>")


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


def selection_label(selection: dict) -> str:
    """A short name for a selection: ``2023 Bahrain - Race`` (en dash)."""
    source = selection.get("source")
    if source == "replay":
        return f"Replay {selection.get('replay_file')}"
    if source == "live":
        return "Live timing"
    gp = str(selection.get("gp") or "").replace(" Grand Prix", "")
    session = SESSION_NAMES.get(str(selection.get("session_type")), selection.get("session_type"))
    return f"{selection.get('year')} {gp} \N{EN DASH} {session}"


def _commit(selection: dict) -> None:
    """Make ``selection`` the one the app loads, and remember it."""
    st.session_state[SELECTION_KEY] = selection
    recent = [s for s in st.session_state.get(RECENT_KEY, []) if s != selection]
    st.session_state[RECENT_KEY] = [selection, *recent][:RECENT_LIMIT]
    if selection.get("source") == "fastf1":
        st.query_params.from_dict(
            {
                "year": str(selection["year"]),
                "gp": str(selection["gp"]),
                "session": str(selection["session_type"]),
            }
        )
    else:
        st.query_params.clear()


LINK_REJECTED_KEY = "link_rejected"
UNKNOWN_LINK = "Link refers to an unknown session"


def _selection_from_url(data_manager) -> dict | None:
    """``?year=2023&gp=Bahrain Grand Prix&session=R`` -> a historical selection.

    The link is checked against the same lists the picker offers (UI-17):
    FastF1 fuzzy-matches event names, so ``?gp=Bahrein`` would otherwise load
    a different Grand Prix than the link names, and a crafted link could
    start cold downloads. A link that names nothing the picker would offer
    sets ``LINK_REJECTED_KEY`` and selects nothing.
    """
    params = st.query_params
    if not any(name in params for name in ("year", "gp", "session")):
        return None
    try:
        year = int(params.get("year", ""))
    except ValueError:
        year = None
    gp, session = params.get("gp") or "", params.get("session") or ""
    valid = (
        year is not None
        and FIRST_SEASON <= year <= datetime.now(UTC).year
        and bool(gp)
        and gp in _event_names_cached(data_manager, year)
        and session in (_session_codes_cached(data_manager, year, gp) or FALLBACK_SESSION_TYPES)
    )
    if not valid:
        st.session_state[LINK_REJECTED_KEY] = True
        st.query_params.clear()
        return None
    return {
        "source": "fastf1",
        "year": year,
        "gp": gp,
        "session_type": session,
        "replay_file": None,
        "telemetry_scope": SCOPE_LABELS["Fastest lap (comparable)"],
    }


def render_session_selector(data_manager) -> dict | None:
    """The sidebar session picker; returns the selection to load, or None.

    Browsing the dropdowns never loads anything: only **Load session** (or a
    Recent entry, or a shared URL on first open) changes the selection. The
    picker is a fragment, so changing a dropdown reruns the sidebar alone -
    the lists still follow each other (Grand Prix by season, sessions by
    weekend format) without redrawing the page.
    """
    if SELECTION_KEY not in st.session_state:
        from_url = _selection_from_url(data_manager)
        st.session_state[SELECTION_KEY] = from_url
        if from_url is not None:
            st.session_state.setdefault("picker_year", from_url["year"])
            st.session_state.setdefault("picker_gp", from_url["gp"])
            st.session_state.setdefault("picker_session", from_url["session_type"])
            st.session_state[RECENT_KEY] = [from_url]

    with st.sidebar:
        _session_picker(data_manager)
    selection = st.session_state.get(SELECTION_KEY)
    if selection is None and st.session_state.get(LINK_REJECTED_KEY):
        st.warning(f"{UNKNOWN_LINK}. Choose a session in the sidebar.")
    return selection


@st.fragment
def _session_picker(data_manager) -> None:
    if _is_race_weekend_cached(data_manager):
        # A session really is on air - but nothing switches silently: the
        # user chooses (LIVE-15).
        st.info("A session is running now")
        if st.button("Go live", key="go_live"):
            _commit(
                {
                    "source": "live",
                    "year": None,
                    "gp": None,
                    "session_type": None,
                    "replay_file": None,
                    "telemetry_scope": SCOPE_LABELS["Fastest lap (comparable)"],
                }
            )
            st.rerun()

    with st.expander("Advanced", expanded=False):
        source_label = st.selectbox("Data source", list(SOURCE_MAP), key="picker_source")
        scope_label = st.radio(
            "Telemetry scope",
            list(SCOPE_LABELS),
            key="picker_scope",
            help=(
                "Fastest lap plots each driver's quickest lap on a 0 -> lap-length "
                "distance axis, so drivers are comparable at the same track "
                "position. Full session plots every lap, with distance "
                "accumulating over the whole run (far heavier to render)."
            ),
        )
    source = SOURCE_MAP[source_label]
    selection: dict = {
        "source": source,
        "year": None,
        "gp": None,
        "session_type": None,
        "replay_file": None,
        "telemetry_scope": SCOPE_LABELS[scope_label],
    }

    ready = True
    if source == "replay":
        # A replay carries its own session identity, so Season/GP/Session
        # would only mislead: the file is the whole selection.
        replays = data_manager.get_available_replays()
        if replays:
            selection["replay_file"] = st.selectbox("Replay file", replays, key="picker_replay")
        else:
            st.caption("No saved replays yet.")
            ready = False
    elif source == "live":
        st.caption("Connects to the F1 SignalR feed while a session is running.")
    else:
        this_year = datetime.now(UTC).year
        seasons = list(range(this_year, FIRST_SEASON - 1, -1))
        year = st.selectbox("Season", seasons, key="picker_year")
        gps = _event_names_cached(data_manager, year)
        if gps:
            if st.session_state.get("picker_gp") not in gps:
                st.session_state.pop("picker_gp", None)
            gp = st.selectbox("Grand Prix", gps, key="picker_gp")
        else:
            st.selectbox("Grand Prix", ["No completed events"], disabled=True)
            st.caption("No completed events for this season. Pick another season.")
            gp, ready = None, False
        session_types = (gp and _session_codes_cached(data_manager, year, gp)) or (
            FALLBACK_SESSION_TYPES
        )
        if st.session_state.get("picker_session") not in session_types:
            st.session_state["picker_session"] = session_types[-1]
        session_type = st.selectbox(
            "Session",
            session_types,
            key="picker_session",
            format_func=lambda code: SESSION_NAMES.get(code, code),
        )
        selection.update(year=year, gp=gp, session_type=session_type)

    if st.button("Load session", type="primary", width="stretch", disabled=not ready):
        _commit(selection)
        st.rerun()

    recent = st.session_state.get(RECENT_KEY, [])
    if recent:
        st.caption("Recent")
        for index, entry in enumerate(recent):
            if st.button(selection_label(entry), key=f"recent_{index}", type="tertiary"):
                _commit(entry)
                st.rerun()


def decimate_by_distance(df: pd.DataFrame, step: float = TelemetryProcessor.DISTANCE_STEP):
    """At most one sample per ``step`` metres of Distance (UX-02).

    FastF1 samples at ~4 Hz plus position ticks; a full-session scope ships
    every one of them to the browser. One point per 5 m (the alignment grid)
    is all a line chart can show, so the rest are dropped - the first sample
    in each 5 m bucket is kept, never an interpolated value, so coded
    channels (gear, DRS) stay real.
    """
    if df is None or df.empty or "Distance" not in df.columns or step <= 0:
        return df
    distance = pd.to_numeric(df["Distance"], errors="coerce")
    buckets = np.floor(distance.to_numpy(float) / float(step))
    keep = np.ones(len(df), dtype=bool)
    keep[1:] = buckets[1:] != buckets[:-1]
    return df[keep]


# The DRS channel reads 0 throughout from 2026: the regulations replaced DRS
# with active aero, so a flat line would only suggest missing data (FEAT-12).
LAST_DRS_SEASON = 2025

TELEMETRY_CHANNELS = {
    "Speed": {"col": "Speed", "unit": "km/h"},
    "Throttle": {"col": "Throttle", "unit": "%"},
    "Brake": {"col": "Brake", "unit": "%"},
    "RPM": {"col": "RPM", "unit": "RPM"},
    "Gear": {"col": "Gear", "unit": ""},
    "DRS": {"col": "DRS", "unit": ""},
}


def telemetry_channels(year=None) -> dict:
    """The channels worth a tab for a season: no DRS from 2026 on."""
    try:
        season = int(year) if year is not None else None
    except (TypeError, ValueError):
        season = None
    if season is not None and season > LAST_DRS_SEASON:
        return {name: cfg for name, cfg in TELEMETRY_CHANNELS.items() if name != "DRS"}
    return dict(TELEMETRY_CHANNELS)


def create_telemetry_chart(
    telemetry_data: dict[str, pd.DataFrame], config: dict, color_map: dict[str, str]
) -> go.Figure | None:
    """Create a multi-driver telemetry line chart.

    WebGL traces (``Scattergl``) on the 5 m grid: twenty drivers over a full
    session were tens of thousands of SVG path points per channel (UX-02).
    """
    col = config["col"]
    unit = config["unit"]
    convert = None
    if col == "Speed":  # UX-12: drawn in the viewer's units
        unit, convert = units.speed_unit(), units.speed

    fig = go.Figure()
    has_data = False

    for driver, df in telemetry_data.items():
        if df.empty or col not in df.columns or "Distance" not in df.columns:
            continue

        has_data = True
        color = color_map.get(driver, NEUTRAL_GREY)
        frame = decimate_by_distance(df)
        suffix = f" {unit}" if unit else ""
        line = dict(color=color, shape="hv") if col == "Gear" else dict(color=color, width=2)
        values = frame[col] if convert is None else convert(pd.to_numeric(frame[col]))
        fig.add_trace(
            go.Scattergl(
                x=frame["Distance"],
                y=values,
                mode="lines",
                name=driver,
                line=line,
                hovertemplate=f"{driver}: %{{y}}{suffix}<br>%{{x:.0f}} m<extra></extra>",
            )
        )

    if not has_data:
        return None

    layout = dict(
        xaxis_title="Distance (m)",
        yaxis_title=f"{col} ({unit})" if unit else col,
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    if col == "Gear":
        # Gear labels are categorical ('N', '1'..'8'); without an explicit
        # order Plotly sorts them lexically and puts N and 1 in odd places.
        layout["yaxis"] = dict(
            type="category",
            categoryorder="array",
            categoryarray=TelemetryProcessor.GEAR_CATEGORIES,
        )
    fig.update_layout(**layout)
    return fig


def render_telemetry_charts(
    telemetry_data: dict[str, pd.DataFrame],
    color_map: dict[str, str],
    year=None,
    uirevision: str | None = None,
):
    """Speed, throttle, brake, RPM, gear and (before 2026) DRS charts.

    ``uirevision`` keeps a zoomed range across reruns and live refreshes.
    """
    if not telemetry_data:
        st.info("No telemetry data available")
        return

    channels = telemetry_channels(year)
    tabs = st.tabs(list(channels))
    for tab, cfg in zip(tabs, channels.values(), strict=True):
        with tab:
            fig = create_telemetry_chart(telemetry_data, cfg, color_map)
            if fig:
                _plot(fig, width="stretch", uirevision=uirevision)
            else:
                st.info(f"No {cfg['col']} data available")
    if "DRS" not in channels:
        st.caption(
            "No DRS chart: from 2026 the regulations replace DRS with active aero, "
            "and the feed's DRS channel reads 0 throughout."
        )


def render_lap_times(
    laps_df: pd.DataFrame,
    color_map: dict[str, str],
    marker_lap: int | None = None,
    track_status: pd.DataFrame | None = None,
    drivers=None,
    uirevision: str | None = None,
):
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
    shown = laps_df if drivers is None else laps_df[laps_df[driver_col].isin(list(drivers))]

    for driver in shown[driver_col].dropna().unique():
        driver_laps = shown[shown[driver_col] == driver].sort_values("LapNumber")
        color = color_map.get(driver, NEUTRAL_GREY)

        lap_times_sec = seconds_series(driver_laps["LapTime"])
        if "IsPitOutLap" in driver_laps.columns:
            pit_out = driver_laps["IsPitOutLap"].fillna(False).astype(bool)
        else:
            pit_out = pd.Series(False, index=driver_laps.index, dtype=bool)

        fig.add_trace(
            go.Scattergl(
                x=driver_laps["LapNumber"],
                y=lap_times_sec,
                mode="lines+markers",
                name=driver,
                line=dict(color=color),
                marker=dict(
                    color=[TEXT if p else color for p in pit_out],
                    size=8,
                    symbol=["diamond" if p else "circle" for p in pit_out],
                ),
                hovertemplate=(
                    f"{driver}: Lap %{{x}}<br>"
                    f"Time: %{{customdata}}<br>"
                    f"Pit: %{{text}}<extra></extra>"
                ),
                customdata=[format_lap(value) for value in lap_times_sec],
                text=["PIT OUT" if p else "" for p in pit_out],
            )
        )

    fig.update_layout(
        xaxis_title="Lap",
        yaxis_title="Lap time (s)",
        height=500,
    )
    shade_neutral_laps(fig, laps_df, track_status)
    _mark_lap(fig, marker_lap)
    _plot(fig, width="stretch", uirevision=uirevision)


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
                marker=dict(color=palette.get(label, NEUTRAL_GREY)),
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
            font=dict(color=color_map.get(driver, NEUTRAL_GREY), size=12),
            align="right",
            xanchor="right",
        )

    fig.update_layout(
        xaxis_title="Lap Number",
        barmode="stack",
        height=max(400, len(stints_df[driver_col].unique()) * 30 + 100),
        margin=dict(l=120),
        yaxis=dict(showticklabels=False),
    )
    _plot(fig, width="stretch")


# Feed state -> (chip label, FLAG_STATES key). Text carries the meaning.
FEED_CHIPS = {
    "idle": ("OFFLINE", "FINISHED"),
    "connecting": ("CONNECTING", "YELLOW"),
    "waiting": ("WAITING", "FINISHED"),
    "live": ("LIVE", "GREEN"),
    "stale": ("STALE", "YELLOW"),
    "reconnecting": ("RECONNECTING", "YELLOW"),
    "auth_required": ("TOKEN NEEDED", "RED"),
    "blocked": ("REFUSED", "RED"),
    "stopped": ("STOPPED", "FINISHED"),
}

# Past this many seconds without a message the caption says so (LIVE-23).
STALE_AFTER_S = 30


def _parse_utc(value) -> datetime | None:
    """An ISO timestamp from the feed (``...Z`` or with an offset), in UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def freshness_caption(last_heartbeat, now: datetime | None = None) -> str:
    """``Last update 3 s ago``, or ``No update for 45 s`` past 30 s (LIVE-23)."""
    moment = _parse_utc(last_heartbeat)
    if moment is None:
        return "No update received yet"
    current = now or datetime.now(UTC)
    seconds = max(int((current - moment).total_seconds()), 0)
    if seconds > STALE_AFTER_S:
        return f"No update for {seconds} s"
    return f"Last update {seconds} s ago"


def token_line(token: str | None, now: datetime | None = None) -> str:
    """One sentence on the subscription token: absent, valid, expired."""
    from data.token_store import token_status

    if not token:
        return "No subscription token: timing, tyres, race control and weather only"
    status = token_status(token, now=now)
    if status["expires_at"] is None:
        return "Subscription token set"
    if status["expired"]:
        return "Subscription token expired - paste a new one under Subscription token"
    return f"Subscription token valid for {status['days_left']} more day(s)"


def render_feed_status(live_client) -> None:
    """The connection state chip, its explanation and the token's expiry."""
    if live_client is None:
        return
    status = live_client.status()
    label, state = FEED_CHIPS.get(status.value, ("UNKNOWN", "FINISHED"))
    parts = [live_client.status_text()]
    stats = getattr(live_client.client, "stats", None)
    if stats is not None and stats.reconnects:
        parts.append(f"{stats.reconnects} reconnect(s)")
    parts.append(token_line(subscription_token()))
    st.html(
        f'<div class="f1-dash" style="display:flex;gap:8px;align-items:center">'
        f"{status_chip(label, state)}"
        f'<span class="f1-dim">{html.escape(" · ".join(parts))}</span></div>'
    )


DELAY_KEY = "live_delay"
MAX_DELAY_S = 300


def live_delay() -> float:
    """The viewer's broadcast delay in seconds, 0 to 300 (LIVE-21)."""
    try:
        value = float(st.session_state.get(DELAY_KEY) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(max(value, 0.0), float(MAX_DELAY_S))


def render_delay_input() -> None:
    """Sidebar input that holds the Live page back to match a TV broadcast."""
    with st.sidebar:
        st.number_input(
            "Broadcast delay (s)",
            min_value=0,
            max_value=MAX_DELAY_S,
            step=5,
            key=DELAY_KEY,
            help="Show the session as it stood this many seconds ago, to match a "
            "delayed TV or streaming picture. The feed itself stays real time.",
        )


def poll_live(data_manager, delay: float) -> dict:
    """``poll_live_data(delay=...)``, tolerating a manager without the argument."""
    if delay:
        try:
            return data_manager.poll_live_data(delay=delay)
        except TypeError:
            pass
    return data_manager.poll_live_data()


LIVE_TABS = ["Telemetry", "Tyres", "Race control", "Weather"]
LIVE_TAB_KEY = "live_tab"


@st.fragment(run_every=3)
def render_live_dashboard(data_manager, processor):
    """Auto-refreshing live view: polls the SignalR buffers every 3 s and
    renders telemetry channels, the track map, tyre stints and lap info."""
    render_feed_status(data_manager.live)
    delay = live_delay()
    snapshot = poll_live(data_manager, delay)
    telemetry = snapshot["telemetry"]
    location = snapshot["location"]
    info = snapshot.get("session_info") or {}

    # The spec dashboard, fed from the *polled* snapshot. It used to be
    # rendered once, outside the fragment, with the empty dict a live session
    # starts from - so the tower, sector cards and map read "No timing data"
    # for the whole session (LIVE-10). The map carries the SC/VSC/RED chip
    # (LIVE-22); there is no second chip under the dashboard any more.
    render_dashboard(snapshot)

    # Car telemetry and positions are the only auth-gated parts of the feed.
    # Timing, tyres, race control and weather work without a token, so the
    # view renders whatever arrived instead of waiting for everything.
    has_car_data = bool(telemetry or location)
    if not has_car_data:
        if subscription_token():
            st.info("Waiting for car telemetry and positions from the F1 SignalR feed")
        else:
            st.warning(
                "Car telemetry and driver positions need an F1TV subscription token "
                f"(set `{TOKEN_ENV_VAR}`, or paste it under Subscription token). Timing, "
                "tyres, race control and weather below do not need one."
            )

    parts = [freshness_caption(info.get("last_heartbeat"))]
    if delay:
        parts.append(f"{delay:g} s behind real time")
    parts.append(f"{len(telemetry)} driver(s) with telemetry, {len(location)} on track")
    st.caption(" · ".join(parts))

    color_map = processor.build_driver_color_map(snapshot["drivers"])

    # No "Timing" tab: the dashboard above is the timing view, and the track
    # map is the dashboard's. Only the open tab is drawn (LIVE-35): with the
    # Weather tab open, the six telemetry figures used to be rebuilt every 3 s.
    tabs = st.tabs(LIVE_TABS, key=LIVE_TAB_KEY, on_change="rerun")
    telemetry_tab, tyres_tab, race_control_tab, weather_tab = tabs
    revision = f"live:{info.get('session_key') or info.get('gp') or 'session'}"
    if telemetry_tab.open:
        with telemetry_tab:
            render_telemetry_charts(
                {d: processor.normalize_units(df.copy()) for d, df in telemetry.items()},
                color_map,
                uirevision=revision,
            )
    if tyres_tab.open:
        with tyres_tab:
            stints_df = processor.process_stints(
                snapshot["stints"], latest_lap=max_lap_number(snapshot["laps"])
            )
            render_tire_strategy(stints_df, color_map, snapshot.get("compound_colors"))
            if not stints_df.empty:
                st.dataframe(stints_df, width="stretch", height=250)
    if race_control_tab.open:
        with race_control_tab:
            render_race_control(snapshot.get("race_control"), limit=25, key="rc:live")
    if weather_tab.open:
        with weather_tab:
            render_weather(snapshot.get("weather"), uirevision=revision)


LIVE_CONTROLS_ENV = "F1_LIVE_CONTROLS"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def live_controls_allowed(environ=None, url: str | None = None, ip: str | None = "") -> bool:
    """Whether this viewer may stop, clear or record the shared feed (LIVE-29).

    The feed is one per process and every tab reads it, so the controls are
    for the person running the app: ``F1_LIVE_CONTROLS=1``, or a browser on
    the same machine - a ``localhost`` URL *and* a loopback socket (Streamlit
    reports ``ip_address`` as None for loopback). Anyone else only reads.
    """
    environ = os.environ if environ is None else environ
    if str(environ.get(LIVE_CONTROLS_ENV, "")).strip() == "1":
        return True
    if not url or ip is not None:
        return False
    host = (urlparse(url).hostname or "").lower()
    return host in _LOCAL_HOSTS


def _viewer_may_control() -> bool:
    try:
        url, ip = st.context.url, st.context.ip_address
    except Exception as exc:  # no script-run context: nobody to grant anything to
        logger.debug("No request context for the live controls: %s", exc)
        return False
    return live_controls_allowed(url=url, ip=ip)


@st.dialog("Stop live timing")
def _confirm_stop(live_client) -> None:
    st.write(
        "This disconnects the live feed for every viewer of this app, and stops "
        "any raw-stream recording. It does not reconnect by itself."
    )
    left, right = st.columns(2)
    if left.button("Stop live timing", type="primary", key="live_stop_confirm"):
        live_client.stop_recording()
        live_client.stop()
        st.rerun()
    if right.button("Cancel", key="live_stop_cancel"):
        st.rerun()


def render_live_controls(live_client):
    """The process-level live controls, for the person running the app only."""
    if not live_client or not _viewer_may_control():
        return

    st.divider()
    st.subheader("Live session controls")

    if live_client.is_recording():
        st.caption(f"Recording · {live_client.recorder.message_count} messages captured")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("View buffered data"):
            st.json(
                {
                    topic: len(live_client.get_buffered_data(topic))
                    for topic in ("CarData.z", "Position.z", "WeatherData")
                }
            )
        # Only the time series: the merged timing state stays, so the tower
        # keeps its order for every viewer (LIVE-29).
        if st.button("Clear buffers"):
            live_client.clear_buffer()
            st.info("Car data, position and weather buffers cleared; timing is kept")

    with col2:
        # Records the raw messages, so a replay feeds the same handler the
        # live client does (LIVE-12). Saving the processed session dict for a
        # live session would have saved the empty dict it starts from.
        if live_client.is_recording():
            if st.button("Stop recording"):
                where = live_client.stop_recording()
                st.success(f"Raw stream saved to {where}")
        elif st.button("Record raw stream"):
            directory = Path(config.replay_dir) / f"raw_{datetime.now(UTC):%Y%m%d_%H%M%S}"
            live_client.start_recording(directory)
            st.info(f"Recording to {directory}")

    with col3:
        if st.button("Stop live"):
            _confirm_stop(live_client)


def render_token_helper(now: datetime | None = None) -> None:
    """A collapsed paste box for the F1TV subscription token (LIVE-24).

    The value goes to ``.env`` only on an explicit Save and is never shown
    back. No automated login: ``fastf1``'s helper starts a blocking local
    auth server, which has no place inside the app.
    """
    from data.token_store import save_subscription_token

    with st.expander("Subscription token", expanded=False):
        st.caption(token_line(subscription_token(), now=now))
        st.caption(
            "Car telemetry and positions need an F1TV subscription. Sign in at "
            "f1tv.formula1.com, open the browser's developer tools, and copy the value "
            "of the login-session cookie (or the JWT inside it). It stays on this "
            "machine, in the .env file."
        )
        token = st.text_input("Token", type="password", key="token_paste")
        if st.button("Save token", key="token_save", disabled=not token):
            try:
                where = save_subscription_token(token, config.env_path)
            except (OSError, ValueError) as exc:
                st.error(f"Could not save the token: {exc}")
            else:
                st.session_state.pop("token_paste", None)
                st.success(f"Saved to {where}. The next connection uses it.")


def render_position_changes(
    laps_df: pd.DataFrame,
    color_map: dict[str, str],
    marker_lap: int | None = None,
    track_status: pd.DataFrame | None = None,
    uirevision: str | None = None,
    drivers=None,
):
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
        if drivers is not None and driver not in drivers:
            continue
        driver_laps = work[work[driver_col] == driver].sort_values("LapNumber")
        fig.add_trace(
            go.Scatter(
                x=driver_laps["LapNumber"],
                y=driver_laps["_pos"],
                mode="lines",
                name=str(driver),
                line=dict(color=color_map.get(driver, NEUTRAL_GREY), width=2),
                hovertemplate=f"{driver}: P%{{y}}<br>Lap %{{x}}<extra></extra>",
                connectgaps=True,
            )
        )

    fig.update_layout(
        xaxis_title="Lap Number",
        # P1 belongs at the top.
        yaxis=dict(title="Position", autorange="reversed", dtick=1, tickformat="d"),
        hovermode="closest",
        height=560,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01),
    )
    shade_neutral_laps(fig, laps_df, track_status)
    _mark_lap(fig, marker_lap)
    _plot(fig, width="stretch", uirevision=uirevision)


def render_weather(
    weather_df: pd.DataFrame, status: DataStatus | None = None, uirevision: str | None = None
):
    """Track/air temperature, humidity, wind and rainfall over the session."""
    if weather_df is None or weather_df.empty:
        show(status or DataStatus.empty("weather data"))
        return

    latest = weather_df.iloc[-1]
    cols = st.columns(5)
    # Wind arrives in m/s and is shown in km/h, matching the dashboard header.
    temp = units.temperature_unit()
    readings = [
        ("Air", "AirTemp", temp, units.temperature),
        ("Track", "TrackTemp", temp, units.temperature),
        ("Humidity", "Humidity", "%", None),
        ("Wind", "WindSpeed", units.speed_unit(), lambda v: units.speed(wind_kmh(v))),
        ("Pressure", "Pressure", "mbar", None),
    ]
    for col, (label, key, unit, convert) in zip(cols, readings, strict=False):
        # The live feed sends these as strings ("21.0"); FastF1 sends floats.
        value = pd.to_numeric(latest.get(key), errors="coerce")
        if convert is not None and pd.notna(value):
            value = convert(value)
        col.metric(
            label,
            f"{float(value):.1f} {unit}" if value is not None and pd.notna(value) else MISSING,
        )

    if "Rainfall" in weather_df.columns and bool(weather_df["Rainfall"].any()):
        st.warning("Rainfall recorded during this session")

    x = _elapsed_minutes(weather_df)
    fig = go.Figure()
    for key, label, color in (
        ("TrackTemp", f"Track temp ({temp})", CHART_WARM),
        ("AirTemp", f"Air temp ({temp})", CHART_COOL),
    ):
        if key in weather_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=units.temperature(pd.to_numeric(weather_df[key], errors="coerce")),
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
                line=dict(color=TEXT_DIM, width=1, dash="dot"),
                yaxis="y2",
            )
        )

    fig.update_layout(
        xaxis_title="Session time (min)",
        yaxis=dict(title=f"Temperature ({temp})"),
        yaxis2=dict(title="Humidity (%)", overlaying="y", side="right", showgrid=False),
        hovermode="x unified",
        height=340,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    _plot(fig, width="stretch", uirevision=uirevision)


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


def race_control_lines(df: pd.DataFrame) -> list[dict]:
    """Race-control rows as plain values for the panel, newest first.

    The message stays text: it is escaped when drawn, so ``*`` or ``$...$`` in
    a steward's message are never read as Markdown or maths (UI-13).
    """
    lines = []
    for _, row in df.iloc[::-1].iterrows():
        flag = str(row.get("Flag") or "").upper()
        lap = pd.to_numeric(row.get("Lap"), errors="coerce")
        message = row.get("Message")
        lines.append(
            {
                "lap": f"L{int(lap)}" if pd.notna(lap) else MISSING,
                "flag": flag if flag and flag not in ("NONE", "NAN", "<NA>") else "",
                "message": "" if message is None or pd.isna(message) else str(message),
            }
        )
    return lines


def race_control_html(lines: list[dict]) -> str:
    """The race-control list as escaped HTML (no Markdown interpretation)."""
    rows = "".join(
        '<div class="f1-rc-row">'
        f'<span class="f1-rc-lap f1-num">{html.escape(line["lap"])}</span>'
        f'<span class="f1-rc-flag">{html.escape(line["flag"])}</span>'
        f'<span class="f1-rc-msg">{html.escape(line["message"])}</span></div>'
        for line in lines
    )
    return f'<div class="f1-rc">{rows}</div>'


def filter_race_control(df: pd.DataFrame, categories=None, search: str = "") -> pd.DataFrame:
    """Messages in ``categories`` (all when empty) whose text contains ``search``."""
    if categories and "Category" in df.columns:
        df = df[df["Category"].astype(str).isin(list(categories))]
    needle = (search or "").strip().lower()
    if needle and "Message" in df.columns:
        df = df[df["Message"].astype(str).str.lower().str.contains(needle, regex=False)]
    return df


def render_race_control(
    race_control_df: pd.DataFrame,
    limit: int = 60,
    status: DataStatus | None = None,
    key: str = "rc",
):
    """Race control feed: flags, safety cars, investigations, penalties.

    ``key`` scopes the filter widgets to one session (UX-06): the old fixed
    ``rc_categories`` key carried one session's category choice into the next.
    """
    if race_control_df is None or race_control_df.empty:
        show(status or DataStatus.empty("race control messages"))
        return

    df = race_control_df.copy()
    categories = sorted({str(c) for c in df.get("Category", pd.Series(dtype=object)).dropna()})
    left, right = st.columns([3, 2])
    chosen = []
    if categories:
        with left:
            chosen = st.multiselect(
                "Filter by category", categories, default=categories, key=f"{key}_categories"
            )
    with right:
        search = st.text_input("Search messages", key=f"{key}_search", placeholder="Car 44")
    df = filter_race_control(df, chosen, search)

    if df.empty:
        st.info("No messages match that filter")
        return

    lines = race_control_lines(df)[:limit]
    st.html(race_control_html(lines))
    if len(df) > limit:
        st.caption(f"Showing the {limit} most recent of {len(df)} messages.")


def render_driver_comparison(
    telemetry_data: dict[str, pd.DataFrame],
    color_map: dict[str, str],
    key_prefix: str = "cmp",
    preselect: tuple = (),
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
        preferred = preselect or ()
        first = usable.index(preferred[0]) if preferred and preferred[0] in usable else 0
        reference = st.selectbox("Reference driver", usable, index=first, key=f"{key_prefix}_ref")
    with col2:
        others = [d for d in usable if d != reference]
        second = others.index(preferred[1]) if len(preferred) > 1 and preferred[1] in others else 0
        compare = st.selectbox("Compared with", others, index=second, key=f"{key_prefix}_cmp")

    ref_df, cmp_df = telemetry_data[reference], telemetry_data[compare]
    delta_distance, delta_seconds = _time_delta(ref_df, cmp_df)

    fig = go.Figure()
    for driver, df in ((reference, ref_df), (compare, cmp_df)):
        fig.add_trace(
            go.Scatter(
                x=df["Distance"],
                y=units.speed(pd.to_numeric(df["Speed"])),
                mode="lines",
                name=driver,
                line=dict(color=color_map.get(driver, NEUTRAL_GREY), width=2),
                hovertemplate=(
                    f"{driver}: %{{y:.0f}} {units.speed_unit()}<br>%{{x:.0f}} m<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        xaxis_title="Distance (m)",
        yaxis_title=f"Speed ({units.speed_unit()})",
        hovermode="x unified",
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    _plot(fig, width="stretch")

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
            line=dict(color=TEXT, width=2),
            hovertemplate="%{y:+.3f} s at %{x:.0f} m<extra></extra>",
        )
    )
    delta_fig.add_hline(y=0, line=dict(color=NEUTRAL_GREY, width=1, dash="dot"))
    delta_fig.update_layout(
        xaxis_title="Distance (m)",
        yaxis_title=f"Δ time (s) — below 0 = {compare} ahead",
        hovermode="x unified",
        height=320,
        showlegend=False,
    )
    _plot(delta_fig, width="stretch")

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


def render_race_trace(
    laps_df: pd.DataFrame,
    color_map: dict[str, str],
    track_status: pd.DataFrame | None = None,
    drivers=None,
    marker_lap: int | None = None,
    key: str = "race_trace",
):
    """Gap to the leader, or to a chosen driver, lap by lap (FEAT-01)."""
    from processing.analysis import race_trace

    driver_col = "DriverAcronym" if "DriverAcronym" in laps_df.columns else "Driver"
    if laps_df.empty or driver_col not in laps_df.columns:
        st.info("No lap data for a race trace")
        return
    names = sorted(laps_df[driver_col].dropna().astype(str).unique())
    reference = st.selectbox(
        "Gap to",
        ["Leader", *names],
        key=f"{key}_reference",
    )
    trace = race_trace(laps_df, reference=None if reference == "Leader" else reference)
    if drivers is not None:
        trace = trace[trace["Driver"].isin(list(drivers))]
    if trace.empty:
        st.info("No lap completion times for a race trace")
        return

    fig = go.Figure()
    for driver, rows in trace.groupby("Driver", sort=False):
        fig.add_trace(
            go.Scattergl(
                x=rows["LapNumber"],
                y=rows["Gap"],
                mode="lines",
                name=str(driver),
                line=dict(color=color_map.get(driver, NEUTRAL_GREY), width=2),
                hovertemplate=f"{driver}: Lap %{{x}}<br>Gap %{{y:.3f}} s<extra></extra>",
            )
        )
    fig.update_layout(
        xaxis_title="Lap",
        yaxis_title=f"Gap to {reference.lower() if reference == 'Leader' else reference} (s)",
        # Behind reads downwards, as on a race trace.
        yaxis=dict(autorange="reversed"),
        height=500,
    )
    shade_neutral_laps(fig, laps_df, track_status)
    _mark_lap(fig, marker_lap)
    _plot(fig, width="stretch")
    st.caption("Gap at the line: the difference between the moments two cars completed the lap.")


def render_tyre_pace(
    laps_df: pd.DataFrame,
    track_status: pd.DataFrame | None = None,
    compound_colors: dict[str, str] | None = None,
    drivers=None,
    total_laps: int | None = None,
):
    """Fuel-corrected lap time against tyre age, and the slope per stint (FEAT-03)."""
    from processing.analysis import FUEL_SECONDS_PER_LAP, degradation, stint_pace

    pace = stint_pace(laps_df, track_status, total_laps=total_laps)
    if drivers is not None:
        pace = pace[pace["Driver"].isin(list(drivers))]
    if pace.empty:
        st.info("No clean racing laps to measure tyre pace")
        return

    palette = compound_palette(compound_colors)
    fig = go.Figure()
    for compound, rows in pace.groupby("Compound", sort=True):
        fig.add_trace(
            go.Scattergl(
                x=rows["TyreLife"],
                y=rows["Corrected"],
                mode="markers",
                name=str(compound),
                marker=dict(color=palette.get(str(compound), NEUTRAL_GREY), size=7),
                customdata=np.stack(
                    [
                        rows["Driver"],
                        rows["LapNumber"],
                        [format_lap(v) for v in rows["LapSeconds"]],
                    ],
                    axis=-1,
                ),
                hovertemplate=(
                    "%{customdata[0]} lap %{customdata[1]}<br>"
                    "Tyre age %{x}<br>Lap %{customdata[2]}<br>"
                    "Corrected %{y:.3f} s<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        xaxis_title="Tyre age (laps)",
        yaxis_title="Fuel-corrected lap time (s)",
        hovermode="closest",
        height=480,
    )
    _plot(fig, width="stretch")

    slopes = degradation(pace)
    if not slopes.empty:
        table = slopes.rename(
            columns={
                "FirstLap": "From lap",
                "LastLap": "To lap",
                "SecondsPerLap": "s per lap",
            }
        )
        st.dataframe(table, hide_index=True, width="stretch")
    st.caption(
        f"In- and out-laps, laps under SC, VSC or red flag, deleted laps and lap 1 are left "
        f"out. The fuel correction is an estimate: {FUEL_SECONDS_PER_LAP:.2f} s for every lap "
        "of fuel still on board."
    )


def render_speed_traps(laps_df: pd.DataFrame, drivers=None):
    """Each driver's best reading at every speed trap, fastest first (FEAT-09)."""
    from processing.analysis import speed_trap_ranking

    ranking = speed_trap_ranking(laps_df)
    if not ranking:
        st.info("No speed-trap readings in this session")
        return
    columns = st.columns(len(ranking))
    for column, (name, table) in zip(columns, ranking.items(), strict=True):
        shown = table if drivers is None else table[table["Driver"].isin(list(drivers))]
        shown = shown.assign(
            Pos=range(1, len(shown) + 1),
            Speed=units.speed(shown["Speed"]).round(0).astype(int),
            Lap=pd.to_numeric(shown["Lap"], errors="coerce").astype("Int64"),
        )[["Pos", "Driver", "Speed", "Lap"]]
        with column:
            st.markdown(f"**{name}** ({units.speed_unit()})")
            st.dataframe(shown, hide_index=True, width="stretch")


def render_deleted_laps(laps_df: pd.DataFrame):
    """Laps the stewards deleted, with the reason they gave (FEAT-11)."""
    from processing.analysis import deleted_laps

    table = deleted_laps(laps_df)
    if table.empty:
        st.info("No lap times were deleted in this session")
        return
    table = table.assign(
        LapNumber=pd.to_numeric(table["LapNumber"], errors="coerce").astype("Int64"),
        LapSeconds=[format_lap(v) for v in table["LapSeconds"]],
        Reason=[str(r) if r else MISSING for r in table["Reason"]],
    ).rename(columns={"LapNumber": "Lap", "LapSeconds": "Lap time"})
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(f"{len(table)} lap time(s) deleted.")


@st.cache_data(ttl=3600, show_spinner=False)
def _standings_cached(_data_manager, year: int, round_num: int | None) -> dict:
    return _data_manager.jolpica.standings(year, round_num)


def render_standings(data_manager, session_info: dict) -> None:
    """Both championships after this round, from Jolpica (FEAT-06)."""
    year, round_num = session_info.get("year"), session_info.get("round")
    if not year:
        return
    try:
        tables = _standings_cached(data_manager, int(year), round_num)
    except Exception as exc:  # network, rate limit, or an unexpected payload
        logger.warning("Standings unavailable: %s", exc)
        st.caption("Championship standings are unavailable right now (Jolpica).")
        return
    drivers, teams = tables.get("drivers"), tables.get("constructors")
    if (drivers is None or drivers.empty) and (teams is None or teams.empty):
        st.caption("No championship standings for this season yet.")
        return
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Drivers**")
        st.dataframe(drivers, hide_index=True, width="stretch")
    with right:
        st.markdown("**Constructors**")
        st.dataframe(teams, hide_index=True, width="stretch")
