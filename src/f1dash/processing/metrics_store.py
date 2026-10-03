"""Persistent session metrics store.

Records derived statistics - fastest lap, fastest sector per sector, and
top speed - per session, and keeps them across app restarts (unlike the
ephemeral :mod:`data.runtime_cache`).

Storage is a small SQLite database in WAL mode (``config.metrics_store_path``,
override with ``F1_METRICS_STORE``), so several browser tabs - each with its
own store object - can write without wiping each other's records (CACHE-02).

Records are *recomputed* from the session's valid laps each time it is
recorded, not kept as running minima: a deleted lap or a later FastF1 data
fix corrects the stored value instead of living for ever (CACHE-05,
REPLAY-18). Only the all-time view is a minimum, taken per circuit.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from f1dash.processing.time_utils import to_seconds
from f1dash.processing.timing import _valid_laps

logger = logging.getLogger(__name__)

SECTORS = ("S1", "S2", "S3")
LAP_METRICS = ("fastest_lap", "fastest_s1", "fastest_s2", "fastest_s3")
METRICS = (*LAP_METRICS, "top_speed")
# Speed-trap columns FastF1 puts on every lap. Top speed comes from these,
# not from scope-limited telemetry (CACHE-02).
SPEED_COLUMNS = ("SpeedST", "SpeedFL", "SpeedI2", "SpeedI1")
SQLITE_MAGIC = b"SQLite format 3\x00"

# Backwards-compatible alias (older call sites imported _to_seconds).
_to_seconds = to_seconds

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    session TEXT NOT NULL,
    metric TEXT NOT NULL,
    circuit TEXT,
    driver TEXT,
    value REAL NOT NULL,
    lap INTEGER,
    updated_at TEXT,
    PRIMARY KEY (session, metric)
)
"""


def _fmt(seconds: float | None) -> str:
    if not seconds:
        return "N/A"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:06.3f}"


def _record(metric: str, driver, value: float, lap) -> dict:
    if metric == "top_speed":
        return {"driver": driver, "kmh": round(float(value), 1)}
    record = {"driver": driver, "seconds": float(value), "display": _fmt(value)}
    if metric == "fastest_lap":
        record["lap"] = lap
    return record


