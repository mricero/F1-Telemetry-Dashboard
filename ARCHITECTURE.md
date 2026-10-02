# F1 Telemetry Dashboard - Architecture

This file describes the code as it is. `CLAUDE.md` holds the rules that keep it
that way (layering, caching, domain gotchas); `IMPROVEMENTS.md` holds the open
work. When this file and the code disagree, the code is right and this file is
the bug (`tests/test_docs_live_claims.py` checks the module list below).

## Overview

A Streamlit app that loads one Formula 1 session at a time and shows it as a
replay, a results page, analysis charts and persistent records:

- **Historical sessions** from FastF1, with its on-disk cache. FastF1's event
  schedule also drives the season and Grand Prix lists and the check for a
  session on air. FastF1 itself reads results from Jolpica, the
  Ergast-compatible REST API; the app's own `data/jolpica_adapter.py` client
  is held by `DataSourceManager` but is not on the load path today.
- **Live timing** over F1's SignalR Core hub,
  `wss://livetiming.formula1.com/signalrcore`, with the app's own client
  (`data/signalr_core.py`). This is an **unofficial** use of undocumented
  endpoints. Car telemetry and positions (`CarData.z`, `Position.z`) need the
  user's own F1TV subscription token in `F1TV_SUBSCRIPTION_TOKEN`; without it
  those topics are not subscribed and everything else still works. The
  `livef1` dependency was removed (REPO-18).
  LiveF1's `RealF1Client` is no longer used: its legacy `/signalr/` hub answers 401.
- **Saved replays**: a loaded session written to disk as Parquet tables plus
  `meta.json`, and raw recordings of the live feed.

Nothing switches source on its own. When a session is on air the sidebar
offers "Go live"; otherwise the user picks a season, Grand Prix and session.

## Layers

```
app.py                     orchestration: selection -> load -> process -> record
  |
  +-- ui/                  all rendering (Streamlit, Plotly, SVG, the replay player)
  |
  +-- processing/          pure transforms over DataFrames: no Streamlit, no network
  |
  +-- data/                adapters, one per upstream API, normalising to the session dict
        |
        +-- FastF1 (historical, disk cache, event schedule)
        +-- Jolpica REST (Ergast-compatible; client kept, unused on the load path)
        +-- livetiming.formula1.com/signalrcore (live)
        +-- replay directory (saved sessions, raw live recordings)
```

Every source produces the same **unified session dict** (`CLAUDE.md` lists
its keys), so one set of renderers draws all of them. Adding a source means
producing that dict, not touching `ui/`.

## Modules

### Entry points

| File | Role |
|---|---|
| `app.py` | Streamlit script. Re-enters through `streamlit run` when started as `python app.py` (bare mode). Loads the selection through the runtime cache, builds the processed views, folds records into the metrics store and hands off to `ui.pages`. |
| `f1dash_cli.py` | The installed `f1dash` command: finds the bundled `app.py`, passes `.streamlit/config.toml` as `--section.key=value` flags, picks a free port, and implements `f1dash paths`, `f1dash update` and `--version`. |
| `config.py` | Loads `.env`, resolves the data locations (repo-local in a checkout, `platformdirs` per-user directories in an install) and reads `__version__` from `pyproject.toml` or the installed metadata. |

### `data/` - adapters and live ingest

