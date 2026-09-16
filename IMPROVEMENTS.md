# IMPROVEMENTS.md — F1 Telemetry Dashboard

**Audit, fix plan and research notes, written so an agent loop can work through it one item at a time.**

| | |
|---|---|
| Repository | `mricero/F1-Telemetry-Dashboard` @ `main` / `72d05ee` ("fixed issues with signalr", 2026-09-06) |
| Audit date | 2026-09-16 |
| Audit environment | Linux, Python 3.11.15, streamlit 1.64.0, fastf1 3.8.3, livef1 1.2.7 (PyPI latest: 1.2.11), pandas 2.3.3, numpy 2.4.6, plotly 6.9.0 |
| Baseline gates | `pytest`: **182 passed, 11 skipped** · `ruff check .`: clean · `black --check .`: clean · `mypy`: **21 errors** (not in CI) |
| Network in audit | Blocked (sandbox). Items that can only be proven against the real F1 feed or APIs are tagged **`VERIFY-LIVE`**. Everything else was checked by reading the code, reading the installed library source, or running a script (evidence is quoted inline). |
| Scope | Every source file, test, script, config and doc in the repo, plus `tasks.md` (rounds 1–5), plus research into the three reference sites and ~15 comparable open-source projects. |

---

## Table of contents

0. [How to run this file in an agent loop](#0-how-to-run-this-file-in-an-agent-loop)
1. [Executive summary](#1-executive-summary)
2. [What earlier rounds already fixed (don't redo)](#2-what-earlier-rounds-already-fixed-dont-redo)
3. [P0 — Live pipeline is broken or produces wrong data](#3-p0--live-pipeline)
4. [P0/P1 — Historical & replay sources](#4-p0p1--historical--replay-sources)
5. [P1 — Timing tower / dashboard correctness](#5-p1--timing-tower--dashboard-correctness)
6. [P1/P2 — Caching, state & persistence](#6-p1p2--caching-state--persistence)
7. [P2 — UI/UX & front-end performance](#7-p2--uiux--front-end-performance)
8. [P2 — Code quality, tooling & repo hygiene](#8-p2--code-quality-tooling--repo-hygiene)
9. [P2 — Tests](#9-p2--tests)
10. [P3 — Documentation](#10-p3--documentation)
11. [P3 — New features (parity with reference sites)](#11-p3--new-features-parity-with-reference-sites)
12. [Target architecture](#12-target-architecture)
13. [Research notes — reference sites](#13-research-notes--reference-sites)
14. [Research notes — open-source projects & how they solved the same problems](#14-research-notes--open-source-projects)
15. [Research notes — data sources, protocols, limits & regulations](#15-research-notes--data-sources-protocols-limits--regulations)
16. [Domain cheat-sheet for the agent](#16-domain-cheat-sheet-for-the-agent)
17. [Suggested execution order (milestones)](#17-suggested-execution-order-milestones)
18. [Sources](#18-sources)

---

## 0. How to run this file in an agent loop

### 0.1 Loop protocol (one item per iteration)

1. **Pick** the first unchecked `- [ ]` item in the milestone order of §17 whose `Depends on` items are all checked.
2. **Read** every file listed under `Files`, and the relevant part of `CLAUDE.md` (it documents gotchas that earlier rounds paid for).
3. **Reproduce first.** Write a failing test that captures the `Evidence` / `Acceptance` of the item. Network-dependent tests go in `tests/test_integration_network.py` behind the `network` marker.
4. **Fix** with the smallest change that satisfies `Acceptance`. Do not change the unified session-dict contract (see `CLAUDE.md` → "The unified session dict is the central contract") unless the item says so; if you must, bump `REPLAY_SCHEMA_VERSION`.
5. **Run the gates** (§0.2). All must pass.
6. **Tick** the checkbox here, append `— done in <short-sha>` and add one line to `tasks.md` under a new "Round 6+" section.
7. **Commit** one item per commit: `fix(LIVE-03): divide Position.z arc length by 10`.
8. If an item turns out to be wrong or blocked (e.g. needs a live race weekend), mark it `- [~]`, write why underneath, and move on.

### 0.2 Gates

```bash
# macOS / Linux (CLAUDE.md currently only shows Windows paths — see DOC-03)
python -m pytest -q
python -m ruff check .
python -m black --check .
python -m mypy --ignore-missing-imports app.py data processing ui   # informational until REPO-04 lands

# Optional, needs internet
F1_NETWORK_TESTS=1 python -m pytest -m network -q

# Only meaningful during a live session
python scripts/live_smoke.py 60
```

### 0.3 Item format

```
- [ ] **ID** · Priority · Effort (S ≤1h, M ≤½day, L >½day) · [VERIFY-LIVE]
  - Files:        paths (line numbers are for commit 72d05ee)
  - Problem:      what is wrong and why it matters
  - Evidence:     how it was confirmed
  - Fix:          recommended approach
  - Acceptance:   testable definition of done
  - Depends on:   other IDs (optional)
```

### 0.4 Ground rules learned from this codebase's history

- **Mocks must mirror real upstream shapes.** Round 4 found a production crash hidden by a mock; this audit found the same pattern again in the live fixtures (TEST-01). Prefer recorded real payloads over hand-written dicts.
- **Streamlit re-executes the whole script on every interaction.** Anything expensive or stateful must be in `st.cache_data` / `st.cache_resource` / `st.session_state`, and anything process-wide must be designed for *multiple browser sessions at once*.
- **`st.stop()` is a no-op in bare mode** — always `raise` after it.
- **Never pass live-feed time strings to `pd.to_timedelta`** — use `processing/time_utils.to_seconds`.
- **Coded channels (Gear/DRS/Brake) are never interpolated.**
- **2026 has no DRS** (active aero); a zero DRS channel is correct data.

---

## 1. Executive summary

The historical FastF1 path is in decent shape after five audit rounds. The **live path is the weak point**: it is built on a client that targets an endpoint F1 deprecated in 2025, it treats delta messages as independent records, and several unit/indexing bugs mean that even when data arrives, some of it is wrong. The new timing-tower dashboard (added after `tasks.md` was last updated) has **semantic errors**: it ranks races by fastest lap, marks P11+ as "knocked out" in every session, and misplaces its mini-sector colours on the map.

**Top 15 issues by impact**

| # | ID | One-line summary |
|---|----|------------------|
| 1 | LIVE-01 | LiveF1 `RealF1Client` connects to the legacy `/signalr/` endpoint; F1 moved to `/signalrcore` in May 2025 and FastF1 3.7 says the old endpoints are deprecated. Live mode is likely dead. `VERIFY-LIVE` |
| 2 | LIVE-02 | `CarData.z` / `Position.z` need an F1TV token since the 2025 Dutch GP; the live view early-returns when those are missing, hiding timing, race control and weather too. |
| 3 | LIVE-05 | Delta topics (`TimingData`, `TyreStintSeries`, `DriverList`, …) are buffered as flat rows instead of being deep-merged into state → missing stints, stale fields, wrong laps. |
| 4 | LIVE-04 | `TimingData` sector keys collide: snapshot `Sectors_1_Value` = sector 1, delta `Sectors_1_Value` = sector **2**. Confirmed against livef1's parser. |
| 5 | LIVE-03 | Live GPS distance is 10× too large (Position.z is in 1/10 m; FastF1 path divides by 10, live path doesn't). Confirmed by script. |
| 6 | DASH-01 | Timing tower sorts **every** session by best lap — race classification, gaps and intervals are wrong for races. |
| 7 | DASH-02 | "Outside the top 10 / KO" is applied to races, practice and sprints; 2026 qualifying eliminates 6 in Q1 and 6 in Q2 from 22 cars. |
| 8 | HIST-01 | Replay source is broken from the UI: the selector passes a bare filename, the loader opens it relative to CWD → `FileNotFoundError`. Confirmed. |
| 9 | HIST-03 | "LiveF1 (Historical)" source is broken: uses attributes (`driver_number`, `name_acronym`, …) that livef1's `Driver` does not have, and column names (`Driver`, `nGear`, `LapNumber`) its silver tables don't use. |
| 10 | LIVE-09 / CACHE-01 | One SignalR connection per browser tab; and the "runtime cache" is process-wide but wiped whenever *any* new browser session starts. |
| 11 | LIVE-10 | In live mode the fancy dashboard renders the initial *empty* dict; it never shows live data. |
| 12 | DASH-04 | Dominance colours on the map are sliced by point index, not distance → drawn in the wrong places. |
| 13 | HIST-07 | Jolpica pagination ignored (default limit 30): `get_seasons()` returns 30 of ~77 seasons; laps and pit stops are truncated. |
| 14 | LIVE-11 | `poll_live_data()` re-parses every buffer on every 3 s tick: **1.8 s** measured for three topics at cap, on a fast machine. |
| 15 | HIST-02 | Replays are `pickle` files → loading one from someone else runs arbitrary code. |

---

## 2. What earlier rounds already fixed (don't redo)

`tasks.md` documents five rounds. Summary so the loop doesn't re-investigate:

- **R1** syntax/attribute crashes, tz-naive comparisons, stint KeyError, `load_dotenv`, test fixes, doc de-duplication.
- **R2** livef1 record shapes, raw-DEFLATE decoding, `poll_live_data()`, `@st.fragment(run_every=3)`, runtime cache, metrics store.
- **R3** UI consolidated into `ui/layout.py`, Jolpica per-instance memo, replay schema header, bounded buffers, live lap numbers, GPS distance, CI workflow.
- **R4** the `get_pos_data().add_distance()` crash, bare-mode `st.stop()`, telemetry scope (`fastest`/`session`), nearest-neighbour for coded channels, testing events filtered, `width="stretch"`.
- **R5** `python app.py` relaunch, weather/race-control/position panels, compound colours from FastF1, head-to-head delta (no deprecated `delta_time`).

**Undocumented round (after R5):** `ui/dashboard.py`, `ui/theme.py`, `ui/track_map.py`, `processing/timing.py`, `layout.md` spec, `REPLAY_SCHEMA_VERSION = 3` (`circuit_info`). This work is not in `tasks.md`; several of its open follow-ups in `tasks.md §9` ("Track dominance map", "Live timing tower") are now partly done. See DOC-03.

---
## 3. P0 — Live pipeline

> Context for the whole section: F1 moved live timing from classic ASP.NET SignalR (`/signalr/`) to **SignalR Core** (`/signalrcore`) during the 2025 Monaco GP weekend (FastF1 issue #753). FastF1 3.7.0 (Nov 2025) rewrote its client for the new endpoint with optional F1TV authentication and notes that F1 deprecated the old endpoints. Since the 2025 Dutch GP, car telemetry, positions (driver tracker), pit-stop times and championship tables require a valid F1TV subscription token (undercut-f1 README; F1 Sensor docs). Community sites have been hit by IP blocking (matteocelani/f1-telemetry's hosted instance is down "due to IP blocking by Formula 1"; f1-dash.com announced its sunset citing "increasing IP restrictions" and "data becoming locked behind subscriptions").

- [ ] **LIVE-01** · P0 · L · `VERIFY-LIVE` — **Live client targets the deprecated legacy endpoint**
  - Files: `data/live_adapter.py:169-199` (`start_livef1_client`), `requirements.txt:9`, `scripts/live_smoke.py:30`, docs.
  - Problem: `livef1.adapters.RealF1Client` connects to `BASE_URL + SIGNALR_ENDPOINT` = `https://livetiming.formula1.com/signalr/` using the bundled `signalr_aio` (classic SignalR, `clientProtocol=1.5`). This is true in the installed 1.2.7 **and** in livef1 1.2.10 (checked the wheel) and on livef1 `main`. The repo's docs, `live_adapter.py` docstring and `live_smoke.py` all claim `wss://livetiming.formula1.com/signalrcore`, which is what FastF1 uses — not what this app uses.
  - Evidence: `livef1/utils/constants.py: SIGNALR_ENDPOINT = "/signalr/"`; `livef1/adapters/realtime_client.py` registers hub `Streaming` over `signalr_aio.Connection`. FastF1 `fastf1/livetiming/client.py`: `_connection_url = 'wss://livetiming.formula1.com/signalrcore'`, uses `signalrcore.HubConnectionBuilder` with `access_token_factory`.
  - Fix: Implement an in-repo **SignalR Core ingest client** (don't depend on livef1 for live):
    1. `OPTIONS https://livetiming.formula1.com/signalrcore/negotiate` → read the `AWSALBCORS` cookie and send it back as a `Cookie` header (this is exactly what FastF1 3.7+ does).
    2. `HubConnectionBuilder().with_url("wss://livetiming.formula1.com/signalrcore", options={"headers": headers, "access_token_factory": token_factory_or_None})` with **automatic reconnect** (`with_automatic_reconnect({...})`).
    3. `connection.on("feed", handler)` — feed messages are `[topic, data, timestamp]`.
    4. `connection.send("Subscribe", [topics], on_invocation=handler)` — the completion result is a dict `{topic: initial_state}` = the **snapshot** to seed state (LIVE-05).
    5. Token: read from env `F1TV_SUBSCRIPTION_TOKEN` or a file path in config; **never** call `fastf1.internals.f1auth.get_auth_token()` inside the Streamlit server — it may start a local HTTP auth server and block. Validate expiry (JWT `exp`) and show "token expires in N days" in the UI.
    6. Keep livef1's parsers only if still useful; otherwise decode `.z` topics with the existing `decode_zipped()`.
  - Acceptance: `scripts/live_smoke.py 60` during a session prints non-zero counts for `TimingData`, `TrackStatus`, `RaceControlMessages`, `WeatherData`, `SessionInfo`, `DriverList`, `ExtrapolatedClock`; with a token also `CarData.z`/`Position.z`. Unit tests feed a recorded CompletionMessage + feed messages through the handler.

- [ ] **LIVE-02** · P0 · M — **Degraded mode when auth-only topics are missing**
  - Files: `ui/layout.py:493-495`, `app.py:233-245`, `data/source_manager.py:281-407`.
  - Problem: `render_live_dashboard` returns early with "Waiting for live data…" when `telemetry` and `location` are both empty. Without an F1TV token those are *always* empty, so timing, tyres, race control and weather — all of which work without auth — are never shown.
  - Fix: Render each panel independently from whatever topics have data. Show a single banner: "Car telemetry and driver positions need an F1TV subscription token (set `F1TV_SUBSCRIPTION_TOKEN`)". Maintain an explicit list `AUTH_TOPICS = {"CarData.z", "Position.z", "PitStopSeries", "ChampionshipPrediction", "DriverRaceInfo", "TeamRadio"}` and only subscribe to them when a token is configured.
  - Acceptance: With buffers holding only `TimingData` + `RaceControlMessages` + `WeatherData`, the live fragment renders the timing table, race control and weather, plus the auth banner (AppTest).
  - Depends on: LIVE-01 (the list of gated topics should be re-confirmed live).

- [x] **LIVE-03** · P0 · S — **Live GPS distance is 10× too large** — done in 59a13b7
  - Files: `data/live_adapter.py:438-466` (`distance_at`, line 457), `data/fastf1_adapter.py:12, 185-199`.
  - Problem: `Position.z` X/Y/Z are in **1/10 m** (same feed FastF1 parses; FastF1's own adapter here divides by `POSITION_UNITS_PER_METRE`). `LiveDataProcessor.distance_at` uses the raw arc length, so live "Distance" is decimetres labelled as metres. Every live telemetry x-axis, and anything integrating over it, is off by 10×.
  - Evidence: script — a straight 10 000-unit X track returned `10000.0` "metres" (should be 1000).
  - Fix: Divide by `POSITION_UNITS_PER_METRE` (import the constant; don't duplicate it). Add a test asserting 1000 m for that input.
  - Acceptance: new unit test passes; existing `test_source_manager` distance tests updated to real units.

- [ ] **LIVE-04** · P0 · S — **TimingData sector keys collide between snapshot and delta**
  - Files: `data/source_manager.py:473-476`, `data/live_adapter.py:313-327`.
  - Problem: livef1's `parse_timing_data` flattens a **list** (`"Sectors": [ {...}, {...}, {...} ]`, the snapshot form) as `Sectors_{index+1}_Value`, but a **dict** (`"Sectors": {"1": {...}}`, the delta form, 0-based keys) as `Sectors_1_Value` via its recursive prefix path. So in deltas `Sectors_1_Value` is sector **2**. The app reads `Sectors_{i}_Value` for i=1..3 as S1..S3.
  - Evidence (run against installed livef1):
    ```
    snapshot keys: ['Sectors_1_Value', 'Sectors_2_Value', 'Sectors_3_Value']
    delta {"Sectors": {"1": {"Value": "31.0"}}} keys: ['Sectors_1_Value']   ← this is sector 2
    ```
    Same issue applies to `Segments` (mini-sectors) and `Speeds`.
  - Fix: Parse raw `TimingData` yourself into a merged state (LIVE-05) where `Sectors` is normalised to a 3-element list, indexing by the numeric key of dict deltas.
  - Acceptance: test with a snapshot followed by a delta for sector index `"1"` yields S1 unchanged and S2 updated.
  - Depends on: LIVE-05.

- [ ] **LIVE-05** · P0 · L — **Treat delta topics as state, not as independent records**
  - Files: `data/live_adapter.py` (whole `LiveDataProcessor`), `data/source_manager.py:281-491`.
  - Problem: Most topics are "keyframe + partial update" streams. The subscription completion returns the full state; subsequent `feed` messages carry only changed fields, sometimes with `_deleted` markers. The current design appends each parsed message to a list and later builds DataFrames row-by-row, so:
    - `TyreStintSeries`: the snapshot (per-driver **list** of stints) is silently dropped by livef1 (`if isinstance(stint, dict)` only); deltas like `{"TotalLaps": 12}` have no `Compound` and are dropped by `parse_tyre_stints` (`data/live_adapter.py:348`). `LapStart`/`LapEnd` never exist in the feed (it has `StartLaps`, `TotalLaps`, `New`, `TyresNotChanged`). Result: live stints are mostly empty or wrong.
    - `parse_tyre_stints` sets `DriverAcronym` to the **racing number** (`:352`), so colours and labels don't match the acronym-keyed colour map.
    - `TimingData`: `Position`, `GapToLeader`, `IntervalToPositionAhead.Value`, `InPit`, `PitOut`, `Retired`, `Stopped`, `NumberOfPitStops`, `BestLapTime`, `Speeds.*` arrive in separate partial messages; row-wise frames don't hold "the current value".
    - `DriverList`: first-seen wins (`seen` set), later updates (line position, colours) ignored.
    - Joining mid-session: anything only present in the snapshot is lost.
  - Fix: A `LiveState` object holding one dict per topic, updated by a **recursive merge** (the approach f1-dash, undercut-f1, matteocelani/f1-telemetry all use):
    ```python
    def deep_merge(base: dict, update: dict) -> dict:
        for key, value in update.items():
            if key == "_deleted":
                for k in value:
                    base.pop(str(k), None)
            elif isinstance(value, dict) and isinstance(base.get(key), (dict, list)):
                target = base[key]
                if isinstance(target, list):           # delta addresses list items by index
                    target = {str(i): v for i, v in enumerate(target)}
                base[key] = deep_merge(target, value)
            else:
                base[key] = value
        return base
    ```
    Normalise index-keyed dicts back to ordered lists when reading (`Sectors`, `Segments`, `Stints`). Keep **append-only history** only for true time series: `CarData.z`, `Position.z`, `RaceControlMessages`, `TeamRadio`, `LapCount`, lap completions derived from `TimingData.NumberOfLaps`, and `WeatherData` samples.
  - Acceptance: replaying a recorded session (TEST-01) produces, at the end, the same classification / stints / best laps as the FastF1 historical load of the same session (tolerances documented in the test).

- [x] **LIVE-06** · P0 · S — **SessionInfo parsed wrongly → header shows "Race" instead of the GP name** — done in b368085
  - Files: `data/source_manager.py:479-491`.
  - Problem: `SessionInfo.Meeting` is a **nested dict** (`{"Name": "Italian Grand Prix", "Location": "Monza", "Country": {...}, "Circuit": {...}}`), and `Name` is the *session* name. The loop assigns `str(Meeting)` to `gp` and then overwrites it with `Name`.
  - Evidence: script with `{"Meeting": {"Name": "Italian Grand Prix"}, "Name": "Race", "Type": "Race"}` → `{'gp': 'Race', 'session_type': 'Race', ...}`. The existing test fixture uses `{"Meeting": "Bahrain"}` (a string), which is not the real shape (TEST-01).
  - Fix: `gp = si["Meeting"]["Name"]`, `session_name = si["Name"]`, `session_type = si["Type"]`, `circuit_key = si["Meeting"]["Circuit"]["Key"]`, `year` from `StartDate`, `gmt_offset = si["GmtOffset"]`.
  - Acceptance: unit test with the real nested shape.

- [ ] **LIVE-07** · P0 · S — **Buffers are not thread-safe**
  - Files: `data/live_adapter.py:157-167, 237-251`.
  - Problem: The client thread `extend`s and `del buf[:overflow]` on lists that the Streamlit script thread is iterating (`get_buffered_data` returns the *same* list object). Python won't raise for list mutation during iteration, but slices shift under the reader → skipped/duplicated rows, and `defaultdict` insertion during `.get` on another thread is a data race.
  - Fix: `threading.Lock` around writes; readers get `list(buf)` copies or, better, an immutable snapshot object published atomically after each merge (§12). Use `collections.deque(maxlen=...)` for bounded series.
  - Acceptance: a stress test with a writer thread appending 100k records while a reader polls 1000 times never sees non-monotonic timestamps within a driver.

- [ ] **LIVE-08** · P0 · M — **"Stop Live" doesn't stop; no reconnect, no heartbeat, no staleness detection**
  - Files: `data/live_adapter.py:210-231, 253-266`, `ui/layout.py:851-853`.
  - Problem: `RealF1Client` has no `stop()` (checked source), so `SignalRLiveAdapter.stop()` does nothing to the socket; `_forever_check` loops forever; the daemon thread keeps the connection open until the process exits. `is_running()` stays `True` if the socket silently dies. There is no reconnect/backoff and no "last message N s ago" indicator.
  - Fix: With the new client (LIVE-01): `connection.stop()`, `on_close` → state `DISCONNECTED`, automatic reconnect with exponential backoff capped at 60 s, a supervisor that marks the feed `STALE` after 30 s without `Heartbeat`, and a status chip in the UI (`CONNECTING / LIVE / STALE / RECONNECTING / STOPPED / AUTH_REQUIRED / BLOCKED(403)`).
  - Acceptance: unit test with a fake connection: stop → thread joins within 5 s; simulated close → reconnect attempted with backoff; 403 on negotiate → `BLOCKED` state surfaced, no tight retry loop.

- [ ] **LIVE-09** · P0 · M — **One SignalR connection per browser session**
  - Files: `app.py:156-157`, `data/source_manager.py:20-27`.
  - Problem: `DataSourceManager()` (which creates its own `SignalRLiveAdapter`) lives in `st.session_state`, i.e. **per browser tab**. Five viewers = five upstream connections, five copies of the buffers, and a much higher chance of F1 rate-limiting or IP-blocking the host.
  - Fix: Create the live ingest service once per process with `@st.cache_resource` (or a module singleton guarded by a lock). Browser sessions only *read* snapshots. Start/stop is a process-level action (admin-only if deployed).
  - Acceptance: two `AppTest` sessions share one adapter instance (assert `id()` equality); starting live in one shows data in the other.

- [ ] **LIVE-10** · P0 · M — **Live mode never feeds the timing-tower dashboard**
  - Files: `app.py:173-202, 233-245`, `ui/layout.py:481-530`, `ui/dashboard.py:343-355`.
  - Problem: `render_dashboard(session_data)` runs with the dict from `_load_live_session()` — all empty frames — so the header, tower, sector cards and map show "No timing data" during a live session. Live data only reaches the plain tabs inside `render_live_dashboard`.
  - Fix: Move `render_dashboard` inside the live fragment and feed it `poll_live_data()` (or the LiveState snapshot). Remove the duplicate plain "Timing" dataframe tab.
  - Acceptance: AppTest with primed buffers: header shows the GP from SessionInfo and the tower has one row per driver.
  - Depends on: LIVE-05, DASH-01.

- [ ] **LIVE-11** · P1 · M — **Poll is O(buffer) Python work every 3 seconds; buffer cap loses race history**
  - Files: `data/source_manager.py:281-407, 410-476`, `data/live_adapter.py:277-310`.
  - Problem: Every tick re-parses *all* buffered records: per-record `pd.to_numeric` scalar calls, `iterrows()` over all `TimingData` rows with a `pd.Series` constructed per row, `groupby` over full frames, ISO timestamp parsing of 20k strings.
  - Evidence: synthetic buffers at cap (20 000 each for `CarData.z`, `Position.z`, `TimingData`): **`poll_live_data()` took 1.80 s**. With all topics and a slower host this exceeds the 3 s `run_every`, so fragments pile up.
  - Also: `buffer_limit=20000` drops the **oldest** `TimingData` records; lap completions from the first part of a race vanish from the lap chart.
  - Fix: Incremental processing — handlers update `LiveState` and append to per-driver ring buffers of numpy arrays; derived tables (laps, stints) are maintained incrementally on lap completion; the snapshot is rebuilt only when `state.version` changed. Lap history is stored separately from the bounded telemetry buffer.
  - Acceptance: perf test: building a snapshot from a state with 22 drivers × 10 min of 4 Hz telemetry + 60 laps < **150 ms** on CI; lap 1 is still present after 2 h of simulated feed.
  - Depends on: LIVE-05.

- [ ] **LIVE-12** · P1 · S — **Unreachable/placeholder live controls; saving a live session saves nothing**
  - Files: `app.py:233-245` (returns), `app.py:287-288` (unreachable `render_live_controls`), `ui/layout.py:827-853`.
  - Problem: For live sessions `main()` returns at line 245, so `render_live_controls` (buffer counts, "Save Raw Stream", "Stop Live") never renders. "Save Raw Stream" only prints advice. For non-live sessions the "💾 Save Session for Replay" button saves the loaded dict; for live it would save the empty initial dict.
  - Fix: Render controls inside the live branch. Implement recording as **append-only JSONL** of raw messages (`subscribe.json` for the snapshot + `live.jsonl` for `[topic, data, timestamp]`), the format undercut-f1 uses; replay feeds the same handler (LIVE-05) with a virtual clock.
  - Acceptance: record 60 s from a replayed fixture → replay reproduces identical final state.

- [ ] **LIVE-13** · P1 · M — **Live telemetry comparisons use a meaningless x-axis**
  - Files: `data/source_manager.py:335-370`, `processing/timing.py:112-144`, `ui/layout.py:691-819`.
  - Problem: Live `Distance` is cumulative since the stream started (and per-driver tails of 2000 samples start at different points), so overlaying drivers, the head-to-head delta, micro-sectors and dominance are not comparable. Historical mode solved this with `telemetry_scope="fastest"`; live has no lap segmentation. Live telemetry frames also have `timestamp` instead of `Time`, so `micro_sector_times` returns `None`.
  - Fix: Segment live telemetry into laps using lap-completion events (`TimingData.NumberOfLaps` changes, with timestamps) and reset distance per lap; expose "current lap" and "last completed lap" per driver; compute comparisons only on completed laps.
  - Acceptance: fixture replay: each completed lap trace starts near 0 m and ends within ±3 % of the circuit length.

- [ ] **LIVE-14** · P2 · S — **Off-track / garage GPS samples pollute trails**
  - Files: `data/live_adapter.py:297-310`, `data/source_manager.py:317-333`.
  - Problem: Position entries carry `Status` (`OnTrack` / `OffTrack`) and cars in the garage report `0,0,0`. They're kept, producing spikes in distance and straight lines to the origin on the map outline.
  - Fix: Drop `Status != "OnTrack"` and exact-zero triples before building trails/distances.
  - Acceptance: unit test with interleaved OffTrack zeros → distance monotonic and no (0,0) in trail.

- [ ] **LIVE-15** · P1 · M — **"Live session detected" is a date heuristic**
  - Files: `data/jolpica_adapter.py:208-224`, `ui/layout.py:85-117`, `data/source_manager.py:65-68`, `data/live_adapter.py:469-477`, `readme.md` ("real-time endpoint probing").
  - Problem: `is_race_weekend()` returns True if **race day** (midnight UTC, no time) is within ±72 h. That is Thursday 00:00 → Wednesday 00:00, including days with no sessions; the UI then shows "🔴 LIVE SESSION DETECTED", and `Auto` hides the historical selectors for the whole window. It also only checks the current calendar year (a race on 1 Jan+ would be missed) and ignores session times entirely. The readme calls this "real-time endpoint probing"; it isn't.
  - Fix: Use the FastF1 event schedule (`Session1DateUtc … Session5DateUtc`) or `https://livetiming.formula1.com/static/{year}/Index.json` to find a session whose window `[start − 15 min, start + duration + 30 min]` contains now; confirm with `SessionStatus` once connected. In `Auto`, show a "Go live" call-to-action rather than silently switching and hiding history.
  - Acceptance: unit tests with a frozen clock at FP1 start −10 min → live; Tuesday after the race → historical; `Auto` on a race weekend still shows historical selectors.

- [ ] **LIVE-16** · P2 · S — **Dead/misleading live code**
  - Files: `data/live_adapter.py:201-208` (`start_fastf1_client` ignores `topics`, blocks, unused), `data/live_adapter.py:469-477` (`check_live_session_available`, unused), `processing/telemetry_processor.py:267-294` (`process_live_telemetry`, unused, uses `index*100` pseudo-distance), `data/live_adapter.py:122-136` (`LapSeries`, `CurrentTyres`, `PitLaneTimeCollection` subscribed but never parsed).
  - Fix: delete, or wire up with tests. `CurrentTyres` + `TimingAppData` are exactly what the tower needs for live tyre compound/age (see DASH-11); `PitLaneTimeCollection` gives pit lane durations.
  - Acceptance: `vulture`/grep shows no unused public functions in `data/` and `processing/`.

---
## 4. P0/P1 — Historical & replay sources

- [x] **HIST-01** · P0 · S — **Replay source can't load files picked in the UI** — done in beac86a
  - Files: `ui/layout.py:156-165`, `data/source_manager.py:569-599`.
  - Problem: `get_available_replays()` returns `f.name` (bare filenames); `get_session_data(source="replay", replay_file=name)` → `_load_replay(name)` → `open(name)` relative to the process CWD, not `replay_dir`. Tests pass because they call `_load_replay` with a full path.
  - Evidence: script → `FileNotFoundError: [Errno 2] No such file or directory: 'x_20260916_113438.pkl'`.
  - Fix: Resolve inside the manager: `path = self.replay_dir / Path(replay_file).name` (also prevents path traversal), verify it exists. Also hide the Season/GP/Session/Scope widgets when source is Replay (they're irrelevant but currently shown, `ui/layout.py:122`).
  - Acceptance: AppTest selects "Replay (Saved)" with one saved file → dashboard renders without error.

- [ ] **HIST-02** · P1 · M — **Replays use `pickle` (arbitrary code execution on load)**
  - Files: `data/source_manager.py:532-599`.
  - Problem: `pickle.load` on a file from anywhere executes code. Replays are the one artefact users are likely to share.
  - Fix: A directory or zip per replay: `meta.json` (schema, app version, session_info, compound_colors, circuit_info rotation) + Parquet (or Arrow IPC) for each table and one Parquet per driver for telemetry/location (add `pyarrow` back to requirements, now with a real use). Keep a *read-only* legacy pickle loader behind an explicit "I trust this file" confirmation, and delete it in a later release. Live recordings use JSONL (LIVE-12).
  - Acceptance: round-trip test for all keys incl. Timedelta/NaT/Int64/category dtypes; loading a legacy `.pkl` requires `allow_pickle=True`.

- [ ] **HIST-03** · P0 · M — **"LiveF1 (Historical)" source is broken**
  - Files: `data/source_manager.py:176-261`.
  - Problem (checked against livef1 1.2.7 source):
    - `session.drivers` values are `livef1.models.driver.Driver` with attributes `RacingNumber`, `Tla`, `TeamColour`, `TeamName`, `FirstName`, `LastName`, `FullName`, `HeadshotUrl` — **not** `driver_number`, `name_acronym`, `team_colour`, `team_name`, `first_name`, `last_name`. Line 209 raises `AttributeError` on the first driver.
    - Silver car telemetry columns are `DriverNo, Utc, timestamp, LapNo, Position, RPM, Speed, GearNo, Throttle, Brake, DRS, X, Y, Z, TrackRegion, Compound, TyreAge, Distance, CarStatus, TrackStatus` — there is no `Driver` column (line 219 guard is always false → empty telemetry) and gear is `GearNo`, not `nGear`.
    - The comment "LiveF1 exposes no GPS channel" is wrong — silver telemetry has `X/Y/Z` and `Distance`, so the map could work.
    - Silver laps use `DriverNo`, `LapNo`, `Sector1_Time…`, `Speed_I1/I2/FL/ST`, `PitIn/PitOut`, `Compound`, `TyreAge`, `GapToLeader`, `IntervalToPositionAhead` — none map to the unified `Driver/LapNumber/Sector1Time/SpeedI1…` schema, so the tower and lap charts get nothing.
    - `session_map` lacks "Sprint Shootout" (2023) naming differences; `circuit_short` mapping is a hand-maintained dict that misses Madrid (2026, "Madring"), Las Vegas variants etc.
  - Fix: Either (a) remove the source from the selector until fixed, or (b) write a proper adapter with explicit column maps, lap-relative distance for the fastest lap (filter `LapNo == fastest`), and resolve the meeting via `meeting_key/session_key` from `Index.json`/FastF1 schedule instead of name heuristics. Mark as experimental in the UI.
  - Acceptance: network test (`F1_NETWORK_TESTS=1`) loads 2025 Bahrain Q via livef1 and gets ≥18 drivers with non-empty telemetry, location and laps; offline unit test with a captured silver-table sample.

- [x] **HIST-04** · P1 · S — **Auto fallback is stuck in 2025** — done in 5ae2645
  - Files: `data/fastf1_adapter.py:27-36`, `data/source_manager.py:493-499`.
  - Problem: `get_available_sessions(years=None)` defaults to `[2023, 2024, 2025]`; `_get_most_recent_completed_race()` calls it without years, so in September 2026 "most recent completed race" is the 2025 finale. The hard fallback is "2024 Abu Dhabi". `config.default_year` (`DEFAULT_YEAR`) exists but is never read.
  - Fix: `years = [now.year, now.year - 1]`; pick the latest event whose **race session end** is in the past (use `Session5DateUtc`). Use config defaults only when the schedule is unavailable.
  - Acceptance: frozen-clock test on 2026-09-16 returns the most recent 2026 round.

- [x] **HIST-05** · P1 · S — **Session list ignores the weekend format** — done in f94f667
  - Files: `ui/layout.py:136-137`.
  - Problem: Static `["FP1","FP2","FP3","Q","S","R"]`: sprint weekends have no FP2/FP3 but do have **Sprint Qualifying ("SQ")**, which is missing entirely; normal weekends have no Sprint. Picking a non-existent session fails at load time with a FastF1 error.
  - Fix: Build the list from the event's `Session1..Session5` names (FastF1 `get_event_schedule`), mapping names → identifiers (`Practice 1`→`FP1`, `Sprint Qualifying`/`Sprint Shootout`→`SQ`, `Sprint`→`S`, `Qualifying`→`Q`, `Race`→`R`), and only include sessions that have started.
  - Acceptance: 2025 Miami (sprint) offers FP1, SQ, S, Q, R; 2025 Monaco offers FP1–3, Q, R.

- [ ] **HIST-06** · P2 · S — **Only three seasons selectable**
  - Files: `ui/layout.py:124-125`.
  - Problem: Season list is `[this_year, this_year-1, this_year-2]`; FastF1 has timing + telemetry from 2018.
  - Fix: `range(now.year, 2017, -1)`; warn that pre-2018 has no telemetry if ever extended via Jolpica.

- [x] **HIST-07** · P1 · S — **Jolpica pagination and rate limits ignored** — done in bcfc5d3
  - Files: `data/jolpica_adapter.py:45-132`.
  - Problem: Jolpica defaults to `limit=30`, max `100`, with `MRData.total`/`offset` for paging. `get_seasons()` therefore returns only the first 30 seasons (1950–1979); `get_lap_times()` returns 30 timing rows of a ~1 200-row race; `get_pit_stops()` truncates races with >30 stops. Limits are **4 req/s burst, 500 req/h** unauthenticated ("will decrease in the future"); there is no retry/backoff on HTTP 429 and failures raise `ConnectionError` that callers swallow.
  - Evidence: Jolpica docs (§18). Live verification blocked in the audit sandbox → the network test should assert `len(get_seasons()) == int(MRData.total)`.
  - Fix: `_fetch_all(endpoint)` that pages with `limit=100&offset=…` until `offset+limit >= total`; a token-bucket limiter (4/s) and `Retry-After`-aware backoff; persistent HTTP cache (`requests-cache`, which FastF1 already depends on) with long TTL for past seasons.
  - Acceptance: mocked paging test (total=250 → 3 requests, 250 rows); 429 test respects `Retry-After`.
  - Note: pagination, the 4 req/s token bucket and `Retry-After` backoff landed; the persistent `requests-cache` layer is deferred to REPO-02, which owns dependency changes.

- [ ] **HIST-08** · P2 · S — **FastF1 first load is slow and blocks the UI; drivers loaded serially**
  - Files: `data/source_manager.py:93-130`, `data/fastf1_adapter.py:65-70, 108-128`.
  - Problem: A race load calls `get_telemetry()` for ~20 drivers sequentially (each merges car+position data and interpolates) behind a single spinner; on a cold cache this takes minutes. Changing *only* the telemetry scope reloads the whole session because the runtime cache key includes `telemetry_scope`.
  - Fix: Cache the loaded `fastf1.core.Session` with `@st.cache_resource(max_entries=3)` keyed by `(year, gp, session)`; derive scope-specific frames from it (cheap); show `st.progress` per driver; compute telemetry lazily for the drivers actually displayed (default: top 10 + favourites); optionally `concurrent.futures.ThreadPoolExecutor` for per-driver merges (numpy releases the GIL for much of the work — measure first).
  - Acceptance: switching scope on a warm session < 2 s; progress bar visible on cold load.

---

## 5. P1 — Timing tower / dashboard correctness

The dashboard (`layout.md` spec → `processing/timing.py`, `ui/dashboard.py`, `ui/track_map.py`) is visually close to the reference sites but several numbers mean something different from what the labels say.

- [ ] **DASH-01** · P1 · M — **Every session is classified by best lap**
  - Files: `processing/timing.py:213-316` (`timed.sort` at 291; gap/interval at 294-306), `tests/test_integration_network.py:167-180` (asserts this behaviour).
  - Problem: For races and sprints, position is the running/finishing order, `Gap` is time behind the leader on the road and `Interval` is time to the car ahead. The tower instead sorts by personal best lap and computes gaps as lap-time differences — a race winner who didn't set the fastest lap shows in P3, a lapped car can be P1.
  - Fix: Classification by session type:
    - **Race / Sprint (historical):** order by `session.results.Position` (fallback: last lap's `Position`); gap = leader's cumulative `Time` at the driver's last completed lap vs the driver's, or "+N LAP(S)"; interval = same vs car ahead; status from `results.Status` (DNF/DSQ/+1 Lap).
    - **Race / Sprint (live):** `TimingData.Lines[n].Line` / `Position`, `GapToLeader`, `IntervalToPositionAhead.Value`, `Retired`, `Stopped`, `InPit`.
    - **Qualifying / SQ:** order by segment reached, then best time in that segment (see DASH-02).
    - **Practice:** best lap (current behaviour).
  - Acceptance: 2023 Bahrain **R** tower P1 = VER, P2 = PER, P3 = ALO (actual result); gaps match `results.Time` within 0.1 s. Update the network test that currently asserts lap-time ordering for all sessions.

- [ ] **DASH-02** · P1 · M — **Knock-out partition is hard-coded "top 10" for all sessions**
  - Files: `processing/timing.py:213, 308`, `ui/dashboard.py:195-247, 343`.
  - Problem: `cutoff=10` marks P11+ as `KO`, dims them and prints "Outside the top 10" in races and practice. In qualifying, the real rule is by segment: with **22 cars (2026)** Q1 eliminates 6 and Q2 eliminates 6 (10 in Q3); with 20 cars (2018–2025) 5 and 5. The row's best time should be from the segment they were eliminated in, not their session best.
  - Fix: Only for `Q`/`SQ`: use `session.results` `Q1/Q2/Q3` columns (or `laps.split_qualifying_sessions()`) to assign segment; partitions `Q3 / Eliminated in Q2 / Eliminated in Q1` with headings; cut-offs derived from car count. Live: `TimingData.SessionPart` plus the per-line `KnockedOut` and cut-off flags (confirm exact key names on a recording).
  - Acceptance: 2026 quali fixture → 10/6/6 split with correct headings; race → no KO styling.

- [ ] **DASH-03** · P1 · M — **Mini-sector colours don't follow the F1 convention; sector strips assume equal thirds**
  - Files: `processing/timing.py:112-174, 252-268`, `ui/theme.py:97-104`.
  - Problem:
    1. Official convention (and `layout.md` §3.8, formula-timer, f1telemetry.com): **purple = session best, green = personal best, yellow = slower than personal best** (f1telemetry.com adds blue = in pit). The code makes green "within 2 % of the session best" (`GREEN_TOLERANCE`), which has nothing to do with personal bests.
    2. Only one lap per driver is analysed (fastest-lap telemetry), so "personal best micro-sector" can't be computed at all; you need all laps.
    3. The 15 slices are equal-distance fifths of equal thirds of the lap, but the real sector boundaries are not at 1/3 and 2/3 of the lap; the strip under "Sector 1" is not sector 1.
  - Fix: Derive sector boundary distances per circuit from FastF1: for the fastest lap, find the distance at `LapStartTime + Sector1Time` and `+ Sector1Time + Sector2Time` via telemetry `SessionTime`; split each real sector into N equal-distance mini-sectors; compute mini-sector times for all valid laps (or at least each driver's top-3 laps); colour purple/green/yellow/grey per the convention. Live: use `TimingData.Sectors[i].Segments[j].Status` codes directly (these are the official mini-sector colours; 2048 = yellow, 2049 = green, 2051 = purple, 2064 = pit lane — confirm on a recording).
  - Acceptance: unit test on synthetic data where driver A's personal-best mini-sector is slower than B's → A green, B purple, A's other slices yellow.

- [ ] **DASH-04** · P1 · S — **Dominance map colours are drawn in the wrong places**
  - Files: `ui/track_map.py:138-154` (`bounds = np.linspace(0, len(projected), …)` at 141), `processing/timing.py:112-144`.
  - Problem: `micro_sector_times` splits the lap into equal **distance** slices, but `build_track_svg` splits the projected polyline into equal **point-count** slices. Telemetry is sampled in time (~4 Hz car / interpolated), so points are much denser in slow corners — slice *k* on the map covers a different stretch of track than slice *k* in the data.
  - Fix: Keep the reference trace's `Distance` column through projection and split at `np.searchsorted(distance, np.linspace(d0, d1, n+1))`. Share one `segment_boundaries(distance, n)` helper between timing and map (and DASH-03's real sector boundaries).
  - Acceptance: synthetic trace with 90 % of points in the first 10 % of distance → first slice covers ~10 % of the path length, not 90 %.

- [ ] **DASH-05** · P1 · S — **"Full session" scope breaks the dashboard**
  - Files: `ui/dashboard.py:301-340`, `processing/timing.py:229-234`, `ui/track_map.py:42-51`.
  - Problem: With `telemetry_scope="session"`, `micro_sector_times` slices the *whole race* (~300 km) into 15 pieces, the dominance map is meaningless, and `_reference_trace` picks the longest trace (all laps) → an SVG path with tens of thousands of `L` commands, drawn three times (casing, ribbon, dominance). Page weight and render time spike.
  - Fix: The dashboard always uses per-driver fastest-lap frames (compute them independently of the chart scope); decimate the outline to ≤1 500 points (Ramer–Douglas–Peucker or uniform distance resampling).
  - Acceptance: session scope on a race renders the dashboard with the same mini-sector/dominance output as fastest scope; SVG size < 150 KB.

- [ ] **DASH-06** · P2 · S — **"Theoretical best" isn't**
  - Files: `processing/timing.py:198-210, 252-268, 310-315`, `ui/dashboard.py:329-339`.
  - Problem: Sector times per row come from the driver's **fastest lap**; `theoretical_best` sums the minimum of those. The true ideal lap is the sum of each sector's best across **all** laps (and per driver, the driver's own best sectors). "Diff" is therefore misleading.
  - Fix: Compute `best_s1/s2/s3` per driver over all valid laps (exclude deleted laps: FastF1 `Deleted` column / `IsAccurate`); session ideal = min over drivers; show both "personal ideal" and "session ideal".
  - Acceptance: synthetic laps where a driver's best S1 is on a slower lap → ideal uses it.

- [x] **DASH-07** · P1 · S — **Wind speed unit is wrong in the header** — done in c0f7d4a
  - Files: `ui/dashboard.py:135-140`, `ui/layout.py:591`.
  - Problem: FastF1 `WindSpeed` is **m/s**. The weather tab labels it m/s; the header prints the same number as "km/h" (3.6× understatement). `layout.md` asks for km/h.
  - Fix: Convert `× 3.6` in the header (and in the tab if you standardise on km/h); unit test.

- [ ] **DASH-08** · P2 · S — **Finished sessions can show "YELLOW FLAG"**
  - Files: `ui/dashboard.py:64-88`.
  - Problem: Historical header flag = last race-control message's `Flag`. That is often a sector-scoped `YELLOW`/`DOUBLE YELLOW`/`CLEAR` or a driver-scoped `BLUE`, not the track state.
  - Fix: For historical sessions, use `session.track_status` (FastF1) final value or show `CHEQUERED`/"SESSION ENDED"; only consider messages with `Scope == "Track"`. Live: `TrackStatus` only.

- [ ] **DASH-09** · P2 · S — **Header "clock" is the weather-sampling window length**
  - Files: `ui/dashboard.py:91-103, 149`.
  - Problem: Shown in the clock slot as `MM:SS`, it's actually the time span of weather samples (often >60 min, so the minutes overflow the format's intent).
  - Fix: Historical: session duration from `session.session_start_time`/last lap, formatted `H:MM:SS`, labelled "Duration". Live: `ExtrapolatedClock` (`Remaining`, `Extrapolating`, `Utc`) counting down client-side; laps `LapCount.CurrentLap/TotalLaps` for races.

- [ ] **DASH-10** · P2 · S — **Driver status badges are not informative**
  - Files: `processing/timing.py:93-109, 271-285`.
  - Problem: Every historical row is `CLASSIFIED` (DNFs, DSQs, DNS included); live `IN PIT` uses FastF1-only `PitInTime/PitOutTime` columns that live laps don't have; `laps_completed` counts the synthetic in-progress live lap row.
  - Fix: Historical from `results.Status` (`Finished`, `+1 Lap`, `Retired`, `Disqualified`, …) → badges `FIN / +1L / DNF / DSQ / DNS`; live from `TimingData` `InPit`, `PitOut`, `Retired`, `Stopped` (latch retirement, as matteocelani/f1-telemetry does, because the feed is lossy).

- [ ] **DASH-11** · P2 · S — **Tyre history shows lap counts, not tyre age**
  - Files: `processing/timing.py:69-80`.
  - Problem: `laps_used = len(stint rows)` ignores tyres that started used (FastF1 `TyreLife`, `FreshTyre`); live has no compound data in laps at all.
  - Fix: Use `TyreLife` max per stint and a "used" marker; live from `TimingAppData.Lines[n].Stints` (`Compound`, `New`, `TotalLaps`, `StartLaps`) — the same source f1-dash/undercut-f1 use.

- [ ] **DASH-12** · P3 · S — **Spec gaps vs `layout.md`**
  - Files: `ui/dashboard.py`, `layout.md`.
  - Problem: Not implemented: last-lap purple highlight when it is the session best (§3.4), country flag in header (§2), position-swap animation (§3), `0 km/h` speed when in pit (§3.13), compass arrow for wind (§2). The spec also says speed trap is "current speed" whereas the code shows the best speed-trap reading.
  - Fix: Decide which spec items still matter; implement or strike them from `layout.md`.

---
## 6. P1/P2 — Caching, state & persistence

- [x] **CACHE-01** · P1 · S — **"Runtime cache" is shared by all users but reset by any new browser session; unbounded** — done in 1c69594
  - Files: `data/runtime_cache.py:77-79`, `app.py:148-153`, `app.py:90-120`.
  - Problem: `runtime_cache` is a module-level singleton (process-wide), but `begin_session()` is guarded by `st.session_state`, which is **per browser session**. Opening the app in a second tab (or a second user connecting) clears the cache for everyone. It also has no size limit: every session viewed stays in memory (a race dict with "Full session" scope is hundreds of MB).
  - Fix: Replace with `@st.cache_resource(max_entries=N, ttl=…)` on the loader (or an LRU with a byte budget using `DataFrame.memory_usage(deep=True)`); drop the "wipe on app open" semantics — Streamlit already starts clean per process. Keep hit/miss stats if useful.
  - Acceptance: two AppTest sessions; the second doesn't evict the first's entry; memory bound test with fake 50 MB frames.
  - Note: kept the in-repo `RuntimeCache` (now LRU + byte budget) rather than moving to `@st.cache_resource`, so the hit/miss stats and the explicit clear survive; `F1_CACHE_MAX_ENTRIES` / `F1_CACHE_MAX_BYTES` tune it.

- [ ] **CACHE-02** · P2 · M — **Metrics store: wrong comparisons, rewrites every rerun, not concurrency-safe**
  - Files: `processing/metrics_store.py`, `app.py:204-231`.
  - Problem:
    - `update_laps()` calls `save()` (full JSON rewrite) on **every rerun**, i.e. every widget interaction (already listed in `tasks.md §9`).
    - "All-time" records compare lap and sector times **across different circuits** (a Monza lap "beats" a Monaco lap) — meaningless.
    - Top speed is taken from the processed telemetry, which in the default "fastest lap" scope only covers each driver's fastest lap (not the session's top speed); the label doesn't include scope, so the two scopes write into the same record.
    - Live sessions are all labelled "Live Session" (LIVE-06), merging different events.
    - Two server processes / threads writing the same JSON file can interleave.
  - Fix: Key records by `(year, round, session_type)`; "all-time" only **per circuit** (circuit key from FastF1/SessionInfo); compute top speed from `laps.SpeedST`/`SpeedFL` max or full-session car data; only write when a record actually changes; store in SQLite (`sqlite3`, WAL mode) instead of JSON.
  - Acceptance: rerun loop of 100 iterations performs 0 writes when nothing changed; all-time view grouped by circuit.

- [ ] **CACHE-03** · P2 · S — **Current weekend's sessions are hidden until race day**
  - Files: `data/fastf1_adapter.py:55-63`, `ui/layout.py:90-95`.
  - Problem: Events are filtered by `EventDate < now` (race date). On Saturday, FP1–3 and Qualifying of that weekend aren't selectable historically. Cached for 60 min.
  - Fix: Include an event if its first session has ended; filter sessions (HIST-05) by their own end time; shorter TTL on race weekends.

- [ ] **CACHE-04** · P3 · S — **FastF1 cache directory hygiene**
  - Files: `data/fastf1_adapter.py:21-25`, `config.py`.
  - Problem: Default `./ff1_cache` inside the repo; grows by several GB over seasons; `FASTF1_CACHE_DIR` is honoured but not documented in a `.env.example` (none exists).
  - Fix: Default to `platformdirs.user_cache_dir("f1-telemetry-dashboard")`; add `.env.example`; show cache size + "clear cache" button in a settings panel.

---

## 7. P2 — UI/UX & front-end performance

- [ ] **UX-01** · P2 · S — **All tabs are computed and shipped on every rerun**
  - Files: `app.py:247-285`, `ui/layout.py:246-269, 509-530`.
  - Problem: `st.tabs` renders every tab's content eagerly by default (Streamlit docs). The analysis area has 8 tabs, and the Telemetry tab has 6 sub-tabs each with a 20-driver Plotly figure — all rebuilt and sent to the browser on every widget click, and every 3 s in live mode.
  - Fix: Streamlit ≥1.55 supports `st.tabs(..., on_change="rerun")` with each tab's `.open` flag for lazy execution; alternatively `st.segmented_control` for the channel picker. Or plot the six channels as one figure with shared x-axis subplots (one render instead of six — also the most useful layout for telemetry reading).
  - Acceptance: instrument render time; switching driver selection re-renders only the visible tab.
  - Depends on: REPO-02 (raise Streamlit floor).

- [ ] **UX-02** · P2 · M — **Plotly performance & live chart behaviour**
  - Files: `ui/layout.py:182-243, 272-331, 422-478, 691-774`.
  - Problem: `go.Scatter` (SVG) with ~20 traces × thousands of points × 6 charts; `hovermode="x unified"` with 20 series produces huge tooltips; live fragment rebuilds figures every 3 s, resetting zoom/legend selections.
  - Fix: `go.Scattergl` for telemetry/lap traces; `uirevision=<session key>` in layout so zoom and legend toggles survive live updates; decimate to the 5 m grid already computed; default to a **driver filter** (UX-03) instead of all drivers; `hovermode="x"` with compact template.
  - Acceptance: live fragment update keeps a zoomed range (manual check + AppTest asserting `uirevision` set).

- [ ] **UX-03** · P2 · S — **No driver selection / favourites**
  - Files: `ui/layout.py`, `app.py`.
  - Problem: Every chart plots every driver. All reference products let you pick/pin drivers (f1-dash favourites, undercut-f1 driver selection, formula-timer driver comparison).
  - Fix: A global `st.multiselect("Drivers", default=top-5 by classification)` plus "favourites" stored in `st.query_params` (shareable URL) or `localStorage`-backed component; highlight favourites in the tower.

- [ ] **UX-04** · P2 · M — **Head-to-head is speed-only and fastest-lap-only**
  - Files: `ui/layout.py:691-819`.
  - Fix: Stacked subplots (Speed, Throttle, Brake, Gear, Δt) sharing x; corner number markers from `circuit_info.corners` (`Distance` column) as vertical lines; lap pickers per driver (fastest, specific lap, best of stint); mini track map highlighting where Δt changes sign. Keep the honest "approximate" caption.

- [ ] **UX-05** · P2 · S — **Two different track maps**
  - Files: `ui/layout.py:422-478` (Plotly map, no rotation, first driver's trail as outline), `ui/track_map.py` (SVG, rotation, corners).
  - Fix: One map module. For analysis add a **speed/gear-coloured lap map** (official FastF1 example — `tasks.md §9`), and a **driver position replay slider** over the session (uses the historical location frames you already load).

- [ ] **UX-06** · P2 · S — **Race control panel usability**
  - Files: `ui/layout.py:654-688`.
  - Problem: Multiselect `key="rc_categories"` is shared across sessions with different category sets (stale defaults); no text search; no link between a message and the lap chart; no "SC/VSC/red flag" period shading anywhere.
  - Fix: key per session; search box; shade SC/VSC/red-flag periods (from `session.track_status`) on lap-time and position charts — the single most requested context in strategy analysis tools like Armchair Strategist.

- [ ] **UX-07** · P2 · M — **Layout is desktop-only**
  - Files: `ui/theme.py` CSS, `ui/dashboard.py:350-355`.
  - Problem: 13-column `nowrap` table and a fixed 60/40 split; on laptops the tower scrolls horizontally with no sticky Pos/Driver columns; unusable on phones.
  - Fix: sticky first two columns; responsive column hiding (Speed, Diff, Tyre history collapse under 1200 px); stack map under tower below 900 px; "compact" density toggle.

- [ ] **UX-08** · P2 · S — **Accessibility**
  - Problem: Meaning conveyed by colour only (purple/green/yellow segments, compound rings); KO rows at 55 % opacity fail contrast; SVG has no `<title>`/ARIA labels; emoji-only tab labels.
  - Fix: `title` tooltips on segments ("Sector 2 · mini 3 · personal best"), compound letters already help — keep; raise KO opacity or use a tinted background without reducing text contrast; `role="img"` + `<title>` on the SVG; add text to tab labels.

- [ ] **UX-09** · P2 · S — **Theme consistency and external font dependency**
  - Files: `ui/theme.py:110` (`@import url('https://fonts.googleapis.com/…')`), `ui/layout.py:75-79`.
  - Problem: The dashboard block is a dark custom theme inside Streamlit's default (possibly light) theme; `st.title` + the custom header duplicate the session title; Google Fonts is fetched at runtime (privacy + offline replays).
  - Fix: `.streamlit/config.toml` with a dark base theme matching `theme.py` tokens (Streamlit ≥1.54 supports chart palettes via theme config); self-host or fall back to system fonts; remove the duplicate title.

- [ ] **UX-10** · P2 · M — **No broadcast delay**
  - Problem: Every live-timing product researched has a user-set delay to stay in sync with TV/streams (undercut-f1: delayed queue, M/N keys, ~50 s suggested; f1-dash: timestamped buffers; matteocelani: up to 3 min; formula-timer: "Delay Control"; F1ReplayTiming: sync from a screenshot).
  - Fix: Keep ingest real-time; the per-browser snapshot is taken from `state_at(now − delay)`. Easiest: keep the last *N* minutes of immutable snapshots (one per second) in a deque and pick by timestamp; delay slider in the sidebar persisted in `st.session_state`/query params.
  - Depends on: LIVE-05, LIVE-11.

- [ ] **UX-11** · P3 · S — **Loading & error UX**
  - Problem: Exceptions are swallowed into empty frames in many adapters (`except Exception: return pd.DataFrame()`), so panels just say "No data" with no reason; first load shows a bare spinner.
  - Fix: A small `DataStatus` per panel (`ok | empty | unavailable(reason) | auth_required | error(msg)`) surfaced as a caption; `st.status` with steps ("Downloading timing…", "Merging telemetry 7/20…").

- [ ] **UX-12** · P3 · S — **Units, time zones, preferences**
  - Fix: km/h ↔ mph, °C ↔ °F, local vs track time (SessionInfo `GmtOffset` / FastF1 `EventDate` local), all in a Settings popover and URL params.

---
## 8. P2 — Code quality, tooling & repo hygiene

- [x] **REPO-01** · P1 · S — **A virtualenv is committed** — done in 969eff8
  - Files: `.venv311/` (46 tracked files: `Scripts/*.exe` Windows launchers, `pyvenv.cfg`, jupyter extensions JS).
  - Problem: `.venv311/` is in `.gitignore` but was committed before; the `.git` directory is 7.6 MB largely for this. Windows binaries in a Python repo also trip security scanners. The project's sync config already has to exclude it.
  - Fix: `git rm -r --cached .venv311 && git commit`; optionally purge from history with `git filter-repo --path .venv311 --invert-paths` (coordinate with collaborators — it rewrites history).
  - Acceptance: `git ls-files | grep -c venv` → 0.

- [ ] **REPO-02** · P1 · S — **Dependency floors are wrong; no lock file**
  - Files: `requirements.txt`, `requirements-dev.txt`.
  - Problem: `streamlit>=1.35` but the code uses `st.plotly_chart(width="stretch")` (added in a late-2025 release, streamlit PR #12559 — confirm the exact version) and several recent APIs; UX-01 needs ≥1.55. `livef1>=1.2.0,<2` floats across patch releases that change parsing (1.2.7 in this audit vs 1.2.11 on PyPI). `requests-cache`/`pyarrow`/`signalrcore` (for LIVE-01) should be explicit. No lock → CI and users get different versions.
  - Fix: `streamlit>=1.55,<2`; explicit `signalrcore` pin compatible with FastF1's; adopt `uv` (`pyproject.toml [project]` + `uv.lock`) or `pip-tools` (`requirements.in` → hashes). Dependabot/Renovate weekly.
  - Acceptance: `uv sync --frozen && pytest` in CI.

- [ ] **REPO-03** · P2 · S — **Python version story is inconsistent**
  - Files: `readme.md` ("Python 3.11+"), `pyproject.toml:3,17` (`py312` targets), `.github/workflows/ci.yml:13` (3.12 only).
  - Problem: Black warns "Python 3.11 cannot parse code formatted for Python 3.12" when run on 3.11 (seen in this audit). FastF1 3.8 needs ≥3.10.
  - Fix: Decide the floor (3.11 is reasonable); set `target-version` to it; CI matrix `3.11, 3.12, 3.13`.

- [ ] **REPO-04** · P2 · M — **Type checking not enforced (21 mypy errors)**
  - Files: `data/source_manager.py:20,32-35` and 5 other files (implicit `Optional` defaults, etc.).
  - Fix: Fix errors (`param: str | None = None`), add `mypy` to CI with `--ignore-missing-imports`, then `--strict` per package progressively (`processing/` first — it's pure).

- [ ] **REPO-05** · P2 · S — **Dead code and unused config**
  - Files: see LIVE-16; plus `ui/layout.py:822-824` (`format_lap_time`, unused), `data/jolpica_adapter.py:134-311` (`get_driver_standings_df`, `get_race_results_df`, `get_lap_times_df`, `get_pit_stops_df`, `get_qualifying_df` — no callers), `config.py:18-22` (`default_year`, `default_gp`, `default_session`, `distance_step`, `cache_ttl_seconds` — never read; `TelemetryProcessor.DISTANCE_STEP` duplicates `distance_step`), `app.py:64-66` (`sys.path.insert`), `data/live_adapter.py:480-501` (`__main__` demo), `processing/telemetry_processor.py:297-317` (`__main__` demo).
  - Fix: delete or use (Jolpica standings are a natural P3 feature — FEAT-06). Add `vulture` to CI with an allow-list.

- [ ] **REPO-06** · P2 · S — **Lint rules are minimal**
  - Files: `pyproject.toml:19-22` (`select = ["E4","E7","E9","F"]`).
  - Fix: add `B` (bugbear), `UP`, `SIM`, `I` (isort), `PD` (pandas-vet), `NPY`, `PERF`, `RUF`, `S` (bandit: would have flagged `pickle`), `DTZ` (naive datetimes — relevant to earlier tz bugs). Fix or `noqa` with reasons.

- [ ] **REPO-07** · P3 · S — **pre-commit configured in deps but not in repo** (`tasks.md §9` item)
  - Fix: `.pre-commit-config.yaml` with ruff (lint+format), mypy on `processing/`, end-of-file/trailing-whitespace, `check-added-large-files` (would have blocked `.venv311`).

- [ ] **REPO-08** · P2 · M — **Row-wise pandas in hot paths**
  - Files: `data/live_adapter.py:277-310` (per-record `pd.to_numeric`), `data/source_manager.py:428-434` (`iterrows` + `pd.Series` per row), `processing/telemetry_processor.py:37-47` and `processing/timing.py:51-66` (`iterrows` for driver maps), `ui/layout.py:371-395` (one `go.Bar` trace per stint row), `processing/time_utils.py:63-65` (`seconds_series` is a Python loop despite "Vectorised" docstring).
  - Fix: build DataFrames from lists of dicts once, then `pd.to_numeric` per column; `dict(zip(...))` for maps; one bar trace per compound with arrays; vectorised regex extraction in `seconds_series` (`str.extract` for `M:SS.mmm`).
  - Acceptance: micro-benchmarks in `tests/perf/` (skipped by default) show ≥5× improvements on 20k rows.

- [ ] **REPO-09** · P3 · S — **`python app.py` relaunch hack may be obsolete**
  - Files: `app.py:13-59`, `CLAUDE.md` "Bare mode is the hazard".
  - Problem: Streamlit 1.59 release notes list "Programmatic app launching with `python app.py`". The custom relaunch + env flag may now be redundant.
  - Fix: Verify on the pinned version; if native, delete the hack and its docs; keep the `raise` after `st.stop()` rule.

- [ ] **REPO-10** · P3 · M — **Packaging & layout**
  - Problem: Modules rely on `sys.path.insert` and top-level package names `data`, `processing`, `ui`, `config` that collide easily with other packages.
  - Fix: `src/f1dash/{data,processing,ui}`, `pyproject.toml [project]` with entry point `f1dash = "f1dash.cli:main"` (wraps `streamlit run`).

- [ ] **REPO-11** · P2 · S — **No logging; errors silently become empty data**
  - Files: many `except Exception: return pd.DataFrame()` / `return {}` (e.g. `data/fastf1_adapter.py:238-241, 256-259, 278-283`, `data/source_manager.py:86-91, 501-530`).
  - Fix: `logging.getLogger(__name__)` everywhere; log at WARNING with the exception when degrading; feed UX-11 status. Configure log level via env.

- [x] **REPO-12** · P2 · S — **HTML injection surface in `st.html`** — done in 3868dda
  - Files: `ui/dashboard.py:179-181, 212, 228, 257-258`, `ui/track_map.py:147-152, 203-206`.
  - Problem: Team colours from the feed / FastF1 are interpolated unescaped into `style="…"` and SVG `stroke`/`fill` attributes. Low risk with FastF1, higher with third-party replays or a future hosted mode.
  - Fix: `safe_hex(colour)` that only accepts `^#?[0-9A-Fa-f]{6}$`; everything else → fallback grey. Unit test with `"red;background:url(x)"`.

- [ ] **REPO-13** · P3 · S — **Secrets & config**
  - Fix: `.env.example` documenting `FASTF1_CACHE_DIR`, `REPLAY_DIR`, `F1_METRICS_STORE`, `F1TV_SUBSCRIPTION_TOKEN` (new), `F1_NETWORK_TESTS`, `LOG_LEVEL`; read the token via `st.secrets` when deployed; never log it; add it to `.gitignore`d `secrets.toml` docs.

---

## 9. P2 — Tests

- [ ] **TEST-01** · P1 · M — **Live fixtures repeat the "mocks diverge from reality" mistake**
  - Files: `tests/test_live_parsing.py:125-260`, `tests/test_source_manager.py:100-190`.
  - Problem: Hand-written records encode the code's assumptions, not the feed: `SessionInfo {"Meeting": "Bahrain"}` (real: nested dict), stints always with `Compound` and `LapStart: None` (real: `StartLaps`/`TotalLaps`, compound-less deltas, list snapshots), only 1-based `Sectors_N_Value` (real deltas are 0-based), X/Y treated as metres. That's why LIVE-03/04/05/06 pass CI.
  - Fix: Commit a small **recorded** live timing fixture (a few minutes of a real session from the public static archive `https://livetiming.formula1.com/static/<year>/<meeting>/<session>/<Topic>.jsonStream`, which is what FastF1 uses for historical loads) into `tests/fixtures/live/`, and build tests that replay it through the real ingest handler. Add a `scripts/capture_fixture.py` to refresh it. Keep fixtures tiny (<2 MB) — gzip them.
  - Acceptance: LIVE-03…06 each have a failing test on the recorded fixture before their fix.

- [x] **TEST-02** · P1 · S — **No UI-to-loader tests per source** — done in <pending>
  - Problem: The Replay path is broken end-to-end (HIST-01) yet green, because nothing drives the selector → loader → dashboard path with a mocked manager.
  - Fix: `AppTest` per source (`fastf1` with a stub session dict, `replay` with a temp dir, `live` with primed state, `livef1` stub) that asserts no exception and key panels render.

- [ ] **TEST-03** · P2 · S — **Network tests never run automatically**
  - Files: `.github/workflows/ci.yml`.
  - Fix: second workflow on `schedule:` (nightly) + `workflow_dispatch` with `F1_NETWORK_TESTS=1`, FastF1 cache via `actions/cache`. Failures open an issue. This catches upstream breakage (schedule shape, Jolpica limits, FastF1 changes) before users do.

- [ ] **TEST-04** · P2 · S — **No performance budgets**
  - Fix: `pytest-benchmark` (opt-in marker `perf`) for `poll_live_data`/snapshot build (LIVE-11), `build_timing_rows` on a full race, `build_track_svg` size.

- [ ] **TEST-05** · P3 · S — **Coverage & property tests**
  - Fix: `pytest --cov` in CI with a floor (start at current, ratchet up); `hypothesis` for `to_seconds`, `deep_merge`, `segment_boundaries`, `resample_to_distance_grid` (monotonic grid, coded channels only take source values).

- [ ] **TEST-06** · P2 · S — **Network test encodes a wrong expectation**
  - Files: `tests/test_integration_network.py:167-180`.
  - Problem: `test_timing_rows_are_ranked_and_complete` asserts fastest-to-slowest classification — correct only for practice/quali (DASH-01). It uses a Q session, so it will keep passing after DASH-01, but add an R-session test asserting real race order.

---

## 10. P3 — Documentation

- [ ] **DOC-01** · P1 · S — **Docs describe a live endpoint the code doesn't use**
  - Files: `readme.md:24, 92, 271`, `data/live_adapter.py:1-20, 109-119`, `scripts/live_smoke.py:30`, `ARCHITECTURE.md §1.3`, `PHASE1_RESEARCH_SUMMARY.md`.
  - Fix: After LIVE-01, document the SignalR Core flow, the F1TV token requirement per topic, the IP-blocking risk, and that the project is unofficial. Until then, state plainly that live mode uses livef1's legacy client and may not connect.

- [ ] **DOC-02** · P2 · S — **readme drift**
  - Problems: repository tree omits `ui/dashboard.py`, `ui/theme.py`, `ui/track_map.py`, `processing/timing.py`, `processing/time_utils.py`, `layout.md`, `.github/`; two sections numbered "### 5."; clone URL is a placeholder (`your-username/f1-telemetry-dashboard`); "Auto-Detection … based on real-time endpoint probing" is inaccurate (LIVE-15); "Intelligent Fallback" promises the most recent GP but falls back to 2025 (HIST-04); "LiveF1 (Historical)" advertised though broken (HIST-03).

- [ ] **DOC-03** · P2 · S — **`CLAUDE.md` / `tasks.md` out of date**
  - Problems: commands use Windows-only `.venv/Scripts/python`; says "four review rounds" (there are five plus an undocumented sixth); architecture section doesn't mention `ui/dashboard.py`/`processing/timing.py`/`ui/track_map.py`; `tasks.md §9` still lists "Live timing tower" and "Track dominance map" as not started.
  - Fix: cross-platform commands; add a "Round 6 — dashboard spec" section to `tasks.md`; link this file from both.

- [ ] **DOC-04** · P3 · S — **`dashboard_preview.html` (86 KB) is a static artefact of unknown freshness**
  - Fix: delete, or regenerate from a fixture via a script and reference it from the readme as a screenshot/preview.

- [ ] **DOC-05** · P3 · S — **Legal & data-use notes**
  - Fix: A "Data sources & terms" section: F1 live timing and archive are unofficial/undocumented endpoints; subscription-gated data requires the user's own F1TV account and must not be redistributed; hosting publicly risks IP blocks (f1-dash, matteocelani precedent); F1 trademarks disclaimer (already in LICENSE). Note Jolpica fair-use limits and OpenF1's paid real-time tier.

---
## 11. P3 — New features (parity with reference sites)

Ordered by value/effort. Each is a self-contained item once the P0/P1 foundations exist.

- [ ] **FEAT-01** · M — **Race trace & gap chart**: gap to leader (or to a reference driver) per lap for all/selected drivers, SC/VSC shaded. undercut-f1 shows gap-to-leader & lap time over the last 15 laps; Armchair Strategist's race trace is its signature. Data: FastF1 `laps.Time` cumulative; live `TimingData.GapToLeader` history.
- [ ] **FEAT-02** · M — **Pit rejoin predictor ("Circle of Doom")**: where a driver would rejoin if they pitted now = current gap − circuit pit loss. f1telemetry.com v2.2.2 moved to "the circuit's real pit loss time (from the circuit API)". Needs a per-circuit pit-loss table (seed from historical `PitInTime→PitOutTime` medians per circuit + stationary time).
- [ ] **FEAT-03** · M — **Tyre degradation / stint pace**: fuel-corrected lap time vs tyre age per compound (Armchair Strategist: fuel-adjusted laps, degradation distributions). Exclude in/out laps, SC laps and `IsAccurate == False`.
- [ ] **FEAT-04** · M — **Lap-by-lap replay slider** of positions on the SVG map for historical sessions (F1ReplayTiming, f1-race-replay). Pre-compute a 2 Hz position table per driver; render client-side with a small custom component so scrubbing doesn't rerun Python.
- [ ] **FEAT-05** · S — **Team radio list** (`TeamRadio` topic / FastF1 has no radio; OpenF1 `team_radio` historical is free from 2023): clip list with driver + time, playable audio; optional local Whisper transcription as undercut-f1 does. *Auth-gated live* — respect the token rules.
- [ ] **FEAT-06** · S — **Standings panels** (drivers/constructors) via Jolpica (the `*_df` helpers already exist, unused) with points-after-this-race projection for live races.
- [ ] **FEAT-07** · S — **Linear track position strip**: every car on a straight 0→lap-length line (f1telemetry.com "Lineal Driver Positions" v2.1.0) — cheap, readable on mobile, great for spotting DRS-train-like groups (in 2026: overtake-mode trains).
- [ ] **FEAT-08** · M — **Car-data dial for one driver** (speed/throttle/brake/gear/RPM as a radial gauge, f1telemetry.com "Circle of Car Data"); live requires token.
- [ ] **FEAT-09** · S — **Speed-trap & sector ranking panel** ("Pace Radar" in matteocelani/f1-telemetry): I1/I2/FL/ST ranking and sector bests.
- [ ] **FEAT-10** · M — **Customisable layout**: choose/arrange panels, saved per user (formula-timer "Custom Columns"; nitrous roadmap "drag-and-drop panels"). In Streamlit: column toggles + panel checklist stored in query params.
- [ ] **FEAT-11** · S — **Track limits / deleted laps view** from race control (`Message` contains "TRACK LIMITS"/"DELETED") and FastF1 `Deleted`/`DeletedReason`.
- [ ] **FEAT-12** · S — **2026 regulation context**: label active-aero/overtake-mode where data exists (FastF1 discussion #861 on 2026 ERS/energy data); hide the DRS channel for 2026+ instead of plotting a flat zero.
- [ ] **FEAT-13** · M — **Weather radar / rain probability** (nitrous concept idea) — low priority, external API needed.
- [ ] **FEAT-14** · S — **Share links**: encode year/GP/session/drivers/tab in `st.query_params` so a view can be bookmarked.

---

## 12. Target architecture

### 12.1 Why change

Streamlit's rerun model is excellent for the historical analysis side and poor at being a *server* for a stateful real-time feed. Today the feed, the state and the rendering all live inside per-browser `session_state`, so every tab owns a socket, every tick reprocesses everything, and there is no single source of truth.

### 12.2 Proposed shape (still one Python process, Streamlit front end)

```
                ┌──────────────────── process-wide (st.cache_resource) ─────────────────────┐
F1 SignalR Core │  IngestClient ──► Recorder (subscribe.json + live.jsonl, optional)          │
 /signalrcore   │      │ feed(topic, data, ts)                                               │
 (+F1TV token)  │      ▼                                                                     │
                │  LiveState  (per-topic dicts, deep_merge, lock)  +  SeriesStore            │
                │      │        (Car/Pos ring buffers, lap completions, RCM, weather)        │
                │      ▼ on change (≤ 1 Hz)                                                  │
                │  SnapshotBuilder → immutable Snapshot{version, ts, unified-dict tables}     │
                │      │                                                                     │
                │  SnapshotHistory (deque of last 5 min, 1/s)  ◄── delay lookup (UX-10)       │
                └──────┼─────────────────────────────────────────────────────────────────────┘
                       ▼
     per browser: @st.fragment(run_every=1..3) → snapshot = history.at(now − delay)
                  → same renderers as historical (unified dict contract)

Historical:  FastF1 Session (st.cache_resource, max 3) → normalise → unified dict (st.cache_data)
Replay:      Parquet/JSON bundle  → unified dict        |  live.jsonl → IngestClient(virtual clock)
```

Key properties:
- **One upstream connection per process** (LIVE-09), reconnect + staleness (LIVE-08).
- **State is merged, not appended** (LIVE-05); series are bounded but lap history is not (LIVE-11).
- **Snapshots are immutable** → no locks needed in render code (LIVE-07).
- **Recording = the same messages the handler consumes** → replay and tests use the real path (LIVE-12, TEST-01).
- **Renderers stay source-agnostic** — the unified dict contract from `CLAUDE.md` is preserved.

### 12.3 Alternative if Streamlit becomes the bottleneck

The mature live-timing projects all split ingest and UI: f1-dash (Rust realtime service → **SSE** → Next.js + Zustand), matteocelani/f1-telemetry (Node backend → WebSocket, 50 ms batching → Next.js, 60 fps interpolated SVG map), F1ReplayTiming (FastAPI serving API + WebSocket + static Next.js build from **one port/container**). If sub-second updates, smooth car animation or many concurrent viewers become goals, keep the Python ingest/state layer from §12.2 and expose snapshots over FastAPI SSE/WebSocket to a small JS front end (or a Streamlit custom component that subscribes directly), leaving Streamlit for historical analysis. Do this only after §12.2 exists — the state layer is reusable either way.

### 12.4 Code sketches

Process-wide live service:
```python
# data/live_service.py
@st.cache_resource(show_spinner=False)
def get_live_service() -> "LiveService":
    return LiveService(token_provider=env_token_provider, recorder_dir=config.replay_dir)
```

Fragment reading an immutable snapshot with a delay:
```python
@st.fragment(run_every=2)
def live_view():
    svc = get_live_service()
    delay = st.session_state.get("delay_s", 0)
    snap = svc.history.at(time.time() - delay)     # None until first data
    if snap is None:
        st.info(svc.status.describe())              # CONNECTING / AUTH_REQUIRED / BLOCKED …
        return
    render_dashboard(snap.session_dict)             # same renderer as historical
```

Plotly figure that keeps zoom across refreshes:
```python
fig.update_layout(uirevision=f"{snap.session_key}:{channel}")
st.plotly_chart(fig, key=f"live-{channel}", width="stretch")
```

Lazy tabs (Streamlit ≥ 1.55):
```python
tabs = st.tabs(["Telemetry", "Laps", "Tyres"], on_change="rerun", key="analysis_tab")  # verify API on the pinned version
if tabs[0].open:
    with tabs[0]:
        render_telemetry_charts(...)
```

---
## 13. Research notes — reference sites

### 13.1 f1telemetry.com — `/en/live-timing`

- **What it is:** web live-timing dashboard; open source as **`mateenunez/f1-telemetry`** (Next.js App Router, TypeScript, Tailwind, a `websocket/` layer and `processors/` directory; env `NEXT_PUBLIC_API` + `NEXT_PUBLIC_WS`; Dockerfile; English/Spanish). An earlier round of this repo already surveyed that GitHub project (28⭐).
- **Widgets (from its help page):**
  - *Drivers Positions* leaderboard: rank, number, team, **tyre compound with age below**, speed, DRS, **pit-stop history with compounds**, gap to leader, interval, lap times, **mini-sector splits**.
  - Colour code "follows the official Formula 1 color standard": **yellow = slower than PB, green = personal best, purple = overall best, blue = in pit lane**. "IN PIT" badge.
  - *Track map*: real-time relative positions; **yellow sectors for incidents/SC, red for red flag**; optional three-sector display.
  - *Circle of Doom*: gaps in seconds around a circle to predict pit rejoin position; pit marker in pink; since v2.2.2 uses **real per-circuit pit loss** from a circuit API.
  - *Circle of Car Data*: radial live speed/throttle/brake/gear/RPM.
  - *Lineal Driver Positions* (v2.1.0): positions flattened onto a straight line.
  - Race-control **translation** and team-radio **transcription** for logged-in users.
- **Product/ops signals (changelog):** v2.0.0 (2026-07-12) introduced **role-based access** ("some data and widgets are now available depending on your account type"), removed chat; v2.2.0 (2026-09-05) server capacity upgrade for peak load; circuits added as calendar changes (Malaysia, Madring).
- **Take-aways for this repo:** (1) adopt the official colour semantics (DASH-03); (2) tyre age + pit history in the tower (DASH-11); (3) sector-state colouring on the map when yellow/red flags (cheap with `TrackStatus` + race-control `Sector`); (4) pit-rejoin and linear-position widgets (FEAT-02, FEAT-07); (5) plan for load: one upstream connection, many readers (LIVE-09).

### 13.2 formula-timer.com — `/livetiming`

- **Stack/licence:** Next.js, GPL-3.0, "independent, unofficial project"; has a Premium tier.
- **Navigation:** Live Timing · Standings · Analytics · Calendar · Teams · Circuits · Replay · Settings.
- **Live page features (quoted):** "Leaderboard: Live positions, lap times, and gaps between drivers", "Track Map: Visual map of driver locations on the circuit", "Mini Sectors: Detailed tracking across micro-sections of the lap", "Team Radio: Live communications between teams and drivers", "Race Control: Official messages, flags, and penalties".
- **Customisation:** "Custom Columns: Toggle visibility of sector times, speed, and tyre data"; "Delay Control: Sync timing data with your preferred viewing method".
- **Analysis:** "Lap Charts: Visualise pace evolution and tyre degradation trends"; "Driver Comparison: Match drivers under identical conditions".
- **Colour legend:** "Green = personal best, Purple = session best, Yellow = slower than personal best".
- **Marketing metrics:** "20+ metrics per driver", "updated every second", "0.001 s timing accuracy".
- **Take-aways:** column toggles (FEAT-10), delay (UX-10), degradation lap charts (FEAT-03), standings/calendar/circuit pages as separate Streamlit pages (`st.navigation`), a Replay section (HIST-02/LIVE-12).

### 13.3 nitrous.software — Nitrous

- **What it is:** native **desktop** app (macOS Apple Silicon/Intel, Linux x86_64/arm64, Windows x86_64) for motorsport live timing and streaming; free with optional premium. f1-dash's author calls it "the spiritual and technical successor to f1-dash".
- **Features (quoted):** Live Timing — "Sector times, gaps, intervals, and pit stop data updated live"; Telemetry — "speed, throttle, brake, gear, and DRS" per car; Trackmap — "generated from actual positional telemetry data"; Race Control — "flag status, safety car and VSC deployments, and steward decisions"; Session Replay — "Scrub forwards and backwards, jump to key events".
- **Auth model:** "log in with your own F1 account directly inside the app. No third-party backend — your credentials never leave your device." Streaming and the "Driver Tracker Track Map require an active F1 subscription".
- **Roadmap:** shipped prototype → F1 replay → MVP 0.1.0 (live timing + race control); in progress 1.0.0 "feature parity with f1-dash.com"; planned F1 TV stream windows, F2/F3/F4/F1 Academy, Philips Hue ambient effects; concepts: weather radar, **drag-and-drop layout with sharing**, GT3/WRC/WEC/MotoGP.
- **Take-aways:** the industry answer to the auth + IP-blocking problem is **run locally with the user's own token** — which fits this app's local Streamlit model well. Make "bring your own F1TV token, stays on your machine" the documented live mode (LIVE-01, REPO-13, DOC-05). Session replay with scrubbing and "jump to key events" (race-control timestamps) is the next most valuable feature (FEAT-04).

### 13.4 Common UI patterns across all references

`✓` = observed in the public pages/READMEs reviewed · `–` = not observed (may still exist behind login or in-app).

| Pattern | f1telemetry | formula-timer | Nitrous | f1-dash | undercut-f1 | This repo today |
|---|---|---|---|---|---|---|
| Tower: pos, gap, interval, last/best, sectors | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (race semantics wrong — DASH-01) |
| Mini-sectors with official colours | ✓ | ✓ | – | ✓ | ✓ | ✗ (DASH-03) |
| Tyre compound + age + history | ✓ | ✓ | – | ✓ | ✓ | partial (DASH-11) |
| Pit status / pit history | ✓ | – | ✓ | ✓ | ✓ | historical only |
| Track map from GPS | ✓ | ✓ | ✓ | ✓ | ✓ (token) | ✓ historical; live needs token |
| Race control feed | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Team radio (+ transcription) | ✓ | ✓ | – | ✓ | ✓ | ✗ (FEAT-05) |
| Broadcast delay | – | ✓ | – | ✓ | ✓ | ✗ (UX-10) |
| Replay / recording | – | ✓ | ✓ | dev simulator | ✓ | pickle, historical only (HIST-02/LIVE-12) |
| Favourites / column toggles | – | ✓ | concept | ✓ | driver select | ✗ (UX-03, FEAT-10) |
| Accounts / auth | site accounts with roles | premium tier | user's own F1 account | – | user's own F1TV token | ✗ (LIVE-01) |

---

## 14. Research notes — open-source projects

| Project | Stack | What to borrow |
|---|---|---|
| **slowlydev/f1-dash** (sunset 2026, still self-hostable) | Rust workspace: `realtime` (SignalR → **SSE**), `api` (schedule), custom `signalr` crate, `simulator` (replays recordings); Next.js dashboard with **Zustand** state | Initial snapshot seeds cache, then incremental updates; `.z` topics base64+inflate; client-side **timestamped buffers (`useBuffer`) for delay**; simulator for development without a live session. Sunset reason: "increasing IP restrictions" and data "locked behind subscriptions". |
| **JustAman62/undercut-f1** | .NET TUI (`undercutf1`), `UndercutF1.Data` library | Delayed-queue channel for broadcast sync; recordings as `subscribe.json` + `live.jsonl`; `undercutf1 import <year> --meeting-key --session-key` converts the **static archive** into replays; `undercutf1 login` reads F1TV cookie/token; gap-over-last-15-laps charts; two-driver gap tracking; pit/stint page with pit-lane times; optional Whisper radio transcription. Documents that since **Dutch GP 2025** Driver Tracker, DRS indicator, championship tables and pit-stop times need a subscription. |
| **matteocelani/f1-telemetry** | pnpm monorepo: Node backend (SignalR decode, raw DEFLATE, **50 ms batching**, WebSocket) + Next.js + shared `core` types | "Handles F1's lossy feed gracefully by cross-referencing pit flags with stint data and permanently latching retirement states"; client-side classification with tie-breaking and position de-duplication; **60 fps interpolated SVG map**; delay up to 3 min; looped replay mode; hosted version **down due to F1 IP blocking** — self-host via Docker. |
| **mateenunez/f1-telemetry** (= f1telemetry.com) | Next.js + Tailwind + WebSocket service | Widget catalogue in §13.1; role-based feature gating; i18n. |
| **adn8naiagent/F1ReplayTiming** | FastAPI (API + WebSocket + serves static Next.js) in **one container/one port**; FastF1 for data; optional Cloudflare R2 storage | Pre-compute sessions (1–3 min each, full season 2–3 h) and persist; track map updates every 0.5 s with interpolation; PiP mode; **sync replay to broadcast from a screenshot of the TV timing tower** (vision model) or manual gap entry; live timing beta via SignalR. |
| **IAmTomShaw/f1-race-replay** | Python + Arcade (OpenGL) + FastF1 | Precompute frames into `computed_data/` (`--refresh-data` to rebuild); interpolated positions; simulated safety-car position (500 m ahead of leader) because SC GPS isn't in the API; honest note that the leaderboard is inaccurate in the first corners due to telemetry noise. Companion `open-pit-wall` hosts a telemetry broadcast server. |
| **Casper-Guo/Armchair-Strategist** | Dash + FastF1, precomputed CSVs, GitHub Actions auto-update in season, pre-commit + Ruff | Strategy plot, **fuel-adjusted lap times**, tyre-degradation distributions, teammate pace violins, race trace vs winner, position scatter — the best reference for the historical analysis tabs. Precompute + scheduled CI refresh is a pattern worth copying for "latest race" (TEST-03). |
| **FraserTarbet/F1Dash** | FastF1 + SQL Server | Track dominance per mini-sector (already partly adopted — fix DASH-04). |
| **theOehrly/Fast-F1** 3.7–3.8 | Python | Reference implementation of the SignalR Core client: `OPTIONS` negotiate for `AWSALBCORS` cookie, `access_token_factory`, `on('feed')`, `Subscribe` completion = snapshot; F1TV auth helper (`fastf1.internals.f1auth`, JWT verified against `https://api.formula1.com/static/jwks.json`); 3.8.0 new `fastf1.exceptions` module (imports from `fastf1.core` deprecated), Python ≥3.10, 2026 team constants. |
| **GoktugOcal/LiveF1** 1.2.x | Python | Useful historical medallion tables (bronze/silver) and topic parsers, but realtime client still on legacy `/signalr/`; parser quirks documented in LIVE-04/05. |
| **tdjsnelling/monaco** | Next.js + Node server | Early open-source live timing UI (f1.tdjs.dev); a good minimal reference for a Node SignalR proxy. |
| **Nicxe/f1_sensor** (Home Assistant) | Python | Works **without** auth using public topics; experimental F1TV token pairing via a browser extension; lists auth-gated topics (`CarData.z`, `Position.z`, `DriverRaceInfo`, `ChampionshipPrediction`, `TeamRadio`, `PitStopSeries`); tokens "usually valid for only a few days". Issue #611 (July 2026): persistent **403 on `/signalrcore/negotiate`** for some users — plan for `BLOCKED` state (LIVE-08). |

---

## 15. Research notes — data sources, protocols, limits & regulations

### 15.1 F1 live timing (unofficial, undocumented)

- **Endpoints:** SignalR Core hub at `wss://livetiming.formula1.com/signalrcore` (negotiate: `https://livetiming.formula1.com/signalrcore/negotiate`). Legacy classic SignalR at `/signalr/` (`negotiate?connectionData=[{"name":"Streaming"}]&clientProtocol=1.5`, headers `User-Agent: BestHTTP`, `Accept-Encoding: gzip,identity`) — the one livef1 uses; considered deprecated since F1's 2025 migration.
- **Protocol (Core):** subscribe with `Subscribe([topics])`; the invocation's completion result is `{topic: full_state}`; subsequent server calls `feed(topic, data, timestamp)`. `.z` topics are base64 of raw DEFLATE JSON (`zlib.decompress(b, -zlib.MAX_WBITS)`).
- **Auth:** JWT "subscription token" from the user's F1 account session; FastF1 verifies against `api.formula1.com/static/jwks.json`. Tokens are short-lived (days). Required for car data, positions, pit-stop times, championship prediction, team radio, driver race info (sources differ slightly on the exact list — re-verify live).
- **Common topics:** `Heartbeat`, `SessionInfo`, `SessionStatus`, `SessionData`, `ExtrapolatedClock`, `TrackStatus`, `LapCount`, `DriverList`, `TimingData`, `TimingAppData`, `TimingStats`, `TopThree`, `RaceControlMessages`, `RcmSeries`, `WeatherData`, `TeamRadio`, `AudioStreams`, `ContentStreams`, `CarData.z`, `Position.z`, `TyreStintSeries`, `PitLaneTimeCollection`, `PitStopSeries`, `DriverRaceInfo`, `ChampionshipPrediction`.
- **Sampling:** car data and positions ≈ 3.7 Hz per car (OpenF1 docs); weather ≈ 1/min; FastF1's `CarData.z` channels `0` RPM, `2` Speed, `3` Gear, `4` Throttle, `5` Brake, `45` DRS.
- **Static archive (post-session):** `https://livetiming.formula1.com/static/<year>/Index.json` → meeting/session paths → per-topic `.json` keyframes and `.jsonStream` files. This is what FastF1 historical loads use and what undercut-f1 imports — ideal for recorded test fixtures (TEST-01).
- **Operational risk:** F1 blocks IPs of heavy/hosted consumers (matteocelani hosted instance, f1-dash sunset). Local, single-connection, user-token usage is the sustainable mode.

### 15.2 FastF1 (historical)

- Version in repo env: 3.8.3 (2026-04-29). Timeline: 3.6.0 (2025-07) preliminary results from timing data; **3.7.0 (2025-11) new live timing endpoint/protocol + F1TV auth**; 3.8.0 (2026-02) Python ≥3.10, Pydantic dep, `fastf1.exceptions`, generated team constants; 3.8.1 pre-season 2026; 3.8.2–3.8.3 crash-lap, tyre and driver data fixes.
- Units: `X/Y/Z` in **1/10 m**; `WindSpeed` in **m/s**; `Speed` km/h; `Brake` boolean in car data; `Distance` from `add_distance()` integrates speed.
- Useful APIs this repo doesn't use yet: `session.results` (`Position`, `Status`, `Q1/Q2/Q3`, `Time`), `laps.split_qualifying_sessions()`, `laps.pick_wo_box()`, `laps.pick_not_deleted()` / `Deleted`, `laps.pick_track_status()`, `session.track_status`, `Laps.TyreLife`/`FreshTyre`, `session.get_circuit_info().corners.Distance` (corner markers on distance charts), `fastf1.plotting.get_driver_style()` (per-session driver colours/line styles for teammates).
- Deprecated: `fastf1.utils.delta_time` (already avoided), imports of exceptions from `fastf1.core`/`fastf1.ergast.interface` (3.8.0).

### 15.3 Jolpica-F1 (Ergast successor)

- Base `https://api.jolpi.ca/ergast/f1/…` (`.json` or trailing slash).
- Pagination: `limit` default **30**, max **100**; `offset`; response `MRData.total/limit/offset`.
- Rate limits (unauthenticated): **4 req/s burst, 500 req/h sustained**, "will decrease in the future" as token access arrives. Cache aggressively.

### 15.4 OpenF1

- Historical data (2023+) **free, no auth**; **real-time requires a paid subscription**. Endpoints include car_data, location, laps, intervals (≈4 s), position, pit, stints, race_control, team_radio, weather, session_result, starting_grid, overtakes, championships. A good *secondary* historical source for team radio and overtakes; not a free live source.

### 15.5 Streamlit capabilities relevant here (1.54 → 1.64)

- 1.54: chart colour palettes in theme config. 1.55: `on_change` for `st.tabs`/`st.expander`/`st.popover` (lazy tabs), widget `bind` to query params. 1.57: Starlette/Uvicorn default server. 1.58: `@st.fragment(parallel=True)`, `st.pagination`. 1.59: `st.skeleton`, `st.mermaid_chart`, programmatic `python app.py` launch. 1.61: lazy `st.dataframe` rows, `refresh_mode="background"` for caches (stale-while-revalidate — ideal for schedules). 1.62: `st.cache` removed. 1.63: `@st.fragment(key=…)` event-scoped fragment reruns.
- `st.tabs` default is **eager** ("all tab content is computed and sent to the frontend regardless of which tab is selected").

### 15.6 2026 regulations that affect data/UI

- **22 cars / 11 teams** (Cadillac joins; Sauber → Audi). Qualifying: "If twenty-two (22) Cars are eligible six (6) will be eliminated after Q1 and Q2"; Q1 18 min, Q2 15 min, Q3 12 min.
- **No DRS** — active aero modes and an overtake/"manual override" energy mode; the DRS channel reads 0 (documented in `CLAUDE.md`). FastF1 discussion #861 tracks 2026 ERS/energy data.
- Calendar changes (e.g. Madrid "Madring") break hand-maintained circuit-name maps — derive from data (HIST-03).

---

## 16. Domain cheat-sheet for the agent

- **Session codes:** FP1/FP2/FP3, `SQ` Sprint Qualifying (2023 "Sprint Shootout"), `S` Sprint, `Q` Qualifying, `R` Race.
- **Race gap** = time behind leader when crossing the same timing line; lapped cars show `+N LAP`. **Interval** = to car directly ahead. Neither equals best-lap difference.
- **Mini-sector colours:** purple session best · green personal best · yellow slower than PB · (blue/grey in pit or no time).
- **Track status codes:** 1 green, 2 yellow, 4 SC, 5 red, 6 VSC deployed, 7 VSC ending.
- **Tyres:** SOFT red `#da291c`, MEDIUM yellow `#ffd12e`, HARD white `#f0f0ec`, INTER green `#43b02a`, WET blue `#0067ad` (take from FastF1 per season).
- **Units:** GPS 1/10 m; wind m/s (FastF1); speed km/h; pressure mbar; temps °C.
- **Data quality:** first-lap telemetry and positions are noisy; feed is lossy — latch retirements, don't un-pit a car on one missing flag; deleted laps exist (`Deleted`); SC laps distort pace — filter with track status.

---

## 17. Suggested execution order (milestones)

Each milestone should end with all gates green and a short entry in `tasks.md`.

**M0 — Quick correctness wins (≈1 day)**
REPO-01 → HIST-01 → LIVE-03 → LIVE-06 → DASH-07 → HIST-04 → HIST-05 → HIST-07 → CACHE-01 → REPO-12 → TEST-02

**M1 — Timing tower semantics (≈2 days)**
DASH-01 → DASH-02 → DASH-04 → DASH-05 → DASH-06 → DASH-03 → DASH-08 → DASH-09 → DASH-10 → DASH-11 → TEST-06 → DASH-12

**M2 — Live foundation (≈4–6 days, needs a race weekend for final verification)**
TEST-01 (recorded fixture) → LIVE-05 (state merge) → LIVE-04 → LIVE-07 → LIVE-01 (SignalR Core client + token) → LIVE-08 → LIVE-09 → LIVE-02 → LIVE-10 → LIVE-11 → LIVE-14 → LIVE-13 → LIVE-15 → LIVE-12 → LIVE-16 → DOC-01

**M3 — Platform & hygiene (≈2 days)**
REPO-02 → REPO-03 → REPO-06 → REPO-07 → REPO-04 → REPO-11 → REPO-05 → REPO-08 → HIST-02 → HIST-03 (or hide source) → HIST-08 → CACHE-02 → CACHE-03 → TEST-03 → TEST-04 → TEST-05 → REPO-09

**M4 — UX (≈3 days)**
UX-01 → UX-02 → UX-03 → UX-10 → UX-05 → UX-04 → UX-06 → UX-07 → UX-08 → UX-09 → UX-11 → UX-12 → HIST-06 → CACHE-04

**M5 — Features**
FEAT-01 → FEAT-03 → FEAT-04 → FEAT-02 → FEAT-07 → FEAT-09 → FEAT-06 → FEAT-11 → FEAT-12 → FEAT-14 → FEAT-05 → FEAT-08 → FEAT-10 → FEAT-13

**M6 — Docs sweep**
DOC-02 → DOC-03 → DOC-04 → DOC-05 → REPO-13 → REPO-10 (optional restructure last, since it touches every import)

---

## 18. Sources

Reference sites
- [F1 Telemetry — Live timing](https://www.f1telemetry.com/en/live-timing) · [Help / widgets](https://www.f1telemetry.com/en/help) · [Changelog](https://www.f1telemetry.com/en/changelog) · [mateenunez/f1-telemetry](https://github.com/mateenunez/f1-telemetry)
- [Formula-Timer — Live timing](https://formula-timer.com/livetiming)
- [Nitrous](https://nitrous.software/) · [Nitrous roadmap](https://nitrous.software/roadmap)
- [f1-dash (sunset notice)](https://f1-dash.com/)

Open-source projects
- [slowlydev/f1-dash](https://github.com/slowlydev/f1-dash) · [DeepWiki architecture summary](https://deepwiki.com/slowlydev/f1-dash)
- [JustAman62/undercut-f1 README](https://github.com/JustAman62/undercut-f1/blob/master/README.md)
- [matteocelani/f1-telemetry](https://github.com/matteocelani/f1-telemetry)
- [adn8naiagent/F1ReplayTiming README](https://github.com/adn8naiagent/F1ReplayTiming/blob/main/README.md)
- [IAmTomShaw/f1-race-replay README](https://github.com/IAmTomShaw/f1-race-replay/blob/main/README.md)
- [Casper-Guo/Armchair-Strategist](https://github.com/Casper-Guo/Armchair-Strategist)
- [FraserTarbet/F1Dash](https://github.com/FraserTarbet/F1Dash)
- [tdjsnelling/monaco](https://github.com/tdjsnelling/monaco)
- [GoktugOcal/LiveF1](https://github.com/GoktugOcal/LiveF1) · [livef1 on PyPI](https://pypi.org/project/livef1/) · [LiveF1 constants.py](https://github.com/GoktugOcal/LiveF1/blob/main/livef1/utils/constants.py)
- [Nicxe/f1_sensor — F1TV auth testing](https://nicxe.github.io/f1_sensor/help/experimental-testing) · [Issue #611 (403 on signalrcore)](https://github.com/Nicxe/f1_sensor/issues/611)

Data sources & protocols
- [FastF1 issue #753 — live timing moved to signalrcore](https://github.com/theOehrly/Fast-F1/issues/753) · [FastF1 PR #760 — SignalR Core + F1 account auth](https://github.com/theOehrly/Fast-F1/pull/760) · [FastF1 releases](https://github.com/theOehrly/Fast-F1/releases) · [FastF1 docs](https://docs.fastf1.dev/)
- [Connecting to the SignalR F1TV data endpoint (dweik.xyz)](https://dweik.xyz/post/f1-signalr-endpoint/)
- [Jolpica rate limits](https://github.com/jolpica/jolpica-f1/blob/main/docs/rate_limits.md) · [Jolpica API docs](https://github.com/jolpica/jolpica-f1/blob/main/docs/README.md)
- [OpenF1 docs](https://openf1.org/docs/)
- [Streamlit st.tabs docs](https://docs.streamlit.io/develop/api-reference/layout/st.tabs) · [Streamlit 2026 release notes](https://docs.streamlit.io/develop/quick-reference/release-notes/2026) · [streamlit PR #12559 — width for st.plotly_chart](https://github.com/streamlit/streamlit/pull/12559)
- [2026 F1 qualifying format explained (Motorsport.com)](https://www.motorsport.com/f1/news/2026-f1-qualifying-format-explained-as-cadillac-expands-the-grid-to-22-cars/10788081/)
- [FastF1 discussion #861 — 2026 ERS/energy data](https://github.com/theOehrly/Fast-F1/discussions/861)

Local evidence (reproducible in this repo)
- Installed-library source reviewed: `livef1/adapters/realtime_client.py`, `livef1/utils/constants.py`, `livef1/data_processing/parse_functions.py`, `livef1/models/driver.py`, `livef1/models/session.py`, `fastf1/livetiming/client.py`, `fastf1/internals/f1auth.py`.
- Scripts run during the audit (recreate from the Evidence blocks): replay-by-name failure (HIST-01), live distance units (LIVE-03), SessionInfo parsing (LIVE-06), `poll_live_data` timing at buffer cap (LIVE-11), livef1 snapshot-vs-delta key naming and stint parsing (LIVE-04/05).
