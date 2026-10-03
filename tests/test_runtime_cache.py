"""Tests for the ephemeral RuntimeCache."""

from f1dash.data.runtime_cache import RuntimeCache


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


class TestMemoryBudget:
    """CACHE-01: the cache held every session ever viewed, unbounded."""

    @staticmethod
    def _frame(megabytes: int):
        import numpy as np
        import pandas as pd

        rows = (megabytes * 1024 * 1024) // 8
        return pd.DataFrame({"Speed": np.zeros(rows, dtype="float64")})

    def test_entry_count_is_bounded(self):
        cache = RuntimeCache(max_entries=3)
        for i in range(5):
            cache.set(f"k{i}", i)

        assert cache.stats()["entries"] == 3
        assert cache.get("k0") is None and cache.get("k4") == 4

    def test_byte_budget_evicts_least_recently_used(self):
        cache = RuntimeCache(max_entries=10, max_bytes=120 * 1024 * 1024)
        cache.set("a", {"telemetry": {"VER": self._frame(50)}})
        cache.set("b", {"telemetry": {"HAM": self._frame(50)}})
        cache.get("a")  # 'a' is now the more recently used
        cache.set("c", {"telemetry": {"LEC": self._frame(50)}})

        assert cache.get("b") is None, "the least recently used entry should go first"
        assert cache.get("a") is not None and cache.get("c") is not None
        assert cache.stats()["bytes"] <= 120 * 1024 * 1024

    def test_an_oversized_entry_is_not_cached(self):
        cache = RuntimeCache(max_entries=10, max_bytes=10 * 1024 * 1024)
        cache.set("huge", {"telemetry": {"VER": self._frame(50)}})

        assert cache.get("huge") is None
        assert cache.stats()["entries"] == 0

    def test_session_dict_size_counts_frames_deeply(self):
        import pandas as pd

        from f1dash.data.runtime_cache import estimate_bytes

        small = estimate_bytes({"laps": pd.DataFrame({"a": [1, 2, 3]})})
        large = estimate_bytes({"laps": self._frame(8)})

        assert large > 8 * 1024 * 1024 > small
