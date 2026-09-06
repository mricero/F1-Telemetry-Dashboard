"""Ephemeral runtime cache.

Everything stored here lives ONLY for the lifetime of the app process:
while the dashboard is open, loaded sessions stay hot in memory for
instant switching; when the app is closed the cache disappears, and the
next launch starts completely fresh.

Derived *metrics* (fastest lap, fastest sectors, top speed) are persisted
separately by :mod:`processing.metrics_store` and therefore survive
restarts.
"""

import threading
import time
from typing import Any, Dict, Optional


class RuntimeCache:
    """Thread-safe in-memory key/value cache scoped to one app session."""

    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._created_at: Dict[str, float] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        self.session_started_at: float = time.time()

    def begin_session(self) -> None:
        """Reset all state. Called once at app startup so every app opening
        starts from a clean slate."""
        with self._lock:
            self._store.clear()
            self._created_at.clear()
            self._hits = 0
            self._misses = 0
            self.session_started_at = time.time()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._store:
                self._hits += 1
                return self._store[key]
            self._misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value
            self._created_at[key] = time.time()

    def remove(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
            self._created_at.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._created_at.clear()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._store),
                "keys": sorted(self._store.keys()),
                "hits": self._hits,
                "misses": self._misses,
                "age_seconds": round(time.time() - self.session_started_at, 1),
            }

    @staticmethod
    def make_key(*parts) -> str:
        """Build a stable cache key from arbitrary hashable parts."""
        return "|".join(str(p) for p in parts)


# Module-level singleton - shared across Streamlit reruns within one process.
runtime_cache = RuntimeCache()
