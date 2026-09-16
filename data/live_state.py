"""Merged state for the live timing feed (IMPROVEMENTS.md LIVE-05).

Most topics are **keyframe + partial update** streams: subscribing returns
the full state, and every later message carries only what changed, sometimes
with ``_deleted`` markers. Buffering each message as a standalone record -
which is what this app used to do - loses the keyframe entirely and drops
every partial that lacks the fields the parser looked for. Live stints were
mostly empty, ``TimingData`` fields went stale, and joining mid-session lost
anything that had only ever appeared in the snapshot.

The merge here is the approach f1-dash, undercut-f1 and matteocelani's
f1-telemetry all converged on.

Only *state* belongs here. True time series - ``CarData.z``, ``Position.z``,
``RaceControlMessages``, ``TeamRadio``, weather samples - stay append-only in
the adapter's buffers.
"""

import copy
import threading
from typing import Any, Dict, List, Optional

# Topics whose messages are merged into state rather than appended. Everything
# else the adapter sees is a time series.
STATE_TOPICS = frozenset(
    {
        "SessionInfo",
        "SessionStatus",
        "SessionData",
        "TrackStatus",
        "LapCount",
        "ExtrapolatedClock",
        "DriverList",
        "TimingData",
        "TimingAppData",
        "TimingStats",
        "TopThree",
        "TyreStintSeries",
        "CurrentTyres",
        "PitLaneTimeCollection",
        "ChampionshipPrediction",
        "DriverRaceInfo",
    }
)

# The feed marks removals with this key rather than by omission.
DELETED_KEY = "_deleted"


def as_list(value: Any) -> List[Any]:
    """Normalise an index-keyed dict back to an ordered list.

    Deltas address list items by position (``{"1": {...}}`` is the *second*
    entry), so state that started life as a list can end up as a dict of
    numeric string keys. Readers call this to get the list back; gaps become
    ``None`` so positions never shift.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        keys = list(value)
        if keys and all(str(key).isdigit() for key in keys):
            indices = [int(key) for key in keys]
            ordered: List[Any] = [None] * (max(indices) + 1)
            for key, index in zip(keys, indices):
                ordered[index] = value[key]
            return ordered
        return [value]
    return [value]


def deep_merge(base: Dict, update: Dict) -> Dict:
    """Recursively merge ``update`` into ``base``, in place, and return it.

    Dicts merge key by key; a dict arriving where the base holds a **list**
    is an index-addressed delta, so the list is converted to an index map
    first. ``_deleted`` lists name keys to drop. Values are deep-copied, so
    the caller can keep mutating the message it handed over.
    """
    for key, value in update.items():
        if key == DELETED_KEY:
            for deleted in value or []:
                base.pop(str(deleted), None)
            continue

        target = base.get(key)
        if isinstance(value, dict) and isinstance(target, (dict, list)):
            if isinstance(target, list):
                # A delta indexes into the list; keep it addressable by key.
                target = {str(index): item for index, item in enumerate(target)}
            base[key] = deep_merge(target, value)
        else:
            base[key] = copy.deepcopy(value)
    return base


class LiveState:
    """One merged dict per topic, safe to update from the client thread."""

    def __init__(self) -> None:
        self._topics: Dict[str, Dict] = {}
        self._version = 0
        self._lock = threading.Lock()

    @property
    def version(self) -> int:
        """Bumped on every update, so readers can skip unchanged state."""
        with self._lock:
            return self._version

    def update(self, topic: str, payload: Optional[Dict]) -> None:
        """Merge one message (keyframe or delta) into the topic's state."""
        if not isinstance(payload, dict):
            return
        with self._lock:
            deep_merge(self._topics.setdefault(topic, {}), payload)
            self._version += 1

    def seed(self, snapshot: Dict[str, Any]) -> None:
        """Apply a subscription completion result: ``{topic: full_state}``."""
        for topic, payload in (snapshot or {}).items():
            self.update(topic, payload)

    def get(self, topic: str) -> Dict:
        """A deep copy of one topic's state, so readers cannot mutate it."""
        with self._lock:
            return copy.deepcopy(self._topics.get(topic, {}))

    def snapshot(self) -> Dict[str, Dict]:
        """A deep copy of everything, for building an immutable view."""
        with self._lock:
            return copy.deepcopy(self._topics)

    def clear(self) -> None:
        with self._lock:
            self._topics.clear()
            self._version += 1
