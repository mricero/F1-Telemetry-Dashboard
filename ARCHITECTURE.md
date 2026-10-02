# F1 Telemetry Dashboard - Architecture

This document describes the code as it is. `CLAUDE.md` holds the rules that keep it correct (the unified session dict, the bare-mode hazard, the FastF1 and live-feed gotchas); `IMPROVEMENTS.md` holds the open work and `tasks.md` the record of what was fixed and why. Where this file and the code disagree, the code is right and this file is a bug (`tests/test_docs_architecture.py` checks the module list).

## Overview

A Streamlit app that replays, tabulates and charts Formula 1 sessions:

- **Historical sessions** through FastF1, which reads F1's public live-timing archive and caches it on disk.
- **Live sessions** over F1's SignalR Core hub, `wss://livetiming.formula1.com/signalrcore`, with the app's own client (`data/signalr_core.py`). This is an **unofficial** use of undocumented endpoints. Timing, race control, weather and track status are free; car telemetry and positions need the user's own F1TV subscription token in `F1TV_SUBSCRIPTION_TOKEN`. LiveF1's `RealF1Client` is no longer used: it targets the legacy `/signalr/` hub, which answers 401 since 2025.
- **Saved replays**: a loaded session written to a directory of Parquet tables plus a JSON manifest, loadable offline.
- **Championship standings** from Jolpica, the Ergast-compatible REST API.

Every source produces the **same session dict** (documented in `CLAUDE.md`), so one set of renderers serves all of them. Adding a source means producing that dict, not touching the UI.

## Layers

```
app.py ............ orchestration: select -> load -> process -> record -> navigate
  │
  ├── ui/ ......... all rendering (Streamlit pages, HTML tower, SVG map, Plotly charts,
  │                 the browser replay player); no data fetching except cached schedule,
  │                 standings and update lookups
  ├── processing/ . pure transforms over DataFrames; no Streamlit, no network
  └── data/ ....... one adapter per upstream, each normalising to the session dict
```

`app.py` stays free of chart code; `ui/layout.py` is the single module for panels and charts (earlier `layout_new.py` variants were deleted on purpose).

### Script flow (`app.py`)

1. `render_header()` sets the page config and the embedded fonts and styles.
2. `init_browser_session()` creates the per-tab `DataSourceManager`, `TelemetryProcessor` and `MetricsStore`.
3. `render_session_selector()` returns a selection only after **Load session** (or a valid shared link). Without one, the navigation offers a start page and **Settings**.
4. `load_session_data()` reads the process-wide runtime cache or calls `DataSourceManager.get_session_data()` inside an `st.status`. A load failure shows the error, calls `st.stop()` and then raises, so no execution context continues without data. Live sessions and sessions that ended in the last few hours (`provisional`) are not cached.
5. `processed_views()` builds laps, stints and colours once per session (telemetry alignment lazily, on first use); `evict_other_sessions()` keeps one session's entries per tab.
6. `record_metrics()` folds the session into the records store once per tab and session.
7. `st.navigation()` runs one page from `ui/pages.py`.

`python app.py` re-enters through Streamlit's CLI (`launch_via_streamlit()`), because a bare interpreter would put Streamlit in bare mode; see `CLAUDE.md`.

## Data layer (`data/`)

| Module | Role |
|---|---|
| `fastf1_adapter.py` | FastF1: schedule (testing events dropped, an event offered once its first session has ended), session load with a three-session process cache (`load_session`), laps with validity, pit and speed-trap columns, per-driver telemetry and GPS frames for the `fastest` or `session` scope, the position timeline, timing stream, track status, race control on the session clock, circuit info, compound colours, `ended_recently` and the not-yet-archived check (`SessionNotArchivedError`). The FastF1 HTTP cache is enabled once per directory per process. |
| `jolpica_adapter.py` | Jolpica (Ergast-compatible): paged, throttled to 4 requests/s, honours `Retry-After` up to 10 s and fails fast beyond it; memoised per instance (keyword arguments included); the HTTP session is built on first use. `standings()` parses both championships into tables. |
| `signalr_core.py` | The SignalR Core client: negotiate (cookie, then `negotiateVersion=1`), websocket, JSON handshake, `Subscribe`; ping every 10 s, 60 s of silence counts as dead, reconnect with backoff 1 to 60 s (reset after a connection that delivered data), 120 s after a refusal (401/403/429, also at the websocket upgrade). An expired token is not sent; a rejected one is dropped for a token-free retry without the auth topics. Bearer tokens and connection ids are redacted from every log line. |
| `live_state.py` | Deep-merged state for the keyframe-plus-delta topics (`STATE_TOPICS`): index-addressed dicts over lists, `_deleted` honoured. A subscription snapshot replaces each topic. |
| `live_adapter.py` | `SignalRLiveAdapter`: the two entry points `seed_state(snapshot)` and `handle_message(topic, data, timestamp)`; decodes `CarData.z`/`Position.z` (base64 plus raw DEFLATE); bounded buffers for the time series; resets state when `SessionInfo` names a new session; optional auto-recording; a `SnapshotCache` shared by every tab (one rebuild per change token) with a per-second history for the broadcast delay. `LiveDataProcessor` turns state into the session dict's tables, including the qualifying standings with segments and knock-outs. |
| `live_recorder.py` | Records the raw stream (`subscribe.json` plus `live.jsonl`, reconnect snapshots inline) and replays it through the same two entry points. |
| `live_service.py` | `get_live_adapter()`: one live adapter per process; browser tabs only read it. |
| `source_manager.py` | `DataSourceManager`: `get_session_data()` for `fastf1`, `live` and `replay`; `poll_live_data()`; `live_session()` against the unfiltered schedule; replay save and load (see below). |
| `runtime_cache.py` | Process-wide, LRU, bounded by entry count and bytes (`F1_CACHE_MAX_ENTRIES`, `F1_CACHE_MAX_BYTES`). Never wiped when a tab opens. |
| `token_store.py` | Writes `F1TV_SUBSCRIPTION_TOKEN=` into the user's `.env` on an explicit Save. |
| `update_check.py` | At most one GitHub releases request a day, 3 s timeout, cached; silent offline; `F1_UPDATE_CHECK=0` disables it. |

