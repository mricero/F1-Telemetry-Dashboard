# F1 Telemetry Dashboard - Architecture Document

## Overview

A professional Formula 1 Telemetry Dashboard built with **Streamlit + Plotly** supporting:
- **Historical Race Playback** via FastF1 (local caching, full telemetry)
- **Real-time Live Telemetry** via the LiveF1 package. This is an **unofficial** use of undocumented endpoints: LiveF1 connects to the legacy `/signalr/` hub, not the `wss://livetiming.formula1.com/signalrcore` endpoint FastF1 uses (LIVE-01), and car telemetry/positions need an `F1TV_SUBSCRIPTION_TOKEN` subscription token.
- **Graceful Fallback** when no live session is active (defaults to most recent cached race)
- **Session Recording & Replay** - save live sessions for offline replay

**Key Difference from Original Plan:** OpenF1 live data requires paid subscription. **We use FREE alternatives:**
1. **FastF1 SignalRClient** (built-in) - `fastf1.livetiming.SignalRClient`
2. **LiveF1 Package** - `pip install livef1` → `RealF1Client`
Both connect to the same official F1 SignalR endpoint - no subscription needed!

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           STREAMLIT UI LAYER                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Session      │  │ Speed/       │  │ Throttle/    │  │ Track Map    │   │
│  │ Selector     │  │ RPM/Gear/DRS │  │ Brake Charts │  │ (GPS)        │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     DATA PROCESSING LAYER    │
                    │  ┌────────────────────────┐  │
                    │  │ Telemetry Processor    │  │
                    │  │ - Distance resampling  │  │
                    │  │ - Driver alignment     │  │
                    │  │ - Unit normalization   │  │
                    │  │ - Color mapping        │  │
                    │  └────────────────────────┘  │
                    └──────────────┬──────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│  FASTF1       │         │   LIVEF1 /    │         │   LOCAL       │
│  (Historical) │         │   JOLPICA     │         │   CACHE/REPLAY│
│               │         │               │         │               │
│ • get_session │         │ • SignalR     │         │ • FastF1 disk │
│ • load()      │         │   live feed   │         │   cache       │
│ • car_data    │         │ • Ergast REST │         │ • Saved .pkl  │
│ • laps        │         │   (schedule/  │         │   sessions    │
│ • stints      │         │   results)    │         │ • Race-       │
│ • Cache       │         │ • get_session │         │   weekend     │
└───────────────┘         └───────────────┘         └───────────────┘
```

---

## 1. Data Ingestion Layer

### 1.1 FastF1 Historical Adapter (`data/fastf1_adapter.py`)

```python
class FastF1Adapter:
    """Loads historical F1 sessions with local caching."""
    
    def __init__(self, cache_dir: str = "./ff1_cache"):
        self.cache_dir = cache_dir
        fastf1.Cache.enable_cache(cache_dir)
    
    def get_available_sessions(self, years: list[int] = None) -> pd.DataFrame:
        """Returns DataFrame of all available sessions from schedule."""
        schedule = fastf1.get_event_schedule(years or [2023, 2024, 2025])
        # Filter to completed sessions only
        return schedule[schedule['EventDate'] < pd.Timestamp.now(tz='UTC')]
    
    def load_session(self, year: int, gp: str, session_type: str) -> fastf1.Session:
        """Load session with automatic caching."""
        session = fastf1.get_session(year, gp, session_type)
        session.load(telemetry=True, laps=True, weather=True, messages=True)
        return session
    
    def get_telemetry(self, session, driver: str, scope: str = SCOPE_FASTEST) -> pd.DataFrame:
        """Get car telemetry (speed, throttle, brake, rpm, gear, drs).

        scope='fastest' (default) takes the driver's fastest lap, so Distance
        runs 0 -> lap length and drivers align at the same track position.
        scope='session' takes every lap; Distance then accumulates over the
        whole run (~300 km for a race), which is far heavier to render.
        """
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame()
        telemetry = selection.get_telemetry()   # merges car + position data
        return telemetry[['Distance', 'Time', 'Speed', 'Throttle',
                          'Brake', 'RPM', 'nGear', 'DRS']]
    
    def get_laps(self, session) -> pd.DataFrame:
        """Get lap timing data.

        NOTE: FastF1 has no 'IsPitOutLap' column - pit activity is exposed as
        the PitOutTime/PitInTime timestamps, so the boolean flag is derived.
        """
        laps = session.laps.copy()
        result = laps[['Driver', 'LapNumber', 'LapTime',
                       'Sector1Time', 'Sector2Time', 'Sector3Time']]
        result['IsPitOutLap'] = laps['PitOutTime'].notna()
        return result
    
    def get_stints(self, session) -> pd.DataFrame:
        """Get tyre stint data, derived from per-lap Stint/Compound values.

        Lap counters are cast to Int64 - a groupby leaves them as floats,
        which would render as "Laps: 12.0" in the strategy chart.
        """
        return (session.laps
                .groupby(['Driver', 'Stint'])
                .agg(LapStart=('LapNumber', 'min'),
                     LapEnd=('LapNumber', 'max'),
                     Compound=('Compound', 'first'))
                .reset_index())
    
    def get_location(self, session, driver: str, scope: str = SCOPE_FASTEST) -> pd.DataFrame:
        """Get GPS location data.

        WARNING: do NOT call `get_pos_data().add_distance()`. Position data
        carries no Speed channel, and distance integration needs one, so it
        raises `ValueError: Telemetry does not contain required channels
        'Time' and 'Speed'`. The merged telemetry already has X/Y/Z *and*
        Distance; a raw-position fallback derives distance from GPS arc
        length instead (coordinates are in 1/10 m).
        """
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame()
        return selection.get_telemetry()[['Distance', 'X', 'Y', 'Z']]
