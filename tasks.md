# 📋 F1 Telemetry Dashboard — Task Register

A tracked list of issues found while auditing the repository (code + all
documentation) and their status. Items marked ✅ are fixed in this pass;
items marked ⬜ are recommended follow-ups.

---

## 1. Critical Code Fixes (✅ Done)

| # | File | Issue | Fix |
|---|------|-------|-----|
| 1.1 | `ui/layout.py:48` | **SyntaxError** — `elif` indented inside a `with` block, so the module could not be imported at all | Corrected indentation; live detection now uses exact label match (`source == "Live (SignalR)"`) so `"LiveF1 (Historical)"` no longer falsely triggers live mode |
| 1.2 | `data/source_manager.py:71` | `self.jolpica.is_race_weekend()` called but `jolpica` was never initialized → `AttributeError` on every session load | Added `self.jolpica = JolpicaAdapter()` in `__init__` (+ import); bare `except:` replaced with `except Exception:` |
| 1.3 | `data/jolpica_adapter.py:166,265` | `is_race_weekend()` defined **twice** (the second silently shadowed the first) | Removed duplicate; kept single timezone-aware implementation |
| 1.4 | `data/fastf1_adapter.py:34` | Timezone-naive `Timestamp.now()` compared against tz-aware `EventDate` → `TypeError` on real FastF1 schedules | Normalize both sides to UTC-aware via `pd.to_datetime(..., utc=True)` |
| 1.5 | `data/fastf1_adapter.py:52,89` | Unguarded `.add_distance()` calls crash on objects lacking that method (and on empty position data) | Guarded with `hasattr(...)` + column checks; column selection filtered to available columns |
| 1.6 | `data/fastf1_adapter.py:75` | `get_stints()` raised `KeyError: 'LapNumber'` when laps already carried `LapStart`/`LapEnd` columns | Supports both layouts: pre-aggregated `LapStart/LapEnd` or derivation from `LapNumber`; always emits `LapCount` |
| 1.7 | `data/live_adapter.py:73,97` | `RealF1Client.run()` internally calls `asyncio.run()`, which raises `RuntimeError` when invoked from another running loop (`start_async` wrapped it in a new loop) | Background thread now calls `run()` directly (client owns its loop); thread errors captured in `_thread_error`; buffer handler hardened for non-list payloads |
| 1.8 | `processing/telemetry_processor.py:84` | `process_laps()` mapped `'Driver'` (acronyms like `VER`) through a driver-**number** keyed dict → `DriverAcronym` was all-NaN and lap charts rendered empty | Number-keyed mapping only applies to API-style tables; acronym-keyed mapping added; final fallback to raw `'Driver'`; `render_lap_times` falls back to `'Driver'` column and drops NaN drivers |
| 1.9 | `app.py:226` | Function typo `render_tire_strategy_strategy` (call site diverged from definition risk); stint rows crashed without fallback when `Compound` was NA | Renamed to `render_tire_strategy`; `driver_col` resolution + `str(...).upper()` coercion |
| 1.10 | `app.py:61-66` | GP dropdown pulled Jolpica `race_name` values (e.g. "Bahrain Grand Prix") for the LiveF1 source, but the circuit map expects short keys → LiveF1 lookups failed | Unified GP listing through FastF1 schedule; `DataSourceManager._gp_to_circuit_short()` normalizes names case-insensitively ("Bahrain Grand Prix" → "Sakhir") before LiveF1 lookup |
| 1.11 | `data/source_manager.py` | Dead code: unused `_load_l`, `_parse_jolpica_laps` stubs; unused imports (`Dict/Optional/Union`, `LiveDataProcessor`) | Removed |
| 1.12 | `data/source_manager.py:44` | `source="replay"` with no file fell through to confusing `ValueError: Unknown source: replay` | Explicit early error: `"source='replay' requires a replay_file"` |
| 1.13 | `config.py` | `.env` file shipped and `python-dotenv` in requirements, but nothing ever called `load_dotenv()` — documented env-var configuration silently never applied | `load_dotenv()` invoked at import time (graceful if package missing) |

## 2. Test Suite Fixes (✅ Done)

