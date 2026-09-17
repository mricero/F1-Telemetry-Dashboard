# 🏎️ F1 Telemetry Dashboard

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-FF4B4B.svg)](https://streamlit.io/)
[![Plotly](https://img.shields.io/badge/Plotly-5.22+-3F4F75.svg)](https://plotly.com/)
[![FastF1](https://img.shields.io/badge/FastF1-3.8+-black.svg)](https://docs.fastf1.dev/)
[![LiveF1](https://img.shields.io/badge/LiveF1-1.2+-red.svg)](https://pypi.org/project/livef1/)

Welcome to the **F1 Telemetry Dashboard**, a professional-grade visualization tool for Formula 1 data. This dashboard is built with **Streamlit** and **Plotly**, designed to provide comprehensive, high-frequency telemetry insights—whether for historical races or real-time live timing.

Unlike many other platforms that require paid subscriptions for real-time F1 data (such as OpenF1's live tier), this project successfully leverages completely **FREE alternatives** to stream live telemetry directly from the official F1 feeds. It also utilizes comprehensive historical APIs to ensure the app never feels "empty" when no live race is happening.

---

## 🌟 Key Features

### 1. **Historical Race Playback**
Dive deep into the archives using the powerful `FastF1` library. 
- Fully cached telemetry data ensures rapid load times on subsequent requests.
- View detailed car data (speed, throttle, brake, RPM, gear, DRS) aligned by track distance for pixel-perfect accuracy.
- Includes historical session metadata, lap times, tire stints, and track conditions.

### 2. **Real-Time Live Telemetry**
This is an **unofficial** project reading undocumented endpoints; F1 does not support this use.

- **Which endpoint:** live mode currently goes through `LiveF1`'s `RealF1Client`, which connects to the **legacy** `https://livetiming.formula1.com/signalr/` hub. F1 moved live timing to `/signalrcore` during 2025 and FastF1 3.7+ documents the old endpoints as deprecated, so **live mode may simply not connect**. Replacing the client is tracked as LIVE-01 in `IMPROVEMENTS.md`.
- **What needs a subscription token:** since the 2025 Dutch GP, car telemetry (`CarData.z`), positions (`Position.z`), pit-stop times, championship prediction and team radio require a valid F1TV token. Set your own in `F1TV_SUBSCRIPTION_TOKEN` - it stays on your machine, and those topics are simply not subscribed without it.
- **What works without one:** timing, tyres, race control, weather, track status and the driver list. The live view renders all of them and tells you what is missing.
- **Running it publicly is a risk:** F1 has IP-blocked heavy and hosted consumers (f1-dash sunset citing "increasing IP restrictions"; matteocelani/f1-telemetry's hosted instance is down "due to IP blocking by Formula 1"). Run it locally, with one connection, on your own token.
- Subscribes to topics such as `CarData.z`, `Position.z`, `TimingData`, `TyreStintSeries`, `WeatherData`, `DriverList`, and more in real time.
- The live view auto-refreshes every 3 seconds: telemetry channels, GPS track map with driver trails, tyre stints and timing all update while the session runs.

### 3. **Intelligent Fallback Architecture**
F1 live timing data is only broadcasted during active race weekends (Friday-Sunday). Our application handles the remaining 90% of the time gracefully:
- **Auto-Detection**: Instantly determines if an F1 live session is active based on real-time endpoint probing.
- **Graceful Degradation**: If no live session is detected, it falls back to the most recently completed Grand Prix.
- **Always Active**: You always land on a rich, data-filled dashboard rather than a blank screen.

### 4. **Session Recording & Offline Replay**
Catching a race live but want to analyze it later? 
- Our built-in local storage allows you to save live SignalR sessions as pickle (`.pkl`) files in `./replay_sessions/` (with a schema header so old files keep loading across app versions).
- Replay mode reloads these pre-recorded sessions, perfect for development or offline analysis without needing internet access.
- To verify SignalR connectivity during a race weekend, run `python scripts/live_smoke.py 30`.

### 5. **Two-Tier Caching: Hot Session Cache + Persistent Records**
- **Runtime cache (memory only)**: every session you load during a visit stays hot in memory, so switching between sessions or re-selecting one is instant. When you close the app the cache is discarded - the next launch always starts completely fresh. No stale data ever survives a restart.
- **Persistent metrics store** (`./metrics_store.json`): derived records - fastest lap, fastest sector per sector (S1/S2/S3) and top speed per driver - are written to disk and kept across restarts. Reopen the app and your benchmark times are still there, ready to beat.
- The 🏆 panel in the app shows both *this session's* records and *all-time* bests across every session you have ever viewed.

### 5. **Advanced Interactive Visualizations**
We use Plotly for deep interactivity and responsive charts:
- **Telemetry Channels**: Multi-driver line and step charts comparing Speed (km/h), Throttle (%), Brake (%), RPM, Gear, and DRS.
- **Distance Resampling**: Telemetry is interpolated onto a uniform 5-meter grid, ensuring accurate multi-driver comparison at the exact same track position.
- **Lap Analysis**: Detailed lap time progression charts with pit-stop indicators.
- **Tire Strategy**: Stacked horizontal bar charts displaying compound usage and stint lengths per driver.
- **GPS Track Map**: Renders the circuit outline with real-time or historical driver positional markers (`X`, `Y` coordinates) for a spatial understanding of the race.

---

## 🏗️ System Architecture

The application is structured into a modern layered architecture, separating UI, processing, and data ingestion.

```text
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
│  FASTF1       │         │   LIVEF1      │         │   LOCAL       │
│  (Historical) │         │   (SignalR)   │         │   REPLAY      │
└───────────────┘         └───────────────┘         └───────────────┘
```

### Data Sources breakdown:
- **Primary Historical**: `FastF1` (Pickle caching to avoid rate limiting).
- **Secondary Historical**: `Jolpica F1 API` (Ergast-compatible REST) used as a fallback for schedule and calendar data.
- **Live**: F1's live timing hub via `LiveF1`'s `RealF1Client`, which targets the legacy `/signalr/` endpoint (F1 moved to `/signalrcore`; see LIVE-01). One connection per process, shared by every browser tab.

---

## 📁 Repository Structure

Below is an overview of the key directories and files in this repository:

```text
.
├── app.py                     # Main Streamlit application entry point (UI setup)
├── config.py                  # Global runtime configuration and environments
├── requirements.txt           # Core Python dependencies
├── requirements-dev.txt       # Development and testing dependencies
├── pytest.ini                 # Pytest configuration (tests live in tests/)
├── ARCHITECTURE.md            # In-depth architectural breakdown & data flows
├── PHASE1_RESEARCH_SUMMARY.md # Research notes on Free APIs vs Paid APIs
├── LICENSE                    # MIT license
├── data/                      # Data Ingestion Layer
│   ├── __init__.py
│   ├── source_manager.py      # Unified interface (Auto/Live/FastF1/LiveF1/Replay)
│   ├── fastf1_adapter.py      # Historical loading & caching via FastF1
│   ├── jolpica_adapter.py     # Free Ergast-compatible REST API fallback (Jolpica)
│   ├── live_adapter.py        # SignalR implementation for Live Timing (LiveF1 / FastF1)
│   └── runtime_cache.py       # Ephemeral hot cache - cleared on every app restart
├── processing/                # Data Processing Layer
│   ├── __init__.py
│   ├── telemetry_processor.py # Distance alignment, unit fixing, formatting
│   └── metrics_store.py       # Persistent fastest lap/sector/top-speed records
├── ui/                        # UI Components (Custom Plotly wrappers)
│   ├── __init__.py
│   └── layout.py              # Canonical Streamlit UI layout components
├── scripts/                   # Manual inspection utilities (not run by pytest)
│   ├── inspect_fastf1.py      # Explore FastF1 session data structures
│   └── inspect_livef1.py      # Explore LiveF1 session data structures
├── tests/                     # Test suites (adapters, cache, metrics, live parsing)
├── ff1_cache/                 # [Auto-generated] FastF1 persistent cache
├── replay_sessions/           # [Auto-generated] Directory for saved replays (.pkl)
└── metrics_store.json         # [Auto-generated] Persistent performance records
```

---

## 🚀 Setup & Installation

Follow these steps to get the dashboard running on your local machine.

### 1. Prerequisites
- **Python 3.11+** is required. We rely on recent Python features to ensure compatibility with recent Pandas and FastF1 versions.
- Git.

### 2. Clone the Repository
```bash
git clone https://github.com/your-username/f1-telemetry-dashboard.git
cd f1-telemetry-dashboard
```

### 3. Setup Virtual Environment
It is highly recommended to isolate dependencies using a virtual environment to prevent conflicts with global packages.

```bash
# Create the virtual environment named .venv
python -m venv .venv

# Activate on Windows
.venv\Scripts\activate

# Activate on macOS/Linux
source .venv/bin/activate
```

### 4. Install Dependencies
Install all required packages from `requirements.txt`.

```bash
pip install -r requirements.txt
```
*(Dependencies include `streamlit`, `plotly`, `pandas`, `fastf1`, `livef1`, and `requests`.)*

If you plan to run the test suite, also install the development dependencies:
```bash
pip install -r requirements-dev.txt
```

Run the tests with:
```bash
pytest
```
(Pytest is configured via `pytest.ini` to collect only from `tests/`, so the network-dependent inspection scripts in `scripts/` are never executed automatically.)

### 5. Setup Cache Directories
The application will automatically create `./ff1_cache` and `./replay_sessions` if they do not exist. Ensure your user has write permissions to the repository folder.

---

## 🖥️ Running the Application

To launch the dashboard, run the Streamlit command from the root directory:

```bash
streamlit run app.py
```

This will spin up a local web server and automatically open the dashboard in your default browser at `http://localhost:8501`.

**`python app.py` works too.** The plain interpreter would otherwise leave Streamlit in "bare mode" — widgets return defaults, session state is unavailable and `st.stop()` does nothing, which turns a failed data load into a confusing crash further down. Rather than refusing, `app.py` detects this and re-enters through Streamlit's own CLI, so hitting **Run** in an IDE starts the dashboard normally. Extra flags pass straight through:

```bash
python app.py --server.port 8600
```

### Navigating the App

1. **Session Selection Panel (Top)**:
   - Choose your **Data Source**: Auto, FastF1 (Historical), LiveF1, Live (SignalR), or Replay.
   - If a live session is active, a red `🔴 LIVE SESSION DETECTED` badge will appear automatically.
   - If in Historical mode, select the **Season**, **Grand Prix**, and **Session** (FP1, FP2, FP3, Q, S, R).
   - Pick a **Telemetry scope**: *Fastest lap* (default — every driver's quickest lap on a shared `0 → lap length` axis, so the charts are directly comparable) or *Full session* (every lap, distance accumulating across the whole run).

2. **Telemetry Tabs**:
   - Flip through `Speed`, `Throttle`, `Brake`, `RPM`, `Gear`, and `DRS` tabs. 
   - Hover over the charts to see precise values. 
   - You can **double-click a driver in the legend** to isolate their data and hide everyone else.
   - You can **click and drag** on the charts to zoom into specific corners or straights.

3. **Driver Comparison**:
   - Pick any two drivers for a head-to-head speed trace plus a cumulative time delta along the lap, so you can see exactly where one gains or loses.
   - The delta is integrated from the speed traces (`ds / v` per step). `fastf1.utils.delta_time` is deprecated since FastF1 3.0 and emits a `FutureWarning`, so it is not used. Expect the result to land within roughly 0.1–0.3 s of the true lap-time gap — read exact gaps off the lap times themselves.

4. **Lap Times & Strategy**:
   - The lap time progression chart marks pit-out laps with red diamonds.
   - **Position Changes** plots the running order lap by lap (P1 at the top) — the clearest view of who actually made progress.
   - The tire strategy chart uses FastF1's official per-season compound colours rather than a hardcoded table.

5. **Track Map**:
   - The interactive track map plots `X` and `Y` telemetry data to draw the circuit and overlays driver positions. The axes are locked to a 1:1 aspect ratio so circuits are not distorted by the container's shape. Useful for spotting traffic during qualifying or visualizing gaps on track during a race.

6. **Weather & Race Control**:
   - Current air/track temperature, humidity, wind and pressure, plus a temperature trace across the session and a rainfall warning.
   - The full race control feed — flags, safety cars, investigations and penalties — filterable by category, newest first. In live mode the current track status (green / yellow / SC / VSC / red) is shown as a banner.

---

## ⚙️ Configuration

The dashboard behavior can be tailored using `config.py` or environment variables. This makes it easy to deploy the app via Docker or Streamlit Cloud.

| Environment Variable | Default Value | Description |
|----------------------|---------------|-------------|
| `FASTF1_CACHE_DIR`   | `./ff1_cache` | Path where FastF1 saves API payloads. |
| `REPLAY_DIR`         | `./replay_sessions` | Path where live session recordings are saved. |
| `DEFAULT_YEAR`       | `2024` | Fallback year when the app first loads. |
| `DEFAULT_GP`         | `Abu Dhabi` | Fallback race location. |
| `DEFAULT_SESSION`    | `R` | Fallback session type (`R` = Race). |
| `F1_METRICS_STORE`   | `./metrics_store.json` | Where fastest lap/sector/top-speed records are persisted. |

You can also adjust `distance_step` in `config.py` (default: 5 meters). Lowering this number (e.g. to 1 or 2 meters) increases chart resolution but uses significantly more memory. Increasing it improves performance on slower devices or low-bandwidth connections.

---

## 🛠️ Technical Deep Dive

### Distance Resampling (`processing/telemetry_processor.py`)
F1 cars cross the start/finish line at different times. If we plot Speed against Time, the data won't align. For example, if we want to compare Verstappen and Hamilton braking into Turn 1, we must convert Time to Track Distance. 
We generate a uniform mathematical grid (e.g., every 5 meters) and use `numpy.interp` to resample the telemetry for every driver onto this exact grid. This enables perfectly aligned X-axes on all Plotly charts, ensuring an apples-to-apples comparison.

Two details matter for that comparison to mean anything:

- **Telemetry scope.** FastF1's `Distance` channel accumulates over whatever laps you ask for. Across a full race that reaches ~300 km, so "the same X value" is not the same corner for two drivers — and 20 drivers × 60,000 points is far more than a browser will happily draw. The **Telemetry scope** control therefore defaults to *Fastest lap*, where distance runs `0 → lap length` and drivers genuinely line up at the same track position. *Full session* is still available when you want the whole run.
- **Discrete channels are not interpolated.** Gear, DRS and Brake are coded values — gear 4.7 does not exist. Those channels take the nearest sample; only Speed, Throttle and RPM are linearly interpolated.

### Pit-out laps
FastF1 has no `IsPitOutLap` column; it exposes pit activity as the `PitOutTime` / `PitInTime` timestamps. The adapter derives the boolean flag from `PitOutTime`, which is what the lap-time chart's diamond markers key off.

### A note on 2026 data
DRS was removed under the 2026 technical regulations (replaced by active-aero X/Z modes plus a manual-override power boost), so the DRS channel reads `0` throughout for 2026 sessions. That is the source data, not a parsing fault.

### The SignalR WebSocket
Formula 1's live timing uses Microsoft's SignalR protocol.
- **Endpoint**: what this app uses today is the legacy hub at `https://livetiming.formula1.com/signalr/` (via LiveF1). FastF1 uses the current `wss://livetiming.formula1.com/signalrcore`; moving over is LIVE-01.
- We subscribe to the Hub and listen for `M` (Method) and `R` (Data) packets.
- **Critical topics**: `CarData.z` (compressed telemetry), `Position.z` (compressed GPS), and `SessionInfo`.
- The data comes base64 encoded and zlib compressed, which our adapters automatically decompress and decode into Pandas DataFrames.

### Medallion Data Architecture
The data ingestion follows a Bronze → Silver → Gold ETL pattern:
- **Bronze**: Raw JSON/Zlib data direct from the SignalR websocket.
- **Silver**: Decoded, unnested dictionaries separated into topics.
- **Gold**: Resampled, aligned Pandas DataFrames merged with driver metadata, ready for Plotly.

---

## 🛑 Troubleshooting

- **`fastf1.core.DataNotLoadedError`**: This means the session data hasn't fully downloaded or cached. Try clearing your `./ff1_cache` directory and running again.
- **SignalR Connection Timeouts**: The official F1 servers can sometimes drop connections. If Live mode gets stuck, refresh the Streamlit app.
- **Memory Errors**: If plotting all 20 drivers causes Streamlit to crash, try increasing `distance_step` in `config.py` to `10` or `15`.

---

## 🤝 Contributing

Contributions are highly welcome! If you'd like to improve the dashboard:
1. Fork the repository.
2. Create a new branch (`git checkout -b feature/amazing-feature`).
3. Commit your changes (`git commit -m 'Add some amazing feature'`).
4. Push to the branch (`git push origin feature/amazing-feature`).
5. Open a Pull Request.

Please ensure you run tests (`pytest tests/`) before submitting your PR.

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

*Disclaimer: This project is unofficial and is not associated in any way with the Formula 1 companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks are trade marks of Formula One Licensing B.V.*
