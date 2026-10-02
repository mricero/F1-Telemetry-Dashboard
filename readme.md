# F1 Telemetry Dashboard

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.55+-FF4B4B.svg)](https://streamlit.io/)
[![FastF1](https://img.shields.io/badge/FastF1-3.8+-black.svg)](https://docs.fastf1.dev/)

A Formula 1 replay, timing and telemetry dashboard built on Streamlit. Pick any session since 2018 and replay it from lights out to the chequered flag on a timing tower and track map, read the final classification and tyre strategy, and dig into telemetry, lap times, race traces and tyre pace. During a race weekend it reads F1's live timing feed directly.

This is an **unofficial** project. It reads public but undocumented F1 endpoints; F1 does not support this use (see [Data sources and terms](#data-sources-and-terms)).

---

## Install

The app installs as a [uv](https://docs.astral.sh/uv/) tool: uv downloads a matching Python itself and keeps the app in its own environment. Nothing touches your system Python.

**Windows** (PowerShell, one line; installs uv if needed, then the app and a Start-menu shortcut "F1 Replay"):

```powershell
irm https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.ps1 | iex
```

**macOS / Linux:**

```bash
curl -LsSf https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.sh | sh
```

**By hand, any OS** (after [installing uv](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard
```

To try it without installing anything permanent:

```bash
uvx --from git+https://github.com/mricero/F1-Telemetry-Dashboard f1dash
```

## Run

```bash
f1dash                    # opens the dashboard in your browser
f1dash --port 8600        # another port (the default 8501 falls back to the next free one)
f1dash --no-browser       # start the server only
f1dash --version
```

## Update

```bash
f1dash update
```

This reinstalls the newest release with uv (`uv tool install --reinstall git+https://github.com/mricero/F1-Telemetry-Dashboard@<tag>`). The app also checks for a newer release at most once a day and names it in the sidebar; set `F1_UPDATE_CHECK=0` to turn that off.

## Uninstall

```bash
uv tool uninstall f1dash
```

Your cache, replays and records stay where `f1dash paths` says; delete those folders too for a clean removal.

## Where your data lives

```bash
f1dash paths
```

prints the four locations. An installed copy uses the per-user folders of your OS (via `platformdirs`): the FastF1 download cache, saved replays, the records database (`metrics_store.sqlite`) and the `.env` settings file. A git checkout keeps them in the project folder instead (`ff1_cache/`, `replay_sessions/`, `metrics_store.sqlite`, `.env`). `FASTF1_CACHE_DIR`, `REPLAY_DIR` and `F1_METRICS_STORE` override either. The **Settings** page in the app shows the same locations, the size of the FastF1 cache, and buttons to clear the cached schedules, the loaded sessions and the download cache.

## Live timing token

Timing, tyres, race control, weather, track status and the driver list need no account. Car telemetry (`CarData.z`) and positions (`Position.z`) need an F1TV subscription since 2025. To use yours, set `F1TV_SUBSCRIPTION_TOKEN` in the `.env` file (or paste it into the **Subscription token** box on the Live page, which writes the same line):

1. Sign in on formula1.com.
2. Open the browser's developer tools, then Application (or Storage), then Cookies.
3. Copy the value of the `login-session` cookie (or the JWT inside it).

The token stays on your machine and is never logged. Tokens last a few days; the Live page shows how many remain, and an expired one is not sent, so the free topics keep flowing.

---

## What it does

### Replay

The default page. A session plays back in the browser: timing tower, track map with every car, the track state (green, yellow, SC, VSC, red), race-control messages and a timeline with jump points for safety cars, pit stops, retirements and fastest laps. Click a driver for their card (last laps, interval trend) and "Analyse this lap". Keyboard: space plays and pauses, the arrow keys step, `F` follows the focused car, `L` toggles labels.

A snapshot at time *t* never uses anything that happened after *t*: a lap deleted later still shows as the best lap until the stewards delete it, and the final classification belongs to the Results page.

### Results

The classification as F1 publishes it, sector bests and mini-sector dominance, the tyre strategy chart (FastF1's official per-season compound colours) and, after races and sprints, the drivers' and constructors' championships after that round (from Jolpica).

### Analysis

One panel at a time, following the replay cursor:

- **Telemetry**: speed, throttle, brake, RPM, gear and (until 2025) DRS against track distance, on a shared 5 m grid. The *Fastest lap* scope (default) lines every driver up at the same track position; *Full session* plots every lap.
- **Head-to-head**: two drivers' speed traces and the cumulative time delta, integrated from the speed traces (`ds / v`). It lands within about 0.1 to 0.3 s of the true lap-time gap; read exact gaps from the lap times.
- **Lap times** and **Positions**, with safety-car, VSC and red-flag laps shaded.
- **Race trace**: the gap to the leader, or to any driver, lap by lap.
- **Tyre pace**: fuel-corrected lap time against tyre age per compound, and the degradation per stint in seconds per lap. In- and out-laps, neutralised laps, deleted laps and lap 1 are left out; the fuel correction is an estimate.
- **Speed traps**: each driver's best I1, I2, finish-line and speed-trap reading.
- **Deleted laps**: lap times the stewards deleted, with their reason.
- **Weather** and **Race control**, the latter searchable and filterable.

A driver filter (top five by default) applies to the per-driver panels and is kept in the URL, so a shared link shows the same cars.

### Records

Fastest lap, fastest sectors and top speed for the session, and the best at that circuit across every session you have opened. They are kept in a small SQLite database, recomputed from valid laps each time a session is opened; deleted laps never set a record.

### Live

During a session the Live page shows the timing screen, race control, weather and, with a token, telemetry and the track map, refreshed every 3 seconds. In qualifying it shows the running segment, its remaining time and the cars eliminated in Q1 and Q2. An optional broadcast delay (0 to 300 s) holds the screen back to match a TV picture.

The connection status reads `CONNECTING`, `WAITING` (connected, no session on air), `LIVE`, `STALE`, `RECONNECTING`, `TOKEN NEEDED`, `REFUSED`, `STOPPED` or `OFFLINE`. The client reconnects on its own; F1 drops long connections after about two hours, so a reconnect is normal. Each live session is also recorded to `replay_sessions/raw_<gp>_<session>_<utc>` (`F1_LIVE_AUTORECORD=0` turns that off).

Stopping, clearing or recording the feed affects every viewer of the app, so those controls appear only to a browser on the same machine or when `F1_LIVE_CONTROLS=1` is set.

### Units

The Settings page switches between metric (km/h, °C) and imperial (mph, °F). The choice is per viewer and kept in the URL (`?units=imperial`).

---

## Data sources and terms

- **FastF1** (historical sessions) reads F1's public live-timing archive and caches it locally. Sessions appear in the archive about one to two hours after they end; the app says so instead of loading a partial session, and does not keep a session that ended in the last few hours in its memory cache.
- **F1 live timing** (`wss://livetiming.formula1.com/signalrcore`) is the same unofficial, undocumented feed F1's own timing screen uses. The app opens one connection per process and backs off after a refusal. F1 has IP-blocked hosted projects that consumed it heavily, so run this app locally, for yourself, with a single connection. Do not host it publicly.
- **Subscription data** (car telemetry and positions) requires your own F1TV account. Use your own token, and do not redistribute what it unlocks.
- **Jolpica** (the Ergast-compatible API, used for standings and the calendar) is free under a fair-use limit of 4 requests per second and 500 per hour; the app throttles itself and gives up rather than wait out a long block.
- **OpenF1** offers real-time data only on a paid tier; this project does not use it.

The project's code is MIT-licensed. The bundled font and the recorded live-timing test fixtures are under their own terms, listed in [NOTICE](NOTICE).

---

## Development

Python 3.11+ (CI tests 3.11 to 3.14).

```bash
git clone https://github.com/mricero/F1-Telemetry-Dashboard.git
cd F1-Telemetry-Dashboard
uv venv --python 3.12
uv pip install -r requirements-dev.lock   # the pinned set CI tests; runtime-only: requirements.lock
```

Then run the app from the checkout:

```bash
streamlit run app.py
# or: python app.py   (re-enters through Streamlit's CLI; see CLAUDE.md, "bare mode")
```

Gates, the same ones CI runs (Python 3.11 to 3.14, Ubuntu and Windows):

```bash
python -m pytest                     # offline and deterministic; deprecation warnings are errors
python -m ruff check .
python -m black --check .
python -m mypy --ignore-missing-imports app.py data processing ui
F1_NETWORK_TESTS=1 python -m pytest -m network   # real FastF1/Jolpica endpoints, opt-in
python -m pytest -m perf                         # wall-clock budgets, run in their own CI job
```

`pre-commit install` runs ruff, black, mypy and a large-file guard on each commit. After editing `requirements.txt` or `requirements-dev.txt`, re-lock with `python scripts/lock_requirements.py`; CI fails when the lock does not match.

During a race weekend, `python scripts/live_smoke.py 60` checks the live connection end to end (`--record <dir>` keeps the raw stream).

### Layout

```text
app.py              orchestration: select -> load -> process -> record; no chart code
config.py           paths, version, .env loading
f1dash_cli.py       the f1dash command
data/               one adapter per upstream, each producing the unified session dict
  fastf1_adapter.py   historical sessions (FastF1)
  jolpica_adapter.py  standings and calendar (Jolpica)
  signalr_core.py     the live SignalR Core client
  live_adapter.py     live state, parsers and the live snapshot
  live_state.py       deep-merged live state topics
  live_recorder.py    raw live-stream recording and replay
  live_service.py     the one live adapter per process
  source_manager.py   the unified interface; replay save/load
  runtime_cache.py    process-wide memory cache of loaded sessions
  token_store.py      writes the F1TV token to .env
  update_check.py     the daily newer-release check
processing/         pure transforms: no Streamlit, no network
  replay.py, replay_model.py, replay_payload.py   the replay clock, snapshots and player payload
  timing.py           the timing tower rows and formats
  analysis.py         race trace, tyre pace, speed traps, deleted laps
  track_periods.py    SC/VSC/red periods per lap
  track_geometry.py   circuit outline and corner labels for the map
  telemetry_processor.py, time_utils.py, metrics_store.py
ui/                 all rendering
  layout.py, pages.py, dashboard.py, replay_view.py, track_map.py, theme.py, units.py, ...
  components/replay_player/   the browser-side replay player (JavaScript)
tests/              pytest suite; tests/js holds the player's Node tests
scripts/            live_smoke.py, capture_fixture.py, convert_legacy_replay.py, ...
```

Every source produces the same session dict, so one set of renderers serves historical, live and replay sessions; `CLAUDE.md` documents it and the domain rules that keep it correct. `ARCHITECTURE.md` has the details, `IMPROVEMENTS.md` the open work and `tasks.md` the record of what was fixed.

### Saved replays

"Save session for replay" writes a directory under the replay folder: `meta.json` (schema version, app version, session info) and one Parquet file per table. Loading one runs no code, and a `meta.json` naming anything a replay never holds is refused. Pickle replays from early versions are no longer offered; convert a trusted one with `python scripts/convert_legacy_replay.py <file> --trust`.

---

## Configuration

Set these in the environment or in `.env` (`.env.example` lists them all):

| Variable | Default | Meaning |
|---|---|---|
| `F1TV_SUBSCRIPTION_TOKEN` | unset | Your F1TV token, for live car telemetry and positions |
| `FASTF1_CACHE_DIR` | see "Where your data lives" | FastF1's download cache |
| `REPLAY_DIR` | see above | Saved replays and live recordings |
| `F1_METRICS_STORE` | see above | The records database (`:memory:` keeps nothing) |
| `LOG_LEVEL` | `WARNING` | Python logging level |
| `F1_LIVE_CONTROLS` | `0` | `1` shows Stop/Clear/Record to every viewer, not only localhost |
| `F1_LIVE_AUTORECORD` | `1` | Record each live session's raw stream |
| `F1_UPDATE_CHECK` | `1` | `0` disables the daily release check |
| `F1_REPLAY_PLAYER` | `browser` | The replay player implementation |
| `F1_CACHE_MAX_ENTRIES` / `F1_CACHE_MAX_BYTES` | `8` / 1 GiB | Bounds of the in-memory session cache |
| `DEFAULT_YEAR`, `DEFAULT_GP`, `DEFAULT_SESSION` | `2024`, `Abu Dhabi`, `R` | Fallback selection |

---

## Troubleshooting

- **"not in F1's archive yet"**: the session ended recently. FastF1's source usually has it one to two hours after the flag.
- **Live status `TOKEN NEEDED` or "Token rejected"**: the token has expired; paste a new one. The free topics keep flowing meanwhile.
- **Live status `REFUSED`**: F1 refused this client or IP (403/429). Wait; do not restart repeatedly, and check any VPN or proxy.
- **A stale schedule on a race weekend**: Settings, then "Clear cached schedules".
- **Slow first load**: FastF1 downloads the session once; later loads come from its cache.

## License

MIT for the project's own code; see [LICENSE](LICENSE). Third-party content is listed in [NOTICE](NOTICE).

*This project is unofficial and is not associated in any way with the Formula 1 companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks are trade marks of Formula One Licensing B.V.*
