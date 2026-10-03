"""HIST-09: a session that has just run is not in F1's archive yet, or only
partly. It must say so, and must not be held in the runtime cache."""

import pandas as pd
import pytest
from fastf1.exceptions import DataNotLoadedError

from f1dash.data.fastf1_adapter import FastF1Adapter, SessionNotArchivedError


class _Unloaded:
    """FastF1's Session after a soft-failed lap load: ``laps`` raises."""

    @property
    def laps(self):
        raise DataNotLoadedError(
            "The data you are trying to access has not been loaded yet. See `Session.load`"
        )


class TestNotArchivedYet:
    def test_a_session_without_laps_says_it_is_not_archived(self):
        with pytest.raises(SessionNotArchivedError, match="not in F1's archive yet"):
            FastF1Adapter._require_laps(_Unloaded(), 2026, "Singapore", "Q")

    def test_empty_laps_say_the_same(self):
        class Empty:
            laps = pd.DataFrame()

        with pytest.raises(SessionNotArchivedError, match="usually 1-2 h"):
            FastF1Adapter._require_laps(Empty(), 2026, "Singapore", "Q")


class TestEndedRecently:
    NOW = pd.Timestamp("2026-10-10T16:30:00Z")

    def test_a_qualifying_that_ended_an_hour_ago_is_recent(self):
        # Started 14:00, about an hour long, so it ended ~15:00.
        assert FastF1Adapter.ended_recently("2026-10-10T14:00:00Z", "Qualifying", now=self.NOW)

    def test_last_weeks_race_is_settled(self):
        assert not FastF1Adapter.ended_recently("2026-10-03T13:00:00Z", "Race", now=self.NOW)

    def test_an_unknown_date_is_treated_as_settled(self):
        assert not FastF1Adapter.ended_recently(None, "Race", now=self.NOW)


class TestRuntimeCaching:
    def _session(self, source="fastf1", is_live=False, date=None):
        return {
            "source": source,
            "is_live": is_live,
            "session_info": {"date": date, "session_name": "Qualifying"},
        }

    def test_a_recent_session_is_not_cached(self, monkeypatch):
        from f1dash import app

        monkeypatch.setattr(FastF1Adapter, "ended_recently", staticmethod(lambda d, n: True))
        assert not app.should_runtime_cache(self._session())

    def test_a_settled_session_is_cached(self, monkeypatch):
        from f1dash import app

        monkeypatch.setattr(FastF1Adapter, "ended_recently", staticmethod(lambda d, n: False))
        assert app.should_runtime_cache(self._session())

    def test_live_and_replays(self):
        from f1dash import app

        assert not app.should_runtime_cache(self._session(source="live", is_live=True))
        assert app.should_runtime_cache(self._session(source="replay"))
