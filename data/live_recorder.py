"""Record and replay a raw live-timing stream (IMPROVEMENTS.md LIVE-12).

"Save Raw Stream" used to print advice and save nothing, and saving a *live*
session through the replay path would have written the empty dict a live
session starts from.

The format is the one undercut-f1 uses, and it is deliberately raw:

* ``subscribe.json`` - the subscription snapshot, ``{topic: full_state}``
* ``live.jsonl``     - one ``[topic, data, timestamp]`` array per line

Because the recording holds the messages themselves, a replay feeds exactly
the handler the live client feeds, so the state it rebuilds is the state the
session ended in - no second parsing path to drift out of step.
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Union

SNAPSHOT_FILE = "subscribe.json"
STREAM_FILE = "live.jsonl"


class LiveRecorder:
    """Append-only writer for one recording directory."""

    def __init__(self, directory: Union[str, Path]):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.message_count = 0
        self._lock = threading.Lock()
        self._stream = open(self.directory / STREAM_FILE, "a", encoding="utf-8")

    def record_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Write the subscription completion result."""
        path = self.directory / SNAPSHOT_FILE
        path.write_text(json.dumps(snapshot), encoding="utf-8")

    def record(self, topic: str, payload: Any, timestamp: Optional[str] = None) -> None:
        """Append one feed message, exactly as it arrived."""
        with self._lock:
            if self._stream.closed:
                return
            self._stream.write(json.dumps([topic, payload, timestamp]) + "\n")
            self._stream.flush()  # a crash mid-session should not lose the tail
            self.message_count += 1

    def close(self) -> None:
        with self._lock:
            if not self._stream.closed:
                self._stream.close()

    def __enter__(self) -> "LiveRecorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def replay_recording(directory: Union[str, Path], adapter=None):
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

    snapshot_path = path / SNAPSHOT_FILE
    if snapshot_path.is_file():
        try:
            target.seed_state(json.loads(snapshot_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass

    stream_path = path / STREAM_FILE
    if stream_path.is_file():
        with open(stream_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    topic, payload, timestamp = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue  # torn write at the end of an interrupted session
                target.handle_message(topic, payload, timestamp)
    return target