| Module | Role |
|---|---|
| `data/fastf1_adapter.py` | FastF1 sessions: laps (with `IsPitOutLap` derived from `PitOutTime`), stints, telemetry and location from the merged `get_telemetry()` under the `fastest`/`session` scope, whole-session positions on a 0.5 s grid, timing stream, track status, weather, race control, compound colours. |
| `data/openf1_adapter.py` | OpenF1 client for the team radio list (2023 onwards, no key, never the F1TV token): finds the session, returns recording links with session-clock times. Shown on the Analysis page; recordings are links, never fetched by the app. |
| `data/jolpica_adapter.py` | Jolpica (Ergast-compatible) REST client with pagination, a request throttle and 429 `Retry-After` retries: schedules, results, standings, lap times, pit stops. `DataSourceManager` holds one; the load path does not call it today. |
| `data/source_manager.py` | `DataSourceManager`: `get_session_data()` for `fastf1`, `live` and `replay`; `live_session()` (the session on air, from the FastF1 schedule); `poll_live_data()` for the live view; `save_replay()` / `_load_replay()` for the Parquet replay format. |
| `data/signalr_core.py` | SignalR Core client: negotiate (`AWSALBCORS` cookie, `negotiateVersion=1`), socket, `Subscribe`, 10 s pings, reconnect with backoff (longer after 401/403/429), optional `Authorization: Bearer` token. |
| `data/live_adapter.py` | `SignalRLiveAdapter`: the two entry points `seed_state()` (subscription snapshot) and `handle_message()` (feed messages); decodes `CarData.z`/`Position.z` (base64 + raw DEFLATE) into bounded buffers. `LiveDataProcessor` turns records into DataFrames and the timing-screen standings. |
| `data/live_state.py` | `LiveState`: deep merge of keyframe + delta topics (`STATE_TOPICS`), `_deleted` markers honoured, re-seeded on reconnect. |
| `data/live_service.py` | `get_live_adapter()`: one live adapter per process; browser tabs only read from it. |
| `data/live_recorder.py` | Writes the raw feed (`subscribe.json` + `live.jsonl`) and replays it through the same two adapter entry points. |
| `data/runtime_cache.py` | Process-lifetime cache of whole session dicts, bounded by count and bytes (`F1_CACHE_MAX_ENTRIES`, `F1_CACHE_MAX_BYTES`), least-recently-used eviction. |
| `data/token_store.py` | Reads the F1TV token's expiry and writes a pasted token to the user's own `.env` on an explicit Save. Never logged. |
| `data/update_check.py` | At most one GitHub releases API call a day (3 s timeout, cached in the user cache directory, `F1_UPDATE_CHECK=0` turns it off) and the `uv` command line `f1dash update` runs. |

### `processing/` - pure transforms

| Module | Role |
|---|---|
| `processing/telemetry_processor.py` | `TelemetryProcessor`: resampling to a 5 m distance grid (`DISTANCE_STEP`), interpolating only continuous channels and taking the nearest sample for gear/DRS/brake; driver colours; lap and stint preparation. |
| `processing/time_utils.py` | `to_seconds()` and `parse_gap()`: the only way live-feed time strings become numbers (never `pd.to_timedelta`). |
| `processing/timing.py` | The timing-tower model: race order by classification with gap/interval, practice and qualifying by best lap, knock-out cut-offs, sector bests, mini-sector dominance. |
| `processing/track_geometry.py` | The one circuit transform (FastF1 rotation, fit into a viewBox) shared by the SVG map and the browser player. |
| `processing/track_periods.py` | Safety car, VSC and red-flag periods on the session clock and per lap, for chart shading and the degradation fit. |
| `processing/pace.py` | Tyre degradation and stint pace: clean laps (no in/out, SC/VSC/red, inaccurate or first lap) with tyre age and fuel-corrected time, per-stint slopes and the per-compound median. |
| `processing/pit_loss.py` | Pit rejoin predictor: green-flag pit lane times from `PitInTime -> PitOutTime`, a per-circuit seed table, and where a car would rejoin given the gaps to the leader. |
| `processing/lap_compare.py` | One lap of one driver for the head-to-head: lap list and time windows, slice with Distance from 0, shared 5 m grid, corner markers. |
| `processing/replay.py` | `ReplayClock` (start, lights out, end) and the `PositionCube`: every car's position on one regular time grid. |
| `processing/replay_model.py` | `tower_series()` change-point series and `snapshot_at(t)`: the session as it stood at `t`, never reading rows stamped after it; `events()` for jump targets. |
| `processing/replay_payload.py` | `build_replay_payload()`: the JSON-safe dict the browser player animates (clock, track, packed positions, tower series with display strings, flags, race control, weather, lap marks). |
| `processing/driver_selection.py` | Which drivers the Analysis charts plot: classification order, the top-five default and the `drivers=` URL form (UX-03). |
| `processing/metrics_store.py` | `MetricsStore`: persistent fastest lap, sector bests and top speed per session and all-time, in SQLite (WAL mode). |

