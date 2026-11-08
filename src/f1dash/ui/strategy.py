"""The Live page's Strategy tab: where a car rejoins if it pits now.

Rendering only. The maths is ``processing.pit_strategy``; the inputs are the
polled live snapshot the rest of the Live page draws from (``standings``,
``stints``, ``drivers``, ``pit_lane_times``), so this adds no connection and
no request. It runs inside the Live page's 3 s fragment: the driver and the
net pit loss are keyed widgets, so they survive every refresh.
"""

import html

import pandas as pd
import streamlit as st

from f1dash.processing.pit_loss import DEFAULT_NET_PIT_LOSS, NET_PIT_LOSS_NOTE
from f1dash.processing.pit_strategy import (
    OK,
    UNCERTAIN,
    UNSUPPORTED,
    DriverSummary,
    RejoinPrediction,
    driver_summary,
    loss_range,
    loss_scenarios,
    net_loss_check,
    observed_pit_lane_median,
    race_order,
    stale_warning,
)
from f1dash.processing.timing import LEADER, MISSING, format_delta, format_lap_gap, is_race_session
from f1dash.ui.dashboard import track_flag, tyre_badge_html
from f1dash.ui.layout import freshness_caption
from f1dash.ui.status import DataStatus, show
from f1dash.ui.theme import team_color

# Rows of the projected order shown either side of the car that pits.
WINDOW = 4


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _seconds(value: float | None) -> str:
    return MISSING if value is None else f"{value:.3f}"


def data_age_seconds(last_heartbeat, now: pd.Timestamp | None = None) -> float | None:
    """Seconds since the feed's last heartbeat, or ``None`` before the first one."""
    moment = pd.to_datetime(last_heartbeat, utc=True, errors="coerce")
    if moment is None or pd.isna(moment):
        return None
    current = now if now is not None else pd.Timestamp.now(tz="UTC")
    return max((current - moment).total_seconds(), 0.0)


def _gap_text(summary: DriverSummary) -> str:
    if summary.position == 1:
        return LEADER
    if summary.laps_down:
        return format_lap_gap(summary.laps_down)
    return format_delta(summary.gap) if summary.gap is not None else MISSING


def _neighbour_text(code: str | None, seconds: float | None, side: str) -> str:
    if code is None:
        return f"Nobody {side}"
    return f"{code} {format_delta(seconds) if seconds is not None else MISSING}"


def summary_html(summary: DriverSummary) -> str:
    """The selected car now: position, tyre, gaps (PRD FR-01)."""
    number = f" #{summary.number}" if summary.number else ""
    tyre = tyre_badge_html(summary.compound, summary.tyre_age, summary.tyre_new)
    pits = MISSING if summary.pits is None else str(summary.pits)
    rows = [
        ("Driver", f'<span class="f1-code">{_esc(summary.driver)}</span>{_esc(number)}'),
        ("Position", f'<span class="f1-num">P{summary.position}</span>'),
        ("Tyre", tyre),
        ("Stops", f'<span class="f1-num">{_esc(pits)}</span>'),
        ("Gap to leader", f'<span class="f1-num">{_esc(_gap_text(summary))}</span>'),
        ("Car ahead", _esc(_neighbour_text(summary.ahead, summary.to_ahead, "ahead"))),
        ("Car behind", _esc(_neighbour_text(summary.behind, summary.to_behind, "behind"))),
    ]
    body = "".join(f"<tr><th>{_esc(label)}</th><td>{value}</td></tr>" for label, value in rows)
    return f'<div class="f1-dash"><table class="f1-kv">{body}</table></div>'


def headline_html(prediction: RejoinPrediction) -> str:
    """``NOW P3 · AFTER THE STOP P5 · PLACES -2``."""
    after = _place(prediction)
    lost = prediction.places_lost
    change = MISSING if lost is None else ("no change" if lost == 0 else f"{-lost:+d}")
    items = [
        ("Now", f"P{prediction.current_position}" if prediction.current_position else MISSING),
        ("After the stop", after),
        ("Places", change),
        ("Net pit loss (assumed)", f"{prediction.net_pit_loss:.1f} s"),
    ]
    cells = "".join(
        f'<div class="f1-rejoin-item"><span class="f1-env-label">{_esc(label)}</span>'
        f'<span class="f1-rejoin-value">{_esc(value)}</span></div>'
        for label, value in items
    )
    return f'<div class="f1-dash"><div class="f1-rejoin">{cells}</div></div>'


