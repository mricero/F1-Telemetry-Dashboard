"""UX-03 driver selection and favourites, driven through the real app."""

import json
import os

import pytest
from streamlit.testing.v1 import AppTest

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

from tests.test_app_sources import _app_script, _open, _press_load

SHARED = {"year": "2026", "gp": "Italian Grand Prix", "session": "R"}


@pytest.fixture(autouse=True)
def _isolate_streamlit_caches():
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _shared_link(**extra) -> AppTest:
    """Open the app the way a copied link does: everything in the URL."""
    app_test = AppTest.from_function(_app_script, default_timeout=60)
    for name, value in {**SHARED, **extra}.items():
        app_test.query_params[name] = value
    app_test.run()
    assert not app_test.exception, app_test.exception
    return app_test


def _loaded() -> AppTest:
    app_test = AppTest.from_function(_app_script, default_timeout=60)
    app_test.run()
    return _press_load(app_test)


def _session_key(app_test: AppTest) -> str:
    keys = [k for k in app_test.session_state if k.startswith("processed:")]
    assert len(keys) == 1, keys
    return keys[0].split(":", 1)[1]


def _trace_names(app_test: AppTest, index: int = 0) -> list[str]:
    chart = app_test.get("plotly_chart")[index]
    figure = json.loads(chart.proto.spec)
    return [trace.get("name") for trace in figure["data"]]


def _param(app_test: AppTest, name: str):
    values = app_test.query_params.get(name)
    return values[-1] if values else None


class TestDriverSelection:
    def test_the_default_is_the_classification_top_five_or_everyone(self):
        app_test = _open(_loaded(), "analysis", section="Telemetry")

        picker = app_test.multiselect(key=f"analysis_drivers_pick:{_session_key(app_test)}")
        # The stub session has two drivers, fewer than five: both.
        assert picker.value == ["VER", "HAM"]
        assert set(_trace_names(app_test)) == {"VER", "HAM"}
        # The default is not written to the URL.
        assert _param(app_test, "drivers") is None

    def test_a_selection_filters_the_charts_and_is_mirrored_in_the_url(self):
        app_test = _open(_loaded(), "analysis", section="Lap times")
        key = _session_key(app_test)

        app_test.multiselect(key=f"analysis_drivers_pick:{key}").set_value(["HAM"]).run()

        assert not app_test.exception, app_test.exception
        assert _trace_names(app_test) == ["HAM"]
        assert _param(app_test, "drivers") == "HAM"
        assert app_test.session_state[f"analysis_drivers:{key}"] == ["HAM"]

    def test_the_selection_survives_another_section(self):
        app_test = _open(_loaded(), "analysis", section="Lap times")
        key = _session_key(app_test)
        app_test.multiselect(key=f"analysis_drivers_pick:{key}").set_value(["HAM"]).run()

        _open(app_test, "analysis", section="Weather")
        _open(app_test, "analysis", section="Positions")

        assert _trace_names(app_test) == ["HAM"]

    def test_a_link_names_the_drivers(self):
        app_test = _open(_shared_link(drivers="HAM"), "analysis", section="Telemetry")

        assert _trace_names(app_test) == ["HAM"]

    def test_unknown_codes_in_a_link_are_ignored(self):
        app_test = _open(_shared_link(drivers="XXX,<b>"), "analysis", section="Telemetry")

        assert set(_trace_names(app_test)) == {"VER", "HAM"}


class TestFavourites:
    def test_a_link_seeds_the_favourites_and_a_load_keeps_them_in_the_url(self):
        app_test = _shared_link(fav="ham,ZZZZ")

        assert app_test.session_state["favourite_drivers"] == ["HAM"]
        assert _param(app_test, "fav") == "HAM"

    def test_the_settings_page_sets_them(self):
        app_test = _open(_loaded(), "settings")

        app_test.multiselect(key="favourite_drivers_pick").set_value(["VER"]).run()

        assert not app_test.exception, app_test.exception
        assert app_test.session_state["favourite_drivers"] == ["VER"]
        assert _param(app_test, "fav") == "VER"


class TestTowerMarksFavourites:
    def _rows(self):
        from f1dash.processing.timing import build_timing_rows
        from tests.test_app_sources import session_dict

        return build_timing_rows(session_dict("fastf1"))

    def test_a_favourite_row_is_marked_with_a_shape_and_a_word(self):
        from f1dash.ui.dashboard import tower_html

        markup = tower_html(self._rows(), favourites=["HAM"])

        assert markup.count('class="fav"') == 1
        assert 'title="Favourite driver">HAM<' in markup

    def test_no_favourites_no_marks(self):
        from f1dash.ui.dashboard import tower_html

        assert "fav" not in tower_html(self._rows())