class MetricsStore:
    """Load/update/persist per-session performance records."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from f1dash.config import resolve_paths

            path = resolve_paths()["metrics_store_path"]
        self.path = str(path)
        self.persist = self.path != ":memory:"
        self.writes = 0  # commits that changed something; tests count them
        self._lock = threading.Lock()
        legacy = self._prepare_file() if self.persist else None
        # A file store opens a short-lived connection per operation, so no
        # connection outlives its use (or its thread); an in-memory store
        # has only the one connection it lives in.
        self._shared = (
            None if self.persist else sqlite3.connect(":memory:", check_same_thread=False)
        )
        with self._db() as conn:
            if self.persist:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            conn.commit()
        if legacy:
            self._import_legacy(legacy)

    # ---------------------------------------------------------------- io
    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
            yield self._shared
            return
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            yield conn
        finally:
            conn.close()

    def _prepare_file(self) -> dict | None:
        """Make ``self.path`` usable as a database; return legacy JSON to import.

        An older store was a JSON file; it is imported once. Anything else
        that is not a database (a truncated file, a JSON list) is set aside
        as ``.bak`` with a warning instead of crashing the app (CACHE-05).
        """
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy: dict | None = None
        sibling = path.with_name("metrics_store.json")
        if not path.exists() and sibling.exists():
            legacy = self._read_legacy(sibling)
        if not path.exists() or path.stat().st_size == 0:
            return legacy
        with path.open("rb") as handle:
            if handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC:
                return legacy
        legacy = self._read_legacy(path)
        backup = path.with_name(path.name + ".bak")
        path.replace(backup)
        if legacy is None:
            logger.warning("Metrics store %s was unreadable; moved it to %s", path, backup)
        return legacy

    @staticmethod
    def _read_legacy(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
            logger.warning("Metrics store %s is not a records file; ignoring it", path)
            return None
        return data["sessions"]

    def _import_legacy(self, sessions: dict) -> None:
        for label, metrics in sessions.items():
            if not isinstance(metrics, dict):
                continue
            rows = {}
            for metric in METRICS:
                rec = metrics.get(metric)
                if not isinstance(rec, dict):
                    continue
                value = rec.get("kmh" if metric == "top_speed" else "seconds")
                if value is not None:
                    rows[metric] = (rec.get("driver"), float(value), rec.get("lap"))
            self._replace(str(label), None, rows, metrics=METRICS)

    def _replace(self, label: str, circuit, rows: dict, metrics=LAP_METRICS) -> bool:
        """Make the stored ``metrics`` of ``label`` equal ``rows``; write only on change."""
        with self._lock, self._db() as conn:
            current = {
                metric: (driver, value, lap)
                for metric, driver, value, lap in conn.execute(
                    "SELECT metric, driver, value, lap FROM records WHERE session = ?",
                    (label,),
                )
                if metric in metrics
            }
            if current == rows:
                return False
            stamp = datetime.now(UTC).isoformat()
            with conn:
                for metric in set(current) - set(rows):
                    conn.execute(
                        "DELETE FROM records WHERE session = ? AND metric = ?", (label, metric)
                    )
                for metric, (driver, value, lap) in rows.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (label, metric, circuit, driver, value, lap, stamp),
                    )
            self.writes += 1
            return True

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()

    # ------------------------------------------------------------ update
    @staticmethod
    def make_label(session_info: dict) -> str:
        """Human-readable stable key for a session."""
        parts = (
            session_info.get("gp"),
            session_info.get("session_type") or session_info.get("session_name"),
            session_info.get("year"),
        )
        label = " ".join(str(p) for p in parts if p)
        return label or session_info.get("source", "unknown-session")

    def update_laps(
        self,
        label: str,
        laps_df: pd.DataFrame,
        driver_map: dict | None = None,
        circuit: str | None = None,
    ) -> dict:
        """Record fastest lap, fastest sectors and speed-trap top speed.

        Computed from the valid laps only (no deleted or inaccurate laps) and
        written over whatever was stored for ``label`` before. ``driver_map``
        optionally maps raw driver identifiers to display acronyms;
        ``circuit`` groups the all-time records.
        """
        df = laps_df
        if df is None or df.empty or "LapTime" not in df.columns:
            return self.session_records(label)
        df = _valid_laps(df)
        # Live feeds and partial sources can arrive without a driver column;
        # the records still stand, just unattributed.
        driver_col = next((c for c in ("DriverAcronym", "Driver") if c in df.columns), None)
        drivers = df[driver_col] if driver_col else pd.Series("unknown", index=df.index)
        if driver_map:
            drivers = drivers.map(lambda d: driver_map.get(d, driver_map.get(str(d), d)))
        work = pd.DataFrame(
            {
                "driver": drivers.astype(str),
                "lap": df["LapNumber"] if "LapNumber" in df.columns else None,
                "fastest_lap": df["LapTime"].map(_to_seconds),
            },
            index=df.index,
        )
        for i, sec in enumerate(SECTORS, start=1):
            col = f"Sector{i}Time"
            work[f"fastest_{sec.lower()}"] = df[col].map(_to_seconds) if col in df.columns else None

        rows: dict[str, tuple] = {}
        for metric in LAP_METRICS:
            values = pd.to_numeric(work[metric], errors="coerce")
            if values.notna().any():
                best = work.loc[values.idxmin()]
                lap = (
                    int(best["lap"]) if metric == "fastest_lap" and pd.notna(best["lap"]) else None
                )
                rows[metric] = (best["driver"], float(values.min()), lap)

        metrics: tuple[str, ...] = LAP_METRICS
        speeds = [c for c in SPEED_COLUMNS if c in df.columns]
        if speeds:
            metrics = METRICS
            trap = df[speeds].apply(pd.to_numeric, errors="coerce").max(axis=1)
            if trap.notna().any():
                rows["top_speed"] = (work.loc[trap.idxmax(), "driver"], float(trap.max()), None)
        self._replace(label, circuit, rows, metrics=metrics)
        return self.session_records(label)

    def update_telemetry(
        self, label: str, telemetry_dict: dict[str, pd.DataFrame], circuit: str | None = None
    ) -> dict:
        """Top speed from telemetry, for sources whose laps carry no speed traps."""
        best_driver, best_speed = None, 0.0
        for driver, df in (telemetry_dict or {}).items():
            if df is None or df.empty or "Speed" not in df.columns:
                continue
            mx = pd.to_numeric(df["Speed"], errors="coerce").max()
            if pd.notna(mx) and mx > best_speed:
                best_speed, best_driver = float(mx), driver
        rows = {"top_speed": (best_driver, best_speed, None)} if best_driver else {}
        self._replace(label, circuit, rows, metrics=("top_speed",))
        return self.session_records(label)

    # ------------------------------------------------------------- read
    def session_records(self, label: str) -> dict:
        with self._lock, self._db() as conn:
            found = conn.execute(
                "SELECT metric, driver, value, lap FROM records WHERE session = ?", (label,)
            ).fetchall()
        return {metric: _record(metric, driver, value, lap) for metric, driver, value, lap in found}

    def all_time(self, circuit: str | None = None) -> dict:
        """Fastest lap / sectors / top speed across the recorded sessions.

        Records only compare on the same track, so with ``circuit`` this is
        that circuit's view; without it, every session recorded.
        """
        query = "SELECT session, metric, driver, value, lap FROM records"
        params: tuple = ()
        if circuit:
            query += " WHERE circuit = ?"
            params = (circuit,)
        with self._lock, self._db() as conn:
            found = conn.execute(query, params).fetchall()
        agg: dict[str, dict] = {}
        best: dict[str, float] = {}
        for label, metric, driver, value, lap in found:
            if metric in best and not (
                value > best[metric] if metric == "top_speed" else value < best[metric]
            ):
                continue
            best[metric] = value
            agg[metric] = {**_record(metric, driver, value, lap), "session": label}
        return agg

    def summary_lines(self, records: dict) -> list:
        lines = []
        fl = records.get("fastest_lap")
        if fl:
            lines.append(f"Fastest lap: **{fl['display']}** ({fl['driver']})")
        for sec in SECTORS:
            rec = records.get(f"fastest_{sec.lower()}")
            if rec:
                lines.append(f"Fastest {sec}: **{rec['display']}** ({rec['driver']})")
        ts = records.get("top_speed")
        if ts:
            lines.append(f"Top speed: **{ts['kmh']} km/h** ({ts['driver']})")
        return lines
