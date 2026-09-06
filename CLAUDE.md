# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The virtualenv is not activated automatically — call its interpreter directly.

```bash
# Run the app (see "Never run app.py directly" below)
streamlit run app.py

# Tests (offline, deterministic)
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest tests/test_fastf1_adapter.py -q          # one file
.venv/Scripts/python -m pytest -k test_get_location -q                  # one test by name
.venv/Scripts/python -m pytest tests/test_fastf1_adapter.py::TestFastF1Adapter::test_get_location

# Opt-in network tests (real FastF1/Jolpica endpoints + full-app smoke test)
F1_NETWORK_TESTS=1 .venv/Scripts/python -m pytest -m network -q

# Lint / format (CI runs both, plus pytest, on Python 3.12)
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m black --check .

# Live SignalR end-to-end check (only meaningful during a race weekend)
.venv/Scripts/python scripts/live_smoke.py 30
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
  'telemetry':    {driver: DataFrame},      # Distance, Time, Speed, Throttle, Brake, RPM, nGear, DRS
  'laps':         DataFrame,                # Driver, LapNumber, LapTime, Sector{1,2,3}Time, IsPitOutLap
  'stints':       DataFrame,                # Driver, Stint, Compound, LapStart, LapEnd, LapCount
  'location':     {driver: DataFrame},      # Distance, X, Y, Z
  'weather':      DataFrame,                # Time, AirTemp, TrackTemp, Humidity, Pressure, Rainfall, Wind*
  'race_control': DataFrame,                # Time, Lap, Category, Flag, Scope, Message
  'compound_colors': {compound: hex},       # FastF1's official per-season tyre colours
  'drivers':      DataFrame,                # driver_number, name_acronym, team_colour, team_name, full_name
  'source':       'fastf1'|'livef1'|'live'|'replay',
  'is_live':      bool,
  'live_client':  SignalRLiveAdapter,       # live only
}
```

Replay files carry a `schema` version (currently **2**); `_load_replay` accepts older files
by defaulting the keys they lack, and rejects newer ones with a clear message. Bump
`REPLAY_SCHEMA_VERSION` whenever this dict gains or changes a persisted key.

`DataSourceManager.get_session_data()` returns it for historical/replay sources;
`poll_live_data()` returns the same shape from the live SignalR buffers. Adding a source
means producing this dict — not touching the UI.

### Layering (keep these boundaries)

- `app.py` — orchestration only: selection → load → process → record. No chart code.
- `ui/layout.py` — **all** rendering. Single canonical module; earlier `layout_new.py` /
  `layout.py.backup` variants were deleted deliberately. No data fetching.
- `processing/` — pure transforms over DataFrames; no Streamlit, no network.
- `data/` — adapters. Each owns one upstream API and normalizes to the dict above.

### Two-tier caching (deliberately different lifetimes)

- `data/runtime_cache.py` — **ephemeral**, process-lifetime, holds whole session dicts for
  instant re-selection. `begin_session()` must be called **once per Streamlit session**,
  guarded by `st.session_state`. Streamlit re-executes the script top-to-bottom on every
  interaction, so calling it unguarded wipes the cache on every click and defeats it entirely.
- `processing/metrics_store.py` — **persistent** JSON (`metrics_store.json`, override with
  `F1_METRICS_STORE`). Fastest lap / sectors / top speed survive restarts by design.

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

- LiveF1's `RealF1Client` runs messages through its own `function_map` **before** invoking
  callbacks, so buffered records are already flat dicts using LiveF1's key names
  (`DriverNo`, `speed`, `n_gear`, `Tla`, …) — not the raw wire names.
- Only subscribe to topics LiveF1 can parse; an unknown topic raises `ParsingError` on
  *every* message. The vetted list is `SignalRLiveAdapter.TELEMETRY_TOPICS`.
- `RealF1Client.run()` creates and owns its own event loop, so it must run on a bare
  background thread — never inside an existing asyncio loop.
- Compressed topics (`CarData.z`, `Position.z`) are base64 + **raw DEFLATE**
  (`zlib.decompress(..., -zlib.MAX_WBITS)`), not zlib-wrapped.

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

`tasks.md` is the standing audit register — findings and their fixes across four review
rounds, plus open follow-ups. Check it before re-investigating something that looks broken.
