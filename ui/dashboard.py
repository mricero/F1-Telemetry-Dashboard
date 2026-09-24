"""Live-timing dashboard assembly (``layout.md`` sections 2-5).

Builds the header bar, the driver leaderboard matrix, the sector top-3
widgets and the track map panel, then lays them out on the spec's 60/40
grid. The dense components are hand-written HTML because Streamlit's own
table widgets cannot express micro-sector strips, tyre badges or team
accent bars.
"""

import html
from collections.abc import Sequence

import pandas as pd
import streamlit as st

from processing.replay import format_clock, positions_at
from processing.replay_model import TRACK_STATUS_FLAGS, flag_state, race_clock_text
from processing.time_utils import to_seconds
from processing.timing import (
    build_timing_rows,
    dashboard_frames,
    format_lap,
    micro_sector_marks,
    micro_sector_times,
    sector_bounds_for_driver,
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
    text_on,
)
from ui.track_map import (
    build_track_svg,
    dominance_legend,
    dominance_segments,
    reference_driver,
    svg_image,
)

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


# Compass arrow per cardinal point, so wind direction reads at a glance
# (layout.md section 2 asks for an inline compass arrow).
_WIND_ARROWS = {"N": "↑", "NE": "↗", "E": "→", "SE": "↘", "S": "↓", "SW": "↙", "W": "←", "NW": "↖"}


def _wind_arrow(degrees) -> str:
    """Arrow glyph for a wind bearing, or empty when unknown."""
    cardinal = _cardinal(degrees)
    return _WIND_ARROWS.get(cardinal, "")


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


# FastF1 (and the SignalR WeatherData feed) report WindSpeed in m/s, but the
# header and the weather panel present km/h, which is what layout.md asks for.
MS_TO_KMH = 3.6


def wind_kmh(wind_speed) -> float | None:
    """Wind speed in km/h from the feed's m/s, or None when unavailable."""
    if wind_speed is None or pd.isna(wind_speed):
        return None
    return float(wind_speed) * MS_TO_KMH


def _is_snapshot(session_data: dict) -> bool:
    """Whether this dict is one moment of a replay (REPLAY-03), not a session."""
    return (session_data.get("session_info") or {}).get("replay_time") is not None


def _flag_state(session_data: dict) -> str:
    """The track's flag condition for the header.

    Race-control messages are scoped: a ``Sector`` yellow or a ``Driver`` blue
    says nothing about the state of the track, so only ``Track``-scoped
    messages count - otherwise a finished session reported "YELLOW FLAG"
    because some sector went yellow once.
    """
    if _is_snapshot(session_data):
        return flag_state(session_data)

    info = session_data.get("session_info") or {}
    status = info.get("track_status")
    if isinstance(status, dict):
        code = str(status.get("status", ""))
        if code in TRACK_STATUS_FLAGS:
            return TRACK_STATUS_FLAGS[code]

    if session_data.get("is_live"):
        # No TrackStatus yet: assume green rather than infer one from history.
        return "GREEN"

    race_control = session_data.get("race_control")
    if (
        race_control is not None
        and not race_control.empty
        and {"Flag", "Scope"} <= set(race_control.columns)
    ):
        track_wide = race_control[race_control["Scope"].astype(str).str.lower() == "track"]
        flags = track_wide["Flag"].dropna()
        for flag in reversed(flags.tolist()):
            state = str(flag).upper()
            if state in FLAG_STATES:
                return state
    return "FINISHED"


CLOCK_PLACEHOLDER = "--:--:--"


def _session_clock(session_data: dict) -> str:
    """What the header's clock slot should read.

    Historical: how long the session ran, from the session time at the last
    completed lap. Live: the time remaining on the feed's ExtrapolatedClock.
    It used to show the *weather sampling window*, which is neither.
    """
    info = session_data.get("session_info") or {}
    if session_data.get("is_live"):
        remaining = info.get("extrapolated_clock")
        return str(remaining) if remaining else CLOCK_PLACEHOLDER

    laps = session_data.get("laps")
    if laps is None or laps.empty or "Time" not in laps.columns:
        return CLOCK_PLACEHOLDER
    seconds = laps["Time"].map(to_seconds).dropna()
    if seconds.empty:
        return CLOCK_PLACEHOLDER
    return format_clock(float(seconds.max()))


