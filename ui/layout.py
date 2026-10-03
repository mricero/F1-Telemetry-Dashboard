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
from plotly.subplots import make_subplots

from config import config
from data.fastf1_adapter import session_codes_for_event
from data.live_adapter import TOKEN_ENV_VAR, subscription_token
from data.openf1_adapter import FIRST_YEAR as OPENF1_FIRST_YEAR
from data.openf1_adapter import get_team_radio
from processing.lap_compare import SCOPE_SESSION as COMPARE_SESSION_SCOPE
from processing.lap_compare import (
    available_laps,
    corner_markers,
    fastest_lap_number,
    lap_telemetry,
    lap_times,
    shared_grid,
)
from processing.telemetry_processor import TelemetryProcessor, max_lap_number
from processing.time_utils import seconds_series
from processing.timing import (
    MISSING,
    format_delta,
    format_lap,
    gap_trace,
    is_race_session,
    is_raining,
)
from processing.track_periods import lap_spans, lap_states
from processing.units import speed_from_kmh, speed_label, temp_from_c, temp_label
from ui.dashboard import render_dashboard, wind_kmh
from ui.fonts import font_face_css
from ui.preferences import format_wall_clock, sync_preference_params, units
from ui.standings import render_standings
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


@st.cache_data(ttl=3600, show_spinner=False)
def _update_notice_cached(version: str) -> str | None:
    from data.update_check import update_notice

    return update_notice(version)


def render_sidebar_footer() -> None:
    """The running version and, when there is one, the newer release (REPO-23,
    DIST-05). The check runs at most hourly here and daily against GitHub."""
    version = app_version()
    st.caption(f"F1 Replay {version}")
    notice = _update_notice_cached(version)
    if notice:
        st.caption(notice)


def _folder_size(path) -> int:
    total = 0
    for item in Path(path).rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


@st.cache_data(ttl=60, show_spinner=False)
def _folder_size_cached(path: str) -> int:
    return _folder_size(path) if Path(path).exists() else 0


def clear_cached_schedules() -> None:
    """Drop the event lists and race-weekend probe (stale on a race weekend)."""
    for cached in (_is_race_weekend_cached, _event_names_cached, _session_codes_cached):
        cached.clear()


def clear_loaded_sessions() -> None:
    """Drop the sessions held in memory: the runtime cache and FastF1's."""
    from data.fastf1_adapter import clear_session_cache
    from data.runtime_cache import runtime_cache

    runtime_cache.clear()
    clear_session_cache()


def render_settings() -> None:
    """Caches, data locations and the version (UI-22, CACHE-04)."""
    st.subheader("Caches")
    left, right = st.columns(2)
    with left:
        st.caption(
            "The event list and the race-weekend check are kept for up to an hour. "
            "Clear them to see a session that has just been added."
        )
        if st.button("Clear cached schedules", key="settings_clear_schedules"):
            clear_cached_schedules()
            st.success("Schedules cleared. The next selection fetches them again.")
    with right:
        st.caption("Loaded sessions stay in memory for instant re-selection until the app closes.")
        if st.button("Clear loaded sessions", key="settings_clear_sessions"):
            clear_loaded_sessions()
            st.success("Loaded sessions cleared. The next load reads them again.")

    st.subheader("Where data lives")
    size_mb = _folder_size_cached(str(config.fastf1_cache_dir)) / (1024 * 1024)
    rows = [
        ("FastF1 cache", f"{config.fastf1_cache_dir} ({size_mb:,.0f} MB)"),
        ("Replays", str(config.replay_dir)),
        ("Records", str(config.metrics_store_path)),
        ("Settings file", str(config.env_path)),
    ]
    st.html(
        '<table class="f1-kv">'
        + "".join(
            f"<tr><th>{html.escape(name)}</th><td>{html.escape(value)}</td></tr>"
            for name, value in rows
        )
        + "</table>"
    )
    st.caption(
        "The FastF1 cache can be deleted while the app is closed; sessions download "
        "again on their next load. Set FASTF1_CACHE_DIR to move it."
    )

    st.subheader("About")
    st.caption(f"F1 Replay {app_version()}. Not affiliated with Formula 1.")


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
    # Favourites (UX-03) stay in a copied link after a session load rewrote it.
    sync_preference_params()


