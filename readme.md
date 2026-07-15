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

### 2. **Real-Time Live Telemetry (100% Free)**
The dashboard connects directly to the official Formula 1 live timing endpoint (`wss://livetiming.formula1.com/signalrcore`).
- Uses the `FastF1 SignalRClient` and the `LiveF1` package.
- No paid subscription is required.
- Receives topics such as `CarData.z`, `Position.z`, `TimingData`, `SessionInfo`, and more in real-time.

### 3. **Intelligent Fallback Architecture**
F1 live timing data is only broadcasted during active race weekends (Friday-Sunday). Our application handles the remaining 90% of the time gracefully:
- **Auto-Detection**: Instantly determines if an F1 live session is active based on real-time endpoint probing.
- **Graceful Degradation**: If no live session is detected, it falls back to the most recently completed Grand Prix.
- **Always Active**: You always land on a rich, data-filled dashboard rather than a blank screen.

### 4. **Session Recording & Offline Replay**
Catching a race live but want to analyze it later? 
- Our built-in local JSON/Parquet storage allows you to save live SignalR sessions.
- Replay mode simulates real-time streaming using these pre-recorded files, perfect for development or offline analysis without needing internet access.

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
- **Live**: Official `wss://livetiming.formula1.com/signalrcore` via `SignalRClient`.

---

## 📁 Repository Structure

Below is an overview of the key directories and files in this repository:

```text
.
├── app.py                     # Main Streamlit application entry point (UI setup)
├── config.py                  # Global runtime configuration and environments
├── requirements.txt           # Core Python dependencies
├── requirements-dev.txt       # Development and testing dependencies
├── ARCHITECTURE.md            # In-depth architectural breakdown & data flows
├── PHASE1_RESEARCH_SUMMARY.md # Research notes on Free APIs vs Paid APIs
├── data/                      # Data Ingestion Layer
│   ├── __init__.py
│   ├── source_manager.py      # Unified interface (Auto/Live/History/Replay)
│   ├── fastf1_adapter.py      # Historical loading & caching via FastF1
│   ├── live_adapter.py        # SignalR implementation for Live Timing
│   └── jolpica_adapter.py     # Free Ergast-compatible REST API fallback
├── processing/                # Data Processing Layer
│   ├── __init__.py
│   └── telemetry_processor.py # Distance alignment, unit fixing, formatting
├── ui/                        # UI Components (Custom Plotly wrappers)
├── hooks/                     # Custom lifecycle hooks
├── rules/                     # Linting and repository rules
├── skills/                    # Custom agent instructions
├── tests/                     # Test suites for Live and FastF1 adapters
│   ├── test_fastf1.py
│   └── test_livef1.py
├── ff1_cache/                 # [Auto-generated] FastF1 persistent cache
└── replay_sessions/           # [Auto-generated] Directory for saved replays
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
*(Dependencies include `streamlit`, `plotly`, `pandas`, `fastf1`, `livef1`, `requests`, and `pyarrow` for replay storage.)*

If you plan to run the test suite, also install the development dependencies:
```bash
pip install -r requirements-dev.txt
```

### 5. Setup Cache Directories
The application will automatically create `./ff1_cache` and `./replay_sessions` if they do not exist. Ensure your user has write permissions to the repository folder.

---

## 🖥️ Running the Application

To launch the dashboard, run the Streamlit command from the root directory:

```bash
streamlit run app.py
```

This will spin up a local web server and automatically open the dashboard in your default browser at `http://localhost:8501`.

### Navigating the App

1. **Session Selection Panel (Top)**:
   - Choose your **Data Source**: Auto, FastF1 (Historical), LiveF1, Live (SignalR), or Replay.
   - If a live session is active, a red `🔴 LIVE SESSION DETECTED` badge will appear automatically.
   - If in Historical mode, select the **Season**, **Grand Prix**, and **Session** (FP1, FP2, FP3, Q, S, R).

2. **Telemetry Tabs**:
   - Flip through `Speed`, `Throttle`, `Brake`, `RPM`, `Gear`, and `DRS` tabs. 
   - Hover over the charts to see precise values. 
   - You can **double-click a driver in the legend** to isolate their data and hide everyone else.
   - You can **click and drag** on the charts to zoom into specific corners or straights.

3. **Lap Times & Strategy**:
   - Scroll down to view the lap time progression chart (which includes pit-out indicators).
   - View the stacked bar chart for tire strategy to see who ran softs/mediums/hards, when they pitted, and for how many laps.

4. **Track Map**:
   - The interactive track map plots `X` and `Y` telemetry data to draw the circuit and overlays driver positions. This is incredibly useful for spotting traffic during qualifying or visualizing gaps on track during a race.

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

You can also adjust `distance_step` in `config.py` (default: 5 meters). Lowering this number (e.g. to 1 or 2 meters) increases chart resolution but uses significantly more memory. Increasing it improves performance on slower devices or low-bandwidth connections.

---

## 🛠️ Technical Deep Dive

### Distance Resampling (`processing/telemetry_processor.py`)
F1 cars cross the start/finish line at different times. If we plot Speed against Time, the data won't align. For example, if we want to compare Verstappen and Hamilton braking into Turn 1, we must convert Time to Track Distance. 
We generate a uniform mathematical grid (e.g., every 5 meters) and use `numpy.interp` to resample the telemetry for every driver onto this exact grid. This enables perfectly aligned X-axes on all Plotly charts, ensuring an apples-to-apples comparison.

### The SignalR WebSocket
Formula 1's live timing uses Microsoft's SignalR protocol.
- **Endpoint**: `wss://livetiming.formula1.com/signalrcore`
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
