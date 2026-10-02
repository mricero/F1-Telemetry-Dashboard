"""Analysis panels built from lap tables: rankings (FEAT-09), deleted laps (FEAT-11).

Kept out of ``ui/layout.py`` so the big module stays a single contract; the
numbers come from :mod:`processing.lap_review` and :mod:`processing.deleted_laps`.
"""

import pandas as pd
import streamlit as st

from processing.lap_review import SPEED_TRAPS, sector_ranking, speed_ranking
from processing.timing import MISSING, SECTORS, format_lap

RANKING_LIMIT = 10


def _lap_cell(value) -> str:
    return MISSING if pd.isna(value) else str(int(value))


def ranking_table(ranking: pd.DataFrame, unit: str, limit: int = RANKING_LIMIT) -> pd.DataFrame:
    """A ``Rank/Driver/Value/Lap/Gap`` frame as display strings.

    ``unit`` is ``"km/h"`` or ``"s"``; the unit sits once in the column
    headers (guideline 5.7), never in the cells.
    """
    speed = unit == "km/h"
    head = ranking.head(limit)
    value_header, gap_header = ("SPEED KM/H", "GAP KM/H") if speed else ("TIME", "GAP")
    if speed:
        values = [f"{v:.0f}" for v in head["Value"]]
        gaps = [
            MISSING if r == 1 else f"-{g:.0f}"
            for r, g in zip(head["Rank"], head["Gap"], strict=True)
        ]
    else:
        values = [format_lap(v) for v in head["Value"]]
        gaps = [
            MISSING if r == 1 else f"+{g:.3f}"
            for r, g in zip(head["Rank"], head["Gap"], strict=True)
        ]
    return pd.DataFrame(
        {
            "Pos": head["Rank"].astype(int).to_numpy(),
            "Driver": head["Driver"].to_numpy(),
            value_header: values,
            "Lap": [_lap_cell(v) for v in head["Lap"]],
            gap_header: gaps,
        }
    )


def render_rankings(laps: pd.DataFrame | None) -> None:
    """Who is quickest through each sector and each speed trap (FEAT-09)."""
    if laps is None or laps.empty:
        st.info("No lap data for this session, so there is nothing to rank.")
        return
    panels: list[tuple[str, pd.DataFrame, str]] = [
        (f"Sector {index}", sector_ranking(laps, index), "s") for index in range(1, SECTORS + 1)
    ] + [(label, speed_ranking(laps, column), "km/h") for column, label in SPEED_TRAPS]
    shown = [panel for panel in panels if not panel[1].empty]
    if not shown:
        st.info("No sector times or speed-trap readings in this session.")
        return
    for start in range(0, len(shown), 2):
        columns = list(st.columns(2))
        for column, (label, ranking, unit) in zip(columns, shown[start : start + 2], strict=False):
            with column:
                st.markdown(f"**{label}**")
                st.dataframe(ranking_table(ranking, unit), hide_index=True, width="stretch")
    missing = [label for label, ranking, _ in panels if ranking.empty]
    st.caption(
        "Best valid lap per driver: deleted and inaccurate laps are left out, so a "
        "track-limits lap never sets a ranking."
        + (f" No data for {', '.join(missing)}." if missing else "")
    )
