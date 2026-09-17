"""Live SignalR end-to-end smoke test (manual, run during a race weekend).

Connects to the official F1 live timing feed for N seconds and prints what
actually arrived per topic - the fastest way to verify auth, buffering rates
and parsing before/during a live session:

    .venv/Scripts/python scripts/live_smoke.py [seconds]

Optionally records a raw stream file (replayable via
fastf1.livetiming.messages_from_raw / LiveDataProcessor.decode_topic_payload):

    F1_LIVE_LOG=raw.txt python scripts/live_smoke.py 30
"""

import asyncio
import sys
import time
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from data.live_adapter import LiveDataProcessor, SignalRLiveAdapter  # noqa: E402


def main(duration: int = 30):
    adapter = SignalRLiveAdapter()
    counts: Counter = Counter()

    adapter.register_callback("CarData.z", lambda d: counts.update(["CarData.z"]))
    # LiveF1 targets the legacy /signalr/ hub, not /signalrcore (LIVE-01).
    print(f"Connecting to F1 live timing (legacy /signalr/ hub) for {duration}s ...")
    log_file = __import__("os").environ.get("F1_LIVE_LOG")
    adapter.start_async(log_file=log_file)

    t0 = time.time()
    try:
        while time.time() - t0 < duration:
            time.sleep(2)
            if adapter.last_error():
                print(f"ERROR from live thread: {adapter.last_error()!r}")
                return 1
            print(f"[{time.time() - t0:5.1f}s] running={adapter.is_running()}")
    except KeyboardInterrupt:
        pass

    print("\n--- buffer summary ---")
    for topic in SignalRLiveAdapter.TELEMETRY_TOPICS:
        n = len(adapter.get_buffered_data(topic))
        if n:
            counts[topic] += n
            print(f"{topic:<24} {n:>6} records")

    car = LiveDataProcessor.parse_car_data(adapter.get_buffered_data("CarData.z"))
    pos = LiveDataProcessor.parse_position_data(adapter.get_buffered_data("Position.z"))
    drv = LiveDataProcessor.parse_driver_list(adapter.get_buffered_data("DriverList"))
    print(
        f'\nCarData rows: {len(car)} ({car["driver_number"].nunique()} drivers)'
        f'\nPosition rows: {len(pos)} ({pos["driver_number"].nunique()} drivers)'
        f"\nDrivers identified: {len(drv)}"
    )

    # Async sanity check used by RealF1Client internally
    asyncio.run(asyncio.sleep(0))
    adapter.stop()
    return 0


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    raise SystemExit(main(secs))
