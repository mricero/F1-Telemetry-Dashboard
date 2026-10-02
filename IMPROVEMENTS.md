# IMPROVEMENTS.md — Rules, fixes and open work

**The plan an agent loop works through, one item at a time. Revision 4, 2026-10-02 (after the Azerbaijan GP, before Singapore).**

| | |
|---|---|
| Repository | F1 Telemetry Dashboard (Streamlit), local checkout, branch `main` |
| Base of this revision | `2eb3971` (code identical to `5326f84`; only this file changed in between) |
| Pinned stack | Python 3.11–3.13 · streamlit 1.59.0 · fastf1 3.8.3 · pandas 2.3.3 · numpy 2.4.6 (3.11) / 2.5.1 (3.12+) · websockets 17.1 · plotly 6.8 (`requirements.lock`) |
| Gates at `2eb3971` | Re-run for this revision on a copy of the working tree, Python 3.12 / numpy 2.5.1: `pytest` **905 passed, 25 skipped** (the 25 are the opt-in network tests) · `ruff`, `black --check`, `mypy`: clean · about **15 000 warnings**, nearly all NumPy's "generic unit" timedelta deprecation (CORE-01, TEST-06) · one perf test flaky (TEST-07) |
| What this revision is | A fresh review of the whole codebase (data, processing, UI and the replay player, repository/CI/docs), with every finding checked against the code and most reproduced with a script, AppTest or a headless browser. It **replaces every item list** of revision 3; the rules (§0), the UI guideline (§5), the architecture (§6) and the domain cheat-sheet (§8) are kept because code, tests and `CLAUDE.md` rely on them |
| Earlier revisions | Revision 1 (audit), 2 (replay/UI plan) and 3 (live feed + replay fixes) are in git history (`git show 084fbcc:IMPROVEMENTS.md`, `35c3c51:…`, `2eb3971:…`). Done items are recorded in `tasks.md` (rounds 1–8). Everything still open from them is carried into §3 with its current status — nothing needs to be read from those versions |

---

## Contents

