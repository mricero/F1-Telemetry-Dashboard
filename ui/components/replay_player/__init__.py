"""The browser replay player as a Streamlit component (IMPROVEMENTS.md REPLAY-05).

A ``st.components.v2`` component with no build step: the HTML, CSS and
JavaScript beside this file are read at registration and handed to
Streamlit inline. The player animates the whole replay in the browser from
one payload (:mod:`processing.replay_payload`), so playback costs the server
nothing and runs at the display's refresh rate.

It reports its cursor only on pause, on a seek while paused and at the end,
and its focused driver when that changes: every state change reruns the
script.
"""

from pathlib import Path

import streamlit as st

from ui.theme import (
    COMPOUND_LETTER,
    COMPOUND_RING,
    FLAG_STATES,
    safe_hex,
    team_color,
    text_on,
)

_DIR = Path(__file__).resolve().parent
COMPONENT_NAME = "f1_replay_player"
_registered: dict = {}


def component_source() -> dict[str, str]:
    """The player's HTML, CSS and JavaScript, read from disk."""
    return {
        kind: (_DIR / f"player.{kind}").read_text(encoding="utf-8")
        for kind in ("html", "css", "js")
    }


def _registry():
    """The running app's component registry, or None outside a runtime."""
    from streamlit.runtime import Runtime

    if not Runtime.exists():
        return None
    return Runtime.instance().bidi_component_registry


def _player():
    """Register the component on first use in each runtime, then reuse it.

    Registration needs a running Streamlit runtime, so it cannot happen at
    import time (tests and scripts import this module without one), and a
    new runtime (a restart, a test) starts with an empty registry.
    """
    registry = _registry()
    if "renderer" not in _registered or (
        registry is not None and registry.get(COMPONENT_NAME) is None
    ):
        source = component_source()
        _registered["renderer"] = st.components.v2.component(
            COMPONENT_NAME, html=source["html"], css=source["css"], js=source["js"]
        )
    return _registered["renderer"]


def player_style(payload: dict, compound_colors: dict | None = None) -> dict:
    """Team, flag and tyre colours for the payload, from ``ui.theme``.

    Kept out of :mod:`processing.replay_payload` so the data layer stays
    free of presentation.
    """
    teams = {}
    for driver in payload.get("drivers", []):
        fill = team_color(driver.get("team"), driver.get("team_colour"))
        teams[driver["code"]] = {"fill": fill, "text": text_on(fill)}
    palette = dict(COMPOUND_RING)
    for compound, colour in (compound_colors or {}).items():
        name = str(compound).upper()
        palette[name] = safe_hex(colour, COMPOUND_RING.get(name, COMPOUND_RING["UNKNOWN"]))
    compounds = {
        name: {"letter": COMPOUND_LETTER.get(name, "?"), "colour": safe_hex(colour)}
        for name, colour in palette.items()
    }
    flags = {key: list(value) for key, value in FLAG_STATES.items()}
    return {"teams": teams, "flags": flags, "compounds": compounds}


def render_replay_player(
    payload: dict,
    key: str,
    cursor: float,
    seek: int,
    on_cursor_change,
    on_focus_change,
    on_analyse_change=None,
):
    """Mount the player.

    ``cursor`` and ``seek`` travel with the data: when Python moves the
    cursor (a jump chosen outside the player) it bumps ``seek`` and the
    player follows. The player's own moves come back through
    ``on_cursor_change``.
    """
    data = {**payload, "cursor": cursor, "seek": seek}
    return _player()(
        key=key,
        data=data,
        default={"cursor": payload["clock"]["lights_out"], "focus": None},
        on_cursor_change=on_cursor_change,
        on_focus_change=on_focus_change,
        on_analyse_change=on_analyse_change or (lambda: None),
    )
