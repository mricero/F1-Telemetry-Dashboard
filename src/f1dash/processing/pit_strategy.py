"""Live pit strategy: where a car rejoins if it pits now (PRD "Live Pit Strategy" V1).

Pure transforms over a ``standings`` frame (the live timing screen, or a replay
snapshot's; both carry ``Driver, Position, GapSeconds, LapsDown,
IntervalSeconds, Status``). No Streamlit, no network.

The order is put on one time axis, seconds behind the leader at the timing
line. ``GapToLeader`` gives it for cars on the lead lap; a lapped car's gap
reads ``1 L``, but its ``IntervalToPositionAhead`` is still in seconds, so the
axis continues by adding intervals down the order. Where neither is known the
car's place on the axis is unknown, and the prediction says how many places
that leaves open instead of guessing.

A stop adds ``net_pit_loss`` seconds to the car's own time: the *net* loss,
the pit lane duration minus the time the same stretch takes on track, never
the pit lane duration itself (``processing.pit_loss`` defines both). The feed
only reports pit lane durations, so the net loss is the viewer's assumption.
Everyone else is assumed to keep their current gap (unchanged relative pace
over the stop, no one else pitting). Under a safety car, a VSC or a red flag a stop costs a
different, unmodelled amount, so those are marked unsupported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise

import pandas as pd

from f1dash.processing.pit_loss import MAX_STOP_S, MIN_STOP_S, MIN_STOPS_FOR_MEDIAN

OK = "ok"
UNCERTAIN = "uncertain"
UNAVAILABLE = "unavailable"
UNSUPPORTED = "unsupported"

# Net-pit-loss slider range: the PRD's 15-35 s, widened to include the value.
LOSS_MIN_S = 15.0
LOSS_MAX_S = 35.0

# A rival within this many seconds of the rejoin point could swap places with
# a slow stop or a quick out-lap: the position is shown, flagged as close.
CLOSE_MARGIN_S = 1.0
# Gaps update per car as each crosses a timing point; out-of-order gaps by
# more than this mean the screen is mid-update.
ORDER_TOLERANCE_S = 0.5
# Matches the Live page's freshness caption (ui.layout.STALE_AFTER_S).
STALE_AFTER_S = 30.0

# Flag states (ui.theme.FLAG_STATES keys) under which a green-flag pit loss
# does not apply, and the sentence saying so.
NEUTRALISED = {
    "SAFETY CAR": "A safety car is out: a stop costs far less than under green, "
    "and V1 does not model it",
    "VSC": "A virtual safety car is out: a stop costs less than under green, "
    "and V1 does not model it",
    "RED": "The race is red-flagged: tyres can be changed in the pit lane without losing time",
}
FINISHED = {"CHEQUERED", "FINISHED"}
STOPPED_STATUSES = {"OUT", "DNF", "DNS", "DSQ", "FIN", "KO"}
PIT_STATUS = "IN PIT"

ASSUMPTIONS = (
    "Every other car keeps its current gap to the leader: unchanged relative pace "
    "through the stop, and no one else pits.",
    "The net pit loss is the value set on the slider: an assumption, not a "
    "measurement. The observed pit lane time is shown beside it for reference "
    "and is not used, since a car also spends time on that stretch when it stays out.",
    "Positions are race classification by time behind the leader, measured at "
    "the timing line; a car that rejoins next to a lapped car is not in a fight with it.",
)


@dataclass(frozen=True)
class OrderEntry:
    """One car on the time axis."""

    driver: str
    position: int
    gap: float | None  # seconds behind the leader, None when not determinable
    status: str = "ON TRACK"
    laps_down: int = 0
    # The interval chain that placed a lapped car, or "gap" from GapToLeader.
    source: str = "gap"


@dataclass(frozen=True)
class Neighbour:
    """A car in the projected order: ``delta`` > 0 means behind the car that pitted.

    The car that pitted is in ``projected_order`` too, with status ``IN PIT``
    and ``delta`` 0.
    """

    driver: str
    position: int  # projected position after the stop
    gap: float | None  # None for a car the timing screen gives no gap for
    delta: float | None
    status: str = "ON TRACK"


@dataclass(frozen=True)
class RejoinPrediction:
    driver: str
    status: str  # OK, UNCERTAIN, UNAVAILABLE or UNSUPPORTED
    net_pit_loss: float
    reason: str = ""  # why there is no prediction (UNAVAILABLE / UNSUPPORTED)
    current_position: int | None = None
    current_gap: float | None = None
    rejoin_gap: float | None = None
    position: int | None = None
    # When unknown gaps leave the place open: the best and worst position.
    position_range: tuple[int, int] | None = None
    ahead: tuple[Neighbour, ...] = ()  # nearest first
    behind: tuple[Neighbour, ...] = ()  # nearest first
    projected_order: tuple[Neighbour, ...] = ()  # everyone with a known place, in order
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = field(default=ASSUMPTIONS)
    # Net-pit-loss margins (seconds). ``loss_to_drop``: this much more loss and the
    # car behind is ahead (at exactly this much it is a tie, which the car
    # already there wins). ``loss_to_gain``: the loss must fall by *more* than
    # this to pass the car ahead. None at the ends of the order, when that
    # neighbour has no timed gap, when the place is a range, or when gaining
    # would need a negative loss.
    loss_to_drop: float | None = None
    loss_to_gain: float | None = None
    drop_to: str | None = None  # the car that would pass
    gain_on: str | None = None  # the car that would be passed

    @property
    def places_lost(self) -> int | None:
        if self.position is None or self.current_position is None:
            return None
        return self.position - self.current_position


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def race_order(standings: pd.DataFrame | None) -> list[OrderEntry]:
    """Every car in classification order with its time behind the leader.

    Cars that have stopped (``OUT`` and the like) come last with no gap: they
    are not in the running order a stop can drop someone into. A car in the pit
    lane keeps its place in the order but no gap (``source == "pit"``).
    """
    if standings is None or standings.empty or not {"Driver", "Position"} <= set(standings):
        return []
    frame = standings.copy()
    frame["Position"] = pd.to_numeric(frame["Position"], errors="coerce")
    frame = frame.dropna(subset=["Position"]).sort_values("Position", kind="stable")
    running: list[OrderEntry] = []
    stopped: list[OrderEntry] = []
    previous: float | None = None
    for row in frame.to_dict("records"):
        status = str(row.get("Status") or "ON TRACK").upper()
        laps_down = int(_number(row.get("LapsDown")) or 0)
        code, position = str(row["Driver"]), int(row["Position"])
        if status in STOPPED_STATUSES:
            stopped.append(OrderEntry(code, position, None, status, laps_down, "stopped"))
            continue
        gap = 0.0 if not running else _number(row.get("GapSeconds"))
        source = "gap"
        if running and status == PIT_STATUS:
            # Mid-stop, the screen's gap lags (seen live: a car in the pit lane
            # still showing its pre-stop gap). Its place is only bounded.
            gap, source = None, "pit"
        elif gap is None:
            interval = _number(row.get("IntervalSeconds"))
            if previous is not None and interval is not None:
                gap, source = round(previous + interval, 3), "interval"
            else:
                source = "unknown"
        running.append(OrderEntry(code, position, gap, status, laps_down, source))
        previous = gap
    return running + stopped


def stale_warning(data_age_s: float | None) -> str | None:
    """The Live page's freshness wording past its 30 s threshold, else ``None``."""
    if data_age_s is None or data_age_s <= STALE_AFTER_S:
        return None
    return (
        f"No update for {int(data_age_s)} s: the order and gaps are from then, "
        "so this is where the car would have rejoined then, not now"
    )