def header_html(session_data: dict) -> str:
    """Global session + environment bar (spec section 2)."""
    info = session_data.get("session_info") or {}
    weather = session_data.get("weather")
    latest = (
        weather.iloc[-1] if weather is not None and not weather.empty else pd.Series(dtype="object")
    )

    flag = _flag_state(session_data)
    bg, fg, label = FLAG_STATES.get(flag, FLAG_STATES["FINISHED"])
    if _is_snapshot(session_data):
        clock_label, clock_value = race_clock_text(info)
    else:
        clock_label = "Remaining" if session_data.get("is_live") else "Duration"
        clock_value = _session_clock(session_data)
    lap_now, lap_total = info.get("current_lap"), info.get("total_laps")
    lap_text = (
        f'<span class="f1-env-label">Lap</span>'
        f'<span class="f1-clock f1-num">{lap_now}/{lap_total}</span>'
        if _is_snapshot(session_data) and lap_now and lap_total
        else ""
    )

    event = _esc(info.get("gp") or "Session")
    country = _esc(info.get("country") or "")
    year = info.get("year")
    session_type = _esc(info.get("session_name") or info.get("session_type") or "")

    def reading(label_text: str, value: str, extra: str = "") -> str:
        return (
            f'<div class="f1-env-item"><span class="f1-env-label">{label_text}</span>'
            f'<span class="f1-env-value {extra} f1-num">{value}</span></div>'
        )

    def number(key: str, unit: str, digits: int = 1) -> str:
        value = latest.get(key)
        if value is None or pd.isna(value):
            return "--"
        return f"{float(value):.{digits}f} {unit}"

    rain = latest.get("Rainfall")
    rain_yes = bool(rain) and not pd.isna(rain)
    wind_speed = wind_kmh(latest.get("WindSpeed"))
    direction = latest.get("WindDirection")
    wind = (
        f"{_wind_arrow(direction)} {wind_speed:.1f} km/h {_cardinal(direction)}".strip()
        if wind_speed is not None
        else "--"
    )

    # REPLAY-08: without the timing stream (an old replay, or FastF1 could
    # not provide it) race gaps are measured at the timing lines. Say so.
    estimated_note = (
        '<div class="f1-note">Gaps estimated at the timing lines</div>'
        if _is_snapshot(session_data) and info.get("gaps_estimated")
        else ""
    )
    return f"""
<div class="f1-header">
  <div class="f1-event">
    <span class="f1-event-name">{event}{f" {year}" if year else ""}</span>
    {f'<span class="f1-event-country">{country}</span>' if country else ""}
    <span class="f1-event-session">{session_type}</span>
  </div>
  <div style="display:flex;align-items:center;gap:12px;">
    {lap_text}
    <span class="f1-env-label">{clock_label}</span>
    <span class="f1-clock f1-num" title="{clock_label}">{clock_value}</span>
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
{estimated_note}"""


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
        # The number is the tyre's *age*, which exceeds the stint length when
        # the driver started on a scrubbed set.
        age = stint.get("laps_used", 0)
        fresh = stint.get("fresh")
        used_class = " used" if fresh is False else ""
        condition = {True: "new", False: "used", None: "condition unknown"}[fresh]
        stint_laps = stint.get("stint_laps")
        stint_note = f", {stint_laps} this stint" if stint_laps not in (None, age) else ""
        badges.append(
            f'<span class="f1-tyre{used_class}" style="border-color:{ring};color:{ring}" '
            f'title="{_esc(compound)} - {age} laps old ({condition}){stint_note}">'
            f"{letter}<em>{age}</em></span>"
        )
    return f'<div class="f1-tyres">{"".join(badges)}</div>'


# Badge -> CSS modifier. "+1L"-style badges (a lapped but classified finish)
# are matched by prefix below.
STATUS_CSS = {
    "IN PIT": "pit",
    "ON TRACK": "track",
    "KO": "ko",
    "CLASSIFIED": "track",
    "FIN": "track",
    "DNF": "out",
    "DSQ": "out",
    "DNS": "out",
    "OUT": "out",
}


def _status_html(status: str) -> str:
    css = STATUS_CSS.get(status)
    if css is None:
        css = "track" if status.startswith("+") and status.endswith("L") else "out"
    return f'<span class="f1-badge {css}">{_esc(status)}</span>'


