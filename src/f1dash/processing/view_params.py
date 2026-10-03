"""The parts of a view a shared link carries, and how they are read back.

FEAT-14: besides the session (``year``/``gp``/``session``), a link carries the
Analysis section (``section=``, bound to its widget in ``ui.pages``), the
chosen drivers (``drivers=``, :mod:`processing.driver_selection`) and the
replay cursor (``t=``, seconds after lights out). A link is input from anyone:
every value goes through a parser here that returns a safe value or ``None``,
never raises, and clamps to what the session actually has. Pure: no
Streamlit, no network.
"""

import math

CURSOR_PARAM = "t"
SECTION_PARAM = "section"


def parse_cursor(raw, low: float, high: float) -> float | None:
    """``"2467.5"`` -> seconds after lights out, clamped to ``[low, high]``.

    ``None`` for anything that is not a finite number (a list uses its last
    value, the way ``st.query_params`` does).
    """
    if isinstance(raw, list | tuple):
        raw = raw[-1] if raw else None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    if not math.isfinite(value) or high < low:
        return None
    return min(max(value, low), high)


def format_cursor(seconds: float, default: float = 0.0) -> str | None:
    """The URL form of a cursor, or ``None`` when it is at the default.

    One decimal at most: the player's own resolution is coarser than that,
    and a shorter value is a shorter link.
    """
    if seconds is None or not math.isfinite(seconds):
        return None
    if abs(seconds - default) < 0.05:
        return None
    return f"{seconds:.1f}".rstrip("0").rstrip(".")


# FEAT-10: the layout a viewer can change. Each entry is (name in the link,
# label). The defaults are today's layout (everything shown), so a link only
# ever names what is *hidden*: ``hide_cols=tyres,pit&hide_panels=map``.
HIDE_COLUMNS_PARAM = "hide_cols"
HIDE_PANELS_PARAM = "hide_panels"

TOWER_COLUMN_CHOICES = (
    ("status", "Status"),
    ("last", "Last lap"),
    ("best", "Best lap"),
    ("gap", "Gap and interval"),
    ("sectors", "Sectors"),
    ("tyres", "Tyres"),
    ("pit", "Pit stops"),
    ("diff", "Diff to ideal"),
    ("speed", "Speed"),
)
PANEL_CHOICES = (
    ("map", "Track map"),
    ("strip", "Track position strip"),
    ("card", "Driver card"),
    ("rc", "Race control"),
    ("sectors", "Sector leaders"),
)


def parse_tokens(raw, choices) -> list[str]:
    """``"tyres,pit"`` (or a list of them) -> the names in ``choices``, in their order.

    Unknown names are dropped and duplicates removed; matching ignores case.
    """
    if raw is None:
        return []
    parts = raw if isinstance(raw, list | tuple) else [raw]
    wanted = {token.strip().lower() for part in parts for token in str(part).split(",")}
    return [name for name, _ in choices if name in wanted]


def format_tokens(names) -> str | None:
    """The URL form of a hidden set, or ``None`` when nothing is hidden."""
    text = ",".join(names)
    return text or None
