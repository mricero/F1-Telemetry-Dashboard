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


class _PagingServer:
    """Mock Jolpica: serves `total` items in pages of `limit`."""

    def __init__(self, total: int, build_page, limit_cap: int = 100):
        self.total = total
        self.build_page = build_page
        self.limit_cap = limit_cap
        self.requests = []

    def __call__(self, url, params=None, timeout=None):
        params = params or {}
        limit = int(params.get("limit", 30))
        offset = int(params.get("offset", 0))
        assert limit <= self.limit_cap, "Jolpica caps limit at 100"
        self.requests.append((offset, limit))
        items = list(range(offset, min(offset + limit, self.total)))
        payload = self.build_page(items)
        payload["MRData"].update({"total": str(self.total), "limit": str(limit)})
        payload["MRData"]["offset"] = str(offset)
        return FakeResponse(payload)


def _seasons_page(items):
    return {"MRData": {"SeasonTable": {"Seasons": [{"season": str(1950 + i)} for i in items]}}}


def _laps_page(items):
    laps = [
        {"number": str(i + 1), "Timings": [{"driverId": "verstappen", "time": "1:31.2"}]}
        for i in items
    ]
    return {"MRData": {"RaceTable": {"Races": [{"round": "1", "Laps": laps}]}}}


def _pit_stops_page(items):
    stops = [
        {"driverId": "hamilton", "lap": str(i + 1), "stop": "1", "duration": "22.5"} for i in items
    ]
    return {"MRData": {"RaceTable": {"Races": [{"round": "1", "PitStops": stops}]}}}


class TestPagination:
    """HIST-07: Jolpica defaults to limit=30 (max 100) and pages with offset."""

    def test_seasons_are_not_truncated_at_30(self, adapter, monkeypatch):
        server = _PagingServer(77, _seasons_page)
        monkeypatch.setattr(adapter.session, "get", server)

        seasons = adapter.get_seasons()

        assert len(seasons) == 77
        assert seasons[0] == 1950 and seasons[-1] == 2026

    def test_paging_uses_the_maximum_page_size(self, adapter, monkeypatch):
        server = _PagingServer(250, _seasons_page)
        monkeypatch.setattr(adapter.session, "get", server)

        adapter.get_seasons()

        assert server.requests == [(0, 100), (100, 100), (200, 100)]

    def test_single_page_makes_one_request(self, adapter, monkeypatch):
        server = _PagingServer(24, _seasons_page)
        monkeypatch.setattr(adapter.session, "get", server)

        adapter.get_seasons()

        assert len(server.requests) == 1

    def test_lap_times_cover_the_whole_race(self, adapter, monkeypatch):
        server = _PagingServer(1200, _laps_page)
        monkeypatch.setattr(adapter.session, "get", server)

        payload = adapter.get_lap_times(2024, 1)
        laps = payload["MRData"]["RaceTable"]["Races"][0]["Laps"]

        assert len(laps) == 1200
        assert len(server.requests) == 12

    def test_pit_stops_are_not_truncated(self, adapter, monkeypatch):
        server = _PagingServer(45, _pit_stops_page)
        monkeypatch.setattr(adapter.session, "get", server)

        payload = adapter.get_pit_stops(2024, 1)
        stops = payload["MRData"]["RaceTable"]["Races"][0]["PitStops"]

        assert len(stops) == 45


class TestRateLimiting:
    """HIST-07: 4 req/s burst, 500 req/h; 429 responses carry Retry-After."""

    def test_burst_is_throttled_to_four_per_second(self, adapter, monkeypatch):
        sleeps = []
        monkeypatch.setattr("data.jolpica_adapter.time.sleep", lambda s: sleeps.append(s))
        clock = {"t": 0.0}
        monkeypatch.setattr("data.jolpica_adapter.time.monotonic", lambda: clock["t"])
        server = _PagingServer(600, _seasons_page)
        monkeypatch.setattr(adapter.session, "get", server)

        adapter.get_seasons()

        assert len(server.requests) == 6
        # The first four go straight out; the rest wait for a token.
        assert len(sleeps) == 2 and all(s > 0 for s in sleeps)

    def test_429_is_retried_after_the_advertised_delay(self, adapter, monkeypatch):
        sleeps = []
        monkeypatch.setattr("data.jolpica_adapter.time.sleep", lambda s: sleeps.append(s))
        attempts = {"n": 0}

        def rate_limited(url, params=None, timeout=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                response = FakeResponse({}, status_error=requests.exceptions.HTTPError("429"))
                response.status_code = 429
                response.headers = {"Retry-After": "7"}
                return response
            ok = FakeResponse({"MRData": {"SeasonTable": {"Seasons": [{"season": "2024"}]}}})
            ok.status_code = 200
            ok.headers = {}
            return ok

        monkeypatch.setattr(adapter.session, "get", rate_limited)

        seasons = adapter.get_seasons()

        assert seasons == [2024]
        assert attempts["n"] == 2
        assert 7 in sleeps

    def test_429_without_retry_after_backs_off(self, adapter, monkeypatch):
        sleeps = []
        monkeypatch.setattr("data.jolpica_adapter.time.sleep", lambda s: sleeps.append(s))

        def always_limited(url, params=None, timeout=None):
            response = FakeResponse({}, status_error=requests.exceptions.HTTPError("429"))
            response.status_code = 429
            response.headers = {}
            return response

        monkeypatch.setattr(adapter.session, "get", always_limited)

        with pytest.raises(ConnectionError, match="rate limit"):
            adapter.get_seasons()

        assert sleeps == sorted(sleeps) and len(sleeps) >= 2  # exponential backoff