```

### 1.2 Jolpica Adapter (`data/jolpica_adapter.py`)

```python
class JolpicaAdapter:
    """Free historical data via Jolpica F1 API (Ergast-compatible)."""

    BASE_URL = "https://api.jolpi.ca/ergast/f1"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'F1-Telemetry-Dashboard/1.0'})

    def _fetch(self, endpoint: str, params: dict = None) -> dict:
        """Generic fetch with error handling."""
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to fetch {endpoint}: {e}")

    @lru_cache(maxsize=32)
    def get_schedule(self, year: int) -> pd.DataFrame:
        """Race schedule for a year (round, race_name, circuit, date...)."""
        ...

    def get_race_results_df(self, year: int, round_num: int) -> pd.DataFrame: ...
    def get_qualifying_df(self, year: int, round_num: int) -> pd.DataFrame: ...
    def get_lap_times_df(self, year: int, round_num: int) -> pd.DataFrame: ...
    def get_pit_stops_df(self, year: int, round_num: int) -> pd.DataFrame: ...
    def get_driver_standings_df(self, year: int) -> pd.DataFrame: ...

    def is_race_weekend(self, year: int = None) -> bool:
        """True if a scheduled race falls within +/-3 days of now (UTC)."""
        ...
```

### 1.3 Live Telemetry Adapter (`data/live_adapter.py`)

**Wire format (verified against LiveF1 source & FastF1 docs):**
- Endpoint in use: the legacy `https://livetiming.formula1.com/signalr/` hub (LiveF1). FastF1 uses `/signalrcore`; see LIVE-01.
- Compressed topics (`CarData.z`, `Position.z`) carry base64-encoded **raw DEFLATE**
  JSON (`zlib.decompress(b64decode(text), -zlib.MAX_WBITS)`).
- CarData channels: `0`=RPM, `2`=Speed, `3`=Gear, `4`=Throttle, `5`=Brake, `45`=DRS.
- LiveF1's `MessageHandlerTemplate` parses messages through its `function_map`
  **before** invoking callbacks, so our buffers hold flat records
  (`DriverNo`, `speed`, `rpm`, `n_gear`, `X/Y/Z`, ...). All subscribed topics
  have dedicated parsers there; unknown topics would raise on every message.

