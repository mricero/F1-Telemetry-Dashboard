"""Record and replay a raw live-timing stream (IMPROVEMENTS.md LIVE-12).

"Save Raw Stream" used to print advice and save nothing, and saving a *live*
session through the replay path would have written the empty dict a live
session starts from.

The format is the one undercut-f1 uses, and it is deliberately raw:

* ``subscribe.json`` - the first subscription snapshot, ``{topic: full_state}``
* ``live.jsonl``     - one ``[topic, data, timestamp]`` array per line; every
  snapshot (the first and each reconnect's) is also written in place as
  ``["__snapshot__", {topic: full_state}, utc]`` (LIVE-32)

Because the recording holds the messages themselves, a replay feeds exactly
the handler the live client feeds, so the state it rebuilds is the state the
session ended in - no second parsing path to drift out of step.
"""

import contextlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SNAPSHOT_FILE = "subscribe.json"
STREAM_FILE = "live.jsonl"
# The topic name of an inline snapshot line in live.jsonl.
SNAPSHOT_MARKER = "__snapshot__"


class LiveRecorder:
    """Append-only writer for one recording directory."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.message_count = 0
        self._lock = threading.Lock()
        # Held open for the length of the session and closed by close();
        # a context manager per message would reopen it thousands of times.
        self._stream = open(self.directory / STREAM_FILE, "a", encoding="utf-8")  # noqa: SIM115

    def record_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Write a subscription completion result where it happened.

        A reconnect's snapshot is the state *at that moment*: overwriting
        ``subscribe.json`` with it and replaying every older message after it
        ended a replay in the wrong state (an SC ending missed during an
        outage stayed deployed). It goes inline into the stream instead;
        ``subscribe.json`` keeps the first one for older readers.
        """
        path = self.directory / SNAPSHOT_FILE
        if not path.exists():
            path.write_text(json.dumps(snapshot), encoding="utf-8")
        self._write([SNAPSHOT_MARKER, snapshot, datetime.now(UTC).isoformat()])

    def record(self, topic: str, payload: Any, timestamp: str | None = None) -> None:
        """Append one feed message, exactly as it arrived."""
        if self._write([topic, payload, timestamp]):
            self.message_count += 1

    def _write(self, entry: list) -> bool:
        with self._lock:
            if self._stream.closed:
                return False
            self._stream.write(json.dumps(entry) + "\n")
            self._stream.flush()  # a crash mid-session should not lose the tail
            return True

    def close(self) -> None:
        with self._lock:
            if not self._stream.closed:
                self._stream.close()

    def __enter__(self) -> "LiveRecorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def replay_recording(directory: str | Path, adapter=None):
    """Rebuild adapter state from a recording, and return the adapter.

    Feeds the recorded messages through the live ingest path in order. A torn
    final line - the process died mid-write - is skipped rather than aborting
    the replay.
    """
    from data.live_adapter import SignalRLiveAdapter

    path = Path(directory)
    if not path.is_dir():
        raise FileNotFoundError(f"no recording at {path}")

    target = adapter if adapter is not None else SignalRLiveAdapter()

    entries = []
    stream_path = path / STREAM_FILE
    if stream_path.is_file():
        with open(stream_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    topic, payload, timestamp = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue  # torn write at the end of an interrupted session
                entries.append((topic, payload, timestamp))

    # Recordings made before LIVE-32 hold their only snapshot in
    # subscribe.json; newer ones carry every snapshot inline.
    inline = any(topic == SNAPSHOT_MARKER for topic, _, _ in entries)
    snapshot_path = path / SNAPSHOT_FILE
    if not inline and snapshot_path.is_file():
        with contextlib.suppress(json.JSONDecodeError):
            target.seed_state(json.loads(snapshot_path.read_text(encoding="utf-8")))

    for topic, payload, timestamp in entries:
        if topic == SNAPSHOT_MARKER:
            if isinstance(payload, dict):
                target.seed_state(payload)
        else:
            target.handle_message(topic, payload, timestamp)
    return target
