"""The Replay page: the session as it stood at the cursor (REPLAY-04).

The whole timing dashboard - header, tower, sector cards, map - is drawn
from :func:`processing.replay_model.snapshot_at` at the cursor, so it shows
the race as it was at that moment: grid order at lights out, the safety car
while it was out, the final order only at the flag. The end-of-session view
is the Results page, one click away.

The cursor lives in ``st.session_state["replay_cursor:<session key>"]``
(float session seconds), so switching session starts the new one at lights
out. Step controls and the scrubber move it; playback here is the server
fallback (one frame a second) until the browser player takes over.
"""

import os

import streamlit as st

from processing.replay import ReplayClock, format_clock
from processing.replay_model import (
    TowerSeries,
    events,
    lap_table,
    session_clock,
    snapshot_at,
    tower_series,
)
from processing.replay_payload import build_replay_payload
from processing.timing import build_timing_rows, sector_leaders
from ui.components.replay_player import player_style, render_replay_player
from ui.dashboard import render_dashboard, sector_cards_html
from ui.theme import DASHBOARD_CSS

# How much session time one second of playback covers, per speed setting.
SPEED_OPTIONS = {"1x": 1.0, "5x": 5.0, "20x": 20.0, "60x": 60.0}
# The server fallback redraws the whole dashboard, so once a second is the
# most it can do without Streamlit spending longer rerunning than drawing.
FRAME_INTERVAL_SECONDS = 1.0
# "Next lap" lands just after the leader crosses the line, so the new lap's
# order is already on the tower.
LAP_LANDING_SECONDS = 1.0

CURSOR_PREFIX = "replay_cursor"
PLAYING_PREFIX = "replay_playing"
SPEED_PREFIX = "replay_speed"
SLIDER_PREFIX = "replay_slider"
JUMP_PREFIX = "replay_jump"
MODEL_PREFIX = "replay_model"
PAYLOAD_PREFIX = "replay_payload"
SEEK_PREFIX = "replay_seek"
FOCUS_PREFIX = "replay_focus"
PLAYER_PREFIX = "replay_player"

# F1_REPLAY_PLAYER=server keeps the server-rendered view (REPLAY-04) instead
# of the browser player, e.g. where custom components are not wanted.
PLAYER_ENV = "F1_REPLAY_PLAYER"


def player_mode() -> str:
    """``browser`` (the default) or ``server``."""
    return "server" if os.environ.get(PLAYER_ENV, "").strip().lower() == "server" else "browser"


def session_key(session_data: dict, selection: dict | None = None) -> str:
    """``source:year:gp:session_type``, or ``replay:<file>`` for a saved replay."""
    chosen = selection or {}
    if chosen.get("source") == "replay" and chosen.get("replay_file"):
        return f"replay:{chosen['replay_file']}"
    info = session_data.get("session_info") or {}
    source = chosen.get("source") or session_data.get("source") or "fastf1"
    year = chosen.get("year") or info.get("year")
    gp = chosen.get("gp") or info.get("gp")
    session_type = chosen.get("session_type") or info.get("session_type")
    return f"{source}:{year}:{gp}:{session_type}"


def cursor_key(key: str) -> str:
    """Where the authoritative replay cursor for one session lives."""
    return f"{CURSOR_PREFIX}:{key}"


def clock_for(session_data: dict) -> ReplayClock:
    """The session's replay clock: stored at load time, or derived here."""
    return session_clock(session_data)


def advance(cursor: float, speed_label: str, end: float, interval: float) -> float:
    """Where the cursor lands after one frame."""
    step = SPEED_OPTIONS.get(speed_label, 1.0) * interval
    return min(cursor + step, end)


def lap_marks(session_data: dict, series: TowerSeries) -> list[float]:
    """Moments a lap was completed: by the leader in a race, by anyone otherwise."""
    if series.kind == "race" and len(series.leader_lap):
        return [float(t) for t in series.leader_lap.t.tolist() if t > 0]
    table = lap_table(session_data.get("laps"))
    return sorted({round(float(t), 3) for t in table["end"].dropna().tolist()})


def next_lap_moment(
    marks: "list[float] | tuple[float, ...]", cursor: float, clock: ReplayClock
) -> float:
    """Just after the next lap completion after ``cursor``."""
    for mark in marks:
        if mark + LAP_LANDING_SECONDS > cursor + 1e-6:
            return clock.clamp(mark + LAP_LANDING_SECONDS)
    return clock.end