```python
class SignalRLiveAdapter:
    """
    FREE live telemetry via SignalR - connects directly to the official F1 feed.
    Two implementations available:
    1. LiveF1 package: livef1.adapters.RealF1Client (async callbacks)
    2. FastF1 built-in: fastf1.livetiming.SignalRClient (saves raw stream to file)
    LiveF1 uses the legacy /signalr/ hub; FastF1 uses /signalrcore (LIVE-01).
    """

    TELEMETRY_TOPICS = [
        "CarData.z",       # Speed, Throttle, Brake, RPM, Gear, DRS
        "Position.z",      # GPS position X,Y,Z
        "TimingData", "WeatherData",
        "RaceControlMessages", "TrackStatus", "SessionInfo",
        "SessionStatus", "DriverList", "LapSeries", "CurrentTyres",
        "PitLaneTimeCollection", "TyreStintSeries",
    ]

    def start_async(self, topics=None, log_file=None):
        """Run the client in a daemon thread.

        RealF1Client.run() creates and owns its own event loop internally,
        so the background thread must call it directly - wrapping it in
        another asyncio loop raises RuntimeError.
        """

    def register_callback(self, topic: str, callback): ...
    def get_buffered_data(self, topic: str) -> list[dict]: ...
    def get_latest_data(self, topic: str) -> dict | None: ...
    def stop(self): ...

    # Buffers are capped per topic (buffer_limit, default 20000 records);
    # the oldest entries are dropped first so long sessions stay bounded.
    _buffer_topic(topic, records)


class LiveDataProcessor:
    """Turn parsed SignalR records into chart-ready DataFrames."""
    parse_car_data(records)      # -> driver_number, Speed/RPM/nGear/Throttle/Brake/DRS
    parse_position_data(records) # -> driver_number, X/Y/Z (track map trails)
    parse_timing_data(records)   # -> flattened timing fields per driver
    parse_weather_data(records)  # -> AirTemp/TrackTemp/Humidity/...
    parse_tyre_stints(records)   # -> stint rows for the tyre strategy chart
    parse_driver_list(records)   # -> drivers table incl. team colours

distance_at(pos_df, timestamps)  # metres travelled along the driver's GPS
                                 #    trajectory, interpolated at CarData
                                 #    times (real x-axis for live charts)


def decode_zipped(text) -> dict:          # base64 + raw-deflate + JSON
def decode_topic_payload(topic, payload)  # replay FastF1-recorded raw streams
```

### 1.4 Ephemeral Runtime Cache (`data/runtime_cache.py`)

```python
class RuntimeCache:
    """Thread-safe in-memory cache scoped to one app session.

    Loaded sessions stay hot while the app is open; everything is discarded
    the moment the process exits, so each app opening starts fresh.
    """
    begin_session()            # called once at startup: full reset
    get(key) / set(key, val)   # hit/miss counters exposed via stats()
    stats()                    # entries, keys, hits, misses, age_seconds

runtime_cache = RuntimeCache()   # module singleton shared across reruns
```

### 1.5 Persistent Metrics Store (`processing/metrics_store.py`)

```python
class MetricsStore:
    """Keeps derived records across app restarts (JSON file on disk).

    Unlike the runtime cache, fastest lap / fastest sector / top speed
    survive closing and reopening the app.
    """
    update_laps(label, laps_df, driver_map=None)  # fastest lap + S1/S2/S3
    update_telemetry(label, telemetry_dict)       # top speed per driver
    session_records(label)                        # records for one session
    all_time()                                    # best across all sessions
    summary_lines(records)                        # markdown-ready lines

# Accepts Timedelta (FastF1), 'M:SS.mmm' and 'SS.mmm' strings (live feed).
# Storage path: ./metrics_store.json (env: F1_METRICS_STORE)
```

### 1.6 Data Source Manager (`data/source_manager.py`)

```python
class DataSourceManager:
    """Unified interface with automatic fallback: Live -> Historical -> Replay"""

    def __init__(self):
        self.fastf1 = FastF1Adapter()
        self.jolpica = JolpicaAdapter()   # race-weekend detection + schedule fallback
        self.live = SignalRLiveAdapter(use_livef1=True)
        self.replay_dir = Path("./replay_sessions")

    def get_session_data(self,
                         source="auto",     # "auto" | "fastf1" | "livef1" | "live" | "replay"
                         year=None, gp=None, session_type=None,
                         replay_file=None) -> dict:
        """
        Returns unified data dict:
        {
            'session_info': {...},
            'telemetry': {driver: DataFrame[Distance, Speed, Throttle, Brake, RPM, Gear, DRS]},
            'laps': DataFrame,
            'stints': DataFrame,
            'location': {driver: DataFrame[Distance, X, Y, Z]},
            'weather': DataFrame,
            'drivers': DataFrame[driver_number, name_acronym, team_colour, team_name],
            'source': 'fastf1'|'livef1'|'live'|'replay',
            'is_live': bool
        }
        """

        if source == "replay":
            return self._load_replay(replay_file)

        if source == "auto":
            # Try live first (during race weekends)
            if self._is_race_weekend():
                return self._load_live_session()
            # Fallback: most recent completed race
            if not year:
                recent = self._get_most_recent_completed_race()
                year, gp, session_type = (recent['year'], recent['gp'],
                                          recent['session_type'])

        if source in ("auto", "fastf1"):
            return self._load_fastf1_session(year, gp, session_type)
        if source == "livef1":
            return self._load_livef1_session(year, gp, session_type)
        if source == "live":
            return self._load_live_session()

        raise ValueError(f"Unknown source: {source}")

    def poll_live_data(self):
        """Fold buffered SignalR records into the unified session-dict shape
        (telemetry / laps / stints / location / weather / drivers). Called
        repeatedly by the auto-refreshing live fragment in the UI."""

    def _is_race_weekend(self):
        return self.jolpica.is_race_weekend()   # safe fallback: False on any error

    def _gp_to_circuit_short(cls, gp: str) -> str:
        """'Bahrain Grand Prix' -> 'Sakhir' for LiveF1 meeting_identifier."""

    def save_replay(self, data: dict, name: str) -> str: ...   # pickled .pkl files
    def _load_replay(self, filepath: str) -> dict: ...
    def get_available_replays(self) -> list[str]: ...
```


