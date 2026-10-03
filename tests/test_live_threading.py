"""Thread-safety of the live buffers (IMPROVEMENTS.md LIVE-07).

The client thread appends to the same lists the Streamlit script thread
iterates, and trimming them shifted the reader's slice underneath it, so a
poll could skip or duplicate records.
"""

import threading

from f1dash.data.live_adapter import SignalRLiveAdapter


class TestConcurrentBufferAccess:
    def test_reader_never_sees_a_torn_sequence(self):
        adapter = SignalRLiveAdapter(buffer_limit=500)
        stop = threading.Event()
        problems = []

        def write():
            for index in range(20000):
                adapter.handle_message("WeatherData", {"n": index})
            stop.set()

        def read():
            while not stop.is_set():
                records = adapter.get_buffered_data("WeatherData")
                numbers = [record["n"] for record in records]
                if numbers != sorted(numbers):
                    problems.append(numbers[:5])
                    return

        writer = threading.Thread(target=write)
        reader = threading.Thread(target=read)
        writer.start()
        reader.start()
        writer.join(timeout=60)
        reader.join(timeout=60)

        assert not problems, f"reader saw non-monotonic records: {problems[:1]}"

    def test_readers_get_their_own_copy(self):
        adapter = SignalRLiveAdapter()
        adapter.handle_message("WeatherData", {"n": 1})

        snapshot = adapter.get_buffered_data("WeatherData")
        adapter.handle_message("WeatherData", {"n": 2})

        assert len(snapshot) == 1, "the reader's list must not grow underneath it"

    def test_mutating_a_snapshot_does_not_corrupt_the_buffer(self):
        adapter = SignalRLiveAdapter()
        adapter.handle_message("WeatherData", {"n": 1})

        adapter.get_buffered_data("WeatherData").clear()

        assert len(adapter.get_buffered_data("WeatherData")) == 1

    def test_state_updates_are_serialised(self):
        adapter = SignalRLiveAdapter()
        done = threading.Event()

        def write():
            for index in range(5000):
                adapter.handle_message("TimingData", {"Lines": {"1": {"Position": str(index)}}})
            done.set()

        writer = threading.Thread(target=write)
        writer.start()
        while not done.is_set():
            lines = adapter.state.get("TimingData").get("Lines", {})
            assert set(lines) <= {"1"}
        writer.join(timeout=60)

        assert adapter.state.get("TimingData")["Lines"]["1"]["Position"] == "4999"

    def test_lap_history_survives_a_full_telemetry_buffer(self):
        adapter = SignalRLiveAdapter(buffer_limit=100)
        for lap in range(1, 6):
            adapter.handle_message(
                "TimingData",
                {"Lines": {"1": {"NumberOfLaps": lap, "LastLapTime": {"Value": f"1:3{lap}.000"}}}},
            )
        for index in range(500):
            adapter.handle_message("WeatherData", {"n": index})

        assert len(adapter.get_buffered_data("WeatherData")) == 100
        assert len(adapter.lap_history) == 5


class TestLapHistorySnapshots:
    def test_recorded_laps_returns_a_copy(self):
        adapter = SignalRLiveAdapter()
        adapter.handle_message(
            "TimingData", {"Lines": {"1": {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:31"}}}}
        )

        snapshot = adapter.recorded_laps()
        snapshot.clear()

        assert len(adapter.recorded_laps()) == 1