| # | Issue | Fix |
|---|-------|-----|
| 2.1 | `test_init_enables_cache` failed: adapter passed `str(Path("./test_cache"))` = `"test_cache"`, not the configured path | Adapter passes the original string to `fastf1.Cache.enable_cache()` |
| 2.2 | `test_get_available_sessions` used tz-aware mock dates against naive comparison (see 1.4) | Passes after UTC normalization |
| 2.3 | `test_get_telemetry` / `test_get_location`: mocks lacked `empty=False`, so adapters returned empty DataFrames; mocks also stubbed `.add_distance` on plain DataFrames | Mocks set `mock_laps.empty = False`; adapter's `hasattr` guard makes both paths valid |
| 2.4 | Root-level `test_fastf1.py` / `test_livef1.py` were network-dependent inspection scripts collected (and executed!) by pytest at repo root | Moved to `scripts/inspect_fastf1.py` / `scripts/inspect_livef1.py` via `git mv`; functions renamed (`inspect_*`); added `pytest.ini` with `testpaths = tests` |

## 3. Documentation Fixes (✅ Done)

| # | Document | Issue | Fix |
|---|----------|-------|-----|
| 3.1 | `readme.md` | Title heading was just an emoji (`# 🏎️`) | Now `# 🏎️ F1 Telemetry Dashboard` |
| 3.2 | `readme.md` | Repo structure listed non-existent dirs (`hooks/`, `rules/`, `skills/`) and wrong test filenames | Structure updated to reality: `scripts/`, `pytest.ini`, `LICENSE`, `ui/layout*.py`, `tests/test_fastf1_adapter.py` |
| 3.3 | `readme.md` | Claimed JSON/Parquet replay storage with pyarrow dependency; actual implementation stores pickled `.pkl` files | Docs corrected; stale `pyarrow` mention removed |
| 3.4 | `readme.md` | Referenced a `LICENSE` file that did not exist | MIT `LICENSE` created (with F1 trademark disclaimer) |
| 3.5 | `ARCHITECTURE.md` | Described an `OpenF1Adapter` / `data/openf1_adapter.py` that does not exist anywhere in the repo | Replaced with the actual adapters: `JolpicaAdapter` (§1.2), `SignalRLiveAdapter`/`LiveDataProcessor` (§1.3), corrected `DataSourceManager` snippet (§1.4) |
| 3.6 | `ARCHITECTURE.md` | Architecture diagram, requirements, `.env`/`config.py` sample, project tree, data-flow, fallback matrix and extensibility list all referenced OpenF1 polling as the live source | All aligned with the implemented free SignalR/Jolpica architecture; `livef1>=1.2.0` added to documented requirements |
| 3.7 | `PHASE1_RESEARCH_SUMMARY.md` | Sections 2–5 duplicated verbatim (380 lines total, ~170 redundant) | Deduplicated (now 239 lines); "(unchanged)" stubs expanded into real summaries of each reference repo |
| 3.8 | `scripts/inspect_livef1.py` | Broken log line `"Loading LiveF1 session (20 (2024 Bahrain Race)..."` | Fixed |

## 4. Verification Performed (✅ Done)

- `py_compile` across every Python file in the repo — clean
- `pytest` — **44/44 passing**
- Import smoke test of `app`, `ui.layout`, `ui.layout_new`, all `data/*`, `processing/*`
- Logic spot-checks: `process_laps` acronyms, `process_stints` compound/count,
  `_gp_to_circuit_short` mappings

---

## 5. Round 2: SignalR Deep-Dive, Live Views & Two-Tier Caching (✅ Done)

Research pass over FastF1 livetiming docs + LiveF1 package source
(`parse_functions.py`, `MessageHandlerTemplate`) established the real wire
format and drove the following work:

| # | Item | Detail |
|---|------|--------|
| 5.1 | **SignalR format verification** | Compressed topics are base64 + **raw DEFLATE** JSON (`zlib.decompress(..., -MAX_WBITS)`); CarData channel ids `0`=RPM, `2`=Speed, `3`=Gear, `4`=Throttle, `5`=Brake, `45`=DRS; LiveF1 parses messages through its `function_map` *before* callbacks, so buffered records are flat dicts (`DriverNo`, `speed`, ...). Old parsers expected different keys entirely → would have produced all-NaN charts. |
| 5.2 | `data/live_adapter.py` rewritten | Parsers now match livef1's actual record shapes for CarData/Position/Timing/Tyres/DriverList; added `decode_zipped()` + `decode_topic_payload()` to replay FastF1-recorded raw streams; positional-list Channels tolerated; topic list trimmed to ones livef1 can parse (unknown topics crash their handler). |
| 5.3 | **Live views actually render** | New `DataSourceManager.poll_live_data()` folds buffers into the unified dict: per-driver telemetry tails (pseudo-distance), GPS trails for the track map, tyre stints from `TyreStintSeries`, laps from Timing best/last values, driver colours from `DriverList`. |
| 5.4 | **Auto-refreshing live dashboard** | `@st.fragment(run_every=3)` in `app.py`: telemetry tabs + track map + tyres + timing update every 3 s while streaming; start/error states handled; historical panels only render on non-live sources. |
| 5.5 | **Ephemeral runtime cache** | New `data/runtime_cache.py`: loaded sessions stay hot in memory while the app is open (instant re-selection); `begin_session()` at startup guarantees a fresh cache on every app launch — nothing survives closing the app. |
| 5.6 | **Persistent metrics store** | New `processing/metrics_store.py`: fastest lap, fastest S1/S2/S3, top speed per session → `metrics_store.json`; records survive restarts by design (unlike the runtime cache). Parses Timedelta, `'M:SS.mmm'` and `'SS.mmm'` formats; 🏆 panel shows this-session + all-time records. |
| 5.7 | Tyre chart hardening | `render_tire_strategy` no longer KeyErrors when `LapStart/LapEnd/LapCount` are missing (live stints arrive without lap boundaries yet). |
| 5.8 | Tests | New suites: `test_runtime_cache.py`, `test_metrics_store.py` (incl. persistence-across-restart via tmp file), `test_live_parsing.py` (real payload round-trip, poll pipeline against primed buffers, empty-buffer safety). |
| 5.9 | Docs/config | `.gitignore` + readme + ARCHITECTURE.md updated (two-tier caching section, verified wire-format notes, data-flow diagram, `F1_METRICS_STORE` env var). |

---

## 6. Round 3: Issue Sweep (✅ Done)

Every open follow-up from the earlier audits, resolved:

| # | Item | Detail |
|---|------|--------|
| 6.1 | **UI consolidated** | `ui/layout.py` is now the single canonical rendering module (all charts, selector, live fragment); `app.py` slimmed to pure orchestration; deleted `ui/layout_new.py` and `ui/layout.py.backup`. |
| 6.2 | **Jolpica cache fixed** | `@lru_cache` on instance methods replaced with `_instance_memo` decorator — no more global retention of adapter/`requests.Session`, per-instance lifetime, failure results not cached. |
| 6.3 | **Replay schema header** | Replays now save `{schema, saved_at, data}`; future schemas rejected with a clear message; legacy bare-pickle replays still load. |
| 6.4 | **Bounded SignalR buffers** | Per-topic cap (`buffer_limit=20000`, min 100) drops oldest records first via testable `SignalRLiveAdapter._buffer_topic()`; memory stays bounded in long sessions. |
| 6.5 | **True live lap numbers** | `_laps_from_timing()` tracks `NumberOfLaps` + `LastLapTime_Value` snapshots → one row per completed lap plus the in-progress lap; record-count fallback only when the feed never sends a lap counter. (Also fixed a truthy-NaN bug where missing values overwrote real lap times.) |
| 6.6 | **Real GPS distances** | `LiveDataProcessor.distance_at()` computes cumulative metres from each driver's Position.z trajectory and interpolates it onto CarData timestamps — telemetry x-axis is now true distance driven; pseudo-distance (`index*10`) only as no-GPS fallback. |
| 6.7 | **Stint bounds derived** | `TelemetryProcessor.process_stints(latest_lap=...)`: live stints without LapStart/LapEnd stack sequentially, earlier stints collapse to 1 lap placeholders, running stint stretches to the latest lap seen. |
| 6.8 | **Test suite grown** | New suites for `TelemetryProcessor` (resample/normalize/colors/laps/stints), `JolpicaAdapter` (mocked HTTP incl. caching & error paths), `source_manager` (replay round-trip + legacy + future-schema, circuit map, lap numbers, GPS distances, buffer caps). 83 unit tests + opt-in network tests. |
| 6.9 | **Network integration tests** | `tests/test_integration_network.py` marked `network`, skipped unless `F1_NETWORK_TESTS=1`; covers Jolpica schedule + FastF1 cached round-trip. |
| 6.10 | **Live E2E harness** | `scripts/live_smoke.py <seconds>`: connects to the real SignalR feed, prints per-topic buffer counts / parsed driver totals; optional raw capture via `F1_LIVE_LOG=file`. Run during a race weekend. |
| 6.11 | **CI pipeline** | `.github/workflows/ci.yml`: ruff + black --check + pytest on push/PR (Python 3.12). Local dev box blocks the ruff binary (App Control policy), so F-class checks were verified locally via an AST-based unused-import/name scan; CI runs real ruff. |
| 6.12 | **Zero-warning pytest** | Fixed at the root: NaN/garbage strings no longer reach `pd.to_timedelta`'s deprecated generic-unit path (`processing/time_utils.py` guards), ISO format hints for live timestamp parsing, registered `network` marker, stdlib `timedelta` in tests, explicit object-dtype fillna in `process_laps`. `pytest` now runs completely clean. |
| 6.13 | Housekeeping | Removed stale `ui/layout.py.backup`; pinned dependency ranges in `requirements.txt`; added `pyproject.toml` (black/ruff config, line-length 100). |