def _place(prediction: RejoinPrediction) -> str:
    if prediction.position is not None:
        return f"P{prediction.position}"
    if prediction.position_range is not None:
        return f"P{prediction.position_range[0]}–P{prediction.position_range[1]}"
    return MISSING


def margin_lines(prediction: RejoinPrediction) -> list[str]:
    """How far the net pit loss can move before the predicted place changes."""
    if prediction.position is None:
        return []
    place = f"P{prediction.position}"
    lines = []
    if prediction.loss_to_drop is not None:
        lines.append(
            f"{prediction.loss_to_drop:.3f} s more net pit loss and {prediction.drop_to} is ahead: "
            f"{place} is lost at that margin, since a tie goes to the car already there."
        )
    elif not prediction.behind:
        lines.append(f"No car behind: {place} is the last place, whatever the loss.")
    else:
        lines.append(
            f"The car behind has no timed gap, so the margin to losing {place} is unknown."
        )
    if prediction.loss_to_gain is not None:
        lines.append(
            f"More than {prediction.loss_to_gain:.3f} s less net pit loss passes "
            f"{prediction.gain_on} for P{prediction.position - 1}."
        )
    elif prediction.position == 1:
        lines.append("Already in the lead after the stop.")
    elif prediction.ahead and prediction.ahead[0].gap is None:
        lines.append("The car ahead has no timed gap, so the margin to gaining a place is unknown.")
    else:
        lines.append(
            f"Even a zero-second stop does not pass {prediction.ahead[0].driver}."
            if prediction.ahead
            else ""
        )
    return [line for line in lines if line]


def scenarios_html(scenarios: list[RejoinPrediction]) -> str:
    """Rejoin place for a quicker, the chosen and a slower stop (scenarios, not a confidence interval)."""
    cells = "".join(
        f'<div class="f1-rejoin-item"><span class="f1-env-label">'
        f"{_esc(f'{s.net_pit_loss:.1f} s net')}</span>"
        f'<span class="f1-rejoin-value">{_esc(_place(s))}</span></div>'
        for s in scenarios
    )
    return f'<div class="f1-dash"><div class="f1-rejoin">{cells}</div></div>'


