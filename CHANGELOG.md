# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/). The version lives in
`pyproject.toml`; `from config import __version__` reads it back.

Item IDs (`LIVE-01`, `REPLAY-05`, ...) refer to `IMPROVEMENTS.md`; `tasks.md`
has one line per item with the details.

## [Unreleased]

## [0.9.0] - 2026-10-02

The first versioned release. It collects the eight review rounds recorded in
`tasks.md`, plus the revision 4 work on packaging and distribution.

### Added

- Install as a tool: `uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard`,
  then run `f1dash`. The `f1dash` command takes `--port`, `--no-browser` and
  `--version`, and the subcommands `paths` and `update` (DIST-01, DIST-02, DIST-05).
- One-line installers `install.ps1` and `install.sh` (DIST-04).
- An installed copy keeps its cache, replays, records and `.env` in the per-user
  directories; a git checkout keeps the repo-local paths (DIST-03, CACHE-04).
- A daily update check that names a newer release, disabled with
  `F1_UPDATE_CHECK=0` (DIST-05).
- `__version__`, this changelog and a release workflow (REPO-23, DIST-06).
- `.env.example` listing every environment variable the app reads (REPO-13).
- `NOTICE` for the bundled font and the recorded F1 timing fixtures (REPO-25).

Round 8 - live feed and replay fixes

- Live mode connects to `wss://livetiming.formula1.com/signalrcore` with its own
  client, pings every 10 s, reconnects with backoff and shows a feed status
  (LIVE-01, LIVE-08).
- `.z` topics are decoded at ingest, so car data and positions reach every panel
  (LIVE-17).

Round 7 - replay and UI

- The replay is the main view: a browser-side player with the timing tower,
  track map, track-state overlay, race control and a focused-driver card
  (REPLAY-01..10).
- Qualifying and practice replays with segment clocks (REPLAY-06).
- A dark, self-contained interface in pages, with the session picker in the
  sidebar and nothing loaded until "Load session" (UI-00..07).

Round 6 - IMPROVEMENTS.md audit loop

- Replays are Parquet directories with a `meta.json`, not pickles (HIST-02).
- Session replay on a shared 2 Hz clock (FEAT-04).
- Timing tower ordered by the official classification, knock-out styling in
  qualifying, distance-based dominance and mini-sectors (DASH-01..12).
- A recorded 2023 Bahrain race as the live-feed fixture; delta topics are merged
  into state (TEST-01, LIVE-05).
- One live connection per process (LIVE-09); live laps, telemetry and GPS trails
  are segmented per lap (LIVE-13).
- Ruff with bugbear, bandit and pandas rules, mypy in CI, pre-commit hooks
  (REPO-04, REPO-06, REPO-07).

Rounds 1-5 - first audits

- Two-tier caching: a process-lifetime runtime cache for whole sessions and a
  persistent records store.
- `python app.py` re-enters through `streamlit run` instead of starting in
  Streamlit's bare mode.

### Changed

- Dependencies have upper bounds and a universal, hashed lock for runtime and
  development (`requirements.lock`, `requirements-dev.lock`); Streamlit 1.64,
  websockets 16, plotly 7 (REPO-19, REPO-20).
- CI runs Ubuntu 3.11-3.14 and Windows 3.11/3.14, re-locks and fails on a diff,
  and audits the lock (TEST-09).

### Removed

- `livef1`, `signalrcore` and `requests-cache`, and the dead LiveF1 loader
  (REPO-18).
- `dashboard_preview.html`; `scripts/preview_replay_player.py` builds a current
  preview (DOC-04).

### Fixed

- Round 4: the crash on a real load (`'NoneType' object has no attribute 'get'`)
  and the data-correctness issues it hid: FastF1 position data has no `Speed`
  channel, `IsPitOutLap` is derived, X/Y/Z are in 1/10 m.
- Round 3 and earlier: a `SyntaxError` in `ui/layout.py`, an uninitialised
  Jolpica adapter, timezone-naive schedule comparisons.

[Unreleased]: https://github.com/mricero/F1-Telemetry-Dashboard/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/mricero/F1-Telemetry-Dashboard/releases/tag/v0.9.0