## 2. Data Processing Layer

### 2.1 Telemetry Processor (`processing/telemetry_processor.py`)

```python
class TelemetryProcessor:
    """Processes raw telemetry into visualization-ready format."""
    
    DISTANCE_STEP = 5  # meters - uniform distance grid for alignment
    
    def __init__(self):
        self.driver_color_map = {}
    
    def build_driver_color_map(self, drivers_df: pd.DataFrame) -> dict:
        """Map driver acronyms to team colors (from FastF1 or LiveF1)."""
        color_map = {}
        for _, row in drivers_df.iterrows():
            acronym = row.get('name_acronym') or row.get('TeamName', '')[:3].upper()
            color = row.get('team_colour') or row.get('TeamColour', '#888888')
            if not str(color).startswith('#'):
                color = f"#{color}"
            color_map[acronym] = color
        self.driver_color_map = color_map
        return color_map
    
    def resample_to_distance_grid(self, 
                                   telemetry_df: pd.DataFrame, 
                                   distance_col: str = 'Distance') -> pd.DataFrame:
        """
        Resample telemetry to uniform 5m distance grid.
        Enables accurate multi-driver comparison at same track position.
        """
        if telemetry_df.empty or distance_col not in telemetry_df.columns:
            return telemetry_df
        
        # Sort by distance
        df = telemetry_df.sort_values(distance_col).reset_index(drop=True)
        
        # Create uniform grid
        max_dist = df[distance_col].max()
        grid = np.arange(0, max_dist, self.DISTANCE_STEP)
        
        # Interpolate each channel
        result = pd.DataFrame({distance_col: grid})
        for col in ['Speed', 'Throttle', 'Brake', 'RPM', 'nGear', 'DRS']:
            if col in df.columns:
                result[col] = np.interp(grid, df[distance_col], df[col])
        
        return result
    
    def align_drivers_by_distance(self, 
                                   telemetry_dict: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        """Align all drivers to same distance grid."""
        aligned = {}
        for driver, df in telemetry_dict.items():
            aligned[driver] = self.resample_to_distance_grid(df)
        return aligned
    
    def normalize_units(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize units for consistent display."""
        df = df.copy()
        if 'Brake' in df.columns:
            # FastF1: boolean → 0/100, LiveF1: already 0/100
            if df['Brake'].max() <= 1:
                df['Brake'] = df['Brake'] * 100
        if 'nGear' in df.columns:
            df['Gear'] = df['nGear'].replace(0, 'N').astype(str)
        return df
    
    def process_laps(self, laps_df: pd.DataFrame, drivers_df: pd.DataFrame) -> pd.DataFrame:
        """Clean and enrich lap data for visualization."""
        if laps_df.empty:
            return laps_df
        df = laps_df.copy()
        # Merge driver info
        if 'Driver' in df.columns and 'driver_number' in drivers_df.columns:
            driver_map = drivers_df.set_index('driver_number')['name_acronym'].to_dict()
            df['DriverAcronym'] = df['Driver'].map(driver_map)
        return df
    
    def process_stints(self, stints_df: pd.DataFrame) -> pd.DataFrame:
        """Prepare stint data for tire strategy chart."""
        if stints_df.empty:
            return stints_df
        df = stints_df.copy()
        df['Compound'] = df['Compound'].fillna('Unknown').str.upper()
        df['LapCount'] = df['LapEnd'] - df['LapStart'] + 1
        return df
```

