"""Property tests for the merge, slicing and resampling primitives (TEST-05).

Example tests pin the cases someone thought of; these state the invariants
every input must keep: a merge is idempotent and never aliases the message,
segment bounds always cover the trace in order, and a resample never invents
values the source did not have.
"""

import copy

import numpy as np
import pandas as pd
import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from hypothesis.extra import numpy as hnp  # noqa: E402

from data.live_state import DELETED_KEY, deep_merge  # noqa: E402
from processing.telemetry_processor import TelemetryProcessor  # noqa: E402
from processing.timing import segment_boundaries  # noqa: E402

# database=None: no .hypothesis/ folder in the working tree (TEST-08).
PROPERTY_SETTINGS = settings(max_examples=60, deadline=None, database=None)

# Feed payloads: string keys (the feed's own, or list indices as strings),
# scalar leaves, nested dicts and lists - the shapes TimingData carries.
keys = st.one_of(
    st.sampled_from(["Lines", "1", "44", "Sectors", "0", "2", "Value", "Stints"]),
    st.text(alphabet="abcXYZ0123456789", min_size=1, max_size=4),
).filter(lambda key: key != DELETED_KEY)
leaves = st.one_of(
    st.none(), st.booleans(), st.integers(-1000, 1000), st.text(max_size=6), st.floats(-1e6, 1e6)
)
payloads = st.recursive(
    leaves,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(keys, children, max_size=4),
    ),
    max_leaves=20,
)
messages = st.dictionaries(keys, payloads, max_size=5)


class TestDeepMerge:
    @PROPERTY_SETTINGS
    @given(messages)
    def test_merging_into_nothing_copies_the_message(self, message):
        assert deep_merge({}, copy.deepcopy(message)) == message

    @PROPERTY_SETTINGS
    @given(messages, messages)
    def test_applying_the_same_delta_twice_changes_nothing(self, base, delta):
        once = deep_merge(copy.deepcopy(base), copy.deepcopy(delta))
        twice = deep_merge(copy.deepcopy(once), copy.deepcopy(delta))

        assert twice == once

    @PROPERTY_SETTINGS
    @given(messages, messages)
    def test_every_key_of_the_delta_is_present_afterwards(self, base, delta):
        merged = deep_merge(copy.deepcopy(base), delta)

        assert set(delta) <= set(merged)
        assert set(base) <= set(merged)

    @PROPERTY_SETTINGS
    @given(messages, messages)
    def test_the_result_does_not_alias_the_message(self, base, delta):
        """The client thread may reuse the dict it handed over."""
        handed_over = copy.deepcopy(delta)
        merged = deep_merge(copy.deepcopy(base), handed_over)
        snapshot = copy.deepcopy(merged)

        for value in handed_over.values():
            if isinstance(value, dict):
                value["mutated-after"] = 1
            elif isinstance(value, list):
                value.append("mutated-after")

        assert merged == snapshot

    @PROPERTY_SETTINGS
    @given(messages, st.data())
    def test_deleted_keys_are_gone(self, base, data):
        if not base:
            return
        doomed = data.draw(st.lists(st.sampled_from(sorted(base)), min_size=1, unique=True))

        merged = deep_merge(copy.deepcopy(base), {DELETED_KEY: doomed})

        assert not set(doomed) & set(merged)
        assert set(merged) == set(base) - set(doomed)


distances = hnp.arrays(
    np.float64,
    st.integers(2, 300),
    elements=st.floats(0, 6000, allow_nan=False, allow_infinity=False),
).map(np.sort)


class TestSegmentBoundaries:
    @PROPERTY_SETTINGS
    @given(distances, st.integers(1, 60))
    def test_bounds_cover_the_trace_in_order(self, distance, segments):
        bounds = segment_boundaries(distance, segments)

        assert len(bounds) == segments + 1
        assert bounds[0] == 0
        assert bounds[-1] == len(distance) - 1
        assert np.all(np.diff(bounds) >= 0)
        assert np.all((bounds >= 0) & (bounds < len(distance)))

    @PROPERTY_SETTINGS
    @given(st.integers(2, 300), st.integers(1, 60), st.floats(0, 5000))
    def test_a_constant_distance_falls_back_to_an_index_split(self, length, segments, value):
        bounds = segment_boundaries(np.full(length, value), segments)

        assert bounds[0] == 0 and bounds[-1] == length - 1
        assert np.all(np.diff(bounds) >= 0)

    @PROPERTY_SETTINGS
    @given(st.integers(0, 1), st.integers(1, 20))
    def test_too_short_a_trace_gives_zeros(self, length, segments):
        assert segment_boundaries(np.zeros(length), segments).tolist() == [0] * (segments + 1)


@st.composite
def lap_traces(draw):
    rows = draw(st.integers(2, 120))
    distance = draw(
        hnp.arrays(
            np.float64,
            rows,
            elements=st.floats(0, 5500, allow_nan=False),
            unique=True,
        )
    )
    speed = draw(hnp.arrays(np.float64, rows, elements=st.floats(0, 360, allow_nan=False)))
    gear = draw(hnp.arrays(np.int64, rows, elements=st.integers(0, 8)))
    brake = draw(hnp.arrays(np.bool_, rows))
    return pd.DataFrame({"Distance": distance, "Speed": speed, "nGear": gear, "Brake": brake})


class TestResampleToDistanceGrid:
    @PROPERTY_SETTINGS
    @given(lap_traces())
    def test_the_grid_is_uniform_and_inside_the_lap(self, trace):
        result = TelemetryProcessor().resample_to_distance_grid(trace)
        if trace["Distance"].max() <= 0:
            return
        grid = result["Distance"].to_numpy()

        step = TelemetryProcessor.DISTANCE_STEP
        assert grid[0] == 0
        assert np.allclose(np.diff(grid), step)
        assert grid[-1] < trace["Distance"].max()

    @PROPERTY_SETTINGS
    @given(lap_traces())
    def test_continuous_channels_stay_within_the_source_range(self, trace):
        result = TelemetryProcessor().resample_to_distance_grid(trace)
        if "Speed" not in result or result.empty:
            return

        assert result["Speed"].min() >= trace["Speed"].min() - 1e-9
        assert result["Speed"].max() <= trace["Speed"].max() + 1e-9

    @PROPERTY_SETTINGS
    @given(lap_traces())
    def test_coded_channels_only_take_values_the_source_had(self, trace):
        """Gear 4.7 does not exist: nGear and Brake are never interpolated."""
        result = TelemetryProcessor().resample_to_distance_grid(trace)
        if "nGear" not in result:
            return

        assert set(result["nGear"]) <= set(trace["nGear"])
        assert set(result["Brake"]) <= set(trace["Brake"])
