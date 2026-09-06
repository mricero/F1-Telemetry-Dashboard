"""Live-timing dashboard assembly (``layout.md`` sections 2-5).

Builds the header bar, the driver leaderboard matrix, the sector top-3
widgets and the track map panel, then lays them out on the spec's 60/40
grid. The dense components are hand-written HTML because Streamlit's own
table widgets cannot express micro-sector strips, tyre badges or team
accent bars.
"""

import html
from typing import Dict, List, Optional, Sequence

import pandas as pd
import streamlit as st

from processing.timing import (
    build_timing_rows,
    format_lap,
    micro_sector_times,
    sector_leaders,
    theoretical_best,
)
from ui.theme import (
    COMPOUND_LETTER,
    COMPOUND_RING,
    DASHBOARD_CSS,
    FLAG_STATES,
    segment_color,
    team_color,
)
from ui.track_map import build_track_svg, dominance_legend, dominance_segments

# Column headers for the leaderboard matrix (spec section 3).
TOWER_COLUMNS = [
    "Pos",
    "Driver",
    "Status",
    "Last lap",
    "Best lap",
    "Interval",
    "Gap",
    "Sector 1",
    "Sector 2",
    "Sector 3",
    "Tyre history",
    "Diff",
    "Speed",
]

_CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _cardinal(degrees) -> str:
    """Wind bearing in degrees -> compass point."""
    if degrees is None or pd.isna(degrees):
        return ""
    return _CARDINALS[int((float(degrees) % 360) / 45 + 0.5) % 8]


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _flag_state(session_data: dict) -> str:
    """Current flag condition from track status, else the last flag message."""
    info = session_data.get("session_info") or {}
    status = info.get("track_status")
    if isinstance(status, dict):
        code = str(status.get("status", ""))
        mapped = {
            "1": "GREEN",
            "2": "YELLOW",
            "4": "SAFETY CAR",
            "5": "RED",
            "6": "VSC",
            "7": "VSC",
        }
        if code in mapped:
            return mapped[code]

    race_control = session_data.get("race_control")
    if race_control is not None and not race_control.empty and "Flag" in race_control.columns:
        flags = race_control["Flag"].dropna()
        if not flags.empty:
            last = str(flags.iloc[-1]).upper()
            if last in FLAG_STATES:
                return last
    return "GREEN" if session_data.get("is_live") else "FINISHED"


def _session_length(weather: Optional[pd.DataFrame]) -> str:
    """Session duration as MM:SS, taken from the weather sampling window."""
    if weather is None or weather.empty or "Time" not in weather.columns:
        return "--:--"
    times = weather["Time"]
    if pd.api.types.is_timedelta64_dtype(times):
        total = float(times.max().total_seconds())
    else:
        parsed = pd.to_datetime(times, errors="coerce", utc=True)
        if parsed.notna().sum() < 2:
            return "--:--"
        total = float((parsed.max() - parsed.min()).total_seconds())
    return f"{int(total // 60):02d}:{int(total % 60):02d}"


def header_html(session_data: dict) -> str:
    """Global session + environment bar (spec section 2)."""
    info = session_data.get("session_info") or {}
    weather = session_data.get("weather")
    latest = (
        weather.iloc[-1] if weather is not None and not weather.empty else pd.Series(dtype="object")
    )

    flag = _flag_state(session_data)
    bg, fg, label = FLAG_STATES.get(flag, FLAG_STATES["FINISHED"])

    event = _esc(info.get("gp") or "Session")
    year = info.get("year")
    session_type = _esc(info.get("session_name") or info.get("session_type") or "")

    def reading(label_text: str, value: str, extra: str = "") -> str:
        return (
            f'<div class="f1-env-item"><span class="f1-env-label">{label_text}</span>'
            f'<span class="f1-env-value {extra} f1-mono">{value}</span></div>'
        )

    def number(key: str, unit: str, digits: int = 1) -> str:
        value = latest.get(key)
        if value is None or pd.isna(value):
            return "--"
        return f"{float(value):.{digits}f} {unit}"

    rain = latest.get("Rainfall")
    rain_yes = bool(rain) and not pd.isna(rain)
    wind_speed = latest.get("WindSpeed")
    wind = (
        f"{float(wind_speed):.1f} km/h {_cardinal(latest.get('WindDirection'))}".strip()
        if wind_speed is not None and not pd.isna(wind_speed)
        else "--"
    )

    return f"""
<div class="f1-header">
  <div class="f1-event">
    <span class="f1-event-name">{event}{f" {year}" if year else ""}</span>
    <span class="f1-event-session">{session_type}</span>
  </div>
  <div style="display:flex;align-items:center;gap:12px;">
    <span class="f1-clock f1-mono">{_session_length(weather)}</span>
    <span class="f1-flag" style="background:{bg};color:{fg};">{label}</span>
  </div>
  <div class="f1-env">
    {reading("Wind", wind)}
    {reading("Track", number("TrackTemp", "&deg;C"))}
    {reading("Air", number("AirTemp", "&deg;C"))}
    {reading("Humidity", number("Humidity", "%"))}
    {reading("Pressure", number("Pressure", "mb"))}
    {reading("Rain", "YES" if rain_yes else "NO", "rain-yes" if rain_yes else "")}
  </div>
</div>
"""


