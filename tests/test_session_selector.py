"""Offline AppTest coverage for the sidebar source selector (HIST-01).

``render_session_selector`` decides which widgets exist per data source. The
Replay path was broken end-to-end while the unit tests stayed green, so these
drive the real Streamlit widget tree with a stub data manager.
"""

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """``_event_names_cached`` ignores the leading-underscore manager argument,
    so results would leak between tests that use different stubs."""
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


class _StubFastF1:
    def get_available_sessions(self, year):
        return pd.DataFrame({"EventName": ["Bahrain Grand Prix", "Monaco Grand Prix"]})


class _StubManager:
    """Minimal stand-in for DataSourceManager: no network, no FastF1."""

    def __init__(self, replays=("Bahrain_R_20260916_120000.pkl",), race_weekend=False):
        self._replays = list(replays)
        self._race_weekend = race_weekend
        self.fastf1 = _StubFastF1()

    def _is_race_weekend(self) -> bool:
        return self._race_weekend

    def get_available_replays(self) -> list:
        return list(self._replays)


def _selector_script():
    import streamlit as st

    from tests.test_session_selector import _StubManager
    from ui.layout import render_session_selector

    st.session_state["selection"] = render_session_selector(_StubManager())


def _run(source_label: str) -> AppTest:
    app = AppTest.from_function(_selector_script, default_timeout=30)
    app.run()
    app.selectbox[0].set_value(source_label).run()
    return app


def _labels(app: AppTest) -> list:
    return [box.label for box in app.selectbox]


class TestReplaySelection:
    def test_replay_hides_the_historical_selectors(self):
        app = _run("Replay (Saved)")

        assert not app.exception
        labels = _labels(app)
        assert "Replay File" in labels
        for irrelevant in ("Season", "Grand Prix", "Session"):
            assert irrelevant not in labels, f"{irrelevant} is meaningless for a replay"
        assert len(app.radio) == 0, "telemetry scope does not apply to a replay"

    def test_replay_selection_passes_the_file_through(self):
        app = _run("Replay (Saved)")

        selection = app.session_state["selection"]
        assert selection["source"] == "replay"
        assert selection["replay_file"] == "Bahrain_R_20260916_120000.pkl"

    def test_fastf1_still_offers_the_historical_selectors(self):
        app = _run("FastF1 (Historical)")

        assert not app.exception
        labels = _labels(app)
        assert "Season" in labels and "Session" in labels


@pytest.mark.parametrize("source_label", ["Replay (Saved)", "FastF1 (Historical)"])
def test_selector_never_raises(source_label):
    app = _run(source_label)
    assert not app.exception


class _SprintFastF1(_StubFastF1):
    """A sprint weekend (no FP2/FP3, with Sprint Qualifying) and a normal one."""

    def get_available_sessions(self, year):
        return pd.DataFrame(
            {
                "Year": [year, year],
                "EventName": ["Miami Grand Prix", "Monaco Grand Prix"],
                "Session1": ["Practice 1", "Practice 1"],
                "Session1DateUtc": [pd.Timestamp("2025-05-02T16:30"), pd.Timestamp("2025-05-23")],
                "Session2": ["Sprint Qualifying", "Practice 2"],
                "Session2DateUtc": [pd.Timestamp("2025-05-02T20:30"), pd.Timestamp("2025-05-23")],
                "Session3": ["Sprint", "Practice 3"],
                "Session3DateUtc": [pd.Timestamp("2025-05-03T16:00"), pd.Timestamp("2025-05-24")],
                "Session4": ["Qualifying", "Qualifying"],
                "Session4DateUtc": [pd.Timestamp("2025-05-03T20:00"), pd.Timestamp("2025-05-24")],
                "Session5": ["Race", "Race"],
                "Session5DateUtc": [pd.Timestamp("2025-05-04T20:00"), pd.Timestamp("2025-05-25")],
            }
        )


class _SprintManager(_StubManager):
    def __init__(self):
        super().__init__()
        self.fastf1 = _SprintFastF1()


def _sprint_script():
    import streamlit as st

    from tests.test_session_selector import _SprintManager
    from ui.layout import render_session_selector

    st.session_state["selection"] = render_session_selector(_SprintManager())


class TestSessionListFollowsTheWeekendFormat:
    """HIST-05: FP2/FP3 don't exist on a sprint weekend; SQ does."""

    @staticmethod
    def _sessions_for(gp_label: str) -> list:
        app = AppTest.from_function(_sprint_script, default_timeout=30)
        app.run()
        app.selectbox[0].set_value("FastF1 (Historical)").run()
        gp_box = next(box for box in app.selectbox if box.label == "Grand Prix")
        gp_box.set_value(gp_label).run()
        session_box = next(box for box in app.selectbox if box.label == "Session")
        return list(session_box.options)

    def test_sprint_weekend_offers_sq_and_s(self):
        assert self._sessions_for("Miami Grand Prix") == ["FP1", "SQ", "S", "Q", "R"]

    def test_conventional_weekend_offers_all_three_practices(self):
        assert self._sessions_for("Monaco Grand Prix") == ["FP1", "FP2", "FP3", "Q", "R"]
