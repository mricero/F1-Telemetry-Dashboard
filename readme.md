# F1 Telemetry Dashboard

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.55+-FF4B4B.svg)](https://streamlit.io/)
[![FastF1](https://img.shields.io/badge/FastF1-3.8+-black.svg)](https://docs.fastf1.dev/)
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)
[![Website](https://img.shields.io/badge/website-live-e10600.svg)](https://mricero.github.io/F1-Telemetry-Dashboard/)

**Formula 1 timing, on your own machine.** Follow a session live from F1's own
timing feed, replay any race with the timing tower and track map, then take every
lap apart in twelve analysis views. Results, tyre strategy and records that
persist between runs are included too. Historical sessions come from FastF1; live
timing comes from F1's SignalR Core feed while a session is on air.

[![The Live page during a race: timing tower, gaps, sectors and tyre history](https://mricero.github.io/F1-Telemetry-Dashboard/assets/img/live-timing.jpg)](https://mricero.github.io/F1-Telemetry-Dashboard/)

[Website](https://mricero.github.io/F1-Telemetry-Dashboard/) ·
[Watch the 21-second film](https://mricero.github.io/F1-Telemetry-Dashboard/assets/video/live-film.mp4) ·
[Install](#install) · [Features](#features) · [Data sources & terms](#data-sources--terms)

This is an **unofficial** project. It reads undocumented F1 endpoints and is
not associated with the Formula 1 companies. Read
[Data sources & terms](#data-sources--terms) before running it.

**At a glance**

| | What you get |
|---|---|
| **Live** | When a session is on air: the timing tower (last and best lap, interval, gap, sectors, tyre history), sector leaders, race control, track status, weather, tyre stints and the championship as it would stand if the race finished now. No account needed. |
| **Replay** | Any session from FastF1: tower, track map, track-position strip, flags, race control and weather as they stood at the cursor, with playback, lap steps and an event timeline. |
| **Analysis** | Twelve views per session: telemetry, head-to-head, lap times, race trace, tyre pace, pit rejoin, rankings, deleted laps, positions, weather, race control and team radio. |
| **Results and records** | Final classification, sector bests, track dominance, tyre strategy and the championship, plus fastest-lap, sector and top-speed records kept across every session you load. |
| **Yours** | Runs locally. Free and MIT licensed. km/h or mph, °C or °F, track or local time, favourite drivers, and shareable links. |

## Install

The installers set up [uv](https://docs.astral.sh/uv/) if it is missing, then
install the app as a uv tool. uv downloads a matching Python itself, so you do
not need Python installed, and your own Python is not touched. No admin
rights are needed.

| System | Command |
|---|---|
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.ps1 \| iex` |
| macOS and Linux | `curl -LsSf https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.sh \| sh` |
| Any OS, with uv | `uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard` |

Windows (PowerShell; also adds a Start-menu shortcut "F1 Replay"):

```powershell
irm https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.ps1 | iex
```

macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.sh | sh
```

Both install the latest GitHub release (or `main` when there is no release
yet). Running one again updates the app.

By hand, on any OS, after [installing uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard
```

If the shell then cannot find `f1dash`, run `uv tool update-shell` and open a
new terminal.

## Run

```sh
f1dash                  # opens the dashboard in the browser
f1dash --port 8600      # a specific port (default 8501, or the next free one)
f1dash --no-browser     # serve without opening a browser window
f1dash --version
```

To try it without installing:

```sh
uvx --from git+https://github.com/mricero/F1-Telemetry-Dashboard f1dash
```

Pick a season, Grand Prix and session in the sidebar and select
**Load session**. Nothing is fetched until you do; the first load of a
session downloads it through FastF1 and takes a while, later loads read the
cache. A loaded session has these pages:

| Page | What it shows |
|---|---|
| **Replay** | The session as it stood at the cursor: timing tower, track map, track-position strip, flags, race control and weather, with playback, lap steps and a timeline of events. |
| **Results** | The final classification, sector bests, track dominance, tyre strategy and the championship after the round. |
| **Analysis** | Twelve views; see [Analysis](#analysis) below. |
| **Records** | Fastest lap, sector bests and top speed, for this session and across every session you have loaded. |
| **Settings** | Units and time zone, favourite drivers, which tower columns and panels show, cached schedules and loaded sessions, data locations and the version. |

While a session is on air the sidebar offers **Go live**; the Live page then
replaces Replay, Results and Analysis.

## Update

```sh
f1dash update
```

This installs the newest GitHub release with
`uv tool install --reinstall git+https://github.com/mricero/F1-Telemetry-Dashboard@<tag>`.
The sidebar footer shows `Update available` when a newer release exists; the
app checks GitHub at most once a day, and `F1_UPDATE_CHECK=0` turns the check
off.

## Uninstall

```sh
uv tool uninstall f1dash
```

This removes the app. Your cache, replays, records and `.env` stay where
`f1dash paths` says they are; delete those folders to remove them as well.
On Windows, delete the "F1 Replay" Start-menu shortcut too.

## Where your data lives

```sh
f1dash paths
```

prints four locations:

| What | Installed copy | Git checkout |
|---|---|---|
| FastF1 cache | user cache directory, `fastf1/` | `ff1_cache/` |
| Replays and live recordings | user data directory, `replays/` | `replay_sessions/` |
| Records | user data directory, `metrics_store.sqlite` | `metrics_store.sqlite` |
| `.env` | user config directory, `.env` | `.env` in the project folder |

The user directories come from `platformdirs` (on Windows under
`%LOCALAPPDATA%\f1dash`, on Linux under `~/.cache/f1dash`,
`~/.local/share/f1dash` and `~/.config/f1dash`). `FASTF1_CACHE_DIR`,
`REPLAY_DIR` and `F1_METRICS_STORE` override them.

A replay is a folder holding `meta.json` and Parquet tables. Replays saved by
older versions as legacy `.pkl` pickles are not loaded, because unpickling a
file runs code from it; convert ones you created yourself with
`python scripts/convert_legacy_replay.py <file> --trust` from a checkout.

## Live timing token

Live timing, tyres, race control, weather and track status work without an
account. Car telemetry and positions (`CarData.z`, `Position.z`) have needed an
F1TV subscription since the 2025 Dutch Grand Prix. To get them, put your own
token in the `.env` file that `f1dash paths` shows:

```env
F1TV_SUBSCRIPTION_TOKEN=<the JWT, or the value of the formula1.com login-session cookie>
```

or paste it into **Subscription token** on the Live page and select
**Save token**. The token is read from your environment or `.env`, sent only
to `livetiming.formula1.com` as `Authorization: Bearer`, and never logged. It
expires after a few days; the Live page shows when.

Live mode connects to `wss://livetiming.formula1.com/signalrcore` with the
app's own SignalR Core client (`src/f1dash/data/signalr_core.py`). F1 closes
long connections, so the client reconnects by itself and shows its state:

| State | Meaning |
|---|---|
| Connecting | Negotiating and opening the socket. |
| Waiting | Connected; no session is on air yet. |
| Live | Feed data is arriving. |
| Reconnecting | The connection dropped; retrying with backoff (normal, F1 drops long connections). |
| Token needed | HTTP 401: the token is missing or expired. Timing still works without it. |
| Refused | HTTP 403: F1 refused this client or address. The app waits two minutes before trying again. |

To check the connection during a race weekend, run
`python scripts/live_smoke.py 30` from a checkout.

## Features

### Live

[![Tyre stints for every driver](https://mricero.github.io/F1-Telemetry-Dashboard/assets/img/live-tyres.jpg)](https://mricero.github.io/F1-Telemetry-Dashboard/#live)

- **Timing tower**: position, last and best lap, interval, gap, sector times and
  tyre history, with the session clock, track status and conditions above it.
- **Sector leaders**, **race control** and **weather** next to the tower.
- **Tyres**: every stint for every driver, by compound and lap.
- **Championship, live**: the drivers' and constructors' standings if the race
  finished in the current order (now, this race, projected, change).
- **Broadcast delay**: hold the live view back to line up with your TV stream.
- **Auto-record**: the raw feed is saved into the replay folder when a session
  starts (`F1_LIVE_AUTORECORD`), so it can be replayed afterwards.

### Replay

[![Replay of the 2020 Abu Dhabi Grand Prix with the track strip, race control and timeline](https://mricero.github.io/F1-Telemetry-Dashboard/assets/img/replay-controls.jpg)](https://mricero.github.io/F1-Telemetry-Dashboard/#replay)

- The timing tower and track map as they stood at the cursor; a snapshot never
  uses information from later in the session.
- A **track-position strip** with every car on one line, for trains and gaps.
- A **timeline** with safety cars, flags, pit stops and fastest laps marked, and
  a jump list to go straight to them.
- **Qualifying aware**: Q1, Q2 and Q3 (and SQ1 to SQ3) segments, knock-outs and
  the segment clock.
- **Save session for replay** writes a session to disk to open offline later.

### Analysis

[![Race trace: gap to the leader per lap with safety car laps shaded](https://mricero.github.io/F1-Telemetry-Dashboard/assets/img/analysis-race-trace.jpg)](https://mricero.github.io/F1-Telemetry-Dashboard/#analysis)

Pick the drivers once and the charts follow them. The drivers, the view and the
replay position are in the URL, so a link opens exactly what you were looking at.

| View | What it shows |
|---|---|
| Telemetry | Speed, throttle, brake, RPM, gear and DRS over each driver's fastest lap, lined up by distance (DRS is hidden for 2026, which replaced it with active aero). |
| Head-to-head | Two drivers stacked: speed, throttle, brake, gear and the time delta, with corner numbers and a lap picker per driver. The delta is integrated from the speed traces and lands within roughly 0.1-0.3 s of the true gap. |
| Lap times | Every lap for the chosen drivers, with pit-out laps marked and neutralised laps shaded. |
| Race trace | The gap to the leader, or to a chosen driver, lap by lap. |
| Tyre pace | Fuel-corrected lap time against tyre age per compound, and the loss per lap. |
| Pit rejoin | Where a car would rejoin if it pitted at the end of a given lap. |
| Rankings | Best sector times and speed-trap readings (I1, I2, finish line, speed trap), ranked. |
| Deleted laps | Laps deleted for track limits, the reason and when it was announced, plus warnings per driver. |
| Positions | The running order lap by lap. |
| Weather | Air and track temperature, humidity, wind and rain over the session. |
| Race control | Every message, filterable and searchable. |
| Team radio | The session's team radio recordings, 2023 onwards, listed from OpenF1. |

### Results, records and settings

| Page | Highlights |
|---|---|
| Results | Final classification, sector bests, track dominance, tyre strategy and the championship after the round. |
| Records | Fastest lap, sector bests and top speed, kept in a local database between runs. |
| Settings | km/h or mph, °C or °F, track or local time, favourite drivers (underlined in the tower), the tower columns and panels shown, cache controls, data locations and the version. |

## Configuration

Every variable is optional. Set it in the environment or in `.env`;
[`.env.example`](.env.example) lists them all with comments.

| Variable | Default | What it does |
|---|---|---|
| `F1TV_SUBSCRIPTION_TOKEN` | unset | Your F1TV token, for car telemetry and positions. |
| `F1_LIVE_CONTROLS` | `0` | `1` shows the Connect/Stop/Clear live controls to every viewer, not only on localhost. |
| `F1_LIVE_AUTORECORD` | `1` | Record the raw live feed into the replay folder when a session starts; `0` turns it off. |
| `FASTF1_CACHE_DIR` | see above | FastF1's HTTP and session cache. |
| `REPLAY_DIR` | see above | Saved and recorded replays. |
| `F1_METRICS_STORE` | see above | The records database. |
| `DEFAULT_YEAR` | `2024` | Fallback season when the schedule cannot be read. |
| `DEFAULT_GP` | `Abu Dhabi` | Fallback Grand Prix. |
| `DEFAULT_SESSION` | `R` | Fallback session (`R` = race). |
| `LOG_LEVEL` | `WARNING` | Logging for the adapters: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `F1_REPLAY_PLAYER` | `browser` | `server` uses the Python-rendered replay instead of the browser player. |
| `F1_CACHE_MAX_ENTRIES` | `8` | Loaded sessions kept in memory. |
| `F1_CACHE_MAX_BYTES` | `1073741824` | Memory budget for loaded sessions, in bytes. |
| `F1_UPDATE_CHECK` | `1` | `0` stops the daily check for a newer release. |
| `F1_NETWORK_TESTS` | unset | `1` enables the tests that reach FastF1, Jolpica and F1 (development). |

## Data sources & terms

| Source | Used for | Notes |
|---|---|---|
| F1 live timing (`livetiming.formula1.com`) | The Live page | Undocumented; car telemetry and positions need your own F1TV token. |
| FastF1 | Schedules, sessions, telemetry and results | Reads F1's timing archive and caches it locally. |
| [Jolpica](https://github.com/jolpica/jolpica-f1) | Results and championship standings | Free, volunteer-run, Ergast-compatible; 4 requests per second and 500 per hour unauthenticated. |
| [OpenF1](https://openf1.org) | The Team radio list | The free historical `team_radio` endpoint only; your F1TV token is never sent to it. |

- **Unofficial endpoints.** Live timing comes from F1's undocumented
  `livetiming.formula1.com` feed, and FastF1 reads F1's timing archive. F1
  does not support this use and can change or close the endpoints at any time.
- **Subscription data is yours alone.** Car telemetry and positions need your
  own F1TV account and token. Use them for your own viewing; do not
  redistribute that data, recordings of it or replays built from it.
- **Hosting it publicly risks IP blocks.** F1 has blocked heavy and hosted
  consumers of the feed (f1-dash closed citing "increasing IP restrictions";
  a hosted f1-telemetry instance went down "due to IP blocking by Formula 1").
  Run the app locally, with one connection, on your own token. The app keeps a
  single upstream connection per process and backs off after a refusal.
- **Jolpica fair use.** FastF1's cache keeps repeat loads off the API.
- **OpenF1** offers historical data for free and real-time data as a paid
  tier. This app reads only its free historical team radio list.
- **Bundled third-party content.** The Titillium Web font (SIL Open Font
  License 1.1) and the recorded F1 timing excerpt in `tests/fixtures/live/`
  are not covered by the MIT License; see [NOTICE](NOTICE).

F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX
and related marks are trade marks of Formula One Licensing B.V.

## Development

Requires Python 3.11+ (CI tests 3.11 to 3.14) and uv.

```sh
git clone https://github.com/mricero/F1-Telemetry-Dashboard.git
cd F1-Telemetry-Dashboard
uv venv
uv pip install -r requirements-dev.lock    # or requirements.lock for the app only
uv pip install --no-deps -e .              # optional: f1dash, python -m f1dash, scripts/
streamlit run app.py
```

The code is the `f1dash` package in `src/f1dash/` (`app.py` the Streamlit
script, `cli.py` the `f1dash` command, then `data/`, `processing/`, `ui/`).
The root `app.py` is a small shim that runs `src/f1dash/app.py`, so
`streamlit run app.py` needs no install; `pytest.ini` puts `src` on the path
for the tests. The editable install is what the `f1dash` command and the
manual scripts in `scripts/` import from (or set `PYTHONPATH=src`).

| Folder | What lives there |
|---|---|
| `src/f1dash/data/` | One adapter per upstream: FastF1, Jolpica, OpenF1 and the live SignalR Core client. |
| `src/f1dash/processing/` | Pure transforms: the timing-tower model, replay snapshots, analysis maths. |
| `src/f1dash/ui/` | All rendering: pages, the timing tower, the track map and the browser replay player. |
| `tests/` | The offline test suite, with recorded live fixtures. |
| `scripts/` | Manual tools: the live smoke test, replay preview, fixture capture. |

Activate the virtualenv first (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere), or call its interpreter directly
(`.venv/Scripts/python` or `.venv/bin/python`). `python app.py` works too: it
re-enters through `streamlit run`, because a plain interpreter would start
Streamlit in bare mode, where widgets return defaults and `st.stop()` does
nothing.

A checkout keeps its data in the project folder (`ff1_cache/`,
`replay_sessions/`, `metrics_store.sqlite`, `.env`), all git-ignored.

Tests and checks, the same ones CI runs:

```sh
python -m pytest                       # offline and deterministic
python -m ruff check .
python -m black --check .
python -m mypy --ignore-missing-imports src
F1_NETWORK_TESTS=1 python -m pytest -m network    # optional, reaches the real APIs
pre-commit install                     # runs ruff, black, mypy and file checks on commit
```

`pytest.ini` collects from `tests/` only, so the manual scripts in `scripts/`
never run as tests. After editing `requirements.txt` or
`requirements-dev.txt`, re-lock with `python scripts/lock_requirements.py`.

How the code fits together is in [ARCHITECTURE.md](ARCHITECTURE.md); the
rules for changing it are in [CLAUDE.md](CLAUDE.md); open work is in
[IMPROVEMENTS.md](IMPROVEMENTS.md); releases are in
[CHANGELOG.md](CHANGELOG.md).

### Troubleshooting

| Problem | What to do |
|---|---|
| A session will not load | FastF1 or its upstream timed out, or the session is too recent to be complete in F1's archive. Try again later, or clear the FastF1 cache folder (`f1dash paths`) and load it again. |
| "F1 asked for a subscription token (HTTP 401)" | The token is missing or has expired; paste a new one. Timing, tyres, race control and weather keep working without it. |
| "F1 refused the connection (HTTP 403)" | F1 refused this client or address. Wait; do not restart repeatedly. |
| High memory use | Keep the telemetry scope on *Fastest lap* (Advanced in the sidebar); *Full session* carries every lap. `F1_CACHE_MAX_ENTRIES` and `F1_CACHE_MAX_BYTES` bound how many loaded sessions stay in memory. |

## Website

The project site at https://mricero.github.io/F1-Telemetry-Dashboard/ is a
single static page with screenshots and a short film of the app. It is served
by GitHub Pages from the `gh-pages` branch, which holds only the site, so none
of it is part of `main`. The screenshots in this readme load from that site.

## License

MIT for the project's own code; see [LICENSE](LICENSE). Third-party content is
listed in [NOTICE](NOTICE).