def _segments_html(states: Sequence[str]) -> str:
    cells = "".join(f'<span style="background:{segment_color(s)}"></span>' for s in states)
    return f'<div class="f1-seg">{cells}</div>'


def _tyres_html(history: Sequence[dict]) -> str:
    if not history:
        return '<span class="f1-dim">—</span>'
    badges = []
    for stint in history[:6]:
        compound = str(stint.get("compound", "UNKNOWN")).upper()
        letter = COMPOUND_LETTER.get(compound, "?")
        ring = COMPOUND_RING.get(compound, "#8a8a8a")
        laps = stint.get("laps_used", 0)
        badges.append(
            f'<span class="f1-tyre" style="border-color:{ring};color:{ring}" '
            f'title="{_esc(compound)} - {laps} laps">{letter}<em>{laps}</em></span>'
        )
    return f'<div class="f1-tyres">{"".join(badges)}</div>'


def _status_html(status: str) -> str:
    css = {
        "IN PIT": "pit",
        "ON TRACK": "track",
        "KO": "ko",
        "CLASSIFIED": "track",
    }.get(status, "out")
    return f'<span class="f1-badge {css}">{_esc(status)}</span>'


def tower_html(rows: Sequence[dict], cutoff: int = 10) -> str:
    """The driver leaderboard matrix (spec section 3)."""
    if not rows:
        return '<div style="padding:24px;color:#8a8a8a">No timing data for this session.</div>'

    head = "".join(f"<th>{_esc(c)}</th>" for c in TOWER_COLUMNS)
    body: List[str] = []
    split_emitted = False

    for row in rows:
        if row.get("knocked_out") and not split_emitted:
            body.append(
                f'<tr><td colspan="{len(TOWER_COLUMNS)}" class="f1-split">'
                f"Outside the top {cutoff}</td></tr>"
            )
            split_emitted = True

        accent = team_color(row.get("team_name"), row.get("team_colour"))
        best_class = "f1-time best" if row.get("is_overall_best") else "f1-time"
        status = (
            "KO" if row.get("knocked_out") and row.get("status") == "CLASSIFIED" else row["status"]
        )
        speed = row.get("speed_kmh")
        speed_text = f"{speed:.0f} km/h" if speed is not None and pd.notna(speed) else "—"

        sector_cells = "".join(
            f'<td><span class="f1-time f1-mono">{_esc(s["display"])}</span>'
            f'{_segments_html(s["segments"])}</td>'
            for s in row["sectors"]
        )

        body.append(
            f'<tr class="{"ko" if row.get("knocked_out") else ""}">'
            f'<td class="f1-pos" style="--accent:{accent}">{row["position"]}</td>'
            f'<td><div class="f1-code">{_esc(row["code"])}</div>'
            f'<div class="f1-team">{_esc(row.get("team_name"))}</div></td>'
            f"<td>{_status_html(status)}</td>"
            f'<td><span class="f1-time f1-mono">{_esc(row["last_lap"])}</span></td>'
            f'<td><span class="{best_class} f1-mono">{_esc(row["best_lap"])}</span></td>'
            f'<td><span class="f1-time f1-mono f1-dim">{_esc(row["interval"])}</span></td>'
            f'<td><span class="f1-time f1-mono f1-dim">{_esc(row["gap"])}</span></td>'
            f"{sector_cells}"
            f"<td>{_tyres_html(row['tyre_history'])}</td>"
            f'<td><span class="f1-time f1-mono f1-dim">{_esc(row["diff"])}</span></td>'
            f'<td><span class="f1-time f1-mono">{_esc(speed_text)}</span></td>'
            "</tr>"
        )

    return (
        '<div style="max-height:640px;overflow:auto;">'
        f'<table class="f1-tower"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def sector_cards_html(leaders: Sequence[Sequence[dict]]) -> str:
    """Sector top-3 widgets (spec section 5)."""
    cards = []
    for index, entries in enumerate(leaders, start=1):
        if entries:
            body = "".join(
                f'<div class="f1-sector-row"><span class="f1-rank">{e["rank"]}</span>'
                f'<span class="f1-pill" style="background:'
                f'{team_color(e.get("team_name"), e.get("team_colour"))}">{_esc(e["code"])}</span>'
                f'<span class="f1-sector-time f1-mono">{_esc(e["time"])}</span></div>'
                for e in entries
            )
        else:
            body = '<div class="f1-sector-row"><span class="f1-dim">No data</span></div>'
        cards.append(
            f'<div class="f1-sector-card"><div class="f1-sector-head">Sector {index}</div>'
            f"{body}</div>"
        )
    return f'<div class="f1-sectors">{"".join(cards)}</div>'


def _driver_meta(rows: Sequence[dict]) -> Dict[str, dict]:
    return {
        r["code"]: {"team_name": r.get("team_name"), "team_colour": r.get("team_colour")}
        for r in rows
    }


def _last_positions(location: Dict[str, pd.DataFrame], rows: Sequence[dict]) -> List[dict]:
    """Driver nodes for the map - only meaningful while a session is live."""
    meta = _driver_meta(rows)
    markers = []
    for code, frame in (location or {}).items():
        if frame is None or frame.empty or not {"X", "Y"}.issubset(frame.columns):
            continue
        point = frame.dropna(subset=["X", "Y"])
        if point.empty:
            continue
        last = point.iloc[-1]
        info = meta.get(code, {})
        markers.append(
            {
                "code": code,
                "x": float(last["X"]),
                "y": float(last["Y"]),
                "team_colour": team_color(info.get("team_name"), info.get("team_colour")),
            }
        )
    return markers


def map_panel_html(session_data: dict, rows: Sequence[dict]) -> str:
    """Track map with dominance colouring, corners and a benchmark overlay."""
    location = session_data.get("location") or {}
    telemetry = session_data.get("telemetry") or {}

    micro = {}
    for code, frame in telemetry.items():
        times = micro_sector_times(frame)
        if times is not None:
            micro[code] = times
    dominance = dominance_segments(micro)
    meta = _driver_meta(rows)

    markers = _last_positions(location, rows) if session_data.get("is_live") else []
    svg = build_track_svg(
        location,
        circuit_info=session_data.get("circuit_info"),
        driver_meta=meta,
        dominance=dominance,
        markers=markers,
    )
    if svg is None:
        return (
            '<div style="padding:32px;color:#8a8a8a;text-align:center;">'
            "No GPS telemetry for this session, so the track map cannot be drawn."
            "</div>"
        )

    best = theoretical_best(rows)
    leader = rows[0]["best_lap"] if rows else "—"
    ideal = (
        f'<div class="f1-bench-label" style="margin-top:4px">Ideal {format_lap(best)}</div>'
        if best is not None
        else ""
    )
    bench = (
        '<div class="f1-bench"><div class="f1-bench-label">Session best</div>'
        f'<div class="f1-bench-time f1-mono">{_esc(leader)}</div>{ideal}</div>'
    )
    return f'<div class="f1-map-wrap">{bench}{svg}</div>{dominance_legend(dominance, meta)}'


def render_dashboard(session_data: dict, cutoff: int = 10) -> None:
    """Render the full timing dashboard on the spec's 60/40 grid."""
    st.html(DASHBOARD_CSS)

    rows = build_timing_rows(session_data, cutoff=cutoff)
    st.html(f'<div class="f1-dash">{header_html(session_data)}</div>')

    left, right = st.columns([6, 4], gap="small")
    with left:
        st.html(f'<div class="f1-dash">{tower_html(rows, cutoff=cutoff)}</div>')
    with right:
        st.html(f'<div class="f1-dash">{sector_cards_html(sector_leaders(rows))}</div>')
        st.html(f'<div class="f1-dash">{map_panel_html(session_data, rows)}</div>')