def previous_lap_moment(
    marks: "list[float] | tuple[float, ...]", cursor: float, clock: ReplayClock
) -> float:
    """Just after the lap completion before the one the cursor has passed."""
    earlier = [mark for mark in marks if mark + LAP_LANDING_SECONDS < cursor - 1e-6]
    if earlier:
        return clock.clamp(earlier[-1] + LAP_LANDING_SECONDS)
    return clock.lights_out


def replay_model(session_data: dict, key: str) -> tuple[TowerSeries, list]:
    """The session's change-point series and events, built once per session."""
    state_key = f"{MODEL_PREFIX}:{key}"
    cached = st.session_state.get(state_key)
    if cached is not None and cached[0] is session_data:
        return cached[1], cached[2]
    series = tower_series(session_data)
    found = events(session_data, series)
    _evict_other_sessions(MODEL_PREFIX, key)
    st.session_state[state_key] = (session_data, series, found)
    return series, found


def _evict_other_sessions(prefix: str, key: str) -> None:
    """Drop another session's cached model/payload from this browser session.

    Each entry holds the whole session dict plus derived tables; keeping one
    per session ever opened in a tab grew memory without bound and defeated
    the runtime cache's byte budget.
    """
    for name in [k for k in st.session_state if isinstance(k, str)]:
        if name.startswith(f"{prefix}:") and name != f"{prefix}:{key}":
            del st.session_state[name]


SEEK_CURSOR_PREFIX = "replay_seek_cursor"
# The focused driver as of the last seek: the only focus sent to the player
# (UI-11). Sending the live focus changed the component's data on every
# driver click, so Streamlit re-sent the whole multi-MB payload each time.
SEEK_FOCUS_PREFIX = "replay_seek_focus"


def _sync_seek_focus(key: str) -> None:
    st.session_state[f"{SEEK_FOCUS_PREFIX}:{key}"] = st.session_state.get(f"{FOCUS_PREFIX}:{key}")


def sync_seek_cursor(key: str) -> None:
    """Send the latest reported cursor and focus to a freshly mounted player."""
    cursor = st.session_state.get(cursor_key(key))
    if cursor is not None:
        st.session_state[f"{SEEK_CURSOR_PREFIX}:{key}"] = cursor
    _sync_seek_focus(key)


def _move(key: str, moment: float, clock: ReplayClock) -> None:
    """Move the cursor from Python; the browser player follows the seek token.

    The moment is also kept as the *seek* cursor, the only cursor sent to the
    player: sending the live cursor changed the component's data on every
    report, so Streamlit re-sent the whole multi-MB payload each time.
    """
    st.session_state[cursor_key(key)] = clock.clamp(moment)
    st.session_state[f"{SEEK_CURSOR_PREFIX}:{key}"] = clock.clamp(moment)
    _sync_seek_focus(key)
    seek = f"{SEEK_PREFIX}:{key}"
    st.session_state[seek] = st.session_state.get(seek, 0) + 1


def replay_payload(session_data: dict, key: str, series: TowerSeries, clock: ReplayClock) -> dict:
    """The player's payload, built once per session and styled from the theme."""
    state_key = f"{PAYLOAD_PREFIX}:{key}"
    cached = st.session_state.get(state_key)
    if cached is not None and cached[0] is session_data:
        return cached[1]
    payload = build_replay_payload(session_data, series, clock, key)
    payload["style"] = player_style(payload, session_data.get("compound_colors"))
    # Lap completions the lap buttons step through: the leader's in a race,
    # anyone's otherwise (qualifying and practice have no leader laps).
    payload["lap_marks"] = lap_marks(session_data, series)
    _evict_other_sessions(PAYLOAD_PREFIX, key)
    st.session_state[state_key] = (session_data, payload)
    return payload


def _player_state(key: str, name: str):
    state = st.session_state.get(f"{PLAYER_PREFIX}:{key}")
    if state is None:
        return None
    return state.get(name) if hasattr(state, "get") else getattr(state, name, None)


def _from_player(key: str, clock: ReplayClock) -> None:
    """The player paused or seeked: its cursor becomes the authoritative one."""
    value = _player_state(key, "cursor")
    if value is not None:
        st.session_state[cursor_key(key)] = clock.clamp(float(value))


ANALYSE_PREFIX = "replay_analyse"


def _analyse_from_player(key: str) -> None:
    """ "Analyse this lap" in the player: open Analysis on the lap chart there."""
    lap = _player_state(key, "analyse")
    if lap is None:
        return
    st.session_state["analysis_section"] = "Lap times"
    st.session_state[f"{ANALYSE_PREFIX}:{key}"] = int(lap)


