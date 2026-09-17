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
# Topics sampled both at the start (for the keyframe) and mid-session (where
# the interesting running is). Without the mid-session window a race fixture
# holds only the formation lap: no lap completions, no car movement.
WINDOWED_TOPICS = ("CarData.z", "Position.z", "TimingData", "TimingAppData")
# Streams run from well before a session starts, so the opening messages are
# cars in the garage reporting 0,0,0 and timing lines with no laps. Each topic
# has a different message rate, so the interesting window is chosen by the
# stream's own session-relative clock rather than by message offset.
DEFAULT_WINDOW = ("01:25:00", "01:28:00")
# Messages kept from the very start, so the keyframe is never lost.
TELEMETRY_HEAD = 12


def _get(url: str, timeout: int = 60) -> bytes | None:
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


def parse_stream(text: str) -> list[list]:
    """``.jsonStream`` is ``<12-char timestamp><json>`` per line."""
    records: list[list] = []
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


def select(records: list[list], topic: str, cap: int, window: tuple) -> list[list]:
    """Which recorded messages to keep: the keyframe plus a session window."""
    if topic not in WINDOWED_TOPICS:
        return records[:cap]
    start, end = window
    head = records[:TELEMETRY_HEAD]
    body = [record for record in records if start <= str(record[0]) <= end]
    return head + body[: max(cap - len(head), 0)]


def capture(
    year: int, meeting: str, session: str, cap: int, window: tuple = DEFAULT_WINDOW
) -> dict[str, int]:
    path = find_session(year, meeting, session)
    print(f"session path: {path}")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}

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
        records = select(parse_stream(_decode(raw)), topic, cap, window)
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
        "window": list(window),
        "topics": written,
    }
    (FIXTURE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return written


def _write(topic: str, records: list[list]) -> None:
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
        "--window",
        nargs=2,
        metavar=("START", "END"),
        default=list(DEFAULT_WINDOW),
        help="session-relative HH:MM:SS range to sample the running session "
        "from (the opening messages are cars in the garage)",
    )
    args = parser.parse_args()

    written = capture(args.year, args.meeting, args.session, args.messages, tuple(args.window))
    total = sum(f.stat().st_size for f in FIXTURE_DIR.glob("*.gz"))
    print(f"\n{len(written)} topics, {total / 1024:.1f} KiB total in {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
