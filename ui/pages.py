"""The app's pages (IMPROVEMENTS.md UI-03, guideline 5.9).

One long scroll of nine eager tabs becomes a handful of pages, each with one
job: **Replay** (the session unfolding), **Results** (the end-of-session
tower and tyre strategy), **Analysis** (one panel at a time), **Records**
(the metrics store) and, while live, **Live**. The session is loaded once,
before navigation, and each page reads it from ``st.session_state``.
"""

import streamlit as st

from data.runtime_cache import runtime_cache
from ui.dashboard import render_dashboard
from ui.layout import (
    render_driver_comparison,
    render_lap_times,
    render_live_controls,
    render_live_dashboard,
    render_position_changes,
    render_race_control,
    render_telemetry_charts,
    render_tire_strategy,
    render_weather,
)
from ui.replay_view import render_session_replay

# What app.main() stores for the pages to draw from.
CONTEXT_KEY = "page_context"

PAGE_REPLAY = "Replay"
PAGE_RESULTS = "Results"
PAGE_ANALYSIS = "Analysis"
PAGE_RECORDS = "Records"
PAGE_LIVE = "Live"

ANALYSIS_SECTIONS = (
    "Telemetry",
    "Head-to-head",
    "Lap times",
    "Positions",
    "Weather",
    "Race control",
)

SCOPE_NOTES = {
    "fastest": "Each driver's fastest lap: distance runs from 0 to the lap length, "
    "so drivers line up at the same track position.",
    "session": "Every lap of the session: distance accumulates across the full run, "
    "so drivers are not aligned by track position.",
}


def _context() -> dict:
    return st.session_state[CONTEXT_KEY]


def replay_page() -> None:
    """The session as it unfolded, from lights out to the flag."""
    context = _context()
    render_session_replay(context["session_data"], context["session_key"])


def results_page() -> None:
    """How the session ended: classification, sectors, dominance, strategy."""
    context = _context()
    render_dashboard(context["session_data"])
    st.subheader("Tyre strategy")
    render_tire_strategy(
        context["stints"], context["color_map"], context["session_data"].get("compound_colors")
    )


def analysis_page() -> None:
    """One analysis panel at a time: only the chosen one is computed."""
    context = _context()
    session_data = context["session_data"]
    section = (
        st.segmented_control(
            "Section",
            ANALYSIS_SECTIONS,
            default=ANALYSIS_SECTIONS[0],
            key="analysis_section",
            label_visibility="collapsed",
        )
        or ANALYSIS_SECTIONS[0]
    )
    if section == "Telemetry":
        scope = (session_data.get("session_info") or {}).get("telemetry_scope")
        note = SCOPE_NOTES.get(str(scope))
        if note:
            st.caption(note)
        render_telemetry_charts(context["telemetry"](), context["color_map"])
    elif section == "Head-to-head":
        render_driver_comparison(context["telemetry"](), context["color_map"])
    elif section == "Lap times":
        render_lap_times(context["laps"], context["color_map"])
    elif section == "Positions":
        render_position_changes(context["laps"], context["color_map"])
    elif section == "Weather":
        render_weather(session_data.get("weather"))
    elif section == "Race control":
        render_race_control(session_data.get("race_control"))


def records_page() -> None:
    """Fastest lap, sectors and top speed: this session and every session viewed."""
    context = _context()
    store, label = context["metrics_store"], context["metrics_label"]
    lines = store.summary_lines(store.session_records(label))
    st.markdown(f"**This session: {label}**")
    if lines:
        st.markdown("\n".join(f"- {line}" for line in lines))
    else:
        st.info("No records yet for this session.")
    all_time = store.summary_lines(store.all_time())
    if all_time:
        st.markdown("**All sessions viewed here**")
        st.markdown("\n".join(f"- {line}" for line in all_time))

    with st.expander("Diagnostics", expanded=False):
        stats = runtime_cache.stats()
        used_mb = stats["bytes"] / (1024 * 1024)
        budget_mb = stats["max_bytes"] / (1024 * 1024)
        st.caption(
            f"Runtime cache: {stats['entries']} session(s) held, "
            f"{used_mb:.0f} of {budget_mb:.0f} MB, "
            f"{stats['hits']} hits and {stats['misses']} misses, "
            f"app open for {stats['age_seconds']} s. The cache clears when the app "
            "closes; the records above are kept."
        )


def live_page() -> None:
    """The live timing screen, polled from the SignalR buffers."""
    context = _context()
    live_client = context["session_data"].get("live_client")
    if live_client and not live_client.is_running():
        if st.button("Start live stream", type="primary"):
            live_client.start_async()
            st.rerun()
    elif live_client and live_client.is_running():
        error = live_client.last_error()
        if error:
            st.error(f"Live client error: {error}")
        render_live_dashboard(context["data_manager"], context["processor"])
    # Buffer counts, raw-stream recording and Stop Live (LIVE-12).
    render_live_controls(live_client)


def page_specs(session_data: dict) -> list[tuple]:
    """``(page function, title, url path)`` for a session; the first opens."""
    records = (records_page, PAGE_RECORDS, "records")
    if session_data.get("is_live"):
        return [(live_page, PAGE_LIVE, "live"), records]
    return [
        (replay_page, PAGE_REPLAY, "replay"),
        (results_page, PAGE_RESULTS, "results"),
        (analysis_page, PAGE_ANALYSIS, "analysis"),
        records,
    ]


def pages_for(session_data: dict) -> list:
    """The session's pages for ``st.navigation``."""
    return [
        st.Page(page, title=title, url_path=path, default=index == 0)
        for index, (page, title, path) in enumerate(page_specs(session_data))
    ]
