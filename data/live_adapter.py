"""Live Telemetry Adapter - FREE SignalR connection to official F1 feed"""
import asyncio
import threading
import pandas as pd
from typing import Dict, List, Callable, Optional, Any
from collections import defaultdict
import json


class SignalRLiveAdapter:
    """
    FREE live telemetry via SignalR - connects directly to F1 official feed.
    Two implementations available:
    1. FastF1 built-in: fastf1.livetiming.SignalRClient (saves to file)
    2. LiveF1 package: livef1.adapters.RealF1Client (async callbacks)
    Both use: wss://livetiming.formula1.com/signalrcore
    """
    
    # Topics to subscribe for telemetry dashboard
    TELEMETRY_TOPICS = [
        "CarData.z",       # Speed, Throttle, Brake, RPM, Gear, DRS (~50Hz)
        "Position.z",      # GPS position X,Y,Z (~50Hz)
        "TimingData",      # Lap times, sectors, gaps
        "TimingDataF1",    # F1-specific timing
        "WeatherData",     # Track temp, air temp, humidity, wind, rain
        "RaceControlMessages",  # Flags, SC, incidents
        "TrackStatus",     # Track conditions (yellow, green, red)
        "SessionInfo",     # Session metadata
        "SessionStatus",   # Session state (racing, stopped, etc.)
        "DriverList",      # Driver info (numbers, names, teams)
        "LapSeries",       # Lap data stream
        "CurrentTyres",    # Current tyre compounds
        "PitLaneTimeCollection",  # Pit lane timing
        "TyreStintSeries", # Tyre stint data
    ]
    
    def __init__(self, use_livef1: bool = True):
        """
        Args:
            use_livef1: If True, use LiveF1 RealF1Client (async callbacks).
                       If False, use FastF1 SignalRClient (file-based).
        """
        self.use_livef1 = use_livef1
        self.client = None
        self._data_buffer: Dict[str, List[Dict]] = defaultdict(list)
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    async def start_livef1_client(self, topics: List[str] = None, log_file: str = None):
        """Start LiveF1 RealF1Client with async callbacks."""
        from livef1.adapters import RealF1Client
        
        topics = topics or self.TELEMETRY_TOPICS
        self.client = RealF1Client(topics=topics, log_file_name=log_file)
        self._running = True
        
        # Register callback for all topics
        @self.client.callback("telemetry_handler")
        async def handle_data(records):
            for topic, data in records.items():
                self._data_buffer[topic].extend(data)
                
                # Call registered callbacks
                if topic in self._callbacks:
                    for cb in self._callbacks[topic]:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(data)
                        else:
                            cb(data)
        
        # Run in background (blocks)
        self.client.run()
    
    def start_fastf1_client(self, filename: str, topics: List[str] = None, timeout: int = 60):
        """Start FastF1 SignalRClient - saves to file."""
        from fastf1.livetiming.client import SignalRClient
        
        topics = topics or self.TELEMETRY_TOPICS
        self.client = SignalRClient(
            filename=filename,
            filemode="w",
            timeout=timeout,
            no_auth=False
        )
        self._running = True
        self.client.start()  # Blocks
    
    def start_async(self, topics: List[str] = None, log_file: str = None):
        """Start live client in background thread."""
        if self._running:
            return
        
        self._running = True
        
        def run_client():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start_livef1_client(topics, log_file))
        
        self._thread = threading.Thread(target=run_client, daemon=True)
        self._thread.start()
    
    def register_callback(self, topic: str, callback: Callable):
        """Register callback for a topic."""
        self._callbacks[topic].append(callback)
    
    def get_buffered_data(self, topic: str) -> List[Dict]:
        """Get buffered data for a topic."""
        return self._data_buffer.get(topic, [])
    
    def get_latest_data(self, topic: str) -> Optional[Dict]:
        """Get most recent record for a topic."""
        data = self._data_buffer.get(topic, [])
        return data[-1] if data else None
    
    def clear_buffer(self, topic: str = None):
        """Clear buffered data."""
        if topic:
            self._data_buffer[topic] = []
        else:
            self._data_buffer.clear()
    
    def is_running(self) -> bool:
        """Check if client is running."""
        return self._running
    
    def stop(self):
        """Stop the client."""
        self._running = False
        if self.client and hasattr(self.client, 'stop'):
            self.client.stop()
        if self._thread:
            self._thread.join(timeout=5)


