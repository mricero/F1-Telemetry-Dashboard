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
    render_feed_status,
    render_lap_times,
    render_live_controls,
    render_live_dashboard,
    render_position_changes,
    render_race_control,
    render_telemetry_charts,
    render_tire_strategy,
    render_weather,
)
from ui.replay_view import (
    FOCUS_PREFIX,
    cursor_key,
    render_session_replay,
    replay_model,
    sync_seek_cursor,
    wants_analysis,
)

# What app.main() stores for the pages to draw from.
CONTEXT_KEY = "page_context"

PAGE_REPLAY = "Replay"
PAGE_RESULTS = "Results"
PAGE_ANALYSIS = "Analysis"
PAGE_RECORDS = "Records"
PAGE_LIVE = "Live"
# Which page ran last, so the replay can tell it is being re-entered.
LAST_PAGE_KEY = "last_page"
# The lap "Analyse this lap" picked in the player, per session.
ANALYSIS_LAP_PREFIX = "analysis_lap"

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
    """The session as it unfolded, from lights out to the flag.

    "Final result" switches to the Results page: a snapshot at the flag has
    no official classification by design (REPLAY-03).
    """
    context = _context()
    pages = context.get("pages") or {}
    results = pages.get(PAGE_RESULTS)
    # Back on the replay: a lap picked earlier no longer overrides the cursor.
    st.session_state.pop(f"{ANALYSIS_LAP_PREFIX}:{context['session_key']}", None)
    if st.session_state.get(LAST_PAGE_KEY) != PAGE_REPLAY:
        # The player was unmounted while away; it remounts at the moment it
        # last reported, not at the last Python seek.
        sync_seek_cursor(context["session_key"])
    st.session_state[LAST_PAGE_KEY] = PAGE_REPLAY
    render_session_replay(
        context["session_data"],
        context["session_key"],
        on_final=(lambda: st.switch_page(results)) if results is not None else None,
    )
    # "Analyse this lap" in the player's driver card (REPLAY-10).
    picked = wants_analysis(context["session_key"])
    if picked is not None and pages.get(PAGE_ANALYSIS):
        # Keep the lap the user picked: the leader's lap at the last paused
        # cursor is a different lap (and there is none outside races).
        st.session_state[f"{ANALYSIS_LAP_PREFIX}:{context['session_key']}"] = int(picked)
        st.switch_page(pages[PAGE_ANALYSIS])


def results_page() -> None:
    """How the session ended: classification, sectors, dominance, strategy."""
    st.session_state[LAST_PAGE_KEY] = "other"
    context = _context()
    render_dashboard(context["session_data"])
    st.subheader("Tyre strategy")
    render_tire_strategy(
        context["stints"], context["color_map"], context["session_data"].get("compound_colors")
    )


def replay_moment(context: dict) -> dict:
    """Where the replay cursor stands, for the Analysis page (UI-07).

    ``lap`` is the leader's lap at the cursor (races), ``focus`` the driver
    focused in the player, ``ahead`` the car in front of them at that moment.
    Empty before the replay has been opened.
    """
    key = context["session_key"]
    cursor = st.session_state.get(cursor_key(key))
    if cursor is None or context["session_data"].get("is_live"):
        return {}
    series, _ = replay_model(context["session_data"], key)
    lap = series.leader_lap.at(cursor) if len(series.leader_lap) else None
    focus = st.session_state.get(f"{FOCUS_PREFIX}:{key}")
    ahead = None
    if focus:
        order = list(series.standings_at(cursor)["Driver"])
        if focus in order and order.index(focus) > 0:
            ahead = order[order.index(focus) - 1]
    return {"cursor": cursor, "lap": lap, "focus": focus, "ahead": ahead}


def analysis_page() -> None:
    """One analysis panel at a time: only the chosen one is computed."""
    st.session_state[LAST_PAGE_KEY] = "other"
    context = _context()
    session_data = context["session_data"]
    moment = replay_moment(context)
    picked = st.session_state.get(f"{ANALYSIS_LAP_PREFIX}:{context['session_key']}")
    if picked is not None:
        moment = {**moment, "lap": picked}
    replay = (context.get("pages") or {}).get(PAGE_REPLAY)
    if moment.get("lap") and replay is not None:
        st.page_link(replay, label=f"Back to replay at lap {moment['lap']}")
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
        pair = tuple(code for code in (moment.get("focus"), moment.get("ahead")) if code)
        render_driver_comparison(context["telemetry"](), context["color_map"], preselect=pair)
    elif section == "Lap times":
        render_lap_times(context["laps"], context["color_map"], marker_lap=moment.get("lap"))
    elif section == "Positions":
        render_position_changes(context["laps"], context["color_map"], marker_lap=moment.get("lap"))
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
    if live_client is None:
        st.info("No live client is available in this process.")
    elif not live_client.is_running():
        render_feed_status(live_client)
        if st.button("Connect to live timing", type="primary"):
            live_client.start_async()
            st.rerun()
        error = live_client.last_error()
        if error:
            st.error(f"The live client stopped: {error}")
    else:
        # Status chip, reconnects and the dashboard refresh every 3 s inside
        # the fragment; between sessions it shows the last session's state.
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
