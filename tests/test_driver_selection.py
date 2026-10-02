"""UX-03: the Analysis driver selection and how a link names it."""

import pandas as pd

from processing.driver_selection import (
    classification_order,
    default_drivers,
    format_codes,
    parse_codes,
)


def _results() -> pd.DataFrame:
    # FastF1 session.results columns (RESULT_COLUMNS), unordered on purpose.
    return pd.DataFrame(
        {
            "Abbreviation": ["HAM", "VER", "NOR", "LEC"],
            "DriverNumber": ["44", "1", "4", "16"],
            "Position": [3.0, 1.0, 2.0, float("nan")],
        }
    )


def _laps() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Driver": ["VER", "VER", "SAI", "SAI", "ALB"],
            "LapNumber": [1, 2, 1, 2, 1],
            "Position": [1, 1, 3, 2, 4],
        }
    )


class TestClassificationOrder:
    def test_the_official_results_lead_in_finishing_order(self):
        order = classification_order({"results": _results()})

        assert order[:3] == ["VER", "NOR", "HAM"]
        assert "LEC" in order  # unclassified, still selectable

    def test_without_results_the_last_lap_order_is_used(self):
        order = classification_order({"laps": _laps()})

        # ALB stopped after lap 1: the cars that ran further rank first.
        assert order == ["VER", "SAI", "ALB"]

    def test_drivers_with_only_telemetry_or_a_table_row_are_appended(self):
        session = {
            "results": _results(),
            "telemetry": {"PIA": pd.DataFrame()},
            "drivers": pd.DataFrame({"name_acronym": ["VER", "RUS"]}),
        }

        order = classification_order(session)

        assert order[-2:] == ["PIA", "RUS"]
        assert len(order) == len(set(order))

    def test_an_empty_session_has_no_drivers(self):
        assert classification_order({}) == []


class TestDefaults:
    def test_the_top_five(self):
        assert default_drivers(list("ABCDEFG")) == list("ABCDE")

    def test_everyone_when_fewer_than_five(self):
        assert default_drivers(["VER", "HAM"]) == ["VER", "HAM"]


class TestParseCodes:
    allowed = ("VER", "NOR", "HAM")

    def test_comma_separated(self):
        assert parse_codes("VER,NOR", self.allowed) == ["VER", "NOR"]

    def test_unknown_codes_and_duplicates_are_dropped(self):
        assert parse_codes("VER,XXX,ver, NOR", self.allowed) == ["VER", "NOR"]

    def test_repeated_parameters_are_accepted(self):
        assert parse_codes(["HAM", "VER"], self.allowed) == ["HAM", "VER"]

    def test_nothing_or_nonsense_is_empty(self):
        assert parse_codes(None, self.allowed) == []
        assert parse_codes("<script>", self.allowed) == []

    def test_format_round_trips(self):
        assert parse_codes(format_codes(["NOR", "VER"]), self.allowed) == ["NOR", "VER"]
