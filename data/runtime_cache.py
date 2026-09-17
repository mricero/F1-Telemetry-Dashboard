"""Ephemeral runtime cache.

Everything stored here lives ONLY for the lifetime of the app process:
while the dashboard is open, loaded sessions stay hot in memory for
instant switching; when the process exits the cache disappears.

It is deliberately **process-wide, not per browser session**. Streamlit gives
every tab its own ``session_state``, so a cache reset keyed to that would be
wiped whenever anyone opened the app - evicting the sessions it exists to keep
hot for everyone else.

Because a "Full session" race dict runs to hundreds of megabytes, entries are
bounded by both a count and a byte budget and evicted least-recently-used.

Derived *metrics* (fastest lap, fastest sectors, top speed) are persisted
separately by :mod:`processing.metrics_store` and therefore survive
restarts.
"""

import os
import sys
import threading
import time
from collections import OrderedDict
from typing import Any

import pandas as pd

# Defaults sized for a laptop: a couple of full-session race dicts.
DEFAULT_MAX_ENTRIES = int(os.getenv("F1_CACHE_MAX_ENTRIES", "8"))
DEFAULT_MAX_BYTES = int(os.getenv("F1_CACHE_MAX_BYTES", str(1024 * 1024 * 1024)))


def estimate_bytes(value: Any) -> int:
    """Approximate memory footprint of a cached value.

    Session dicts are mostly DataFrames, whose ``memory_usage(deep=True)``
    accounts for object columns properly; everything else falls back to
    ``sys.getsizeof``, which is enough to keep the budget honest.
    """
    if isinstance(value, pd.DataFrame):
        return int(value.memory_usage(deep=True).sum())
    if isinstance(value, pd.Series):
        return int(value.memory_usage(deep=True))
    if isinstance(value, dict):
        return sys.getsizeof(value) + sum(
            estimate_bytes(k) + estimate_bytes(v) for k, v in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return sys.getsizeof(value) + sum(estimate_bytes(v) for v in value)
    return sys.getsizeof(value)


class RuntimeCache:
    """Thread-safe, LRU-bounded in-memory cache scoped to the app process."""

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._created_at: dict[str, float] = {}
        self._sizes: dict[str, int] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.session_started_at: float = time.time()

    def begin_session(self) -> None:
        """Drop everything and reset the counters.

        This is an explicit "clear the cache" action. It must **not** be wired
        to a browser session opening - see the module docstring.
        """
        with self._lock:
            self._store.clear()
            self._created_at.clear()
            self._sizes.clear()
            self._hits = 0
            self._misses = 0
            self.session_started_at = time.time()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._store:
                self._hits += 1
                self._store.move_to_end(key)  # most recently used
                return self._store[key]
            self._misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        size = estimate_bytes(value)
        with self._lock:
            self._discard(key)
            if size > self.max_bytes:
                # Caching it would immediately evict everything else.
                return
            self._store[key] = value
            self._created_at[key] = time.time()
            self._sizes[key] = size
            self._evict_to_budget()

    def remove(self, key: str) -> None:
        with self._lock:
            self._discard(key)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._created_at.clear()
            self._sizes.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._store),
                "keys": sorted(self._store.keys()),
                "bytes": sum(self._sizes.values()),
                "max_bytes": self.max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "age_seconds": round(time.time() - self.session_started_at, 1),
            }

    # -- internals (callers hold the lock) ---------------------------------

    def _discard(self, key: str) -> None:
        self._store.pop(key, None)
        self._created_at.pop(key, None)
        self._sizes.pop(key, None)

    def _evict_to_budget(self) -> None:
        while self._store and (
            len(self._store) > self.max_entries or sum(self._sizes.values()) > self.max_bytes
        ):
            oldest = next(iter(self._store))
            self._discard(oldest)

    @staticmethod
    def make_key(*parts) -> str:
        """Build a stable cache key from arbitrary hashable parts."""
        return "|".join(str(p) for p in parts)


# Module-level singleton - shared across Streamlit reruns *and* browser
# sessions within one process.
runtime_cache = RuntimeCache()
