"""Championship standings and the points-after-this-race projection (FEAT-06).

Pure transforms: no Streamlit, no network. The Jolpica payloads come in from
``data.jolpica_adapter``; the running order of a live race comes in from the
session's ``standings`` table.

Points are the current scale: 25-18-15-12-10-8-6-4-2-1 for a race and
8-7-6-5-4-3-2-1 for a sprint. The fastest-lap point was abolished from 2025,
so there is none to add. Half points for a race stopped early are not
modelled: the projection assumes the full distance.
"""

import re
from datetime import timedelta

import pandas as pd

RACE_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
SPRINT_POINTS = (8, 7, 6, 5, 4, 3, 2, 1)

DRIVER_COLUMNS = ["Position", "Code", "Driver", "Team", "ConstructorId", "Points", "Wins"]
CONSTRUCTOR_COLUMNS = ["Position", "Team", "ConstructorId", "Points", "Wins"]

# FastF1's session date is any moment of the weekend; Jolpica dates the race.
ROUND_DATE_TOLERANCE = timedelta(days=3)


def points_for(position: int, sprint: bool = False) -> int:
    """Points for finishing in ``position`` (1-based); 0 outside the points."""
    table = SPRINT_POINTS if sprint else RACE_POINTS
    return table[position - 1] if 1 <= position <= len(table) else 0


def _standings_list(payload: dict | None) -> dict:
    lists = ((payload or {}).get("MRData") or {}).get("StandingsTable", {}).get("StandingsLists")
    return lists[0] if lists else {}


def _number(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_driver_standings(payload: dict | None) -> pd.DataFrame:
    """Jolpica ``driverStandings`` -> one row per driver, in championship order."""
    rows: list[dict] = []
    for entry in _standings_list(payload).get("DriverStandings", []):
        driver = entry.get("Driver") or {}
        teams = entry.get("Constructors") or [{}]
        team = teams[-1]  # the team the driver last scored for
        rows.append(
            {
                "Position": _number(entry.get("position"), len(rows) + 1),
                "Code": str(driver.get("code") or "").upper(),
                "Driver": f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                "Team": team.get("name", ""),
                "ConstructorId": team.get("constructorId", ""),
                "Points": _number(entry.get("points")),
                "Wins": _number(entry.get("wins")),
            }
        )
    return pd.DataFrame(rows, columns=DRIVER_COLUMNS)


def parse_constructor_standings(payload: dict | None) -> pd.DataFrame:
    """Jolpica ``constructorStandings`` -> one row per team, in championship order."""
    rows: list[dict] = []
    for entry in _standings_list(payload).get("ConstructorStandings", []):
        team = entry.get("Constructor") or {}
        rows.append(
            {
                "Position": _number(entry.get("position"), len(rows) + 1),
                "Team": team.get("name", ""),
                "ConstructorId": team.get("constructorId", ""),
                "Points": _number(entry.get("points")),
                "Wins": _number(entry.get("wins")),
            }
        )
    return pd.DataFrame(rows, columns=CONSTRUCTOR_COLUMNS)


def _normal(name) -> str:
    text = re.sub(r"formula\s*1|grand\s*prix|\bgp\b", " ", str(name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", text)


def round_for_event(schedule: pd.DataFrame | None, gp: str | None, date=None) -> int | None:
    """The round of a season's calendar that a session belongs to.

    ``schedule`` is ``JolpicaAdapter.get_schedule`` (``round``, ``race_name``,
    ``date``). The session date wins (any day of the weekend, within three
    days of the race); the Grand Prix name is the fallback, which is all a live
    session has.
    """
    if schedule is None or schedule.empty:
        return None
    when = pd.to_datetime(date, utc=True, errors="coerce") if date is not None else pd.NaT
    if pd.notna(when) and "date" in schedule.columns:
        gap = (pd.to_datetime(schedule["date"], utc=True) - when).abs()
        if gap.min() <= ROUND_DATE_TOLERANCE:
            return int(schedule.loc[gap.idxmin(), "round"])
    wanted = _normal(gp)
    if wanted:
        names = schedule["race_name"].map(_normal)
        hit = schedule[names == wanted]
        if hit.empty:
            hit = schedule[names.map(lambda n: bool(n) and (n in wanted or wanted in n))]
        if len(hit) == 1:
            return int(hit.iloc[0]["round"])
    return None


def project_standings(
    drivers: pd.DataFrame,
    constructors: pd.DataFrame,
    order: list[str],
    sprint: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Both championships if the race ended in ``order`` (driver codes, P1 first).

    Adds ``Gain`` (points this race), ``Projected``, ``ProjectedPosition`` and
    ``Change`` (places gained, negative when lost). A team scores what its
    drivers score; a driver missing from ``drivers`` (no points yet this
    season) is not placed and scores nothing for any team.
    """
    gain_by_code = {code: points_for(place, sprint) for place, code in enumerate(order, 1)}

    drv = drivers.copy()
    drv["Gain"] = drv["Code"].map(gain_by_code).fillna(0).astype(int)
    team_gain = drv.groupby("ConstructorId")["Gain"].sum()
    con = constructors.copy()
    con["Gain"] = con["ConstructorId"].map(team_gain).fillna(0).astype(int)

    return _rank(drv), _rank(con)


def _rank(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["Projected"] = frame["Points"] + frame["Gain"]
    # Stable sort: equal points keep the current order (the real tie-break is
    # on countback, which needs every result).
    ranked = frame.sort_values(["Projected", "Position"], ascending=[False, True], kind="stable")
    frame["ProjectedPosition"] = pd.Series(range(1, len(ranked) + 1), index=ranked.index)
    frame["Change"] = frame["Position"] - frame["ProjectedPosition"]
    return frame.loc[ranked.index].reset_index(drop=True)
