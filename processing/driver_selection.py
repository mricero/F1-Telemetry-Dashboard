"""Which drivers the Analysis charts plot, and how a link names them (UX-03).

Twenty-two lines on one chart cannot be read, so the charts start from the
top five of the session's classification. The selection travels in the URL
as ``drivers=VER,NOR`` (FEAT-14); a link is only ever read through
:func:`parse_codes`, which keeps the codes the session actually has. Pure:
no Streamlit, no network.
"""

from collections.abc import Iterable

import pandas as pd

DEFAULT_COUNT = 5


def _unique(codes: Iterable) -> list[str]:
    seen: list[str] = []
    for code in codes:
        if code is None or (not isinstance(code, str) and pd.isna(code)):
            continue
        text = str(code).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _results_order(results: pd.DataFrame | None) -> list[str]:
    """Finishing order from the official classification, when there is one."""
    if results is None or getattr(results, "empty", True):
        return []
    if "Abbreviation" not in results.columns or "Position" not in results.columns:
        return []
    position = pd.to_numeric(results["Position"], errors="coerce")
    ordered = results.assign(_pos=position).sort_values("_pos", na_position="last", kind="stable")
    return _unique(ordered["Abbreviation"])


def _laps_order(laps: pd.DataFrame | None) -> list[str]:
    """Running order on each driver's last lap: furthest lap first, then position."""
    if laps is None or getattr(laps, "empty", True) or "LapNumber" not in laps.columns:
        return []
    column = "DriverAcronym" if "DriverAcronym" in laps.columns else "Driver"
    if column not in laps.columns:
        return []
    work = pd.DataFrame(
        {
            "code": laps[column],
            "lap": pd.to_numeric(laps["LapNumber"], errors="coerce"),
            "pos": pd.to_numeric(
                laps.get("Position", pd.Series(index=laps.index)), errors="coerce"
            ),
        }
    ).dropna(subset=["code"])
    if work.empty:
        return []
    last = work.sort_values("lap", kind="stable").groupby("code", sort=False).tail(1)
    last = last.sort_values(["pos", "lap"], ascending=[True, False], na_position="last")
    return _unique(last["code"])


def classification_order(session_data: dict, laps: pd.DataFrame | None = None) -> list[str]:
    """Every driver code of the session, classified drivers first.

    The official results lead; drivers missing from them follow in their
    last-lap running order, then anyone who only has telemetry or a driver
    table row.
    """
    drivers = session_data.get("drivers")
    acronyms = (
        list(drivers["name_acronym"])
        if isinstance(drivers, pd.DataFrame) and "name_acronym" in drivers.columns
        else []
    )
    return _unique(
        [
            *_results_order(session_data.get("results")),
            *_laps_order(laps if laps is not None else session_data.get("laps")),
            *sorted(str(code) for code in (session_data.get("telemetry") or {})),
            *acronyms,
        ]
    )


def default_drivers(order: list[str], count: int = DEFAULT_COUNT) -> list[str]:
    """The top ``count`` of the classification, or everyone when fewer."""
    return list(order[: max(int(count), 0)])


def parse_codes(value, allowed: Iterable[str]) -> list[str]:
    """``"VER,NOR"`` (or a list of them) -> the codes ``allowed`` contains.

    Unknown codes are dropped, duplicates removed, order kept; codes are
    matched case-insensitively and returned as the session spells them.
    """
    if value is None:
        return []
    parts = value if isinstance(value, list | tuple) else [value]
    lookup = {str(code).upper(): str(code) for code in allowed}
    found: list[str] = []
    for part in parts:
        for raw in str(part).split(","):
            code = lookup.get(raw.strip().upper())
            if code is not None and code not in found:
                found.append(code)
    return found


def format_codes(codes: Iterable[str]) -> str:
    """The URL form of a selection: ``VER,NOR``."""
    return ",".join(_unique(codes))