def wants_analysis(key: str) -> int | None:
    """The lap "Analyse this lap" asked for, once (then forgotten)."""
    return st.session_state.pop(f"{ANALYSE_PREFIX}:{key}", None)


def _focus_from_player(key: str) -> None:
    st.session_state[f"{FOCUS_PREFIX}:{key}"] = _player_state(key, "focus")


def _shift(key: str, delta: float, clock: ReplayClock) -> None:
    _move(key, st.session_state[cursor_key(key)] + delta, clock)


def _to_lap(key: str, marks: list[float], clock: ReplayClock, forward: bool) -> None:
    cursor = st.session_state[cursor_key(key)]
    step = next_lap_moment if forward else previous_lap_moment
    _move(key, step(marks, cursor, clock), clock)


def _jump(key: str, targets: dict, clock: ReplayClock) -> None:
    chosen = st.session_state.get(f"{JUMP_PREFIX}:{key}")
    if chosen in targets:
        _move(key, targets[chosen], clock)
    st.session_state[f"{JUMP_PREFIX}:{key}"] = None


def _scrub(key: str, clock: ReplayClock) -> None:
    race_seconds = st.session_state[f"{SLIDER_PREFIX}:{key}"]
    _move(key, clock.lights_out + float(race_seconds), clock)


def _toggle_play(key: str) -> None:
    playing = f"{PLAYING_PREFIX}:{key}"
    st.session_state[playing] = not st.session_state.get(playing, False)


def _event_label(moment: float, clock: ReplayClock, label: str) -> str:
    return f"{format_clock(moment - clock.lights_out)}  {label}"


def render_session_replay(session_data: dict, key: str | None = None, on_final=None) -> None:
    """The Replay page: step and scrub through the session.

    ``on_final`` is called when "Final result" is pressed (the Results page
    switch); without it the button is not offered.
    """
    laps = session_data.get("laps")
    if laps is None or getattr(laps, "empty", True):
        st.info("This session has no lap data to replay.")
        return

    clock = clock_for(session_data)
    if clock.end <= clock.start:
        st.info("This session covers no time to replay.")
        return

    key = key or session_key(session_data)
    series, found = replay_model(session_data, key)
    marks = lap_marks(session_data, series)
    cursor, playing, speed = cursor_key(key), f"{PLAYING_PREFIX}:{key}", f"{SPEED_PREFIX}:{key}"
    st.session_state.setdefault(cursor, clock.lights_out)
    st.session_state.setdefault(playing, False)
    st.session_state.setdefault(speed, "5x")
    st.session_state[cursor] = clock.clamp(st.session_state[cursor])
    is_playing = st.session_state[playing]

    if player_mode() == "browser":
        _browser_view(session_data, key, series, found, clock, on_final)
        return

    # Row 1: the step controls. Row 2: the scrubber with the clock, the
    # jump list, speed and the switch to the final result.
    buttons = st.columns(8, gap="small")
    buttons[0].button(
        "Pause" if is_playing else "Play",
        on_click=_toggle_play,
        args=(key,),
        width="stretch",
    )
    buttons[1].button(
        "Lights out", on_click=_move, args=(key, clock.lights_out, clock), width="stretch"
    )
    for column, delta in zip(buttons[2:6], (-30, -5, 5, 30), strict=True):
        column.button(f"{delta:+d}s", on_click=_shift, args=(key, delta, clock), width="stretch")
    buttons[6].button(
        "Previous lap", on_click=_to_lap, args=(key, marks, clock, False), width="stretch"
    )
    buttons[7].button("Next lap", on_click=_to_lap, args=(key, marks, clock, True), width="stretch")

    moment = st.session_state[cursor]
    slider_key = f"{SLIDER_PREFIX}:{key}"
    # The scrubber follows the cursor, whichever control moved it last.
    st.session_state[slider_key] = moment - clock.lights_out
    scrub, readout, jump, pace, final = st.columns([5, 1.6, 2.2, 0.9, 1.3], gap="small")
    scrub.slider(
        "Session time",
        min_value=float(clock.start - clock.lights_out),
        max_value=float(clock.end - clock.lights_out),
        step=float(clock.step),
        format="%.0f s",
        key=slider_key,
        on_change=_scrub,
        args=(key, clock),
        label_visibility="collapsed",
        disabled=is_playing,
    )
    targets = {_event_label(moment, clock, label): moment for moment, _, label in found}
    jump.selectbox(
        "Jump to",
        list(targets),
        index=None,
        placeholder="Jump to",
        key=f"{JUMP_PREFIX}:{key}",
        on_change=_jump,
        args=(key, targets, clock),
        label_visibility="collapsed",
    )
    pace.selectbox("Speed", list(SPEED_OPTIONS), key=speed, label_visibility="collapsed")
    if on_final is not None and final.button("Final result", width="stretch"):
        on_final()

    if is_playing:
        _play(session_data, series, clock, key)
    else:
        _readout(readout, series, moment, clock)
        render_dashboard(snapshot_at(session_data, moment, series))