---

## 3. UI Layer (Streamlit + Plotly)

### 3.1 Layout Structure (`ui/layout.py`)

```python
def render_header():
    st.set_page_config(page_title="F1 Telemetry Dashboard", layout="wide")
    st.title("🏎️ Formula 1 Telemetry Dashboard")
    st.caption("Historical (FastF1) • Live (SignalR - FREE) • Replay (Local)")

def render_session_selector(data_manager: DataSourceManager) -> dict:
    """Session selection with live detection."""
    
    # Check for live session (Jolpica schedule probe)
    is_race_weekend = data_manager._is_race_weekend()
    
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        source = st.selectbox(
            "Data Source",
            ["Auto (Live → Historical)", "FastF1 (Historical)", "LiveF1 (Historical)", "Live (SignalR)", "Replay (Saved)"],
            index=0
        )
    
    if live_session and "Auto" in source:
        with col2:
            st.success(f"🔴 LIVE: {live_session['session_name']} - {live_session['date_start'][:16]}")
        return {'source': 'auto', 'is_live': True}
    
    # Historical selection
    with col2:
        years = st.selectbox("Season", [2025, 2024, 2023], index=0)
    
    meetings = data_manager.fastf1.get_available_sessions([years])
    gps = meetings['EventName'].unique()
    
    with col3:
        gp = st.selectbox("Grand Prix", gps)
    
    session_types = ['FP1', 'FP2', 'FP3', 'Q', 'S', 'R']
    session_type = st.selectbox("Session", session_types, index=len(session_types)-1)
    
    return {
        'source': source.split('(')[0].strip().lower(),
        'year': years,
        'gp': gp,
        'session_type': session_type,
        'is_live': False
    }

def render_telemetry_charts(telemetry_data: dict, color_map: dict):
    """Render speed, throttle, brake, rpm, gear, DRS charts."""
    
    tabs = st.tabs(["📈 Speed", "⚡ Throttle", "🛑 Brake", "🔧 RPM", "⚙️ Gear", "🚀 DRS"])
    
    channel_config = {
        "Speed": {"col": "Speed", "unit": "km/h", "color": "default"},
        "Throttle": {"col": "Throttle", "unit": "%", "color": "default"},
        "Brake": {"col": "Brake", "unit": "%", "color": "default"},
        "RPM": {"col": "RPM", "unit": "RPM", "color": "default"},
        "Gear": {"col": "Gear", "unit": "", "color": "default"},
        "DRS": {"col": "DRS", "unit": "", "color": "default"},
    }
    
    for i, (tab_name, config) in enumerate(channel_config.items()):
        with tabs[i]:
            fig = create_telemetry_chart(telemetry_data, config, color_map)
            st.plotly_chart(fig, width="stretch")

def create_telemetry_chart(telemetry_data: dict, config: dict, color_map: dict) -> go.Figure:
    """Create multi-driver telemetry line chart."""
    fig = go.Figure()
    
    for driver, df in telemetry_data.items():
        if df.empty or config['col'] not in df.columns:
            continue
        
        color = color_map.get(driver, "#888888")
        
        if config['col'] == 'Gear':
            # Gear as step chart
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[config['col']],
                mode='lines', name=driver,
                line=dict(color=color, shape='hv'),
                hovertemplate=f"{driver}: %{{y}}<br>Distance: %{{x}}m<extra></extra>"
            ))
        else:
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[config['col']],
                mode='lines', name=driver,
                line=dict(color=color, width=2),
                hovertemplate=f"{driver}: %{{y}} {config['unit']}<br>Distance: %{{x}}m<extra></extra>"
            ))
    
    fig.update_layout(
        title=f"{config['col']} by Track Distance",
        xaxis_title="Distance (m)",
        yaxis_title=f"{config['col']} ({config['unit']})",
        hovermode="x unified",
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    return fig

def render_lap_times(laps_df: pd.DataFrame, color_map: dict):
    """Render lap time chart with pit stop indicators."""
    if laps_df.empty:
        st.warning("No lap data available")
        return
    
    fig = go.Figure()
    
    for driver in laps_df['DriverAcronym'].unique():
        driver_laps = laps_df[laps_df['DriverAcronym'] == driver].sort_values('LapNumber')
        color = color_map.get(driver, "#888888")
        
        # Convert LapTime to seconds for plotting
        lap_times_sec = driver_laps['LapTime'].dt.total_seconds()
        
        # Pit out lap markers
        pit_out = driver_laps['IsPitOutLap'].fillna(False).astype(bool)
        
        fig.add_trace(go.Scatter(
            x=driver_laps['LapNumber'],
            y=lap_times_sec,
            mode='lines+markers',
            name=driver,
            line=dict(color=color),
            marker=dict(
                color=['red' if p else color for p in pit_out],
                size=8,
                symbol=['diamond' if p else 'circle' for p in pit_out]
            ),
            hovertemplate=(
                f"{driver}: Lap %{{x}}<br>"
                f"Time: %{{customdata}}<br>"
                f"Pit: %{{text}}<extra></extra>"
            ),
            customdata=driver_laps['LapTime'].astype(str),
            text=['🔧 PIT OUT' if p else '' for p in pit_out]
        ))
    
    fig.update_layout(
        title="Lap Times by Driver",
        xaxis_title="Lap Number",
        yaxis_title="Lap Time (seconds)",
        hovermode="x unified",
        height=500
    )
    
    # Format y-axis as MM:SS.mmm
    fig.update_yaxes(tickformat="%M:%S.%3f")
    
    st.plotly_chart(fig, width="stretch")

def render_tire_strategy(stints_df: pd.DataFrame, color_map: dict):
    """Render horizontal bar chart for tire strategy."""
    
    COMPOUND_COLORS = {
        "SOFT": "red", "MEDIUM": "yellow", "HARD": "white",
        "INTERMEDIATE": "green", "WET": "blue", "UNKNOWN": "gray"
    }
    
    fig = go.Figure()
    
    for _, row in stints_df.iterrows():
        driver = row.get('DriverAcronym') or row.get('Driver')
        compound = row['Compound'].upper()
        
        fig.add_trace(go.Bar(
            x=[row['LapCount']],
            y=[driver],
            base=row['LapStart'],
            orientation='h',
            marker=dict(color=COMPOUND_COLORS.get(compound, "gray")),
            hovertemplate=(
                f"{driver}: {compound}<br>"
                f"Laps: {row['LapCount']}<br>"
                f"Start: {row['LapStart']}<br>"
                f"End: {row['LapEnd']}<extra></extra>"
            ),
            showlegend=False
        ))
    
    # Add driver labels with team colors
    for driver in stints_df['DriverAcronym'].unique():
        fig.add_annotation(
            x=-2, y=driver, xref="x", yref="y",
            text=f"<b>{driver}</b>", showarrow=False,
            font=dict(color=color_map.get(driver, "#AAA"), size=12),
            align="right", xanchor="right"
        )
    
    fig.update_layout(
        title="Tire Strategy by Driver",
        xaxis_title="Lap Number",
        barmode="stack",
        height=max(400, len(stints_df['DriverAcronym'].unique()) * 30 + 100),
        margin=dict(l=120),
        yaxis=dict(showticklabels=False)
    )
    
    st.plotly_chart(fig, width="stretch")

def render_track_map(location_data: dict, color_map: dict):
    """Render track map with driver positions."""
    # Simplified: show circuit outline from first driver's GPS
    if not location_data:
        st.info("GPS data not available for track map")
        return
    
    first_driver = next(iter(location_data))
    df = location_data[first_driver]
    
    if df.empty or 'X' not in df.columns:
        st.info("Track map requires GPS data")
        return
    
    fig = go.Figure()
    
    # Circuit outline
    fig.add_trace(go.Scatter(
        x=df['X'], y=df['Y'],
        mode='lines', line=dict(color='#444', width=2),
        name='Circuit', showlegend=False
    ))
    
    # Driver positions (last known)
    for driver, loc_df in location_data.items():
        if loc_df.empty:
            continue
        last_pos = loc_df.iloc[-1]
        fig.add_trace(go.Scatter(
            x=[last_pos['X']], y=[last_pos['Y']],
            mode='markers+text',
            marker=dict(color=color_map.get(driver, "#888"), size=12),
            text=[driver], textposition="top center",
            name=driver, showlegend=False
        ))
    
    fig.update_layout(
        title="Track Map - Driver Positions",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        height=500, showlegend=False
    )
    
    st.plotly_chart(fig, width="stretch")
```