def _bounds(running: list[OrderEntry], index: int) -> tuple[float, float]:
    """Where an unplaced car's gap must lie: between its known neighbours in the order."""
    low = next((e.gap for e in reversed(running[:index]) if e.gap is not None), 0.0)
    high = next((e.gap for e in running[index + 1 :] if e.gap is not None), math.inf)
    return low, high


def predict_pit_rejoin(
    standings: pd.DataFrame | None,
    driver: str,
    net_pit_loss: float,
    *,
    flag: str = "GREEN",
    race: bool = True,
    data_age_s: float | None = None,
    nearby: int = 2,
) -> RejoinPrediction:
    """Where ``driver`` rejoins after a stop costing ``net_pit_loss`` seconds, from now.

    ``flag`` is the track's flag state (``ui.theme.FLAG_STATES`` key);
    ``data_age_s`` is how old the timing is, for the staleness warning.
    A larger loss never gives a better position (the property tests check it).
    """
    loss = _number(net_pit_loss)
    if loss is None or loss < 0:
        return RejoinPrediction(
            driver, UNAVAILABLE, float("nan"), "The net pit loss is not a number"
        )

    def no(status: str, reason: str, **extra) -> RejoinPrediction:
        return RejoinPrediction(driver, status, loss, reason, **extra)

    if not race:
        return no(UNSUPPORTED, "Pit rejoin predictions need a race or sprint")
    order = race_order(standings)
    if not order:
        return no(UNAVAILABLE, "No timing data yet, so there is no order to rejoin into")
    me = next((e for e in order if e.driver == driver), None)
    if me is None:
        return no(UNAVAILABLE, f"{driver} is not in the timing data")
    current = {"current_position": me.position, "current_gap": me.gap}
    flag = str(flag or "GREEN").upper()
    if flag in FINISHED:
        return no(UNAVAILABLE, "The race has finished", **current)
    if flag in NEUTRALISED:
        return no(UNSUPPORTED, NEUTRALISED[flag], **current)
    if me.status in STOPPED_STATUSES:
        return no(UNAVAILABLE, f"{driver} is out of the race", **current)
    if me.status == PIT_STATUS:
        return no(
            UNAVAILABLE,
            f"{driver} is in the pit lane now; their gap is mid-stop and will settle "
            "after the out-lap",
            **current,
        )
    if me.gap is None:
        return no(
            UNAVAILABLE,
            f"The timing screen has no gap for {driver} yet (a gap, or an interval to a car "
            "with one), so the rejoin point is unknown",
            **current,
        )

    rejoin = me.gap + loss
    running = [e for e in order if e.status not in STOPPED_STATUSES and e.driver != driver]
    warnings: list[str] = []

    placed_ahead = [e for e in running if e.gap is not None and e.gap <= rejoin]
    placed_behind = [e for e in running if e.gap is not None and e.gap > rejoin]
    open_places = 0
    for index, entry in enumerate(running):
        if entry.gap is not None:
            continue
        low, high = _bounds(running, index)
        if high <= rejoin:
            placed_ahead.append(entry)
        elif low > rejoin:
            placed_behind.append(entry)
        else:
            open_places += 1

    best = len(placed_ahead) + 1
    position: int | None = best
    position_range = None
    status = OK
    if open_places:
        position = None
        position_range = (best, best + open_places)
        status = UNCERTAIN
        warnings.append(
            f"{open_places} car(s) around the rejoin point have no gap or interval on the "
            f"timing screen, so {driver} could rejoin anywhere from P{best} to "
            f"P{best + open_places}"
        )

    # Every running car in one list. An unplaced car sorts just after its last
    # known neighbour, which keeps the classification order; its gap stays None.
    projected: list[tuple[float, int, str, float | None, str]] = []
    for index, entry in enumerate(running):
        key = entry.gap if entry.gap is not None else _bounds(running, index)[0] + 1e-6 * index
        projected.append((key, 0, entry.driver, entry.gap, entry.status))
    # Ties go to the car already there (rank 0): it is ahead on the road.
    projected.append((rejoin, 1, driver, rejoin, PIT_STATUS))
    projected.sort(key=lambda item: (item[0], item[1]))
    me_index = next(i for i, item in enumerate(projected) if item[1] == 1)

    def neighbour(index: int) -> Neighbour:
        _, _, code, gap, state = projected[index]
        delta = None if gap is None else round(gap - rejoin, 3)
        return Neighbour(code, index + 1, None if gap is None else round(gap, 3), delta, state)

    ahead = tuple(neighbour(i) for i in range(me_index - 1, max(me_index - 1 - nearby, -1), -1))
    behind = tuple(
        neighbour(i) for i in range(me_index + 1, min(me_index + 1 + nearby, len(projected)))
    )
    projected_order = tuple(neighbour(i) for i in range(len(projected)))

    for rival in ahead[:1] + behind[:1]:
        if rival.delta is not None and abs(rival.delta) < CLOSE_MARGIN_S and status == OK:
            status = UNCERTAIN
            side = "ahead of" if rival.delta > 0 else "behind"
            warnings.append(
                f"Too close to call: {driver} would rejoin {abs(rival.delta):.1f} s {side} "
                f"{rival.driver}; a slow stop or a quick out-lap swaps them"
            )
    near = {n.driver for n in ahead + behind}
    in_pit = [e.driver for e in running if e.source == "pit" and e.driver in near]
    if in_pit:
        warnings.append(
            f"{', '.join(in_pit)} {'is' if len(in_pit) == 1 else 'are'} in the pit lane now, "
            "so placed by classification only: the gap on the screen is mid-stop"
        )
    gaps = [e.gap for e in running if e.gap is not None and e.source == "gap"]
    if any(b < a - ORDER_TOLERANCE_S for a, b in pairwise(gaps)):
        status = UNCERTAIN
        warnings.append(
            "Some gaps on the timing screen are out of order (updates still arriving), "
            "so the order is approximate"
        )
    stale = stale_warning(data_age_s)
    if stale:
        status = UNCERTAIN
        warnings.insert(0, stale)
    chained = {e.driver for e in running if e.source == "interval"}
    if me.source == "interval" or chained & {n.driver for n in ahead + behind}:
        warnings.append(
            "Lapped cars are placed by adding intervals down the order, since their gap to "
            "the leader reads in laps"
        )

    to_drop, drop_to, to_gain, gain_on = _margins(position, rejoin, loss, ahead, behind)
    return RejoinPrediction(
        driver=driver,
        status=status,
        net_pit_loss=loss,
        loss_to_drop=to_drop,
        drop_to=drop_to,
        loss_to_gain=to_gain,
        gain_on=gain_on,
        current_position=me.position,
        current_gap=round(me.gap, 3),
        rejoin_gap=round(rejoin, 3),
        position=position,
        position_range=position_range,
        ahead=ahead,
        behind=behind,
        projected_order=projected_order,
        warnings=tuple(warnings),
    )


