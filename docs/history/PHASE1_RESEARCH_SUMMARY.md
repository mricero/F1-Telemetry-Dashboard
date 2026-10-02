> **Superseded (kept for history).** This is the research note written before
> the first implementation. Several of its conclusions no longer hold: live mode
> does not use LiveF1 or the classic `/signalr/` hub (that hub answers 401 since
> 2025; the app has its own SignalR Core client for `/signalrcore`), replays are
> Parquet directories rather than pickles, and the app is replay-first. See
> `ARCHITECTURE.md` and `CLAUDE.md` for how the code works now.

# Phase 1: Research & Reconnaissance - Summary Report

## Executive Summary

Based on comprehensive research of **four** reference F1 telemetry repositories and official FastF1/OpenF1/LiveF1 documentation, I have identified the key architectural patterns, data flows, and fallback mechanisms needed to build a professional F1 Telemetry Dashboard supporting both real-time live telemetry (**free via FastF1 SignalR or LiveF1 package**) and historical race playback (FastF1).

**Critical Finding: OpenF1 live data requires paid subscription. Free alternatives exist:**
1. **FastF1 built-in SignalR client** (`fastf1.livetiming.SignalRClient`) - connects directly to `wss://livetiming.formula1.com/signalrcore` 
2. **LiveF1 package** (`pip install livef1`) - Python toolkit with `RealF1Client` using same SignalR endpoint + Jolpica F1 API for historical data
3. **Jolpica F1 API** - Free Ergast-compatible REST API for historical data (no auth needed)

---

## 1. Framework Research Findings

### FastF1 (Historical Data + Free Live Timing)
**Source:** https://github.com/theOehrly/Fast-F1, https://docs.fastf1.dev

**Key Capabilities:**
- **Local Caching**: FastF1 implements caching for ALL API requests automatically via `fastf1.Cache.enable_cache('cache_dir')`. Cached data is stored as pickle files in a local directory structure organized by year/event/session.
- **Data Access**: Provides access to F1 timing data, car telemetry, position, tyre data, weather data, event schedule, and session results.
- **Data Format**: Returns extended Pandas DataFrames with custom F1-specific methods.
- **Telemetry Channels**: Speed, Throttle, Brake, RPM, Gear, DRS, n_gear (7 channels at ~50Hz).
- **Live Timing (FREE)**: FastF1 includes a built-in SignalR client (`fastf1.livetiming.SignalRClient`) that connects directly to Formula 1's official livetiming endpoint:
  - WebSocket: `wss://livetiming.formula1.com/signalrcore`
  - Negotiate: `https://livetiming.formula1.com/signalrcore/negotiate`
  - Requires AWSALBCORS cookie from OPTIONS request
  - Supports topics: Heartbeat, AudioStreams, DriverList, ExtrapolatedClock, RaceControlMessages, SessionInfo, SessionStatus, TeamRadio, TimingAppData, TimingStats, TrackStatus, WeatherData, Position.z, CarData.z, ContentStreams, SessionData, TimingData, TopThree, RcmSeries, LapCount
  - Saves raw SignalR data to file for later replay
  - **No paid subscription needed** - uses official F1 feed directly
- **Session Loading**: `fastf1.get_session(year, gp, session_type)` loads sessions; `.load()` fetches data (with caching).

**SignalR Client Usage:**
```python
from fastf1.livetiming.client import SignalRClient

client = SignalRClient(
    filename="session_data.txt",
    filemode="w",
    timeout=60,
    no_auth=False  # Uses F1 auth token
)
client.start()  # Blocks until timeout or Ctrl+C
```

### LiveF1 Package (Free Live + Historical)
**Source:** https://github.com/GoktugOcal/LiveF1, https://pypi.org/project/livef1/

**Key Capabilities:**
- **RealF1Client**: SignalR-based live client with async callback handlers
- **Same SignalR endpoint**: `wss://livetiming.formula1.com/signalrcore` with same topics
- **Historical Data**: Uses Jolpica F1 API (Ergast-compatible) for seasons, calendars, drivers, constructors, results, standings
- **Data Processing**: Medallion architecture (Bronze→Silver→Gold) for ETL
- **Topics Supported**: CarData.z, Position.z, SessionInfo, TrackStatus, WeatherData, RaceControlMessages, LapSeries, TimingData, DriverList, TyreStintSeries, PitLaneTimeCollection, CurrentTyres, TeamRadio, etc.
- **Installation**: `pip install livef1`

