# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The virtualenv is not activated automatically — call its interpreter directly:
`.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere (written `python` below).
Install with `uv pip install -r requirements-dev.lock` (or `pip install --require-hashes -r ...`).

```bash
# Run the app (see "Bare mode is the hazard" below)
streamlit run app.py

# Tests (offline, deterministic). pytest.ini already passes -q; deprecation
# warnings are errors (TEST-06).
python -m pytest
python -m pytest tests/test_fastf1_adapter.py                       # one file
python -m pytest -k test_get_location                               # one test by name
python -m pytest tests/test_fastf1_adapter.py::TestFastF1Adapter::test_get_location

# Opt-in network tests (real FastF1/Jolpica endpoints + full-app smoke test)
F1_NETWORK_TESTS=1 python -m pytest -m network
# Wall-clock budgets and the real-server smoke test (deselected by default)
python -m pytest -m perf
python -m pytest -m smoke

# Lint / format / types - CI runs all of them on Python 3.11-3.14 (Ubuntu) and
# 3.11/3.14 (Windows), plus the player's jsdom tests and pip-audit
python -m ruff check .
python -m black --check .
python -m mypy --ignore-missing-imports app.py data processing ui

# The replay player's JavaScript tests (Node 22; also run by tests/test_player_js.py)
cd tests/js && npm ci && node --test

