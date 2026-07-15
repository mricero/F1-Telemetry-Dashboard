# F1 Telemetry Dashboard - Architecture Document

## Overview

A professional Formula 1 Telemetry Dashboard built with **Streamlit + Plotly** supporting:
- **Historical Race Playback** via FastF1 (local caching, full telemetry)
- **Real-time Live Telemetry (FREE)** via FastF1 SignalR client or LiveF1 package - connects directly to official F1 feed `wss://livetiming.formula1.com/signalrcore`
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
│  FASTF1       │         │   OPENF1      │         │   LOCAL       │
│  (Historical) │         │   (Live/Hist) │         │   CACHE/REPLAY│
│               │         │               │         │               │
│ • get_session │         │ • /meetings   │         │ • FastF1 disk │
│ • load()      │         │ • /sessions   │         │   cache       │
│ • car_data    │         │ • /laps       │         │ • Saved JSON  │
│ • laps        │         │ • /car_data   │         │   sessions    │
│ • stints      │         │ • /location   │         │ • Pre-loaded  │
│ • Cache       │         │ • /weather    │         │   samples     │
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
    
    def get_telemetry(self, session: fastf1.Session, driver: str) -> pd.DataFrame:
        """Get car telemetry (speed, throttle, brake, rpm, gear, drs)."""
        laps = session.laps.pick_drivers(driver)
        if laps.empty:
            return pd.DataFrame()
        telemetry = laps.get_telemetry().add_distance()
        return telemetry[['Distance', 'Speed', 'Throttle', 'Brake', 'RPM', 'nGear', 'DRS']]
    
    def get_laps(self, session: fastf1.Session) -> pd.DataFrame:
        """Get lap timing data."""
        return session.laps[['Driver', 'LapNumber', 'LapTime', 'Sector1Time', 
                             'Sector2Time', 'Sector3Time', 'IsPitOutLap']]
    
    def get_stints(self, session: fastf1.Session) -> pd.DataFrame:
        """Get tyre stint data."""
        return session.laps[['Driver', 'Stint', 'Compound', 'LapStart', 'LapEnd']].drop_duplicates()
    
    def get_location(self, session: fastf1.Session, driver: str) -> pd.DataFrame:
        """Get GPS location data."""
        laps = session.laps.pick_drivers(driver)
        if laps.empty:
            return pd.DataFrame()
        return laps.get_pos_data().add_distance()[['Distance', 'X', 'Y', 'Z']]
```

### 1.2 OpenF1 Adapter (`data/openf1_adapter.py`)

```python
class OpenF1Adapter:
    """Fetches data from OpenF1 REST API with filtering support."""
    
    BASE_URL = "https://api.openf1.org/v1"
    
    def __init__(self):
        self.session = requests.Session()
    
    def _fetch(self, endpoint: str, params: dict = None) -> pd.DataFrame:
        """Generic fetch with filtering and error handling."""
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.session.get(url, params=params, timeout=10)
        response.raise_for_status()
        return pd.DataFrame(response.json())
    
    # Historical Data (always available)
    def get_meetings(self, year: int = None) -> pd.DataFrame:
        params = {"year": year} if year else {}
        return self._fetch("meetings", params)
    
    def get_sessions(self, meeting_key: int) -> pd.DataFrame:
        return self._fetch("sessions", {"meeting_key": meeting_key})
    
    def get_drivers(self, session_key: int) -> pd.DataFrame:
        return self._fetch("drivers", {"session_key": session_key})
    
    def get_laps(self, session_key: int, driver_number: int = None) -> pd.DataFrame:
        params = {"session_key": session_key}
        if driver_number:
            params["driver_number"] = driver_number
        return self._fetch("laps", params)
    
    def get_stints(self, session_key: int) -> pd.DataFrame:
        return self._fetch("stints", {"session_key": session_key})
    
    def get_pit_stops(self, session_key: int) -> pd.DataFrame:
        return self._fetch("pit", {"session_key": session_key})
    
    def get_telemetry(self, session_key: int, driver_number: int) -> pd.DataFrame:
        """Get high-frequency car data (~3.7Hz)."""
        return self._fetch("car_data", {"session_key": session_key, "driver_number": driver_number})
    
    def get_location(self, session_key: int, driver_number: int) -> pd.DataFrame:
        """Get GPS location (~3.7Hz)."""
        return self._fetch("location", {"session_key": session_key, "driver_number": driver_number})
    
    def get_weather(self, meeting_key: int) -> pd.DataFrame:
        return self._fetch("weather", {"meeting_key": meeting_key})
    
    def get_intervals(self, session_key: int) -> pd.DataFrame:
        return self._fetch("intervals", {"session_key": session_key})
    
    def get_position(self, session_key: int) -> pd.DataFrame:
        return self._fetch("position", {"session_key": session_key})
    
    # Live Session Detection
    def get_latest_session(self) -> dict | None:
        """Check if a live session is currently active."""
        try:
            df = self._fetch("sessions", {"session_key": "latest"})
            if not df.empty:
                session = df.iloc[0]
                # Check if session is recent (within last 6 hours)
                start = pd.to_datetime(session['date_start'], utc=True)
                if (pd.Timestamp.now(tz='UTC') - start) < pd.Timedelta(hours=6):
                    return session.to_dict()
        except:
            pass
        return None
