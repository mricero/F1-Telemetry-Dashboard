# IMPROVEMENTS.md — Session replay and UI rebuild

**Implementation plan written so an agent loop can work through it one item at a time.**

| | |
|---|---|
| Repository | F1 Telemetry Dashboard (Streamlit), local checkout |
| Plan date | 2026-09-23 |
| Base | `084fbcc` ("feat(FEAT-04): replay a whole session") plus uncommitted line-ending churn (see REPO-14) |
| Pinned stack | Python 3.11–3.13 · streamlit 1.59.0 · fastf1 3.8.3 · pandas 2.3.3 · numpy 2.5 · plotly 6.8 (`requirements.lock`) |
| Goal | 1. The replay shows the session **unfolding** (order, gaps, tyres, pits, flags, race control, weather at every moment), not only the final result. 2. A UI that reads like a professional timing product, not like generated boilerplate: no emoji, no decorative effects, one clear visual system (§5). |
| Previous content | The earlier audit (revision 1: live pipeline, caching, tooling, most of it done) was removed from this file on request. It is in git history (`git show 084fbcc:IMPROVEMENTS.md`) and the done items are summarised in `tasks.md`. Its still-open items (LIVE-01, LIVE-08, CACHE-02…) are **out of scope** for this loop. |

---

## Contents

