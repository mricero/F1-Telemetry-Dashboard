"""Record a slice of a real F1 live-timing session as a test fixture.

The hand-written live fixtures in this repo encoded the code's assumptions
rather than the feed, which is exactly how the LIVE-03..06 bugs passed CI
(IMPROVEMENTS.md TEST-01). This script pulls the **real** payloads from the
public static archive - the same files FastF1 reads for historical sessions -
so tests can replay genuine messages through the ingest handler.

    python scripts/capture_fixture.py --year 2023 --meeting "Bahrain" --session Race

Fixtures are gzipped JSON Lines of ``[timestamp, payload]`` pairs and are
truncated hard: they exist to pin *shapes*, not to mirror a whole session.
"""

import argparse
import gzip
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

BASE_URL = "https://livetiming.formula1.com/static"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "live"

# Topics worth pinning. `.z` topics are base64 + raw DEFLATE and stay tiny
# because only the first few messages are kept.
STREAM_TOPICS = (
    "TimingData",
    "TimingAppData",
    "TyreStintSeries",
    "DriverList",
    "RaceControlMessages",
    "WeatherData",
    "TrackStatus",
    "LapCount",
    "SessionStatus",
    "ExtrapolatedClock",
    "CarData.z",
    "Position.z",
)
# Keyframe (non-streamed) topics: one full snapshot.
SNAPSHOT_TOPICS = ("SessionInfo",)

DEFAULT_MESSAGE_CAP = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (F1-Telemetry-Dashboard fixture capture)"}

# The first messages of a session are the keyframe, which is the whole point
# for state-bearing topics. For car telemetry and positions they are cars
# sitting in the garage reporting 0,0,0, so a mid-session window is taken as
# well - keeping a few of the garage samples, which are their own edge case.
TELEMETRY_TOPICS = ("CarData.z", "Position.z")
# Cars only start reporting real coordinates once they leave the garage, which
# at Bahrain 2023 is ~4700 messages into a ~9500-message race stream.
DEFAULT_TELEMETRY_SKIP = 4750
TELEMETRY_HEAD = 6


def _get(url: str, timeout: int = 60) -> Optional[bytes]:
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        print(f"  ! {url} -> HTTP {exc.code}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"  ! {url} -> {exc.reason}", file=sys.stderr)
    return None


def _decode(raw: bytes) -> str:
    """The archive serves UTF-16 for streams and UTF-8-BOM for keyframes."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def find_session(year: int, meeting: str, session: str) -> str:
    """Path of one session in the archive, e.g. ``2023/..._Race/``."""
    raw = _get(f"{BASE_URL}/{year}/Index.json")
    if raw is None:
        raise SystemExit(f"cannot read the {year} index")
    index = json.loads(_decode(raw))
    for entry in index.get("Meetings", []):
        if meeting.lower() not in str(entry.get("Name", "")).lower():
            continue
        for candidate in entry.get("Sessions", []):
            if session.lower() in str(candidate.get("Name", "")).lower():
                path = candidate.get("Path")
                if path:
                    return str(path)
    raise SystemExit(f"no session matching {meeting!r}/{session!r} in {year}")


def parse_stream(text: str) -> List[list]:
    """``.jsonStream`` is ``<12-char timestamp><json>`` per line."""
    records: List[list] = []
    for line in text.splitlines():
        if not line.strip() or len(line) < 13:
            continue
        timestamp, payload = line[:12], line[12:]
        try:
            records.append([timestamp, json.loads(payload)])
        except json.JSONDecodeError:
            # `.z` payloads are quoted base64; keep them as the string they are.
            records.append([timestamp, payload.strip('"')])
    return records


def select(records: List[list], topic: str, cap: int, skip: int) -> List[list]:
    """Which recorded messages to keep for a topic."""
    if topic not in TELEMETRY_TOPICS or len(records) <= skip:
        return records[:cap]
    head = records[:TELEMETRY_HEAD]
    body = records[skip : skip + max(cap - TELEMETRY_HEAD, 0)]
    return head + body


def capture(
    year: int, meeting: str, session: str, cap: int, skip: int = DEFAULT_TELEMETRY_SKIP
) -> Dict[str, int]:
    path = find_session(year, meeting, session)
    print(f"session path: {path}")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written: Dict[str, int] = {}

    for topic in SNAPSHOT_TOPICS:
        raw = _get(f"{BASE_URL}/{path}{topic}.json")
        if raw is None:
            continue
        payload = json.loads(_decode(raw))
        _write(topic, [["00:00:00.000", payload]])
        written[topic] = 1

    for topic in STREAM_TOPICS:
        raw = _get(f"{BASE_URL}/{path}{topic}.jsonStream")
        if raw is None:
            continue
        records = select(parse_stream(_decode(raw)), topic, cap, skip)
        if not records:
            continue
        _write(topic, records)
        written[topic] = len(records)

    manifest = {
        "source": f"{BASE_URL}/{path}",
        "year": year,
        "meeting": meeting,
        "session": session,
        "message_cap": cap,
        "telemetry_skip": skip,
        "topics": written,
    }
    (FIXTURE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return written


def _write(topic: str, records: List[list]) -> None:
    target = FIXTURE_DIR / f"{topic}.jsonl.gz"
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    size = target.stat().st_size
    print(f"  {topic:22s} {len(records):4d} messages  {size / 1024:6.1f} KiB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--meeting", default="Bahrain")
    parser.add_argument("--session", default="Race")
    parser.add_argument("--messages", type=int, default=DEFAULT_MESSAGE_CAP)
    parser.add_argument(
        "--telemetry-skip",
        type=int,
        default=DEFAULT_TELEMETRY_SKIP,
        help="messages to skip before sampling CarData.z/Position.z (cars are "
        "in the garage at 0,0,0 at the start of a stream)",
    )
    args = parser.parse_args()

    written = capture(args.year, args.meeting, args.session, args.messages, args.telemetry_skip)
    total = sum(f.stat().st_size for f in FIXTURE_DIR.glob("*.gz"))
    print(f"\n{len(written)} topics, {total / 1024:.1f} KiB total in {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
