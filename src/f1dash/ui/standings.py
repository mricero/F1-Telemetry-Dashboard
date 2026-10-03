"""Championship standings and the live points projection (FEAT-06).

Drivers' and constructors' standings from Jolpica as they stood after the
selected round, on the Results page. While a race is live there is no Results
page, so the Live page shows the standings before this round and what they
become if the race finished in the current order.

Everything that touches the network is cached with a TTL (Streamlit reruns the
script on every interaction); the maths is in ``processing.standings``.
"""

import pandas as pd
import streamlit as st

from data.jolpica_adapter import JolpicaAdapter
from processing.standings import (
    parse_constructor_standings,
    parse_driver_standings,
    project_standings,
    round_for_event,
)
from processing.timing import is_race_session
from ui.status import DataStatus, show

STANDINGS_TTL_SECONDS = 1800
SPRINT_NAMES = {"s", "sprint"}

PROJECTED_NOTE = (
    "25-18-15-12-10-8-6-4-2-1 points for a race, 8-7-6-5-4-3-2-1 for a sprint; "
    "no fastest-lap point since 2025. Cars that have stopped score nothing."
)


@st.cache_data(ttl=STANDINGS_TTL_SECONDS, show_spinner=False)
def _round_cached(year: int, gp: str | None, date: str | None) -> int | None:
    """The calendar round of a session, from the Jolpica schedule."""
    return round_for_event(JolpicaAdapter().get_schedule(year), gp, date)


@st.cache_data(ttl=STANDINGS_TTL_SECONDS, show_spinner=False)
def _standings_cached(year: int, round_num: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Driver and constructor standings after one round."""
    adapter = JolpicaAdapter()
    return (
        parse_driver_standings(adapter.get_driver_standings(year, round_num)),
        parse_constructor_standings(adapter.get_constructor_standings(year, round_num)),
    )


def is_sprint(info: dict) -> bool:
    return any(
        str(info.get(key) or "").strip().lower() in SPRINT_NAMES
        for key in ("session_type", "session_name")
    )


def live_order(session_data: dict) -> list[str]:
    """Driver codes in the live running order, cars that have stopped left out."""
    table = session_data.get("standings")
    if not isinstance(table, pd.DataFrame) or table.empty:
        return []
    table = table.sort_values("Position")
    if "Status" in table.columns:
        table = table[table["Status"] != "OUT"]
    return [str(code) for code in table["Driver"]]


def _table(frame: pd.DataFrame, columns: list[str], labels: list[str]) -> None:
    shown = frame[columns].copy()
    if "Change" in shown:
        shown["Change"] = shown["Change"].map(lambda v: "–" if v == 0 else f"{v:+d}")
    shown.columns = pd.Index(labels)
    st.dataframe(shown, hide_index=True, width="stretch")


def render_standings(session_data: dict) -> None:
    """Both championships as of the session's round, plus the live projection."""
    info = session_data.get("session_info") or {}
    year = info.get("year")
    live = bool(session_data.get("is_live") or info.get("is_live"))
    if not year:
        show(DataStatus.unavailable("The session has no season, so standings cannot be looked up"))
        return
    date = info.get("date")
    try:
        round_num = _round_cached(int(year), info.get("gp"), None if date is None else str(date))
        if round_num is None:
            show(DataStatus.unavailable(f"Could not match this session to a {year} round"))
            return
        # A live round is not in the table yet: the base is the previous one.
        base = round_num - 1 if live else round_num
        if base < 1:
            show(DataStatus.unavailable("There are no standings before the first round"))
            return
        drivers, constructors = _standings_cached(int(year), int(base))
    except ConnectionError:
        show(DataStatus.unavailable("Jolpica could not be reached, so standings are not shown"))
        return
    if drivers.empty and constructors.empty:
        show(DataStatus.unavailable(f"Jolpica has no standings for {year} after round {base}"))
        return

    order = live_order(session_data) if live and is_race_session(info) else []
    if order and not drivers.empty:
        drivers, constructors = project_standings(drivers, constructors, order, is_sprint(info))
        st.caption(
            f"Championship after round {base}, and if the race finished in the current "
            f"order. {PROJECTED_NOTE}"
        )
        driver_cols = ["ProjectedPosition", "Code", "Team", "Points", "Gain", "Projected", "Change"]
        driver_labels = ["POS", "DRIVER", "TEAM", "NOW", "RACE", "PROJECTED", "CHANGE"]
        team_cols = ["ProjectedPosition", "Team", "Points", "Gain", "Projected", "Change"]
        team_labels = ["POS", "TEAM", "NOW", "RACE", "PROJECTED", "CHANGE"]
    else:
        st.caption(f"Championship after round {base} of {year}, from Jolpica.")
        driver_cols = ["Position", "Code", "Driver", "Team", "Points", "Wins"]
        driver_labels = ["POS", "CODE", "DRIVER", "TEAM", "POINTS", "WINS"]
        team_cols = ["Position", "Team", "Points", "Wins"]
        team_labels = ["POS", "TEAM", "POINTS", "WINS"]

    left, right = st.columns(2, gap="small")
    with left:
        st.markdown("**Drivers**")
        if drivers.empty:
            show(DataStatus.unavailable("No driver standings yet"))
        else:
            _table(drivers, driver_cols, driver_labels)
    with right:
        st.markdown("**Constructors**")
        if constructors.empty:
            show(DataStatus.unavailable("No constructor standings yet"))
        else:
            _table(constructors, team_cols, team_labels)