**RealF1Client Usage:**
```python
from livef1.adapters import RealF1Client

client = RealF1Client(
    topics=["CarData.z", "Position.z"],
    log_file_name="race_data.json"
)

@client.callback("telemetry_handler")
async def handle_data(records):
    for record in records:
        print(record)

client.run()  # Blocks, handles async internally
```

### OpenF1 API (Historical Free, Live Paid)
**Source:** https://openf1.org/docs/

**Key Endpoints (REST + Filtering):**
- Historical data (2023+) is **free** and accessible without authentication
- Real-time data requires **paid subscription** (~30s delay on free tier)
- Same endpoints as documented before

### Jolpica F1 API (Free Historical)
**Used by LiveF1 package**
- Ergast-compatible REST API
- Free, no auth needed
- Seasons, calendars, drivers, constructors, results, standings
- Base URL: `https://api.jolpi.ca/ergast/f1/`

---

## 2. Codebase Analysis - Reference Repositories

### A. bordanattila/OpenF1_tutorial (Streamlit + Plotly + OpenF1)
**Architecture:**
```
app/
├── data_loader.py       # OpenF1 API calls with @st.cache_data
├── data_processor.py    # Clean/transform data, build driver_color_map
└── visualizer.py        # Plotly charts (lap times, tire strategy, pit stops)
main.py                  # Streamlit UI: year/country/session selectors

**Key Patterns:** @st.cache_data on all fetches; Year -> Country -> GP -> Session dropdowns;
driver acronym -> team color mapping for consistent Plotly theming.
**Live Session Handling:** No explicit live mode - uses historical OpenF1 data only.

### B. f1stuff/f1-live-data (FastF1 Live Timing + InfluxDB + Grafana)
**Architecture:**
```
src/dataimporter/        # FastF1 live timing client
storage/                 # Pre-built Grafana dashboards + InfluxDB config
saves/                   # Recorded session files (.txt)
docker-compose.yaml      # InfluxDB + Grafana

**Key Patterns:** Two modes - process-live-session (real-time FastF1 SignalR) and
process-mock-data (replay saved .txt files with --speedup); data flows into
InfluxDB visualized by Grafana.

