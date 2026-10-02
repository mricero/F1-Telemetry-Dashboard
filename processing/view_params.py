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
