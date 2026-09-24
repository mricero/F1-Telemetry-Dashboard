# IMPROVEMENTS.md — Rules, fixes and open work

**The plan an agent loop works through, one item at a time. Revision 3, 2026-09-24 (Azerbaijan GP weekend).**

| | |
|---|---|
| Repository | F1 Telemetry Dashboard (Streamlit), local checkout, branch `main` |
| Base of this revision | `5326f84` |
| Pinned stack | Python 3.11–3.13 · streamlit 1.59.0 · fastf1 3.8.3 · pandas 2.3.3 · numpy 2.4.6 (3.11) / 2.5.1 (3.12+) · websockets 17 · plotly 6.8 (`requirements.lock`) |
| Gates at `5326f84` | `pytest`: **905 passed, 25 skipped** on 3.11 and 3.12 · `ruff`, `black --check`, `mypy`: clean |
| Done since revision 2 | REPO-14, REPLAY-01…08, REPLAY-10, UI-00…07 (agent loop, 2026-09-23/24) · REPO-15, LIVE-01, LIVE-08, REPLAY-11…16, UI-08 (this revision, §2) |
| What this revision adds | The live feed works again (SignalR Core client), a race-weekend runbook (§1), fixes for seven defects a review found in the replay work, and one open backlog (§3) that replaces every earlier list |
| Earlier revisions | Revision 1 (audit, 2026-09-16) and revision 2 (replay/UI plan, 2026-09-23) are in git history (`git show 084fbcc:IMPROVEMENTS.md`, `git show 35c3c51:IMPROVEMENTS.md`). Everything still open from them is carried into §3 — nothing needs to be read from those versions. |

---

## Contents

