"""Main Streamlit Application - F1 Telemetry Dashboard.

Thin orchestration layer: session selection, data loading (with the
two-tier cache), processing and record keeping. All rendering lives in
:mod:`ui.layout`.

Either entry point works::

    streamlit run app.py     # the normal way
    python app.py            # re-enters through Streamlit automatically
"""

import logging
import os
import sys
from pathlib import Path

from streamlit.runtime import exists as streamlit_runtime_exists

# Guards against a relaunch loop if Streamlit somehow starts without its
# runtime: the second pass through gives up instead of forking forever.
_RELAUNCH_FLAG = "F1_DASHBOARD_RELAUNCHED"

_BARE_MODE_HELP = """\
Could not start the Streamlit runtime. Run the dashboard directly with:

    streamlit run app.py

The plain interpreter leaves Streamlit in "bare mode", where widgets return
defaults, session state is unavailable and st.stop() does nothing - which
turns any load failure into a confusing crash further down.
"""


def launch_via_streamlit() -> None:
    """Hand this script to ``streamlit run`` and never return.

    Running ``python app.py`` - an IDE's Run button, for instance - would
    otherwise leave Streamlit in bare mode. Rather than failing with advice,
    re-enter through Streamlit's own CLI so the dashboard just starts.
    """
    if os.environ.get(_RELAUNCH_FLAG):
        print(_BARE_MODE_HELP, file=sys.stderr)
        raise SystemExit(2)
    os.environ[_RELAUNCH_FLAG] = "1"

    from streamlit.web import cli as streamlit_cli

    script = str(Path(__file__).resolve())
    print(f"Starting Streamlit: streamlit run {script}", file=sys.stderr)
    # Streamlit's CLI reads sys.argv; pass through any extra user flags.
    sys.argv = ["streamlit", "run", script, *sys.argv[1:]]
    raise SystemExit(streamlit_cli.main())


# Re-enter through Streamlit *before* the heavy imports below: applying the
# @st.cache_data decorators in ui.layout without a runtime emits confusing
# "No runtime found" warnings on the way out.
if __name__ == "__main__" and not streamlit_runtime_exists():
    launch_via_streamlit()  # does not return

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from config import config  # noqa: E402  (loads .env before adapters read it)