### `ui/` - rendering

| Module | Role |
|---|---|
| `ui/layout.py` | The canonical rendering module: the sidebar session picker, charts (telemetry, head-to-head with the integrated time delta, lap times, positions, weather, race control), the live view and its controls, Settings. Network lookups on a rerun are wrapped in `@st.cache_data` with a TTL. |
| `ui/pages.py` | The `st.navigation` pages: Replay, Results, Analysis, Records and Settings for a loaded session; Live, Records and Settings while live. |
| `ui/preferences.py` | Per-viewer preferences: the Analysis driver selection (`drivers=`) and favourite drivers (`fav=`), validated and mirrored in the URL (UX-03). |
| `ui/replay_view.py` | The Replay page: the dashboard drawn from `snapshot_at()` at the cursor, with the browser player or the server fallback (`F1_REPLAY_PLAYER`). |
| `ui/dashboard.py` | The timing dashboard assembly (`layout.md` sections 2-5): header bar, tower, sector top-3 widgets, map panel. |
| `ui/track_map.py` | The SVG track map: outline, corner numbers, dominance layer, car markers. |
| `ui/theme.py` | Colour tokens (guideline 5.4 of `IMPROVEMENTS.md`) emitted once as CSS variables, flag states, shared CSS. |
| `ui/fonts.py` | Titillium Web embedded as base64 `@font-face`, so the app requests no fonts at runtime. |
| `ui/status.py` | `DataStatus`: why a panel is empty, carried to the panel that shows it. |

`ui/components/replay_player/` holds the browser replay player
(`player.js`, `player.css`, `player.html`), a Streamlit v2 component that
only looks values up in the payload and draws; `layout.md` section 9 is its
contract.

## Data flow

```
1. Select a session (sidebar)     -> selection dict in st.session_state
2. Load                           -> runtime_cache.get(key)
                                     miss: DataSourceManager.get_session_data()
                                     FastF1 / Parquet replay / live snapshot
3. Keep                           -> runtime_cache.set(key, data)  (memory only)
4. Process                        -> TelemetryProcessor (distance grid, colours),
                                     processing.timing, processing.replay_model
5. Record                         -> MetricsStore.update_laps() / update_telemetry()
                                     (persisted in SQLite, once per session)
6. Render                         -> ui.pages -> ui.layout / ui.replay_view / ui.dashboard
7. Save (optional)                -> DataSourceManager.save_replay()
Live: a fragment re-runs every 3 s -> poll_live_data() from the process-wide adapter
```

## Replay format

`DataSourceManager.save_replay()` writes a **directory** in `REPLAY_DIR`:

- `meta.json` - `schema` (currently 7, `REPLAY_SCHEMA_VERSION`), the app
  name, `saved_at`, plain values (`session_info`, `compound_colors`, ...) and
  the lists of tables written;
- one Parquet file per table (`laps`, `stints`, `results`, `weather`,
  `race_control`, `drivers`, `positions`, `timing_stream`, `track_status`);
- one subdirectory of per-driver Parquet files for `telemetry`, `location`
  and, when they differ, `dashboard_telemetry` / `dashboard_location`.

Parquet keeps the `Timedelta`, nullable `Int64` and categorical dtypes, and
loading it cannot run code. An older schema loads with defaults for the keys
it lacks; a newer one is refused with a message. Legacy pickle replays from
before HIST-02 are refused by the app; convert ones you created yourself with
`scripts/convert_legacy_replay.py --trust`.