def order_html(
    prediction: RejoinPrediction,
    current: dict[str, int],
    colours: dict[str, str],
    window: int = WINDOW,
) -> str:
    """The projected order around the rejoin point, the car that pits outlined (PRD FR-04)."""
    rows = list(prediction.projected_order)
    me = next((i for i, n in enumerate(rows) if n.driver == prediction.driver), None)
    if me is None:
        return ""
    shown = rows[max(me - window, 0) : me + window + 1]
    head = "".join(
        f"<th>{label}</th>"
        for label in ("Pos", "Driver", "Now", "Gap to leader", f"To {_esc(prediction.driver)}")
    )
    body = []
    for row in shown:
        is_me = row.driver == prediction.driver
        colour = colours.get(row.driver, "var(--line)")
        now = current.get(row.driver)
        if is_me:
            delta = "PITS"
        elif row.delta is None:
            delta = MISSING
        else:
            delta = f"{row.delta:+.3f}"
        badge = '<span class="f1-badge pit">PIT</span>' if row.status == "IN PIT" else ""
        if is_me:
            badge = ""
        body.append(
            f'<tr class="{"focus" if is_me else ""}">'
            f'<td class="f1-pos" style="--team:{colour}">{row.position}</td>'
            f'<td><span class="f1-code">{_esc(row.driver)}</span> {badge}</td>'
            f'<td class="f1-num f1-dim">{"P" + str(now) if now else MISSING}</td>'
            f'<td class="f1-num f1-dim">{_esc(_seconds(row.gap))}</td>'
            f'<td class="f1-num">{_esc(delta)}</td></tr>'
        )
    return (
        '<div class="f1-dash"><table class="f1-tower">'
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def _colours(drivers: pd.DataFrame | None) -> dict[str, str]:
    if drivers is None or drivers.empty or "name_acronym" not in drivers:
        return {}
    return {
        str(row.get("name_acronym")): team_color(row.get("team_name"), row.get("team_colour"))
        for row in drivers.to_dict("records")
    }


def observed_lane_text(median: float | None, stops: int) -> str:
    """The observed pit lane time, kept apart from the net-loss assumption."""
    if median is None:
        timed = f"{stops} stop(s) timed so far, 3 needed" if stops else "no stops timed yet"
        return (
            f"Observed: {timed}. Pit lane times are counted from when this server "
            "connected to the feed."
        )
    return (
        f"Observed: median pit lane time {median:.1f} s over {stops} stops in this race "
        "(pit entry to pit exit). Reference only: it is not the net loss and is not used, "
        "because a car that stays out also needs time to cover that stretch."
    )


def _set_state(key: str, value) -> None:
    """Button callback: runs before the next script run, so a widget's key may be set."""
    st.session_state[key] = value


def _label(code: str, positions: dict[str, int]) -> str:
    return f"P{positions[code]}  {code}" if code in positions else code


def render_pit_strategy(snapshot: dict, key: str = "strategy") -> None:
    """Pick a car and a net pit loss; see where it would rejoin from the current order."""
    info = snapshot.get("session_info") or {}
    session = info.get("session_key") or info.get("gp") or "live"
    standings = snapshot.get("standings")
    order = race_order(standings if isinstance(standings, pd.DataFrame) else None)
    if not is_race_session(info):
        show(DataStatus.unavailable("Pit rejoin predictions need a race or sprint"))
        return
    if not order:
        show(DataStatus.unavailable("No timing order yet, so there is nothing to rejoin into"))
        return

    positions = {entry.driver: entry.position for entry in order}
    codes = [entry.driver for entry in order]
    driver_key = f"{key}_driver:{session}"
    if st.session_state.get(driver_key) not in codes:
        st.session_state.pop(driver_key, None)  # a car that left the timing data

    lane_median, lane_stops = observed_pit_lane_median(snapshot.get("pit_lane_times"))
    low, high = loss_range(DEFAULT_NET_PIT_LOSS)
    loss_key = f"{key}_net_loss:{session}"
    if loss_key not in st.session_state:
        st.session_state[loss_key] = DEFAULT_NET_PIT_LOSS
    st.session_state[loss_key] = min(max(float(st.session_state[loss_key]), low), high)

    left, right = st.columns([2, 3], gap="medium")
    with left:
        driver = st.selectbox(
            "Driver",
            codes,
            key=driver_key,
            format_func=lambda code: _label(code, positions),
        )
        summary = driver_summary(standings, driver, snapshot.get("stints"), snapshot.get("drivers"))
        if summary is not None:
            st.html(summary_html(summary))
        loss = st.slider(
            "Estimated net pit loss (s)",
            min_value=low,
            max_value=high,
            step=0.5,
            format="%.1f",
            key=loss_key,
            help=NET_PIT_LOSS_NOTE,
        )
        st.button(
            f"Reset to {DEFAULT_NET_PIT_LOSS:.1f} s",
            key=f"{key}_reset:{session}",
            on_click=_set_state,
            args=(loss_key, DEFAULT_NET_PIT_LOSS),
        )
        st.caption(f"Assumption. {NET_PIT_LOSS_NOTE}")
        st.caption(observed_lane_text(lane_median, lane_stops))

    age = data_age_seconds(info.get("last_heartbeat"))
    quicker, prediction, slower = loss_scenarios(
        standings, driver, loss, flag=track_flag(snapshot), data_age_s=age
    )
    with right:
        st.caption(freshness_caption(info.get("last_heartbeat")))
        if prediction.status in (OK, UNCERTAIN):
            st.html(headline_html(prediction))
            for line in margin_lines(prediction):
                st.caption(line)
            st.html(order_html(prediction, positions, _colours(snapshot.get("drivers"))))
            st.markdown("**Scenarios**")
            st.html(scenarios_html([quicker, prediction, slower]))
            st.caption(
                "The same calculation for a stop 2 s quicker and 2 s slower: scenarios, "
                "not a confidence interval."
            )
            for warning in prediction.warnings:
                st.warning(warning)
            check = net_loss_check(loss, lane_median)
            if check:
                st.warning(check)
        elif prediction.status == UNSUPPORTED:
            st.info(prediction.reason.rstrip(".") + ".")
        else:
            show(DataStatus.unavailable(prediction.reason))
        stale = stale_warning(age)
        if stale and prediction.status not in (OK, UNCERTAIN):
            st.warning(stale)  # otherwise it is the first of the prediction's warnings
        st.caption(
            "An estimate, not a forecast. "
            + " ".join(prediction.assumptions)
            + " Neutralised running (SC, VSC, red flag) is not modelled."
        )