### 3.2 Main App (`app.py`)

```python
import streamlit as st
import pandas as pd

from data.source_manager import DataSourceManager
from processing.telemetry_processor import TelemetryProcessor
from ui.layout import (
    render_header, render_session_selector,
    render_telemetry_charts, render_lap_times,
    render_tire_strategy, render_track_map
)

def main():
    render_header()
    
    # Initialize managers
    if 'data_manager' not in st.session_state:
        st.session_state.data_manager = DataSourceManager()
    if 'processor' not in st.session_state:
        st.session_state.processor = TelemetryProcessor()
    
    data_manager = st.session_state.data_manager
    processor = st.session_state.processor
    
    # Session Selection
    with st.expander("📋 Session Selection", expanded=True):
        selection = render_session_selector(data_manager)
    
    # Load Data
    with st.spinner("Loading session data..."):
        try:
            session_data = data_manager.get_session_data(**selection)
        except Exception as e:
            st.error(f"Failed to load session: {e}")
            st.stop()
    
    # Build color map
    color_map = processor.build_driver_color_map(session_data['drivers'])
    
    # Process telemetry
    telemetry_aligned = processor.align_drivers_by_distance(session_data['telemetry'])
    telemetry_processed = {d: processor.normalize_units(df) for d, df in telemetry_aligned.items()}
    
    # Process laps & stints
    laps_processed = processor.process_laps(session_data['laps'], session_data['drivers'])
    stints_processed = processor.process_stints(session_data['stints'])
    
    # Display Session Info
    info = session_data['session_info']
    live_badge = " 🔴 **LIVE**" if session_data.get('is_live') else ""
    st.markdown(f"### {info.get('gp', '')} {info.get('year', '')} - {info.get('session_type', '')}{live_badge}")
    
    # Telemetry Charts
    st.markdown("---")
    st.subheader("📊 Telemetry Channels")
    render_telemetry_charts(telemetry_processed, color_map)
    
    # Lap Times
    st.markdown("---")
    st.subheader("⏱️ Lap Times")
    render_lap_times(laps_processed, color_map)
    
    # Tire Strategy
    st.markdown("---")
    st.subheader("🛞 Tire Strategy")
    render_tire_strategy(stints_processed, color_map)
    
    # Track Map
    st.markdown("---")
    st.subheader("🗺️ Track Map")
    render_track_map(session_data['location'], color_map)
    
    # Replay Save Option
    if not session_data.get('is_live') and st.button("💾 Save Session for Replay"):
        name = f"{info.get('gp', 'race')}_{info.get('session_type', 'R')}"
        path = data_manager.save_replay(session_data, name)
        st.success(f"Saved to {path}")

if __name__ == "__main__":
    main()
```