---

## 7. Round 4: Runtime Crash & Data-Correctness Sweep (✅ Done)

Triggered by a hard crash on a real load
(`AttributeError: 'NoneType' object has no attribute 'get'`, `app.py:72`,
2026 Australian GP Race). The reported error was a *symptom*; the audit
traced it to a masked exception, then swept the whole pipeline against
live FastF1 3.8.3 / Streamlit 1.59 / pandas 2.3 data.

### 7.1 Crashes

| # | File | Issue | Fix |
|---|------|-------|-----|
| 7.1.1 | `data/fastf1_adapter.py` | **Root cause.** `get_location()` called `.add_distance()` on `get_pos_data()`. Position data has no Speed channel, so FastF1 raises `ValueError: Telemetry does not contain required channels 'Time' and 'Speed'` — killing *every* FastF1 session load. | Location now comes from the merged `get_telemetry()` (which already carries X/Y/Z **and** Distance). Raw-position fallback derives distance from GPS arc length (÷10: coordinates are 1/10 m). |
| 7.1.2 | `app.py:66-72` | The real error was swallowed: `st.error(...)` then `st.stop()` — but `st.stop()` is a **no-op in bare mode**, so execution continued with `session_data = None` and crashed 6 lines later with a misleading `AttributeError`. | Load moved into `load_session_data()`, which re-raises after `st.stop()` and can never return `None`. |
| 7.1.3 | `app.py` | `python app.py` silently degrades (widgets return defaults, no session state). | `require_streamlit_runtime()` exits 2 with instructions to use `streamlit run app.py`. |
| 7.1.4 | `data/source_manager.py` | `_load_livef1_session()` did `range(len(df)) * 10` — a `TypeError`, latent on every LiveF1 load without a Distance column. | `np.arange(len(df)) * 10`; channel selection filtered to available columns. |
| 7.1.5 | `data/live_adapter.py` | `decode_zipped("")` raised `IndexError` on `text[0]`. | Explicit `ValueError` for empty payloads. |

### 7.2 Data correctness