# Live SignalR end-to-end check (only meaningful during a race weekend)
python scripts/live_smoke.py 30
```

`scripts/inspect_*.py` are manual exploration scripts, not tests — `pytest.ini` pins
`testpaths = tests` so they are never collected.

## Bare mode is the hazard; `app.py` self-relaunches

`python app.py` would put Streamlit in **bare mode**: widgets return defaults, session state
is unavailable, and **`st.stop()` becomes a no-op** — which silently converts any load failure
into a confusing crash further down the script.

`app.py` handles this by re-entering through Streamlit's own CLI (`launch_via_streamlit()`),
so both entry points work. Two things to preserve:

- The relaunch check sits **above the heavy imports**, because applying `ui.layout`'s
  `@st.cache_data` decorators without a runtime emits "No runtime found" warnings.
- `_RELAUNCH_FLAG` guards against a fork loop; don't remove it.

Keep the bare-mode hazard in mind when adding error paths — after `st.stop()`, always `raise`
as well, so no execution context can continue without data.

To exercise rendering programmatically, use `streamlit.testing.v1.AppTest` (see
`TestAppSmoke` in `tests/test_integration_network.py`), which runs the script in a real
script-run context.

## Architecture

### The unified session dict is the central contract

Every data source produces the **same** dict shape, so one set of renderers works for all of
them. This is the single most important thing to preserve:

```python
{
  'session_info': {...},                    # gp, year, session_type, telemetry_scope, ...
                                            # + session_start (s), segment_starts [s], total_laps,
                                            #   replay_clock {start, lights_out, end, step} (JSON-safe)
  'telemetry':    {driver: DataFrame},      # Distance, Time, Speed, Throttle, Brake, RPM, nGear, DRS
  'laps':         DataFrame,                # Driver, LapNumber, LapTime, Sector{1,2,3}Time, IsPitOutLap
  'stints':       DataFrame,                # Driver, Stint, Compound, LapStart, LapEnd, LapCount
  'location':     {driver: DataFrame},      # Distance, X, Y, Z
  'positions':    DataFrame,                # Time (s), Driver, X, Y - whole session on a 0.5 s grid
  'timing_stream': DataFrame,               # Time (s), Driver, Position, GapToLeader, IntervalToPositionAhead,
                                            #   GapSeconds, GapLapsDown, IntervalSeconds, IntervalLapsDown
  'track_status': DataFrame,                # Time (s), Status ('1' green .. '7' VSC ending), Message
  'weather':      DataFrame,                # Time, AirTemp, TrackTemp, Humidity, Pressure, Rainfall, Wind*
  'race_control': DataFrame,                # Time (wall clock), SessionTime, Lap, Category, Flag, Scope, Message
  'compound_colors': {compound: hex},       # FastF1's official per-season tyre colours
  'drivers':      DataFrame,                # driver_number, name_acronym, team_colour, team_name, full_name
  'source':       'fastf1'|'livef1'|'live'|'replay',
  'is_live':      bool,
  'live_client':  SignalRLiveAdapter,       # live only
}
```

Replay files carry a `schema` version (currently **8**); `_load_replay` accepts older files
by defaulting the keys they lack, and rejects newer ones with a clear message. Bump
`REPLAY_SCHEMA_VERSION` whenever this dict gains or changes a persisted key. A replay's
`meta.json` is untrusted input: it is validated against the known tables and value keys before
any file is read (SEC-02), and non-JSON values are refused at save time rather than written as
their `str()` (REPLAY-19).

`DataSourceManager.get_session_data()` returns it for historical/replay sources;
`poll_live_data()` returns the same shape from the live SignalR buffers. Adding a source
means producing this dict — not touching the UI.

### Layering (keep these boundaries)

- `app.py` — orchestration only: selection → load → process → record. No chart code.
- `ui/` — **all** rendering. `ui/layout.py` holds every Streamlit panel and Plotly chart (earlier
  `layout_new.py` / `layout.py.backup` variants were deleted deliberately); `ui/pages.py` the
  pages; `ui/dashboard.py` and `ui/track_map.py` the Results tower HTML and SVG map;
  `ui/replay_view.py` plus `ui/components/replay_player/` the browser replay player. No data
  fetching beyond `st.cache_data`-wrapped lookups.
- `processing/` — pure transforms over DataFrames; no Streamlit, no network. `timing.py` builds
  the tower rows, `replay_model.py` the moment-by-moment snapshots, `analysis.py` the Analysis
  tables.
- `data/` — adapters. Each owns one upstream API and normalizes to the dict above.

### Two-tier caching (deliberately different lifetimes)

- `data/runtime_cache.py` — **ephemeral**, process-lifetime, holds whole session dicts for
  instant re-selection. `begin_session()` must be called **once per Streamlit session**,
  guarded by `st.session_state`. Streamlit re-executes the script top-to-bottom on every
  interaction, so calling it unguarded wipes the cache on every click and defeats it entirely.
- `processing/metrics_store.py` — **persistent** SQLite in WAL mode (`metrics_store.sqlite` at
  `config.metrics_store_path`, override with `F1_METRICS_STORE`). Fastest lap / sectors / top
  speed survive restarts by design; a session's records are recomputed from its *valid* laps
  each time, written only on change, and all-time records compare a circuit only with itself.

Anything in `ui/layout.py` that hits the network on a rerun (schedule lookup, race-weekend
probe) must be wrapped in `@st.cache_data` with a TTL.

## Domain gotchas that will re-break if forgotten

### FastF1

- **`get_pos_data()` has no `Speed` channel.** Calling `.add_distance()` on it raises
  `ValueError: Telemetry does not contain required channels 'Time' and 'Speed'`. Take X/Y/Z
  from the merged `get_telemetry()`, which already carries Distance. This one crash took down
  every session load; `test_get_location_never_integrates_bare_position_data` guards it.
- **There is no `IsPitOutLap` column.** FastF1 exposes `PitOutTime`/`PitInTime` timestamps;
  the boolean is derived in `get_laps()`.
- **X/Y/Z are in 1/10 metre**, so GPS arc length must be divided by 10 to get metres.
- **`Distance` accumulates over the lap selection** — ~300 km across a race, which makes the
  x-axis meaningless for driver comparison and ships ~1 M points to the browser. Hence
  `telemetry_scope`: `fastest` (default, `0 → lap length`, comparable) vs `session`.
- `pick_fastest()` returns `None` when no lap has a valid time; drivers can have zero laps.
- Pre-season testing events appear **twice** per season and have no Race session — filtered
  out of the schedule by `EventFormat == 'testing'`.

### Live SignalR feed

- **The endpoint is SignalR Core: `wss://livetiming.formula1.com/signalrcore`.** The client is
  `data/signalr_core.py`. The classic `/signalr/` hub (LiveF1's `RealF1Client`) answers **401**
  since F1's 2025 move; LiveF1 is no longer used for live. Handshake: `OPTIONS
  /signalrcore/negotiate` (collect the `AWSALBCORS` cookie; the status is irrelevant) → `POST
  /signalrcore/negotiate?negotiateVersion=1` → `connectionToken` → socket `?id=<token>` →
  `{"protocol":"json","version":1}` + `0x1E` → `Subscribe` invocation.
- The `Subscribe` **completion** (type 3) carries `{topic: full_state}` → `seed_state()`; feed
  messages (type 1, target `feed`) carry `[topic, data, timestamp]` → `handle_message()`. The
  recorder and fixture replay use the same two entry points - keep it that way.
- Frames hold several JSON messages separated by `0x1E`. Type 6 = ping (~15 s, the only traffic
  between sessions), type 7 = close. The client pings every 10 s, treats 60 s of silence as a
  dead socket, reconnects with backoff (1 → 60 s; 120 s after 401/403) and F1 drops long
  connections (~2 h), so reconnecting is normal.
- An F1TV subscription token (`F1TV_SUBSCRIPTION_TOKEN`: the JWT or the `login-session` cookie
  value) is sent as `Authorization: Bearer`. Without it `CarData.z`/`Position.z` never arrive;
  they are not subscribed (`AUTH_TOPICS`). Never call `fastf1.internals.f1auth` in the server.
- `handle_message` normalises raw payloads: state topics (`STATE_TOPICS`, including
  `RaceControlMessages`, `TrackStatus`, `TimingData`) are deep-merged; `CarData.z`/`Position.z`
  are decoded into flat records; `WeatherData` keeps the message timestamp. Parsers read those
  records - never raw payloads.
- Compressed topics (`CarData.z`, `Position.z`) are base64 + **raw DEFLATE**
  (`zlib.decompress(..., -zlib.MAX_WBITS)`), not zlib-wrapped.
- The live tower is ordered by the timing screen via a `standings` table built from
  `TimingData` (`LiveDataProcessor.standings_from_state`) - the same columns a replay snapshot
  has. Races read `GapToLeader`/`IntervalToPositionAhead`, other sessions `TimeDiffToFastest`.
- One adapter per process (`data/live_service.get_live_adapter()`); browser tabs only read.
- Neither the cloud sandbox nor the linked VM can reach `livetiming.formula1.com`; verify live
  behaviour with `scripts/live_smoke.py` on the maintainer's machine.

### Time parsing

Never pass live-feed time strings to `pd.to_timedelta`: it reads `'1:31.204'` as
*hours:minutes* and trips NumPy's deprecated generic-timedelta unit. Everything routes
through `processing/time_utils.to_seconds()`, which handles `Timedelta`, `'M:SS.mmm'`,
`'SS.mmm'`, numerics and NA.

### Telemetry channels

Gear, DRS and Brake are **coded** values — gear 4.7 does not exist. `resample_to_distance_grid`
interpolates only `CONTINUOUS_CHANNELS` (Speed/Throttle/RPM) and takes the nearest sample for
`DISCRETE_CHANNELS`. Gear labels are rounded whole numbers pinned to
`TelemetryProcessor.GEAR_CATEGORIES` (`N`, `1`..`8`).

DRS reads `0` throughout for **2026** sessions — the regulations removed DRS in favour of
active aero. That is correct source data, not a parsing fault.

### Deprecated FastF1 APIs to avoid

- **`fastf1.utils.delta_time`** is deprecated since FastF1 3.0, is documented as "no longer a
  stable part of the API", and emits a `FutureWarning`. `ui.layout._time_delta` integrates
  `ds / v` over a shared distance grid instead. It lands within ~0.1–0.3 s of the true
  lap-time gap, which the UI states plainly — don't present it as exact.
- Prefer `fastf1.plotting.get_compound_mapping(session)` over a hardcoded compound table; the
  branding is per-season.

### Streamlit API

Use `width="stretch"`, not the deprecated `use_container_width=True`.

## Testing conventions

Mocks must match the **real** upstream shapes. Mocks that diverged (a plain DataFrame standing
in for a FastF1 `Telemetry`, an `IsPitOutLap` column FastF1 never emits) previously let a
production crash pass CI while all tests were green. When mocking FastF1, mirror the actual
columns and the actual failure modes — `tests/test_fastf1_adapter.py` shows the pattern,
including a `PositionOnly` frame whose `add_distance()` raises exactly as FastF1's does.

Network-dependent tests belong in `tests/test_integration_network.py` behind the `network`
marker and `F1_NETWORK_TESTS=1`, so the default suite stays offline and deterministic.

## Config

`config.py` loads `.env` at import time, and `app.py` imports it **before** the adapters so
the values are in place when they read them. `DataSourceManager` takes `cache_dir` /
`replay_dir` defaulting to `config.fastf1_cache_dir` / `config.replay_dir`
(`FASTF1_CACHE_DIR`, `REPLAY_DIR`).

`tasks.md` is the standing audit register — findings and their fixes across every review
round, plus open follow-ups. Check it before re-investigating something that looks broken.
`IMPROVEMENTS.md` is the agent-loop plan: rules, the UI guideline and the open items.