0. [How to run this file in an agent loop](#0-how-to-run-this-file-in-an-agent-loop)
1. [Diagnosis: why the replay shows only the end result](#1-diagnosis-why-the-replay-shows-only-the-end-result)
2. [Target design and plan iterations](#2-target-design-and-plan-iterations)
3. [Items — replay (REPLAY-01…10) and repo hygiene (REPO-14)](#3-items--replay-and-repo-hygiene)
4. [Items — UI (UI-00…07)](#4-items--ui)
5. [UI guideline (binding for every UI change)](#5-ui-guideline-binding-for-every-ui-change)
6. [Execution order](#6-execution-order)
7. [Architecture](#7-architecture)
8. [Research notes](#8-research-notes)
9. [Domain cheat-sheet](#9-domain-cheat-sheet)
10. [Sources](#10-sources)

---

## 0. How to run this file in an agent loop

### 0.1 Loop protocol (one item per iteration)

1. **Pick** the first unchecked `- [ ]` item in the order of §6 whose `Depends on` items are all checked. Skip `- [~]` items (blocked; the reason is written under them).
2. **Read** every file listed under `Files`, the relevant part of `CLAUDE.md` (gotchas earlier rounds paid for), and — for any item that touches what the user sees — **§5 UI guideline in full**.
3. **Reproduce first.** Write a failing test that captures the item's `Acceptance`. Network-dependent tests go in `tests/test_integration_network.py` behind the `network` marker.
4. **Fix** with the smallest change that satisfies `Acceptance`. The unified session dict (`CLAUDE.md` → "The unified session dict is the central contract") may only change where an item says so; when a persisted key is added, bump `REPLAY_SCHEMA_VERSION` and default the key for older replays.
5. **Run the gates** (§0.2). All must pass, including the UI guideline checks (§5.12) once UI-00 has landed.
6. **Tick** the checkbox here, append `— done in <short-sha>`, add one line to `tasks.md` under a "Round 7 — replay and UI" heading.
7. **Commit** one item per commit: `feat(REPLAY-03): snapshot the session at time t`.
8. If an item turns out to be wrong or blocked, mark it `- [~]`, write why underneath, and move on.

### 0.2 Gates

```bash
python -m pytest -q
python -m ruff check .
python -m black --check .
python -m mypy --ignore-missing-imports app.py data processing ui
# optional, needs internet
F1_NETWORK_TESTS=1 python -m pytest -m network -q
```

`python` is the interpreter that has `requirements.lock` installed (on the maintainer's Windows machine `.venv/Scripts/python` or `.venv311/Scripts/python`). Do not create another virtualenv inside the repo.

### 0.3 Before the first commit of a session

1. `git --no-optional-locks status --short`. If dozens of files show as modified, compare with `git diff --ignore-cr-at-eol --stat`; files missing from the second list are **line-ending churn only** (~55 files on 2026-09-23). Do **REPO-14** first and never mix churn with a real change.
2. If git reports `.git/index.lock` and no git process is running, the lock is stale; delete it. (One was moved to `_to_delete/` on 2026-09-23 — that folder can be deleted.)

### 0.4 Item format

```
- [ ] **ID** · Priority · Effort (S ≤1h, M ≤½ day, L >½ day)
  - Files:        paths
  - Problem:      what is wrong and why it matters
  - Fix:          the approach, concrete enough to implement
  - Acceptance:   testable definition of done
  - Depends on:   other IDs (optional)
```

### 0.5 Ground rules

- **A replay snapshot never sees the future.** `processing/replay_model.snapshot_at(t)` reads only rows stamped `≤ t`. `results`, final `Position`, fastest-lap telemetry and dominance are end-of-session facts and belong to the Results page. A property test enforces this (REPLAY-03).
- **Racing semantics live in Python; the replay player's JavaScript only looks values up and draws.** If the JS needs a new rule (who is P3, is this lap purple), add a payload field computed and tested in Python.
- **`fastf1._api` is private.** Wrap each call in `try/except` with a WARNING log, pin its columns with a test, and keep a fallback so a FastF1 upgrade degrades the replay instead of breaking the load.
- **Mocks mirror real upstream shapes** (see `CLAUDE.md` → Testing conventions). Use the real value formats listed in §9.
- **Streamlit reruns the whole script on every interaction.** Anything expensive is cached; anything per-viewer lives in `st.session_state` under a key that includes the session key.
- **Replay state keys (use exactly these):** `session_key = f"{source}:{year}:{gp}:{session_type}"` (for a saved replay: `f"replay:{replay_file}"`). `st.session_state[f"replay_cursor:{session_key}"]` is the authoritative cursor (float, session seconds). The player component is mounted with `key=f"replay_player:{session_key}"`; when its `cursor` state changes, copy it into `replay_cursor:{session_key}`. Every other reader (snapshot panels, Analysis markers) reads `replay_cursor:{session_key}`.
- **`st.stop()` is a no-op in bare mode** — always `raise` after it.
- **Time strings never go through `pd.to_timedelta`** — use `processing/time_utils.to_seconds`.
- **The UI guideline (§5) is binding.** No emoji anywhere in the interface, no gradients, glows or glass, no external font or script requests. If a change needs an exception, write it in §5.13 with the reason before implementing it.

---

## 1. Diagnosis: why the replay shows only the end result

Verified by reading `app.py`, `ui/dashboard.py`, `processing/timing.py`, `processing/replay.py`, `ui/replay_view.py`, `data/fastf1_adapter.py`, `data/source_manager.py` and the installed FastF1 3.8.3 / Streamlit 1.59.0 source, and checked by an independent review against the code and the cached 2023 Bahrain GP files.

| # | What the user sees | Root cause (file → function) |
|---|---|---|
| D1 | The large dashboard at the top of the page (header, tower, sector cards, map) always shows the **final** classification. | `app.main()` calls `render_dashboard(session_data)` with the whole-session dict. `processing/timing.build_timing_rows` aggregates **all** laps (`best`, `last`, `laps_completed`), orders races by the final `results.Position` and takes gaps from the final `results.Time`. Nothing in the tower depends on time. |
| D2 | The header clock reads the session duration, the flag reads CHEQUERED / SESSION ENDED, the weather is the last sample. | `ui/dashboard._session_clock` uses `max(laps.Time)`; `_flag_state` reads the last `Track`-scoped race-control flag; `header_html` uses `weather.iloc[-1]`. |
| D3 | The replay is the 7th of 9 tabs, below the end-result dashboard, and only shows a map and a list of driver codes. | FEAT-04 added `ui/replay_view.render_session_replay` as a tab: map markers, `order_at()` (position at the last completed lap) and the leader's lap. No gaps, intervals, tyres, pit status, flags, race control or weather at time *t*. |
| D4 | The running order is empty when the replay opens; qualifying and practice replays miss each driver's first run. | The cursor starts at `timeline_bounds()[0]`. For a race that is lights out, because `get_position_timeline` uses `Laps.get_pos_data()`, which starts at the first `LapStartTime` (2023 Bahrain: session "Started" 1:02:36.7, VER completes lap 1 at 1:04:15.9) — so there is no pre-race data at all. `order_at()` returns "No completed laps yet" until the first car completes lap 1 (~100 s). In Q/FP the first lap's `LapStartTime` is NaT, so each driver's first out-lap is missing. No explicit *lights out* exists anywhere. |
| D5 | Playback is jerky (about 2 frames per second), flickers, and every viewer costs server CPU. | `_play()` is `@st.fragment(run_every=0.5)`: it rebuilds the whole SVG in Python and sends it via `st.html` twice a second. Cars jump between samples (`positions_at` snaps to the nearest 0.5 s point; no interpolation). |
| D6 | Each frame gets slower the longer the session. | `positions_at` runs `np.abs(times - t).argmin()` over the entire tidy timeline (212 k rows for a race) plus a boolean filter — O(N) per frame. `order_at` regroups all laps every frame. |
| D7 | Qualifying and practice replays order drivers wrongly. | `order_at` sorts by `laps.Position`, which FastF1 leaves NaN outside races, then falls back to "most laps first". |
| D8 | The gaps and intervals shown on the TV timing screen are not available to anything. | FastF1's `_extended_timing_data()` returns `(laps_data, stream_data, session_split_times)`; `Session._load_laps_data` **discards `stream_data`** (per-timestamp `Position`, `GapToLeader`, `IntervalToPositionAhead`). It is already on disk (`ff1_cache/<year>/<event>/<session>/_extended_timing_data.ff1pkl`), so using it costs no download. `session.track_status` and the qualifying segment boundaries (`session._session_split_times`) are loaded too and never passed on. |

**UI problems found in the same pass** (fixed by §4, measured against §5):

- 50+ emoji in interface strings: every tab label in `app.py` (`"📊 Telemetry"`, `"⚔️ Head-to-Head"`, …), buttons (`"💾 Save Session for Replay"`, `"⏹️ Stop Live"`), headings (`"🏆 Session & All-Time Records"`), the page title (`st.title("🏎️ Formula 1 Telemetry Dashboard")`), the telemetry sub-tabs (`"🚀 DRS"`), the weather metrics (`"🌡️ Air"`), and emoji used as status indicators in `ui/layout.py` (`TRACK_STATUS`: coloured circles; flag icons; `"🔧 PIT OUT"` chart labels).
- Two titles before any data (`st.title` + caption, then the dashboard header bar names the session again).
- Session selection is an always-open expander in the main flow; each dropdown change starts a load.
- The dark dashboard is embedded in Streamlit's default (possibly light) theme; `ui/theme.py` imports Inter and JetBrains Mono from Google Fonts at runtime — an external request and the most common default typeface of generated interfaces.
- A developer-facing "records and cache statistics" panel is expanded by default in the middle of the page.
- Nine tabs rendered eagerly; two different track maps (Plotly and SVG).

---

## 2. Target design and plan iterations

### 2.1 Target design

1. **The session dict carries the time-stamped streams it needs** (REPLAY-02): `timing_stream`, `track_status`, `session_info.replay_clock`, `session_info.segment_starts`, `session_info.session_start`. `REPLAY_SCHEMA_VERSION` 6 → **7**.
2. **One pure module owns time semantics** — `processing/replay_model.py` (REPLAY-03):
   - `tower_series(session_data)`: vectorised change-point series per driver **per field** (position, gap, interval, lap, last lap, best lap, sectors, tyre, tyre age, pit count, status). Built once per session with `merge_asof` / `searchsorted`, never by looping `build_timing_rows` over time.
   - `snapshot_at(session_data, t, series=None)`: a unified-dict-shaped snapshot of the session as it stood at `t`, drawn by the existing renderers with small snapshot-mode changes. At `end` its tower **order** equals the final result; the full end-of-session view (gaps and status from `results`) stays the Results page.
   - `events(session_data, series)`: safety car, VSC, red flag, pit stops, retirements, fastest-lap changes, chequered flag.
3. **The replay is the main view** (REPLAY-04, UI-03): for historical and saved sessions the default page is *Replay*, rendered from `snapshot_at(cursor)` with precise step controls; the old dashboard becomes the *Results* page, unchanged.
4. **Smooth playback runs in the browser** (REPLAY-05): a `st.components.v2` component gets one precomputed payload and animates the whole replay screen at display refresh rate — interpolated car motion, animated position changes, a timeline with safety-car periods shaded. It reports its cursor to Python only on pause, seek or focus change.
5. **The interface follows one written guideline** (§5): emoji removed, one accent colour, self-contained typography, dense but calm tables, plain labels.

### 2.2 Plan iterations (why the design looks like this)

- **v1** — call `build_timing_rows(snapshot_at(t))` at every event time and ship the rows to the browser. Rejected: a race has ~28 000 timing-stream rows; at ~100 ms per call that is most of an hour of CPU per session.
- **v2** — vectorised change-point series, with `snapshot_at` reading **the same series**. One source of truth, so the server view and the browser player cannot disagree; tests compare them at random times.
- **v3** — the server-rendered step view (REPLAY-04) comes **before** the browser player, so a correct replay exists early and the component item — the only one that cannot be fully tested with `AppTest` — starts from a verified model. The server view remains the fallback (`F1_REPLAY_PLAYER=server`). Payload trimmed: positions projected into the SVG viewBox in Python and quantised to Int16 (max value 10 000 = 1000 units × 10, safe) — ≈0.93 MB raw / ≈1.24 MB base64 for 20 cars × 97 min at 2 Hz; tower fields stored as separate series per field (repeating all 13 fields at every change point would cost ~2 MB on its own). Budget ≤ 4 MB JSON.
- **v4** — after an independent review against the code and the installed libraries: the player only reports its cursor on pause/seek/focus (in components v2 every `setStateValue` reruns the script and re-serialises the multi-MB payload); "Final result" switches to the Results view instead of pretending the snapshot equals it; `events()` moved into REPLAY-03 so REPLAY-04 can use it; `segment_starts` corrected to FastF1's real `_session_split_times` shape; retirement status derived only from data stamped ≤ t.
- **v5** — the UI work is governed by an explicit guideline (§5) with automated checks (UI-00, §5.12), after reviewing how generated interfaces are recognised (emoji as icons, purple gradients, glows, identical rounded cards, Inter by default, filler copy) and how established data-heavy sites present timing data.
- **Deliberately not done:** inventing a safety-car position (f1-race-replay simulates one 500 m ahead of the leader; the data holds no SC location, so this project shows the SC state as a flag and a track tint), and ordering the leaderboard by GPS progress (f1-race-replay's README says it is wrong in the first corners and during pit stops; the official timing stream is used instead).

---

## 3. Items — replay and repo hygiene

- [ ] **REPO-14** · P0 · S — **Line-ending churn: ~55 files "modified" with no content change**
  - Files: new `.gitattributes`; every tracked text file.
  - Problem: on 2026-09-23 `git status` lists ~55 modified files (`app.py`, all of `data/`, `processing/`, `ui/`, the docs… — the exact count drifts, don't hardcode it) with equal insertions and deletions; `git diff --ignore-cr-at-eol --stat` shows only genuinely edited files (none apart from this document's own revision) — the working tree has CRLF, the index LF, and there is no `.gitattributes`. Any commit made now would either carry thousands of noise lines or hide real edits among them.
  - Fix: add
    ```gitattributes
    * text=auto eol=lf
    *.gz binary
    *.parquet binary
    *.ff1pkl binary
    *.png binary
    ```
    then `git add --renormalize .`. Because the index is already LF, this stages nothing but `.gitattributes` — that is expected, not a failure. Commit **only** that as `chore(REPO-14): normalise line endings`; afterwards the CRLF files stop showing as modified (on Windows, `git checkout -- .` or a fresh checkout converts the working tree if they still do, **after** confirming `git diff --ignore-cr-at-eol` is empty so no real edit is lost). Commit the rewritten `IMPROVEMENTS.md` separately afterwards as `docs: rewrite IMPROVEMENTS.md as the replay and UI plan`. Add `_to_delete/` to `.gitignore` (it holds a stale lock file moved aside on 2026-09-23; delete the folder).
  - Acceptance: after the commit, `git status --short` is empty (or lists only genuinely edited files); `tests/test_repo_hygiene.py` asserts `.gitattributes` contains `eol=lf`.

- [ ] **REPLAY-01** · P0 · M — **Replay clock, O(1) position lookup and interpolation**
  - Files: `processing/replay.py`, `data/fastf1_adapter.py` (`get_position_timeline`), `ui/replay_view.py`, `tests/test_replay_playback.py`, `tests/test_replay_view.py` (its asserts of a global `replay_cursor == 0.0` are **rewritten** — the cursor becomes per-session and starts at `lights_out`), `tests/perf/test_hot_paths.py`.
  - Problem: D4, D5 (snapping), D6.
  - Fix:
    1. Add `@dataclass(frozen=True) class ReplayClock: start: float; lights_out: float; end: float; step: float = DEFAULT_STEP_SECONDS` (seconds of session time) and `replay_clock(laps, timeline, session_type, session_start=None) -> ReplayClock`:
       - `lights_out` = min of the **non-NaT** `LapStartTime` of `LapNumber == 1` for `R`/`S`; otherwise `session_start` if given; otherwise the min non-NaT `LapStartTime` of any lap; otherwise the timeline start.
       - `end` = `max(laps.Time) + 60`, capped at the timeline end; `start` = timeline start.
       - `session_start` comes from `session.session_start_time` (REPLAY-02 stores it as `session_info["session_start"]`, seconds).
    2. Add `PositionCube`: built once from the tidy timeline — `times: np.ndarray (F,)`, `codes: list[str] (D)`, `xy: np.ndarray (F, D, 2) float32` with NaN where a driver has no sample. `positions_at(cube, t)` computes `i = (t - t0) / step`, linearly interpolates rows `floor(i)` and `floor(i)+1`, drops NaN drivers. Keep the old `positions_at(timeline, t)` signature working by building/caching the cube (so existing callers/tests keep passing) — or update callers; do not leave two implementations.
    3. `order_at` must not be called per frame any more once REPLAY-03 lands; until then leave it.
    4. `ui/replay_view.py`: default cursor = `clock.lights_out`, stored under a per-session key (`f"replay_cursor:{session_key}"`) so switching sessions resets it; a "Lights out" button (text label, §5.6).
    5. Fill the Q/FP gap: in `get_position_timeline`, when a driver's first lap has a NaT `LapStartTime`, take that driver's positions from `session.pos_data[driver_number]` between `session_start_time` and their last lap `Time` instead of `laps.get_pos_data()` (position data only — never `.add_distance()` it, see CLAUDE.md). Optional flag `include_pre_race=False` does the same for races to add the grid/formation lap; keep it off by default (payload size).
  - Acceptance:
    - `positions_at` on a 22-driver × 14 000-frame cube < **2 ms** (perf test in `tests/perf/`).
    - A synthetic driver at x=0 at t=0 and x=10 at t=0.5 reads x=5 at t=0.25 (interpolation, not snapping).
    - Race fixture with lap 1 `LapStartTime = 3600 s` → `lights_out == 3600`; a fixture whose lap-1 `LapStartTime` is NaT for one driver still gets the min of the others; replay view's initial cursor equals `lights_out` (AppTest).
    - Unit test with a fake session: a driver whose first lap has NaT `LapStartTime` gets timeline rows from `session_start` (Q/FP out-lap no longer missing).
    - Existing `tests/test_replay_playback.py` / `tests/test_replay_view.py` updated, all green.

- [ ] **REPLAY-02** · P0 · M — **Carry the timing stream, track status and segment boundaries in the session dict**
  - Files: `data/fastf1_adapter.py` (new `get_timing_stream`, `get_track_status`, `get_segment_starts`), `data/source_manager.py` (`_load_fastf1_session`, `REPLAY_SCHEMA_VERSION`, `_finalise_replay` defaults), `processing/time_utils.py` (new `parse_gap`), `CLAUDE.md` (contract block), `tests/test_fastf1_adapter.py`, `tests/test_replay_format.py`, `tests/test_integration_network.py`.
  - Problem: D8.
  - Fix:
    1. `get_timing_stream(session) -> DataFrame[Time: float s, Driver: str (acronym), Position: Int64, GapToLeader: str, IntervalToPositionAhead: str]`:
       ```python
       import warnings
       try:
           from fastf1 import _api as ff1_api          # private; see note
           with warnings.catch_warnings():
               warnings.simplefilter("ignore")
               _, stream, _ = ff1_api._extended_timing_data(session.api_path)
       except Exception as exc:                         # degrade, never crash the load
           logger.warning("Timing stream unavailable: %s", exc)
           return pd.DataFrame(columns=TIMING_STREAM_COLUMNS)
       ```
       Map the racing number in `stream["Driver"]` to the acronym via `session.results[["DriverNumber", "Abbreviation"]]`; convert `Time` with `to_seconds`; `Position` arrives as plain `int64` — cast to `Int64`; keep the raw gap strings (they carry "LAP n"/"n L" semantics) and add parsed numeric columns `GapSeconds`, `GapLapsDown`, `IntervalSeconds`, `IntervalLapsDown` via `parse_gap` (below). The call hits FastF1's own cache (`_extended_timing_data.ff1pkl`), so no extra download for a session already loaded. Scale: 2023 Bahrain R has 28 475 stream rows.
       *Note:* `fastf1._api` is private and `fastf1.api` emits a `UserWarning` on import. Pin the expected columns with a test that fails loudly if FastF1 changes them, and keep the lap-based fallback of REPLAY-03 working when the frame is empty.
    2. `processing/time_utils.parse_gap(value) -> tuple[float | None, int | None]` = `(seconds, laps_down)`. Table-driven test must cover: `"+1.234"`→(1.234, 0); `"12.5"`→(12.5, 0); `"LAP 23"`→(0.0, 0) (the leader's gap **and** the leader's interval); `"1 L"` (the form FastF1's cache holds — 2 865 times in 2023 Bahrain R), `"1L"`, `"2 LAPS"`, `"+1 LAP"`→(None, n); `""`, `None`, `NaN`→(None, None). (The live fixture `tests/fixtures/live/TimingData.jsonl.gz` shows `"LAP 1"`, `"+60.928"` and `""`; FastF1's stream never stores `""` — its parser skips empty strings and forward-fills.)
    3. `get_track_status(session) -> DataFrame[Time: float s, Status: str, Message: str]` from `session.track_status`. `get_segment_starts(session, session_type) -> list[float]`: `[]` unless `session_type in {"Q", "SQ"}`; otherwise read `getattr(session, "_session_split_times", None)` (a list of Timedeltas — real values: 2023 Bahrain Q `[0, 2758.7 s, 4138.7 s]`, 2023 Bahrain R `[0, 1 day, 1 day]`), drop entries ≥ 1 day, and replace element 0 with `session.session_start_time` (element 0 is 0, not the Q1 start). Also store `session_info["session_start"]` = `to_seconds(session.session_start_time)`.
    4. Unified dict: add `"timing_stream"`, `"track_status"` (DataFrames) and `session_info["segment_starts"]`, `session_info["replay_clock"]` (a plain dict of `ReplayClock`, JSON-safe so replays persist it). Bump `REPLAY_SCHEMA_VERSION` **6 → 7**; `_finalise_replay` defaults missing keys to empty frames / `[]` / `None` so schema ≤ 6 replays still load.
    5. Update the contract block in `CLAUDE.md` with the three new keys and their columns.
  - Acceptance:
    - Offline: a fake session whose `_extended_timing_data` is monkeypatched to return a recorded-shape stream (`Time` Timedelta, `Driver` racing-number strings) yields acronyms, float seconds and parsed gaps; a raising fake yields an empty frame with the right columns and a WARNING log.
    - Replay round-trip (`tests/test_replay_format.py`) keeps `timing_stream`, `track_status`, `replay_clock`; a schema-6 bundle loads with empty defaults.
    - Offline: `get_segment_starts` on a fake session with `_session_split_times = [0, 1 day, 1 day]` and type `R` → `[]`; with `[0, 2758.7 s, 4138.7 s]`, `session_start_time = 1000 s` and type `Q` → exactly `[1000.0, 2758.7, 4138.7]`.
    - Network (`F1_NETWORK_TESTS=1`): 2023 Bahrain R → ≥ 20 drivers, ≥ 20 000 stream rows, VER `Position == 1` on its last row, `segment_starts == []`; `track_status` non-empty; 2023 Bahrain Q → 3 strictly increasing `segment_starts`, the first equal to `session_start`.
  - Depends on: REPLAY-01 (for `ReplayClock`).

- [ ] **REPLAY-03** · P0 · L — **`processing/replay_model.py`: tower change-point series and `snapshot_at(t)`**
  - Files: new `processing/replay_model.py`; `processing/timing.py` (`build_timing_rows` learns `standings` and snapshot mode); `data/fastf1_adapter.py` (`get_laps` must also keep `Sector1SessionTime`, `Sector2SessionTime`, `Sector3SessionTime` — today it drops them); `ui/dashboard.py`; new `tests/replay_fixtures.py` (synthetic sessions); new `tests/test_replay_model.py`; `tests/test_timing.py`.
  - Problem: D1, D2, D7 — all renderers read end-of-session aggregates.
  - Fix:
    1. **`tower_series(session_data) -> TowerSeries`** — for each driver, change-point arrays `t[]` + `value[]` per field; the value at time `t` is `value[searchsorted(t_arr, t, "right") - 1]` (or the field's default before the first point). Fields and sources:

       | Field | Race / Sprint | Qualifying / Practice |
       |---|---|---|
       | `position` | `timing_stream.Position` (as-of) ; fallback: rank by (laps completed ↓, crossing time of last completed lap ↑) | rank by best valid lap so far (`Deleted`/`IsAccurate` excluded), ties by who set it first |
       | `gap`, `interval` (display strings + seconds + laps_down) | `timing_stream` via `parse_gap`; fallback: leader's crossing `Time` at the driver's lap count vs the driver's (timing-line gap), `+N LAP(S)` when lapped; display strings per §5.7 | best-so-far delta to P1 / car ahead |
       | `lap` | laps completed + 1 (capped at total laps; "FIN" after the flag) | laps completed |
       | `last_lap`, `best_lap` (+ `last_is_pb`, `last_is_sb`) | from laps whose `Time ≤ t` | same |
       | `sectors` (current lap) | `Sector{n}Time` revealed when `Sector{n}SessionTime ≤ t`; cleared when the next lap starts | same |
       | `tyre`, `tyre_age`, `tyre_new` | the lap in progress' `Compound`, `TyreLife`, `FreshTyre` (changes at `PitOutTime`) | same |
       | `pits` | count of `PitInTime ≤ t` (race only) | — |
       | `status` | `IN PIT` while `PitInTime ≤ t < PitOutTime`; `FIN` once the driver has crossed the line after the leader completed the final lap; `OUT` from the driver's last position sample + 5 s **if they have no `FIN`** (derived from data stamped ≤ t only — never from final `results`); else `ON TRACK` | `IN PIT` / `ON TRACK` / `OUT` (no running car after the last sample); qualifying `KO` only after the segment the driver was eliminated in has ended (`segment_starts`) |

       Build with `pd.merge_asof` / `np.searchsorted`; store **each field as its own change-point series** (a point only when that field's value changes) — this is also the payload format of REPLAY-05; no Python loop over time steps.
    1b. **`events(session_data, series) -> list[tuple[float, str, str]]`** (also in `replay_model.py`, used by REPLAY-04's "Jump to…" and REPLAY-05's timeline): SC / VSC / red-flag starts from `track_status` (codes 4, 6, 5), pit stops (`PitInTime`), retirements (`OUT` transitions), fastest-lap changes, and the chequered flag (from `race_control` `Flag == "CHEQUERED"` — `track_status` never carries it).
    2. **`snapshot_at(session_data, t, series=None) -> dict`** returns a *new* unified dict:
       - `laps`: rows with `Time ≤ t`, plus one `IsInProgress=True` row per running driver carrying the lap in progress (`LapTime` NaN, revealed sectors only). `processing.timing._laps_completed` already ignores `IsInProgress` rows (already in `processing/timing.py`), **but nothing else does**: `build_timing_rows` computes `last_lap` from `lap_seconds.iloc[-1]`, which would read "—" for every running driver — see step 3.
       - `standings`: new DataFrame `Driver, Position, Gap, GapSeconds, LapsDown, Interval, IntervalSeconds, Status, Pits` taken from the series at `t`.
       - `results`: **empty frame** (final classification must not leak).
       - `stints`: stints whose start lap ≤ current lap, the current one clipped to the current lap.
       - `race_control`, `weather`, `track_status`: rows with `Time ≤ t`.
       - `telemetry`/`location`/`dashboard_*`: unchanged objects (analysis context), but `session_info["replay_time"] = t` tells the dashboard not to draw fastest-lap dominance or micro-sector strips (they come from laps set later in the session).
       - `session_info`: copy plus `replay_time`, `current_lap`, `total_laps`, `elapsed` (= `t - lights_out`).
    3. `processing/timing.build_timing_rows`:
       - when `session_data` has a non-empty `standings`, race/sprint order, gap, interval and status come from it (not from `results`);
       - when `session_info.replay_time` is set: never read `results`; skip `segment_states` / micro-sector strips entirely (they come from `dashboard_telemetry`, i.e. fastest laps from the whole session — hiding them in the UI is not enough); take the **sector cells from the lap in progress** (revealed sectors) or else the last completed lap, not from the fastest lap;
       - compute `last_lap`, `best_lap`, `best_sectors` and speed traps over rows **without** `IsInProgress` (use the in-progress row only for the revealed sectors and the current tyre);
       - when `standings` is absent the current behaviour is unchanged (live and full-session views keep working).
    4. `ui/dashboard.py` in snapshot mode (`replay_time` set): header clock = `format_clock(elapsed)` labelled "Race time" (or segment time in Q); flag = last `track_status.Status` ≤ t mapped through the existing `ui/dashboard.TRACK_STATUS_FLAGS` onto `ui/theme.FLAG_STATES` keys (`GREEN`, `YELLOW`, `SAFETY CAR`, `RED`, `VSC`; code 7 → `VSC`), except that a `Track`-scoped `CHEQUERED` race-control message ≤ t wins; weather = last row ≤ t; map = outline + corners + car markers from `positions_at(t)` (no dominance layer).
  - Acceptance (synthetic fixtures in `tests/replay_fixtures.py`: a 3-driver 5-lap race where B passes A on lap 3, C pits on lap 4 and A retires on lap 5; a 22-driver qualifying with 3 segments; a practice):
    1. `build_timing_rows(snapshot_at(race, end))` **order** == final `results` order (only the order is compared; gaps/status on the Results page come from `results` and may be formatted differently).
    2. Between the end of lap 2 and the end of lap 3 the order is A, B, C; after lap 3 it is B, A, C.
    3. **No-future-leak property:** for 20 random `t`, build a copy of the session where every row stamped after `t` (laps by `Time`, stream, positions, race control, weather, track status) is replaced with garbage **and** `results` is replaced entirely; rebuild `tower_series` from that copy and assert `snapshot_at(t)`'s tower rows, header flag and weather equal those from the original session.
    4. C is `IN PIT` between its `PitInTime` and `PitOutTime`, its tyre changes at `PitOutTime`, `pits` goes 0 → 1.
    5. A is `ON TRACK` until its last position sample + 5 s, `OUT` afterwards (not from lap 1).
    6. Qualifying: nobody is KO before Q1 ends; after Q1 ends the bottom 6 of 22 are; the 10/6/6 partition appears only after Q2 ends.
    7. `len(snapshot_at(t).race_control)` is non-decreasing in `t`.
    8. `snapshot_at` on a synthetic 22-car 57-lap race < **150 ms**; `tower_series` < **2 s** (perf tests).
    9. Network: 2023 Bahrain R, `t` = leader's lap-10 `Time` + 1 s → tower top 3 equals FastF1 `laps[LapNumber == 10]` top 3 by `Position`; VER–PER gap within 0.5 s of the difference of their lap-10 `Time`.
  - Depends on: REPLAY-02.

- [ ] **REPLAY-04** · P0 · M — **Make the replay the main view (server-rendered, scrub/step driven)**
  - Files: `app.py`, `ui/replay_view.py` (rewrite), `ui/dashboard.py`, `tests/test_replay_view.py`, `tests/test_app_sources.py`.
  - Problem: D3 — the replay is a secondary tab and the prominent dashboard shows the end result.
  - Fix:
    1. For non-live sessions, the Replay page (UI-03) renders a **transport bar** then `render_dashboard(snapshot_at(session, cursor, series))`. `series` is built once per session and cached (`@st.cache_data(hash_funcs=…)` keyed by the session key, or stored next to the session in the runtime cache).
    2. Transport bar (text buttons per §5.6/§5.9): `Lights out` · `-30s` · `-5s` · `+5s` · `+30s` · `Previous lap` · `Next lap` · `Jump to` select (from `replay_model.events()` — REPLAY-03) · "Final result" (**switches to the Results view**, i.e. `render_dashboard(full dict)` — the snapshot at `end` has no `results` by design) · a session-time `st.slider` (label shows `format_clock(cursor - lights_out)` and `Lap n/N` next to it). Cursor lives in `st.session_state[f"replay_cursor:{session_key}"]` so switching sessions resets it.
    3. Keep the existing fragment playback as **fallback** at 1 frame/s over the full dashboard (it will be replaced as the default by REPLAY-05; keep it behind `F1_REPLAY_PLAYER=server`).
    4. Delete the old Replay tab once this view is the default (no duplicate replay UIs).
  - Acceptance (AppTest with the synthetic race fixture as a stub manager, pattern of `tests/test_app_sources.py`):
    - First render: cursor == `lights_out`; header shows race time `0:00:00` and `Lap 1/5`; tower order is the grid order from the stream, **not** the final result.
    - `+30 s` moves the cursor by exactly 30 (clamped to `end`); "Next lap" lands 1 s after the leader's next lap `Time`; "Final result" renders the Results view, whose tower equals today's `render_dashboard(full dict)` output.
    - "Jump to… Pit stop — C" puts C's status at `IN PIT`.
  - Depends on: REPLAY-03, UI-03.

- [ ] **REPLAY-05** · P1 · L — **Browser-side replay player (`st.components.v2`) — smooth playback without reruns**
  - Files: new `processing/replay_payload.py`; new `ui/components/replay_player/{__init__.py, player.html, player.css, player.js}`; `ui/replay_view.py`; new `scripts/preview_replay_player.py`; new `tests/test_replay_payload.py`; optional `tests/e2e/test_replay_player.py`.
  - Problem: D5 — Python-side frame rendering caps playback at ~2 fps with flicker and server load per viewer.
  - Fix:
    1. **Payload (Python, pure, fully tested)** — `build_replay_payload(session_data, series, clock) -> dict`:
       ```text
       {
         "v": 1, "session_key": "2023-Bahrain Grand Prix-R",
         "clock":   {"start": s, "lights_out": s, "end": s, "step": 0.5, "total_laps": 57},
         "track":   {"view": [1000, 760], "path": "M…Z", "sf": [x1,y1,x2,y2],
                     "corners": [{"x":…, "y":…, "label":"4"}]},              # projected with ui/track_map's own transform
         "drivers": [{"code":"VER","number":"1","name":"Max Verstappen","team":"Red Bull Racing","colour":"#3671c6"}],
         "pos":     {"t0": s, "step": 0.5, "frames": F, "drivers": D,
                     "xy_b64": "<Int16 LE, shape [F][D][2], viewBox units ×10, -32768 = absent>"},
         "tower":   {"<code>": {"pos": [[t…],[v…]], "gap": [[t…],[v…]], "int": …, "lap": …, "last": …, "last_flag": …,
                                "best": …, "tyre": …, "age": …, "new": …, "pits": …, "status": …}},   # one change-point series per field
         "flags":   [[t, "GREEN"|"YELLOW"|"SAFETY CAR"|"VSC"|"RED"|"CHEQUERED"], …],   # the FLAG_STATES keys in ui/theme.py
         "rcm":     [[t, lap, category, flag, message], …],
         "weather": [[t, air, track, humidity, rain, wind_kmh, wind_dir], …],
         "leader_laps": [[t, lap], …],
         "events":  [[t, "sc"|"vsc"|"red"|"pit"|"out"|"fastest"|"flag", "label"], …]
       }
       ```
       - Positions: the timeline window (`[clock.start, clock.end]`), rotated + projected with the **same** functions `ui/track_map.build_track_svg` uses (extract `track_transform(location, circuit_info)` so map and payload share one projection), multiplied by 10, rounded, `astype("<i2")` (max 10 000 — safe), base64.
       - Tower arrays are `TowerSeries` serialised (display strings precomputed in Python and formatted exactly per §5.7: `"LEADER"`, `"+1.234"`, `"+1 LAP"`, `"1:32.456"`, `"–"` for missing).
       - Budget: ≤ **4 MB** of JSON for a synthetic 22-car, 2-hour race (test). Expect ≈1.3 MB positions + well under 1 MB tower with per-field series.
       - Cache per session key (`@st.cache_data` on a function taking the session key + a hashable digest).
    2. **Component (JS, vanilla ES module, no build step, no CDN)** — registered once at import:
       ```python
       _DIR = Path(__file__).parent
       replay_player = st.components.v2.component(
           "f1_replay_player",
           html=(_DIR / "player.html").read_text(encoding="utf-8"),
           css=(_DIR / "player.css").read_text(encoding="utf-8"),
           js=(_DIR / "player.js").read_text(encoding="utf-8"),
       )
       def render_replay_player(payload: dict, key: str, height: int = 760):
           return replay_player(key=key, data=payload, height=height,
                                default={"cursor": payload["clock"]["lights_out"], "focus": None},
                                on_cursor_change=lambda: None, on_focus_change=lambda: None)
       ```
       JS responsibilities, and nothing else:
       - `export default function({ data, parentElement, setStateValue })` keeps its per-instance state in a `WeakMap` keyed by `parentElement`; rebuilds DOM only when `data.session_key` changes; returns a cleanup that cancels `requestAnimationFrame` and removes listeners. `parentElement` is a **ShadowRoot** (styles isolated, the default): query inside it, attach keyboard listeners to a focusable wrapper `<div tabindex="0">` inside it (focus it on click), and write e2e selectors that pierce the shadow root.
       - Decode `xy_b64` once into an `Int16Array`; car position at cursor = linear interpolation of frames `⌊i⌋` and `⌊i⌋+1`; hide absent cars.
       - Tower field at cursor = binary search in each driver's `t` array; order rows by `pos`; rows are absolutely positioned and moved with `transform: translateY(rank * rowHeight)` + `transition: transform 350ms ease` so overtakes animate; re-render text only when a value changed.
       - Header (event, session, `Lap n/N`, race time, flag chip, weather), race-control ticker (last 4 messages ≤ cursor), timeline bar with lap ticks, SC/VSC (amber) and red-flag (red) shading, event markers (click = seek).
       - Controls: play/pause, ±5 s, ±30 s, prev/next lap, speed `0.5x 1x 2x 4x 8x 16x 32x 64x`; keyboard Space, ←/→ (±5 s), Shift+←/→ (±30 s), `[`/`]` (lap), `1`–`8` (speed), `F` (toggle follow focused driver). Click a tower row or a car → focus (others dimmed, focused car larger + halo).
       - `setStateValue("cursor", t)` **only** on pause, on a seek while paused, and on playback reaching the end; `setStateValue("focus", code)` on focus change. Never while playing: in components v2 every state change reruns the Streamlit script, which re-serialises and re-hashes the multi-MB `data` payload.
       - Styling exactly per UI-04 and §5 (tokens, Titillium/system type, no emoji, no gradients/shadows/glow, icons only from §5.8); respects `prefers-reduced-motion`. `test_ui_guideline` scans the component files.
    3. `ui/replay_view.py`: the Replay page mounts the player (default) and, below it, the server snapshot panels that benefit from Python (sector cards, lap chart marker) driven by `st.session_state[key].cursor`. `F1_REPLAY_PLAYER=server` keeps REPLAY-04's view as the fallback.
    4. `scripts/preview_replay_player.py [--fixture | --year 2023 --gp Bahrain --session R]` writes `replay_preview.html` (self-contained: inlines player.css/js and the payload, with a 20-line shim that calls the default export on a `<div>`), so a human or agent can open it in a browser with no Streamlit.
  - Acceptance:
    - `tests/test_replay_payload.py`: required keys/types; JSON size budget; decoding `xy_b64` in Python reproduces projected positions within 0.1 viewBox units; for 50 random `t`, the tower value looked up from the payload (Python re-implementation of the JS binary search, ~10 lines) equals `snapshot_at(t)`'s tower (order, gap string, tyre, status).
    - **Verify first** whether `AppTest` renders a components-v2 element on 1.59. If it does: assert the Replay page mounts it with `key=f"replay_player:{session_key}"` and `st.session_state[key].cursor` defaults to `lights_out`. If it does not: unit-test `ui/replay_view` with the cursor injected (a `cursor=` parameter) and assert the snapshot panels render at that time; record which path was taken in `tasks.md`.
    - `player.css` defines the §5.4 tokens once in `:root` and the three breakpoints of UI-04; `test_ui_guideline` passes on the component files.
    - Optional e2e (`-m e2e`, skipped when Playwright is missing): open `replay_preview.html`, press Space, wait 2 s, assert the header clock advanced and at least one car's `transform`/`cx` changed; save a screenshot to `test-artifacts/replay.png`. Also take screenshots at 1440×900 and 390×844 and assert no horizontal scroll (`document.documentElement.scrollWidth <= innerWidth`).
    - Manual (record in `tasks.md`): 2023 Bahrain R at 16× plays without flicker; the Streamlit process performs no script reruns between pause/seek events (log a line at the top of `main()` at DEBUG and watch it).
  - Depends on: REPLAY-03, REPLAY-04, UI-04.

- [ ] **REPLAY-06** · P1 · M — **Qualifying & practice replays**
  - Files: `processing/replay_model.py`, `processing/replay_payload.py`, player JS, `tests/test_replay_model.py`.
  - Fix: header shows the running segment (`Q1`/`Q2`/`Q3` from `segment_starts`) and time elapsed in it; tower ordered by best valid lap **in the current segment** (Q) or in the session (FP/SQ as applicable); "on a flying lap" marker for a driver whose lap started after `PitOutTime` and hasn't completed; purple/green flash when a lap completes as session best / personal best (`last_flag`); eliminated drivers move to the partition only when their segment ends.
  - Acceptance: synthetic qualifying fixture — at a time inside Q2, Q1-eliminated drivers sit under "Eliminated in Q1" and the rest are ordered by Q2 best; a driver who completes a faster lap mid-Q2 moves up at that lap's `Time`, not before.
  - Depends on: REPLAY-03 (and REPLAY-05 for the player side).

- [ ] **REPLAY-07** · P2 · S — **Track state on the map**
  - Fix: track ribbon tinted with the flag colour at 30 % under SC/VSC and red under a red flag (from `flags`); an `SC` / `VSC` / `RED` chip on the map — no invented SC marker (no SC position in the data). Optional: marshal-sector yellows from race-control messages with `Scope == "Sector"` using `session.get_circuit_info().marshal_sectors` (X/Y/Distance) to colour the nearest stretch — add `marshal_sectors` to `circuit_info` if you do this.
  - Files: `processing/replay_payload.py`, `ui/components/replay_player/player.js`, `player.css`, optionally `data/fastf1_adapter.py` (`get_circuit_info`).
  - Acceptance: payload `flags` for a synthetic SC period produces a `SAFETY CAR` state between its start and end; unit test for the sector → track-slice mapping if implemented.
  - Depends on: REPLAY-05.

- [ ] **REPLAY-08** · P2 · S — **Old replays and missing streams degrade honestly**
  - Fix: when `timing_stream` is empty (schema ≤ 6 bundle, or FastF1 failed), the model uses the timing-line fallback (REPLAY-03 table) and the header shows a small "gaps estimated at timing lines" note; when `positions` (the existing position-timeline key, schema 6) is empty the player shows the tower and timeline without the map rather than refusing to open.
  - Files: `processing/replay_model.py`, `processing/replay_payload.py`, `ui/replay_view.py`, player JS, `tests/test_replay_model.py`, `tests/test_replay_format.py`.
  - Acceptance: a schema-6 replay fixture opens the Replay page without errors and shows the note; a fixture without positions renders the tower.
  - Depends on: REPLAY-05.

- [~] **REPLAY-09** · P3 · L · network — **Exact timing-screen replay from F1's static archive through the live pipeline** (optional, after REPLAY-05)
  - **Blocked:** it depends on the live snapshot builder being proven against a running session (LIVE-01), which is out of scope for this loop. Leave it for a later plan.
  - Files: `scripts/capture_fixture.py`, new `scripts/import_archive_replay.py`, `data/live_recorder.py`.
  - Idea: `scripts/capture_fixture.py` already knows the archive URLs (`https://livetiming.formula1.com/static/<year>/<meeting>/<session>/<Topic>.jsonStream`). Feed `TimingData`, `TimingAppData`, `TrackStatus`, `RaceControlMessages`, `WeatherData`, `LapCount` through `SignalRLiveAdapter.handle_message` on a virtual clock (the same path `data/live_recorder.replay_recording` uses) and sample `poll_live_data()` every second into keyframes. Gains: official mini-sector segment colours (`Segments[].Status`), exact broadcast tower. Cost: one download per session, more CPU. Only worth it once the live snapshot builder is proven (LIVE-01).
  - Acceptance: for 2023 Bahrain R, keyframe order at the end of lap 10 matches REPLAY-03's.

- [ ] **REPLAY-10** · P3 · M — **Focused-driver card in the player**
  - Files: `processing/replay_payload.py`, player JS/CSS, `ui/replay_view.py`.
  - Fix: when a driver is focused, a card shows last 5 lap times (from the tower series), current tyre + age, pits, gap-to-car-ahead trend sparkline (last 5 min), and an "Analyse this lap" text button that sets `st.session_state` for the Analysis page (UI-07).
  - Depends on: REPLAY-05, UI-07.

---

## 4. Items — UI

> Every item here is measured against §5. UI-00 comes first because it installs the automated checks that keep the rest honest.

- [ ] **UI-00** · P1 · M — **Remove every emoji and install the guideline checks**
  - Files: `app.py`, `ui/layout.py`, `ui/replay_view.py`, `ui/dashboard.py`, `ui/theme.py`, new `tests/test_ui_guideline.py`, tests that assert on emoji labels (`tests/test_replay_view.py`, `tests/test_live_view.py`, `tests/test_app_sources.py`, `tests/test_session_selector.py` — update their expected strings).
  - Problem: 50+ emoji in interface strings (inventory in §1). Emoji are also used as **state indicators** (`ui/layout.TRACK_STATUS` circles, flag icons, `"🔧 PIT OUT"` chart text), where they carry meaning that disappears for anyone who can't tell the colours apart.
  - Fix:
    1. Replace each emoji label with the plain text from §5.9 (tabs `Telemetry`, `Head-to-head`, `Lap times`, `Positions`, `Tyres`, `Track` (removed in UI-03), `Weather`, `Race control`; telemetry sub-tabs `Speed`, `Throttle`, `Brake`, `RPM`, `Gear`, `DRS`; buttons `Save session for replay`, `Start live stream`, `Stop live`, `Record raw stream`, `Stop recording`, `Clear buffers`, `View buffered data`, `Play`/`Pause`, `Lights out`; weather metrics `Air`, `Track`, `Humidity`, `Wind`, `Pressure`).
    2. Replace emoji state indicators with text chips rendered through one helper `ui/theme.status_chip(label, state)` using the flag colours: `TRACK_STATUS` becomes `{code: (flag_key, label)}`; race-control flag column shows the flag word, not an icon; the lap chart's pit-out annotation reads `PIT OUT`.
    3. Add `tests/test_ui_guideline.py` implementing all checks in §5.12. Checks that current code cannot pass yet (e.g. hex literals outside `ui/theme.py`, Google Fonts URL) are marked `xfail(strict=True)` with the item that will fix them (UI-01 for URLs/`st.title`, UI-05 for hex literals), so they flip to real assertions when that item lands.
  - Acceptance: `tests/test_ui_guideline.py::test_no_emoji` passes over `app.py` and `ui/`; no test asserts on an emoji string; the live, replay and app-source AppTests still pass with the new labels.

- [ ] **UI-01** · P1 · M — **Dark theme, self-contained typography, clean shell**
  - Files: new `.streamlit/config.toml`; new `ui/fonts.py`, new `ui/assets/fonts/` (Titillium Web 600/700 woff2 + `OFL.txt`); `ui/layout.py` (`render_header`), `ui/theme.py` (tokens and CSS), `app.py`.
  - Fix:
    ```toml
    # .streamlit/config.toml
    [theme]
    base = "dark"
    primaryColor = "#e10600"
    backgroundColor = "#0b0c0f"
    secondaryBackgroundColor = "#13151a"
    textColor = "#eceef1"
    font = "sans serif"
    [client]
    toolbarMode = "minimal"
    [server]
    runOnSave = false
    ```
    - `st.set_page_config(page_title="F1 Replay", layout="wide", initial_sidebar_state="expanded")` as the first Streamlit call — **no emoji `page_icon`**: omit it (Streamlit's default favicon) unless a plain image file is committed under `ui/assets/`.
    - Delete `st.title`/`st.caption` (the header bar is the title).
    - Replace the colour constants in `ui/theme.py` with the §5.4 tokens (keep the old names as aliases only if tests import them) and emit them once as CSS variables.
    - Remove the Google Fonts `@import`. `ui/fonts.py` reads the woff2 files, base64-encodes them and returns an `@font-face` block; it is injected once into the main document with the dashboard CSS (`st.html`). If the files are missing, return an empty string (system stack takes over). Download the fonts once, manually, from the upstream OFL repository and commit them with `OFL.txt`; never fetch at runtime.
    - Apply §5.3: Titillium Web for labels/headings/driver codes, system stack for body, `tabular-nums` for all numbers; drop JetBrains Mono.
    - One CSS rule for Streamlit's block padding (`.block-container{padding-top:1rem}`), nothing else targeting Streamlit internals.
  - Acceptance: `test_ui_guideline` checks 4 (no URLs) and 7 (no `st.title`, no emoji `page_icon`) now pass without xfail (check 5, hex literals, is finished by UI-05); a unit test asserts `ui.fonts.font_face_css()` contains `@font-face` and `font/woff2` when the files exist and returns `""` when they don't; `.streamlit/config.toml` has `base = "dark"`.
  - Depends on: UI-00.

- [ ] **UI-02** · P1 · M — **Sidebar session picker with an explicit Load**
  - Files: `ui/layout.py` (`render_session_selector`), `app.py`, `tests/test_session_selector.py`.
  - Fix: move selection to `st.sidebar` inside `st.form("session_picker")` with a **Load session** submit button (the one primary button on the sidebar); the form result is stored in `st.session_state["selection"]` and only a submit changes it, so browsing the dropdowns never triggers a load. Seasons `range(now.year, 2017, -1)` (FastF1 has timing and telemetry from 2018). Under the form, a "Recent" list of the last 5 loaded sessions as plain text buttons ("2023 Bahrain – Race"). Mirror `year/gp/session` into `st.query_params` on load and read them on first run, so a URL reopens the same session. Data source and telemetry scope move into a collapsed "Advanced" expander inside the form. The live notice ("A session is running now") stays at the top of the sidebar with a **Go live** button — text only, no red-circle emoji.
  - Acceptance: AppTest — changing the Grand Prix select without pressing Load does not call `get_session_data` (stub manager counts calls); pressing Load calls it once; `?year=2023&gp=Bahrain Grand Prix&session=R` preselects and auto-loads on first run.
  - Depends on: UI-01.

- [ ] **UI-03** · P1 · M — **Pages instead of one long scroll**
  - Files: `app.py`, `ui/layout.py`, new `ui/pages.py`, tests.
  - Fix: load once (before navigation), put the session dict in `st.session_state["session_data"]`, then `st.navigation([...], position="top")` with function pages named exactly as §5.9:
    - **Replay** (default for historical/saved sessions) — REPLAY-04/05; until REPLAY-04 lands it calls the existing `render_session_replay`.
    - **Results** — today's `render_dashboard(session_data)` (end-of-session tower, sector cards, dominance map) and the tyre strategy chart.
    - **Analysis** — Telemetry, Head-to-head, Lap times, Positions, Weather, Race control, chosen with `st.segmented_control` so only the visible panel is computed (verify `st.tabs(..., on_change="rerun")` with `.open` on 1.59 if you prefer tabs). The Plotly Track tab is dropped — the SVG map is the one map.
    - **Records** — the metrics-store panel; cache statistics under a collapsed "Diagnostics" expander.
    - **Live** (only when the live source is selected) — the existing live fragment.
  - Acceptance: AppTest — default page for a historical stub session is Replay; Analysis → Telemetry renders one telemetry chart group (count `plotly_chart` elements); the records panel is not on the Replay page; page names match §5.9 exactly.
  - Depends on: UI-02.

- [ ] **UI-04** · P1 · M — **Replay screen specification** (the contract REPLAY-05 implements; copy it into `layout.md` as its section 9, "Replay screen")
  - Files: `layout.md` (new section 9), `ui/theme.py` (shared tokens, `FLAG_STATES` labels).
  - Spec (desktop ≥ 1200 px; sizes in CSS px; colours are §5.4 tokens):
    ```
    +------------------------------------------------------------------------------------------+
    | BAHRAIN GRAND PRIX 2023 · RACE    LAP 23/57    0:41:07    [GREEN]    AIR 26° TRACK 31° DRY |  header 48
    +--------------------------------+---------------------------------------------------------+
    | POS  DRIVER        GAP     TYRE  PIT |                                                   |
    |  1 | VER   1     LEADER    M 14   1  |              track map (SVG, 60 %)                |
    |  2 | PER  11     +4.211    M 14   1  |    cars: 9 px circles in team colour,             |
    |  3 | ALO  14    +21.030    H  3   2  |    optional 3-letter labels, focused car 12 px    |
    |  … 22 rows x 30 px (40 %)            |    with accent ring                               |
    |                                      +---------------------------------------------------+
    | [Gap | Interval]                     | RACE CONTROL   L22 14:02  TRACK LIMITS CAR 16 T4  |  3 lines
    +--------------------------------------+---------------------------------------------------+
    | [play] [-30s] [-5s] [+5s] [+30s]  Previous lap  Next lap   Speed 1x v   ----o------------ |  controls 36
    | lap ticks every 5 · SC/VSC shaded (flag colour 30 %) · red flag shaded · pit, out, fastest |  timeline 28
    +------------------------------------------------------------------------------------------+
    ```
    - Tower columns (race): `POS` (28, right) · 4 px team bar · driver code (Titillium 600, 44) + number (`--text-dim`, 24) · `GAP` or `INTERVAL` (toggle, 80, right) · `LAST` (80; `--best` text if session best, `--pb` if personal best) · tyre badge (§5.6) + age · `PIT` count (28) · status chip (`PIT`, `OUT`, `FIN`; empty when on track). Qualifying/practice: `POS`, driver, `BEST`, `GAP`, `LAST`, S1/S2/S3 of the lap in progress, tyre.
    - Row height 30, zebra `--surface`/`--surface-2`, no row dividers; focused row has a 2 px `--accent` left border and `--surface-2` background; `OUT` rows use `--text-dim` text **and** the `OUT` chip. Interval under 1.000 s shown in `--text` (overtaking range), others `--text-dim`.
    - Header: event name Titillium 700 22 px uppercase; the flag chip uses `FLAG_STATES` colours, and `FLAG_STATES` labels are shortened to the chip words `GREEN`, `YELLOW`, `SC`, `VSC`, `RED`, `CHEQUERED` (keys unchanged — the payload's `flags` use the keys); weather as text.
    - Timeline: full width, lap ticks labelled every 5 laps in `--text-dim`, hover shows `Lap n · 0:41:07`, click or drag seeks; event markers are 6 px shapes with distinct forms (pit = square, retirement = cross drawn with SVG lines, fastest lap = diamond) plus `title` text — not colour alone.
    - Transport: only play/pause is an icon (§5.8 inline SVG with `aria-label`); every other control is text (`-30s`, `-5s`, `+5s`, `+30s`, `Previous lap`, `Next lap`, speed select `0.5x … 64x`).
    - Responsive: 900–1199 px → map 55 %, tower drops `LAST` and `PIT`; < 900 px → map on top (50 vh), tower below at full width, race control behind a "Race control" text toggle.
    - Motion and accessibility exactly as §5.10 and §5.11.
  - Acceptance: section 9 of `layout.md` lists every value above; `ui/theme.FLAG_STATES` labels are the chip words; (the `player.css` tokens/breakpoints and the 1440×900 / 390×844 screenshots are checked in REPLAY-05's acceptance).and 390×844 show tower and map with no horizontal scroll.

- [ ] **UI-05** · P2 · S — **Results page polish**
  - Files: `ui/dashboard.py`, `ui/track_map.py`, `ui/theme.py`, `ui/layout.py` (Plotly template), `tests/test_track_map.py`, `tests/test_ui_guideline.py`.
  - Fix: apply §5 to `tower_html` and the header: tokens instead of literals, sticky header row and sticky first two columns, hide `Speed`, `Diff`, `Tyre history` under 1200 px, KO rows tinted `--surface-2` with the `KO` chip instead of 55 % opacity, `title` tooltips on micro-sector cells ("Sector 2 · mini 3 · personal best"), `role="img"` and `<title>` on the SVG, the Plotly template from §5.6 on every chart.
  - Acceptance: CSS contains `position: sticky` for `th` and the first two `td`s; `tests/test_track_map.py` asserts the SVG has a `<title>`; `test_ui_guideline` has no remaining xfails.

- [ ] **UI-06** · P2 · S — **Loading and empty states**
  - Files: `app.py`, `data/source_manager.py` (`_load_fastf1_session` gains `progress=None`), new `ui/status.py` (`DataStatus`), `ui/layout.py`.
  - Fix: `st.status("Loading 2023 Bahrain Grand Prix – Race", expanded=False)` with steps ("Timing and laps", "Positions, 20 drivers", "Telemetry 7/20", "Building replay") updated from `_load_fastf1_session` through an optional `progress` callback; each panel shows why it is empty through a `DataStatus` (`ok | empty | unavailable(reason) | auth_required | error(msg)`), worded per §5.9.
  - Acceptance: AppTest with a stub manager that calls `progress` shows the status element; a panel given `DataStatus.unavailable("FastF1 has no weather data for this session")` renders that sentence.

- [ ] **UI-07** · P2 · S — **Analysis follows the replay cursor**
  - Files: `ui/layout.py` (`render_lap_times`, `render_position_changes`, `render_driver_comparison`), `ui/pages.py`.
  - Fix: Lap-times and Positions charts draw a 1 px `--accent` vertical line at the replay's current lap (`st.session_state[f"replay_cursor:{session_key}"]` → lap); Head-to-head preselects the focused driver and the car ahead at the cursor; a text link "Back to replay at lap n" on the Analysis page.
  - Acceptance: AppTest — with a cursor at lap 3 of the fixture, the lap-time figure's `layout.shapes` contains a line at x = 3.
  - Depends on: UI-03, REPLAY-04 (cursor), REPLAY-05 (focus).

---

## 5. UI guideline (binding for every UI change)

This is the design contract for everything a user sees: Streamlit pages, the HTML tower and header, the SVG map, Plotly charts, the replay component, and all interface text. It exists because the current UI has the marks people now associate with machine-generated interfaces (emoji as icons, generic defaults, decorative chrome), and because a timing product is judged on how quickly and calmly it can be read.

### 5.1 What this product should feel like

A **broadcast timing screen and a results sheet**, not a landing page. Reference points studied (§8.3): the timing tower on F1 broadcasts and F1's own site, results tables on established sports sites, live flight maps (map + list + timeline), and financial terminals. What they share:

- Data is the interface. Chrome (borders, backgrounds, icons, titles) is minimal and exists only to separate or label data.
- Dense, aligned, monospaced-width numbers; uppercase short labels; very little rounding.
- One accent colour used rarely; every other colour *means* something (team, tyre compound, flag, session best).
- Nothing moves unless the data moved.

### 5.2 Patterns that make an interface look generated — banned

| Don't | Why it reads as generated / hurts | Do instead |
|---|---|---|
| **Emoji anywhere in the UI**: page title, tab and page names, buttons, headings, metric labels, status indicators, chart annotations, toasts, `page_icon` | Emoji used as navigation and bullets is the single most-cited tell; it renders differently per OS, cannot be styled, and carries no defined meaning | Plain text labels. Status is a text chip (`PIT`, `OUT`, `SC`). Where an icon is genuinely clearer (play, pause), use an inline SVG from §5.8 with an `aria-label` |
| Gradients (backgrounds, buttons, text via `background-clip:text`), especially purple/indigo | The loudest "default output" signal; decorative, no meaning | Flat surfaces from the palette in §5.4 |
| Glow, neon, blurred "aurora" backgrounds, glassmorphism (`backdrop-filter`), heavy drop shadows | Decoration standing in for hierarchy | Hierarchy through type weight, size, contrast and spacing. Shadows only on things that float (tooltip, popover): one level, `0 2px 8px rgba(0,0,0,.4)` |
| `rounded-2xl`-style large radii on everything, cards inside cards, a row of three identical feature cards | Template look; wastes space in a data product | Radius ≤ 4 px (chips 2 px, panels 4 px, circles only for car markers and tyre badges). Group with spacing and a 1 px divider, not nested boxes |
| Colour-coded side bars / dots on every block with no defined meaning | Colour as noise | The only coloured bars are **team colour** on driver rows. Every dot or chip maps to a documented state (§5.4 table) and carries text |
| Inter / Roboto pulled from Google Fonts as the unexamined default; any runtime font request | Default typeface + external request (breaks offline replays, leaks the viewer's IP) | §5.3: self-contained type stack, embedded in CSS |
| Filler or hype copy: "Unlock insights", "Seamless", "Powerful", "Dive into", "Let's go!", "Oops!", "Magic", "AI-powered", exclamation marks, arrow glyphs on buttons ("Load →") | Says nothing specific; reads as template text | §5.9: short, literal, specific labels |
| Identical fade-in/slide-up animation on every element, spinners everywhere, pulsing "live" dots | Motion without information | §5.10: motion only when data changes position or value |
| Generic "hero" header with a big title and a subtitle before any data | Pushes data below the fold | The session header bar *is* the title; data starts in the first 60 px |
| Pie/donut/gauge charts, 3-D effects | Angle and area are read poorly (NN/g) | Bars, lines, dot plots, tables |

### 5.3 Typography

- **Families** (no network requests; files committed with their OFL licence):
  - Labels, headings, driver codes: **Titillium Web** 600/700 — an open-licence motorsport-associated face, embedded as base64 `@font-face` (woff2 subset: Latin, digits, punctuation) from `ui/assets/fonts/` via `ui/fonts.py`. If the font files are not available, fall back silently to the system stack; never fetch at runtime.
  - Body text and numbers: system UI stack `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` with `font-variant-numeric: tabular-nums` on every element that shows a time, gap, speed, lap or position.
  - No monospace family for timing (tabular figures give the alignment without the "code editor" look). Monospace only for raw data/debug views.
- **Scale** (px): 11 (table labels, uppercase, letter-spacing .06em) · 13 (table body, captions) · 15 (body) · 18 (panel titles) · 22 (header event name). Nothing larger in the app.
- Weights: 400 body, 600 labels and driver codes, 700 only for P1-P3 positions and the header event name.
- Uppercase only for short labels (≤ 3 words) and driver codes; sentences are sentence case.
- Line height 1.25 in tables, 1.45 in prose. Numbers right-aligned in columns; text left-aligned.
- `@font-face` must be injected into the **main document** (one `st.html` style block), because `@font-face` declared inside a component's shadow root is ignored by browsers; the shadow-DOM component then uses the family name.

### 5.4 Colour

Tokens live once in `ui/theme.py` (Python) and are mirrored as CSS variables in the shared stylesheet and the player CSS. No hex literal outside these two places (checked by §5.12).

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0b0c0f` | page background |
| `--surface` | `#13151a` | panels, tower, map background |
| `--surface-2` | `#1a1d23` | zebra rows, hovered rows, inputs |
| `--line` | `#262a31` | 1 px dividers and borders |
| `--text` | `#eceef1` | primary text (contrast ≥ 12:1 on `--bg`) |
| `--text-dim` | `#9aa1ab` | secondary text (≥ 4.5:1 on `--surface`) |
| `--accent` | `#e10600` | **only**: the focused driver outline, the playhead and the cursor marker in charts, the keyboard focus ring, the primary button. Never for decoration |
| `--best` | `#b138dd` | session best (purple) — timing convention, not decoration |
| `--pb` | `#2fbf5b` | personal best (green) |
| `--slower` | `#e6c229` | slower than personal best (yellow) |
| flag colours | `ui/theme.FLAG_STATES` | GREEN / YELLOW / SAFETY CAR / VSC / RED / CHEQUERED chips and track tint |
| compound colours | FastF1 per-season mapping (`compound_colors`), fallback `COMPOUND_RING` | tyre badges only |
| team colours | FastF1 `TeamColor` through `safe_hex` | 4 px row bar, car marker fill, chart lines |

Rules: colour is never the only carrier of meaning (every coloured state also has text, a letter, a shape or a `title`); at most one saturated non-data colour on screen (the accent); state colours are used only for their state; text on team colours uses black or white by computed contrast (≥ 4.5:1), not always white.

### 5.5 Layout and spacing

- 4 px base grid; spacing steps 4 / 8 / 12 / 16 / 24 / 32. Panels are separated by 8 px gaps or a 1 px `--line`, not by nested cards.
- Wide layout. The first screen shows data, not controls: header bar (48 px), then the replay/tower/map. Session selection lives in the sidebar (UI-02).
- Asymmetric split where the content is asymmetric: tower 40 % / map 60 % on the replay; tower 60 % / cards + map 40 % on Results.
- Tables: row height 30 px (replay) / 28 px (Results), 1 px row dividers **or** zebra — not both; sticky header; no vertical cell borders.
- Empty and loading states occupy the same box the data will (no layout jump), with one line of text saying what is missing and why (§5.9).
- Breakpoints: ≥ 1200 desktop, 900–1199 compact (drop low-priority columns), < 900 stacked (map above tower). No horizontal page scroll at any width ≥ 360 px.

### 5.6 Components

- **Buttons**: text labels in sentence case ("Load session", "Final result", "Next lap"). One primary button per view (`--accent` fill), everything else secondary (outline `--line`, text `--text`). Height 32 px, radius 4 px. No icon-only buttons except the transport controls, which have `aria-label` and `title`.
- **Chips / badges** (status, flag, tyre): 2 px radius, 11 px uppercase text, fixed width per column so rows align. The allowed status vocabulary is fixed: `ON TRACK` (shown as an empty cell), `PIT` (the model's `IN PIT` status), `OUT`, `FIN`, `DNF`, `DSQ`, `DNS`, `KO`. Lapped cars are shown in the gap column (`+1 LAP`), not as a chip. Flags: `GREEN`, `YELLOW`, `SC`, `VSC`, `RED`, `CHEQUERED`.
- **Tyre badge**: 18 px circle outlined in the compound colour with the compound letter (S/M/H/I/W) inside, age as a number to the right; a used set has a dashed outline and says "used" in its `title`.
- **Tables** are HTML tables (or the component's positioned rows), never `st.dataframe` for the tower. Column headers are 11 px uppercase `--text-dim`.
- **Track map**: track as a 14 px `--surface-2` ribbon with a 1 px darker edge; start/finish as a short white line; corner numbers 10 px `--text-dim` without circles; car markers 9 px team-colour circles with a 1 px `--bg` stroke and an optional 3-letter label; the focused car 12 px with an `--accent` ring; no drop shadows, no glow.
- **Charts (Plotly)**: one shared template in `ui/theme.py` — transparent paper, `--surface` plot area, `--line` gridlines at 1 px, `--text-dim` 11 px tick labels, no chart titles inside the figure (the panel label is the title), legend only when there are more than 3 series, `hovermode="x"` with a compact template, team colours for drivers, dashed line for a teammate's second car.
- **Streamlit widgets**: use them as they are under the dark theme; do not restyle Streamlit internals with fragile class selectors beyond the block padding rule in UI-01.

### 5.7 Data presentation rules

- Lap times `1:32.456`; gaps `+4.211` (3 decimals under 60 s, `+1:02.3` above); lapped `+1 LAP` / `+2 LAPS`; leader shows `LEADER` in the gap column (not "LAP 23" — the lap count is in the header); intervals under 1.000 s get the `--text` colour instead of `--text-dim` (overtaking range).
- Units always visible once per column header (`GAP`, `SPEED KM/H`), never repeated in every cell.
- Missing values are an en dash `–` in `--text-dim`, never `NaN`, `None`, `0` or an empty cell.
- Times of day in the track's local time with the zone once in the header; session time as `H:MM:SS` from lights out.
- Wind as `12 km/h NE` (text bearing; an arrow glyph is optional and must not be an emoji).

### 5.8 Icons

- Default: **no icons** — text labels.
- Allowed icon set, as inline SVG defined once in the player (`<symbol>`s) and in `ui/icons.py` for HTML: play, pause, skip back, skip forward, previous lap, next lap, fullscreen. 16 px, 1.5 px stroke, `currentColor`, each with `aria-label` and `title`.
- No emoji, no icon fonts, no remote icon libraries, no icon-in-a-rounded-square decorations.

### 5.9 Copy

- Literal and specific. Name the thing and the action: "Load session", "Race time", "Jump to", "No weather data for this session", "Car positions need an F1TV subscription token".
- No exclamation marks, no rhetorical questions, no first-person plural ("Let's"), no hype adjectives, no apologies ("Oops", "Sorry"), no ellipsis loading text other than "Loading timing data…"-style progress steps.
- Sentence case for everything except short uppercase labels and chips.
- Page names: `Replay`, `Results`, `Analysis`, `Records` (`Live` when live). Analysis sections: `Telemetry`, `Head-to-head`, `Lap times`, `Positions`, `Weather`, `Race control` (tyre strategy lives on Results).
- Error messages say what failed and what to do: "Could not load 2023 Bahrain Grand Prix – Race: FastF1 timed out. Try again, or pick another session."

### 5.10 Motion

- Allowed: tower rows sliding to a new position (350 ms ease-out) when the order changes; car markers moving continuously (interpolated); the playhead; a 600 ms background flash (`--best`/`--pb` at 25 % opacity) on a lap-time cell when a session-best/personal-best lap completes.
- Not allowed: entrance animations, pulsing dots, spinners other than Streamlit's own loading states, parallax, hover lifts.
- `prefers-reduced-motion: reduce` disables row transitions and flashes (positions still update).

### 5.11 Accessibility

- Contrast: body text ≥ 4.5:1, large text and UI boundaries ≥ 3:1 (tokens above satisfy this; recheck any new colour).
- Every coloured state has a text equivalent (chip text, compound letter, `title` attribute).
- The replay player is operable by keyboard (Space, ←/→, Shift+←/→, `[`/`]`, `1`–`8`) with a visible focus ring (`--accent`, 2 px), and the map `<svg>` has `role="img"` and a `<title>` naming the session and moment.
- No information only on hover; tooltips repeat what is already visible or add detail.

### 5.12 Automated checks (added by UI-00, run in the normal test suite)

`tests/test_ui_guideline.py` scans `app.py`, `ui/**/*.py`, `ui/components/**/*.{js,css,html}` and `.streamlit/config.toml` and fails on:

1. Any emoji or pictographic character (Unicode ranges U+1F000–U+1FAFF, U+2600–U+27BF, U+2B00–U+2BFF, U+FE0F, U+23E9–U+23FA, U+25A0–U+25FF geometric shapes such as ▶) in string literals or markup. Typographic characters used in data (`–`, `·`, `°`, `←`/`→` in keyboard help text) are allowed.
2. `gradient(`, `backdrop-filter`, `text-shadow`, `filter: blur` or `drop-shadow` in CSS/inline styles.
3. `border-radius` values above 4 px except `50%` (circles).
4. `http://` or `https://` URLs in CSS or HTML strings (no remote fonts, scripts, images).
5. Hex colour literals outside `ui/theme.py`, `.streamlit/config.toml` and the player's `:root` block.
6. Banned copy (case-insensitive, whole words) in user-facing strings — string arguments of `st.*` calls and text nodes of HTML/JS markup: `unlock`, `seamless`, `powerful`, `supercharge`, `elevate`, `effortless`, `magic`, `oops`, `let's`, `dive into`, `ai-powered`, and `!` at the end of a user-facing string.
7. `st.title(` anywhere, and `page_icon=` with a non-file value.

### 5.13 Exceptions log

Record any deliberate exception here with the reason and the item that introduced it. (Empty.)

---

## 6. Execution order

```
REPO-14
REPLAY-01 → REPLAY-02 → REPLAY-03
UI-00 → UI-01 → UI-02 → UI-03
REPLAY-04                      checkpoint: the app opens on a correct lights-out replay (server-rendered)
UI-04 → REPLAY-05              checkpoint: smooth browser playback
REPLAY-06 → REPLAY-08 → REPLAY-07
UI-05 → UI-06 → UI-07
REPLAY-10
REPLAY-09                      optional, needs network
```

Each checkpoint ends with all gates green and a manual check recorded in `tasks.md` (what was loaded, what was seen).

---

## 7. Architecture

```
            load (once per session, cached)                              per viewer
+------------------------------------------------------+   +--------------------------------------+
| FastF1 Session                                       |   | Replay page                          |
|  - laps, results, weather, race control              |   |  - replay_player (components.v2)     |
|  - Laps.get_pos_data()  -> positions (2 Hz tidy)     |   |    decode once, rAF loop, no reruns  |
|  - _extended_timing_data().stream -> timing_stream   |-->|    setStateValue(cursor|focus) on    |
|  - track_status, _session_split_times                |   |    pause / seek / focus only         |
|  v                                                   |   |  - snapshot panels <- snapshot_at()  |
| unified dict (schema 7)                              |   | Results page <- render_dashboard()   |
|  - tower_series()      (vectorised change points)    |   | Analysis page <- marker at cursor    |
|  - snapshot_at(t)      (unified-dict shaped)         |   | Records page <- metrics store        |
|  - events()                                          |   +--------------------------------------+
|  - build_replay_payload() (<= 4 MB JSON, Int16 xy)   |
+------------------------------------------------------+
```

Layering stays as `CLAUDE.md` describes: `data/` adapters produce the unified dict; `processing/` (including `replay.py`, `replay_model.py`, `replay_payload.py`) is pure — no Streamlit, no network; `ui/` renders; `app.py` orchestrates. The player's JS/CSS/HTML live in `ui/components/replay_player/` and are read from disk at import.

The same model can serve live mode later: a live `standings` table from `TimingData` (Position, GapToLeader, IntervalToPositionAhead) has the shape `snapshot_at` produces, so `build_timing_rows` gets one "ordered by the timing screen" path for both.

---

## 8. Research notes

### 8.1 How replay products structure the experience

- **F1ReplayTiming** (FastAPI + Next.js, FastF1): precomputes each session once (1–3 min) and stores it; positions "updating every 0.5 seconds with smooth interpolation"; leaderboard with position, gap, interval, last lap, sector indicators, tyre compound/age/history, pit count with a live pit timer, grid-position change, fastest-lap and penalty markers, sub-1 s interval highlighting; marshal-sector flags, weather, track status, race control; controls 0.5×–20×, skip 5 s/30 s/1 min/5 min, lap jumping, progress bar; lap comparison with pit and SC periods shaded. Adopted: interpolation, skip buttons, lap jumps, shaded timeline, interval highlight.
- **f1-race-replay** (Python + Arcade, FastF1): precomputed frames; keyboard-first controls (Space, ←/→, number keys for speed, `L` for labels); click/Shift-click driver selection; simulates the safety car 500 m ahead of the leader; its README says the GPS-progress leaderboard is wrong in the first corners and during pit stops. Adopted: keyboard map, driver focus. Not adopted: simulated SC, GPS-derived order.
- **Nitrous** (desktop app): "Scrub forwards and backwards, jump to key events" — the `Jump to` control and timeline markers.

### 8.2 Streamlit facts used by this plan (pinned 1.59.0, checked in the installed source)

- `st.components.v2.component(name, html=, css=, js=, isolate_styles=True)` registers an inline component with no build step. Mount with `key=`, `data=` (JSON, Arrow or bytes), `default=`, `width=`, `height=` and one `on_<name>_change` callback per state/trigger. The JS default export receives `{data, parentElement, setStateValue, setTriggerValue}` and may return a cleanup function. With a `key`, Streamlit updates the existing element instead of remounting. `parentElement` is a ShadowRoot when styles are isolated. **Every state change reruns the script** and re-serialises `data` — hence cursor updates only on pause/seek/focus. Components are trusted code; never put feed strings into `innerHTML` unescaped.
- `st.navigation(position="top")`, `st.segmented_control`, `st.tabs(..., on_change="rerun")` with `.open`, `st.status`, and the theme keys `base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `font`, `[client] toolbarMode` all exist in 1.59.
- `@font-face` rules inside a shadow root are ignored by browsers, so fonts are declared once in the main document (§5.3).

### 8.3 Interface research: what reads as generated, and what established sites do

Generated-interface tells, as collected by design practitioners in 2025–2026 (sources in §10): emoji used as icons, bullets and headings; purple/indigo gradients (traced to framework defaults such as Tailwind's `indigo-500`) and gradient text; dark mode with decorative glow or aurora backgrounds; glassmorphism by reflex; `rounded-2xl shadow-lg` on everything and cards nested in cards; the "hero + three feature cards + call to action" template; multicoloured side bars and status dots with no defined meaning; Inter/Roboto as the unexamined default; uniform spacing with no hierarchy; the same fade-in on every element; generic line icons (sparkles, arrows, lightning); and weightless copy ("Build faster. Ship smarter", "Seamless", "Powerful"). The shared remedy is constraint: one committed direction, a real typeface, a specific palette of one dominant colour plus one accent plus neutrals, a layout principle, and a tone.

Established data-heavy sites (F1's own timing and results pages, broadcast timing graphics, sports results tables, live flight trackers, market terminals) do the opposite: data first; dense aligned tables with tabular figures; short uppercase labels; little or no rounding; colour reserved for meaning (team, tyre, flag, best/personal best); a single accent; map + list + timeline for anything that moves over time; no decorative motion.

Dashboard guidance from Nielsen Norman Group: encode quantities with position and length (bars, lines), not area or angle (no pies, donuts, gauges, 3-D); use colour for categories, reinforced by shape or position because up to 8 % of men have a colour-vision deficiency; the dashboard should answer at a glance.

These three sets of findings are condensed into §5.

### 8.4 FastF1 facts used by this plan (3.8.3, installed source and the local cache)

- `fastf1._api._extended_timing_data(path)` returns `(laps_data, stream_data, session_split_times)`; `stream_data` columns are `Time, Driver, Position, GapToLeader, IntervalToPositionAhead` (`EMPTY_STREAM`). `Session._load_laps_data` keeps only `laps_data`. The result is cached as `_extended_timing_data.ff1pkl`. `fastf1.api` (public alias) emits a `UserWarning` on import.
- `session._session_split_times` — real values: 2023 Bahrain Q `[0, 2758.7 s, 4138.7 s]`, 2023 Bahrain R `[0, 1 day, 1 day]`.
- `session.session_start_time` is the session-status "Started" time (2023 Bahrain R: 1:02:36.7). `session.track_status` has `Time, Status, Message`.
- 2023 Bahrain R: 28 475 timing-stream rows; the lapped form in the cache is `"1 L"` (2 865 occurrences).

---

## 9. Domain cheat-sheet

- **Session codes:** FP1/FP2/FP3, `SQ` Sprint Qualifying (2023 "Sprint Shootout"), `S` Sprint, `Q` Qualifying, `R` Race.
- **Session time:** laps `Time`/`LapStartTime`/`Sector{n}SessionTime`/`PitInTime`/`PitOutTime`, positions `SessionTime`, weather/race-control/track-status `Time` share one origin — compare them as seconds via `to_seconds`.
- **Lights out** ≈ min non-NaT `LapStartTime` of lap 1 in a race (= `session.session_start_time`). Raw `session.pos_data` starts earlier (installation laps, grid, formation lap); `Laps.get_pos_data()` — what this repo uses — starts at the first `LapStartTime`; in Q/FP the first lap's `LapStartTime` is NaT.
- **Timing-stream gap strings:** leader `"LAP 23"` (gap and interval), others `"+12.345"`, lapped `"1 L"`; the raw live feed also sends `""` before timing starts (`tests/fixtures/live/TimingData.jsonl.gz`), FastF1's stream forward-fills and never stores it. Parse with `processing.time_utils.parse_gap` (REPLAY-02).
- **Race gap** = time behind the leader at the same timing line; lapped cars show `+N LAP`. **Interval** = to the car directly ahead. Neither equals a best-lap difference.
- **Qualifying 2026:** 22 cars, 6 eliminated after Q1 and 6 after Q2 (20 cars 2018–2025: 5 and 5).
- **Track status codes:** 1 green, 2 yellow, 4 SC, 5 red, 6 VSC deployed, 7 VSC ending. The chequered flag comes from race control, not track status.
- **Mini-sector colours:** purple session best · green personal best · yellow slower than personal best.
- **Units:** GPS 1/10 m; wind m/s from FastF1 (convert to km/h for display); speed km/h; temperatures °C.
- **Data quality:** first-lap timing is noisy; the feed is lossy — do not un-pit a car on one missing flag; deleted laps exist (`Deleted`); SC laps distort pace.
- **2026:** no DRS (active aero) — a zero DRS channel is correct data.

---

## 10. Sources

Replay products and platform
- [adn8naiagent/F1ReplayTiming](https://github.com/adn8naiagent/F1ReplayTiming) · [IAmTomShaw/f1-race-replay](https://github.com/IAmTomShaw/f1-race-replay) · [Nitrous](https://nitrous.software/)
- [Streamlit — st.components.v2.component](https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component) · [Component mounting](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/mount) · [Interactive counter example](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/examples/interactive-counter)
- [FastF1 documentation](https://docs.fastf1.dev/)

Interface guidance
- [7 Signs a UI Has Been Vibe Coded — The Fountain Institute](https://www.thefountaininstitute.com/blog/signs-vibe-coded-ui)
- [AI Slop Fonts and Gradients: The Tells That Give Away AI Design — 925 Studios](https://www.925studios.co/blog/ai-slop-design-tells)
- [funboy322/avoid-ai-design (pattern catalogue)](https://github.com/funboy322/avoid-ai-design)
- [Why Every AI-Built Website Looks the Same (Tailwind indigo-500) — DEV](https://dev.to/alanwest/why-every-ai-built-website-looks-the-same-blame-tailwinds-indigo-500-3h2p)
- [The Purple Gradient Problem — DEV](https://dev.to/james_anderson_h/the-purple-gradient-problem-why-ai-ui-all-looks-alike-and-how-to-fix-it-3j65)
- [Dashboards: Making Charts and Graphs Easier to Understand — Nielsen Norman Group](https://www.nngroup.com/articles/dashboards-preattentive/)
- [Titillium Web (SIL Open Font License)](https://fonts.google.com/specimen/Titillium+Web) — download once, commit with the licence; never load at runtime

Local evidence
- Installed source: `fastf1/_api.py` (`_extended_timing_data`, `EMPTY_STREAM`), `fastf1/core.py` (`_load_laps_data`, `_session_split_times`, `session_start_time`), `streamlit/components/v2/__init__.py` (mount signature), `streamlit/commands/navigation.py` (`position="top"`).
- Cache: `ff1_cache/2023/2023-03-05_Bahrain_Grand_Prix/2023-03-05_Race/_extended_timing_data.ff1pkl` and the qualifying equivalent.
- Emoji inventory: `app.py` lines ~176–315, `ui/layout.py` (TRACK_STATUS, flag icons, tabs, weather metrics, live controls), `ui/replay_view.py` (play/start buttons).
