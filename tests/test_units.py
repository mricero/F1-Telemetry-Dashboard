"""Display units (UX-12): data stays metric; only what is drawn converts."""

import pytest
from streamlit.testing.v1 import AppTest

from ui import units


class TestConversions:
    def test_speed(self):
        assert units.speed(160.9344, units.IMPERIAL) == pytest.approx(100.0)
        assert units.speed(300.0, units.METRIC) == 300.0
        assert units.speed_unit(units.IMPERIAL) == "mph"

    def test_temperature(self):
        assert units.temperature(25.0, units.IMPERIAL) == pytest.approx(77.0)
        assert units.temperature_unit(units.METRIC) == "°C"

    def test_outside_a_script_run_it_is_metric(self):
        assert units.preference() == units.METRIC


class TestSettingsAndDisplays:
    def test_the_choice_reaches_the_tower_and_the_url(self):
        def script():
            import streamlit as st

            from ui.dashboard import tower_html
            from ui.pages import settings_page

            settings_page()
            st.html(tower_html([]) or "")
            st.markdown(f"unit={__import__('ui.units', fromlist=['x']).speed_unit()}")

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()
        assert any(md.value == "unit=km/h" for md in app_test.markdown)

        app_test.radio(key="settings_units").set_value(units.IMPERIAL).run()

        assert not app_test.exception, app_test.exception
        assert app_test.query_params.get(units.UNITS_KEY) in (units.IMPERIAL, [units.IMPERIAL])
        assert any(md.value == "unit=mph" for md in app_test.markdown)

    def test_a_shared_link_sets_the_units(self):
        def script():
            import streamlit as st

            from ui import units as u

            st.markdown(f"unit={u.temperature_unit()}")

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.query_params[units.UNITS_KEY] = units.IMPERIAL
        app_test.run()

        assert any(md.value == "unit=°F" for md in app_test.markdown)

    def test_the_tower_speed_column_converts(self):
        from ui.dashboard import _speed_text

        assert _speed_text(321.9, "") == "322"  # metric outside a script run