def _readout(container, series: TowerSeries, moment: float, clock: ReplayClock) -> None:
    """``0:41:07 · Lap 23/57`` beside the scrubber."""
    text = format_clock(moment - clock.lights_out)
    lap = series.leader_lap.at(moment) if len(series.leader_lap) else None
    if lap and series.total_laps:
        text += f" \N{MIDDLE DOT} Lap {lap}/{series.total_laps}"
    container.markdown(f"**{text}**")


@st.fragment(run_every=FRAME_INTERVAL_SECONDS)
def _play(session_data: dict, series: TowerSeries, clock: ReplayClock, key: str) -> None:
    """Advance the cursor and redraw, without rerunning the whole script."""
    cursor, playing = cursor_key(key), f"{PLAYING_PREFIX}:{key}"
    if not st.session_state.get(playing):
        return
    moment = advance(
        st.session_state[cursor],
        st.session_state[f"{SPEED_PREFIX}:{key}"],
        clock.end,
        FRAME_INTERVAL_SECONDS,
    )
    st.session_state[cursor] = moment
    _readout(st, series, moment, clock)
    render_dashboard(snapshot_at(session_data, moment, series))
    if moment >= clock.end:
        st.session_state[playing] = False


def _browser_view(session_data, key, series, found, clock, on_final) -> None:
    """The browser player, with the panels only Python can draw below it."""
    payload = replay_payload(session_data, key, series, clock)
    seek = f"{SEEK_PREFIX}:{key}"
    st.session_state.setdefault(seek, 0)
    seek_cursor = f"{SEEK_CURSOR_PREFIX}:{key}"
    st.session_state.setdefault(seek_cursor, st.session_state[cursor_key(key)])
    seek_focus = f"{SEEK_FOCUS_PREFIX}:{key}"
    if seek_focus not in st.session_state:
        _sync_seek_focus(key)
    render_replay_player(
        payload,
        key=f"{PLAYER_PREFIX}:{key}",
        cursor=st.session_state[seek_cursor],
        seek=st.session_state[seek],
        focus=st.session_state[seek_focus],
        on_cursor_change=lambda: _from_player(key, clock),
        on_focus_change=lambda: _focus_from_player(key),
        on_analyse_change=lambda: _analyse_from_player(key),
    )

    moment = st.session_state[cursor_key(key)]
    jump, final, readout = st.columns([3, 1.2, 5.8], gap="small")
    targets = {_event_label(t, clock, label): t for t, _, label in found}
    jump.selectbox(
        "Jump to",
        list(targets),
        index=None,
        placeholder="Jump to",
        key=f"{JUMP_PREFIX}:{key}",
        on_change=_jump,
        args=(key, targets, clock),
        label_visibility="collapsed",
    )
    if on_final is not None and final.button("Final result", width="stretch"):
        on_final()
    readout.caption(f"Sector leaders at {format_clock(moment - clock.lights_out)} (paused)")
    st.html(DASHBOARD_CSS)
    st.html(f'<div class="f1-dash">{_sector_cards(session_data, key, series, moment)}</div>')


def _sector_cards(session_data: dict, key: str, series: TowerSeries, moment: float) -> str:
    """Sector leaders at ``moment``, memoised per session and moment.

    A player report (focus, analyse) reruns the script without moving the
    cursor; rebuilding the snapshot and the tower each time cost ~170 ms.
    """
    memo_key = f"{SECTOR_MEMO_PREFIX}:{key}"
    memo = st.session_state.get(memo_key)
    rounded = round(float(moment), 1)
    if memo is not None and memo[0] == rounded:
        return memo[1]
    rows = build_timing_rows(snapshot_at(session_data, moment, series))
    markup = sector_cards_html(sector_leaders(rows))
    st.session_state[memo_key] = (rounded, markup)
    return markup


SECTOR_MEMO_PREFIX = "replay_sector_cards"