class LiveDataProcessor:
    """Process raw SignalR data into structured DataFrames."""
    
    @staticmethod
    def parse_car_data(raw_records: List[Dict]) -> pd.DataFrame:
        """Parse CarData.z records into DataFrame."""
        if not raw_records:
            return pd.DataFrame()
        
        rows = []
        for record in raw_records:
            # CarData.z format: [driver_number, timestamp, rpm, speed, gear, throttle, brake, drs]
            # This is a simplified parser - actual format may vary
            try:
                rows.append({
                    'driver_number': record.get('DriverNumber', record.get('driver_number')),
                    'timestamp': record.get('Utc', record.get('timestamp')),
                    'RPM': record.get('RPM', record.get('rpm')),
                    'Speed': record.get('Speed', record.get('speed')),
                    'nGear': record.get('Gear', record.get('gear')),
                    'Throttle': record.get('Throttle', record.get('throttle')),
                    'Brake': record.get('Brake', record.get('brake')),
                    'DRS': record.get('DRS', record.get('drs')),
                })
            except:
                continue
        
        return pd.DataFrame(rows)
    
    @staticmethod
    def parse_position_data(raw_records: List[Dict]) -> pd.DataFrame:
        """Parse Position.z records into DataFrame."""
        if not raw_records:
            return pd.DataFrame()
        
        rows = []
        for record in raw_records:
            try:
                rows.append({
                    'driver_number': record.get('DriverNumber', record.get('driver_number')),
                    'timestamp': record.get('Utc', record.get('timestamp')),
                    'X': record.get('X', record.get('x')),
                    'Y': record.get('Y', record.get('y')),
                    'Z': record.get('Z', record.get('z')),
                })
            except:
                continue
        
        return pd.DataFrame(rows)
    
    @staticmethod
    def parse_timing_data(raw_records: List[Dict]) -> pd.DataFrame:
        """Parse TimingData records into DataFrame."""
        if not raw_records:
            return pd.DataFrame()
        
        rows = []
        for record in raw_records:
            try:
                rows.append({
                    'driver_number': record.get('DriverNumber', record.get('driver_number')),
                    'timestamp': record.get('Utc', record.get('timestamp')),
                    'LapNumber': record.get('LapNumber', record.get('lap_number')),
                    'LapTime': record.get('LapTime', record.get('lap_time')),
                    'Sector1Time': record.get('Sector1Time', record.get('sector1_time')),
                    'Sector2Time': record.get('Sector2Time', record.get('sector2_time')),
                    'Sector3Time': record.get('Sector3Time', record.get('sector3_time')),
                    'Position': record.get('Position', record.get('position')),
                })
            except:
                continue
        
        return pd.DataFrame(rows)
    
    @staticmethod
    def parse_weather_data(raw_records: List[Dict]) -> pd.DataFrame:
        """Parse WeatherData records into DataFrame."""
        if not raw_records:
            return pd.DataFrame()
        
        rows = []
        for record in raw_records:
            try:
                rows.append({
                    'timestamp': record.get('Utc', record.get('timestamp')),
                    'AirTemp': record.get('AirTemp', record.get('air_temp')),
                    'TrackTemp': record.get('TrackTemp', record.get('track_temp')),
                    'Humidity': record.get('Humidity', record.get('humidity')),
                    'WindSpeed': record.get('WindSpeed', record.get('wind_speed')),
                    'WindDirection': record.get('WindDirection', record.get('wind_direction')),
                    'Rainfall': record.get('Rainfall', record.get('rainfall')),
                })
            except:
                continue
        
        return pd.DataFrame(rows)


def check_live_session_available() -> bool:
    """Check if there's likely a live F1 session running."""
    # Check if current date/time falls within a typical race weekend
    # This is a simplified check - in production, query Jolpica/OpenF1 schedule
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # F1 races typically run Friday-Sunday
    # This is a placeholder - real implementation would check schedule
    return False


if __name__ == "__main__":
    # Test the adapter
    import time
    
    adapter = SignalRLiveAdapter(use_livef1=True)
    
    # Register a callback
    def on_car_data(data):
        print(f"CarData: {len(data)} records")
    
    adapter.register_callback("CarData.z", on_car_data)
    
    # Start in background
    adapter.start_async(topics=["CarData.z", "Position.z"], log_file="test_session.json")
    
    # Wait for some data
    time.sleep(10)
    
    # Check buffered data
    print(f"Buffered CarData: {len(adapter.get_buffered_data('CarData.z'))}")
    print(f"Buffered Position: {len(adapter.get_buffered_data('Position.z'))}")
    
    adapter.stop()