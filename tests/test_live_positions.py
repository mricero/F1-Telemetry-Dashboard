"""Position filtering for live trails (IMPROVEMENTS.md LIVE-14).

Cars in the garage report 0,0,0 and entries carry a Status. Keeping those
samples put a straight line to the origin through the track outline and made
distance jump by a circuit's width every time a car was in the pits.
"""

import numpy as np
import pandas as pd

from f1dash.data.live_adapter import LiveDataProcessor
from tests import live_fixtures


def _records(*entries) -> list:
    return [
        {"DriverNo": "1", "Utc": f"2026-05-01T12:00:{i:02d}Z", **entry}
        for i, entry in enumerate(entries)
    ]


class TestOnTrackFiltering:
    def test_garage_zero_triples_are_dropped(self):
        frame = LiveDataProcessor.parse_position_data(
            _records(
                {"X": 0, "Y": 0, "Z": 0, "Status": "OnTrack"},
                {"X": 1000, "Y": 500, "Z": -10, "Status": "OnTrack"},
                {"X": 1100, "Y": 520, "Z": -10, "Status": "OnTrack"},
            )
        )

        assert len(frame) == 2
        assert not ((frame["X"] == 0) & (frame["Y"] == 0)).any()

    def test_off_track_samples_are_dropped(self):
        frame = LiveDataProcessor.parse_position_data(
            _records(
                {"X": 1000, "Y": 500, "Z": 0, "Status": "OnTrack"},
                {"X": 9999, "Y": 9999, "Z": 0, "Status": "OffTrack"},
                {"X": 1100, "Y": 520, "Z": 0, "Status": "OnTrack"},
            )
        )

        assert frame["X"].tolist() == [1000.0, 1100.0]

    def test_records_without_a_status_are_kept(self):
        frame = LiveDataProcessor.parse_position_data(
            _records({"X": 1000, "Y": 500, "Z": 0}, {"X": 1100, "Y": 520, "Z": 0})
        )

        assert len(frame) == 2

    def test_distance_stays_monotonic_across_a_pit_stop(self):
        frame = LiveDataProcessor.parse_position_data(
            _records(
                {"X": 0, "Y": 0, "Z": 0, "Status": "OnTrack"},
                {"X": 1000, "Y": 0, "Z": 0, "Status": "OnTrack"},
                {"X": 0, "Y": 0, "Z": 0, "Status": "OnTrack"},
                {"X": 2000, "Y": 0, "Z": 0, "Status": "OnTrack"},
                {"X": 3000, "Y": 0, "Z": 0, "Status": "OnTrack"},
            )
        )

        distance = LiveDataProcessor.distance_at(frame, frame["timestamp"])

        assert distance is not None
        assert np.all(np.diff(distance) >= -1e-9)
        # 1000 -> 2000 -> 3000 position units = 200 m, not a trip via origin.
        assert distance[-1] == 200.0

    def test_the_recorded_garage_samples_are_filtered_out(self):
        from f1dash.data.live_adapter import decode_topic_payload

        records = []
        for timestamp, payload in live_fixtures.messages("Position.z"):
            records.extend(decode_topic_payload("Position.z", [(timestamp, payload)]))
        raw = pd.DataFrame(records)
        filtered = LiveDataProcessor.parse_position_data(records)

        assert ((raw["X"] == 0) & (raw["Y"] == 0)).any(), "fixture has garage samples"
        assert not ((filtered["X"] == 0) & (filtered["Y"] == 0)).any()