# The adapters degrade to empty frames when an upstream call fails and say so
# through logging; without this their warnings would never be emitted (REPO-11).
logging.basicConfig(
    level=getattr(logging, str(config.log_level).upper(), logging.WARNING),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
from data.runtime_cache import runtime_cache  # noqa: E402
from data.source_manager import DataSourceManager  # noqa: E402
from processing.metrics_store import MetricsStore  # noqa: E402
from processing.telemetry_processor import TelemetryProcessor, max_lap_number  # noqa: E402
from ui.dashboard import render_dashboard  # noqa: E402
from ui.layout import (  # noqa: E402
    render_driver_comparison,
    render_header,
    render_lap_times,
    render_live_controls,
    render_live_dashboard,
    render_position_changes,
    render_race_control,
    render_session_selector,
    render_telemetry_charts,
    render_tire_strategy,
    render_track_map,
    render_weather,
)
from ui.replay_view import render_session_replay, session_key  # noqa: E402


def load_session_data(data_manager, selection: dict) -> dict:
    """Fetch a session, using the runtime cache when possible.

    Never returns None: load failures are surfaced with ``st.error`` and halt
    the script, so callers can rely on getting a real session dict.
    """
    cache_key = runtime_cache.make_key("session", selection)
    session_data = runtime_cache.get(cache_key)
    if session_data is not None:
        return session_data

    with st.spinner("Loading session data..."):
        try:
            session_data = data_manager.get_session_data(**selection)
        except Exception as exc:
            st.error(f"Failed to load session: {exc}")
            with st.expander("Details"):
                st.exception(exc)
            st.stop()
            # st.stop() raises under `streamlit run`; the explicit raise keeps
            # any other execution context from continuing without data.
            raise

    if session_data is None:
        st.error("The data source returned no session data.")
        st.stop()
        raise RuntimeError("get_session_data() returned None")

    if not session_data.get("is_live"):
        runtime_cache.set(cache_key, session_data)
    return session_data


def ensure_driver_table(session_data: dict) -> pd.DataFrame:
    """Return a usable drivers table, synthesizing one for bare live feeds."""
    drivers_df = session_data.get("drivers")
    if drivers_df is not None and not drivers_df.empty:
        return drivers_df

    # Live feeds may not have sent DriverList yet: fall back to telemetry keys.
    names = sorted(session_data.get("telemetry", {}).keys())
    drivers_df = pd.DataFrame(
        {
            "driver_number": names,
            "name_acronym": names,
            "team_colour": ["#888888"] * len(names),
            "team_name": [""] * len(names),
            "full_name": names,
        }
    )
    session_data["drivers"] = drivers_df
    return drivers_df


def init_browser_session() -> None:
    """Per-browser-session setup, safe to call on every rerun.

    ``st.session_state`` is per browser tab, so nothing process-wide belongs
    here: the runtime cache used to be wiped from this spot, which meant a
    second viewer evicted the first viewer's loaded sessions.
    """
    if "data_manager" not in st.session_state:
        # Per-tab manager, but it shares the one process-wide live adapter.
        st.session_state.data_manager = DataSourceManager()
    if "processor" not in st.session_state:
        st.session_state.processor = TelemetryProcessor()
    if "metrics_store" not in st.session_state:
        st.session_state.metrics_store = MetricsStore()


def main():
    """Main Streamlit application."""
    render_header()

    init_browser_session()

    data_manager = st.session_state.data_manager
    processor = st.session_state.processor
    metrics_store = st.session_state.metrics_store

    # Session Selection
    with st.expander("📋 Session Selection", expanded=True):
        selection = render_session_selector(data_manager)

    # Load Data (runtime-cached: repeat selections are instant, and
    # everything evaporates when the app closes)
    session_data = load_session_data(data_manager, selection)

    # Build color map (live feeds may not have DriverList yet)
    drivers_df = ensure_driver_table(session_data)
    color_map = processor.build_driver_color_map(drivers_df)

    # Process telemetry (live snapshots already carry real distances derived
    # from Position.z; resampling them again would waste cycles, so only
    # align historical data)
    if session_data.get("is_live"):
        telemetry_processed = {
            d: processor.normalize_units(df.copy()) for d, df in session_data["telemetry"].items()
        }
    else:
        telemetry_aligned = processor.align_drivers_by_distance(session_data["telemetry"])
        telemetry_processed = {
            d: processor.normalize_units(df) for d, df in telemetry_aligned.items()
        }

    # Process laps & stints (latest known lap helps bound live tyre stints)
    laps_processed = processor.process_laps(session_data["laps"], session_data["drivers"])
    stints_processed = processor.process_stints(
        session_data["stints"], latest_lap=max_lap_number(laps_processed)
    )

    info = session_data["session_info"]

    # --- Live timing dashboard (layout.md): header bar, leaderboard matrix,
    # sector widgets and the vector track map on the 60/40 grid. For a live
    # session this dict is still empty; the auto-refreshing fragment renders
    # the dashboard from each poll instead (LIVE-10).
    if not session_data.get("is_live"):
        render_dashboard(session_data)

    # Metrics label + persistent record keeping (survives app restarts)
    # The selection only overrides the session's own identity where it says
    # something: a replay selects a file, leaving year/GP/session unset.
    chosen = {k: v for k, v in selection.items() if v is not None}
    metrics_label = MetricsStore.make_label({**info, **chosen})
    if not laps_processed.empty:
        metrics_store.update_laps(metrics_label, laps_processed)
    if telemetry_processed:
        metrics_store.update_telemetry(metrics_label, telemetry_processed)

    with st.expander("🏆 Session & All-Time Records", expanded=True):
        rec_lines = metrics_store.summary_lines(metrics_store.session_records(metrics_label))
        if rec_lines:
            st.markdown(f"**This session — {metrics_label}**")
            for line in rec_lines:
                st.markdown(f"- {line}")
        else:
            st.info("No records yet for this session.")
        at_lines = metrics_store.summary_lines(metrics_store.all_time())
        if at_lines:
            st.markdown("**🏅 All-time (across sessions viewed)**")
            for line in at_lines:
                st.markdown(f"- {line}")
        cache_stats = runtime_cache.stats()
        used_mb = cache_stats["bytes"] / (1024 * 1024)
        budget_mb = cache_stats["max_bytes"] / (1024 * 1024)
        st.caption(
            f"Runtime cache: {cache_stats['entries']} session(s) hot · "
            f"{used_mb:.0f} / {budget_mb:.0f} MB · "
            f"{cache_stats['hits']} hits / {cache_stats['misses']} misses · "
            f"app open for {cache_stats['age_seconds']}s "
            f"(cache clears automatically when the app closes; "
            f"records above are kept)"
        )

    # Live mode handling - auto-refreshing fragment polls the SignalR buffers
    if session_data.get("is_live"):
        live_client = session_data.get("live_client")
        if live_client and not live_client.is_running():
            if st.button("🔴 Start Live Stream"):
                live_client.start_async()
                st.rerun()
        elif live_client and live_client.is_running():
            err = live_client.last_error()
            if err:
                st.error(f"Live client error: {err}")
            render_live_dashboard(data_manager, processor)
        # Buffer counts, raw-stream recording and Stop Live. These sat after
        # an unconditional return, so they never rendered (LIVE-12).
        render_live_controls(live_client)
        return

    # --- Deep-dive analysis. The dashboard above answers "what happened";
    # these tabs are for digging into a single channel or driver.
    st.markdown("---")
    analysis = st.tabs(
        [
            "📊 Telemetry",
            "⚔️ Head-to-Head",
            "⏱️ Lap Times",
            "📈 Positions",
            "🛞 Tyres",
            "🗺️ Track",
            "🎬 Replay",
            "🌤️ Weather",
            "🚩 Race Control",
        ]
    )
    with analysis[0]:
        scope_note = {
            "fastest": "Each driver's fastest lap — distance runs 0 → lap length, "
            "so drivers line up at the same track position.",
            "session": "Every lap of the session — distance accumulates across the "
            "full run, so drivers are not aligned by track position.",
        }.get(info.get("telemetry_scope"))
        if scope_note:
            st.caption(scope_note)
        render_telemetry_charts(telemetry_processed, color_map)
    with analysis[1]:
        render_driver_comparison(telemetry_processed, color_map)
    with analysis[2]:
        render_lap_times(laps_processed, color_map)
    with analysis[3]:
        render_position_changes(laps_processed, color_map)
    with analysis[4]:
        render_tire_strategy(stints_processed, color_map, session_data.get("compound_colors"))
    with analysis[5]:
        render_track_map(session_data["location"], color_map)
    with analysis[6]:
        st.caption(
            "Play the session back from the start: every car where it actually "
            "was, the running order at that moment, and the lap they were on."
        )
        render_session_replay(session_data, session_key(session_data, selection))
    with analysis[7]:
        render_weather(session_data.get("weather"))
    with analysis[8]:
        render_race_control(session_data.get("race_control"))

    # Replay Save Option
    if st.button("💾 Save Session for Replay"):
        name = f"{info.get('gp', 'race')}_{info.get('session_type', 'R')}"
        path = data_manager.save_replay(session_data, name)
        st.success(f"Saved to {path}")


if __name__ == "__main__":
    # The bare-interpreter case already relaunched above; reaching here means
    # Streamlit's runtime is up and this is the real script run.
    main()