| # | Issue | Fix |
|---|-------|-----|
| 7.2.1 | **Pit-out markers never rendered.** `get_laps()` looked for `IsPitOutLap`/`PitOutLap`; FastF1 has neither — it exposes `PitOutTime`. The column was silently dropped, so the lap chart's diamond markers were dead code. | `IsPitOutLap` derived from `PitOutTime.notna()` (32 pit-out laps now flagged in the 2026 Australian GP). |
| 7.2.2 | **Meaningless comparison axis + 1.05 M chart points.** `Distance` accumulates over all laps (302 km for a race), so "same X" was not the same corner, and 20 drivers × 60 k points hung the browser. | New **telemetry scope**: default `fastest` (each driver's fastest lap, 0 → lap length, **20,903 points — 50× fewer**); `session` still available. Surfaced as a UI radio and threaded through `get_session_data(telemetry_scope=...)`. |
| 7.2.3 | **3,691 fake gear values.** Linear interpolation of `nGear` produced fractional gears, then `.astype(str)` made each one its own chart category. | Coded channels (`nGear`, `DRS`, `Brake`) resample **nearest-neighbour**; only Speed/Throttle/RPM interpolate. Gear labels rounded to whole numbers; Gear axis pinned to `GEAR_CATEGORIES` (`N`, `1`..`8`). |
| 7.2.4 | Stint counters rendered as "Laps: 12.0" (groupby leaves floats). | `Stint`/`LapStart`/`LapEnd`/`LapCount` cast to `Int64`. |
| 7.2.5 | "Pre-Season Testing" appeared **twice** in the GP dropdown and has no Race session. | `get_available_sessions()` drops `EventFormat == 'testing'` (falls back to `RoundNumber > 0`). |
| 7.2.6 | `build_driver_color_map()` raised `TypeError` on a NaN `TeamName` (`float[:3]`). | NA-safe `_first_present()` helper; rows with no identifier are skipped. |
| 7.2.7 | `MetricsStore.update_laps()` raised `KeyError` when laps had no `Driver` column. | Falls back to `DriverAcronym`, then to `"unknown"`. |
| 7.2.8 | Track map was distorted by the container's aspect ratio. | `yaxis` pinned with `scaleanchor="x"`. |

### 7.3 Caching, performance & config

| # | Issue | Fix |
|---|-------|-----|
| 7.3.1 | **The runtime cache never cached anything.** `runtime_cache.begin_session()` ran at the top of `main()`, and Streamlit re-executes the script on *every* interaction — so the cache was wiped on each rerun and every click re-downloaded the session. | Guarded behind `st.session_state["_runtime_cache_started"]`, so it resets once per app session as intended. |
| 7.3.2 | `_is_race_weekend()` (Jolpica) and `get_available_sessions()` (FastF1 schedule) hit the network on every rerun. | `@st.cache_data` with 15 min / 60 min TTLs. |
| 7.3.3 | Session load called `get_telemetry()` **and** `get_location()` per driver — two expensive car/position merges. | `get_driver_frames()` does one merge and returns both; drivers with no laps are dropped instead of yielding empty frames. |
| 7.3.4 | `config.fastf1_cache_dir` / `config.replay_dir` were never read, so the documented `FASTF1_CACHE_DIR` / `REPLAY_DIR` `.env` settings did nothing (only `load_dotenv()` had been wired in 1.13). | `DataSourceManager(cache_dir=..., replay_dir=...)` defaults to the config values. |

### 7.4 Deprecations, CI & tests

| # | Issue | Fix |
|---|-------|-----|
| 7.4.1 | `use_container_width` is deprecated in Streamlit (removal announced after 2025-12-31); 8 call sites + 4 in the docs. | Migrated to `width="stretch"`. |
| 7.4.2 | **CI's Black step was broken.** Black's default excludes cover `.venv` but not `.venv311`, so `black --check .` walked 8,982 vendored files and reported "4170 files would be reformatted". | `extend-exclude` in `[tool.black]`; now 26 project files. |
| 7.4.3 | Ruff: `E712` (`== True`), 2× `E741` (ambiguous `l`), 6× `F841`, `E402`. | All resolved (`ruff check .` clean; `black --check .` clean). |
| 7.4.4 | **The tests hid the crash.** `test_get_location` mocked `get_pos_data()` as a plain DataFrame, so the `hasattr(..., "add_distance")` guard was never exercised; `test_get_laps` asserted on an `IsPitOutLap` column FastF1 never emits. | `tests/test_fastf1_adapter.py` rewritten against real FastF1 shapes, incl. a `PositionOnly` frame whose `add_distance()` raises exactly as FastF1's does. |
| 7.4.5 | No end-to-end coverage — `python app.py` cannot exercise rendering. | `TestAppSmoke` runs the whole dashboard via `streamlit.testing.v1.AppTest` and asserts zero exceptions/errors. **105 unit + 7 network tests green.** |
| 7.4.6 | `pyarrow` was required "for parquet support in replay", but replays are pickled and nothing imports it. | Dropped from `requirements.txt`. |

### 7.5 Verified, not a bug

- **DRS reads `0` for all 2026 sessions.** DRS was removed under the 2026 regulations (replaced by active-aero X/Z modes and a manual-override power boost). The raw FastF1 channel is zero; the pipeline reproduces it faithfully. Documented in `readme.md`.
- All `DeprecationWarning`s under pytest originate in FastF1/pandas internals, not this project (verified by promoting them to errors across the full pipeline).

---

## 8. Round 5: Launcher Fix & Peer-Review Feature Gap (✅ Done)

Triggered by `python app.py` printing instructions instead of starting, plus a
request to review comparable projects. Ten F1 dashboards were surveyed via the
GitHub API — `theOehrly/Fast-F1` (5.3k⭐), `FraserTarbet/F1Dash` (106⭐),
`bordanattila/OpenF1_tutorial` (59⭐), `matteocelani/f1-telemetry` (42⭐),
`mateenunez/f1-telemetry` (28⭐), `misha-met/Delta` (22⭐),
`arabacibahadir/f1stats` (21⭐), `SheerWill007/F1` (12⭐),
`br-g/fastf1-livetiming` (12⭐), `yashsoni27/delta-dash` (10⭐),
`aashnakunk/f1-live-pitwall` (5⭐) — alongside a re-read of the FastF1
`core` / `utils` / `plotting` docs.

### 8.1 The launcher

| # | Issue | Fix |
|---|-------|-----|
| 8.1.1 | Round 4 made `python app.py` exit with advice. Correct, but useless from an IDE's Run button — the reported symptom was simply "it doesn't work". | `launch_via_streamlit()` re-enters through Streamlit's own CLI, so **both** `streamlit run app.py` and `python app.py` start the dashboard. Extra flags pass through (`python app.py --server.port 8600`). |
| 8.1.2 | The relaunch still printed two `No runtime found, using MemoryCacheStorageManager` warnings. | Cause: `ui.layout`'s `@st.cache_data` decorators were applied during the bare first pass. The relaunch check now sits **above** the heavy imports. Console output is clean. |
| 8.1.3 | A Streamlit start that somehow lacked a runtime could fork endlessly. | `_RELAUNCH_FLAG` env guard: the second pass prints the help text and exits 2. |

### 8.2 Data that was fetched and thrown away

The survey showed every comparable project surfacing data this app already had
in memory but never rendered.

| # | Issue | Fix |
|---|-------|-----|
| 8.2.1 | **Weather was loaded and discarded** — 148 rows per session (`AirTemp`, `TrackTemp`, `Humidity`, `Pressure`, `Rainfall`, wind). | `render_weather()`: five metric tiles, a track/air temperature trace with humidity on a second axis, and a rainfall warning. |
| 8.2.2 | **Race control was never surfaced.** `RaceControlMessages` and `TrackStatus` were subscribed live and buffered but had no parser; the historical feed (167 messages) was not even read. | `FastF1Adapter.get_race_control()` plus `LiveDataProcessor.parse_race_control()` / `parse_track_status()` (both emitting the same shape), and `render_race_control()` with category filtering, newest-first. Live mode shows a green/yellow/SC/VSC/red banner. |
| 8.2.3 | **Lap `Position` was dropped** by `get_laps()`, so the lap-by-lap running order — a staple of every peer — could not be drawn. | `Position`, `Compound` and `Stint` retained; new `render_position_changes()` with P1 at the top and the legend ordered by final classification. |

### 8.3 Correctness and analysis

| # | Issue | Fix |
|---|-------|-----|
| 8.3.1 | Compound colours were hardcoded CSS names (`"red"`, `"yellow"`, `"white"`) — neither the real branding nor season-aware. | `FastF1Adapter.compound_colors()` wraps `fastf1.plotting.get_compound_mapping(session)` (SOFT `#da291c`, MEDIUM `#ffd12e`, HARD `#f0f0ec`, INTERMEDIATE `#43b02a`, WET `#0067ad`). `compound_palette()` merges session colours over a corrected fallback. |
| 8.3.2 | No head-to-head comparison — the most common FastF1 analysis, present in F1Dash, delta-dash and others. | `render_driver_comparison()`: two-driver speed trace plus cumulative time delta. |
| 8.3.3 | The obvious implementation, `fastf1.utils.delta_time`, is **deprecated since FastF1 3.0**, documented as "no longer a stable part of the API", and emits a `FutureWarning` (verified). | Delta integrated in-house as `ds / v` over a shared distance grid, with speeds clamped so a stationary car cannot produce an infinite step time. Validated against real lap-time gaps across four driver pairs: errors 0.018–0.272 s, sign always correct. The UI states the approximation rather than implying exactness. |
| 8.3.4 | Replay files gained new keys. | `REPLAY_SCHEMA_VERSION` bumped to 2; older replays load with the missing keys defaulted. |

### 8.4 Tests

| # | Item | Detail |
|---|------|--------|
| 8.4.1 | `tests/test_layout_analysis.py` (19 tests) | Delta integration against analytically known answers (5 km at 200 vs 180 km/h ⇒ exactly 10 s), zero-speed finiteness, grid sampling with shuffled input, session-time conversion for both `Timedelta` and `Timestamp` columns, compound palette overrides, live race-control/track-status parsing. |
| 8.4.2 | App smoke test extended | A second `AppTest` case asserts the previously-discarded data actually reaches the UI — it fails if any panel falls back to its "no data" notice. It caught the stale section list immediately when the new panels landed. |
| 8.4.3 | Totals | **121 unit tests + 8 network tests green**; `ruff` and `black` clean. |

### 8.5 Deliberately not adopted

- **Track dominance / fastest-per-minisector map** (F1Dash's signature view) — genuinely useful, but it needs a mini-sector segmentation scheme this codebase has no basis for yet. Left as a follow-up rather than guessed at.
- **Timing tower** (matteocelani) — only meaningful during a live session, and it overlaps the existing live timing tab.
- **SQL-backed session store** (F1Dash uses SQL Server) — the two-tier cache already covers this app's single-user scope.

---

## 9. Remaining Follow-ups (⬜ Open)

- [ ] Enable `mypy` in CI once type-hint coverage is complete (tools already in `requirements-dev.txt`).
- [ ] LiveF1 historical source (`_load_livef1_session`) still lacks GPS/location and uses index-based distances — wire Position.z replay or LiveF1 silver tables when available.
- [ ] Consider `LapSeries` topic parsing as an additional cross-check for live lap progression.
- [ ] Pre-commit hooks config (`.pre-commit-config.yaml`) for ruff/black locally.
- [ ] `MetricsStore` rewrites `metrics_store.json` on every rerun; batch or debounce if it grows.
- [ ] Track dominance map: colour each mini-sector by the fastest driver through it (F1Dash's
      signature view). Needs a mini-sector segmentation derived from the circuit's distance axis.
- [ ] Speed-coloured track map (official FastF1 example) — X/Y is already available alongside
      Speed in the merged telemetry, so this is a rendering task only.
- [ ] Live timing tower (position, gap, interval, last/best lap) from the `TimingData` feed.

---

## Round 6+ — IMPROVEMENTS.md audit loop

Items from `IMPROVEMENTS.md` (2026-09-16 audit), worked one per commit in the §17 milestone order.

### M0 — Quick correctness wins

- **REPO-01** — Untracked the committed `.venv311/` virtualenv (46 files, incl. Windows `.exe` launchers) from the git index; added `tests/test_repo_hygiene.py` asserting no `venv` paths are tracked and that the ignore rules are in place.
- **HIST-01** — Replay files picked in the sidebar now load: `DataSourceManager._resolve_replay` resolves a bare name against `replay_dir` (confining `../` traversal) and raises a named `FileNotFoundError`; the Season/GP/Session/Scope widgets are hidden for the Replay source. New `tests/test_session_selector.py` drives the selector through an offline `AppTest`.