---

## 4. Dependencies

### requirements.txt
```txt
# Core Framework
streamlit>=1.35.0
plotly>=5.22.0
pandas>=2.2.0
numpy>=1.26.0

# F1 Data
fastf1>=3.8.0
livef1>=1.2.0
requests>=2.31.0
python-dotenv>=1.0.0
```

### Development Dependencies (requirements-dev.txt)
```txt
-r requirements.txt
pytest>=8.0.0
pytest-cov>=4.1.0
ruff>=0.4.0
black>=24.0.0
mypy>=1.10.0
pre-commit>=3.7.0
```

---

## 5. Configuration

### .env
```env
# FastF1 Cache Directory
FASTF1_CACHE_DIR=./ff1_cache

# Replay Sessions Directory
REPLAY_DIR=./replay_sessions

# App Settings
DEFAULT_YEAR=2024
DEFAULT_GP=Abu Dhabi
DEFAULT_SESSION=R

# Persistent metrics (fastest lap/sector/top speed records)
F1_METRICS_STORE=./metrics_store.json
```

### config.py (Runtime Config)
```python
from dataclasses import dataclass
import os

@dataclass
class Config:
    fastf1_cache_dir: str = os.getenv("FASTF1_CACHE_DIR", "./ff1_cache")
    replay_dir: str = os.getenv("REPLAY_DIR", "./replay_sessions")
    default_year: int = int(os.getenv("DEFAULT_YEAR", "2024"))
    default_gp: str = os.getenv("DEFAULT_GP", "Abu Dhabi")
    default_session: str = os.getenv("DEFAULT_SESSION", "R")
    distance_step: int = 5  # meters for distance grid alignment
    cache_ttl_seconds: int = 3600  # Streamlit cache TTL

config = Config()
```

---

## 6. Project Structure