0. [How to run this file in an agent loop](#0-how-to-run-this-file-in-an-agent-loop)
1. [Race-weekend runbook (Baku, 24–26 September 2026)](#1-race-weekend-runbook-baku-2426-september-2026)
2. [Fixed in this revision](#2-fixed-in-this-revision)
3. [Open items](#3-open-items)
4. [Execution order](#4-execution-order)
5. [UI guideline (binding for every UI change)](#5-ui-guideline-binding-for-every-ui-change)
6. [Architecture](#6-architecture)
7. [Research notes](#7-research-notes)
8. [Domain cheat-sheet](#8-domain-cheat-sheet)
9. [Sources](#9-sources)

---

## 0. How to run this file in an agent loop

### 0.1 Loop protocol (one item per iteration)

1. **Pick** the first unchecked `- [ ]` item in the order of §4 whose `Depends on` items are all checked. Skip `- [~]` items (blocked; the reason is written under them).
2. **Read** every file listed under `Files`, the relevant part of `CLAUDE.md`, §0.5 below, and — for anything the user sees — **§5 UI guideline in full**.
3. **Reproduce first.** Write a failing test that captures the item's `Acceptance`. Network-dependent tests go in `tests/test_integration_network.py` behind the `network` marker.
4. **Fix** with the smallest change that satisfies `Acceptance`. The unified session dict (`CLAUDE.md` → "The unified session dict is the central contract") changes only where an item says so; when a persisted key is added, bump `REPLAY_SCHEMA_VERSION` (currently **7**) and default the key for older replays.
5. **Run the gates** (§0.2). All must pass.
6. **Tick** the checkbox, append `— done in <short-sha>`, add one line to `tasks.md` under the current round heading ("Round 8 — live feed and replay fixes" for this revision).
7. **Commit** one item per commit: `fix(LIVE-19): live qualifying segments`. End the message with the attribution lines the session asks for.
8. If an item is wrong or blocked, mark it `- [~]`, write why underneath, and move on.

### 0.2 Gates

```bash
python -m pytest -q
python -m ruff check .
python -m black --check .
python -m mypy --ignore-missing-imports app.py data processing ui
# optional, needs internet
F1_NETWORK_TESTS=1 python -m pytest -m network -q
# only meaningful while a session is on air (see §1)
python scripts/live_smoke.py 60
```

`python` is the interpreter that has `requirements.lock` installed (on the maintainer's Windows machine `.venv\Scripts\python` — Python 3.14 there — or `.venv311\Scripts\python`). The lock must install on **3.11**, the floor CI tests: pins that need a newer Python get an environment marker (see REPO-15). Do not create another virtualenv inside the repo.

`pytest.ini` sets `addopts = -q`; adding another `-q` hides the pass/fail summary line.

### 0.3 Before the first commit of a session

1. `git --no-optional-locks status --short`. The tree should be clean apart from your own work. If dozens of files show as modified, compare with `git diff --ignore-cr-at-eol --stat`: files missing from the second list are line-ending churn only (`.gitattributes` normalises to LF since REPO-14).
2. If git reports `.git/index.lock` and no git process is running, the lock is stale; delete it. `_to_delete/` (git-ignored) holds scratch files moved aside by earlier tool sessions and can be deleted.

### 0.4 Item format

```
- [ ] **ID** · Priority · Effort (S ≤1h, M ≤½ day, L >½ day) · [LIVE] when it needs a session on air
  - Files:        paths (grep for names; line numbers in carried items are historical)
  - Problem:      what is wrong and why it matters
  - Fix:          the approach, concrete enough to implement
  - Acceptance:   testable definition of done
  - Depends on:   other IDs (optional)
```

### 0.5 Ground rules

General

- **Mocks mirror real upstream shapes** (`CLAUDE.md` → Testing conventions). Use recorded payloads (`tests/fixtures/live/`, the synthetic sessions in `tests/replay_fixtures.py`) and the value formats in §8. A fake `{"n": 1}` CarData payload once passed tests while the real base64 payload was silently dropped.
- **Streamlit reruns the whole script on every interaction.** Anything expensive is cached; anything per-viewer lives in `st.session_state` under a key that includes the session key, and a browser session keeps only the current session's heavy entries (REPLAY-15).
- **`st.stop()` is a no-op in bare mode** — always `raise` after it.
- **Time strings never go through `pd.to_timedelta`** — use `processing/time_utils.to_seconds`; gap cells use `parse_gap`.
- **The UI guideline (§5) is binding.** No emoji, no gradients/glows/glass, radius ≤ 4 px, no external font or script requests; `tests/test_ui_guideline.py` enforces it. Exceptions go in §5.13 first.

Live feed

- **The endpoint is `wss://livetiming.formula1.com/signalrcore`** and the client is `data/signalr_core.py`. Do not reintroduce LiveF1's `RealF1Client` or any `/signalr/` client: that hub answers 401.
- **Two entry points only.** The subscription snapshot goes to `SignalRLiveAdapter.seed_state(result)`, every feed message to `handle_message(topic, data, timestamp)`. The recorder, the fixture replay and the tests use the same two. Parsers read normalised records, never raw payloads.
- **Delta topics are state, not rows.** Anything in `data/live_state.STATE_TOPICS` is deep-merged (index-addressed dicts over lists, `_deleted` honoured); a reconnect re-seeds it, so it cannot duplicate. Only true time series (`CarData.z`, `Position.z`, `WeatherData`) go to the bounded buffers.
- **A dropped connection is normal.** F1 drops long connections (~2 h) and restarts; the client reconnects with backoff. A refusal (401/403/429) waits 120 s and is shown, never retried hot — hammering the endpoint is how hosted projects got IP-blocked.
- **One connection per process** (`data/live_service.get_live_adapter()`); browser tabs only read.
- **The token stays local.** `F1TV_SUBSCRIPTION_TOKEN` (the JWT, or the `login-session` cookie value) is read from the environment/`.env`, sent as `Authorization: Bearer`, never logged, never fetched with `fastf1.internals.f1auth` inside the server (it starts a blocking local auth server).
- **Neither the cloud sandbox nor the linked Linux VM can reach `livetiming.formula1.com`** (proxy allowlist). Live behaviour is proven offline against recorded frames and then on the maintainer's machine with `scripts/live_smoke.py`; items that need that are tagged `[LIVE]`.

Replay

- **A replay snapshot never sees the future.** `processing/replay_model.snapshot_at(t)` reads only rows stamped `≤ t`. Final `results` (including their row order — the drivers table follows finishing order), final `Position`, fastest-lap telemetry and dominance belong to the Results page. Property tests enforce this.
- **Racing semantics live in Python; the player's JavaScript only looks values up and draws.** A new rule becomes a payload field computed and tested in Python.
- **Every `setStateValue` from the player reruns the script.** Report on pause, release, the end of a key burst, focus, analyse and unmount — never per frame or per drag event — and keep the component's `data` stable between Python seeks.
- **`fastf1._api` is private.** Wrap calls, pin columns with a test, keep a fallback.

---

## 1. Race-weekend runbook (Baku, 24–26 September 2026)

The 2026 Azerbaijan Grand Prix runs Thursday to **Saturday** (the race moved to Saturday for Azerbaijan's Remembrance Day on 27 September). Baku is UTC+4, India UTC+5:30.

| Session | Baku | UTC | India |
|---|---|---|---|
| Practice 1 | Thu 24 Sep 09:30 | 05:30 | 11:00 |
| Practice 2 | Thu 24 Sep 13:00 | 09:00 | 14:30 |
| Practice 3 | Fri 25 Sep 09:30 | 05:30 | 11:00 |
| Qualifying | Fri 25 Sep 13:00 | 09:00 | 14:30 |
| **Race** | **Sat 26 Sep 12:00** | **08:00** | **13:30** |

### 1.1 Before a session (5 minutes)

1. `git pull` / confirm `main` contains `b80e4b2` (LIVE-01) and `5326f84`.
2. `.venv\Scripts\python -m pip install -r requirements.lock` (adds nothing new if `websockets` ≥ 13 is present; it was 16.0).
3. Optional, for car telemetry and car positions on the map: put your token in `.env` as `F1TV_SUBSCRIPTION_TOKEN=...`. How to get it: sign in on formula1.com in a browser, open the developer tools → Application/Storage → Cookies → `login-session`, copy the value (the app extracts `subscriptionToken` itself). Tokens last a few days; the Live page shows how many remain.
4. `.venv\Scripts\python scripts\live_smoke.py 30`. Outside a session expect `Connected, waiting for a session to start` and the previous session's drivers; exit code 1 simply means no live data arrived yet.

### 1.2 During a session

1. `streamlit run app.py`. In the sidebar: Data source **Live (SignalR)**, or leave **Auto** and press **Go live** when "A session is running now" appears (it uses the FastF1 schedule, 15 min before the start to 30 min after the end).
2. Live page → **Connect to live timing**. The chip should go `CONNECTING` → `WAITING` → `LIVE` once the session publishes. The tower is ordered by the timing screen (gap/interval from `TimingData`), the header shows the remaining session time, the flag, weather and race control. Without a token the map and the telemetry charts stay empty and the page says why.
3. **Record raw stream** under "Live session controls" to keep the session: `replay_sessions/raw_<time>/subscribe.json` + `live.jsonl`. These replay through the same ingest path (`data/live_recorder.replay_recording`) and are the best fixtures for LIVE-18.

### 1.3 What the status chip means

| Chip | Meaning | What to do |
|---|---|---|
| `CONNECTING` | negotiating / opening the socket | wait a few seconds |
| `WAITING` | connected; only pings arrive (no session on air, or it paused) | normal between sessions |
| `LIVE` | feed data arriving | — |
| `STALE` | connected but nothing for 60 s; reconnecting | usually recovers by itself |
| `RECONNECTING` | the connection dropped; retrying with backoff (1 → 60 s) | normal, especially ~2 h into a session |
| `TOKEN NEEDED` | negotiate answered 401 | set or renew `F1TV_SUBSCRIPTION_TOKEN`; restart the stream |
| `REFUSED` | negotiate answered 403/429 (F1 refused this client or IP) | wait; do not restart repeatedly; check VPN/proxy; retries are spaced 120 s |
| `STOPPED` / `OFFLINE` | stopped by the user / never started | press Connect |

### 1.4 Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| `live_smoke.py` prints a proxy or TLS error | corporate proxy / firewall | `websockets` honours `HTTPS_PROXY`; try another network |
| Chip stays `WAITING` during a session | the session has not started publishing, or the feed changed | compare with F1's own live timing page; run `live_smoke.py 60 --record out` and keep the recording |
| Tower empty but chip `LIVE` | `TimingData` not arriving (only `Heartbeat`/`WeatherData`) | the smoke script's per-topic counts show which topics arrive |
| Map/telemetry empty | no token, or token expired | the Live page caption names the token state |
| Gaps look wrong in qualifying | live qualifying partitions are not implemented yet | LIVE-19 |

After each session the same session is available historically: FastF1 reads F1's post-session archive (usually within an hour or two), so the Replay page can play FP1/FP2 of today already.

---

## 2. Fixed in this revision

### 2.1 Live feed: why it could not work, and what changed

| # | Problem (verified in code and against current sources) | Fix |
|---|---|---|
| L1 | Live mode used LiveF1's `RealF1Client`, which targets the classic `/signalr/` hub. F1 moved to SignalR Core in 2025 and the classic negotiate now answers **401** (confirmed by a project that runs against the 2026 feed, §7.1). Live mode could not connect at all. | `data/signalr_core.py`: our own SignalR Core client (LIVE-01). |
| L2 | The raw ingest path the new client uses (`handle_message`) buffered `CarData.z`/`Position.z` as base64 strings the parsers could not read; the recorder/fixture path had the same hole, hidden by a test that used a fake `{"n": 1}` payload. | `normalise_series`: decode `.z` topics into flat records at ingest; weather keeps its timestamp. |
| L3 | `RaceControlMessages` and `TrackStatus` were read from buffers that only the old livef1 path filled, so race control and the header flag were always empty on the raw path. | Both are merged state now (`STATE_TOPICS`); the snapshot reads them from state. A reconnect's fresh snapshot can no longer duplicate every message. |
| L4 | The live tower was ordered by best lap (DASH-01's live half was never done): a race showed the wrong order and lap-time differences as gaps. | `LiveDataProcessor.standings_from_state`: order, gap, interval, last/best lap, sectors, pit count and status from `TimingData`, in the same `standings` columns a replay snapshot has. |
| L5 | No reconnect, no staleness detection, "Stop" could not stop (LIVE-08). | Client pings every 10 s, 60 s silence = dead, exponential backoff, 120 s after 401/403/429, `stop()` joins within 5 s, status chip on the Live page. |
| L6 | The header's "Remaining" clock was a placeholder: `ExtrapolatedClock` was never turned into a time, and a cached snapshot would have frozen it. | `extrapolated_remaining()` counts down from `Remaining` at `Utc` while `Extrapolating`; refreshed on every poll even when nothing else changed. |
| L7 | Stints came only from `TyreStintSeries`, which not every client/feed delivers. | Falls back to `TimingAppData` stints (same fields). |
| L8 | `requirements.lock` pinned numpy 2.5.1, which needs Python 3.12 — the lock failed to install on 3.11, the declared floor and first CI job. | Environment markers (REPO-15). |

Items (all done):

- [x] **REPO-15** · P1 · S — **The lock installs on the 3.11 floor** — done in d3f424c
  - Files: `requirements.lock`, `requirements.txt`.
  - Fix: `numpy==2.4.6; python_version < "3.12"` and `numpy==2.5.1; python_version >= "3.12"`; `websockets>=13,<18` declared (the client's transport). Regenerating with `scripts/lock_requirements.py` on 3.12+ drops the marker — re-add it by hand until the script learns markers (see REPO-16).
  - Acceptance: `uv pip install -r requirements.lock` succeeds on 3.11; full suite green on 3.11 and 3.12.

- [x] **LIVE-01** · P0 · L — **SignalR Core client** — done in b80e4b2
  - Files: `data/signalr_core.py` (new), `data/live_adapter.py`, `data/live_state.py`, `data/live_service.py`, `data/source_manager.py`, `scripts/live_smoke.py`, `readme.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `tests/test_signalr_core.py` (new), `tests/test_live_view.py`, `tests/test_live_service.py`, `tests/test_live_threading.py`, `tests/test_docs_live_claims.py`.
  - Fix: `OPTIONS /signalrcore/negotiate` (cookie) → `POST ...?negotiateVersion=1` (`connectionToken`; Bearer token when configured) → `wss://…/signalrcore?id=<token>` with `User-Agent: BestHTTP` and the cookie → JSON handshake → `Subscribe` (completion = snapshot → `seed_state`) → `feed` → `handle_message`. `websockets.sync.client` on one daemon thread; handlers that raise are logged and do not drop the connection. Subscribed topics: `Heartbeat, CarData.z*, Position.z*, ExtrapolatedClock, TimingData, TimingAppData, TimingStats, TopThree, WeatherData, TrackStatus, SessionInfo, SessionStatus, SessionData, DriverList, RaceControlMessages, LapCount, TyreStintSeries, PitLaneTimeCollection` (* only with a token). The live snapshot gains `standings`, merged race control/track status, the countdown clock, lap count and the last heartbeat. `live_smoke.py` reports state changes, per-topic counts and what the dashboard would show, and can `--record`.
  - Acceptance (offline, met): the whole protocol against fakes (cookie, version 1, `?id=`, handshake, Subscribe arguments, snapshot and feed dispatch, pings, refused handshake, server close); the recorded 2023 Bahrain messages as SignalR frames through client → adapter → `poll_live_data` give the GP name, 20+ drivers, a timing-screen-ordered tower with `LEADER`, race control, track status, decoded car data/positions and timestamped weather. **Live acceptance is LIVE-18.**

- [x] **LIVE-08** · P0 · M — **Reconnect, staleness, stop, status** — done in b80e4b2
  - Fix: see L5. States `IDLE, CONNECTING, WAITING, LIVE, STALE, RECONNECTING, AUTH_REQUIRED, BLOCKED, STOPPED`; plain text per state (`STATUS_TEXT`) and a chip vocabulary (`ui/layout.FEED_CHIPS`).
  - Acceptance (met): reconnects after drops and stops within 5 s; 401 → `AUTH_REQUIRED` and 403 → `BLOCKED` with exactly one attempt inside the back-off window; silent socket → `STALE`; pings only → `WAITING`.

- [x] **LIVE-17** · P0 · M — **Live tower, race control, flag and clock from the raw feed** — done in b80e4b2
  - Fix: L2, L3, L4, L6, L7.
  - Acceptance (met): `tests/test_signalr_core.py::TestStandings` (race: `LEADER`/`+1.234`/`+1 LAP`, `IN PIT`/`OUT`, purple last lap; practice: `TimeDiffToFastest`), `TestSessionClock`, `TestAdapterLifecycle::test_a_reconnect_snapshot_does_not_duplicate_race_control`, `tests/test_live_view.py::TestFullFeedThroughTheRealIngestPath`.

### 2.2 Replay: defects found by review of REPLAY-01…10 / UI-00…07

An independent review of the committed replay/UI work ran the player in a simulated DOM against every fixture payload and profiled the Python side. It found no crash, and these defects:

- [x] **REPLAY-11** · P1 · S — **The opening order was the final classification** — done in 2f7b555
  - Problem: drivers without a position yet were tie-broken by the drivers table, which follows `session.results` (finishing order). A race without a timing stream opened at lights out showing the result; qualifying and practice showed the final order before anyone was timed.
  - Fix: `_driver_codes` orders races by `GridPosition` (known before the start; pit-lane start last) and other sessions by car number.
  - Acceptance (met): reversing the drivers table changes nothing at the start of race, qualifying and practice fixtures; a race starts in grid order.

- [x] **REPLAY-12** · P1 · S — **Lap buttons jumped to the end in qualifying/practice** — done in 5326f84
  - Fix: the payload carries `lap_marks` (the leader's completions in a race, anyone's otherwise), used by the player's lap buttons and `[`/`]`.

- [x] **REPLAY-13** · P2 · S — **"Analyse this lap" marked the wrong lap** — done in 5326f84
  - Fix: the picked lap is stored (`analysis_lap:<session>`) and used by the Analysis markers until the replay is reopened.

- [x] **REPLAY-14** · P1 · M — **Rerun storms and a lost position** — done in 5326f84
  - Problem: every drag event and held arrow key called `setStateValue` (each one a script rerun), and the live cursor was part of the component's `data`, so every report re-sent the whole payload (1–2 MB for a race). Leaving the page lost the playback position; the focused driver was never sent back.
  - Fix: drags report on release, key bursts 400 ms after the last key, `data.cursor` is the last *Python seek* only, the cursor is reported before focus/analyse, when the tab hides and on unmount, the player remounts at the last reported moment after another page, and `data.focus` restores the focus. Checked in jsdom: one report per burst, Next lap stays inside the session for race/qualifying/practice.

- [x] **REPLAY-15** · P2 · S — **Session-state memory grew per session opened** — done in 5326f84
  - Fix: only the current session's model/payload stay in `st.session_state`; sector cards are memoised per moment.

- [x] **REPLAY-16** · P2 · S — **Snapshot over its 150 ms budget** — done in 2f7b555
  - Fix: in-progress lap cells written once per column; timedelta sector columns converted in one call. 173 ms → ~130 ms on a two-core runner. The seconds-series perf test takes the best of three runs with 25 % slack.

- [x] **UI-08** · P3 · S — **Streamlit widgets used an 8 px radius** — done in 5326f84
  - Fix: `baseRadius = "4px"` in `.streamlit/config.toml` (guideline 5.6).

Not changed (reviewed, fine): the player's binary search, listener/rAF cleanup, shadow-root keyboard handling, `setStateValue` during playback, saved replays keeping the new tables.

---

## 3. Open items

### 3.1 Live feed

- [ ] **LIVE-18** · P0 · S · [LIVE] — **Verify the live client against a real session and keep the recording**
  - Files: `scripts/live_smoke.py`, `data/signalr_core.py`, `tests/fixtures/live/`, `scripts/capture_fixture.py`, `tasks.md`.
  - Problem: LIVE-01 is proven offline only; no environment the agent runs in can reach the feed (§0.5).
  - Fix: during Practice 3 / Qualifying / the Race (§1): run `live_smoke.py 120 --record replay_sessions/raw_baku_<session>` and, separately, the app's Live page for a few minutes. If a topic behaves differently from the recorded 2023 shapes, fix the normaliser/state and add a regression test using a slice of the new recording (keep fixtures < 2 MB gzipped; add a `scripts/capture_fixture.py --from-recording` option if convenient).
  - Acceptance: the smoke script shows state `LIVE`, non-zero counts for `TimingData`, `TrackStatus`, `RaceControlMessages`, `WeatherData`, `SessionInfo`, `DriverList`, `ExtrapolatedClock` (and `CarData.z`/`Position.z` with a token); the Live page tower matches F1's own live timing order; a reconnect (unplug the network 30 s) returns to `LIVE`; results written to `tasks.md` with the date and session.

- [ ] **LIVE-19** · P1 · M — **Live qualifying: segments, knock-outs and the segment clock**
  - Files: `data/live_adapter.py` (`standings_from_state`), `data/source_manager.py`, `processing/timing.py` (`_rows_from_standings` partitions), tests.
  - Problem: in live qualifying the tower has no Q1/Q2/Q3 partitions; `KnockedOut` only becomes a `KO` status, and the gap is the session-wide difference to the fastest lap rather than the running segment's.
  - Fix: read `TimingData.SessionPart` (1–3) and per-line `KnockedOut`, `BestLapTimes` (list per segment) and `Stats`; order by best time in the running segment, eliminated drivers under "Eliminated in Q1/Q2" headings (`Partition` column, same headings as the replay); header label `Q2` with the remaining time. Confirm key names on the LIVE-18 qualifying recording before coding.
  - Acceptance: a fixture built from that recording (or synthetic with the confirmed shapes) inside Q2 shows Q1-eliminated drivers under the heading and the rest ordered by Q2 bests.
  - Depends on: LIVE-18 (for the shapes).

- [ ] **LIVE-20** · P2 · S — **Record every live session automatically**
  - Files: `data/live_adapter.py`, `ui/layout.py` (`render_live_controls`), `config.py`.
  - Problem: recording is a manual button; a forgotten click loses the session's raw data (the archive has it later, but not the exact live stream).
  - Fix: `F1_LIVE_AUTORECORD=1` (default on) starts `start_recording()` into `replay_sessions/raw_<gp>_<session>_<utc>` when the first `SessionInfo` arrives, rotating when `SessionInfo` changes; the button stays for manual control.
  - Acceptance: feeding two sessions' `SessionInfo` through `handle_message` produces two recording directories; with the flag off nothing is written.

- [ ] **LIVE-21** · P2 · M — **Broadcast delay** (was UX-10)
  - Files: `data/live_adapter.py` or a new `data/live_history.py`, `data/source_manager.py`, `ui/layout.py`.
  - Problem: every live-timing product lets the viewer delay the timing to match the TV/stream (typically 30–60 s); this app shows it real-time.
  - Fix: keep ingest real-time; keep one immutable snapshot per second for the last 5 minutes (`deque`); the live page reads `snapshot_at(now − delay)`; a delay number input (0–300 s) in the sidebar, persisted in `st.session_state`.
  - Acceptance: with a 30 s delay the tower shows the order from 30 s earlier in a simulated feed.

- [ ] **LIVE-22** · P2 · S — **Track state and sector yellows on the live map**
  - Files: `ui/dashboard.py`, `ui/track_map.py`, `data/fastf1_adapter.py` (`get_circuit_info`).
  - Fix: reuse REPLAY-07's tint/chip for `TrackStatus` 4/5/6/7 on the live map; optionally colour marshal sectors from race-control messages with `Scope == "Sector"` (needs `marshal_sectors` in `circuit_info`, loaded from FastF1 for the event when the live session starts).
  - Acceptance: a live snapshot with `TrackStatus` 4 renders the `SC` chip and tint.

- [ ] **LIVE-23** · P3 · S — **"Last update" freshness**
  - Fix: caption `Last update 3 s ago` from `last_heartbeat`/`stats.last_data_at`, turning to text `No update for 45 s` over 30 s.
  - Acceptance: unit test with a frozen clock.

- [ ] **LIVE-24** · P3 · S — **Token helper**
  - Fix: on the Live page, a collapsed "Subscription token" expander: paste box (not stored server-wide; written to `.env` only on explicit "Save"), shows expiry via `token_expiry`, explains where to find `login-session`. No automated login.
  - Acceptance: pasting a JWT with a past `exp` shows "expired"; saving writes exactly one `F1TV_SUBSCRIPTION_TOKEN=` line.

- [~] **REPLAY-09** · P3 · L — **Exact timing-screen replay from F1's static archive through the live pipeline**
  - **Blocked until LIVE-18 passes** (the live snapshot builder has to be proven on a real feed first).
  - Idea: feed the archive's `TimingData`, `TimingAppData`, `TrackStatus`, `RaceControlMessages`, `WeatherData`, `LapCount` `.jsonStream` files through `handle_message` on a virtual clock (the path `data/live_recorder.replay_recording` uses) and sample `poll_live_data()` each second into keyframes; gains official mini-sector colours and the exact broadcast tower.

### 3.2 Repository

- [ ] **REPO-16** · P3 · S — **The lock script should keep environment markers**
  - Files: `scripts/lock_requirements.py`, `tests/test_dependencies.py`.
  - Fix: a `MARKERS` table in the script (`numpy`: 3.11 → the last version supporting it) so regenerating on 3.12+ keeps the 3.11 pin; a test that every pinned version's `Requires-Python` accepts 3.11 or carries a marker.
  - Acceptance: running the script on 3.12 reproduces the current lock.

### 3.3 Carried backlog from revision 1

Still open and still valid. Text as audited on 2026-09-16 (line numbers refer to that commit; grep for the names). Superseded items were dropped: HIST-06, UX-01, UX-05, UX-07, UX-08, UX-09, UX-11 (done through UI-00…08), UX-10 (now LIVE-21), FEAT-04 (done), FEAT-08 (a radial gauge conflicts with guideline 5.2), FEAT-13 (not planned). New UI in these items follows §5. Items written as one line (FEAT-*) get `Files:` and `Acceptance:` written into them as the first step of their iteration, committed with the item.

- [ ] **HIST-08** · P2 · S — **FastF1 first load is slow and blocks the UI; drivers loaded serially**
  - Files: `data/source_manager.py:93-130`, `data/fastf1_adapter.py:65-70, 108-128`.
  - Problem: A race load calls `get_telemetry()` for ~20 drivers sequentially (each merges car+position data and interpolates) behind a single spinner; on a cold cache this takes minutes. Changing *only* the telemetry scope reloads the whole session because the runtime cache key includes `telemetry_scope`.
  - Fix: Cache the loaded `fastf1.core.Session` with `@st.cache_resource(max_entries=3)` keyed by `(year, gp, session)`; derive scope-specific frames from it (cheap); show `st.progress` per driver; compute telemetry lazily for the drivers actually displayed (default: top 10 + favourites); optionally `concurrent.futures.ThreadPoolExecutor` for per-driver merges (numpy releases the GIL for much of the work — measure first).
  - Acceptance: switching scope on a warm session < 2 s; progress bar visible on cold load.

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

- [ ] **UX-06** · P2 · S — **Race control panel usability**
  - Files: `ui/layout.py:654-688`.
  - Problem: Multiselect `key="rc_categories"` is shared across sessions with different category sets (stale defaults); no text search; no link between a message and the lap chart; no "SC/VSC/red flag" period shading anywhere.
  - Fix: key per session; search box; shade SC/VSC/red-flag periods (from `session.track_status`) on lap-time and position charts — the single most requested context in strategy analysis tools like Armchair Strategist.
  - Note (2026-09-24): SC/VSC/red shading now exists on the replay timeline (REPLAY-07); the lap-time and position charts still lack it.

- [ ] **UX-12** · P3 · S — **Units, time zones, preferences**
  - Fix: km/h ↔ mph, °C ↔ °F, local vs track time (SessionInfo `GmtOffset` / FastF1 `EventDate` local), all in a Settings popover and URL params.

- [ ] **REPO-09** · P3 · S — **`python app.py` relaunch hack may be obsolete**
  - Files: `app.py:13-59`, `CLAUDE.md` "Bare mode is the hazard".
  - Problem: Streamlit 1.59 release notes list "Programmatic app launching with `python app.py`". The custom relaunch + env flag may now be redundant.
  - Fix: Verify on the pinned version; if native, delete the hack and its docs; keep the `raise` after `st.stop()` rule.

- [ ] **REPO-10** · P3 · M — **Packaging & layout**
  - Problem: Modules rely on `sys.path.insert` and top-level package names `data`, `processing`, `ui`, `config` that collide easily with other packages.
  - Fix: `src/f1dash/{data,processing,ui}`, `pyproject.toml [project]` with entry point `f1dash = "f1dash.cli:main"` (wraps `streamlit run`).

- [ ] **REPO-13** · P3 · S — **Secrets & config**
  - Fix: `.env.example` documenting `FASTF1_CACHE_DIR`, `REPLAY_DIR`, `F1_METRICS_STORE`, `F1TV_SUBSCRIPTION_TOKEN` (new), `F1_NETWORK_TESTS`, `LOG_LEVEL`; read the token via `st.secrets` when deployed; never log it; add it to `.gitignore`d `secrets.toml` docs.

- [ ] **TEST-03** · P2 · S — **Network tests never run automatically**
  - Files: `.github/workflows/ci.yml`.
  - Fix: second workflow on `schedule:` (nightly) + `workflow_dispatch` with `F1_NETWORK_TESTS=1`, FastF1 cache via `actions/cache`. Failures open an issue. This catches upstream breakage (schedule shape, Jolpica limits, FastF1 changes) before users do.

- [ ] **TEST-04** · P2 · S — **No performance budgets**
  - Fix: `pytest-benchmark` (opt-in marker `perf`) for `poll_live_data`/snapshot build (LIVE-11), `build_timing_rows` on a full race, `build_track_svg` size.
  - Note (2026-09-24): budgets exist as plain tests (`tests/perf/`, `TestPerformance` in `tests/test_replay_model.py`); this item is only about CI-stable measurement (best-of-N, relative budgets).

- [ ] **TEST-05** · P3 · S — **Coverage & property tests**
  - Fix: `pytest --cov` in CI with a floor (start at current, ratchet up); `hypothesis` for `to_seconds`, `deep_merge`, `segment_boundaries`, `resample_to_distance_grid` (monotonic grid, coded channels only take source values).

- [ ] **DOC-02** · P2 · S — **readme drift**
  - Problems: repository tree omits `ui/dashboard.py`, `ui/theme.py`, `ui/track_map.py`, `processing/timing.py`, `processing/time_utils.py`, `layout.md`, `.github/`; two sections numbered "### 5."; clone URL is a placeholder (`your-username/f1-telemetry-dashboard`); "Auto-Detection … based on real-time endpoint probing" is inaccurate (LIVE-15); "Intelligent Fallback" promises the most recent GP but falls back to 2025 (HIST-04); "LiveF1 (Historical)" advertised though broken (HIST-03).

- [ ] **DOC-03** · P2 · S — **`CLAUDE.md` / `tasks.md` out of date**
  - Problems: commands use Windows-only `.venv/Scripts/python`; says "four review rounds" (there are five plus an undocumented sixth); architecture section doesn't mention `ui/dashboard.py`/`processing/timing.py`/`ui/track_map.py`; `tasks.md §9` still lists "Live timing tower" and "Track dominance map" as not started.
  - Fix: cross-platform commands; add a "Round 6 — dashboard spec" section to `tasks.md`; link this file from both.
  - Note (2026-09-24): the live section of `CLAUDE.md` was rewritten for LIVE-01; the Windows-only commands and "four review rounds" remain.

- [ ] **DOC-04** · P3 · S — **`dashboard_preview.html` (86 KB) is a static artefact of unknown freshness**
  - Fix: delete, or regenerate from a fixture via a script and reference it from the readme as a screenshot/preview.

- [ ] **DOC-05** · P3 · S — **Legal & data-use notes**
  - Fix: A "Data sources & terms" section: F1 live timing and archive are unofficial/undocumented endpoints; subscription-gated data requires the user's own F1TV account and must not be redistributed; hosting publicly risks IP blocks (f1-dash, matteocelani precedent); F1 trademarks disclaimer (already in LICENSE). Note Jolpica fair-use limits and OpenF1's paid real-time tier.

- [ ] **FEAT-01** · M — **Race trace & gap chart**: gap to leader (or to a reference driver) per lap for all/selected drivers, SC/VSC shaded. undercut-f1 shows gap-to-leader & lap time over the last 15 laps; Armchair Strategist's race trace is its signature. Data: FastF1 `laps.Time` cumulative; live `TimingData.GapToLeader` history.

- [ ] **FEAT-02** · M — **Pit rejoin predictor ("Circle of Doom")**: where a driver would rejoin if they pitted now = current gap − circuit pit loss. f1telemetry.com v2.2.2 moved to "the circuit's real pit loss time (from the circuit API)". Needs a per-circuit pit-loss table (seed from historical `PitInTime→PitOutTime` medians per circuit + stationary time).

- [ ] **FEAT-03** · M — **Tyre degradation / stint pace**: fuel-corrected lap time vs tyre age per compound (Armchair Strategist: fuel-adjusted laps, degradation distributions). Exclude in/out laps, SC laps and `IsAccurate == False`.

- [ ] **FEAT-05** · S — **Team radio list** (`TeamRadio` topic / FastF1 has no radio; OpenF1 `team_radio` historical is free from 2023): clip list with driver + time, playable audio; optional local Whisper transcription as undercut-f1 does. *Auth-gated live* — respect the token rules.

- [ ] **FEAT-06** · S — **Standings panels** (drivers/constructors) via Jolpica (the `*_df` helpers already exist, unused) with points-after-this-race projection for live races.
  - Note (2026-09-24): the Jolpica `*_df` wrappers were deleted in REPO-05; the query methods are kept behind the allow-list in `tests/test_no_dead_code.py`.

- [ ] **FEAT-07** · S — **Linear track position strip**: every car on a straight 0→lap-length line (f1telemetry.com "Lineal Driver Positions" v2.1.0) — cheap, readable on mobile, great for spotting DRS-train-like groups (in 2026: overtake-mode trains).

- [ ] **FEAT-09** · S — **Speed-trap & sector ranking panel** ("Pace Radar" in matteocelani/f1-telemetry): I1/I2/FL/ST ranking and sector bests.

- [ ] **FEAT-10** · M — **Customisable layout**: choose/arrange panels, saved per user (formula-timer "Custom Columns"; nitrous roadmap "drag-and-drop panels"). In Streamlit: column toggles + panel checklist stored in query params.

- [ ] **FEAT-11** · S — **Track limits / deleted laps view** from race control (`Message` contains "TRACK LIMITS"/"DELETED") and FastF1 `Deleted`/`DeletedReason`.

- [ ] **FEAT-12** · S — **2026 regulation context**: label active-aero/overtake-mode where data exists (FastF1 discussion #861 on 2026 ERS/energy data); hide the DRS channel for 2026+ instead of plotting a flat zero.

- [ ] **FEAT-14** · S — **Share links**: encode year/GP/session/drivers/tab in `st.query_params` so a view can be bookmarked.
  - Note (2026-09-24): UI-02 already mirrors `year/gp/session` into `st.query_params`; what remains is the drivers, the page and the replay cursor.

---

## 4. Execution order

```
Race weekend first (time-bound):
  LIVE-18   during Practice 3 (Fri 05:30 UTC), Qualifying (Fri 09:00 UTC), Race (Sat 08:00 UTC)
  LIVE-19   right after the qualifying recording exists
Then:
  LIVE-20 -> LIVE-23 -> LIVE-22 -> LIVE-21 -> LIVE-24 -> REPO-16
  CACHE-03 -> HIST-08 -> CACHE-02 -> CACHE-04
  UX-02 -> UX-03 -> UX-04 -> UX-06 -> UX-12
  TEST-03 -> TEST-04 -> TEST-05
  FEAT-01 -> FEAT-03 -> FEAT-02 -> FEAT-07 -> FEAT-09 -> FEAT-06 -> FEAT-11 -> FEAT-12 -> FEAT-14 -> FEAT-05 -> FEAT-10
  REPLAY-09 (once LIVE-18 has passed)
  REPO-09 -> REPO-13 -> DOC-02 -> DOC-03 -> DOC-04 -> DOC-05 -> REPO-10 (restructure last: it touches every import)
```

Each group ends with all gates green and one line per item in `tasks.md`.

## 5. UI guideline (binding for every UI change)

This is the design contract for everything a user sees: Streamlit pages, the HTML tower and header, the SVG map, Plotly charts, the replay component, and all interface text. It exists because the current UI has the marks people now associate with machine-generated interfaces (emoji as icons, generic defaults, decorative chrome), and because a timing product is judged on how quickly and calmly it can be read.

### 5.1 What this product should feel like

A **broadcast timing screen and a results sheet**, not a landing page. Reference points studied (§7.4): the timing tower on F1 broadcasts and F1's own site, results tables on established sports sites, live flight maps (map + list + timeline), and financial terminals. What they share:

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

- **Streamlit widgets** inherit the 4 px radius from `.streamlit/config.toml` (`baseRadius`, UI-08).
- **Buttons**: text labels in sentence case ("Load session", "Final result", "Next lap"). One primary button per view (`--accent` fill), everything else secondary (outline `--line`, text `--text`). Height 32 px, radius 4 px. No icon-only buttons except the transport controls, which have `aria-label` and `title`.
- **Chips / badges** (status, flag, tyre): 2 px radius, 11 px uppercase text, fixed width per column so rows align. The allowed status vocabulary is fixed: `ON TRACK` (shown as an empty cell), `PIT` (the model's `IN PIT` status), `OUT`, `FIN`, `DNF`, `DSQ`, `DNS`, `KO`. Lapped cars are shown in the gap column (`+1 LAP`), not as a chip. Flags: `GREEN`, `YELLOW`, `SC`, `VSC`, `RED`, `CHEQUERED`. Live feed state: `OFFLINE`, `CONNECTING`, `WAITING`, `LIVE`, `STALE`, `RECONNECTING`, `TOKEN NEEDED`, `REFUSED`, `STOPPED` (`ui/layout.FEED_CHIPS`), always followed by a plain sentence saying what it means.
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

Record any deliberate exception here with the reason and the item that introduced it.

- **SVG namespace URL** (REPLAY-04). `st.html` sanitises its markup and drops inline `<svg>`, so the track map is embedded as an `<img>` of a standalone SVG document, which must declare `xmlns="http://www.w3.org/2000/svg"`. It is a namespace identifier, not a request; check 4 of `tests/test_ui_guideline.py` allows exactly this string.

---

## 6. Architecture

```
  historical / saved (load once per session, cached)                 live (one per process)
+------------------------------------------------------+   +------------------------------------------------+
| FastF1 Session                                       |   | SignalRCoreClient (data/signalr_core.py)        |
|  - laps, results, weather, race control              |   |   negotiate -> wss ?id= -> handshake -> Subscribe|
|  - Laps.get_pos_data()  -> positions (2 Hz tidy)     |   |   completion -> seed_state(snapshot)            |
|  - _extended_timing_data().stream -> timing_stream   |   |   feed -> handle_message(topic, data, ts)       |
|  - track_status, _session_split_times                |   |   ping 10 s, silence 60 s, backoff, status      |
|  v                                                   |   |  v                                              |
| unified dict (schema 7)                              |   | SignalRLiveAdapter                               |
|  - tower_series()  (vectorised change points)        |   |   LiveState (STATE_TOPICS, deep merge)          |
|  - snapshot_at(t)  (unified-dict shaped, standings)  |   |   buffers: CarData/Position records, weather    |
|  - events(), build_replay_payload() (<= 4 MB JSON)   |   |   lap history, optional recorder (jsonl)        |
+------------------------------------------------------+   |  v poll_live_data() -> unified dict + standings |
            |                                              +------------------------------------------------+
            v                                                         v
  Replay page: replay_player (components.v2) + panels        Live page: fragment every 3 s, feed chip,
  Results page: render_dashboard(full dict)                  render_dashboard(snapshot) - the same renderer
  Analysis page: markers at the replay cursor                build_timing_rows orders both by `standings`
```

Layering stays as `CLAUDE.md` describes: `data/` adapters produce the unified dict; `processing/` is pure (no Streamlit, no network); `ui/` renders; `app.py` orchestrates. The player's JS/CSS/HTML live in `ui/components/replay_player/`.


---

## 7. Research notes

### 7.1 F1 live timing in 2026 (SignalR Core)

- **Endpoint and handshake.** FastF1 3.7+ (`fastf1/livetiming/client.py`, unchanged on `main` as of this revision) uses `wss://livetiming.formula1.com/signalrcore`, pre-negotiates with `OPTIONS https://livetiming.formula1.com/signalrcore/negotiate` to collect the `AWSALBCORS` cookie, builds the connection with `signalrcore`'s `HubConnectionBuilder` (`access_token_factory` for the F1TV token), handles `on("feed")` and invokes `Subscribe` whose completion is the full-state snapshot. It has no reconnect ("TODO: enable auto reconnect?").
- **Confirmed against the 2026 feed** by pitwall (a Python live strategy engine, commit of 2026-09-14, run live at the 2026 Dutch GP): "The legacy `/signalr/negotiate` path now answers 401; `/signalrcore` still accepts an unauthenticated connection"; `POST /signalrcore/negotiate?negotiateVersion=1` returns `connectionToken`; socket `?id=<token>`; handshake `{"protocol":"json","version":1}`; message types 3 (completion/snapshot), 1 (feed, `[topic, data, timestamp]`), 6 (ping every ~15 s, the only traffic between sessions); headers `User-agent: BestHTTP`, `Accept-Encoding: gzip, identity`; the feed "reliably disconnects after approximately two hours"; between sessions the endpoint replies with the last session's snapshot, then pings.
- **What needs a subscription.** Since the 2025 Dutch GP the free feed no longer carries car telemetry and positions (Driver Tracker), the DRS indicator, championship tables and pit-stop times (undercut-f1 README); post-session imports still have everything. This app subscribes to `CarData.z`/`Position.z` only with a token.
- **Refusals happen.** Home-Assistant integration `f1_sensor` issue #611 (2026): persistent 403 on `/signalrcore/negotiate?negotiateVersion=1` and the Index fetch for some users, closed without a fix — the reason for the `BLOCKED` state and the 120 s back-off. Hosted projects (f1-dash, matteocelani/f1-telemetry) cite IP restrictions; run locally, one connection, own token.
- **Tokens** are JWTs verified by FastF1 against `https://api.formula1.com/static/jwks.json`, usually valid for a few days; the value people find is the `login-session` cookie, URL-encoded JSON holding `data.subscriptionToken` (undercut-f1 documents copying it manually).

### 7.2 How replay products structure the experience

- **F1ReplayTiming** (FastAPI + Next.js, FastF1): precomputes each session once (1–3 min) and stores it; positions "updating every 0.5 seconds with smooth interpolation"; leaderboard with position, gap, interval, last lap, sector indicators, tyre compound/age/history, pit count with a live pit timer, grid-position change, fastest-lap and penalty markers, sub-1 s interval highlighting; marshal-sector flags, weather, track status, race control; controls 0.5×–20×, skip 5 s/30 s/1 min/5 min, lap jumping, progress bar; lap comparison with pit and SC periods shaded. Adopted: interpolation, skip buttons, lap jumps, shaded timeline, interval highlight.
- **f1-race-replay** (Python + Arcade, FastF1): precomputed frames; keyboard-first controls (Space, ←/→, number keys for speed, `L` for labels); click/Shift-click driver selection; simulates the safety car 500 m ahead of the leader; its README says the GPS-progress leaderboard is wrong in the first corners and during pit stops. Adopted: keyboard map, driver focus. Not adopted: simulated SC, GPS-derived order.
- **Nitrous** (desktop app): "Scrub forwards and backwards, jump to key events" — the `Jump to` control and timeline markers.

### 7.3 Streamlit facts used by this plan (pinned 1.59.0, checked in the installed source)

- `st.components.v2.component(name, html=, css=, js=, isolate_styles=True)` registers an inline component with no build step. Mount with `key=`, `data=` (JSON, Arrow or bytes), `default=`, `width=`, `height=` and one `on_<name>_change` callback per state/trigger. The JS default export receives `{data, parentElement, setStateValue, setTriggerValue}` and may return a cleanup function. With a `key`, Streamlit updates the existing element instead of remounting. `parentElement` is a ShadowRoot when styles are isolated. **Every state change reruns the script** and re-serialises `data` — hence cursor updates only on pause/seek/focus. Components are trusted code; never put feed strings into `innerHTML` unescaped.
- `st.navigation(position="top")`, `st.segmented_control`, `st.tabs(..., on_change="rerun")` with `.open`, `st.status`, and the theme keys `base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `font`, `[client] toolbarMode` all exist in 1.59.
- `@font-face` rules inside a shadow root are ignored by browsers, so fonts are declared once in the main document (§5.3).

### 7.4 Interface research: what reads as generated, and what established sites do

Generated-interface tells, as collected by design practitioners in 2025–2026 (sources in §9): emoji used as icons, bullets and headings; purple/indigo gradients (traced to framework defaults such as Tailwind's `indigo-500`) and gradient text; dark mode with decorative glow or aurora backgrounds; glassmorphism by reflex; `rounded-2xl shadow-lg` on everything and cards nested in cards; the "hero + three feature cards + call to action" template; multicoloured side bars and status dots with no defined meaning; Inter/Roboto as the unexamined default; uniform spacing with no hierarchy; the same fade-in on every element; generic line icons (sparkles, arrows, lightning); and weightless copy ("Build faster. Ship smarter", "Seamless", "Powerful"). The shared remedy is constraint: one committed direction, a real typeface, a specific palette of one dominant colour plus one accent plus neutrals, a layout principle, and a tone.

Established data-heavy sites (F1's own timing and results pages, broadcast timing graphics, sports results tables, live flight trackers, market terminals) do the opposite: data first; dense aligned tables with tabular figures; short uppercase labels; little or no rounding; colour reserved for meaning (team, tyre, flag, best/personal best); a single accent; map + list + timeline for anything that moves over time; no decorative motion.

Dashboard guidance from Nielsen Norman Group: encode quantities with position and length (bars, lines), not area or angle (no pies, donuts, gauges, 3-D); use colour for categories, reinforced by shape or position because up to 8 % of men have a colour-vision deficiency; the dashboard should answer at a glance.

These three sets of findings are condensed into §5.

### 7.5 FastF1 facts used by this plan (3.8.3, installed source and the local cache)

- `fastf1._api._extended_timing_data(path)` returns `(laps_data, stream_data, session_split_times)`; `stream_data` columns are `Time, Driver, Position, GapToLeader, IntervalToPositionAhead` (`EMPTY_STREAM`). `Session._load_laps_data` keeps only `laps_data`. The result is cached as `_extended_timing_data.ff1pkl`. `fastf1.api` (public alias) emits a `UserWarning` on import.
- `session._session_split_times` — real values: 2023 Bahrain Q `[0, 2758.7 s, 4138.7 s]`, 2023 Bahrain R `[0, 1 day, 1 day]`.
- `session.session_start_time` is the session-status "Started" time (2023 Bahrain R: 1:02:36.7). `session.track_status` has `Time, Status, Message`.
- 2023 Bahrain R: 28 475 timing-stream rows; the lapped form in the cache is `"1 L"` (2 865 occurrences).

---

## 8. Domain cheat-sheet

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
- **Live topics and shapes:** state topics are keyframe + partial updates (deep-merge them); `TimingData.Lines[n]` carries `Position`/`Line`, `GapToLeader`, `IntervalToPositionAhead.Value`, `TimeDiffToFastest`, `TimeDiffToPositionAhead`, `LastLapTime{Value, PersonalFastest, OverallFastest}`, `BestLapTime.Value`, `Sectors[i].Value` (deltas address sectors by 0-based key), `InPit`, `PitOut`, `Retired`, `Stopped`, `KnockedOut`, `NumberOfPitStops`, `NumberOfLaps`; `ExtrapolatedClock` = `{Utc, Remaining, Extrapolating}`; `LapCount` = `{CurrentLap, TotalLaps}`; `RaceControlMessages.Messages` is a list, then `{"<index>": {...}}` deltas; live timestamps are ISO UTC with 7 fractional digits (`2026-09-26T08:05:00.1234567Z`), archive timestamps are session-relative (`00:03:32.498`).
- **2026 Baku:** race on **Saturday** 26 September (Remembrance Day on the 27th).


---

## 9. Sources

Live timing
- [FastF1 livetiming client source (main)](https://github.com/theOehrly/Fast-F1/blob/main/fastf1/livetiming/client.py) · [FastF1 issue #753 — live timing moved to signalrcore](https://github.com/theOehrly/Fast-F1/issues/753) · [FastF1 PR #760 — SignalR Core + F1 account auth](https://github.com/theOehrly/Fast-F1/pull/760)
- [Ark07Yad/pitwall — live SignalR Core feed verified against the 2026 endpoint](https://github.com/Ark07Yad/pitwall) (`src/pitwall/feed/signalr.py`)
- [JustAman62/undercut-f1 README — what needs an F1TV subscription](https://github.com/JustAman62/undercut-f1)
- [Nicxe/f1_sensor issue #611 — 403 on signalrcore negotiate](https://github.com/Nicxe/f1_sensor/issues/611)
- [Connecting to the SignalR F1TV data endpoint (classic protocol, historical)](https://dweik.xyz/post/f1-signalr-endpoint/)
- [Azerbaijan GP 2026 schedule — Sky Sports](https://www.skysports.com/f1/news/13588823/azerbaijan-gp-2026-saturday-race-dates-schedule-baku-weather-uk-start-time-and-how-to-watch-or-stream-f1-live-on-sky-sports) · [Formula 1 timetable](https://www.formula1.com/en/latest/article/formula-1-qatar-airways-azerbaijan-grand-prix-2026.EsMvgTyba8dZZ6RynZXv7)

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