### C. siddoboi/f1-telemetry-dashboard (FastAPI + React + ML Anomaly Detection)
**Architecture:**
```
backend/app/             # FastAPI REST + WebSocket (replay/live/history)
backend/ml/              # Isolation Forest anomaly detection + physics rules
frontend/src/components/ # TelemetryCharts, TrackMapView, SessionView (Recharts)

**Key Innovations:** Distance-based alignment (5m grid), Isolation Forest trained on a
baseline lap, three data modes (FastF1 historical / OpenF1 polling / MongoDB replay),
10Hz WebSocket replay engine with pause/speed/seek.
Note: its claim that "no true real-time feed exists publicly" is outdated -
FastF1 SignalR and LiveF1 both provide free live telemetry.

### D. GoktugOcal/LiveF1 (LiveF1 Package - NEW REFERENCE)
**Architecture:**
```
livef1/
├── adapters/
│   ├── realtime_client.py      # RealF1Client - SignalR async client
│   ├── livetimingf1_adapter.py # Static Livetiming API (historical)
│   ├── jolpicaf1_adapter.py    # Jolpica F1 API (historical)
│   ├── signalr_aio/            # SignalR async implementation
│   └── functions.py            # SignalR message handlers
├── data_processing/            # Medallion ETL (Bronze→Silver→Gold)
├── models/                     # Data models
└── utils/                      # Constants, logger, exceptions
```

**Key Patterns:**
- **RealF1Client** uses `signalrcore` library for async SignalR connection
- Callbacks registered via `@client.callback("method_name")` decorator
- MessageHandlerTemplate parses SignalR "R" (topic data) and "M" (method calls) formats
- Uses `function_map` from `data_processing.etl` for data parsing
- Supports logging to file for replay

---

## 3. Edge Case: No Live Session Active (Fallback Strategy)

**Problem:** F1 live timing data is ONLY available during actual race weekends (Friday-Sunday). 90% of the week, there's no live data.

**Solutions from References:**

| Repository | Fallback Approach |
|------------|-------------------|
| **bordanattila/OpenF1_tutorial** | No live mode - only historical OpenF1 data (always available for 2023+) |
| **f1stuff/f1-live-data** | `process-mock-data` mode: replays saved `.txt` files from previous sessions |
| **siddoboi/f1-telemetry-dashboard** | Three-tier: (1) FastF1 historical (always works), (2) OpenF1 live (race weekends only), (3) MongoDB instant replay (saved laps) |
| **LiveF1 (GoktugOcal)** | Historical via Jolpica F1 API + saved SignalR logs |

**Recommended Fallback Architecture for Our Dashboard:**

1. **Primary: FastF1 Historical Cache** (Always Available)
   - Enable FastF1 local caching: `fastf1.Cache.enable_cache('./ff1_cache')`
   - Pre-populate cache with recent seasons (2023-2025) during setup
   - Default UI loads most recent completed race

2. **Secondary: Jolpica F1 API / OpenF1 Historical** (Always Available for 2023+)
   - Free, no auth needed
   - Query meetings, sessions, laps, results

3. **Tertiary: Session Replay from Saved SignalR Data**
   - **Live Recording**: Use `SignalRClient` or `RealF1Client` to save live sessions to `.txt`/`.json` files
   - Provide pre-recorded sample sessions (e.g., 2024 Bahrain GP Qualifying)
   - Replay engine simulates real-time streaming from saved data

4. **Live Detection Logic:**
   ```python
   def check_live_session():
       """Check if F1 livetiming has active session via SignalR"""
       # Try connecting to SignalR - if no data for 60s, no live session
       # Or check Jolpica/OpenF1 for sessions starting within last 6 hours
       pass
   
   # UI Logic:
   if live_session_available():
       start_signalr_client(topics=["CarData.z", "Position.z", "TimingData", ...])
   else:
       show_historical_mode()  # Default to most recent cached race
   ```

5. **UI State Management:**
   - Default landing page: "2024 Abu Dhabi GP - Race" (most recent completed)
   - Live indicator: 🔴 LIVE badge when active session detected
   - Graceful messaging: "No live session currently. Showing 2024 Abu Dhabi GP Race."
   - One-click "Replay Last Race" button
   - "Record Live Session" button when live (saves to replay_sessions/)

---

## 4. Key Technical Decisions for Phase 2 Architecture

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **UI Framework** | Streamlit + Plotly | Proven in bordanattila/OpenF1_tutorial; rapid prototyping, built-in caching, interactive Plotly |
| **Historical Data** | FastF1 (primary) + Jolpica/OpenF1 (fallback) | FastF1 has richer telemetry + local caching; Jolpica is free Ergast-compatible REST |
| **Live Data (FREE)** | **FastF1 SignalRClient** (built-in) OR **LiveF1 RealF1Client** | Both use official F1 SignalR feed `wss://livetiming.formula1.com/signalrcore` - no paid subscription! |
| **Telemetry Channels** | Speed, Throttle, Brake, RPM, Gear, DRS | Available from both FastF1 (`car_data`) and LiveF1 (`CarData.z`) |
| **Caching Strategy** | FastF1 disk cache + Streamlit `@st.cache_data` | Dual-layer: FastF1 caches API responses; Streamlit caches processed DataFrames |
| **Distance Alignment** | Resample to uniform distance grid (5m) | From siddoboi: enables accurate multi-driver comparison at same track position |
| **Data Format** | Pandas DataFrames → Plotly Figures | Consistent with all reference implementations |
| **Session Selection** | Year → Country → GP → Session dropdowns | Proven UX pattern from bordanattila tutorial |
| **Replay Storage** | Local JSON/Parquet files in `./replay_sessions/` | Simple, no DB dependency, portable |

---

## 5. Sources Referenced

1. **FastF1 GitHub** - https://github.com/theOehrly/Fast-F1 (README, fastf1/livetiming/client.py, examples/)
2. **FastF1 Docs** - https://docs.fastf1.dev (features, caching, getting started)
3. **OpenF1 API Docs** - https://openf1.org/docs/ (all endpoints, filtering, CSV format)
4. **bordanattila/OpenF1_tutorial** - https://github.com/bordanattila/OpenF1_tutorial (app/data_loader.py, data_processor.py, visualizer.py, main.py)
5. **f1stuff/f1-live-data** - https://github.com/f1stuff/f1-live-data (README, docker-compose, dataimporter modes)
6. **siddoboi/f1-telemetry-dashboard** - https://github.com/siddoboi/f1-telemetry-dashboard (README, architecture diagram, backend/app structure)
7. **LiveF1 Package** - https://github.com/GoktugOcal/LiveF1 (README, livef1/adapters/realtime_client.py, livetimingf1_adapter.py, examples/)
8. **LiveF1 PyPI** - https://pypi.org/project/livef1/ (installation, quickstart, real-time/historical examples)

---

*Phase 1 Complete. Ready for Phase 2: Architecture Design (ARCHITECTURE.md).*

---