```
f1-telemetry-dashboard/
├── app.py                      # Main Streamlit entry point
├── requirements.txt
├── requirements-dev.txt
├── .env
├── config.py
├── PHASE1_RESEARCH_SUMMARY.md  # Research documentation
├── ARCHITECTURE.md             # This file
├── data/
│   ├── __init__.py
│   ├── fastf1_adapter.py       # FastF1 historical data loading
│   ├── jolpica_adapter.py      # Jolpica F1 API (Ergast-compatible) client
│   ├── live_adapter.py         # SignalR live timing adapter (LiveF1/FastF1)
│   ├── runtime_cache.py        # Ephemeral hot cache (cleared on app restart)
│   └── source_manager.py       # Unified data source with fallback + live polling
├── processing/
│   ├── __init__.py
│   ├── telemetry_processor.py  # Distance alignment, normalization
│   ├── metrics_store.py        # Persistent fastest lap/sector/top-speed records
│   └── time_utils.py           # Shared lap/sector time parsing (M:SS.mmm etc.)
├── ui/
│   ├── __init__.py
│   └── layout.py               # Canonical Streamlit UI components + charts
├── scripts/
│   ├── inspect_fastf1.py       # Manual FastF1 data-structure inspection
│   ├── inspect_livef1.py       # Manual LiveF1 data-structure inspection
│   └── live_smoke.py           # Live SignalR E2E smoke test (race weekends)
├── tests/                      # Adapter, cache, metrics and parsing suites
├── pytest.ini                  # Collect tests from tests/ only; network marker
├── pyproject.toml              # black/ruff configuration
├── .github/workflows/ci.yml    # ruff + black --check + pytest
├── LICENSE                     # MIT license
├── ff1_cache/                  # FastF1 local cache (gitignored)
├── replay_sessions/            # Saved replay files (gitignored)
├── metrics_store.json          # Persisted records (gitignored)
└── .gitignore
```

---

## 7. Data Flow Summary

```
USER ACTION                          DATA FLOW
────────────────────────────────────────────────────────────────────────
1. Select Session                    → runtime_cache.get(key)
   |─ HIT:  instant reuse            → cached unified dict
   └─ MISS: DataSourceManager.get_session_data()
2. Auto-detect Live?                 → _is_race_weekend() (Jolpica probe)
   |─ YES: SignalR Live              → start_async() + poll_live_data() every 3s
   └─ NO:  Historical                → FastF1Adapter.load_session()
3. Hot-Cache Result                  → runtime_cache.set(key, data)
                                       (memory only - discarded on app close)
4. Process Telemetry                 → TelemetryProcessor.align_drivers_by_distance()
5. Normalize Units                   → TelemetryProcessor.normalize_units()
6. Build Color Map                   → TelemetryProcessor.build_driver_color_map()
7. Update Records                    → MetricsStore.update_laps()/update_telemetry()
                                       (PERSISTED: fastest lap / sectors / top speed
                                        survive app restarts)
8. Render Charts                     → UI layout functions → Plotly Figures
9. Optional: Save Replay             → DataSourceManager.save_replay()
```

---

## 8. Fallback Behavior Matrix

| Scenario | Primary Source | Fallback 1 | Fallback 2 | UI Indicator |
|----------|---------------|------------|------------|--------------|
| Race Weekend (Live) | SignalR Live (FREE) | FastF1 Historical | Replay | 🔴 LIVE badge |
| Mid-week (No Live) | FastF1 Historical | Jolpica schedule/results | Replay | "Showing most recent GP" |
| API Failure | FastF1 Cache | Replay Files | Error | Warning toast |
| First Run (No Cache) | FastF1/Jolpica Historical | Sample Replay | Error | "Loading..." spinner |

---

## 9. Future Extensibility Points

1. **Replay Streaming Engine**: Simulate real-time playback from saved SignalR sessions with pause/seek/speed controls
2. **ML Anomaly Detection**: Add Isolation Forest module (per siddoboi architecture)
3. **MongoDB Persistence**: Replace file-based replay with MongoDB time-series
4. **Multi-Session Comparison**: Compare qualifying vs race pace
5. **Sector Time Analysis**: Add sector-by-sector delta charts
6. **Driver Profile Overlay**: Headshots, stats, career highlights
7. **Export Features**: CSV, PDF reports, telemetry bundles

---

*Architecture Complete. Ready for Phase 3: TDD Implementation.*