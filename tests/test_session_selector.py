"""The sidebar session picker (HIST-01, HIST-05, LIVE-15, UI-02).

``render_session_selector`` draws the picker in the sidebar and returns the
selection to load. Browsing it must never load anything: only **Load
session**, a Recent entry or a shared URL changes the selection. These drive
the real Streamlit widget tree with stub managers.
"""

import os

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

os.environ.setdefault("F1_METRICS_STORE", ":memory:")


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

    def __init__(self, replays=("Bahrain_R_20260916_120000",), race_weekend=False):
        self._replays = list(replays)
        self._race_weekend = race_weekend
        self.fastf1 = _StubFastF1()

    def _is_race_weekend(self) -> bool:
        return self._race_weekend

    def get_available_replays(self) -> list:
        return list(self._replays)


def _selector_script():
    import streamlit as st

    from f1dash.ui.layout import render_session_selector
    from tests.test_session_selector import _StubManager

    st.session_state["returned"] = render_session_selector(_StubManager())


def _app(script=_selector_script) -> AppTest:
    app = AppTest.from_function(script, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def _labels(app: AppTest) -> list:
    return [box.label for box in app.selectbox]


def _load(app: AppTest) -> AppTest:
    next(button for button in app.button if button.label == "Load session").click().run()
    return app


class TestNothingLoadsUntilAsked:
    def test_the_first_open_selects_nothing(self):
        app = _app()

        assert app.session_state["returned"] is None
        assert any(button.label == "Load session" for button in app.button)

    def test_browsing_the_dropdowns_selects_nothing(self):
        app = _app()
        app.selectbox(key="picker_gp").set_value("Monaco Grand Prix").run()

        assert app.session_state["returned"] is None

    def test_load_session_commits_what_is_shown(self):
        app = _app()
        app.selectbox(key="picker_gp").set_value("Monaco Grand Prix").run()
        _load(app)

        selection = app.session_state["returned"]
        assert selection["source"] == "fastf1"
        assert selection["gp"] == "Monaco Grand Prix"
        assert selection["session_type"] == "R"

    def test_seasons_go_back_to_2018(self):
        seasons = [int(season) for season in _app().selectbox(key="picker_year").options]

        assert seasons[-1] == 2018
        assert seasons == sorted(seasons, reverse=True)

    def test_a_loaded_session_is_offered_under_recent(self):
        app = _load(_app())
        year = app.session_state["returned"]["year"]

        labels = [button.label for button in app.button]
        assert any(label.startswith(f"{year} Bahrain") for label in labels)


class TestReplaySelection:
    def test_replay_hides_the_historical_selectors(self):
        app = _app()
        app.selectbox(key="picker_source").set_value("Saved replay").run()

        labels = _labels(app)
        assert "Replay file" in labels
        for irrelevant in ("Season", "Grand Prix", "Session"):
            assert irrelevant not in labels, f"{irrelevant} is meaningless for a replay"

    def test_replay_selection_passes_the_file_through(self):
        app = _app()
        app.selectbox(key="picker_source").set_value("Saved replay").run()
        _load(app)

        selection = app.session_state["returned"]
        assert selection["source"] == "replay"
        assert selection["replay_file"] == "Bahrain_R_20260916_120000"

    def test_fastf1_offers_the_historical_selectors(self):
        labels = _labels(_app())

        assert "Season" in labels and "Grand Prix" in labels and "Session" in labels


@pytest.mark.parametrize("source_label", ["Saved replay", "FastF1 (historical)"])
def test_selector_never_raises(source_label):
    app = _app()
    app.selectbox(key="picker_source").set_value(source_label).run()
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

    from f1dash.ui.layout import render_session_selector
    from tests.test_session_selector import _SprintManager

    st.session_state["returned"] = render_session_selector(_SprintManager())


class TestSessionListFollowsTheWeekendFormat:
    """HIST-05: FP2/FP3 don't exist on a sprint weekend; SQ does."""

    @staticmethod
    def _sessions_for(gp_label: str) -> list:
        app = _app(_sprint_script)
        app.selectbox(key="picker_gp").set_value(gp_label).run()
        return list(app.selectbox(key="picker_session").options)

    def test_sprint_weekend_offers_sq_and_s(self):
        assert self._sessions_for("Miami Grand Prix") == [
            "Practice 1",
            "Sprint qualifying",
            "Sprint",
            "Qualifying",
            "Race",
        ]

    def test_conventional_weekend_offers_all_three_practices(self):
        assert self._sessions_for("Monaco Grand Prix") == [
            "Practice 1",
            "Practice 2",
            "Practice 3",
            "Qualifying",
            "Race",
        ]


class TestARunningSessionIsOfferedNotForced:
    """LIVE-15: a running session must not take the historical view away."""

    @staticmethod
    def _live_app():
        def script():
            import streamlit as st

            from f1dash.ui.layout import render_session_selector
            from tests.test_session_selector import _StubManager

            st.session_state["returned"] = render_session_selector(_StubManager(race_weekend=True))

        return _app(script)

    def test_the_historical_selectors_stay_available(self):
        labels = _labels(self._live_app())

        assert "Season" in labels and "Session" in labels

    def test_going_live_is_an_explicit_choice(self):
        app = self._live_app()

        assert any(button.label == "Go live" for button in app.button)
        assert app.session_state["returned"] is None

    def test_pressing_go_live_selects_the_live_source(self):
        app = self._live_app()
        next(button for button in app.button if button.label == "Go live").click().run()

        assert app.session_state["returned"]["source"] == "live"


# --- the whole app: loads are counted -------------------------------------

LOADS: list = []


class CountingManager(_StubManager):
    """The app's data manager, counting every load it is asked for."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.live = None

    def get_session_data(self, **selection):
        from tests.test_app_sources import session_dict

        LOADS.append(selection)
        return session_dict(selection.get("source", "fastf1"))

    def save_replay(self, data, name):
        return name


def _counting_app_script():
    from f1dash import app
    from f1dash.data.runtime_cache import runtime_cache
    from tests.test_session_selector import CountingManager

    app.DataSourceManager = CountingManager
    runtime_cache.begin_session()
    app.main()


class TestTheAppLoadsOnlyOnLoad:
    """UI-02 acceptance, driven through the real app."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        LOADS.clear()
        yield
        LOADS.clear()

    def test_changing_the_grand_prix_does_not_load(self):
        app = _app(_counting_app_script)
        app.selectbox(key="picker_gp").set_value("Monaco Grand Prix").run()

        assert LOADS == []
        assert any("press Load session" in info.value for info in app.info)

    def test_pressing_load_loads_once(self):
        app = _app(_counting_app_script)
        app.selectbox(key="picker_gp").set_value("Monaco Grand Prix").run()
        _load(app)

        assert len(LOADS) == 1
        assert LOADS[0]["gp"] == "Monaco Grand Prix"

    def test_a_shared_link_preselects_and_loads(self):
        app = AppTest.from_function(_counting_app_script, default_timeout=30)
        app.query_params["year"] = "2023"
        app.query_params["gp"] = "Bahrain Grand Prix"
        app.query_params["session"] = "R"
        app.run()

        assert not app.exception, app.exception
        assert len(LOADS) == 1
        loaded = (LOADS[0]["year"], LOADS[0]["gp"], LOADS[0]["session_type"])
        assert loaded == (2023, "Bahrain Grand Prix", "R")
        assert app.selectbox(key="picker_gp").value == "Bahrain Grand Prix"


class TestSharedLinksAreValidated:
    """UI-17: a link loads only what the picker itself would offer."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        LOADS.clear()
        yield
        LOADS.clear()

    @staticmethod
    def _open(**params) -> AppTest:
        app = AppTest.from_function(_counting_app_script, default_timeout=30)
        for name, value in params.items():
            app.query_params[name] = value
        app.run()
        assert not app.exception, app.exception
        return app

    @pytest.mark.parametrize(
        "params",
        [
            {"year": "2017", "gp": "Bahrain Grand Prix", "session": "R"},
            {"year": "2023", "gp": "Nope Grand Prix", "session": "R"},
            {"year": "2023", "gp": "Bahrain Grand Prix", "session": "XYZ"},
            {"year": "soon", "gp": "Bahrain Grand Prix", "session": "R"},
        ],
        ids=["before-2018", "unknown-gp", "unknown-session", "not-a-year"],
    )
    def test_a_bad_link_loads_nothing_and_says_so(self, params):
        app = self._open(**params)

        assert LOADS == []
        assert app.session_state["selection"] is None
        assert any("Link refers to an unknown session" in w.value for w in app.warning)

    def test_a_valid_link_leaves_the_picker_showing_what_loaded(self):
        app = self._open(year="2023", gp="Monaco Grand Prix", session="Q")

        assert len(LOADS) == 1
        loaded = LOADS[0]
        assert app.selectbox(key="picker_year").value == loaded["year"] == 2023
        assert app.selectbox(key="picker_gp").value == loaded["gp"] == "Monaco Grand Prix"
        assert app.selectbox(key="picker_session").value == loaded["session_type"] == "Q"
        assert not app.warning


class TestSidebarOnPhones:
    """UI-19: the sidebar opens expanded only while the picker is all there is."""

    def test_nothing_selected_and_no_link_expands_it(self):
        from f1dash.ui.layout import sidebar_state

        assert sidebar_state(None, {}) == "expanded"

    def test_a_shared_link_or_a_selection_lets_the_browser_decide(self):
        from f1dash.ui.layout import sidebar_state

        assert sidebar_state(None, {"year": "2023", "gp": "Bahrain Grand Prix"}) == "auto"
        assert sidebar_state({"source": "fastf1"}, {}) == "auto"
