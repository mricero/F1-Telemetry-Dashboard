"""Live SignalR Core smoke test (manual; meaningful during a session weekend).

Connects to F1's live timing hub at ``wss://livetiming.formula1.com/signalrcore``
with the app's own client for N seconds and prints what arrived per topic, the
connection state as it changes, and what the dashboard would show - the
fastest way to check connectivity, the token and parsing before a session:

    .venv/Scripts/python scripts/live_smoke.py [seconds] [--record DIR]

Between sessions the hub still answers: the subscription snapshot holds the
last session's final state, then only pings arrive (state WAITING). During a
session the state turns LIVE and TimingData, TrackStatus, WeatherData ...
start counting. Set F1TV_SUBSCRIPTION_TOKEN to also receive CarData.z and
Position.z.
"""

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config  # noqa: F401  (loads .env: F1TV_SUBSCRIPTION_TOKEN)
from data.live_adapter import SignalRLiveAdapter, subscription_token
from data.signalr_core import token_expiry


def main(duration: int = 30, record: str | None = None) -> int:
    adapter = SignalRLiveAdapter()
    token = subscription_token()
    if token:
        expiry = token_expiry(token)
        if expiry is None:
            print("Subscription token: set (expiry unknown)")
        elif expiry <= datetime.now(UTC):
            print(
                f"WARNING: the subscription token expired {expiry:%Y-%m-%d %H:%M} UTC. "
                "It will not be sent; timing, race control and weather still arrive. "
                "Renew F1TV_SUBSCRIPTION_TOKEN for car data and positions."
            )
        else:
            print(f"Subscription token: set (expires {expiry:%Y-%m-%d %H:%M} UTC)")
    else:
        print("Subscription token: not set - CarData.z / Position.z will not arrive")
    if record:
        adapter.start_recording(record)
        print(f"Recording the raw stream to {record}")

    print(f"Connecting to wss://livetiming.formula1.com/signalrcore for {duration}s ...")
    adapter.start_async()
    started = time.time()
    last_status = None
    try:
        while time.time() - started < duration:
            time.sleep(1)
            status = adapter.status()
            if status is not last_status:
                print(f"[{time.time() - started:5.1f}s] {adapter.status_text()}")
                last_status = status
    except KeyboardInterrupt:
        pass
    finally:
        adapter.stop()
        adapter.stop_recording()

    stats = adapter.client.stats if adapter.client is not None else None
    print("\n--- messages per topic ---")
    if stats is None or not stats.per_topic:
        print("(no feed messages - no session on air, or the connection was refused)")
    else:
        for topic, count in sorted(stats.per_topic.items()):
            print(f"{topic:<24} {count:>6}")
        print(f"snapshots: {stats.snapshots}  reconnects: {stats.reconnects}")
    if stats is not None and stats.last_error:
        print(f"last error: {stats.last_error}")
    notice = getattr(adapter.client, "token_notice", None)
    if notice:
        print(f"token: {notice}")
    if adapter.recorder_error:
        print(adapter.recorder_error)

    print("\n--- what the dashboard would show ---")
    from data.source_manager import DataSourceManager

    manager = DataSourceManager(live_adapter=adapter)
    snapshot = manager.poll_live_data()
    info = snapshot["session_info"]
    print(f"session: {info.get('gp')} - {info.get('session_name')} ({info.get('status')})")
    print(f"drivers: {len(snapshot['drivers'])}  laps: {len(snapshot['laps'])}")
    standings = snapshot.get("standings")
    if standings is not None and not standings.empty:
        print(
            standings[["Position", "Driver", "Gap", "Interval", "Status"]]
            .head(10)
            .to_string(index=False)
        )
    print(f"race control messages: {len(snapshot['race_control'])}")
    print(
        f"car telemetry for {len(snapshot['telemetry'])} driver(s), "
        f"positions for {len(snapshot['location'])}"
    )
    return 0 if stats is not None and (stats.snapshots or stats.messages) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("seconds", nargs="?", type=int, default=30)
    parser.add_argument("--record", help="directory to record the raw stream into")
    args = parser.parse_args()
    raise SystemExit(main(args.seconds, args.record))