def _margins(
    position: int | None,
    rejoin: float,
    loss: float,
    ahead: tuple[Neighbour, ...],
    behind: tuple[Neighbour, ...],
) -> tuple[float | None, str | None, float | None, str | None]:
    """How far the net pit loss can move before the predicted place changes.

    Ties go to the car already there, so the place is lost at exactly
    ``loss_to_drop`` more, and gained only past ``loss_to_gain`` less.
    """
    if position is None:
        return None, None, None, None
    to_drop = drop_to = to_gain = gain_on = None
    if behind and behind[0].gap is not None:
        to_drop, drop_to = round(behind[0].gap - rejoin, 3), behind[0].driver
    if ahead and ahead[0].gap is not None:
        needed = round(rejoin - ahead[0].gap, 3)
        if needed < loss:  # the loss cannot go below zero
            to_gain, gain_on = needed, ahead[0].driver
    return to_drop, drop_to, to_gain, gain_on


SCENARIO_STEP_S = 2.0


def loss_scenarios(
    standings: pd.DataFrame | None,
    driver: str,
    net_pit_loss: float,
    step: float = SCENARIO_STEP_S,
    *,
    flag: str = "GREEN",
    race: bool = True,
    data_age_s: float | None = None,
) -> list[RejoinPrediction]:
    """The same prediction at ``net_pit_loss - step``, ``net_pit_loss`` and ``net_pit_loss + step``.

    Scenarios for a quicker or slower stop, not a confidence interval. The
    lower one stops at zero loss; the options are :func:`predict_pit_rejoin`'s.
    """
    losses = (max(float(net_pit_loss) - step, 0.0), float(net_pit_loss), float(net_pit_loss) + step)
    return [
        predict_pit_rejoin(standings, driver, value, flag=flag, race=race, data_age_s=data_age_s)
        for value in losses
    ]


