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
from ui.layout import (  # noqa: E402
    render_header,
    render_session_selector,
    render_sidebar_footer,
    selection_label,
)
from ui.pages import CONTEXT_KEY, enter_page, pages_for  # noqa: E402
from ui.replay_view import SECTOR_MEMO_PREFIX, session_key  # noqa: E402
from ui.theme import NEUTRAL_GREY  # noqa: E402


def load_session_data(data_manager, selection: dict) -> dict:
    """Fetch a session, using the runtime cache when possible.

    Never returns None: load failures are surfaced with ``st.error`` and halt
    the script, so callers can rely on getting a real session dict.
    """
    cache_key = runtime_cache.make_key("session", selection)
    session_data = runtime_cache.get(cache_key)
    if session_data is not None:
        return session_data

    name = selection_label(selection)
    with st.status(f"Loading {name}", expanded=False) as status:

        def progress(step: str) -> None:
            status.update(label=f"Loading {name}: {step}")

        try:
            session_data = data_manager.get_session_data(**selection, progress=progress)
        except Exception as exc:
            status.update(label=f"Could not load {name}", state="error")
            st.error(f"Could not load {name}: {exc}. Try again, or pick another session.")
            with st.expander("Details"):
                st.exception(exc)
            st.stop()
            # st.stop() raises under `streamlit run`; the explicit raise keeps
            # any other execution context from continuing without data.
            raise

        status.update(label=f"Loaded {name}", state="complete")

    if session_data is None:
        st.error("The data source returned no session data.")
        st.stop()
        raise RuntimeError("get_session_data() returned None")

    if cacheable(session_data):
        runtime_cache.set(cache_key, session_data)
    return session_data


def cacheable(session_data: dict) -> bool:
    """Whether a loaded session may stay in the runtime cache.

    Live sessions change by the second, and a session that ended in the last
    few hours may still be partial in F1's archive (HIST-09): both are loaded
    afresh next time instead of being served from the cache for the life of
    the process.
    """
    return not session_data.get("is_live") and not session_data.get("provisional")


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
            "team_colour": [NEUTRAL_GREY] * len(names),
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


PROCESSED_PREFIX = "processed"
# Per-session entries a browser session keeps for the current session only
# (UI-18): each holds the session dict or markup derived from it.
PER_SESSION_PREFIXES = (PROCESSED_PREFIX, SECTOR_MEMO_PREFIX)


def evict_other_sessions(key: str) -> None:
    """Drop every per-session entry that belongs to a session other than ``key``.

    Loading four sessions in one tab used to leave four ``processed:*``
    entries, each pinning a whole session dict, which defeated the runtime
    cache's byte budget.
    """
    for name in [k for k in st.session_state if isinstance(k, str)]:
        for prefix in PER_SESSION_PREFIXES:
            if name.startswith(f"{prefix}:") and name != f"{prefix}:{key}":
                del st.session_state[name]


def processed_views(session_data: dict, key: str, processor) -> dict:
    """Laps, stints and colours for the pages, built once per session.

    Streamlit reruns the whole script on every click; redoing this on each
    replay step would make the step controls sluggish. Telemetry alignment is
    the expensive part and only the Analysis page needs it, so it is built
    on first use.
    """
    cache_key = f"{PROCESSED_PREFIX}:{key}"
    cached = st.session_state.get(cache_key)
    if cached is not None and cached["session_data"] is session_data:
        return cached
    evict_other_sessions(key)

    drivers_df = ensure_driver_table(session_data)
    laps = processor.process_laps(session_data["laps"], drivers_df)
    views: dict = {
        "session_data": session_data,
        "color_map": processor.build_driver_color_map(drivers_df),
        "laps": laps,
        "stints": processor.process_stints(session_data["stints"], latest_lap=max_lap_number(laps)),
    }

    def telemetry() -> dict:
        if "telemetry_frames" not in views:
            if session_data.get("is_live"):
                views["telemetry_frames"] = {
                    d: processor.normalize_units(df.copy())
                    for d, df in session_data["telemetry"].items()
                }
            else:
                aligned = processor.align_drivers_by_distance(session_data["telemetry"])
                views["telemetry_frames"] = {
                    d: processor.normalize_units(df) for d, df in aligned.items()
                }
        return views["telemetry_frames"]

    views["telemetry"] = telemetry
    st.session_state[cache_key] = views
    return views


def record_metrics(
    metrics_store, label: str, views: dict, key: str, circuit: str | None = None
) -> None:
    """Fold the session into the persistent records, once per session.

    Top speed comes from the laps' speed traps when the source has them, so
    the telemetry alignment - the slow part, and only Analysis needs it -
    is not built just to read one number (HIST-08, CACHE-02).
    """
    done_key = f"recorded:{key}"
    if st.session_state.get(done_key):
        return
    if not views["laps"].empty:
        metrics_store.update_laps(label, views["laps"], circuit=circuit)
    if not metrics_store.has_top_speed(label):
        telemetry = views["telemetry"]()
        if telemetry:
            metrics_store.update_telemetry(label, telemetry, circuit=circuit)
    st.session_state[done_key] = True


def main():
    """Main Streamlit application: select, load once, then navigate."""
    # One line per script run: the replay player must not cause any while
    # it plays (REPLAY-05 checks this with LOG_LEVEL=DEBUG).
    logging.getLogger("app").debug("script run")
    render_header()

    init_browser_session()

    data_manager = st.session_state.data_manager
    processor = st.session_state.processor
    metrics_store = st.session_state.metrics_store

    # Session selection lives in the sidebar; nothing loads until the user
    # presses Load session (or opens a shared link).
    selection = render_session_selector(data_manager)
    if selection is None:
        render_sidebar_footer()
        page = st.navigation(pages_for(None), position="top")
        enter_page(page.title)
        page.run()
        return

    # Load Data (runtime-cached: repeat selections are instant, and
    # everything evaporates when the app closes)
    session_data = load_session_data(data_manager, selection)
    key = session_key(session_data, selection)
    views = processed_views(session_data, key, processor)
    info = session_data["session_info"]

    # The selection only overrides the session's own identity where it says
    # something: a replay selects a file, leaving year/GP/session unset.
    chosen = {k: v for k, v in selection.items() if v is not None}
    metrics_label = MetricsStore.make_label({**info, **chosen})
    if not session_data.get("is_live"):
        record_metrics(metrics_store, metrics_label, views, key, circuit=info.get("gp"))

    pages = pages_for(session_data)
    st.session_state[CONTEXT_KEY] = {
        **views,
        "pages": {page.title: page for page in pages},
        "session_key": key,
        "metrics_store": metrics_store,
        "metrics_label": metrics_label,
        "metrics_circuit": info.get("gp"),
        "data_manager": data_manager,
        "processor": processor,
    }

    if not session_data.get("is_live"):
        with st.sidebar:
            if st.button("Save session for replay"):
                name = f"{info.get('gp', 'race')}_{info.get('session_type', 'R')}"
                path = data_manager.save_replay(session_data, name)
                st.success(f"Saved to {path}")

    render_sidebar_footer()
    page = st.navigation(pages, position="top")
    enter_page(page.title)
    page.run()


if __name__ == "__main__":
    # The bare-interpreter case already relaunched above; reaching here means
    # Streamlit's runtime is up and this is the real script run.
    main()