def tower_html(rows: Sequence[dict]) -> str:
    """The driver leaderboard matrix (spec section 3)."""
    if not rows:
        return '<div style="padding:24px;color:#8a8a8a">No timing data for this session.</div>'

    head = "".join(f"<th>{_esc(c)}</th>" for c in TOWER_COLUMNS)
    body: list[str] = []

    for row in rows:
        # Only knock-out sessions carry a partition, and it names the segment
        # ("Eliminated in Q2") rather than an invented top-ten boundary.
        partition = row.get("partition")
        if partition:
            body.append(
                f'<tr><td colspan="{len(TOWER_COLUMNS)}" class="f1-split">'
                f"{_esc(partition)}</td></tr>"
            )

        accent = team_color(row.get("team_name"), row.get("team_colour"))
        best_class = "f1-time best" if row.get("is_overall_best") else "f1-time"
        last_class = "f1-time best" if row.get("last_is_session_best") else "f1-time"
        status = (
            "KO" if row.get("knocked_out") and row.get("status") == "CLASSIFIED" else row["status"]
        )
        speed = row.get("speed_kmh")
        speed_text = f"{speed:.0f} km/h" if speed is not None and pd.notna(speed) else "—"
        # Diff is measured against the session ideal; the driver's own ideal
        # lap is the other half of the picture (spec section 3.12).
        personal_ideal = row.get("personal_ideal")
        ideal_hint = (
            f"Personal ideal {format_lap(personal_ideal)}"
            if personal_ideal is not None
            else "No personal ideal lap yet"
        )

        sector_cells = "".join(
            f'<td><span class="f1-time f1-num">{_esc(s["display"])}</span>'
            f'{_segments_html(s["segments"])}</td>'
            for s in row["sectors"]
        )

        body.append(
            f'<tr class="{"ko" if row.get("knocked_out") else ""}">'
            f'<td class="f1-pos" style="--team:{accent}">{row["position"]}</td>'
            f'<td><div class="f1-code">{_esc(row["code"])}</div>'
            f'<div class="f1-team">{_esc(row.get("team_name"))}</div></td>'
            f"<td>{_status_html(status)}</td>"
            f'<td><span class="{last_class} f1-num">{_esc(row["last_lap"])}</span></td>'
            f'<td><span class="{best_class} f1-num">{_esc(row["best_lap"])}</span></td>'
            f'<td><span class="f1-time f1-num f1-dim">{_esc(row["interval"])}</span></td>'
            f'<td><span class="f1-time f1-num f1-dim">{_esc(row["gap"])}</span></td>'
            f"{sector_cells}"
            f"<td>{_tyres_html(row['tyre_history'])}</td>"
            f'<td><span class="f1-time f1-num f1-dim" title="{_esc(ideal_hint)}">'
            f'{_esc(row["diff"])}</span></td>'
            f'<td><span class="f1-time f1-num">{_esc(speed_text)}</span></td>'
            "</tr>"
        )

    return (
        '<div style="max-height:640px;overflow:auto;">'
        f'<table class="f1-tower"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def sector_cards_html(leaders: Sequence[Sequence[dict]]) -> str:
    """Sector top-3 widgets (spec section 5)."""

    def pill(entry: dict) -> str:
        colour = team_color(entry.get("team_name"), entry.get("team_colour"))
        return (
            f'<span class="f1-pill" style="background:{colour};color:{text_on(colour)}">'
            f'{_esc(entry["code"])}</span>'
        )

    cards = []
    for index, entries in enumerate(leaders, start=1):
        if entries:
            body = "".join(
                f'<div class="f1-sector-row"><span class="f1-rank">{e["rank"]}</span>'
                f"{pill(e)}"
                f'<span class="f1-sector-time f1-num">{_esc(e["time"])}</span></div>'
                for e in entries
            )
        else:
            body = '<div class="f1-sector-row"><span class="f1-dim">No data</span></div>'
        cards.append(
            f'<div class="f1-sector-card"><div class="f1-sector-head">Sector {index}</div>'
            f"{body}</div>"
        )
    return f'<div class="f1-sectors">{"".join(cards)}</div>'


def _driver_meta(rows: Sequence[dict]) -> dict[str, dict]:
    return {
        r["code"]: {"team_name": r.get("team_name"), "team_colour": r.get("team_colour")}
        for r in rows
    }


def _last_positions(location: dict[str, pd.DataFrame], rows: Sequence[dict]) -> list[dict]:
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


def info_time(session_data: dict) -> str:
    """The snapshot's moment as race time, for the map's alternative text."""
    info = session_data.get("session_info") or {}
    return format_clock(float(info.get("elapsed") or 0.0))


def _replay_markers(session_data: dict, rows: Sequence[dict]) -> list[dict]:
    """Every car where it was at the snapshot's moment."""
    meta = _driver_meta(rows)
    moment = float((session_data.get("session_info") or {}).get("replay_time") or 0.0)
    markers = positions_at(session_data.get("positions"), moment)
    for marker in markers:
        info = meta.get(marker["code"], {})
        marker["team_colour"] = team_color(info.get("team_name"), info.get("team_colour"))
    return markers


def map_panel_html(session_data: dict, rows: Sequence[dict]) -> str:
    """Track map with dominance colouring, corners and a benchmark overlay.

    A replay snapshot draws the outline, corners and every car at that
    moment instead: the dominance layer comes from fastest laps set later
    in the session.
    """
    telemetry, location = dashboard_frames(session_data)
    laps = session_data.get("laps")
    if _is_snapshot(session_data):
        svg = build_track_svg(
            location,
            circuit_info=session_data.get("circuit_info"),
            driver_meta=_driver_meta(rows),
            markers=_replay_markers(session_data, rows),
        )
        if svg is None:
            return (
                '<div style="padding:32px;color:#8a8a8a;text-align:center;">'
                "No GPS telemetry for this session, so the track map cannot be drawn."
                "</div>"
            )
        label = f"Track map with every car at {info_time(session_data)}"
        return f'<div class="f1-map-wrap">{svg_image(svg, label)}</div>'

    micro = {}
    for code, frame in telemetry.items():
        times = micro_sector_times(
            frame, sector_bounds=sector_bounds_for_driver(laps, str(code), frame)
        )
        if times is not None:
            micro[code] = times
    dominance = dominance_segments(micro)
    meta = _driver_meta(rows)

    # The outline comes from one driver's trace, so the slice boundaries are
    # that driver's real sectors - keeping the map aligned with the strips.
    outline_driver = reference_driver(location)
    segment_distances = (
        micro_sector_marks(
            sector_bounds_for_driver(laps, str(outline_driver), telemetry.get(outline_driver))
        )
        if outline_driver is not None
        else None
    )

    markers = _last_positions(location, rows) if session_data.get("is_live") else []
    svg = build_track_svg(
        location,
        circuit_info=session_data.get("circuit_info"),
        driver_meta=meta,
        dominance=dominance,
        markers=markers,
        segment_distances=segment_distances,
    )
    if svg is None:
        return (
            '<div style="padding:32px;color:#8a8a8a;text-align:center;">'
            "No GPS telemetry for this session, so the track map cannot be drawn."
            "</div>"
        )

    best = theoretical_best(rows)
    fastest = next((row for row in rows if row.get("is_overall_best")), rows[0] if rows else None)
    leader = fastest["best_lap"] if fastest else "—"
    ideal = (
        f'<div class="f1-bench-label" style="margin-top:4px">'
        f"Session ideal {format_lap(best)}</div>"
        if best is not None
        else ""
    )
    bench = (
        '<div class="f1-bench"><div class="f1-bench-label">Session best</div>'
        f'<div class="f1-bench-time f1-num">{_esc(leader)}</div>{ideal}</div>'
    )
    label = "Track map coloured by the fastest driver through each mini-sector"
    image = svg_image(svg, label)
    return f'<div class="f1-map-wrap">{bench}{image}</div>{dominance_legend(dominance, meta)}'


def render_dashboard(session_data: dict) -> None:
    """Render the full timing dashboard on the spec's 60/40 grid."""
    st.html(DASHBOARD_CSS)

    rows = build_timing_rows(session_data)
    st.html(f'<div class="f1-dash">{header_html(session_data)}</div>')

    left, right = st.columns([6, 4], gap="small")
    with left:
        st.html(f'<div class="f1-dash">{tower_html(rows)}</div>')
    with right:
        st.html(f'<div class="f1-dash">{sector_cards_html(sector_leaders(rows))}</div>')
        st.html(f'<div class="f1-dash">{map_panel_html(session_data, rows)}</div>')