Raw live recordings (`data/live_recorder.py`) are a separate, deliberately
raw format: `subscribe.json` plus `live.jsonl`, one `[topic, data, timestamp]`
line per message.

## Live feed

- One `SignalRLiveAdapter` per process (`data/live_service.py`); the
  Connect/Stop controls are shown to localhost only unless
  `F1_LIVE_CONTROLS=1`.
- The `Subscribe` completion goes to `seed_state()`, each `feed` message to
  `handle_message()`. State topics are deep-merged; only `CarData.z`,
  `Position.z` and `WeatherData` go to the bounded buffers (20 000 records per
  topic by default).
- The tower follows the timing screen: `LiveDataProcessor.standings_from_state`
  builds the same columns a replay snapshot has.
- The feed is recorded to the replay folder as soon as a session starts
  (`F1_LIVE_AUTORECORD`, on by default).
- A dropped connection is normal (F1 closes long connections); the client
  reconnects with backoff from 1 s to 60 s, and waits 120 s after a refusal.

## Caching and storage

| What | Where | Lifetime |
|---|---|---|
| FastF1 HTTP and session cache | `FASTF1_CACHE_DIR` | Persistent, managed by FastF1 |
| Loaded session dicts | `data/runtime_cache.py` | Process lifetime, LRU-bounded |
| Schedules, race-weekend check | `@st.cache_data` in `ui/layout.py` | Up to an hour; Settings clears them |
| Records | `F1_METRICS_STORE` (`metrics_store.sqlite`) | Persistent |
| Replays, live recordings | `REPLAY_DIR` | Persistent |
| Update check answer | user cache directory | One day |

In a git checkout the defaults are repo-local (`ff1_cache/`,
`replay_sessions/`, `metrics_store.sqlite`, `.env`). An installed copy uses
the `platformdirs` user cache, data and config directories; `f1dash paths`
prints them.

## Configuration

`config.py` loads `.env` without overriding real environment variables, then
builds one `Config`:

```python
@dataclass
class Config:
    fastf1_cache_dir: str        # FASTF1_CACHE_DIR
    replay_dir: str              # REPLAY_DIR
    metrics_store_path: str      # F1_METRICS_STORE
    env_path: str                # where .env was looked for
    default_year: int = 2024     # DEFAULT_YEAR
    default_gp: str = "Abu Dhabi"  # DEFAULT_GP
    default_session: str = "R"   # DEFAULT_SESSION
    log_level: str = "WARNING"   # LOG_LEVEL
```

The distance-grid step is `TelemetryProcessor.DISTANCE_STEP`; cache lifetimes
are set at each `@st.cache_data` call site. The other variables are read where
they are used: `F1TV_SUBSCRIPTION_TOKEN`, `F1_LIVE_CONTROLS`,
`F1_LIVE_AUTORECORD`, `F1_REPLAY_PLAYER`, `F1_CACHE_MAX_ENTRIES`,
`F1_CACHE_MAX_BYTES`, `F1_UPDATE_CHECK` and, for tests, `F1_NETWORK_TESTS`.
`.env.example` lists every one; `tests/test_env_example.py` keeps it complete.

## Dependencies

`requirements.txt` holds the runtime floors and ceilings (Streamlit 1.55 or
newer, pandas 2.x, FastF1 3.8, pyarrow, websockets, platformdirs, ...) and
equals `[project] dependencies` in `pyproject.toml`. `requirements.lock` and
`requirements-dev.lock` are universal, hashed locks compiled by
`scripts/lock_requirements.py`.

## CI

`.github/workflows/ci.yml`, on pushes to `main`, pull requests and on demand:

- `lock` - re-compiles the locks and fails on any difference;
- `lint-and-test` - Ubuntu with Python 3.11, 3.12, 3.13 and 3.14, plus Windows
  with 3.11 and 3.14: ruff, black `--check`, mypy, pytest with a coverage
  floor, and a check that the run left the working tree clean;
