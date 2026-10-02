"""Per-viewer preferences and the parts of a view a link carries.

UX-03: the Analysis charts plot a chosen set of drivers (default: the top
five of the classification), kept per session in ``st.session_state`` and
mirrored in the URL as ``drivers=VER,NOR``; favourite drivers (``fav=``)
are marked in the Results tower.

Every value read from the URL goes through a validator first: a link is
input from anyone, so an unknown code or value is ignored, never shown.
"""

import re

import pandas as pd
import streamlit as st

from processing.driver_selection import default_drivers, format_codes, parse_codes
from processing.view_params import (
    HIDE_COLUMNS_PARAM,
    HIDE_PANELS_PARAM,
    PANEL_CHOICES,
    TOWER_COLUMN_CHOICES,
    format_tokens,
    parse_tokens,
)

DRIVERS_PARAM = "drivers"
DRIVERS_PREFIX = "analysis_drivers"
DRIVERS_WIDGET_PREFIX = "analysis_drivers_pick"

FAVOURITES_PARAM = "fav"
FAVOURITES_KEY = "favourite_drivers"
FAVOURITES_WIDGET = "favourite_drivers_pick"
# A driver code as F1 writes it; anything else in a link is dropped.
_CODE = re.compile(r"^[A-Z]{3}$")
MAX_FAVOURITES = 6


def mirror_param(name: str, value: str | None) -> None:
    """Write ``name=value`` to the URL, or remove it when ``value`` is None."""
    if value is None:
        if name in st.query_params:
            del st.query_params[name]
    elif st.query_params.get(name) != value:
        st.query_params[name] = value


def _url_value(name: str):
    """A parameter's values as a list, or None when the URL lacks it."""
    if name not in st.query_params:
        return None
    return st.query_params.get_all(name)


# ---------------------------------------------------------------- drivers


def selected_drivers(session_key: str, order: list[str]) -> list[str]:
    """The drivers the Analysis charts plot for one session.

    First use reads ``drivers=`` from the URL (codes the session does not
    have are dropped; a link naming none of them is ignored), else the top
    five of the classification.
    """
    state = f"{DRIVERS_PREFIX}:{session_key}"
    if state not in st.session_state:
        raw = _url_value(DRIVERS_PARAM)
        chosen = parse_codes(raw, order) if raw is not None else []
        explicit_none = raw is not None and all(not str(part).strip() for part in raw)
        if not chosen and not explicit_none:
            chosen = default_drivers(order)
        st.session_state[state] = chosen
    return [code for code in st.session_state[state] if code in order]


def render_driver_picker(session_key: str, order: list[str]) -> list[str]:
    """The Analysis driver multiselect; returns the selection.

    The selection is kept outside the widget, so it survives the widget
    being unmounted on another page or section.
    """
    state = f"{DRIVERS_PREFIX}:{session_key}"
    widget = f"{DRIVERS_WIDGET_PREFIX}:{session_key}"
    chosen = selected_drivers(session_key, order)

    def _changed() -> None:
        st.session_state[state] = list(st.session_state.get(widget) or [])

    st.multiselect(
        "Drivers",
        order,
        default=chosen,
        key=widget,
        on_change=_changed,
        placeholder="Choose drivers",
        help="The drivers the telemetry, lap time and position charts plot.",
    )
    chosen = selected_drivers(session_key, order)
    mirror_param(DRIVERS_PARAM, None if chosen == default_drivers(order) else format_codes(chosen))
    if not chosen:
        st.info("No drivers selected. Choose at least one above.")
    return chosen


def only_drivers(frames: dict[str, pd.DataFrame], drivers) -> dict[str, pd.DataFrame]:
    """The per-driver frames of the selected drivers, in selection order."""
    if drivers is None:
        return dict(frames)
    return {code: frames[code] for code in drivers if code in frames}


# ------------------------------------------------------------- favourites


def _parse_favourites(raw) -> list[str]:
    codes: list[str] = []
    for part in raw or []:
        for code in str(part).split(","):
            code = code.strip().upper()
            if _CODE.match(code) and code not in codes:
                codes.append(code)
    return codes[:MAX_FAVOURITES]


