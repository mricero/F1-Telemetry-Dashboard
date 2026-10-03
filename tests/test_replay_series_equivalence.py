"""The vectorised change-point builders give the series the scalar ones gave (REPLAY-28).

``tower_series`` spent most of its time in one ``pd.isna`` per stream value.
The faster code must not change a single point, so this module keeps the
pre-REPLAY-28 implementations verbatim as the oracle: each session is built
once with them patched into :mod:`processing.replay_model` and once with the
current code, and every field of every driver must match - times, values and
the values' Python types.
"""

import numpy as np
import pandas as pd
import pytest

import f1dash.processing.replay_model as model
from f1dash.processing.replay_model import (
    LEADER,
    MISSING,
    POSITION_SETTLE_SECONDS,
    _gap_display,
    format_lap_gap,
    is_leader_cell,
    tower_series,
)
from tests import replay_fixtures as fx
from tests.test_replay_model import _big_race, _red_flag_race

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# --------------------------------------------------------------------------
# The oracle: the implementations as they were before REPLAY-28.
# --------------------------------------------------------------------------


def _old_clean(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    if isinstance(value, np.generic):
        return value.item()
    return value


def _old_series(points):
    ordered = sorted(
        ((float(t), _old_clean(v)) for t, v in points if t is not None and not pd.isna(t)),
        key=lambda item: item[0],
    )
    times: list[float] = []
    values: list = []
    for moment, value in ordered:
        if times and moment == times[-1]:
            values[-1] = value
            if len(values) > 1 and values[-1] == values[-2]:
                times.pop()
                values.pop()
            continue
        if values and value == values[-1]:
            continue
        times.append(moment)
        values.append(value)
    return model.FieldSeries(np.asarray(times, dtype=float), tuple(values))


def _old_combine(first, second, merge, defaults=(None, None)):
    moments = sorted(set(first.t.tolist()) | set(second.t.tolist()))
    return _old_series(
        (m, merge(first.at(m, defaults[0]), second.at(m, defaults[1]))) for m in moments
    )


def _old_settled_positions(rows):
    times = rows["Time"].to_numpy(float)
    values = rows["Position"].tolist()
    points = []
    for index, (moment, value) in enumerate(zip(times, values, strict=True)):
        if value is None or pd.isna(value):
            continue
        following = times[index + 1] if index + 1 < len(times) else np.inf
        if following - moment >= POSITION_SETTLE_SECONDS:
            points.append((moment + POSITION_SETTLE_SECONDS, int(value)))
    return points


def _old_stream_display(position, times, seconds, laps, raw):
    lapform = [str(value or "").upper().startswith("LAP") for value in raw]
    cells = _old_series(zip(times, zip(seconds, laps, lapform, strict=True), strict=True))

    def merge(place, cell):
        seconds_now, laps_now, form = cell if isinstance(cell, tuple) else (None, None, False)
        return _gap_display(place, seconds_now, laps_now, form)

    return _old_combine(position, cells, merge)


def _old_race_from_stream(stream, codes):
    fields = {}
    for code in codes:
        own = stream[stream["Driver"] == code].sort_values("Time", kind="stable")
        if own.empty:
            continue
        times = own["Time"].to_numpy(float)

        def column(name, own=own):
            if name not in own.columns:
                return [None] * len(own)
            return [_old_clean(value) for value in own[name].tolist()]

        position = _old_series(_old_settled_positions(own))
        gap_s, gap_laps = column("GapSeconds"), column("GapLapsDown")
        int_s, int_laps = column("IntervalSeconds"), column("IntervalLapsDown")
        raw_int = column("IntervalToPositionAhead")
        int_s = [
            None if is_leader_cell(cell) or position.at(t) == 1 else value
            for t, value, cell in zip(times.tolist(), int_s, raw_int, strict=True)
        ]
        fields[code] = {
            "position": position,
            "gap": _old_stream_display(position, times, gap_s, gap_laps, column("GapToLeader")),
            "gap_s": _old_series(zip(times, gap_s, strict=True)),
            "laps_down": _old_series(zip(times, gap_laps, strict=True)),
            "interval": _old_stream_display(
                position, times, int_s, int_laps, column("IntervalToPositionAhead")
            ),
            "interval_s": _old_series(zip(times, int_s, strict=True)),
        }
    return fields


ORACLE = {
    "_clean": _old_clean,
    "_series": _old_series,
    "_combine": _old_combine,
    "_settled_positions": _old_settled_positions,
    "_stream_display": _old_stream_display,
    "_race_from_stream": _old_race_from_stream,
}


def _oracle_series(session: dict, monkeypatch) -> model.TowerSeries:
    with monkeypatch.context() as patch:
        for name, function in ORACLE.items():
            patch.setattr(model, name, function)
        return tower_series(session)


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _typed(values) -> list:
    return [(type(value), value) for value in values]


def _assert_same_field(new, old, where: str) -> None:
    assert np.array_equal(new.t, old.t), f"{where}: times differ"
    assert new.t.dtype == old.t.dtype, where
    assert _typed(new.v) == _typed(old.v), f"{where}: values differ"


def _assert_same_series(new: model.TowerSeries, old: model.TowerSeries) -> None:
    assert new.drivers == old.drivers
    assert new.kind == old.kind and new.estimated == old.estimated
    assert new.total_laps == old.total_laps and new.chequered == old.chequered
    _assert_same_field(new.leader_lap, old.leader_lap, "leader_lap")
    assert new.fields.keys() == old.fields.keys()
    for code, fields in old.fields.items():
        assert new.fields[code].keys() == fields.keys(), code
        for name, series in fields.items():
            _assert_same_field(new.fields[code][name], series, f"{code}.{name}")


def _messy_race() -> dict:
    """The big race with a stream that exercises every branch of the old code.

    Gaps that change every update, the leader's ``LAP n`` cells, lapped cars,
    missing values of each kind (NaN, None, NA), repeated timestamps and
    positions that flicker for less than the settle time.
    """
    session = _big_race()
    stream = session["timing_stream"].copy()
    rng = np.random.default_rng(11)
    rows = len(stream)
    gap = np.round(stream["GapSeconds"].to_numpy() + rng.normal(0, 0.3, rows).cumsum() % 3, 3)
    stream["GapSeconds"] = np.where(rng.random(rows) < 0.03, np.nan, np.abs(gap))
    interval = np.round(rng.uniform(0.2, 2.5, rows), 3)
    stream["IntervalSeconds"] = np.where(rng.random(rows) < 0.03, np.nan, interval)
    leader = stream["Position"].to_numpy() == 1
    stream["IntervalToPositionAhead"] = np.where(
        leader, "LAP 12", [f"+{value:.3f}" for value in interval]
    )
    stream["GapToLeader"] = np.where(leader, "LAP 12", [f"+{value:.3f}" for value in np.abs(gap)])
    lapped = rng.random(rows) < 0.05
    stream.loc[lapped, "GapToLeader"] = "1 L"
    stream["GapLapsDown"] = pd.array(np.where(lapped, 1, 0), dtype="Int64")
    stream.loc[rng.random(rows) < 0.02, "GapLapsDown"] = pd.NA
    stream["IntervalLapsDown"] = np.where(rng.random(rows) < 0.02, None, 0).astype(object)
    flicker = rng.random(rows) < 0.05
    stream["Position"] = stream["Position"].astype(float)
    stream.loc[flicker, "Position"] = stream.loc[flicker, "Position"] + 1
    stream.loc[rng.random(rows) < 0.01, "Position"] = np.nan
    # Repeated stamps: some rows share the previous row's time.
    times = stream["Time"].to_numpy().copy()
    repeat = np.flatnonzero(rng.random(rows) < 0.03)
    repeat = repeat[repeat > 0]
    times[repeat] = times[repeat - 1]
    stream["Time"] = times
    session["timing_stream"] = stream.sample(frac=1.0, random_state=5)  # unsorted
    return session


SESSIONS = {
    "fixture race": fx.race_session,
    "fixture race without a stream": lambda: fx.race_session(with_stream=False),
    "qualifying": fx.qualifying_session,
    "practice": fx.practice_session,
    "red flag race": _red_flag_race,
    "big race": _big_race,
    "messy race": _messy_race,
}


@pytest.mark.parametrize("name", list(SESSIONS))
def test_the_series_equal_the_scalar_implementation(name, monkeypatch):
    session = SESSIONS[name]()

    old = _oracle_series(session, monkeypatch)
    new = tower_series(session)

    _assert_same_series(new, old)


def test_the_messy_race_reaches_every_display_case():
    """The comparison above is only as good as the cases the stream covers."""
    series = tower_series(_messy_race())
    shown = {value for code in series.drivers for value in series.fields[code]["gap"].v}
    interval = {value for code in series.drivers for value in series.fields[code]["interval_s"].v}

    assert {MISSING, LEADER, format_lap_gap(1)} <= shown
    assert len(shown) > 1000  # the gaps really change
    assert None in interval


# Points as the builders see them: times with repeats and gaps, values of
# every kind a column carries (numpy scalars, missing markers, tuples).
times = st.one_of(
    st.none(), st.just(float("nan")), st.sampled_from([0.0, 0.5, 1.0, 1.0, 2.0, 3.5, 10.0])
)
values = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(pd.NA),
    st.just(pd.NaT),
    st.integers(0, 3),
    st.sampled_from([1.0, 2.0, np.float64(1.0), np.int64(2), True, False]),
    st.sampled_from(["+1.000", "LAP 3", MISSING]),
    st.tuples(st.sampled_from([None, 1.5]), st.sampled_from([None, 0, 1]), st.booleans()),
)


@settings(max_examples=300, deadline=None, database=None)
@given(st.lists(st.tuples(times, values), max_size=30))
def test_series_matches_the_scalar_builder_for_any_points(points):
    new, old = model._series(points), _old_series(points)

    _assert_same_field(new, old, "points")
