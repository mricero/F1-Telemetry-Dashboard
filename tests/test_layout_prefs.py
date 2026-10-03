"""FEAT-10: tower column toggles and the panel checklist, kept in query params."""

import os
import re

import pytest
from streamlit.testing.v1 import AppTest

from processing.view_params import (
    PANEL_CHOICES,
    TOWER_COLUMN_CHOICES,
    format_tokens,
    parse_tokens,
)

os.environ.setdefault("F1_METRICS_STORE", ":memory:")

from tests.test_app_sources import _open
from tests.test_preferences import _loaded, _param, _shared_link


class TestTokens:
    def test_names_are_returned_in_the_checklist_order(self):
        assert parse_tokens("pit,tyres", TOWER_COLUMN_CHOICES) == ["tyres", "pit"]

    def test_unknown_names_duplicates_and_case_are_handled(self):
        assert parse_tokens(["TYRES,tyres,<b>", "nope"], TOWER_COLUMN_CHOICES) == ["tyres"]

    def test_nothing_reads_as_nothing_hidden(self):
        assert parse_tokens(None, PANEL_CHOICES) == []
        assert parse_tokens("", PANEL_CHOICES) == []

    def test_round_trip(self):
        names = ["last", "pit"]
        assert parse_tokens(format_tokens(names), TOWER_COLUMN_CHOICES) == names

    def test_the_default_is_omitted(self):
        assert format_tokens([]) is None

    def test_every_name_is_unique(self):
        for choices in (TOWER_COLUMN_CHOICES, PANEL_CHOICES):
            names = [name for name, _ in choices]
            assert len(names) == len(set(names))


class TestTowerHtml:
    def _rows(self):
        from processing.timing import build_timing_rows
        from tests.test_app_sources import session_dict

        return build_timing_rows(session_dict("fastf1"))

    def _headers(self, markup: str) -> list[str]:
        return re.findall(r"<th[^>]*>([^<]+)</th>", markup)

    def test_by_default_every_column_is_drawn(self):
        from ui.dashboard import TOWER_COLUMNS, tower_html

        assert self._headers(tower_html(self._rows())) == TOWER_COLUMNS

    def test_a_hidden_column_is_left_out_of_the_header_and_every_row(self):
        from ui.dashboard import TOWER_COLUMNS, tower_html

        rows = self._rows()
        full = tower_html(rows)
        markup = tower_html(rows, hidden=["tyres", "sectors"])

        assert self._headers(markup) == [
            c
            for c in TOWER_COLUMNS
            if c not in ("Tyre history", "Sector 1", "Sector 2", "Sector 3")
        ]
        assert full.count("<td") - markup.count("<td") == len(rows) * 4

    def test_gap_and_interval_share_one_switch(self):
        from ui.dashboard import tower_html

        headers = self._headers(tower_html(self._rows(), hidden=["gap"]))

        assert "Gap" not in headers and "Interval" not in headers


class TestPreferencesInTheLink:
    def test_a_link_hides_columns_and_panels(self):
        app_test = _shared_link(hide_cols="tyres,pit", hide_panels="map")

        assert app_test.session_state["layout_hidden_columns"] == ["tyres", "pit"]
        assert app_test.session_state["layout_hidden_panels"] == ["map"]
        # The link survives the session load that rewrites the URL.
        assert _param(app_test, "hide_cols") == "tyres,pit"
        assert _param(app_test, "hide_panels") == "map"

    def test_unknown_names_in_a_link_are_ignored(self):
        app_test = _shared_link(hide_cols="<script>,nope", hide_panels="")

        assert app_test.session_state["layout_hidden_columns"] == []
        assert _param(app_test, "hide_cols") is None

    def test_the_default_layout_leaves_the_url_clean(self):
        app_test = _shared_link()

        assert _param(app_test, "hide_cols") is None
        assert _param(app_test, "hide_panels") is None

    def test_the_settings_checklists_write_the_url(self):
        app_test = _open(_loaded(), "settings")

        app_test.multiselect(key="layout_columns_shown").set_value(
            [name for name, _ in TOWER_COLUMN_CHOICES if name != "diff"]
        ).run()
        app_test.multiselect(key="layout_panels_shown").set_value(["map", "strip"]).run()

        assert not app_test.exception, app_test.exception
        assert _param(app_test, "hide_cols") == "diff"
        assert _param(app_test, "hide_panels") == "card,rc,sectors"

    def test_showing_everything_again_removes_the_parameters(self):
        app_test = _open(_shared_link(hide_cols="diff"), "settings")

        app_test.multiselect(key="layout_columns_shown").set_value(
            [name for name, _ in TOWER_COLUMN_CHOICES]
        ).run()

        assert _param(app_test, "hide_cols") is None


def _dashboard_script():
    from tests.test_app_sources import session_dict
    from ui.dashboard import render_dashboard

    render_dashboard(session_dict("fastf1"))


class TestDashboardPanels:
    def _html(self, **params) -> str:
        app_test = AppTest.from_function(_dashboard_script, default_timeout=60)
        for name, value in params.items():
            app_test.query_params[name] = value
        app_test.run()
        assert not app_test.exception, app_test.exception
        return "".join(element.proto.body for element in app_test.get("html"))

    def test_by_default_the_side_panels_are_drawn(self):
        markup = self._html()

        assert 'class="f1-sector-card"' in markup

    @pytest.mark.parametrize("hidden", ["sectors", "map", "sectors,map"])
    def test_a_hidden_panel_is_not_drawn(self, hidden):
        markup = self._html(hide_panels=hidden)

        assert ('class="f1-sector-card"' in markup) == ("sectors" not in hidden)
        assert "f1-tower" in markup