def live_pit_stops(pit_lane_times: pd.DataFrame | None) -> list[float]:
    """Pit lane durations a stop could plausibly cost (a drive-through or a repair is not one)."""
    if pit_lane_times is None or pit_lane_times.empty or "Duration" not in pit_lane_times:
        return []
    durations = pd.to_numeric(pit_lane_times["Duration"], errors="coerce").dropna()
    return [float(d) for d in durations if MIN_STOP_S <= d <= MAX_STOP_S]


def observed_pit_lane_median(pit_lane_times: pd.DataFrame | None) -> tuple[float | None, int]:
    """``(median seconds, stops)`` of the pit lane durations announced in this race.

    Observed pit lane time, entry to exit: shown for reference, **not** a net
    pit loss and never used as one. The median is ``None`` below three stops.
    """
    stops = live_pit_stops(pit_lane_times)
    if len(stops) < MIN_STOPS_FOR_MEDIAN:
        return None, len(stops)
    return round(float(pd.Series(stops).median()), 1), len(stops)


def net_loss_check(net_pit_loss: float, pit_lane_median: float | None) -> str | None:
    """A warning when the net loss is set at or above the observed pit lane time.

    The net loss is the pit lane time minus the time the same stretch takes on
    track, so it should be lower; a value at or above it double-counts.
    """
    if pit_lane_median is None or net_pit_loss < pit_lane_median:
        return None
    return (
        f"The net pit loss ({net_pit_loss:.1f} s) is set at or above the observed pit lane "
        f"time ({pit_lane_median:.1f} s). The net loss should be lower: it leaves out the "
        "time a car takes over the same stretch when it stays out"
    )