- `perf` - the wall-clock budgets (`-m perf`) and the real-server smoke test
  (`-m smoke`);
- `js` - the replay player's JavaScript with `node --test`;
- `package` - `uv build`, `uv tool install` of the wheel and `f1dash --version`
  on Ubuntu and Windows;
- `installers` - `install.ps1` / `install.sh` from the checkout, twice;
- `audit` - `pip-audit` over the runtime lock.

`.github/workflows/network.yml` runs the opt-in network tests nightly and
opens an issue when they fail. `.github/workflows/release.yml` runs on a
pushed `v*` tag: `uv build`, a check that the tag equals the version in
`pyproject.toml`, a smoke install of the wheel (`uv tool install`, then
`f1dash --version`) on Ubuntu and Windows, and a GitHub Release with the wheel,
the sdist, both installers and the `CHANGELOG.md` section as notes. Publishing
to PyPI (trusted publishing, `uv publish`) runs only when the repository
variable `PUBLISH_PYPI` is `true`.

## Project tree

```
.
├── app.py                      # Streamlit script (orchestration only)
├── f1dash_cli.py               # the installed `f1dash` command
├── config.py                   # .env, data locations, version
├── install.ps1 / install.sh    # one-line installers (uv + f1dash)
├── pyproject.toml              # package metadata, ruff/black/mypy config
├── requirements.txt            # runtime dependencies
├── requirements-dev.txt        # test and lint dependencies
├── requirements.lock           # hashed runtime lock
├── requirements-dev.lock       # hashed development lock
├── pytest.ini                  # testpaths = tests, markers
├── .env.example                # every environment variable, commented
├── .streamlit/config.toml      # theme and server settings
├── readme.md, CHANGELOG.md, LICENSE, NOTICE
├── ARCHITECTURE.md             # this file
├── CLAUDE.md                   # rules for agents working on the code
├── IMPROVEMENTS.md             # open work and the UI guideline
├── tasks.md                    # audit register
├── layout.md                   # dashboard and replay-screen specification
├── docs/history/               # superseded research notes
├── data/
│   ├── fastf1_adapter.py
│   ├── openf1_adapter.py
│   ├── jolpica_adapter.py
│   ├── live_adapter.py
│   ├── live_recorder.py
│   ├── live_service.py
│   ├── live_state.py
│   ├── runtime_cache.py
│   ├── signalr_core.py
│   ├── source_manager.py
│   ├── token_store.py
│   └── update_check.py
├── processing/
│   ├── driver_selection.py
│   ├── metrics_store.py
│   ├── pace.py
│   ├── pit_loss.py
│   ├── replay.py
│   ├── replay_model.py
│   ├── lap_compare.py
│   ├── replay_payload.py
│   ├── telemetry_processor.py
│   ├── time_utils.py
│   ├── timing.py
│   ├── track_geometry.py
│   └── track_periods.py
├── ui/
│   ├── dashboard.py
│   ├── fonts.py
│   ├── layout.py
│   ├── pages.py
│   ├── preferences.py
│   ├── replay_view.py
│   ├── status.py
│   ├── theme.py
│   ├── track_map.py
│   ├── assets/fonts/           # Titillium Web (OFL-1.1)
│   └── components/replay_player/
├── scripts/
│   ├── capture_fixture.py      # record a live-feed fixture
│   ├── convert_legacy_replay.py
│   ├── inspect_fastf1.py       # manual exploration, not a test
│   ├── live_smoke.py           # live end-to-end check (race weekends)
│   ├── lock_requirements.py
│   └── preview_replay_player.py
├── tests/                      # offline suite; network tests behind a marker
│   ├── fixtures/live/          # recorded F1 timing excerpt (see NOTICE)
│   └── js/                     # the player's JavaScript tests
└── .github/
    ├── dependabot.yml
    └── workflows/              # ci.yml, network.yml, release.yml
```

Generated and ignored: `ff1_cache/`, `replay_sessions/`,
`metrics_store.sqlite`, `.env`.
