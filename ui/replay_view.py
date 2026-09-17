"""Watch a session unfold (IMPROVEMENTS.md FEAT-04).

The other panels answer "what happened overall"; this one answers "what was
happening at this moment" - cars on the map where they actually were, the
running order as it stood, and the lap they were on - from lights out to the
flag, either scrubbed by hand or played back.

Streamlit reruns the script on every widget change, so playback is a fragment
that advances a cursor kept in session state; the map itself is the same SVG
builder the dashboard uses.
"""

import pandas as pd
import streamlit as st

from processing.replay import (
    DEFAULT_STEP_SECONDS,
    format_clock,
    lap_at,
    order_at,
    positions_at,
    timeline_bounds,
)
from ui.theme import DASHBOARD_CSS, team_color
from ui.track_map import build_track_svg

# How much session time one second of playback covers, per speed setting.
SPEED_OPTIONS = {"1x": 1.0, "5x": 5.0, "20x": 20.0, "60x": 60.0}
# How often the playback fragment redraws. Faster than this and Streamlit
# spends longer rerunning the script than the frame is on screen for.
FRAME_INTERVAL_SECONDS = 0.5

CURSOR_KEY = "replay_cursor"
PLAYING_KEY = "replay_playing"
SPEED_KEY = "replay_speed"


def advance(cursor: float, speed_label: str, end: float, interval: float) -> float:
    """Where the cursor lands after one frame."""
    step = SPEED_OPTIONS.get(speed_label, 1.0) * interval
    return min(cursor + step, end)


def driver_meta(drivers: pd.DataFrame) -> dict:
    """Acronym -> team name/colour, for the map markers and the order list."""
    if drivers is None or drivers.empty or "name_acronym" not in drivers.columns:
        return {}
    meta = {}
    for row in drivers.itertuples():
        code = str(getattr(row, "name_acronym", "") or "")
        if code:
            meta[code] = {
                "team_name": getattr(row, "team_name", "") or "",
                "team_colour": getattr(row, "team_colour", "") or "",
            }
    return meta


def order_html(order: list, meta: dict) -> str:
    """Compact running order for the moment being shown."""
    if not order:
        return '<div class="f1-dim" style="padding:8px">No completed laps yet.</div>'

    rows = []
    for entry in order[:22]:
        info = meta.get(entry["code"], {})
        accent = team_color(info.get("team_name"), info.get("team_colour"))
        lap = entry.get("lap")
        rows.append(
            f'<div style="display:flex;align-items:center;gap:8px;padding:2px 0">'
            f'<span style="width:22px;text-align:right;color:#8a8a8a">{entry["position"]}</span>'
            f'<span style="width:4px;height:14px;background:{accent};display:inline-block"></span>'
            f'<span style="font-weight:700">{entry["code"]}</span>'
            f'<span style="margin-left:auto;color:#8a8a8a">L{lap if lap else "-"}</span>'
            f"</div>"
        )
    return f'<div class="f1-dash" style="padding:10px">{"".join(rows)}</div>'


def _frame(session_data: dict, moment: float) -> None:
    """Draw one moment: the map, the order, and the clock."""
    timeline = session_data.get("positions")
    laps = session_data.get("laps")
    meta = driver_meta(session_data.get("drivers"))

    markers = positions_at(timeline, moment)
    for marker in markers:
        info = meta.get(marker["code"], {})
        marker["team_colour"] = team_color(info.get("team_name"), info.get("team_colour"))

    left, right = st.columns([6, 4], gap="small")
    with left:
        svg = build_track_svg(
            session_data.get("location") or {},
            circuit_info=session_data.get("circuit_info"),
            driver_meta=meta,
            markers=markers,
        )
        if svg is None:
            st.info("No GPS data for this session, so the track cannot be drawn.")
        else:
            st.html(f'<div class="f1-dash">{svg}</div>')

    with right:
        st.metric("Session clock", format_clock(moment))
        st.metric("Lap", lap_at(laps, moment))
        st.caption(f"{len(markers)} car(s) on track")
        st.html(order_html(order_at(laps, moment), meta))


def render_session_replay(session_data: dict) -> None:
    """Scrub or play back a whole session."""
    st.html(DASHBOARD_CSS)

    timeline = session_data.get("positions")
    if timeline is None or getattr(timeline, "empty", True):
        st.info(
            "This session has no position timeline to replay. Load a session from "
            "FastF1 (or save it as a replay) and it will be built automatically."
        )
        return

    start, end = timeline_bounds(timeline)
    if end <= start:
        st.info("The position timeline covers no time.")
        return

    st.session_state.setdefault(CURSOR_KEY, start)
    st.session_state.setdefault(PLAYING_KEY, False)
    st.session_state.setdefault(SPEED_KEY, "5x")

    controls = st.columns([1, 1, 2, 6])
    with controls[0]:
        if st.button("⏸️ Pause" if st.session_state[PLAYING_KEY] else "▶️ Play"):
            st.session_state[PLAYING_KEY] = not st.session_state[PLAYING_KEY]
            st.rerun()
    with controls[1]:
        if st.button("⏮️ Start"):
            st.session_state[CURSOR_KEY] = start
            st.session_state[PLAYING_KEY] = False
            st.rerun()
    with controls[2]:
        st.session_state[SPEED_KEY] = st.selectbox(
            "Speed",
            list(SPEED_OPTIONS),
            index=list(SPEED_OPTIONS).index(st.session_state[SPEED_KEY]),
            label_visibility="collapsed",
        )
    with controls[3]:
        # The slider is the source of truth while paused; playback writes to
        # the same cursor, so scrubbing and playing cannot disagree.
        chosen = st.slider(
            "Session time",
            min_value=float(start),
            max_value=float(end),
            value=float(st.session_state[CURSOR_KEY]),
            step=float(DEFAULT_STEP_SECONDS),
            format="%.1f s",
            label_visibility="collapsed",
            disabled=st.session_state[PLAYING_KEY],
        )
        if not st.session_state[PLAYING_KEY]:
            st.session_state[CURSOR_KEY] = chosen

    if st.session_state[PLAYING_KEY]:
        _play(session_data, end)
    else:
        _frame(session_data, st.session_state[CURSOR_KEY])


@st.fragment(run_every=FRAME_INTERVAL_SECONDS)
def _play(session_data: dict, end: float) -> None:
    """Advance the cursor and redraw, without rerunning the whole script."""
    if not st.session_state.get(PLAYING_KEY):
        return

    moment = advance(
        st.session_state[CURSOR_KEY],
        st.session_state[SPEED_KEY],
        end,
        FRAME_INTERVAL_SECONDS,
    )
    st.session_state[CURSOR_KEY] = moment

    _frame(session_data, moment)

    if moment >= end:
        st.session_state[PLAYING_KEY] = False
        st.caption("Chequered flag - replay finished.")