0. [How to run this file in an agent loop](#0-how-to-run-this-file-in-an-agent-loop)
1. [Review summary and live-session checklist](#1-review-summary-and-live-session-checklist)
2. [What changed in this file](#2-what-changed-in-this-file)
3. [Open items](#3-open-items)
4. [Execution order](#4-execution-order)
5. [UI guideline (binding for every UI change)](#5-ui-guideline-binding-for-every-ui-change)
6. [Architecture](#6-architecture)
7. [Research notes](#7-research-notes)
8. [Domain cheat-sheet](#8-domain-cheat-sheet)
9. [Sources](#9-sources)
10. [Command-line install with uv (distribution)](#10-command-line-install-with-uv-distribution)

---

## 0. How to run this file in an agent loop

### 0.1 Loop protocol (one item per iteration)

1. **Pick** the first unchecked `- [ ]` item in the order of §4 whose `Depends on` items are all checked. Skip `- [~]` items (blocked; the reason is written under them).
2. **Read** every file listed under `Files`, the relevant part of `CLAUDE.md`, §0.5 below, and — for anything the user sees — **§5 UI guideline in full**.
3. **Reproduce first.** Write a failing test that captures the item's `Acceptance`. Network-dependent tests go in `tests/test_integration_network.py` behind the `network` marker.
4. **Fix** with the smallest change that satisfies `Acceptance`. The unified session dict (`CLAUDE.md` → "The unified session dict is the central contract") changes only where an item says so; when a persisted key is added, bump `REPLAY_SCHEMA_VERSION` (currently **7**) and default the key for older replays.
5. **Run the gates** (§0.2). All must pass.
6. **Tick** the checkbox, append `— done in <short-sha>`, add one line to `tasks.md` under the current round heading ("Round 9 — revision 4 review" for this revision).
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

`python` is the interpreter that has `requirements.lock` installed (on the maintainer's Windows machine `.venv\Scripts\python` — Python 3.14 there; `.venv311` is an empty folder, see REPO-24). The lock must install on **3.11**, the floor CI tests: pins that need a newer Python get an environment marker (see REPO-15). Do not create another virtualenv inside the repo.

`pytest.ini` sets `addopts = -q`; adding another `-q` hides the pass/fail summary line.

### 0.3 Before the first commit of a session

1. `git --no-optional-locks status --short`. Use `--no-optional-locks` on **every** read-only git command: a plain `git status` refreshes the index and, in a sandbox that cannot delete files, leaves a stale `.git/index.lock` behind (it happened during the revision 4 review). The tree should be clean apart from your own work. If dozens of files show as modified, compare with `git diff --ignore-cr-at-eol --stat`: files missing from the second list are line-ending churn only (`.gitattributes` normalises to LF since REPO-14).
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


---

## 1. Review summary and live-session checklist

### 1.1 What the review found

| Area | Headline |
|---|---|
| Repository | The ~60 local commits since the two on `origin` were **never pushed**, so CI has never run on them (REPO-17). Three dependencies are unused (`livef1`, `signalrcore`, `requests-cache`) and one of them writes `livef1.log` and adds log handlers on import (REPO-18). The `websockets` 17 pin holds Streamlit at 1.59 when 1.64 is current (REPO-19). The lock pins 19 of ~84 packages, and the CI lock check cannot fail (REPO-20). The pre-commit mypy hook reports 156 errors that CI never sees (REPO-21). One msgpack advisory comes in through `signalrcore` (REPO-18). |
| Live feed | The reconnect backoff never resets, so after a few drops every reconnect waits 60 s (LIVE-25). An expired token blocks the free feed too (LIVE-26). State is never reset when the session changes, so qualifying `KO` leaks into the race (LIVE-27). "Go live" can only appear on the last day of an event (LIVE-28). Any viewer can clear or stop the feed for everyone (LIVE-29). The bearer token is logged at `LOG_LEVEL=DEBUG` (SEC-01). |
| Replay / results | A CHEQUERED flag at the end of Q1 marks the rest of qualifying finished and hides a Q2 red flag (REPLAY-17). Deleted laps win the Results tower and the records (REPLAY-18). Saved replays store the circuit corners as a string and crash the map (REPLAY-19). The leader is drawn as "within a second" all race (REPLAY-21). Qualifying cut-offs miscount when a car never ran (REPLAY-22). A GPS dropout reads as a retirement (REPLAY-23). |
| Time parsing / deps | `to_seconds("02:00:00")`, the live session clock, still goes through `pd.to_timedelta` and will silently vanish once NumPy finalises its deprecation (CORE-01). FastF1 3.8.3 pins `pandas<3`, so the project stays on pandas 2.3.3 and needs a NumPy ceiling (REPO-19). |
| UI / player | The SC/VSC/RED map chip stays on screen after the period ends, and an empty driver card is always shown (UI-09). Opening Records loses the replay position (UI-10). A driver click re-sends the whole 2–4 MB payload (UI-11). The shared chart template overrides per-chart hover and legend settings (UI-12). Tower words, contrast and keyboard access need work (UI-13, UI-14, UI-15). |

Not verifiable from the review environment: the real live feed (`livetiming.formula1.com` is unreachable from the sandbox and the linked VM) and real FastF1 sessions (no FastF1 cache in the sandbox). Replay findings were reproduced on the synthetic sessions in `tests/replay_fixtures.py`. REPLAY-25 needs a real red-flag race to confirm.

**LIVE-18 is still open.** The Baku weekend (24–26 Sep) passed with no recording: `replay_sessions/` holds only one legacy `.pkl`. The next window is the **Singapore GP, 9–11 October 2026, a sprint weekend**, so FP1, Sprint Qualifying (SQ1–SQ3), the Sprint, Qualifying and the Race are all on air. Check the session times on formula1.com. Do the pre-weekend live fixes in §4 first.

### 1.2 Live-session checklist (any race weekend)

1. Confirm `main` contains the §4 "before the next race weekend" items. Install with `.venv\Scripts\python -m pip install -r requirements.lock`.
2. Optional, for car telemetry and positions: set `F1TV_SUBSCRIPTION_TOKEN=...` in `.env`. To get it, sign in on formula1.com, open developer tools → Application → Cookies → `login-session`, and copy the value. Tokens last a few days; the Live page shows how many days remain.
3. `.venv\Scripts\python scripts\live_smoke.py 120 --record replay_sessions\raw_<gp>_<session>` during each session. Run the app's Live page separately for a few minutes.
4. Afterwards, write the per-topic counts, the final state and any differences from the recorded 2023 shapes into `tasks.md` (LIVE-18 acceptance).

| Chip | Meaning | What to do |
|---|---|---|
| `CONNECTING` | negotiating / opening the socket | wait a few seconds |
| `WAITING` | connected; only pings arrive | normal between sessions |
| `LIVE` | feed data arriving | — |
| `STALE` | connected but nothing for 60 s; reconnecting | usually recovers by itself |
| `RECONNECTING` | the connection dropped; retrying with backoff | normal, especially ~2 h in |
| `TOKEN NEEDED` | negotiate answered 401 | renew `F1TV_SUBSCRIPTION_TOKEN`. After LIVE-26 the free feed carries on without it |
| `REFUSED` | 403/429: F1 refused this client or IP | wait; don't restart repeatedly; check VPN/proxy |
| `STOPPED` / `OFFLINE` | stopped by the user / never started | press Connect |

---

## 2. What changed in this file

- Revision 3's §1 (Baku runbook), §2 (fixed items) and §3 (open items) are gone. The fixed items are in `tasks.md` "Round 8". The still-valid open items are re-stated in §3 below, each with a **Status (2026-10-02)** line that says what the code already does.
- Folded or renumbered:
  - REPO-16 is folded into REPO-20, because a `uv pip compile --universal` lock writes the environment markers itself.
  - The heartbeat-freshness defect is part of LIVE-23.
  - The live half of CACHE-03 is LIVE-28. Both share one line in `data/fastf1_adapter.py`, so do them together.
- New ID series, continuing the old numbering so commit messages and docstrings stay unambiguous:
  - LIVE-25…36, REPLAY-17…29, UI-09…23, REPO-17…25, HIST-09…11, CACHE-05, TEST-06…09, DOC-06;
  - `SEC-` for security items;
  - `CORE-` for the shared time-parsing and pandas/NumPy layer.
- Research notes §7.2 (replay products) and §7.4 (interface research) are dropped; their conclusions already live in §5. §7.6 is new: the dependency landscape as of this revision.
- §0 gained the git `--no-optional-locks` rule (§0.3) and the round heading for this revision.

---

## 3. Open items

Line numbers refer to `2eb3971`. Grep for the names if the code has moved.

### 3.1 Repository, dependencies and security

- [ ] **REPO-17** · P0 · S — **Push the history (after scanning it) and let CI run**
  - Files: git history, `.github/workflows/ci.yml`.
  - Problem:
    - `origin` (`github.com/mricero/F1-Telemetry-Dashboard`) has 2 old commits ("fixed issues with signalr", "fixed bugs and added stuff"). Local `main` is ~60 commits ahead.
    - All the work exists on one Windows disk, and `ci.yml` has never run. Every "gates green" claim is local.
    - REPO-01 removed a committed virtualenv, so the history may hold large blobs. `.env` may once have held a token.
  - Fix:
    - Check for large blobs: `git rev-list --objects --all | git cat-file --batch-check='%(objectsize) %(rest)' | sort -n | tail -20`.
    - Check for secrets: `git log --all -p -S F1TV_SUBSCRIPTION_TOKEN`, or `gitleaks detect --log-opts=--all`. Clean with `git filter-repo` if anything is found.
    - Check whether the remote's two commits are ancestors: `git merge-base --is-ancestor origin/main main`. If not, rebase or merge them deliberately.
    - Push, then protect `main` so it requires green CI.
  - Acceptance: `origin/main` equals local `main`; the first Actions run is green on every leg; no blob over 512 KB and no secret anywhere in history.

- [ ] **SEC-01** · P1 · S — **The bearer token is logged at `LOG_LEVEL=DEBUG`**
  - Files: `data/signalr_core.py:227-241` (`_default_connect`), `app.py:69-72` (root logger from `LOG_LEVEL`).
  - Problem: at DEBUG, `websockets` logs every handshake header, including `Authorization: Bearer <JWT>`, the `?id=<connectionToken>` path and every frame. DEBUG is documented, and REPLAY-05 used it. This breaks the rule that the token is never logged.
  - Fix: pass `logger=logging.getLogger("data.signalr_core.ws")` to `connect()` and pin it to INFO, or add a redacting `logging.Filter` on the `websockets` loggers.
  - Acceptance: a local `websockets.sync.server`, root logging at DEBUG, connecting with a token: the token string is absent from `caplog.text`.

- [ ] **SEC-02** · P2 · S — **Replay loading trusts `meta.json`; legacy pickles are listed but cannot load**
  - Files: `data/source_manager.py:975-991` (`_load_replay`), `:1063-1071` (replay list).
  - Problem:
    - `frame_dicts` keys become path segments, so `"../x"` reads Parquet from outside the bundle.
    - `values` can set any top-level key, `is_live` and `source` included.
    - `.pkl` replays appear in the picker, but no UI path passes `allow_pickle`, so choosing one always errors.
  - Fix: accept only `FRAME_KEYS` / `FRAME_DICT_KEYS` and the known value keys; reject path separators; hide `.pkl` from the list (REPO-24 converts the one that exists).
  - Acceptance: a crafted meta with `"../evil"` or `"is_live": true` is rejected with a clear message; `.pkl` files are not offered.

- [ ] **REPO-18** · P1 · S — **Remove `livef1`, `signalrcore`, `requests-cache` and the dead LiveF1 loader**
  - Files:
    - `requirements.txt`, `requirements.lock`;
    - `data/source_manager.py:12` (top-level `import livef1`), `:253-386` (`CIRCUIT_MAP`, `_gp_to_circuit_short`, `_load_livef1_session`, unreachable because `:133` raises `NotImplementedError`), plus the unreachable buffer fallbacks for state topics around `:444-460`, `:571-597`, `:759`, `:785`;
    - `tests/test_livef1_source.py`, `tests/test_source_manager.py:89-98`, `tests/test_dependencies.py`;
    - `scripts/inspect_livef1.py`, `readme.md`.
  - Problem:
    - `livef1` is used only by dead code, yet it:
      - adds ~20 runtime packages (pytest, h3, lxml, bs4, setuptools, …);
      - adds ~0.7 s to every start;
      - creates `livef1.log` in the working directory and attaches its own INFO stream and file handlers, so log lines are duplicated.
    - `signalrcore` is never imported. FastF1 pulls it in anyway, along with `msgpack==1.1.2`, which has advisory **PYSEC-2026-3625** (fixed in 1.2.1, found with `pip-audit`). Exposure is low, since no msgpack unpacker is used here.
    - `requests-cache` is never imported; its comment in `requirements.txt` ("HTTP caching for the Jolpica adapter") is false.
    - `test_no_dead_code` skips `_private` names, which is why the dead fallbacks survived.
  - Fix:
    - Delete the three dependencies, the dead loader and fallbacks, their tests and the script. This supersedes revision 1's HIST-03 "keep the code".
    - Add a reverse test: every declared runtime dependency is imported somewhere (allow-list `pyarrow` as the Parquet engine).
    - Add `pip-audit` to CI (TEST-09), with a documented ignore for the msgpack advisory until FastF1 moves.
  - Acceptance: `import data.source_manager` creates no `livef1.log` and adds no handlers; the runtime resolve shrinks by ≥ 15 packages; suite green.

- [ ] **REPO-19** · P1 · S — **Dependency ceilings and upgrades: NumPy < 2.6 while pandas < 3; unblock Streamlit 1.64**
  - Files: `requirements.txt`, `requirements.lock`, `tests/test_dependencies.py`.
  - Problem:
    - FastF1 3.8.3 (latest) requires `pandas<3`, so pandas stays on 2.3.3, the last 2.x. On that pandas, numpy 2.5 deprecates every `pd.Timedelta(seconds=…)` / `pd.to_timedelta(str)` call ("generic unit … will raise an error in the future"). FastF1 itself calls these on every laps load (`core.py` `__fix_tyre_info`, telemetry resampling), so a future NumPy turns this into a broken historical load that this repo cannot patch. `numpy<3` does not stop it; numpy 2.4.6 does not warn.
    - Every Streamlit from 1.60 to 1.64 requires `websockets<17`. With `websockets==17.1` locked, the app is held on 1.59. A plain `pip install -r requirements.txt` (the readme's main step) resolves to 1.64 + websockets 16, a stack CI never tests.
    - The floor `websockets>=13` is too low: the sync client's `proxy` support, which `signalr_core` relies on, arrived in 15.0.
    - `plotly<7` and `pyarrow<25` block plotly 7.1 / pyarrow 25.0.1.
  - Fix:
    - `numpy>=1.26,<2.6` with a comment naming FastF1's `pandas<3` pin;
    - `websockets>=15,<17`, `plotly>=5.22,<8`, `pyarrow>=16,!=25.0.0,<26`;
    - re-lock to streamlit 1.64, websockets 16.1, plotly 7.1, pyarrow 25.0.1, plus current ruff/mypy (bump the pre-commit revs to match);
    - a test that asserts `numpy<2.6` while `pandas<3` is pinned.
    - Check plotly 7 rendering in a browser and `live_smoke.py` on websockets 16 by hand.
  - Acceptance: lock updated; suite green on 3.11–3.13 (and 3.14, TEST-09); the generic-unit warning count from production paths is 0 (CORE-01 handles ours).
  - Depends on: REPO-18.

- [ ] **REPO-20** · P1 · M — **A real lock: every transitive package, hashes, markers; a CI check that can fail** (supersedes REPO-16)
  - Files: `requirements.lock`, new `requirements-dev.lock`, `scripts/lock_requirements.py`, `.github/workflows/ci.yml:33-34`, `tests/test_dependencies.py`.
  - Problem:
    - The lock pins 19 of ~84 packages, so altair, protobuf, scipy, starlette and others float between installs.
    - `pip install --dry-run --no-deps` exits 0 even when the lock contradicts `requirements.txt`, so the CI check cannot fail.
    - `lock_requirements.py` drops the numpy marker (old REPO-16).
    - Dev tools share the lock, so end users install black, mypy and pre-commit.
  - Fix:
    - `uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes -o requirements.lock`, and the same for `requirements-dev.txt` into `requirements-dev.lock`. `--universal` writes the version markers itself.
    - Replace the script with a two-line wrapper or delete it.
    - CI: install with `--require-hashes`, re-compile and `git diff --exit-code`, then `pip check`.
  - Acceptance: every installed package is pinned; editing `requirements.txt` without re-locking turns CI red; the lock installs on 3.11.
  - Depends on: REPO-19.

- [ ] **REPO-21** · P1 · S — **The pre-commit mypy hook fails on `processing/`**
  - Files: `.pre-commit-config.yaml`, `readme.md` (pre-commit section), `tests/test_pre_commit_config.py`.
  - Problem:
    - The hook's isolated env installs unpinned `pandas-stubs`. With `check_untyped_defs` on `processing.*`, it reports **156 errors in 5 files**. CI runs mypy without stubs and passes.
    - Any commit touching `replay_model.py` / `replay_payload.py` is therefore blocked, or made with `--no-verify`.
    - The hook env also lacks numpy and streamlit.
    - `id: ruff` is the legacy alias.
  - Fix:
    - A `repo: local` hook: `entry: python -m mypy --ignore-missing-imports app.py data processing ui`, `language: system`, `pass_filenames: false`.
    - Rename the ruff hook to `ruff-check`.
    - Test that the pre-commit revs equal the lock's versions.
  - Acceptance: `pre-commit run --all-files` is green on a clean checkout.

- [ ] **REPO-22** · P2 · S — **Streamlit sends usage statistics from every viewer's browser**
  - Files: `.streamlit/config.toml`, `tests/test_ui_guideline.py`.
  - Problem: `browser.gatherUsageStats` defaults to `true`, which conflicts with "no external requests" (§0.5, §5.2).
  - Fix: `[browser] gatherUsageStats = false`; while there, consider `[server] enableWebsocketCompression = true` (measure first, REPLAY-29).
  - Acceptance: a guideline test asserts the setting.

- [ ] **REPO-23** · P2 · S — **No version number, no changelog**
  - Files: `pyproject.toml` (no `[project]`), new `CHANGELOG.md`, `data/source_manager.py:903-906` (replay `meta`), `ui/layout.py:119` (`set_page_config`).
  - Problem:
    - Nothing says which build is running.
    - A shared replay's `meta.json` names the app but not its version.
    - Nothing exists to tag a release against.
  - Fix:
    - Add `[project]` with `name`, `version = "0.9.0"`, `requires-python = ">=3.11"`.
    - Read `__version__` from `importlib.metadata`, falling back to `pyproject.toml`.
    - Stamp `meta["app_version"]`.
    - Show the version in `menu_items={"About": …}` and the sidebar footer.
    - Seed `CHANGELOG.md` from the `tasks.md` rounds and tag `v0.9.0` after REPO-17.
  - Acceptance: the version is visible in the app and in a saved replay's `meta.json`; `git tag` lists `v0.9.0`.

- [ ] **REPO-24** · P3 · S — **Working-folder clutter**
  - Files: `replay_sessions/Abu Dhabi Grand Prix_R_20260708_072230.pkl`, `.venv311/` (empty), `test_cache/`, `livef1.log`, `.gitignore`.
  - Fix:
    - `scripts/convert_legacy_replay.py`: load a trusted pickle with `allow_pickle=True`, then `save_replay`. Convert the one `.pkl`, then remove it.
    - Delete `.venv311` and remove its mention (done in §0.2).
    - Ignore `test_cache/` (TEST-08 stops creating it).
    - `livef1.log` disappears with REPO-18.
  - Acceptance: the replay picker lists only replays that load; `git status --ignored` shows no stray files after a test run.

- [ ] **REPO-25** · P3 · S — **LICENSE does not cover the third-party content in the repo**
  - Files: `LICENSE`, `tests/fixtures/live/manifest.json`, `ui/assets/fonts/OFL.txt`.
  - Problem: MIT reads as covering everything, but the repo ships F1 live-timing recordings (FOM data) and Titillium Web (OFL-1.1).
  - Fix: a `NOTICE` (or a LICENSE section) listing the fonts under OFL-1.1 and the fixture data as © Formula One World Championship Ltd, included for testing only and not under MIT. DOC-05 covers the readme side.
  - Acceptance: NOTICE lists both; the readme links it.

- [ ] **REPO-09** · P3 · S — **`python app.py` relaunch hack** (carried)
  - Status (2026-10-02): **still needed.** Streamlit 1.59's "python app.py" support is `st.App("app.py").run()` (`streamlit/web/server/starlette/starlette_app.py`). It is an explicit launcher that runs the Starlette/uvicorn server mode. A plain page script started with `python app.py` still lands in bare mode. Putting `st.App` inside `app.py` would also switch `streamlit run app.py` to ASGI mode through AST discovery.
  - Fix:
    - Option: a separate `run.py` (`st.App("app.py").run()`), then delete `launch_via_streamlit` and `_RELAUNCH_FLAG`. This changes the server backend, so verify the v2 component, fragments and AppTest under it first.
    - Otherwise close the item as "keep".
  - Acceptance: either `python run.py` serves the app with the player working and the hack deleted, or the item is closed with the reason in `tasks.md`.

- [ ] **REPO-10** · P3 · M — **Packaging and layout** (carried)
  - Status: partly done. `app.py` no longer patches `sys.path`, but `scripts/live_smoke.py:22` and `scripts/preview_replay_player.py:18` still do. There is no `src/` layout. REPO-23 adds `[project]`.
  - Fix: `src/f1dash/{data,processing,ui}`, entry point `f1dash = "f1dash.cli:main"` (wraps `streamlit run`). Last in the order: it touches every import.

- [ ] **REPO-13** · P3 · S — **`.env.example` and secrets** (carried)
  - Status: open. There is no `.env.example`. `config.py` reads six variables; the token variable is read in `data/live_adapter.py:158`.
  - Fix: `.env.example` documenting `FASTF1_CACHE_DIR`, `REPLAY_DIR`, `F1_METRICS_STORE`, `F1TV_SUBSCRIPTION_TOKEN`, `F1_NETWORK_TESTS`, `LOG_LEVEL`, `F1_REPLAY_PLAYER` and any flag added by this revision (`F1_LIVE_CONTROLS`, `F1_LIVE_AUTORECORD`). Read the token via `st.secrets` when deployed.
  - Acceptance: a test checks that every `os.getenv`/`environ.get` name in the code appears in `.env.example`.

### 3.2 Live feed

- [ ] **LIVE-25** · P1 · S — **Reconnect backoff never resets after a healthy connection**
  - Files: `data/signalr_core.py:327-367` (`run` loop), `:420-445` (`_stream_once`).
  - Problem:
    - `_stream_once` returns normally only when stopped. Every real drop raises, including a server close, the 60 s silence and F1's ~2 h cut. The `except` path doubles the backoff, so the "had data → reset" branch never runs.
    - Measured on a fake with eight connections that each delivered data and then dropped: waits of 1, 2, 4, 8, 16, 32, 60, 60 s. Late in a race weekend every reconnect costs a minute of timing.
  - Fix: keep `self._had_data`, reset it at the start of `_stream_once` and set it when data arrives. In `except`, reset the backoff to `backoff_start` when it is set.
  - Acceptance: the same fake gives every wait equal to `backoff_start`; a socket that drops without data still doubles.

- [ ] **LIVE-26** · P1 · S — **An expired or rejected token also blocks the free feed**
  - Files: `data/signalr_core.py:339-346`, `:384-387`, `data/live_adapter.py:368-402`, `scripts/live_smoke.py`.
  - Problem:
    - With a stale `F1TV_SUBSCRIPTION_TOKEN`, negotiate answers 401, and the client retries the same token every 120 s for ever. Timing, race control and weather need no token, but never arrive.
    - Topics are fixed at `start_async`, so they cannot drop the auth topics.
    - `live_smoke.py` prints the expiry but does not warn when it has passed.
  - Fix:
    - Do not send a token whose `token_expiry` is in the past.
    - On a 401 sent with a token, retry once at once without it, using a topics provider that drops `AUTH_TOPICS`.
    - Status text "Token rejected – timing only".
  - Acceptance: a fake that answers 401 only when `Authorization` is present connects within `backoff_start`, reaches `LIVE`, and the Subscribe arguments contain no `CarData.z`.

- [ ] **LIVE-27** · P1 · M — **The snapshot merges into old state, and nothing resets when the session changes**
  - Files: `data/live_state.py:117-129` (`seed`), `data/live_adapter.py:290-345`, `:769-772`.
  - Problem:
    - `seed()` deep-merges a new snapshot over the existing state, and nothing clears state, `lap_history`, `_lap_counter` or the buffers when `SessionInfo` changes.
    - Reproduced: after a qualifying snapshot, a race snapshot leaves the race leader at `Status == "KO"`, and qualifying laps stay in `lap_history`.
    - The `[-40:]` dedupe can then drop real race laps that share a (driver, lap) pair.
    - `Stopped`, `Retired` and entries deleted during an outage survive the same way.
  - Fix:
    - `seed()` replaces each topic (`self._topics[topic] = deepcopy(payload)`).
    - When `SessionInfo.Key` (or `Path`) differs from the last one seen, clear state, lap history, lap counter and buffers, then bump the change token.
  - Acceptance: a qualifying snapshot followed by a race snapshot has no `KO` status and an empty `lap_history`; re-seeding the same session keeps `lap_history`.

- [ ] **LIVE-28** · P1 · S — **"Go live" can only appear on the last day of an event** (live half of CACHE-03)
  - Files: `data/fastf1_adapter.py:225-238` (`get_available_sessions` filter), `data/source_manager.py:147-162` (`live_session`), `ui/layout.py:126-128`.
  - Problem:
    - `live_session()` is fed `get_available_sessions()`, which drops every event whose `EventDate` (the last session's date at 00:00) is still ahead. So no practice session, Sprint Qualifying, Sprint or Qualifying is ever detected as live.
    - Reproduced: a clock frozen in Baku qualifying gives 0 events and `None`; the unfiltered schedule gives `Q`.
  - Fix: `FastF1Adapter.get_schedule(years)` without the date filter, used by `live_session()`. Fix CACHE-03 in the same commit.
  - Acceptance: with the clock frozen during a Saturday qualifying (and a Friday Sprint Qualifying), `live_session()` returns that session.

- [ ] **LIVE-29** · P1 · S — **Any viewer can clear or stop the one live feed every viewer shares**
  - Files: `ui/layout.py:996-1038` (`render_live_controls`), `ui/pages.py:220`, `data/live_adapter.py:477-487` (`clear_buffer`).
  - Problem:
    - "Clear buffers" also runs `state.clear()` on the merged state topics, which only come back with the next Subscribe snapshot (the next reconnect, up to ~2 h away). One click empties the tower for everyone.
    - "Stop live" and "Record raw stream" are process-wide and ask for no confirmation. This contradicts "browser tabs only read".
  - Fix:
    - Make Clear empty only the time-series buffers, or resubscribe after clearing.
    - Render the process-level controls only when `F1_LIVE_CONTROLS=1`, or when the request comes from localhost.
    - Confirm Stop with `st.dialog`.
  - Acceptance: after Clear, `poll_live_data()` keeps its standings; with the flag unset, the buttons are not rendered (AppTest).

- [ ] **LIVE-30** · P2 · S — **Refusals at the websocket upgrade are retried hot**
  - Files: `data/signalr_core.py:351-355`, `:393`.
  - Problem: a 401/403/429 on the upgrade raises `websockets.exceptions.InvalidStatus`. The generic `except` treats it as a drop and retries at 1, 2, 4 … s with status `RECONNECTING`, which breaks "a refusal waits 120 s and is shown".
  - Fix: catch `InvalidStatus` and take the `NegotiateError` branch using `exc.response.status_code`.
  - Acceptance: a fake connect raising `InvalidStatus(403)` gives `BLOCKED`, with exactly one attempt inside the back-off window.

- [ ] **LIVE-31** · P2 · S — **A recorder failure freezes live state**
  - Files: `data/live_adapter.py:251-252`, `:334-335`, `:347-363`.
  - Problem:
    - `self.recorder.record(...)` runs before the state update and is not guarded. With `OSError(28)` (disk full), every later message raises and the tower freezes for the rest of the session.
    - `stop_recording()` on the UI thread can null `recorder` between the check and the call, which raises `AttributeError`.
  - Fix: read `rec = self.recorder` once and wrap the call in try/except. On error, log once, set `recorder = None` and keep the error for the Live page.
  - Acceptance: a raising recorder still updates state; `is_recording()` turns False and the error is shown.

- [ ] **LIVE-32** · P2 · S — **Recordings lose reconnect snapshots, so replays end in the wrong state**
  - Files: `data/live_recorder.py:39-42`, `:65-97`, `data/live_adapter.py:334-335`.
  - Problem:
    - Each reconnect overwrites `subscribe.json`, and `replay_recording` applies that latest snapshot first and every message after it.
    - Reproduced: the SC is deployed, the SC ending is missed during an outage, and the reconnect snapshot shows green. The live state ends green, but the replay ends with the SC deployed.
    - This breaks LIVE-18 fixtures and REPLAY-09.
  - Fix: write each snapshot inline in `live.jsonl` as `["__snapshot__", result, utc]` and `seed_state` at that point when replaying. `subscribe.json` stays the first snapshot.
  - Acceptance: the scenario above replays to green; old recordings still replay.

- [ ] **LIVE-33** · P2 · S — **Stop is not honoured during negotiate or connect**
  - Files: `data/signalr_core.py:315-325`, `:383-418`, `data/live_adapter.py:407-419`, `:489-507`.
  - Problem:
    - `_stream_once` does not check `_stop` between negotiate (up to 2 × 15 s), connect (15 s) and Subscribe, and `join(5)` times out.
    - The thread then opens a fresh socket and seeds state after Stop. The status goes STOPPED → WAITING → STOPPED and `is_running()` stays True, so the Live page has no Connect button.
  - Fix: check `_stop` after negotiate and after connect (close and return); never set a status after stop; `start()` joins a lingering thread or uses a fresh Event per run.
  - Acceptance: with a negotiate that sleeps 2 s, `stop()` means connect is never called and the status stays `STOPPED`.

- [ ] **LIVE-34** · P2 · S — **Live rainfall reads "yes" in every dry session**
  - Files: `data/live_adapter.py:630-636` (weather values stay strings), `ui/dashboard.py:216-217` (`bool("0")` is True), `ui/layout.py:766` (`.any()` over `"0"`), `processing/replay_payload.py:169` (same pattern, safe today).
  - Problem: the live `WeatherData` fields are strings (`Rainfall: "0"`), so the dashboard shows rain and the layout warns about it with the 2023 fixture.
  - Fix: `pd.to_numeric` the numeric weather fields in `parse_weather_data`; treat rain as `== 1`.
  - Acceptance: with `tests/fixtures/live/WeatherData.jsonl.gz`, the dashboard shows no rain and the numeric columns have numeric dtypes.

- [ ] **LIVE-35** · P2 · M — **The Live page redraws all four tabs every 3 s**
  - Files: `ui/layout.py:680-692`.
  - Problem: with the Weather tab open, the fragment still builds six telemetry figures across all drivers, the stint chart and a dataframe every 3 s. This overlaps UX-02, which covers the zoom reset and `Scattergl`.
  - Fix: `st.tabs(..., key="live_tab", on_change="rerun")` (available in 1.59) and render only the tab whose `.open` is true.
  - Acceptance: AppTest on the Weather tab counts one `plotly_chart` after a fragment tick.

- [ ] **LIVE-36** · P3 · M — **The live snapshot is rebuilt once per browser tab**
  - Files: `data/source_manager.py:76-78`, `:417-427`, `:639-641`.
  - Problem: the adapter is shared, but each tab's manager holds its own change-token cache, so N viewers mean N full rebuilds every 3 s (budget ~150 ms each), competing with the ingest thread for the GIL.
  - Fix: move the cache (token, snapshot, lock) to the adapter or `live_service`.
  - Acceptance: two managers on one adapter build once per change token.

- [ ] **LIVE-18** · P0 · S · [LIVE] — **Verify the live client against a real session and keep the recording** (carried)
  - Status (2026-10-02): not done; the Baku weekend passed without a recording. Next window: **Singapore, 9–11 Oct 2026 (sprint weekend)**.
  - Files: `scripts/live_smoke.py`, `data/signalr_core.py`, `tests/fixtures/live/`, `scripts/capture_fixture.py`, `tasks.md`.
  - Fix:
    - Follow §1.2 during FP1, Sprint Qualifying, the Sprint, Qualifying and the Race.
    - Where a topic differs from the 2023 shapes, fix the normaliser and add a regression test from a slice of the recording (< 2 MB gzipped; add `capture_fixture.py --from-recording` if convenient).
  - Acceptance:
    - The smoke script shows `LIVE` and non-zero counts for `TimingData`, `TrackStatus`, `RaceControlMessages`, `WeatherData`, `SessionInfo`, `DriverList` and `ExtrapolatedClock` (plus `CarData.z`/`Position.z` with a token).
    - The tower matches F1's own live-timing order.
    - Unplugging the network for 30 s returns to `LIVE`.
    - Results go into `tasks.md`.
  - Depends on: LIVE-25, LIVE-26, LIVE-27, LIVE-30, LIVE-32 (so the recording is trustworthy), CORE-01.

- [ ] **LIVE-19** · P1 · M — **Live qualifying: segments, knock-outs and the segment clock** (carried)
  - Status: not started. `standings_from_state` (`data/live_adapter.py:710-804`) maps `KnockedOut` to `KO` and uses `TimeDiffToFastest`; it reads neither `SessionPart`, `BestLapTimes` nor partitions.
  - Fix:
    - Read `TimingData.SessionPart` (1–3), per-line `KnockedOut`, `BestLapTimes` (per segment) and `Stats`.
    - Order by best time in the running segment; put eliminated drivers under the replay's "Eliminated in Q1/Q2" headings (`Partition` column).
    - Header `Q2` (or `SQ2`) with the remaining time.
    - Confirm the key names on the Singapore SQ/Q recordings first.
  - Acceptance: a fixture inside Q2 shows Q1-eliminated drivers under the heading and the rest ordered by Q2 bests; the same for SQ.
  - Depends on: LIVE-18, LIVE-27.

- [ ] **LIVE-20** · P2 · S — **Record every live session automatically** (carried)
  - Status: not done; recording is only the button at `ui/layout.py:1029-1032`.
  - Fix: `F1_LIVE_AUTORECORD=1` (default on) starts recording into `replay_sessions/raw_<gp>_<session>_<utc>` when the first `SessionInfo` arrives, rotating when it changes.
  - Acceptance: two sessions' `SessionInfo` through `handle_message` produce two directories; with the flag off, nothing is written.
  - Depends on: LIVE-32, LIVE-27.

- [ ] **LIVE-21** · P2 · M — **Broadcast delay** (carried)
  - Status: not done.
  - Fix: ingest stays real-time. Keep one immutable snapshot per second for 5 minutes (`deque`). The Live page reads `snapshot_at(now − delay)` from a 0–300 s sidebar input kept in `st.session_state`.
  - Acceptance: with a 30 s delay, the tower shows the order from 30 s earlier in a simulated feed.
  - Depends on: LIVE-36.

- [ ] **LIVE-22** · P2 · S — **Track state and sector yellows on the live map** (carried)
  - Status: partly done. The header flag reads `TrackStatus` (`ui/dashboard.py:126-131`), but a second, sentence-case chip sits under the dashboard (`ui/layout.py:666-669`; see UI-13). The map tint and chip exist only for replay snapshots (`ui/dashboard.py:491-513`). The live `circuit_info` is `{}` (`source_manager.py:634`), so there are no corners and no marshal sectors.
  - Fix: load `circuit_info` from FastF1 for the event when the live session starts; reuse the replay tint and chip; optional marshal-sector yellows from race-control messages with `Scope == "Sector"`.
  - Acceptance: a live snapshot with `TrackStatus` 4 renders the `SC` chip and tint on the map.

- [ ] **LIVE-23** · P3 · S — **"Last update" freshness** (carried, extended)
  - Status: open. `session_info.last_heartbeat` (`source_manager.py:797`) and `stats.last_data_at` exist but nothing shows them. A Heartbeat does not change the change token, and the cache-hit path refreshes only the clock, so `last_heartbeat` freezes in the cached snapshot (`data/live_adapter.py:254-257`, `:445-454`, `source_manager.py:421-427`).
  - Fix: refresh `last_heartbeat` on the cache-hit path; caption `Last update 3 s ago`, turning into `No update for 45 s` past 30 s.
  - Acceptance: after a Heartbeat the next poll shows the new UTC; a frozen-clock unit test checks both captions.

- [ ] **LIVE-24** · P3 · S — **Token helper** (carried)
  - Status: partly done. Expiry is shown (`ui/layout.py:614-626`); there is no paste box. LIVE-26 is the more urgent half.
  - Fix: a collapsed "Subscription token" expander with a paste box, written to `.env` only on an explicit Save; show the expiry; explain where `login-session` is. No automated login.
  - Acceptance: a JWT with a past `exp` shows "expired"; saving writes exactly one `F1TV_SUBSCRIPTION_TOKEN=` line.

- [~] **REPLAY-09** · P3 · L — **Exact timing-screen replay from F1's static archive through the live pipeline** (carried)
  - **Blocked until LIVE-18 passes and LIVE-32 is done.** Snapshots sampled after a reconnect are wrong until then.
  - Idea:
    - Feed the archive's `TimingData`, `TimingAppData`, `TrackStatus`, `RaceControlMessages`, `WeatherData` and `LapCount` `.jsonStream` files through `handle_message` on a virtual clock (`replay_recording` has no clock yet).
    - Sample `poll_live_data()` each second into keyframes.

### 3.3 Time parsing and the pandas/NumPy layer

- [ ] **CORE-01** · P1 · S — **`to_seconds` still sends `H:MM:SS` and unit strings to `pd.to_timedelta`**
  - Files: `processing/time_utils.py:16-18`, `:43-63`, `:90-93`; caller `data/source_manager.py:40` (`extrapolated_remaining`).
  - Problem:
    - The live `ExtrapolatedClock.Remaining` is `"02:00:00"`. It misses both regexes and falls through to `pd.to_timedelta`. Under NumPy 2.5 that raises the generic-unit DeprecationWarning on every poll; `tests/test_signalr_core.py::TestSessionClock` fails three times under `-W error::DeprecationWarning`.
    - Once NumPy makes it an error, the `except (ValueError, TypeError)` and `errors="coerce"` will swallow it, and the header clock disappears silently. This is the `CLAUDE.md` rule "never `to_timedelta` live strings".
    - `str(Timedelta)` strings (`"0 days 00:01:31.204000"`) take the same path.
    - `"1 L"` parses as 0.001 s, because `'L'` means milliseconds.
    - The scalar and vector parsers disagree: `to_seconds("91")` is None but `seconds_series(["91"])` is 91.0, and bool columns become 1.0/0.0.
  - Fix:
    - One grammar for `to_seconds` and `_M_S_EXTRACT`: `[D days ]H:MM:SS(.f+)`, `M:SS(.f+)` and `SS(.f+)`.
    - Never call `pd.to_timedelta` on a string; unit-suffixed strings return None.
  - Acceptance:
    - `to_seconds("02:00:00") == 7200`; `to_seconds("0 days 00:01:31.204000") == 91.204`; `to_seconds("1 L") is None`.
    - No warning under `-W error::DeprecationWarning`.
    - Property test: `seconds_series(s).equals(s.map(to_seconds))`.

- [ ] **CORE-02** · P2 · S — **Silent-downcasting FutureWarnings that change meaning under pandas 3**
  - Files: `data/live_adapter.py:972-978` (`ffill().bfill()` on object flag columns in a `groupby.transform`), `data/source_manager.py:648` (`_ever_true`), `data/fastf1_adapter.py:737`, `processing/timing.py:241` (`_valid_laps`: `Deleted` is all-None when race control failed to load), `processing/timing.py:329`.
  - Problem:
    - Completed laps from `lap_history` lack `InPit/PitOut/Retired/Stopped` (NaN), while in-progress rows hold bools, so the columns are `object`. The silent object→bool downcast is deprecated.
    - With `future.no_silent_downcasting` (the pandas 3 default), a driver missing from the current `Lines` keeps NaN, and `~frame["Retired"]` raises `TypeError`.
  - Fix: build the flags per driver from `lines`, then `.map(...).astype(bool)`. Elsewhere use `.astype("boolean").fillna(False)` or `.eq(True)`.
  - Acceptance: the live, timing and source-manager tests pass with `-W error::FutureWarning` and `pd.set_option("future.no_silent_downcasting", True)`; the flag columns are `bool`.

### 3.4 Replay, results and historical data

- [ ] **REPLAY-17** · P1 · S — **A CHEQUERED flag at the end of Q1 marks the rest of qualifying as finished**
  - Files: `processing/replay_model.py:433-458` (`chequered_times`), `:461-475` (`flag_state`), `:478-492` (`flag_timeline`), `:1320-1326` (`events`), `processing/replay_payload.py:307`.
  - Problem:
    - Race control shows a track-wide CHEQUERED flag at the end of Q1, Q2 and Q3 (and of SQ1–SQ3).
    - `flag_timeline` keeps only the points before `flags[0]`, and `flag_state` returns CHEQUERED from `flags[0]` onwards.
    - Reproduced: qualifying with a Q2 red flag gives `[(0, GREEN), (1440, CHEQUERED)]`. The red flag vanishes from the tint, the chip and the timeline.
  - Fix:
    - Races keep the current rule.
    - In Q/SQ, each CHEQUERED ends its segment; the state returns to track status at the next `segment_starts` entry.
    - Practice uses only the last CHEQUERED.
    - One `flag` event per segment.
  - Acceptance: in that scenario, `flag_timeline` contains RED at 1800, `flag_state(1850) == "RED"`, and CHEQUERED appears once per segment.

- [ ] **REPLAY-18** · P1 · S — **Deleted laps win the Results tower, the micro-sectors and the records**
  - Files: `processing/timing.py:362-372` (`fastest_lap_row`), `:625-626`, `:938-950` (`best_seconds` over all laps), `:1009-1023`; `processing/metrics_store.py:91-137`.
  - Problem:
    - Only the sector bests go through `_valid_laps`. Reproduced on practice with E's 1:30.500 `Deleted`: the Results tower ranks E P1 in purple, while the replay's end state ranks E P3.
    - `MetricsStore` permanently records E 1:30.500.
    - Micro-sector bounds come from the deleted lap, but the telemetry comes from FastF1's `pick_fastest()`, a different lap.
  - Fix: use `_valid_laps` for best, session best, `fastest_lap_row` and the metrics lap/sector records. Still show a deleted lap as "last", with a struck-through marker and `DeletedReason` in its `title` (the start of FEAT-11).
  - Acceptance: the Results tower and the replay's end state agree; the store records the fastest valid lap.

- [ ] **REPLAY-19** · P1 · S — **Saved replays crash the map: `circuit_info.corners` is stored as a string**
  - Files: `data/source_manager.py:934-939` (`meta["values"]` written with `json.dumps(default=str)`), `:980`, consumer `processing/track_geometry.py:134-137`.
  - Problem:
    - The corners DataFrame becomes its `repr` string. After the round trip, `TrackGeometry.corners()` raises `ValueError: DataFrame constructor not properly called!`, so the payload and the map fail for every saved replay that has corners.
    - `session_info.date` comes back as `str`.
    - `tests/test_replay_format.py` only uses `{"rotation": 12.5}`.
  - Fix:
    - Write the corners as `circuit_info.corners.parquet` and restore the date as a timestamp.
    - Reject non-JSON values instead of `default=str`.
    - Bump `REPLAY_SCHEMA_VERSION` to **8**; a schema ≤ 7 `str` corners value loads as an empty frame.
  - Acceptance: save → load returns an equal corners frame and the payload builds; a schema-7 replay with string corners opens without a map crash.

- [ ] **REPLAY-20** · P2 · M — **Snapshots know about lap deletions before the stewards announce them**
  - Files: `processing/replay_model.py:390` (`valid` from the final `Deleted`), `:660`, `:742`, `:901`, `:1312`, `processing/timing.py:851`.
  - Problem:
    - FastF1 derives `Deleted` from a later race-control message ("… TIME 1:29.123 DELETED", possibly REINSTATED). At the moment the lap ends, the real screen showed it as best; the snapshot never does.
    - Fastest-lap events, `last_flag`, the qualifying order and knock-outs all use knowledge from minutes later.
    - `_garbled` (the no-future-leak test) scrambles `Deleted` only on future laps, so it cannot catch this.
  - Fix:
    - Derive `deleted_at` per lap from the message's `SessionTime`, reusing FastF1's patterns including reinstatement.
    - A lap counts as valid until `deleted_at`; recompute the series at deletion moments.
  - Acceptance: a lap deleted 90 s after it ends is session best in `snapshot_at(t+30)` and not in `snapshot_at(t+120)`; a new no-future-leak case covers late deletions.

- [ ] **REPLAY-21** · P2 · S — **The race leader is highlighted as "close" and has a 0.000 s interval trend**
  - Files: `processing/replay_model.py:810-821`, `processing/replay_payload.py:237-258` (`_interval_trend`, `_close_series`), `ui/components/replay_player/player.js:632`, `:737-760`.
  - Problem:
    - The leader's `IntervalToPositionAhead` is `"LAP n"`, which parses to 0.0, so `close` is True for whoever leads.
    - The sparkline reads "Interval to the car ahead: 0.000 s" and drops to 0 whenever a car takes the lead.
  - Fix: `interval_s` and `close` are None while the raw cell is lap-form or the settled position is 1.
  - Acceptance: the leader's `close` is False at every t, and the trend values are None while leading.

- [ ] **REPLAY-22** · P2 · S — **Qualifying cut-offs count cars with laps, not the entry list**
  - Files: `processing/timing.py:662-700` (`qualifying_cutoffs(len(rows))`).
  - Problem: with 22 entries and one car that never ran, the cut-offs become `[15, 10]`, and the official P16, who set a Q2 time, is shown as "Eliminated in Q1". The same happens with 19 runners in 2018–25.
  - Fix: count the entries (`results` or the drivers table); a Q2 or Q3 time also proves the segment was reached.
  - Acceptance: that scenario puts the "Eliminated in Q1" heading above P17.

- [ ] **REPLAY-23** · P2 · M — **A GPS dropout over 5 s makes a race car OUT and adds a "Retirement" event**
  - Files: `processing/replay_model.py:47` (5 s threshold), `:594-605`, `:624-632`, `:1157`, `:1305-1310`.
  - Problem: removing 7 s of a car's position samples mid-race gives `OUT` and a `Retirement - B` event, and drops its lap in progress from the snapshot. Tunnels (Monaco, Singapore) and feed dropouts make this common.
  - Fix:
    - In races, require a longer silence (20–30 s) and no timing progress (sector stamps or stream rows) during it.
    - Exclude red-flag windows.
    - Emit "Retirement" only for an OUT that never ends before the flag; any shorter silence is not an event.
  - Acceptance: a 7 s gap gives no OUT and no event; a car that stops for good still gives one.

- [ ] **REPLAY-24** · P2 · S — **The qualifying segment clock runs through red flags**
  - Files: `processing/replay_model.py:1254-1268` (`segment_elapsed`).
  - Problem: the session clock stops under a red flag, but after a 30-minute Q1 suspension the header reads "Q1 time 0:48:00" for an 18-minute segment.
  - Fix: subtract the overlap of `Status == "5"` windows inside the segment. Optionally show the remaining time (18/15/12 min in 2026) and label sprint segments SQ1–SQ3.
  - Acceptance: with track status 5 in a test, `segment_elapsed` is frozen between the red flag and the restart.

- [ ] **REPLAY-25** · P2 · S — **Pit-lane entries under a red flag count as pit stops** (confirm on real data first)
  - Files: `processing/replay_model.py:703-706` (`pits` = count of `PitInTime`), `:1299-1303`.
  - Problem: under a red flag every car enters the pit lane, so every car gets Pits = 1 and about 20 "Pit stop" events appear within seconds.
  - Fix: label entries during status 5 as "Red flag" and exclude them from `pits`, or compare with TimingAppData stint changes.
  - Acceptance: confirmed or refuted on 2023 Australia R (three red flags); a synthetic red flag leaves `pits` unchanged.

- [ ] **REPLAY-26** · P3 · S — **The Results tower breaks tied laps by row order, not by who set the time first**
  - Files: `processing/timing.py:625-626` (`_classify_by_best_lap`), `:1009-1013`.
  - Fix: sort by `(best, time the best was set)`.
  - Acceptance: A sets 1:30.500 at 383 s and E at 503 s; A is P1 in both the Results tower and the replay.

- [ ] **REPLAY-27** · P3 · S — **A pit-lane starter shows ON TRACK while waiting at pit exit**
  - Files: `processing/replay_model.py:573-587`, `:615`.
  - Fix: in races, when lap 1 has `PitOutTime` and no earlier `PitInTime` (grid 0), open an IN PIT window `[lights_out, pit_out)`.
  - Acceptance: a fixture with a pit-lane starter shows IN PIT at lights out + 5 s.

- [ ] **REPLAY-28** · P3 · M — **`tower_series` spends most of its time in per-value `pd.isna`**
  - Files: `processing/replay_model.py:199-252`, `:784-821`.
  - Problem: a 22-car, 57-lap race (31 k stream rows) takes ~0.45 s. That includes ~637 k `pd.isna` calls through `_clean`; `_race_from_stream` is 80 % of it.
  - Fix: clean stream columns once per column with vectorised operations; build `_series` from numpy change masks (`v[1:] != v[:-1]`).
  - Acceptance: `tower_series` on the big synthetic race < 0.15 s, with identical series (equality test).

- [ ] **REPLAY-29** · P3 · M — **Replay payload size (1.6–3 MB uncompressed)**
  - Files: `processing/replay_payload.py:95-118`, `:237-248`, `:318`.
  - Problem: positions are 1.31 MB of base64 Int16 for a 93-minute race. `trend`, sampled every 5 s, is 37 % of the non-position payload, although `interval_s` is already a change-point series. `server.enableWebsocketCompression` is off.
  - Fix: ship `interval_s` change points and drop `trend`; delta-encode positions (Int8 deltas plus keyframes) or quantise to 0.5 viewBox units; measure compression (REPO-22).
  - Acceptance: the reference race payload shrinks by ≥ 40 %; the player tests pass.

- [ ] **HIST-09** · P2 · S — **A session in progress, or not archived yet, fails to load or is cached partial**
  - Files: `data/fastf1_adapter.py:164-166`, `:257`, `:488`, `data/source_manager.py:180-209`, `app.py:119-120`.
  - Problem: a session becomes selectable once it has started (the session-level filter). If FastF1 soft-fails the lap load, `session.laps` raises `DataNotLoadedError` ("…has not been loaded yet. See `Session.load`"); otherwise a partial dict is runtime-cached for the life of the process. This comes from reading FastF1's source; reproduce it with a mock.
  - Fix: catch `DataNotLoadedError` and empty laps, and say "not in F1's archive yet (usually 1–2 h after the session)". Don't runtime-cache sessions that ended less than ~3 h ago, or give them a TTL.
  - Acceptance: a mocked session without `_laps` shows that message; a recent session is not cached.

- [ ] **HIST-10** · P3 · S — **The FastF1 cache is re-enabled for every browser tab**
  - Files: `data/fastf1_adapter.py:198-202`, `data/source_manager.py:68-69`.
  - Problem: each `DataSourceManager` calls `fastf1.Cache.enable_cache`, which builds a new requests-cache SQLite session without closing the old one, possibly in the middle of another tab's load. An unused Jolpica `requests.Session` is also created per tab.
  - Fix: enable once per process behind a module guard; build the Jolpica session lazily.
  - Acceptance: after two managers, FastF1's cached session is the same object.

- [ ] **HIST-11** · P3 · S — **Jolpica memo ignores keyword arguments; `Retry-After` is uncapped**
  - Files: `data/jolpica_adapter.py:22-55`.
  - Problem: `get_practice_results(2024, 1, session="2")` raises `TypeError`. A 429 carrying `Retry-After: 3600` blocks the Streamlit script thread for an hour.
  - Fix: key the memo on `(args, frozenset(kwargs.items()))`; cap `Retry-After` at ~10 s and raise beyond that.
  - Acceptance: the keyword call works; `Retry-After: 3600` raises quickly. Patch `time.sleep` in the fixture (TEST-06).

- [ ] **CACHE-05** · P3 · S — **The metrics store never corrects a record and crashes on a malformed file**
  - Files: `processing/metrics_store.py:44-51`, `:115-137`.
  - Problem: per-session records are replaced only when beaten, so a wrong value (REPLAY-18, or a later FastF1 data fix) stays for ever. A JSON file holding `[]` makes `load()` raise `AttributeError` at app start.
  - Fix: recompute and overwrite each session's records (only "all-time" stays a running minimum); reset the store when the top level is not a dict, keeping a `.bak`.
  - Acceptance: corrected laps lower or raise the stored record; a list-JSON file loads empty with a logged warning.

- [ ] **HIST-08** · P2 · S — **FastF1 first load is slow; drivers load serially; changing scope reloads** (carried)
  - Status: partly done. Per-driver progress exists ("Telemetry i/N" in `st.status`, `source_manager.py:178-188`). Still open:
    - no `st.cache_resource` for the `Session`;
    - the loop is serial (`:186-193`);
    - `telemetry_scope` is in the runtime-cache key (`app.py:88`);
    - the `session` scope merges every driver twice (`:198-207`);
    - `record_metrics` forces `views["telemetry"]()` (`app.py:209`), defeating the lazy telemetry build.
  - Fix: cache the loaded session per `(year, gp, session)` with `max_entries=3`; derive scope frames from it; compute telemetry lazily; measure a `ThreadPoolExecutor` for the merges.
  - Acceptance: switching scope on a warm session < 2 s; loading does not build the telemetry alignment until Analysis opens.

- [ ] **CACHE-02** · P2 · M — **Metrics store: cross-circuit "all-time", writes, concurrency** (carried)
  - Status: partly done. Rerun writes are gone (recorded once per tab per session, `app.py:202-213`), and live sessions are no longer recorded. Still open:
    - `update_laps` saves unconditionally (`metrics_store.py:138`);
    - all-time records compare circuits (`:160-176`);
    - top speed comes from scope-limited telemetry (`:141-155`);
    - each tab holds its own in-memory store, so the last writer wipes other tabs' records;
    - the `.tmp` filename is shared.
  - Fix: key by `(year, round, session_type)`; all-time per circuit; top speed from `laps.SpeedST/SpeedFL`; write only on change; SQLite in WAL mode.
  - Acceptance: 100 reruns with nothing changed write 0 times; two concurrent writers keep both records; all-time is grouped by circuit.
  - Depends on: CACHE-05, REPLAY-18.

- [ ] **CACHE-03** · P2 · S — **The current weekend's sessions are hidden until race day** (carried)
  - Status: valid. `fastf1_adapter.py:225-238` filters on `EventDate` (the last session's date at 00:00). The session-level filter is already done (`:144-168`).
  - Fix: include an event once its first session has ended; shorter schedule TTL on race weekends. Same commit as LIVE-28.
  - Acceptance: on Saturday of a sprint weekend, FP1, SQ and the Sprint are selectable.

- [ ] **CACHE-04** · P3 · S — **FastF1 cache directory hygiene** (carried)
  - Status: valid. `config.py:15` and `fastf1_adapter.py:198` default to `./ff1_cache`, relative to the working directory. The cache in the maintainer's folder already holds eleven sessions plus the HTTP SQLite.
  - Fix: default to `platformdirs.user_cache_dir("f1-telemetry-dashboard")` (and the same for replays and the metrics store when not overridden); show the size and a clear button on the Settings page (UI-22).
  - Acceptance: with no env vars, nothing is written inside the repository.

### 3.5 User interface and the replay player

Every item here follows §5. No XSS was found: every `st.html` value is escaped, numeric or passed through `safe_hex`; the player only uses `textContent`/`setAttribute`. The player's listener, rAF and ResizeObserver cleanup is clean.

- [ ] **UI-09** · P1 · S — **The map's SC/VSC/RED chip stays after the period ends; an empty driver card is always shown**
  - Files: `ui/components/replay_player/player.css:58-61` (`.rp-chip { display: inline-block }`), `:135` (`.rp-card { display: grid }`), `player.js:249`, `:469-470`, `:676`, `:715`.
  - Problem:
    - An author `display` rule beats the browser's `[hidden]` rule. Reproduced in Chromium: seeking from under an SC to after it, the header reads GREEN while the map chip still says "SC".
    - With no driver focused, a lone "Analyse this lap" button shows and does nothing.
  - Fix: `.rp [hidden] { display: none !important; }`, or toggle a class instead. Disable Analyse with no focus.
  - Acceptance: a Playwright or jsdom test seeks into and past an SC period and finds the chip not rendered; with nothing focused the card is not rendered.

- [ ] **UI-10** · P1 · S — **Opening Records (or Live) loses the replay position** (REPLAY-14 regression)
  - Files: `ui/pages.py:80-84`, `:173-199`.
  - Problem: `records_page` and `live_page` never set `LAST_PAGE_KEY`, so `sync_seek_cursor` is skipped on return and the player remounts at the last Python seek. Reproduced: Replay at 0:03:00 → Records → Replay shows 0:02:00.
  - Fix: track the current page in `main()` by comparing the `st.navigation(...)` result with the previous one, instead of setting it in each page.
  - Acceptance: AppTest Replay → seek → Records → Replay gets the reported cursor back in the player's `data.cursor`.

- [ ] **UI-11** · P2 · S — **A driver click re-sends the whole player payload; each rerun serialises it twice**
  - Files: `ui/replay_view.py:374-383`, `ui/components/replay_player/__init__.py:106`, `player.js:913-930` (`update()` ignores `data.focus`).
  - Problem:
    - `focus` is part of `data`, so each tower or car click changes the element hash and re-sends the payload: 115 KB per click on the 26 KB fixture, 2.6–4 MB on a real race.
    - Streamlit also runs `json.dumps(data)` twice per rerun (proto plus a `sort_keys` identity), about 50 ms per MB even when nothing changed.
  - Fix:
    - Send focus as a mount-time value that only changes together with `seek`, or drop it from `data`.
    - Later, serve the payload once (static/media URL plus a version) and pass only the URL.
  - Acceptance: AppTest shows the component's JSON is byte-identical before and after a focus change.

- [ ] **UI-12** · P2 · S — **The shared chart template overrides each chart's own layout**
  - Files: `ui/layout.py:56-59` (`_plot` applies `chart_layout` last), `:363-369`, `:731-738`, `ui/theme.py:178-204`.
  - Problem: the Positions chart asks for `hovermode="closest"` and a vertical legend at x = 1.01, but gets "x unified" (a 20-driver tooltip) and a horizontal legend anchored off the plot. The Telemetry and Weather legends are overridden the same way.
  - Fix: apply the template first (as `layout.template`, or `update_layout(chart_layout)` before the chart's own `update_layout`).
  - Acceptance: a unit test where a chart's `hovermode` and `legend` survive `_plot`.

- [ ] **UI-13** · P2 · S — **Tower vocabulary and formats break §5.6/§5.7 where the guideline test can't see**
  - Files: `processing/timing.py:312`, `:339-359`, `:878`, `:1026`; `ui/dashboard.py:152`, `:213`, `:224`, `:317-324`; `ui/layout.py:46-53`, `:448`, `:457`, `:528`, `:666-669`, `:764`, `:854-855`.
  - Problem:
    - A `CLASSIFIED` chip on every qualifying and practice row.
    - `IN PIT` instead of `PIT`.
    - A `+1L` chip, where lapped cars belong in the gap column.
    - Speed `0` for a car in the pit.
    - `--` / `--:--:--` instead of `–`.
    - Lap-time hover shows `0 days 00:01:30`.
    - The live track-status chip says "Track clear" / "Safety car" / "VSC ending" instead of GREEN/SC/VSC.
    - Named colours `"red"` / `"gray"` slip past the hex check.
    - Race-control text goes through `st.markdown`, so `*` and `$…$` in feed text are interpreted.
  - Fix:
    - Map statuses to the §5.6 vocabulary (empty on track); use the shared missing-value constant everywhere and `format_lap` for hover.
    - Use theme tokens for colours.
    - Show race control with `st.text`/escaped HTML.
    - Extend check 5 of `test_ui_guideline.py` to CSS colour names.
  - Acceptance: new guideline assertions over the tower HTML for the race, qualifying and practice fixtures.

- [ ] **UI-14** · P2 · S — **Contrast below 4.5:1 for session-best times and RED chips**
  - Files: `ui/theme.py:25`, `:89-98`; `player.css:12`, `:111`.
  - Problem:
    - `--best` text measures 3.9:1 on `--surface` and 3.6:1 on `--surface-2` (13 px purple lap times).
    - The RED chip (white on the red flag colour) is 3.91:1 at 11 px, and it is reused for TOKEN NEEDED and REFUSED.
    - §5.11 states that the tokens pass.
  - Fix:
    - A lighter `--best-text` for purple times; `#b138dd` stays for fills and flashes.
    - A darker red or dark text on the RED chip.
    - Record both in §5.4.
  - Acceptance: a test computes WCAG contrast for every `FLAG_STATES` fg/bg pair and for every time-text token on both surfaces, all ≥ 4.5:1.

- [ ] **UI-15** · P2 · M — **Player keyboard and screen-reader gaps**
  - Files: `player.js:235-240`, `:309-323`, `:360-398`, `:434-466`, `:907-910`.
  - Problem:
    - Tower rows and map cars are click-only `div`/`g` elements, so a keyboard user cannot focus a driver. That also locks out the driver card, "Analyse this lap" and follow mode.
    - The Gap/Interval and Labels toggles lack `aria-pressed`; the race-control toggle lacks `aria-expanded`.
    - Follow (F) has no visible control.
    - The timeline has no `role="slider"` / `aria-valuetext`.
  - Fix: make rows `button`s (or `role="row"` + `tabindex` + Enter/Space); ↑/↓ moves focus between drivers; add the ARIA states; add a "Follow" toggle.
  - Acceptance: a jsdom test tabs to a row, presses Enter and sees the card; the toggles report `aria-pressed`.

- [ ] **UI-16** · P3 · S — **Player shortcuts fire with Ctrl/Cmd held**
  - Files: `player.js:886-911` (`onKey`).
  - Problem: Ctrl/Cmd+F (find in page) also toggles follow, Ctrl+L toggles labels, and Cmd+1…8 (switch tab) changes the speed.
  - Fix: `if (event.ctrlKey || event.metaKey || event.altKey) return;` at the top.
  - Acceptance: a jsdom keydown `{key: "f", ctrlKey: true}` leaves follow off.

- [ ] **UI-17** · P2 · S — **Shared-link parameters load unvalidated, and the sidebar disagrees with what loaded**
  - Files: `ui/layout.py:184-224`.
  - Problem:
    - `?year=2017`, `?gp=Nope Grand Prix` and `?session=XYZ` are committed and loaded as given, while the sidebar shows other values.
    - FastF1 fuzzy-matches event names, so a typo silently loads a different GP.
    - A crafted link starts cold downloads.
  - Fix: validate against `_event_names_cached`, `_session_codes_cached` and `FIRST_SEASON`. On a mismatch, `st.warning("Link refers to an unknown session")` and load nothing.
  - Acceptance: AppTest with bad parameters gives `selection is None` and a warning; after a valid link, the picker values equal the loaded selection.

- [ ] **UI-18** · P2 · S — **Per-session processed views are never evicted**
  - Files: `app.py:161-199`, `ui/replay_view.py:134-143`, `:405-422`.
  - Problem:
    - `processed:{key}` holds the session dict, the laps and, once Analysis has been opened, the aligned telemetry. Loading four sessions in one tab leaves four entries.
    - REPLAY-15 evicted only the model and payload, so this still defeats the runtime cache's byte budget.
    - `replay_sector_cards:*` accumulates the same way.
  - Fix: `_evict_other_sessions("processed", key)`, and the same for the sector memo.
  - Acceptance: after loading three sessions, exactly one `processed:*` entry remains.

- [ ] **UI-19** · P2 · S — **On phones the sidebar covers the data on every first load**
  - Files: `ui/layout.py:119` (`initial_sidebar_state="expanded"`).
  - Fix: `"auto"`, or `"expanded"` only while `st.session_state.get("selection") is None`.
  - Acceptance: Playwright at 375 px with a shared link shows the header bar unobstructed.

- [ ] **UI-20** · P3 · S — **Mobile Results: sector-3 times are clipped**
  - Files: `ui/theme.py` (`.f1-sectors { grid-template-columns: repeat(3, 1fr) }` inside `.f1-dash { overflow: hidden }`).
  - Fix: `repeat(auto-fit, minmax(150px, 1fr))`, or one column under 600 px.
  - Acceptance: at 360 px no `.f1-sector-time` has `scrollWidth > clientWidth`.

- [ ] **UI-21** · P3 · S — **The Analysis section resets on every page round trip**
  - Files: `ui/pages.py:144-153`.
  - Fix: Streamlit 1.59's `segmented_control(..., persist_state="session")`, or `bind="query-params"`, which also covers part of FEAT-14.
  - Acceptance: AppTest Weather → Replay → Analysis still shows Weather.

- [ ] **UI-22** · P3 · M — **No About, Settings or cache controls**
  - Files: `ui/layout.py:119`, `ui/pages.py`, `.streamlit/config.toml:12-13` (`toolbarMode = "minimal"` hides Streamlit's own "Clear cache").
  - Problem: a user cannot drop the 1-hour schedule cache (stale on race weekends) or the runtime cache, see the version, or change units.
  - Fix:
    - A `Settings` page with "Clear cached schedules" (`.clear()` on the `@st.cache_data` functions), a runtime-cache clear, the FastF1 cache size and location (CACHE-04), and units (UX-12).
    - `menu_items={"About": …}` with the version (REPO-23).
    - Page name `Settings`, per §5.9.
  - Acceptance: AppTest clears the caches; About shows the version.
  - Depends on: REPO-23.

- [ ] **UI-23** · P3 · S — **The driver card's DOM is rebuilt every animation frame**
  - Files: `player.js:713-759`.
  - Problem: while a driver is focused, the last-laps list and the sparkline SVG are recreated about 60 times a second. That contradicts `layout.md` §9.2 ("text is rewritten only when a value changes").
  - Fix: memoise on `(code, laps.length, last trend index)`.
  - Acceptance: a jsdom mutation counter sees no card-child mutations across frames within one lap.

Carried UI items (text as in revision 3; statuses checked against the code on 2026-10-02):

- [ ] **UX-02** · P2 · M — **Plotly performance and live chart behaviour.** Status: valid. There is no `Scattergl` and no `uirevision`, and the template forces "x unified" (`ui/theme.py:200`). Fix: `go.Scattergl` for telemetry and laps; `uirevision=<session key>`; decimate to the 5 m grid; default to a driver filter (UX-03); `hovermode="x"` with a compact template. Acceptance: a live fragment update keeps a zoomed range (AppTest asserts `uirevision`). Depends on: UI-12.
- [ ] **UX-03** · P2 · S — **Driver selection and favourites.** Status: valid; every chart plots every driver. Fix: a global driver multiselect (default top 5) with favourites in `st.query_params`; highlight favourites in the tower.
- [ ] **UX-04** · P2 · M — **Head-to-head is speed-only and fastest-lap-only.** Status: valid (`ui/layout.py:861-948`). Fix: stacked Speed/Throttle/Brake/Gear/Δt sharing x; corner markers from `circuit_info.corners`; lap pickers per driver; keep the "approximate" caption.
- [ ] **UX-06** · P2 · S — **Race-control panel usability.** Status: valid. The key is still `"rc_categories"` (`ui/layout.py:836`), there is no search, and SC/VSC/red shading exists only on the player timeline. Fix: a per-session key, a search box, and SC/VSC/red shading on the lap-time and position charts.
- [ ] **UX-12** · P3 · S — **Units, time zones, preferences.** Status: valid. km/h and °C are hard-coded; `gmt_offset` is stored (`source_manager.py:774`) but never read. Fix: km/h ↔ mph, °C ↔ °F, and local vs track time on the Settings page (UI-22) and in URL params.

### 3.6 Tests and CI

- [ ] **TEST-06** · P2 · S — **The suite hides ~15 000 warnings, some of which will become errors**
  - Files: `pytest.ini`, `tests/test_replay_model.py:101`, `:216`, `:256`, `:430-435`, `:486`, `tests/perf/test_hot_paths.py:47`, `tests/test_fastf1_adapter.py:662-713`, `tests/test_jolpica_adapter.py:216`.
  - Problem:
    - `pytest.ini` has no `filterwarnings` and no `--strict-markers`.
    - Test code builds `pd.Timedelta(seconds=…)`, which numpy 2.5 deprecates.
    - Two class-scoped fixtures are written as instance methods (`PytestRemovedIn10Warning`).
    - The slowest test really sleeps 2 s in the Jolpica throttle.
  - Fix:
    - `filterwarnings = error`, plus one documented ignore for FastF1-internal generic-unit warnings while on pandas 2.
    - Add `--strict-markers`.
    - In tests, use `pd.Timedelta(x, unit="s")`.
    - Use `@classmethod` or module-scope fixtures.
    - Patch `time.sleep` in the Jolpica fixture.
  - Acceptance: the suite passes under the new filters with < 100 warnings.
  - Depends on: CORE-01, CORE-02.

- [ ] **TEST-07** · P2 · S — **A timing test fails about 2 runs in 7**
  - Files: `tests/perf/test_hot_paths.py:63-80` (`test_it_is_not_slower_than_the_loop_it_replaced`).
  - Problem: it compares two timings about 1.0–1.3× apart, with 25 % slack. It failed in a full run and in 1 of 6 isolated runs. `test_it_matches_the_scalar_parser` already covers correctness.
  - Fix: drop the relative timing assert, or move it behind a `perf` marker excluded by default (part of TEST-04).
  - Acceptance: 20 consecutive full runs green.

- [ ] **TEST-08** · P2 · S — **Tests and CI write into the working tree**
  - Files: `tests/test_fastf1_adapter.py:88`, `:107+`, `tests/conftest.py`, `.github/workflows/ci.yml:46-47`, `tests/test_repo_hygiene.py`, `.gitignore`.
  - Problem:
    - `FastF1Adapter()` with defaults enables a real `./ff1_cache/fastf1_http_cache.sqlite`, and `:88` creates `./test_cache` (not ignored).
    - In CI, `FASTF1_CACHE_DIR: ~/.cache/fastf1` is not tilde-expanded, so a literal `./~/.cache/fastf1` appears.
    - The hygiene test runs `git ls-files` with `check=True` and errors in a source ZIP.
  - Fix:
    - An autouse conftest fixture pointing `FASTF1_CACHE_DIR`, `REPLAY_DIR` and `F1_METRICS_STORE` at `tmp_path`.
    - `${{ runner.temp }}/fastf1` in CI.
    - Skip the hygiene test outside a git work tree.
  - Acceptance: a full run leaves `git status --ignored` unchanged.

- [ ] **TEST-09** · P2 · M — **CI coverage gaps**
  - Files: `.github/workflows/ci.yml`, new `.github/dependabot.yml`.
  - Problem:
    - Only Ubuntu with 3.11–3.13. The maintainer runs Windows with 3.14, so that combination is never tested.
    - `cache: pip` keys on `requirements.txt`, not the lock.
    - mypy runs twice per leg (the step plus `tests/test_type_checking.py`).
    - No `permissions`, `concurrency` or `timeout-minutes`.
    - Node-20 actions (`checkout@v4`, `setup-python@v5`).
    - No dependabot (which is how Streamlit fell five minors behind) and no `pip-audit`.
  - Fix:
    - Add `windows-latest` × {3.11, 3.14} and `ubuntu` 3.14 legs.
    - `cache-dependency-path: requirements*.lock`.
    - Deselect `test_type_checking` in CI.
    - Add `permissions: contents: read`, a concurrency group and a 20-minute timeout.
    - Use `checkout@v5` / `setup-python@v6`.
    - Dependabot for pip, github-actions and pre-commit.
    - A `pip-audit` step.
  - Acceptance: Windows and 3.14 legs green; the cache key changes with the lock; dependabot opens PRs.
  - Depends on: REPO-17, REPO-20.

- [ ] **TEST-03** · P2 · S — **Network tests never run automatically** (carried). Status: valid; the 25 network tests have never run in CI. Fix: a nightly + `workflow_dispatch` workflow with `F1_NETWORK_TESTS=1`, the FastF1 cache via `actions/cache`, and an issue opened on failure. Depends on: REPO-17.
- [ ] **TEST-04** · P2 · S — **CI-stable performance budgets** (carried). Status: partly done (plain-test budgets in `tests/perf/` and `TestPerformance`). TEST-07 is the first concrete case. Fix: best-of-N or relative budgets behind a `perf` marker, run in their own CI job.
- [ ] **TEST-05** · P3 · S — **Coverage and property tests** (carried). Status: partly done. `pytest-cov` is installed but CI runs `pytest -v` without `--cov`, and `hypothesis` is not installed. Fix: `--cov` with a floor; `hypothesis` for `to_seconds` (with CORE-01), `deep_merge`, `segment_boundaries`, `resample_to_distance_grid`.

### 3.7 Documentation

- [ ] **DOC-06** · P2 · S — **`ARCHITECTURE.md` and the historical docs contradict the code**
  - Files: `ARCHITECTURE.md` (config block, project tree, replay format, CI section), `PHASE1_RESEARCH_SUMMARY.md`, `layout.md`, `tests/test_docs_live_claims.py`.
  - Problem:
    - The config block lists `distance_step` and `cache_ttl_seconds`, which `config.py` doesn't have.
    - The tree lists 5 of the 10 `data/` modules, 4 of 9 in `processing/` and 1 of 11 in `ui/`.
    - Replays are described as pickled `.pkl`; they are Parquet directories.
    - CI is described as "ruff + black + pytest"; it also runs mypy across a matrix.
    - PHASE1 says LiveF1's client uses "the same SignalR endpoint", a hub that answers 401.
  - Fix:
    - Correct those sections.
    - Move `PHASE1_RESEARCH_SUMMARY.md` to `docs/history/` with a "superseded" banner; keep `layout.md` but mark which sections are binding (§9 is).
    - Extend the docs test so every module under `data/`, `processing/` and `ui/` appears in ARCHITECTURE.md.
  - Acceptance: that test passes; grep finds no `.pkl` replay claim.

- [ ] **DOC-02** · P2 · S — **readme drift** (carried, extended). Status: every revision-1 problem is still there:
  - the incomplete tree;
  - two "### 5." headings;
  - the placeholder clone URL (use `github.com/mricero/F1-Telemetry-Dashboard` after REPO-17);
  - "Auto-Detection … real-time endpoint probing";
  - the fallback claim;
  - LiveF1 advertised.

  Also:
  - replays described as `.pkl`;
  - "adjust `distance_step` in config.py";
  - "zlib compressed" (it is raw DEFLATE);
  - "Auto"/"LiveF1" sources and a "LIVE SESSION DETECTED" badge that doesn't exist;
  - a Streamlit 1.35+ badge (the floor is 1.55);
  - a config table missing `F1TV_SUBSCRIPTION_TOKEN` and `LOG_LEVEL`;
  - "refresh the app" on timeouts (it reconnects itself);
  - the broken mypy hook (REPO-21).

  Acceptance: a grep for `.pkl`, `distance_step`, `zlib`, `LiveF1` and `1.35+` in the readme is clean, apart from legacy-format notes.
- [ ] **DOC-03** · P2 · S — **`CLAUDE.md` / `tasks.md` out of date** (carried). Status: partly done (`tasks.md` has rounds 6–8; `CLAUDE.md` links this file). Still open:
  - Windows-only commands (`CLAUDE.md:14-27`);
  - "four review rounds";
  - no mention of `ui/dashboard.py`, `processing/timing.py`, `ui/track_map.py`;
  - "CI … on Python 3.12" (it is a 3.11–3.13 matrix plus mypy);
  - `tasks.md` §9 still lists the tower and dominance map as not started.

  Fix: cross-platform commands, and add a "Round 9" heading to `tasks.md` with the first item done from this revision.
- [ ] **DOC-04** · P3 · S — **`dashboard_preview.html` (86 KB) is a stale artefact** (carried). Status: tracked and referenced nowhere. Fix: delete it; `scripts/preview_replay_player.py` already generates a current preview.
- [ ] **DOC-05** · P3 · S — **Data sources and terms** (carried). Status: the readme has only the "unofficial" and IP-block notes. Fix: a "Data sources & terms" section covering:
  - unofficial endpoints;
  - subscription data needs the user's own F1TV account and must not be redistributed;
  - hosting publicly risks IP blocks;
  - Jolpica fair use;
  - OpenF1's paid real-time tier.

  Link NOTICE (REPO-25).

### 3.8 Features (carried backlog)

Still valid. Each gets `Files:` and `Acceptance:` written in as the first step of its iteration. New UI follows §5.

- [x] **FEAT-01** · M — **Race trace and gap chart:** gap to the leader or to a reference driver per lap, with SC/VSC shaded. Status: open; only the player card's 5-minute sparkline exists. Prefer the payload's `interval_s` change points (REPLAY-29) over a new sampling. — done
- [ ] **FEAT-02** · M — **Pit rejoin predictor:** current gap minus the circuit's pit loss, from a per-circuit table seeded from historical `PitInTime → PitOutTime` medians.
- [ ] **FEAT-03** · M — **Tyre degradation and stint pace:** fuel-corrected lap time against tyre age per compound, excluding in/out laps, SC laps and `IsAccurate == False`. Status: open; the laps frame drops FastF1's `TrackStatus` column (`data/fastf1_adapter.py:491-525`), which is needed to exclude SC laps. Keep it.
- [ ] **FEAT-05** · S — **Team radio list:** OpenF1 `team_radio` (historical, free from 2023); the live feed's `TeamRadio` is auth-gated. Respect the token rules.
- [ ] **FEAT-06** · S — **Standings panels** via Jolpica (`get_driver_standings` / `get_constructor_standings` exist with no callers), with a points-after-this-race projection for live races.
- [ ] **FEAT-07** · S — **Linear track-position strip:** every car on a straight 0 → lap-length line, readable on mobile, good for spotting overtake-mode trains.
- [ ] **FEAT-09** · S — **Speed-trap and sector ranking panel.** Status: partly done (sector top-3 cards and a top-speed column exist); still missing the I1/I2/FL/ST ranking.
- [ ] **FEAT-10** · M — **Customisable layout:** column toggles and a panel checklist kept in query params.
- [ ] **FEAT-11** · S — **Track limits / deleted laps view** from race control and FastF1 `Deleted`/`DeletedReason`. Status: `DeletedReason` is not kept. REPLAY-18 and REPLAY-20 lay the groundwork.
- [ ] **FEAT-12** · S — **2026 regulation context:** hide the DRS channel for 2026+ instead of plotting a flat zero (the DRS tab is always rendered, `ui/layout.py:388-397`); label active aero and overtake mode where data exists.
- [ ] **FEAT-14** · S — **Share links.** Status: partly done; year/gp/session and the page are in the URL. Still missing: the drivers, the analysis section and the replay cursor. Streamlit 1.59's widget `bind="query-params"` can carry them (with UI-21).

---

## 4. Execution order

```
First (safety of the work itself):
  REPO-17 -> SEC-01

Before the next race weekend (Singapore, Fri 9 Oct 2026), so the live check is trustworthy:
  LIVE-25 -> LIVE-26 -> LIVE-30 -> LIVE-27 -> LIVE-33 -> LIVE-31 -> LIVE-32
  -> CORE-01 -> LIVE-28 + CACHE-03 -> LIVE-29 -> LIVE-34
During the weekend:
  LIVE-18   FP1, Sprint Qualifying, Sprint, Qualifying, Race (section 1.2)
  LIVE-19   once the SQ/Q recordings exist

Correctness of what users already see:
  REPLAY-19 -> REPLAY-17 -> REPLAY-18 -> UI-09 -> UI-10 -> REPLAY-21 -> REPLAY-22
  -> REPLAY-24 -> REPLAY-23 -> REPLAY-20 -> REPLAY-25 -> REPLAY-26 -> REPLAY-27
  -> HIST-09 -> CACHE-05 -> SEC-02

Dependencies, warnings, CI:
  REPO-18 -> REPO-19 -> REPO-20 -> REPO-21 -> CORE-02 -> TEST-06 -> TEST-07 -> TEST-08
  -> TEST-09 -> REPO-22 -> TEST-03

UI polish:
  UI-13 -> UI-14 -> UI-12 -> UI-18 -> UI-11 -> UI-17 -> UI-19 -> UI-20 -> UI-21
  -> UI-16 -> UI-15 -> UI-23 -> LIVE-35

Then the carried backlog:
  LIVE-20 -> LIVE-23 -> LIVE-22 -> LIVE-36 -> LIVE-21 -> LIVE-24
  HIST-08 -> CACHE-02 -> CACHE-04 -> HIST-10 -> HIST-11
  REPO-23 -> UI-22 -> UX-02 -> UX-03 -> UX-06 -> UX-04 -> UX-12
  REPLAY-28 -> REPLAY-29 -> TEST-04 -> TEST-05
  FEAT-01 -> FEAT-03 -> FEAT-02 -> FEAT-07 -> FEAT-09 -> FEAT-06 -> FEAT-11 -> FEAT-12
  -> FEAT-14 -> FEAT-05 -> FEAT-10
  REPLAY-09 (once LIVE-18 has passed)
  REPO-25 -> REPO-24 -> REPO-13 -> REPO-09 -> DOC-06 -> DOC-02 -> DOC-03 -> DOC-04 -> DOC-05
  -> REPO-10 (restructure last: it touches every import)

Distribution (section 10), as soon as REPO-17, REPO-18, REPO-23 and CACHE-04 are done:
  DIST-01 -> DIST-02 -> DIST-03 -> DIST-04 -> DIST-05 -> DOC-07 -> DIST-06
```

Each group ends with all gates green and one line per item in `tasks.md`.

## 5. UI guideline (binding for every UI change)

This is the design contract for everything a user sees: Streamlit pages, the HTML tower and header, the SVG map, Plotly charts, the replay component, and all interface text. It exists because the current UI has the marks people now associate with machine-generated interfaces (emoji as icons, generic defaults, decorative chrome), and because a timing product is judged on how quickly and calmly it can be read.

### 5.1 What this product should feel like

A **broadcast timing screen and a results sheet**, not a landing page. Reference points studied (revision 3 §7.4, in git history): the timing tower on F1 broadcasts and F1's own site, results tables on established sports sites, live flight maps (map + list + timeline), and financial terminals. What they share:

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


---

## 7. Research notes

### 7.1 F1 live timing in 2026 (SignalR Core)

- **Endpoint and handshake.**
  - FastF1 3.7+ (`fastf1/livetiming/client.py`) uses `wss://livetiming.formula1.com/signalrcore`.
  - It pre-negotiates with `OPTIONS …/signalrcore/negotiate` to collect the `AWSALBCORS` cookie, handles `on("feed")`, and invokes `Subscribe`, whose completion is the full-state snapshot.
  - It has no reconnect.
- **Confirmed against the 2026 feed** by pitwall, a Python live strategy engine run live at the 2026 Dutch GP:
  - the legacy `/signalr/negotiate` answers 401;
  - `POST /signalrcore/negotiate?negotiateVersion=1` returns `connectionToken`, and the socket opens with `?id=<token>`;
  - handshake `{"protocol":"json","version":1}`;
  - message types 3 (snapshot), 1 (feed `[topic, data, timestamp]`) and 6 (ping every ~15 s, the only traffic between sessions);
  - `User-agent: BestHTTP`;
  - the feed "reliably disconnects after approximately two hours".
- **What needs a subscription.** Since the 2025 Dutch GP the free feed no longer carries car telemetry or positions, the DRS indicator, championship tables or pit-stop times. Post-session archives still have everything.
- **Refusals happen.** `f1_sensor` issue #611 (2026) reports a persistent 403 on negotiate for some users. Hosted projects cite IP restrictions. Run locally, with one connection and your own token.
- **Tokens** are JWTs, usually valid for a few days. The `login-session` cookie is URL-encoded JSON holding `data.subscriptionToken`.

### 7.3 Streamlit facts used by this plan (pinned 1.59.0; checked in the installed source)

- `st.components.v2.component(name, html=, css=, js=, isolate_styles=True)` registers an inline component with no build step.
  - **Every state change reruns the script and re-serialises `data`**, twice: once for the proto and once with `sort_keys` for identity (UI-11).
  - Components are trusted code; never put feed strings into `innerHTML`.
- `st.navigation(position="top")`, `st.segmented_control` (with `persist_state="session"` and `bind="query-params"`), `st.tabs(..., on_change="rerun")` with `.open`, `st.status` and `st.dialog` all exist in 1.59.
- `st.App("app.py").run()` is the 1.59 programmatic launcher. It runs the Starlette/uvicorn server mode; a plain `python app.py` still means bare mode (REPO-09).
- `st.html` sanitises with DOMPurify unless `unsafe_allow_javascript` is set, and drops inline `<svg>` (hence §5.13).
- `browser.gatherUsageStats` defaults to `true` and `server.enableWebsocketCompression` to `false` (REPO-22).
- `@font-face` rules inside a shadow root are ignored by browsers, so fonts are declared once in the main document (§5.3).

### 7.5 FastF1 facts used by this plan (3.8.3, installed source and the local cache)

- `fastf1._api._extended_timing_data(path)` returns `(laps_data, stream_data, session_split_times)`. `stream_data` columns are `Time, Driver, Position, GapToLeader, IntervalToPositionAhead`. The result is cached as `_extended_timing_data.ff1pkl`.
- `session._session_split_times`, real values:
  - 2023 Bahrain Q: `[0, 2758.7 s, 4138.7 s]`;
  - 2023 Bahrain R: `[0, 1 day, 1 day]`.
- `session.session_start_time` is the session-status "Started" time.
- `Deleted` / `DeletedReason` are set from later race-control messages, including reinstatements (REPLAY-20).
- FastF1 itself builds `pd.Timedelta(days=1)`, `pd.Timedelta(milliseconds=1)` and `pd.Timedelta(seconds=1/frequency)` on every load, which numpy 2.5 + pandas 2.3 deprecate (REPO-19).
- 2023 Bahrain R: 28 475 timing-stream rows; the lapped form in the cache is `"1 L"`.

### 7.6 Dependency landscape (PyPI, 2026-10-02)

| Package | Pinned | Latest | Constraint that matters |
|---|---|---|---|
| fastf1 | 3.8.3 | 3.8.3 | `pandas<3.0.0,>=2.1.1`, `numpy<3` |
| pandas | 2.3.3 | 3.0.6 | blocked by FastF1; 2.3.3 is the last 2.x |
| numpy | 2.4.6 / 2.5.1 | 2.5.3 | 2.5 deprecates generic-unit timedeltas used by pandas 2.3 and FastF1 → ceiling `<2.6` |
| streamlit | 1.59.0 | 1.64.0 | 1.60+ require `websockets<17` |
| websockets | 17.1 | 17.1 | must drop to 16.x for Streamlit 1.64; ≥ 15 for the sync client's proxy support |
| plotly | 6.8.0 | 7.1.0 | the repo caps `<7` |
| pyarrow | 24.0.0 | 25.0.1 | Streamlit excludes 25.0.0 |
| msgpack (via signalrcore/FastF1) | 1.1.2 | — | `pip-audit`: PYSEC-2026-3625, fixed in 1.2.1 |

A trial of pandas 3.0.6 passed every processing, UI, source-manager and replay-format test except the 20 `TestNoFutureLeaks` cases. Those fail in the test helper `_garbled` (copy-on-write: "output array is read-only"; use `.to_numpy(copy=True)`). Plan the move once FastF1 lifts its pin.

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


---


---

## 9. Sources

Live timing
- [FastF1 livetiming client source (main)](https://github.com/theOehrly/Fast-F1/blob/main/fastf1/livetiming/client.py) · [FastF1 issue #753 — live timing moved to signalrcore](https://github.com/theOehrly/Fast-F1/issues/753) · [FastF1 PR #760 — SignalR Core + F1 account auth](https://github.com/theOehrly/Fast-F1/pull/760)
- [Ark07Yad/pitwall — live SignalR Core feed verified against the 2026 endpoint](https://github.com/Ark07Yad/pitwall) (`src/pitwall/feed/signalr.py`)
- [JustAman62/undercut-f1 README — what needs an F1TV subscription](https://github.com/JustAman62/undercut-f1)
- [Nicxe/f1_sensor issue #611 — 403 on signalrcore negotiate](https://github.com/Nicxe/f1_sensor/issues/611)
- [Connecting to the SignalR F1TV data endpoint (classic protocol, historical)](https://dweik.xyz/post/f1-signalr-endpoint/)
- [Singapore GP 2026 (9–11 October, sprint weekend) — GPFans](https://www.gpfans.com/us/f1-race-calendar/singapore-grand-prix-2026) · [Formula 1 2026 calendar](https://www.formula1.com/en/latest/article/formula-1-reveals-calendar-for-2026-season.YctbMZWqBvrgyddrnauo8)

Platform
- [Streamlit — st.components.v2.component](https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component) · [Component mounting](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/mount) · [Interactive counter example](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/examples/interactive-counter)
- [FastF1 documentation](https://docs.fastf1.dev/)
- PyPI metadata (2026-10-02): [fastf1](https://pypi.org/project/fastf1/) · [streamlit](https://pypi.org/project/streamlit/) · [pandas](https://pypi.org/project/pandas/) · [numpy](https://pypi.org/project/numpy/) · [websockets](https://pypi.org/project/websockets/) · [plotly](https://pypi.org/project/plotly/) · [pyarrow](https://pypi.org/project/pyarrow/)
- [pip-audit](https://pypi.org/project/pip-audit/) (msgpack PYSEC-2026-3625)

Interface guidance
- [7 Signs a UI Has Been Vibe Coded — The Fountain Institute](https://www.thefountaininstitute.com/blog/signs-vibe-coded-ui)
- [AI Slop Fonts and Gradients: The Tells That Give Away AI Design — 925 Studios](https://www.925studios.co/blog/ai-slop-design-tells)
- [funboy322/avoid-ai-design (pattern catalogue)](https://github.com/funboy322/avoid-ai-design)
- [Why Every AI-Built Website Looks the Same (Tailwind indigo-500) — DEV](https://dev.to/alanwest/why-every-ai-built-website-looks-the-same-blame-tailwinds-indigo-500-3h2p)
- [The Purple Gradient Problem — DEV](https://dev.to/james_anderson_h/the-purple-gradient-problem-why-ai-ui-all-looks-alike-and-how-to-fix-it-3j65)
- [Dashboards: Making Charts and Graphs Easier to Understand — Nielsen Norman Group](https://www.nngroup.com/articles/dashboards-preattentive/)
- [Titillium Web (SIL Open Font License)](https://fonts.google.com/specimen/Titillium+Web) — download once, commit with the licence; never load at runtime

Local evidence
- Installed source: `fastf1/_api.py` (`_extended_timing_data`), `fastf1/core.py` (`_load_laps_data`, `_session_split_times`, `__fix_tyre_info`), `streamlit/components/v2/` and `streamlit/components/v2/bidi_component/main.py` (mount and serialisation), `streamlit/web/server/starlette/starlette_app.py` (`st.App`), `streamlit/config.py` (`gatherUsageStats`).
- Revision 4 review scripts and screenshots (not committed): reproductions for LIVE-25…34, REPLAY-17…27, UI-09…21 against `tests/replay_fixtures.py` sessions and the recorded live fixtures, AppTest runs, and Chromium screenshots at 1440 px and 375 px.

---

## 10. Command-line install with uv (distribution)

**Goal.** Anyone on Windows, macOS or Linux installs, runs and updates the dashboard from GitHub with a couple of commands. There is no exe build and no manual Python install.

- `uv` downloads a matching Python itself.
- `uv` installs from `requirements`-equivalent pins in seconds from its cache.
- `uv` keeps the app in an isolated tool environment.

This is the chosen alternative to a frozen PyInstaller build: it avoids the 300–500 MB bundle, unsigned-exe warnings, antivirus false positives and Streamlit freezing problems.

Target user experience (written into the readme by DOC-07):

```powershell
# Windows, one line (installs uv if needed, then the app, plus a Start-menu shortcut)
irm https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.ps1 | iex

# or by hand, any OS
uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard
f1dash                 # opens the dashboard in the browser
f1dash update          # newest release
uv tool uninstall f1dash
```

The PyPI names `f1dash` and `f1-telemetry-dashboard` were free on 2026-10-02 (`f1-replay` is taken). `uv tool upgrade` is documented for registry installs. For git installs, `--reinstall` is the reliable update path until confirmed otherwise.

Depends on, from §3:

- REPO-17: the code must be on GitHub.
- REPO-23: a version number.
- CACHE-04: user data directories.
- REPO-18: drop the dead dependencies first, so the install is smaller.

- [ ] **DIST-01** · P1 · S — **Make the project an installable package**
  - Files: `pyproject.toml` (new `[project]` and `[build-system]`), `requirements.txt`, `tests/test_dependencies.py`.
  - Problem: there is no `[project]` table, so nothing can be `pip`/`uv` installed. The app, its assets, the player files and `.streamlit/config.toml` are loose files found relative to the working directory.
  - Fix:
    - Add `[project]`: `name = "f1dash"`, the version from REPO-23, `requires-python = ">=3.11"`, `dependencies` equal to `requirements.txt` (one source of truth: generate one from the other, or make `requirements.txt` read `-e .`), and `[project.scripts] f1dash = "f1dash_cli:main"`.
    - Build backend: `hatchling`. Include `app.py`, `config.py`, `f1dash_cli.py`, `data/`, `processing/`, `ui/` (with `ui/assets/**` and `ui/components/replay_player/*.{js,css,html}`) and `.streamlit/config.toml` as package data.
    - Keep the flat layout for now; REPO-10 moves it under `src/f1dash/` later.
  - Acceptance:
    - `uv build` produces a wheel containing every file above, checked by a test that lists the wheel.
    - `uv tool install dist/*.whl` followed by `f1dash --version` prints the version in a clean temp directory.
    - Dependencies in `pyproject.toml` equal `requirements.txt`, checked by a test.
  - Depends on: REPO-23, REPO-18.

- [ ] **DIST-02** · P1 · S — **The `f1dash` command**
  - Files: new `f1dash_cli.py`, `tests/test_cli.py`.
  - Problem: Streamlit apps start with `streamlit run app.py`, from the folder holding `.streamlit/config.toml`. An installed tool has neither a known path nor that working directory.
  - Fix:
    - `main()` finds the installed `app.py` next to the module.
    - It reads the bundled `.streamlit/config.toml` and passes each key as a `--section.key=value` flag. This keeps the theme, `baseRadius`, `toolbarMode` and REPO-22's `gatherUsageStats = false`.
    - It calls `streamlit.web.cli.main(["run", app_path, *flags, "--server.port", port])`. Going through `streamlit run` keeps `app.py`'s bare-mode relaunch out of play.
    - Options: `--port` (default 8501, falling back to the next free port), `--no-browser`, `--version`, and the subcommands `update` (DIST-05) and `paths` (prints where cache, replays, records and `.env` live).
    - It loads `.env` from the user config directory (DIST-03) before starting.
  - Acceptance: a unit test with `streamlit.web.cli.main` monkeypatched asserts the argv (app path, every theme flag equal to `config.toml`, the port). `f1dash --no-browser --port 8599` serves HTTP 200 on `/` from a temp directory, behind the `network`-free smoke marker.
  - Depends on: DIST-01.

- [ ] **DIST-03** · P1 · S — **User data directories for an installed app**
  - Files: `config.py`, `data/fastf1_adapter.py`, `processing/metrics_store.py`, `data/live_adapter.py` (token from `.env`).
  - Problem: the FastF1 cache (`./ff1_cache`), replays, `metrics_store.json` and `.env` default to the working directory. Installed, that would be wherever the user typed `f1dash`, or the tool's `site-packages`.
  - Fix:
    - When the code is not running from a git checkout (no `.git` next to `app.py`), default to `platformdirs`: `user_cache_dir("f1dash")/fastf1`, `user_data_dir("f1dash")/replays`, `user_data_dir("f1dash")/metrics_store.json` and `user_config_dir("f1dash")/.env`.
    - Environment variables still override.
    - A checkout keeps today's paths, so development does not change.
    - Add `platformdirs` to the dependencies.
    - This implements CACHE-04 for the installed case; do both together.
  - Acceptance:
    - With no env vars and an installed wheel, a load writes nothing inside the tool environment or the current directory.
    - `f1dash paths` prints the four locations.
    - The checkout behaviour is unchanged (test).
  - Depends on: DIST-01, CACHE-04.

- [ ] **DIST-04** · P2 · S — **One-line installers: `install.ps1` and `install.sh`**
  - Files: new `install.ps1`, `install.sh` (repository root).
  - Fix:
    - The scripts are idempotent, so re-running one also updates.
    - Step 1: install uv if `uv` is missing, using Astral's official installer (`irm https://astral.sh/uv/install.ps1 | iex`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`).
    - Step 2: `uv tool install --python 3.12 --reinstall git+https://github.com/mricero/F1-Telemetry-Dashboard@<latest release tag>`. Take the tag from `https://api.github.com/repos/mricero/F1-Telemetry-Dashboard/releases/latest`, falling back to `main`.
    - Step 3: `uv tool update-shell` so `f1dash` is on `PATH`.
    - Step 4, Windows only: create a Start-menu shortcut "F1 Replay" running `f1dash`.
    - The scripts print the next step ("Run f1dash") and the token hint.
    - No admin rights. The scripts never touch the user's own Python, and never collect or write a token.
  - Acceptance: a CI job on `windows-latest` and `ubuntu-latest` runs the script from the checkout (pointing at the local path instead of the git URL), then `f1dash --version`. A second run succeeds as well.
  - Depends on: DIST-02, REPO-17.

- [ ] **DIST-05** · P2 · S — **Update check and `f1dash update`**
  - Files: `f1dash_cli.py`, `ui/layout.py` (sidebar footer / About, with UI-22), new `data/update_check.py`.
  - Fix:
    - `f1dash update` asks the GitHub releases API for the latest tag and runs `uv tool install --reinstall git+https://github.com/mricero/F1-Telemetry-Dashboard@<tag>`. Once the package is on PyPI (DIST-06), it runs `uv tool upgrade f1dash` instead.
    - The app checks the same API at most once a day, cached in the user cache directory, with a 3 s timeout. It is silent offline and can be disabled with `F1_UPDATE_CHECK=0`.
    - When a newer version exists, the sidebar footer shows `Update available: v0.10.0 – run f1dash update` in plain text, following §5.9 (no emoji, no exclamation mark).
  - Acceptance:
    - A mocked API answering a newer tag shows the caption; an equal tag or a network error shows nothing.
    - `f1dash update` builds the expected `uv` argv (unit test with `subprocess.run` mocked).
  - Depends on: DIST-02, REPO-23.

- [ ] **DIST-06** · P2 · S — **Release workflow: build, smoke-install, publish**
  - Files: new `.github/workflows/release.yml`, `CHANGELOG.md`.
  - Fix:
    - The workflow runs on a pushed `v*` tag.
    - It runs `uv build` and checks the tag equals the package version.
    - Smoke install on `windows-latest` and `ubuntu-latest`: `uv tool install dist/*.whl` then `f1dash --version`.
    - It creates the GitHub Release with the wheel, the sdist, `install.ps1`/`install.sh` and the CHANGELOG section as notes.
    - Optionally it publishes to PyPI with trusted publishing (`uv publish`), so users can `uv tool install f1dash` / `uv tool upgrade f1dash`. Reserve the name first.
  - Acceptance: tagging `v0.9.0` produces a Release with the assets, and the smoke legs pass. With PyPI enabled, `uv tool install f1dash==0.9.0` works.
  - Depends on: DIST-01, TEST-09, REPO-17.

- [ ] **DOC-07** · P1 · S — **Update the GitHub readme for this installation**
  - Files: `readme.md`, `tests/test_docs_live_claims.py`.
  - Problem: the readme's install section is a developer setup (clone, virtualenv, `pip install -r requirements.txt`, `streamlit run app.py`), and its clone URL is a placeholder (`your-username/f1-telemetry-dashboard`). After DIST-01…04 the readme on GitHub is the first thing a user sees, so it must lead with the uv install.
  - Fix: rewrite the top of `readme.md` as **Install** → **Run** → **Update** → **Uninstall** → **Where your data lives** → **Live timing token**, then move the existing developer setup under **Development**:
    - the Windows one-liner `irm https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.ps1 | iex` and the macOS/Linux `curl -LsSf …/install.sh | sh`;
    - the manual route: install uv, then `uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard`;
    - running `f1dash` (and `f1dash --port`), plus `uvx --from git+https://github.com/mricero/F1-Telemetry-Dashboard f1dash` to try it without installing;
    - updating with `f1dash update` (or `uv tool install --reinstall …`), uninstalling with `uv tool uninstall f1dash`;
    - `f1dash paths` and the `.env` location for `F1TV_SUBSCRIPTION_TOKEN`;
    - the development setup, using the real URL `https://github.com/mricero/F1-Telemetry-Dashboard.git` and `uv pip install -r requirements.lock`.

    Push the change so github.com shows it. Fold the DOC-02 items about the install section into this commit.
  - Acceptance:
    - A docs test asserts the readme contains `uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard`, `install.ps1`, `f1dash update`, `uv tool uninstall f1dash`, and no `your-username`.
    - The rendered readme on github.com shows the Install section first.
  - Depends on: DIST-04, DIST-05, REPO-17.

Execution order for this section, after REPO-17, REPO-18, REPO-23 and CACHE-04 from §4:

```
DIST-01 -> DIST-02 -> DIST-03 -> DIST-04 -> DIST-05 -> DOC-07 -> DIST-06
```