### Saved replays (schema 8)

`save_replay()` writes `<name>_<utc>/` with `meta.json` (schema, `app_version`, plain values) and one Parquet file per table: the frame keys, the per-driver frame groups under subfolders, and `circuit_info.corners.parquet`. Loading validates `meta.json` before touching any file: only known tables, table groups and value keys; driver names that look like paths and a replay claiming to be live are refused. A replay always loads with `source="replay"` and `is_live=False`, and keys an older schema lacks get defaults (a schema 7 corners string becomes an empty table). Legacy pickle replays are not listed; `scripts/convert_legacy_replay.py --trust` converts one. Bump `REPLAY_SCHEMA_VERSION` whenever the dict gains or changes a persisted key.

## Processing layer (`processing/`)

| Module | Role |
|---|---|
| `time_utils.py` | `to_seconds()` and `seconds_series()`: one grammar (`[D days ]H:MM:SS`, `M:SS.mmm`, `SS.mmm`, Timedelta, numbers) that never sends a string to `pd.to_timedelta`; `parse_gap()` for gap cells. A property test holds the scalar and vector parsers equal. |
| `telemetry_processor.py` | Distance-grid resampling (continuous channels interpolated, coded channels nearest-sample), driver alignment, unit normalisation, colour maps, gear categories. |
| `timing.py` | The timing-tower rows for the Results page: classification, gaps, sectors and mini-sectors from valid laps only, qualifying cut-offs from the entry list, formats. |
| `replay.py` | The replay clock (`start`, `lights_out`, `end`, `step`) and the 0.5 s position timeline. |
| `replay_model.py` | `tower_series()`: every tower field of every driver as change-point series, and `snapshot_at(t)`, which reads only what had happened by `t` (lap deletions count from the stewards' message, not before). Flags per qualifying segment, red-flag-aware segment clocks, race OUT only after 25 s without position or timing progress, pit-lane starts, red-flag pit entries left out, `events()` for the timeline. |
| `replay_payload.py` | The browser player's payload: tower series, packed positions (delta-coded, DEFLATE, base64) and the packed interval trend. |
| `track_geometry.py` | The circuit outline fitted to the SVG view box, start/finish line and corner labels. |
| `track_periods.py` | SC, VSC and red-flag periods on the session clock and per lap, for chart shading and the pace analysis. |
| `analysis.py` | Race trace, fuel-corrected tyre pace and degradation, speed-trap ranking, deleted laps, default drivers. |
| `metrics_store.py` | The records store: SQLite in WAL mode at `config.metrics_store_path`, shared safely by every tab. A session's records are recomputed from its valid laps and written only when they change; all-time records are per circuit; old JSON stores are imported once. |

## UI layer (`ui/`)

| Module | Role |
|---|---|
| `pages.py` | The pages: **Replay**, **Results**, **Analysis** (one section at a time, a driver filter kept in the URL), **Records**, **Live** while live, and **Settings** (caches, file locations, units, version). |
| `replay_view.py` | The Replay page: mounts the player component and keeps the cursor, focus and seek in `st.session_state` across reruns and page switches. |
| `components/replay_player/` | The browser player (`player.js`, `player.css`, `player.html`): looks values up and draws them, never decides racing rules. It reports to Python only on pause, release, the end of a key burst, focus and analyse. Tested in jsdom (`tests/js`). |
| `dashboard.py` | The Results dashboard's HTML: header bar, timing tower, sector cards, map panel. |
| `track_map.py` | The SVG track map for the Results and Live pages. |
| `layout.py` | Every Streamlit panel and Plotly chart, the session picker, the live controls and the token helper. Network lookups on a rerun are wrapped in `st.cache_data` with a TTL. |
| `theme.py` | Design tokens and shared CSS (guideline section 5 of `IMPROVEMENTS.md`); contrast is tested. |
| `fonts.py` | Titillium Web embedded as base64 `@font-face`; no runtime font requests. |
| `status.py` | Why a panel is empty, in one consistent box. |
| `units.py` | Metric or imperial display, per viewer, seeded from `?units=`. |

## Live pipeline

```
SignalRCoreClient (thread) --Subscribe result--> SignalRLiveAdapter.seed_state()
                           --feed messages-----> SignalRLiveAdapter.handle_message()
                                                    ├─ STATE_TOPICS -> LiveState (deep merge)
                                                    └─ CarData.z / Position.z / WeatherData -> bounded buffers
Live page fragment (every 3 s) -> DataSourceManager.poll_live_data()
                                    -> SnapshotCache (one build per change token, all tabs)
                                    -> optional broadcast delay (snapshot from N s ago)
                                    -> the session dict -> the same renderers
```

Stopping, clearing or recording the shared feed is offered only to a browser on the same machine, or to everyone with `F1_LIVE_CONTROLS=1`. Verify live behaviour with `scripts/live_smoke.py` on a machine that can reach the endpoint.

## Caching and persistence

| What | Where | Lifetime |
|---|---|---|
| Loaded session dicts | `data/runtime_cache.py`, process memory | until the process exits or Settings clears it; live and provisional sessions are never stored |
| Loaded FastF1 sessions | `data/fastf1_adapter.py`, process memory, three entries | same; recent sessions are not kept |
| Schedules, standings, update check | `st.cache_data` with a TTL | up to an hour; Settings clears the schedules |
| FastF1 downloads | `FASTF1_CACHE_DIR` | on disk until deleted (Settings can delete it) |
| Records | `F1_METRICS_STORE` (`metrics_store.sqlite`) | on disk |
| Replays and live recordings | `REPLAY_DIR` | on disk |

## Configuration

`config.py` loads `.env` (from `Config.env_path`) at import time, which is why `app.py` imports it before the adapters. A git checkout keeps the files in the project folder; an installed copy uses the per-user directories from `platformdirs`. `FASTF1_CACHE_DIR`, `REPLAY_DIR` and `F1_METRICS_STORE` override either. `Config` carries the four paths, the default selection and `log_level`; the distance-grid step lives on `TelemetryProcessor.DISTANCE_STEP` and cache lifetimes at each `st.cache_data` call site. `.env.example` lists every variable the code reads, and a test keeps it complete.

## Packaging and CI

`pyproject.toml` builds the `f1dash` wheel with hatchling; `f1dash_cli.py` is the `f1dash` command (`--port`, `--no-browser`, `--version`, `paths`, `update`). `install.ps1` and `install.sh` install it as a uv tool. `requirements.lock` and `requirements-dev.lock` are universal, hashed `uv pip compile` locks; CI re-locks and fails on a difference.

`.github/workflows/ci.yml` runs ruff, black, mypy and pytest (with a coverage floor and a clean-tree check) on Python 3.11 to 3.14 on Ubuntu and 3.11 and 3.14 on Windows, plus jobs for the wall-clock budgets and the real-server smoke test (`perf`, `smoke` markers), the player's jsdom tests, the wheel and the installers, and `pip-audit`. `network.yml` runs the opt-in network tests nightly. `release.yml` builds, checks the tag against the version, smoke-installs on Windows and Linux and publishes a GitHub Release on a `v*` tag. Dependabot watches pip, GitHub Actions and pre-commit.

## Project structure

```
app.py, config.py, f1dash_cli.py
data/        fastf1_adapter, jolpica_adapter, signalr_core, live_state, live_adapter,
             live_recorder, live_service, source_manager, runtime_cache, token_store,
             update_check
processing/  time_utils, telemetry_processor, timing, replay, replay_model, replay_payload,
             track_geometry, track_periods, analysis, metrics_store
ui/          pages, replay_view, dashboard, track_map, layout, theme, fonts, status, units,
             components/replay_player/ (player.js, player.css, player.html), assets/fonts/
scripts/     live_smoke, capture_fixture, convert_legacy_replay, inspect_fastf1,
             lock_requirements, preview_replay_player
tests/       pytest suite (offline by default; `network`, `perf`, `smoke` markers),
             tests/fixtures/live (recorded 2023 feed excerpts), tests/js (player tests)
docs/history/  superseded research notes
```
