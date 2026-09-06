"""Tests for the ephemeral RuntimeCache."""

from data.runtime_cache import RuntimeCache


class TestRuntimeCache:
    def test_set_get(self):
        cache = RuntimeCache()
        payload = {"telemetry": {"VER": "df"}}
        cache.set("k1", payload)
        assert cache.get("k1") == payload

    def test_miss_returns_none_and_counts(self):
        cache = RuntimeCache()
        assert cache.get("nope") is None
        stats = cache.stats()
        assert stats["misses"] == 1

    def test_hits_counted(self):
        cache = RuntimeCache()
        cache.set("k", 1)
        cache.get("k")
        cache.get("k")
        assert cache.stats()["hits"] == 2

    def test_begin_session_clears_everything(self):
        cache = RuntimeCache()
        cache.set("a", 123)
        cache.get("missing")  # bump misses
        cache.begin_session()
        stats = cache.stats()
        assert cache.get("a") is None
        assert stats["entries"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    def test_remove(self):
        cache = RuntimeCache()
        cache.set("x", [1])
        cache.remove("x")
        assert cache.get("x") is None

    def test_make_key_stable(self):
        a = RuntimeCache.make_key("session", {"year": 2024}, "R")
        b = RuntimeCache.make_key("session", {"year": 2024}, "R")
        c = RuntimeCache.make_key("session", {"year": 2023}, "R")
        assert a == b
        assert a != c