def favourite_drivers() -> list[str]:
    """The viewer's favourite drivers, seeded once from ``fav=`` in the URL."""
    if FAVOURITES_KEY not in st.session_state:
        st.session_state[FAVOURITES_KEY] = _parse_favourites(_url_value(FAVOURITES_PARAM))
    return list(st.session_state[FAVOURITES_KEY])


def render_favourites_picker(order: list[str]) -> list[str]:
    """Favourite drivers, marked in the Results tower."""
    current = favourite_drivers()
    options = list(dict.fromkeys([*order, *current]))

    def _changed() -> None:
        st.session_state[FAVOURITES_KEY] = list(st.session_state.get(FAVOURITES_WIDGET) or [])

    st.multiselect(
        "Favourite drivers",
        options,
        default=current,
        key=FAVOURITES_WIDGET,
        on_change=_changed,
        max_selections=MAX_FAVOURITES,
        placeholder="None",
        help="Favourites are underlined in the Results tower.",
    )
    sync_preference_params()
    return favourite_drivers()


# ----------------------------------------------------------------- layout

HIDDEN_COLUMNS_KEY = "layout_hidden_columns"
HIDDEN_PANELS_KEY = "layout_hidden_panels"
COLUMNS_WIDGET = "layout_columns_shown"
PANELS_WIDGET = "layout_panels_shown"


def _hidden(state: str, param: str, choices) -> list[str]:
    """What the viewer hid, seeded once from the URL (unknown names dropped)."""
    if state not in st.session_state:
        st.session_state[state] = parse_tokens(_url_value(param), choices)
    return list(st.session_state[state])


def hidden_columns() -> list[str]:
    """Tower columns the viewer hid (FEAT-10); nothing hidden is today's layout."""
    return _hidden(HIDDEN_COLUMNS_KEY, HIDE_COLUMNS_PARAM, TOWER_COLUMN_CHOICES)


def hidden_panels() -> list[str]:
    """Panels the viewer hid (FEAT-10)."""
    return _hidden(HIDDEN_PANELS_KEY, HIDE_PANELS_PARAM, PANEL_CHOICES)


def _shown_picker(label, choices, hidden, state, widget, help_text) -> None:
    names = [name for name, _ in choices]
    labels = dict(choices)

    def _changed() -> None:
        shown = set(st.session_state.get(widget) or [])
        st.session_state[state] = [name for name in names if name not in shown]

    st.multiselect(
        label,
        names,
        default=[name for name in names if name not in hidden],
        key=widget,
        on_change=_changed,
        format_func=lambda name: labels[name],
        help=help_text,
    )


def render_layout_pickers() -> None:
    """The Settings checklists: which tower columns and panels are shown."""
    _shown_picker(
        "Tower columns shown",
        TOWER_COLUMN_CHOICES,
        hidden_columns(),
        HIDDEN_COLUMNS_KEY,
        COLUMNS_WIDGET,
        "Applies to the replay and results towers; a column a view does not have is ignored.",
    )
    _shown_picker(
        "Panels shown",
        PANEL_CHOICES,
        hidden_panels(),
        HIDDEN_PANELS_KEY,
        PANELS_WIDGET,
        "Applies to the replay and results pages.",
    )
    sync_preference_params()


def layout_for_player() -> dict:
    """The layout the browser player receives."""
    return {"hide_cols": hidden_columns(), "hide_panels": hidden_panels()}


def preference_params() -> dict[str, str]:
    """The viewer-wide preferences as URL parameters (empty when default)."""
    params: dict[str, str] = {}
    favourites = favourite_drivers()
    if favourites:
        params[FAVOURITES_PARAM] = format_codes(favourites)
    for name, hidden in (
        (HIDE_COLUMNS_PARAM, hidden_columns()),
        (HIDE_PANELS_PARAM, hidden_panels()),
    ):
        text = format_tokens(hidden)
        if text:
            params[name] = text
    return params


def sync_preference_params() -> None:
    """Mirror the viewer-wide preferences into the URL, on every run.

    Loading a session rewrites the URL from scratch; this puts the
    preferences back, so a copied link always carries them.
    """
    params = preference_params()
    for name in (FAVOURITES_PARAM, HIDE_COLUMNS_PARAM, HIDE_PANELS_PARAM):
        mirror_param(name, params.get(name))
