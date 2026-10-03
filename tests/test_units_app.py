"""UX-12 through the real app: links, the Settings page and a speed axis."""

import json
import os

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

from tests.test_app_sources import _open
from tests.test_preferences import _loaded, _param, _shared_link


class TestUnitsInTheLink:
    def test_a_link_names_the_units(self):
        app_test = _shared_link(speed="mph", temp="f", tz="local")

        units = app_test.session_state["viewer_units"]
        assert (units.speed, units.temp, units.time) == ("mph", "f", "local")
        # They survive the session load that rewrites the URL.
        shown = (_param(app_test, "speed"), _param(app_test, "temp"), _param(app_test, "tz"))
        assert shown == ("mph", "f", "local")

    def test_unknown_units_are_ignored_and_the_default_leaves_the_url_clean(self):
        app_test = _shared_link(speed="furlongs", temp="<b>")

        units = app_test.session_state["viewer_units"]
        assert (units.speed, units.temp, units.time) == ("kmh", "c", "track")
        assert _param(app_test, "speed") is None and _param(app_test, "temp") is None

    def test_the_settings_page_changes_them_and_the_url_follows(self):
        app_test = _open(_loaded(), "settings")

        app_test.radio(key="units_speed").set_value("mph").run()
        app_test.radio(key="units_temp").set_value("f").run()

        assert not app_test.exception, app_test.exception
        assert _param(app_test, "speed") == "mph"
        assert _param(app_test, "temp") == "f"
        app_test.radio(key="units_speed").set_value("kmh").run()
        assert _param(app_test, "speed") is None

    def test_the_speed_chart_shows_mph(self):
        app_test = _open(_shared_link(speed="mph"), "analysis", section="Telemetry")

        assert not app_test.exception, app_test.exception
        # The first chart is the Speed tab.
        figure = json.loads(app_test.get("plotly_chart")[0].proto.spec)
        assert figure["layout"]["yaxis"]["title"]["text"] == "Speed (mph)"

    def test_the_speed_chart_is_km_h_by_default(self):
        app_test = _open(_loaded(), "analysis", section="Telemetry")

        figure = json.loads(app_test.get("plotly_chart")[0].proto.spec)
        assert figure["layout"]["yaxis"]["title"]["text"] == "Speed (km/h)"
