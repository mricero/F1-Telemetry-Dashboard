"""Loader for the recorded live-timing fixtures (IMPROVEMENTS.md TEST-01).

The files under ``tests/fixtures/live`` are real messages pulled from F1's
public static archive by ``scripts/capture_fixture.py`` - the same archive
FastF1 reads for historical sessions. Hand-written fixtures encoded the code's
assumptions instead of the feed, which is how the LIVE-03..06 bugs passed CI.
"""

import gzip
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "live"

Message = Tuple[str, Any]


@lru_cache(maxsize=None)
def messages(topic: str) -> List[Message]:
    """Recorded ``(timestamp, payload)`` messages for one topic."""
    path = FIXTURE_DIR / f"{topic}.jsonl.gz"
    if not path.is_file():
        raise FileNotFoundError(f"no recorded fixture for {topic!r} in {FIXTURE_DIR}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [tuple(json.loads(line)) for line in handle if line.strip()]


def payloads(topic: str) -> List[Any]:
    """Just the payloads, in recorded order."""
    return [payload for _, payload in messages(topic)]


def first_payload(topic: str) -> Any:
    return payloads(topic)[0]


@lru_cache(maxsize=None)
def manifest() -> Dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def available_topics() -> List[str]:
    return sorted(path.name.split(".jsonl.gz")[0] for path in FIXTURE_DIR.glob("*.jsonl.gz"))
