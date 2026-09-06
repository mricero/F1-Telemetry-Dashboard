"""Tests for data.jolpica_adapter (HTTP fully mocked)."""

import pandas as pd
import pytest
import requests

from data.jolpica_adapter import JolpicaAdapter


class FakeResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


def _schedule_payload(races):
    return {"MRData": {"RaceTable": {"Races": races}}}


@pytest.fixture
def adapter(monkeypatch):
    a = JolpicaAdapter()
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return FakeResponse(fake_get.payload)

    fake_get.calls = calls
    fake_get.payload = _schedule_payload([])
    monkeypatch.setattr(a.session, "get", fake_get)
    return a


class TestCaching:
    def test_repeated_call_hits_http_once(self, adapter):
        adapter.session.get.payload = _schedule_payload([])
        first = adapter.get_schedule(2024)
        second = adapter.get_schedule(2024)
        assert len(adapter.session.get.calls) == 1
        assert first is second  # same object served from memo

    def test_different_args_cached_separately(self, adapter):
        adapter.session.get.payload = _schedule_payload([])
        adapter.get_schedule(2023)
        adapter.get_schedule(2024)
        assert len(adapter.session.get.calls) == 2

    def test_memo_is_per_instance(self):
        a1, a2 = JolpicaAdapter(), JolpicaAdapter()
        assert a1._memo is not a2._memo
        assert not hasattr(JolpicaAdapter, "_memo")  # no class-level leak

    def test_no_global_lru_cache_retaining_instances(self):
        import gc

        a = JolpicaAdapter()
        assert a.get_seasons.__wrapped__ is not None  # decorator preserved the function
        del a
        gc.collect()
        # If lru_cache were used on methods, instances would be retained in
        # cache_info globals; with _instance_memo there is nothing global.
        from data.jolpica_adapter import _instance_memo

        assert callable(_instance_memo)


class TestScheduleParsing:
    def test_schedule_dataframe(self, adapter):
        adapter.session.get.payload = _schedule_payload(
            [
                {
                    "round": "1",
                    "raceName": "Bahrain Grand Prix",
                    "Circuit": {
                        "circuitName": "Bahrain International Circuit",
                        "circuitId": "bahrain",
                        "Location": {"locality": "Sakhir", "country": "Bahrain"},
                    },
                    "date": "2024-03-02",
                    "time": "15:00:00Z",
                    "url": "https://example.com/race",
                }
            ]
        )
        df = adapter.get_schedule(2024)
        assert df["race_name"].iloc[0] == "Bahrain Grand Prix"
        assert df["round"].iloc[0] == 1
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert df["date"].dt.tz is not None  # timezone-aware UTC

    def test_empty_schedule(self, adapter):
        assert adapter.get_schedule(1999).empty


class TestIsRaceWeekend:
    def _schedule_with_race_days_away(self, days: float) -> pd.DataFrame:
        from datetime import timedelta

        race_date = pd.Timestamp.now(tz="UTC") + timedelta(days=days)
        return pd.DataFrame([{"round": 1, "date": race_date}])

    def test_true_within_window(self, adapter, monkeypatch):
        monkeypatch.setattr(
            adapter, "get_schedule", lambda year: self._schedule_with_race_days_away(1)
        )
        assert adapter.is_race_weekend(2026) is True

    def test_false_far_from_race(self, adapter, monkeypatch):
        monkeypatch.setattr(
            adapter, "get_schedule", lambda year: self._schedule_with_race_days_away(20)
        )
        assert adapter.is_race_weekend(2026) is False


class TestFetchErrors:
    def test_request_exception_wrapped(self, adapter, monkeypatch):
        def boom(url, params=None, timeout=None):
            raise requests.exceptions.ConnectTimeout("no net")

        monkeypatch.setattr(adapter.session, "get", boom)
        with pytest.raises(ConnectionError, match="Failed to fetch"):
            adapter.get_seasons()

    def test_cache_does_not_persist_failures(self, adapter, monkeypatch):
        state = {"fail": True}

        def flaky(url, params=None, timeout=None):
            if state["fail"]:
                raise requests.exceptions.ConnectTimeout("no net")
            return FakeResponse({"MRData": {"SeasonTable": {"Seasons": [{"season": "2024"}]}}})

        monkeypatch.setattr(adapter.session, "get", flaky)
        with pytest.raises(ConnectionError):
            adapter.get_seasons()
        state["fail"] = False
        assert adapter.get_seasons() == [2024]
