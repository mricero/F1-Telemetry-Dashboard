"""Neutralised periods and their shading (UX-06), the race-control filter,
and the 2026 telemetry channels (FEAT-12)."""

import pandas as pd
import plotly.graph_objects as go

from f1dash.processing.track_periods import lap_spans, lap_states, neutral_periods


def _status(*rows):
    return pd.DataFrame(rows, columns=["Time", "Status"])


def _laps(n=6, lap_s=90.0):
    return pd.DataFrame(
        {
            "Driver": ["VER"] * n,
            "LapNumber": list(range(1, n + 1)),
            "LapStartTime": [pd.Timedelta(i * lap_s, unit="s") for i in range(n)],
            "Time": [pd.Timedelta((i + 1) * lap_s, unit="s") for i in range(n)],
        }
    )


class TestNeutralPeriods:
    def test_an_sc_period_runs_until_the_next_change(self):
        periods = neutral_periods(_status((100, "1"), (200, "4"), (380, "1")))
        assert periods == [{"state": "SC", "start": 200.0, "end": 380.0}]

    def test_vsc_ending_is_still_a_vsc(self):
        periods = neutral_periods(_status((10, "6"), (50, "7"), (70, "1")))
        assert [p["state"] for p in periods] == ["VSC"]
        assert periods[0]["end"] == 70.0

    def test_an_open_period_closes_at_the_end(self):
        assert neutral_periods(_status((10, "5")), end=99.0)[0]["end"] == 99.0

    def test_no_frame_no_periods(self):
        assert neutral_periods(None) == []


class TestLapStates:
    def test_status_changes_mark_the_laps_they_overlap(self):
        # SC from 200 s to 380 s covers laps 3 (180-270), 4 (270-360) and 5 (360-450).
        states = lap_states(_laps(), _status((0, "1"), (200, "4"), (380, "1")))
        assert states == {3: "SC", 4: "SC", 5: "SC"}
        assert lap_spans(states) == [{"state": "SC", "first": 3, "last": 5}]

    def test_the_laps_own_column_wins_and_red_outranks_sc(self):
        laps = _laps(3)
        laps["TrackStatus"] = ["1", "45", "1"]
        assert lap_states(laps, None) == {2: "RED"}


class TestShading:
    def test_neutral_laps_are_shaded_with_their_word(self):
        from f1dash.ui.layout import shade_neutral_laps

        fig = go.Figure()
        spans = shade_neutral_laps(fig, _laps(), _status((0, "1"), (200, "6"), (300, "1")))

        assert [s["state"] for s in spans] == ["VSC"]
        texts = [a.text for a in fig.layout.annotations]
        assert texts == ["VSC"]


class TestRaceControlFilter:
    FRAME = pd.DataFrame(
        {
            "Category": ["Flag", "SafetyCar", "Other"],
            "Message": ["YELLOW IN TRACK SECTOR 4", "SAFETY CAR DEPLOYED", "CAR 1 TIME DELETED"],
        }
    )

    def test_categories_and_search_combine(self):
        from f1dash.ui.layout import filter_race_control

        assert len(filter_race_control(self.FRAME, ["Flag", "Other"], "deleted")) == 1
        assert len(filter_race_control(self.FRAME, None, "")) == 3
        assert len(filter_race_control(self.FRAME, None, "safety")) == 1


class TestTelemetryChannels:
    def test_2026_has_no_drs_channel(self):
        from f1dash.ui.layout import telemetry_channels

        assert "DRS" not in telemetry_channels(2026)
        assert "DRS" in telemetry_channels(2025)
        assert "DRS" in telemetry_channels(None)
