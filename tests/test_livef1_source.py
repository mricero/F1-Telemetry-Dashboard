"""The LiveF1 (Historical) source is not offered while it is broken (HIST-03).

The loader reads attributes livef1's `Driver` does not have
(`driver_number`, `name_acronym`, ...) and column names its silver tables do
not use (`Driver`, `nGear`, `LapNumber`), so it raises on the first driver or
returns nothing. The library also raises building a Session for some seasons.
Offering it in the selector only promises something the app cannot deliver.
"""

import pandas as pd
import pytest

from ui.layout import SOURCE_MAP


class TestSelectorDoesNotOfferIt:
    def test_livef1_is_not_in_the_source_list(self):
        assert not any("LiveF1" in label for label in SOURCE_MAP)

    def test_the_sources_that_work_are_still_offered(self):
        assert set(SOURCE_MAP.values()) >= {"fastf1", "live", "replay"}


class TestLoaderExplainsItself:
    @pytest.fixture
    def manager(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "data.source_manager.FastF1Adapter", lambda *a, **kw: type("A", (), {})()
        )
        from data.source_manager import DataSourceManager

        return DataSourceManager(replay_dir=str(tmp_path))

    def test_asking_for_it_explicitly_raises_a_clear_error(self, manager):
        with pytest.raises(NotImplementedError, match="HIST-03"):
            manager.get_session_data(
                source="livef1", year=2024, gp="Bahrain Grand Prix", session_type="R"
            )

    def test_the_error_points_at_the_working_source(self, manager):
        with pytest.raises(NotImplementedError, match="fastf1"):
            manager.get_session_data(
                source="livef1", year=2024, gp="Bahrain Grand Prix", session_type="R"
            )


class TestDriverShapeIsRecorded:
    """The mismatch, pinned so a future fix has the real shapes to work from."""

    def test_livef1_driver_attribute_names(self):
        from livef1.models.driver import Driver

        parameters = set(Driver.__init__.__code__.co_varnames)

        # What livef1 provides ...
        assert {"RacingNumber", "Tla", "TeamName", "TeamColour"} <= parameters
        # ... and what the old loader asked for.
        assert not {"driver_number", "name_acronym", "team_colour"} & parameters

    def test_the_unified_driver_columns_are_unchanged(self):
        from data.live_adapter import LiveDataProcessor

        frame = LiveDataProcessor.drivers_from_state(
            {"1": {"RacingNumber": "1", "Tla": "VER", "TeamName": "RB", "TeamColour": "3671C6"}}
        )

        assert list(frame.columns) == [
            "driver_number",
            "name_acronym",
            "team_colour",
            "team_name",
            "full_name",
        ]
        assert isinstance(frame, pd.DataFrame)