def loss_range(value: float) -> tuple[float, float]:
    """The net-pit-loss slider's bounds: 15-35 s, widened to keep the value inside with room."""
    return (
        float(min(LOSS_MIN_S, math.floor(value - 5.0))),
        float(max(LOSS_MAX_S, math.ceil(value + 5.0))),
    )


@dataclass(frozen=True)
class DriverSummary:
    """What the strategy panel shows about the selected car now (PRD FR-01)."""

    driver: str
    number: str | None
    position: int
    status: str
    gap: float | None  # to the leader, seconds (lapped cars via the interval chain)
    laps_down: int
    ahead: str | None
    to_ahead: float | None
    behind: str | None
    to_behind: float | None
    compound: str | None = None
    tyre_age: int | None = None
    tyre_new: bool | None = None
    pits: int | None = None
    team_name: str | None = None
    team_colour: str | None = None


def _last_stint(stints: pd.DataFrame | None, driver: str) -> dict:
    if stints is None or stints.empty or "Driver" not in stints:
        return {}
    rows = stints[stints["Driver"].astype(str) == driver]
    if rows.empty:
        return {}
    if "Stint" in rows:
        rows = rows.sort_values("Stint", kind="stable")
    return rows.iloc[-1].to_dict()


def driver_summary(
    standings: pd.DataFrame | None,
    driver: str,
    stints: pd.DataFrame | None = None,
    drivers: pd.DataFrame | None = None,
) -> DriverSummary | None:
    """Position, gaps either side and current tyre of ``driver``, or ``None`` if not timed."""
    order = race_order(standings)
    running = [e for e in order if e.status not in STOPPED_STATUSES]
    me = next((e for e in order if e.driver == driver), None)
    if me is None:
        return None
    index = next((i for i, e in enumerate(running) if e.driver == driver), None)
    ahead = running[index - 1] if index else None
    behind = running[index + 1] if index is not None and index + 1 < len(running) else None

    def between(front: OrderEntry | None, back: OrderEntry | None) -> float | None:
        if front is None or back is None or front.gap is None or back.gap is None:
            return None
        return round(back.gap - front.gap, 3)

    stint = _last_stint(stints, driver)
    age = _number(stint.get("TyreAge"))
    new = stint.get("New")
    number = team_name = team_colour = None
    if drivers is not None and not drivers.empty and "name_acronym" in drivers:
        match = drivers[drivers["name_acronym"].astype(str) == driver]
        if not match.empty:
            first = match.iloc[0]
            number = str(first.get("driver_number")) if first.get("driver_number") else None
            team_name = first.get("team_name") or None
            team_colour = first.get("team_colour") or None
    pits = None
    if standings is not None and "Pits" in standings and "Driver" in standings:
        value = standings.loc[standings["Driver"].astype(str) == driver, "Pits"]
        pits = (
            int(value.iloc[0]) if not value.empty and _number(value.iloc[0]) is not None else None
        )
    return DriverSummary(
        driver=driver,
        number=number,
        position=me.position,
        status=me.status,
        gap=me.gap,
        laps_down=me.laps_down,
        ahead=ahead.driver if ahead else None,
        to_ahead=between(ahead, me),
        behind=behind.driver if behind else None,
        to_behind=between(me, behind),
        compound=str(stint["Compound"]).upper() if stint.get("Compound") else None,
        tyre_age=None if age is None else int(age),
        tyre_new=new if isinstance(new, bool) else None,
        pits=pits,
        team_name=team_name,
        team_colour=team_colour,
    )