```

### 1.3 Data Source Manager (`data/source_manager.py`)

```python
class DataSourceManager:
    """Unified interface with automatic fallback: Live → Historical → Replay"""
    
    def __init__(self):
        self.fastf1 = FastF1Adapter()
        self.openf1 = OpenF1Adapter()
        self.replay_dir = Path("./replay_sessions")
        self.replay_dir.mkdir(exist_ok=True)
    
    def get_session_data(self, 
                         source: str = "auto",  # "auto", "fastf1", "openf1", "replay"
                         year: int = None, 
                         gp: str = None, 
                         session_type: str = None,
                         replay_file: str = None) -> dict:
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
            'source': 'fastf1'|'openf1'|'replay',
            'is_live': bool
        }
        """
        
        if source == "replay" and replay_file:
            return self._load_replay(replay_file)
        
        if source == "auto":
            # Try live first
            live_session = self.openf1.get_latest_session()
            if live_session:
                return self._load_openf1_session(live_session['session_key'], is_live=True)
            
            # Fallback: most recent completed race
            if not year:
                recent = self._get_most_recent_completed_race()
                year, gp, session_type = recent['year'], recent['gp'], recent['session_type']
        
        if source in ("auto", "fastf1"):
            return self._load_fastf1_session(year, gp, session_type)
        
        if source == "openf1":
            # Need to resolve meeting_key/session_key from year/gp/session_type
            meetings = self.openf1.get_meetings(year)
            meeting = meetings[meetings['meeting_name'].str.contains(gp, case=False)].iloc[0]
            sessions = self.openf1.get_sessions(meeting['meeting_key'])
            session = sessions[sessions['session_name'].str.contains(session_type, case=False)].iloc[0]
            return self._load_openf1_session(session['session_key'], is_live=False)
        
        raise ValueError(f"Unknown source: {source}")
    
    def _load_fastf1_session(self, year: int, gp: str, session_type: str) -> dict:
        session = self.fastf1.load_session(year, gp, session_type)
        drivers = session.results['Abbreviation'].tolist()
        
        return {
            'session_info': {
                'year': year, 'gp': gp, 'session_type': session_type,
                'session_name': session.name, 'date': session.date
            },
            'telemetry': {d: self.fastf1.get_telemetry(session, d) for d in drivers},
            'laps': self.fastf1.get_laps(session),
            'stints': self.fastf1.get_stints(session),
            'location': {d: self.fastf1.get_location(session, d) for d in drivers},
            'weather': self._get_weather_from_session(session),
            'drivers': self._get_driver_info(session),
            'source': 'fastf1',
            'is_live': False
        }
    
    def _load_openf1_session(self, session_key: int, is_live: bool) -> dict:
        drivers_df = self.openf1.get_drivers(session_key)
        driver_numbers = drivers_df['driver_number'].tolist()
        driver_acronyms = drivers_df['name_acronym'].tolist()
        
        return {
            'session_info': {
                'session_key': session_key,
                'is_live': is_live
            },
            'telemetry': {
                acr: self.openf1.get_telemetry(session_key, num) 
                for acr, num in zip(driver_acronyms, driver_numbers)
            },
            'laps': self.openf1.get_laps(session_key),
            'stints': self.openf1.get_stints(session_key),
            'location': {
                acr: self.openf1.get_location(session_key, num) 
                for acr, num in zip(driver_acronyms, driver_numbers)
            },
            'weather': self.openf1.get_weather(
                self.openf1.get_sessions(
                    self.openf1._fetch("sessions", {"session_key": session_key}).iloc[0]['meeting_key']
                ).iloc[0]['meeting_key']
            ),
            'drivers': drivers_df,
            'source': 'openf1',
            'is_live': is_live
        }
    
    def _get_most_recent_completed_race(self) -> dict:
        """Find most recent completed race from FastF1 schedule."""
        schedule = fastf1.get_event_schedule([2025, 2024, 2023])
        completed = schedule[schedule['EventDate'] < pd.Timestamp.now(tz='UTC')]
        if completed.empty:
            return {'year': 2024, 'gp': 'Abu Dhabi', 'session_type': 'R'}
        last_event = completed.iloc[-1]
        return {'year': last_event['Year'], 'gp': last_event['EventName'], 'session_type': 'R'}
    
    def save_replay(self, data: dict, name: str) -> str:
        """Save session data for offline replay."""
        filepath = self.replay_dir / f"{name}_{pd.Timestamp.now():%Y%m%d_%H%M%S}.parquet"
        # Convert DataFrames to parquet-compatible format
        save_data = {k: v.to_dict('records') if isinstance(v, pd.DataFrame) else v 
                     for k, v in data.items() if k != 'telemetry' and k != 'location'}
        save_data['telemetry'] = {k: v.to_dict('records') for k, v in data['telemetry'].items()}
        save_data['location'] = {k: v.to_dict('records') for k, v in data['location'].items()}
        with open(filepath, 'wb') as f:
            pickle.dump(save_data, f)
        return str(filepath)
    
    def _load_replay(self, filepath: str) -> dict:
        with open(filepath, 'rb') as f:
            data = pickle.load(f)
        # Reconstruct DataFrames
        for k in ['laps', 'stints', 'weather', 'drivers']:
            if k in data:
                data[k] = pd.DataFrame(data[k])
        data['telemetry'] = {k: pd.DataFrame(v) for k, v in data['telemetry'].items()}
        data['location'] = {k: pd.DataFrame(v) for k, v in data['location'].items()}
        data['source'] = 'replay'
        return data
```

---

## 2. Data Processing Layer

### 2.1 Telemetry Processor (`processing/telemetry_processor.py`)

```python
class TelemetryProcessor:
    """Processes raw telemetry into visualization-ready format."""
    
    DISTANCE_STEP = 5  # meters - uniform distance grid for alignment
    
    def __init__(self):
        self.driver_color_map = {}
    
    def build_driver_color_map(self, drivers_df: pd.DataFrame) -> dict:
        """Map driver acronyms to team colors (from FastF1 or OpenF1)."""
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
            # FastF1: boolean → 0/100, OpenF1: already 0/100
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
    st.caption("Historical (FastF1) • Live (OpenF1) • Replay (Local)")

def render_session_selector(data_manager: DataSourceManager) -> dict:
    """Session selection with live detection."""
    
    # Check for live session
    live_session = data_manager.openf1.get_latest_session()
    
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        source = st.selectbox(
            "Data Source",
            ["Auto (Live → Historical)", "FastF1 (Historical)", "OpenF1 (API)", "Replay (Saved)"],
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
            st.plotly_chart(fig, use_container_width=True)

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
        pit_out = driver_laps['IsPitOutLap'] == True
        
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
    
    st.plotly_chart(fig, use_container_width=True)

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
    
    st.plotly_chart(fig, use_container_width=True)

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
    
    st.plotly_chart(fig, use_container_width=True)
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
requests>=2.31.0
python-dotenv>=1.0.0

# Utilities
pyarrow>=16.0.0  # for parquet support in replay
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
# OpenF1 API (free tier for historical, paid for real-time)
OPENF1_BASE_URL=https://api.openf1.org/v1

# FastF1 Cache Directory
FASTF1_CACHE_DIR=./ff1_cache

# Replay Sessions Directory
REPLAY_DIR=./replay_sessions

# App Settings
DEFAULT_YEAR=2024
DEFAULT_GP=Abu Dhabi
DEFAULT_SESSION=R
```

### config.py (Runtime Config)
```python
from dataclasses import dataclass
import os

@dataclass
class Config:
    openf1_base_url: str = os.getenv("OPENF1_BASE_URL", "https://api.openf1.org/v1")
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
│   ├── openf1_adapter.py       # OpenF1 REST API client
│   └── source_manager.py       # Unified data source with fallback
├── processing/
│   ├── __init__.py
│   └── telemetry_processor.py  # Distance alignment, normalization
├── ui/
│   ├── __init__.py
│   └── layout.py               # Streamlit UI components
├── tests/
│   ├── __init__.py
│   ├── test_fastf1_adapter.py
│   ├── test_openf1_adapter.py
│   ├── test_telemetry_processor.py
│   └── test_source_manager.py
├── ff1_cache/                  # FastF1 local cache (gitignored)
├── replay_sessions/            # Saved replay files (gitignored)
└── .gitignore
```

---

## 7. Data Flow Summary

```
USER ACTION                          DATA FLOW
────────────────────────────────────────────────────────────────────────
1. Select Session                    → DataSourceManager.get_session_data()
2. Auto-detect Live?                 → OpenF1Adapter.get_latest_session()
   ├─ YES: Load OpenF1 Live          → OpenF1Adapter.get_*() endpoints
   └─ NO: Load Historical            → FastF1Adapter.load_session()
3. Process Telemetry                 → TelemetryProcessor.align_drivers_by_distance()
4. Normalize Units                   → TelemetryProcessor.normalize_units()
5. Build Color Map                   → TelemetryProcessor.build_driver_color_map()
6. Render Charts                     → UI layout functions → Plotly Figures
7. Optional: Save Replay             → DataSourceManager.save_replay()
```

---

## 8. Fallback Behavior Matrix

| Scenario | Primary Source | Fallback 1 | Fallback 2 | UI Indicator |
|----------|---------------|------------|------------|--------------|
| Race Weekend (Live) | OpenF1 Live | FastF1 Historical | Replay | 🔴 LIVE badge |
| Mid-week (No Live) | FastF1 Historical | OpenF1 Historical | Replay | "Showing 2024 Abu Dhabi GP" |
| API Failure | FastF1 Cache | Replay Files | Error | Warning toast |
| First Run (No Cache) | OpenF1 Historical | Sample Replay | Error | "Loading..." spinner |

---

## 9. Future Extensibility Points

1. **WebSocket Live Streaming**: Replace OpenF1 polling with FastF1 SignalR client for true real-time
2. **ML Anomaly Detection**: Add Isolation Forest module (per siddoboi architecture)
3. **MongoDB Persistence**: Replace file-based replay with MongoDB time-series
4. **Multi-Session Comparison**: Compare qualifying vs race pace
5. **Sector Time Analysis**: Add sector-by-sector delta charts
6. **Driver Profile Overlay**: Headshots, stats, career highlights
7. **Export Features**: CSV, PDF reports, telemetry bundles

---

*Architecture Complete. Ready for Phase 3: TDD Implementation.*