# Both helpers below hit the network. Streamlit re-runs this module top to
# bottom on every widget interaction, so without caching the schedule and the
# race-weekend probe would be re-fetched on every click.
# A session becomes selectable once it has started, so on a race weekend the
# lists change every few hours; an hour-old list hid a session that had just
# run (CACHE-03). Settings can clear them at once.
SCHEDULE_TTL_SECONDS = 900


@st.cache_data(ttl=900, show_spinner=False)
def _is_race_weekend_cached(_data_manager) -> bool:
    return _data_manager._is_race_weekend()


@st.cache_data(ttl=SCHEDULE_TTL_SECONDS, show_spinner=False)
def _event_names_cached(_data_manager, year: int) -> list:
    meetings = _data_manager.fastf1.get_available_sessions(year)
    if meetings is None or meetings.empty or "EventName" not in meetings.columns:
        return []
    return sorted(meetings["EventName"].dropna().unique().tolist())


# Sprint weekends have no FP2/FP3 but do have SQ, so the session list comes
# from the event's own schedule rather than a fixed six-entry list.
FALLBACK_SESSION_TYPES = ["FP1", "FP2", "FP3", "Q", "S", "R"]


@st.cache_data(ttl=SCHEDULE_TTL_SECONDS, show_spinner=False)
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
    gp, session = params.get("gp"), params.get("session")
    valid = (
        year is not None
        and FIRST_SEASON <= year <= datetime.now(UTC).year
        and bool(gp)
        and gp in _event_names_cached(data_manager, year)
        and session
        in (_session_codes_cached(data_manager, year, str(gp)) or FALLBACK_SESSION_TYPES)
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
        render_sidebar_footer()
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
    telemetry_data: dict[str, pd.DataFrame],
    config: dict,
    color_map: dict[str, str],
    speed_unit: str = "kmh",
) -> go.Figure | None:
    """Create a multi-driver telemetry line chart.

    ``speed_unit`` (``kmh`` or ``mph``) converts the Speed channel for display
    (UX-12); the data itself stays in km/h.

    WebGL traces (``Scattergl``) on the 5 m grid: twenty drivers over a full
    session were tens of thousands of SVG path points per channel (UX-02).
    """
    col = config["col"]
    unit = config["unit"]
    convert_speed = col == "Speed" and speed_unit != "kmh"
    if convert_speed:
        unit = speed_label(speed_unit)

    fig = go.Figure()
    has_data = False

    for driver, df in telemetry_data.items():
        if df.empty or col not in df.columns or "Distance" not in df.columns:
            continue

        has_data = True
        color = color_map.get(driver, NEUTRAL_GREY)
        frame = decimate_by_distance(df)
        values = speed_from_kmh(frame[col], speed_unit) if convert_speed else frame[col]
        suffix = f" {unit}" if unit else ""
        y_format = ":.1f" if convert_speed else ""
        line = dict(color=color, shape="hv") if col == "Gear" else dict(color=color, width=2)
        fig.add_trace(
            go.Scattergl(
                x=frame["Distance"],
                y=values,
                mode="lines",
                name=driver,
                line=line,
                hovertemplate=f"{driver}: %{{y{y_format}}}{suffix}<br>%{{x:.0f}} m<extra></extra>",
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
            fig = create_telemetry_chart(telemetry_data, cfg, color_map, units().speed)
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
    # A failed recorder (disk full, folder gone) stops recording, not the
    # feed; say so, or the raw stream silently ends (LIVE-31).
    error = getattr(live_client, "recorder_error", None)
    if error:
        st.warning(error)


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
    st.subheader("Championship")
    render_standings(snapshot)


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
    """Lap-by-lap running order - who gained and lost places, and when.

    ``drivers`` limits the lines to the Analysis selection (UX-03).
    """
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


def _wind_speed(wind_ms, unit: str):
    """Wind from the feed's m/s in km/h or mph; None when unavailable."""
    kmh = wind_kmh(wind_ms)
    return None if kmh is None else speed_from_kmh(kmh, unit)


def render_weather(
    weather_df: pd.DataFrame, status: DataStatus | None = None, uirevision: str | None = None
):
    """Track/air temperature, humidity, wind and rainfall over the session."""
    if weather_df is None or weather_df.empty:
        show(status or DataStatus.empty("weather data"))
        return

    latest = weather_df.iloc[-1]
    cols = st.columns(5)
    chosen = units()
    degrees = temp_label(chosen.temp)
    # Wind arrives in m/s and is shown in km/h (or mph), matching the header.
    readings = [
        ("Air", "AirTemp", degrees, lambda v: temp_from_c(v, chosen.temp)),
        ("Track", "TrackTemp", degrees, lambda v: temp_from_c(v, chosen.temp)),
        ("Humidity", "Humidity", "%", None),
        (
            "Wind",
            "WindSpeed",
            speed_label(chosen.speed),
            lambda v: _wind_speed(v, chosen.speed),
        ),
        ("Pressure", "Pressure", "mbar", None),
    ]
    for col, (label, key, unit, convert) in zip(cols, readings, strict=False):
        # The live feed sends these as strings ("21.0"); FastF1 sends floats.
        value = pd.to_numeric(latest.get(key), errors="coerce")
        if convert is not None:
            value = convert(value)
        col.metric(label, f"{value:g} {unit}" if pd.notna(value) else MISSING)

    if "Rainfall" in weather_df.columns and any(map(is_raining, weather_df["Rainfall"])):
        st.warning("Rainfall recorded during this session")

    x = _elapsed_minutes(weather_df)
    fig = go.Figure()
    for key, label, color in (
        ("TrackTemp", f"Track temp ({degrees})", CHART_WARM),
        ("AirTemp", f"Air temp ({degrees})", CHART_COOL),
    ):
        if key in weather_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=temp_from_c(pd.to_numeric(weather_df[key], errors="coerce"), chosen.temp),
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
        yaxis=dict(title=f"Temperature ({degrees})"),
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


def race_control_lines(df: pd.DataFrame, clock=None) -> list[dict]:
    """Race-control rows as plain values for the panel, newest first.

    ``clock`` turns a row's wall-clock ``Time`` into the text shown (a time of
    day with its zone, UX-12); without it, or without a time, ``time`` is "".

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
                "time": clock(row.get("Time")) if clock is not None else "",
                "lap": f"L{int(lap)}" if pd.notna(lap) else MISSING,
                "flag": flag if flag and flag not in ("NONE", "NAN", "<NA>") else "",
                "message": "" if message is None or pd.isna(message) else str(message),
            }
        )
    return lines


def race_control_html(lines: list[dict]) -> str:
    """The race-control list as escaped HTML (no Markdown interpretation)."""
    timed = any(line.get("time") for line in lines)
    rows = "".join(
        '<div class="f1-rc-row">'
        + (
            f'<span class="f1-rc-time f1-num">{html.escape(line.get("time") or MISSING)}</span>'
            if timed
            else ""
        )
        + f'<span class="f1-rc-lap f1-num">{html.escape(line["lap"])}</span>'
        f'<span class="f1-rc-flag">{html.escape(line["flag"])}</span>'
        f'<span class="f1-rc-msg">{html.escape(line["message"])}</span></div>'
        for line in lines
    )
    return f'<div class="f1-rc{" f1-rc-timed" if timed else ""}">{rows}</div>'


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
    info: dict | None = None,
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

    lines = race_control_lines(df, lambda stamp: format_wall_clock(stamp, info))[:limit]
    st.html(race_control_html(lines))
    if len(df) > limit:
        st.caption(f"Showing the {limit} most recent of {len(df)} messages.")


# Stacked channels of the head-to-head, top to bottom: (title, column, unit).
COMPARISON_PANELS = (
    ("Speed", "Speed", "km/h"),
    ("Throttle", "Throttle", "%"),
    ("Brake", "Brake", "%"),
    ("Gear", "nGear", ""),
)


def _lap_label(lap: int, seconds: float | None = None) -> str:
    return f"Lap {lap} ({format_lap(seconds)})" if seconds else f"Lap {lap}"


def _lap_picker(column, label: str, key: str, driver: str, laps_df, frame, scope):
    """A lap selectbox for one driver; returns (lap number or None, frame of that lap)."""
    options = available_laps(frame, laps_df, driver, scope)
    if not options:
        # No lap table (or a live feed): the frame itself is the lap.
        with column:
            st.caption(f"{driver}: lap number unknown")
        return None, lap_telemetry(frame, laps_df, driver, None, scope)
    times = lap_times(laps_df, driver)
    fastest = fastest_lap_number(laps_df, driver)
    index = options.index(fastest) if fastest in options else 0
    with column:
        lap = st.selectbox(
            label,
            options,
            index=index,
            format_func=lambda n: _lap_label(n, times.get(n)),
            key=f"{key}_lap_{driver}",
        )
    return lap, lap_telemetry(frame, laps_df, driver, lap, scope)


def render_driver_comparison(
    telemetry_data: dict[str, pd.DataFrame],
    color_map: dict[str, str],
    key_prefix: str = "cmp",
    preselect: tuple = (),
    session_data: dict | None = None,
    laps: pd.DataFrame | None = None,
):
    """Head-to-head: Speed, Throttle, Brake, Gear and the time delta, stacked.

    The panels share one distance axis and carry the circuit's corner numbers
    when known. Each driver has a lap picker; with the fastest-lap scope only
    that lap is loaded, with the full-session scope any timed lap can be
    chosen (cut out of the run by its time window). The delta is integrated
    from the speed traces on the shared distance grid rather than via
    ``fastf1.utils.delta_time``, which is deprecated since FastF1 3.0 and
    emits a FutureWarning. DRS is not plotted, so 2026+ needs no special case.
    """
    session_data = session_data or {}
    scope = (session_data.get("session_info") or {}).get("telemetry_scope")
    raw = session_data.get("telemetry") or telemetry_data
    laps_df = laps if laps is not None else session_data.get("laps")
    usable = sorted(
        d
        for d, df in telemetry_data.items()
        if not df.empty and {"Distance", "Speed"}.issubset(df.columns)
    )
    if len(usable) < 2:
        st.info("Need telemetry for at least two drivers to compare")
        return

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        preferred = preselect or ()
        first = usable.index(preferred[0]) if preferred and preferred[0] in usable else 0
        reference = st.selectbox("Reference driver", usable, index=first, key=f"{key_prefix}_ref")
    with col3:
        others = [d for d in usable if d != reference]
        second = others.index(preferred[1]) if len(preferred) > 1 and preferred[1] in others else 0
        compare = st.selectbox("Compared with", others, index=second, key=f"{key_prefix}_cmp")

    ref_frame = raw.get(reference, telemetry_data[reference])
    cmp_frame = raw.get(compare, telemetry_data[compare])
    ref_lap, ref_df = _lap_picker(
        col2, "Reference lap", key_prefix, reference, laps_df, ref_frame, scope
    )
    cmp_lap, cmp_df = _lap_picker(
        col4, "Compared lap", key_prefix, compare, laps_df, cmp_frame, scope
    )
    if scope != COMPARE_SESSION_SCOPE:
        st.caption(
            "Only each driver's fastest lap is loaded. Choose 'Full session' under "
            "Telemetry scope (sidebar, Advanced) to pick other laps."
        )

    if ref_df.empty or cmp_df.empty:
        st.info("No telemetry for the chosen lap")
        return
    ref_g, cmp_g = shared_grid(ref_df, cmp_df)
    if len(ref_g) < 10 or not {"Distance", "Speed"}.issubset(ref_g.columns):
        st.info("Not enough telemetry on the chosen laps to compare")
        return
    delta_distance, delta_seconds = _time_delta(ref_g, cmp_g)

    panels = [p for p in COMPARISON_PANELS if p[1] in ref_g.columns and p[1] in cmp_g.columns]
    rows = len(panels) + (1 if delta_seconds is not None else 0)
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.025)
    names = {
        reference: f"{reference} {_lap_label(ref_lap)}" if ref_lap else reference,
        compare: f"{compare} {_lap_label(cmp_lap)}" if cmp_lap else compare,
    }
    for row, (title, col, unit) in enumerate(panels, start=1):
        for driver, frame in ((reference, ref_g), (compare, cmp_g)):
            line = dict(color=color_map.get(driver, NEUTRAL_GREY), width=2)
            if driver == compare and col != "Speed":
                line["dash"] = "dot"
            if col == "nGear":
                line["shape"] = "hv"
            fig.add_trace(
                go.Scatter(
                    x=frame["Distance"],
                    y=frame[col],
                    mode="lines",
                    name=names[driver],
                    legendgroup=driver,
                    showlegend=row == 1,
                    line=line,
                    hovertemplate=f"{driver} {title}: %{{y:.0f}} {unit}<extra></extra>",
                ),
                row=row,
                col=1,
            )
        fig.update_yaxes(title_text=f"{title} ({unit})" if unit else title, row=row, col=1)
        if col == "nGear":
            fig.update_yaxes(
                range=[0.5, 8.5], tickmode="array", tickvals=list(range(1, 9)), row=row, col=1
            )
    if delta_seconds is not None:
        fig.add_trace(
            go.Scatter(
                x=delta_distance,
                y=delta_seconds,
                mode="lines",
                name="Delta",
                showlegend=False,
                line=dict(color=TEXT, width=2),
                hovertemplate="%{y:+.3f} s<extra></extra>",
            ),
            row=rows,
            col=1,
        )
        fig.add_hline(y=0, line=dict(color=NEUTRAL_GREY, width=1, dash="dot"), row=rows, col=1)
        fig.update_yaxes(title_text=f"Δt (s), below 0 = {compare} ahead", row=rows, col=1)

    corners = corner_markers(session_data.get("circuit_info"), float(ref_g["Distance"].iloc[-1]))
    for distance, label in corners:
        fig.add_vline(x=distance, line=dict(color=TEXT_DIM, width=1, dash="dot"), opacity=0.4)
        fig.add_annotation(
            x=distance,
            y=1,
            yref="paper",
            text=label,
            showarrow=False,
            yshift=8,
            font=dict(size=10, color=TEXT_DIM),
        )
    axis = chart_layout(0)["xaxis"]
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    fig.update_xaxes(title_text="Distance (m)", row=rows, col=1)
    fig.update_layout(
        height=140 * rows + 120,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1),
        margin=dict(l=56, r=16, t=40, b=40),
    )
    _plot(
        fig,
        width="stretch",
        uirevision=f"{key_prefix}:{reference}:{ref_lap}:{compare}:{cmp_lap}",
    )
    if not corners:
        st.caption("No corner positions for this circuit.")

    if delta_seconds is None:
        st.caption("Not enough overlapping distance to compute a time delta.")
        return

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


@st.cache_data(ttl=3600, show_spinner=False)
def _team_radio_cached(
    year: int,
    session_name: str,
    date: str | None,
    country: str | None,
    drivers: pd.DataFrame | None,
    session_start: float | None,
) -> pd.DataFrame:
    """OpenF1 team radio, fetched once an hour per session (FEAT-05)."""
    stamp = pd.to_datetime(date, errors="coerce") if date else None
    return get_team_radio(year, session_name, stamp, country, drivers, session_start)


def render_team_radio(session_data: dict) -> None:
    """The session's team radio as a table of links (FEAT-05).

    The page never fetches a recording: each row links to the MP3 and the
    viewer opens it. OpenF1 has them from 2023 on.
    """
    info = session_data.get("session_info") or {}
    year = info.get("year")
    name = info.get("session_name")
    if session_data.get("is_live") or not year or not name:
        show(DataStatus.unavailable("Team radio is listed for finished sessions only"))
        return
    if int(year) < OPENF1_FIRST_YEAR:
        show(DataStatus.unavailable(f"OpenF1 has team radio from {OPENF1_FIRST_YEAR} onwards"))
        return
    date = info.get("date")
    try:
        radio = _team_radio_cached(
            int(year),
            str(name),
            None if date is None else str(date),
            info.get("country"),
            session_data.get("drivers"),
            info.get("session_start"),
        )
    except ConnectionError:
        show(DataStatus.unavailable("OpenF1 could not be reached, so team radio is not listed"))
        return
    if radio.empty:
        show(DataStatus.empty("team radio on OpenF1"))
        return
    table = radio.copy()
    table["Clock"] = [
        "" if pd.isna(t) else f"{int(t) // 3600}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}"
        for t in table["Time"]
    ]
    st.caption(
        f"{len(table)} recordings from OpenF1. Each link opens the audio on F1's server; "
        "the clock is approximate."
    )
    st.dataframe(
        table[["Clock", "Driver", "Url"]],
        hide_index=True,
        width="stretch",
        column_config={"Url": st.column_config.LinkColumn("Recording", display_text="Open")},
    )


# --- Analysis sections over the laps frame (IMPROVEMENTS.md 3.8) -------------
#
# Each section has a pure builder (a figure or an HTML string, testable without
# a Streamlit runtime) and a ``render_*`` function that draws it. The numbers
# come from ``processing.timing``.

LEADER = "Leader"
NOT_A_RACE = (
    "The race trace needs a race or sprint: in other sessions the cars do not share a "
    "start, so a gap at the timing line has no meaning."
)


def _line_styles(drivers, color_map: dict[str, str]) -> dict[str, dict]:
    """Team colour per driver; the second car of a team is dashed (guideline 5.6)."""
    seen: set[str] = set()
    styles = {}
    for driver in drivers:
        colour = color_map.get(driver, NEUTRAL_GREY)
        styles[driver] = {"color": colour, "width": 2, "dash": "dash" if colour in seen else None}
        seen.add(colour)
    return styles


def race_trace_figure(
    laps: pd.DataFrame,
    color_map: dict[str, str],
    reference: str | None = None,
    track_status: pd.DataFrame | None = None,
    marker_lap: int | None = None,
) -> go.Figure | None:
    """Gap at the line per lap, one line per driver; ``None`` without lap times.

    The y axis runs downwards (the leader, or the cars ahead of the
    reference, at the top), as on a timing screen. SC, VSC and red-flag laps
    are shaded with their word.
    """
    trace = gap_trace(laps, reference=reference)
    if trace.empty:
        return None
    last = trace.sort_values("LapNumber").groupby("Driver")[["LapNumber", "Gap"]].last()
    # Legend and hover in running order at each driver's last lap.
    order = last.sort_values(["LapNumber", "Gap"], ascending=[False, True]).index
    styles = _line_styles(order, color_map)
    fig = go.Figure()
    for driver in order:
        rows = trace[trace["Driver"] == driver].sort_values("LapNumber")
        fig.add_trace(
            go.Scatter(
                x=rows["LapNumber"],
                y=rows["Gap"],
                mode="lines",
                name=str(driver),
                line=styles[driver],
                customdata=[format_delta(value) for value in rows["Gap"]],
                hovertemplate=f"{driver} %{{customdata}}<extra></extra>",
            )
        )
    target = "leader" if reference is None else reference
    fig.update_layout(
        xaxis_title="Lap",
        yaxis={"title": f"Gap to {target} (s)", "autorange": "reversed"},
        height=520,
    )
    shade_neutral_laps(fig, laps, track_status)
    _mark_lap(fig, marker_lap)
    return fig


def render_race_trace(
    laps: pd.DataFrame,
    color_map: dict[str, str],
    session_info: dict | None = None,
    track_status: pd.DataFrame | None = None,
    marker_lap: int | None = None,
    key: str = "race_trace",
    uirevision: str | None = None,
) -> None:
    """The race trace section: gap to the leader or to a chosen driver (FEAT-01)."""
    if not is_race_session(session_info):
        st.info(NOT_A_RACE)
        return
    drivers = sorted(set(gap_trace(laps)["Driver"]))
    if not drivers:
        st.info("No lap completion times for this session, so there is no race trace.")
        return
    choice = st.selectbox("Gap to", [LEADER, *drivers], key=f"{key}_reference")
    reference = None if choice == LEADER else choice
    fig = race_trace_figure(laps, color_map, reference, track_status, marker_lap)
    if fig is None:
        st.info(f"{choice} completed no laps, so there is nothing to measure from.")
        return
    _plot(fig, width="stretch", uirevision=uirevision)
    st.caption(
        "Gap when each car crossed the timing line to complete the lap. "
        "Shaded laps ran under a safety car, VSC or red flag."
    )


def tyre_pace_figure(
    pace: pd.DataFrame, compound_colors: dict[str, str] | None = None
) -> go.Figure | None:
    """Fuel-corrected lap time against tyre age, one marker colour per compound.

    ``pace`` is :func:`processing.pace.stint_pace`'s output. The compound
    letter is in the legend and the hover, so colour is not the only carrier.
    """
    if pace is None or pace.empty:
        return None
    palette = compound_palette(compound_colors)
    fig = go.Figure()
    for compound, rows in pace.groupby("Compound", sort=True):
        fig.add_trace(
            go.Scatter(
                x=rows["TyreAge"],
                y=rows["FuelCorrected"],
                mode="markers",
                name=str(compound),
                marker={"color": palette.get(str(compound), NEUTRAL_GREY), "size": 6},
                customdata=[
                    f"{driver} lap {lap}"
                    for driver, lap in zip(rows["Driver"], rows["LapNumber"], strict=True)
                ],
                hovertemplate=(
                    "%{customdata}<br>tyre age %{x:.0f} laps<br>%{y:.3f} s<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        xaxis_title="Tyre age (laps)",
        yaxis_title="Fuel-corrected lap time (s)",
        showlegend=True,
        height=480,
    )
    return fig


def degradation_table(summary: pd.DataFrame) -> pd.DataFrame:
    """Per-compound degradation formatted for display (seconds per lap of tyre age)."""
    if summary is None or summary.empty:
        return pd.DataFrame(columns=["Compound", "Stints", "Laps", "Loss s/lap"])
    return pd.DataFrame(
        {
            "Compound": summary["Compound"],
            "Stints": summary["Stints"],
            "Laps": summary["Laps"],
            "Loss s/lap": [f"{value:+.3f}" for value in summary["Slope"]],
        }
    )


def render_tyre_pace(
    laps: pd.DataFrame,
    session_info: dict | None = None,
    track_status: pd.DataFrame | None = None,
    compound_colors: dict[str, str] | None = None,
    uirevision: str | None = None,
) -> None:
    """Tyre degradation: clean laps against tyre age per compound (FEAT-03)."""
    from processing.pace import compound_degradation, stint_pace

    # Qualifying and practice fuel loads differ run to run, so only races are corrected.
    race = is_race_session(session_info)
    pace = stint_pace(laps, track_status, fuel_correct=race)
    fig = tyre_pace_figure(pace, compound_colors)
    if fig is None:
        st.info(
            "No clean laps with a tyre compound in this session, so there is no "
            "degradation to show."
        )
        return
    _plot(fig, width="stretch", uirevision=uirevision)
    table = degradation_table(compound_degradation(pace))
    if table.empty:
        st.info("No stint has enough clean laps to fit a trend.")
    else:
        st.dataframe(table, hide_index=True, width="stretch")
    st.caption(
        "Loss per lap is the median across stints of a straight-line fit to lap time "
        "against tyre age."
        + (" Lap times are corrected for fuel burned." if race else "")
        + " In-laps, out-laps, the first lap, safety car, VSC and red-flag laps and "
        "inaccurately timed laps are left out."
    )


def rejoin_sentence(driver: str, lap: int, result: dict) -> str:
    """One literal sentence for a predicted rejoin (FEAT-02)."""
    parts = [
        f"If {driver} pitted at the end of lap {lap}, it would rejoin in P{result['position']}"
    ]
    if result["ahead"] is not None:
        parts.append(f"{result['gap_ahead']:.3f} s behind {result['ahead']}")
    if result["behind"] is not None:
        parts.append(f"{result['gap_behind']:.3f} s ahead of {result['behind']}")
    return ", ".join(parts) + "."


def render_pit_rejoin(
    laps: pd.DataFrame,
    session_info: dict | None = None,
    track_status: pd.DataFrame | None = None,
    focus: str | None = None,
    lap: int | None = None,
    key: str = "pit_rejoin",
) -> None:
    """Pit rejoin predictor: current gap plus the circuit's pit loss (FEAT-02)."""
    from processing.pit_loss import pit_loss_for, rejoin_after_lap

    if not is_race_session(session_info):
        st.info(
            "The pit rejoin predictor needs a race or sprint: it works from the gap to the leader."
        )
        return
    trace = gap_trace(laps)
    if trace.empty:
        st.info("No lap completion times for this session, so a rejoin cannot be predicted.")
        return
    drivers = sorted(set(trace["Driver"]))
    last_lap = int(trace["LapNumber"].max())
    loss, source = pit_loss_for((session_info or {}).get("gp"), laps, track_status)
    left, middle, right = st.columns(3)
    driver = left.selectbox(
        "Driver",
        drivers,
        index=drivers.index(focus) if focus in drivers else 0,
        key=f"{key}_driver",
    )
    at_lap = middle.number_input(
        "After lap",
        min_value=1,
        max_value=last_lap,
        value=min(max(int(lap or last_lap), 1), last_lap),
        step=1,
        key=f"{key}_lap",
    )
    seconds = right.number_input(
        "Pit loss (s)",
        min_value=5.0,
        max_value=60.0,
        value=float(loss),
        step=0.5,
        key=f"{key}_loss:{loss}",
    )
    result = rejoin_after_lap(laps, driver, int(at_lap), float(seconds))
    if result is None:
        st.info(f"{driver} has no timed lap {int(at_lap)}, so there is no gap to start from.")
        return
    st.markdown(rejoin_sentence(driver, int(at_lap), result))
    st.caption(
        f"Pit loss {loss:.1f} s: {source}. It is the pit lane time from pit entry to pit exit; "
        "the other cars are assumed to stay out at their gaps to the leader at the end of that lap."
    )
