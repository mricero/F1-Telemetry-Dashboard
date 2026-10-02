"""Persistent session metrics store.

Records derived statistics - fastest lap, fastest sector per sector, and
top speed - per session, and keeps them across app restarts (unlike the
ephemeral :mod:`data.runtime_cache`).

Storage is a small SQLite database in WAL mode (CACHE-02): every browser tab
holds its own ``MetricsStore`` object, but they all read and write the same
rows, so one tab can no longer overwrite another tab's records with its stale
in-memory copy. The location is ``config.metrics_store_path``
(``F1_METRICS_STORE`` overrides it; ``:memory:`` keeps nothing).

A session's records are **recomputed** from its laps each time it is folded
in (CACHE-05): a deleted lap that once won (REPLAY-18) or a later correction
in F1's data replaces the stored value instead of standing for ever. Only the
all-time view is a minimum across sessions, and it compares a circuit with
itself, not Monaco with Monza.

A store written by older versions as JSON is imported once and kept as
``<name>.bak``; a JSON file of the wrong shape is set aside the same way.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from processing.time_utils import to_seconds
from processing.timing import _valid_laps

logger = logging.getLogger(__name__)

SECTORS = ("S1", "S2", "S3")
LAP_METRICS = ("fastest_lap", "fastest_s1", "fastest_s2", "fastest_s3")
METRICS = (*LAP_METRICS, "top_speed")
# Speed-trap columns of a FastF1 laps frame, fastest first in practice. They
# cover the whole session, unlike the scope-limited telemetry (CACHE-02).
SPEED_COLUMNS = ("SpeedST", "SpeedFL", "SpeedI2", "SpeedI1")
MEMORY = ":memory:"

# Backwards-compatible alias (older call sites imported _to_seconds).
_to_seconds = to_seconds

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    session    TEXT NOT NULL,
    metric     TEXT NOT NULL,
    circuit    TEXT,
    driver     TEXT,
    value      REAL NOT NULL,
    display    TEXT,
    lap        INTEGER,
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


def _default_path() -> str:
    from config import config

    return str(config.metrics_store_path)


class MetricsStore:
    """Load/update/persist per-session performance records."""

    def __init__(self, path: str | None = None):
        self.path = str(path or _default_path())
        self.persist = self.path != MEMORY
        self.writes = 0  # rows written, for the "write only on change" check
        self._lock = threading.Lock()
        if self.persist:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            legacy = self._take_legacy_json(Path(self.path))
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
        else:
            legacy = None
            self._conn = sqlite3.connect(MEMORY, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        if legacy:
            self._import_legacy(legacy)

    # ---------------------------------------------------------- legacy
    def _take_legacy_json(self, path: Path) -> dict | None:
        """The sessions of a JSON store at ``path`` (or beside it), set aside as ``.bak``."""
        candidates = [path]
        if path.suffix != ".json":
            candidates.append(path.with_name("metrics_store.json"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                head = candidate.read_bytes()[:16]
            except OSError:
                continue
            if head.startswith(b"SQLite format 3"):
                continue
            backup = candidate.with_name(candidate.name + ".bak")
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                data = None
            try:
                candidate.replace(backup)
            except OSError as exc:
                logger.warning("Could not set the old records file %s aside: %s", candidate, exc)
                continue
            if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
                logger.warning(
                    "Records file %s is not a records store; starting empty (kept as %s)",
                    candidate,
                    backup,
                )
                return None
            return data["sessions"]
        return None

    def _import_legacy(self, sessions: dict) -> None:
        for label, records in sessions.items():
            if not isinstance(records, dict):
                continue
            rows = {}
            for metric in METRICS:
                record = records.get(metric)
                if isinstance(record, dict):
                    value = record.get("kmh") if metric == "top_speed" else record.get("seconds")
                    if value is not None:
                        rows[metric] = {**record, "value": float(value)}
            self._replace(str(label), rows, metrics=METRICS, circuit=None)

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
        """Recompute fastest lap, fastest sectors and trap speed from a laps frame.

        Deleted laps and laps FastF1 marks inaccurate do not count
        (REPLAY-18). ``driver_map`` optionally maps raw driver identifiers
        (numbers or codes) in the 'Driver' column to display acronyms.
        """
        df = laps_df
        if df is None or df.empty or "LapTime" not in df.columns:
            return self.session_records(label)
        df = _valid_laps(df)
        # Live feeds and partial sources can arrive without a driver column;
        # the records still stand, just unattributed.
        driver_col = next((c for c in ("DriverAcronym", "Driver") if c in df.columns), None)

        work = pd.DataFrame(
            {
                "raw_driver": df[driver_col] if driver_col else "unknown",
                "lap_number": df["LapNumber"] if "LapNumber" in df.columns else None,
                "seconds": df["LapTime"].map(_to_seconds),
            },
            index=df.index,
        )
        for i, sec in enumerate(SECTORS, start=1):
            col = f"Sector{i}Time"
            work[sec.lower()] = df[col].map(_to_seconds) if col in df.columns else None

        if driver_map:
            work["driver"] = work["raw_driver"].map(
                lambda d: driver_map.get(d, driver_map.get(str(d), d))
            )
        else:
            work["driver"] = work["raw_driver"]

        rows: dict[str, dict] = {}
        valid = work.dropna(subset=["seconds"])
        if not valid.empty:
            best = valid.loc[valid["seconds"].idxmin()]
            rows["fastest_lap"] = {
                "driver": str(best["driver"]),
                "value": float(best["seconds"]),
                "display": _fmt(best["seconds"]),
                "lap": int(best["lap_number"]) if pd.notna(best["lap_number"]) else None,
            }
        for sec in SECTORS:
            col = sec.lower()
            sub = work.dropna(subset=[col])
            if sub.empty:
                continue
            row = sub.loc[sub[col].idxmin()]
            rows[f"fastest_{col}"] = {
                "driver": str(row["driver"]),
                "value": float(row[col]),
                "display": _fmt(row[col]),
            }

        metrics: tuple[str, ...] = LAP_METRICS
        speed_cols = [c for c in SPEED_COLUMNS if c in df.columns]
        if speed_cols:
            speeds = df[speed_cols].apply(pd.to_numeric, errors="coerce").max(axis=1)
            if speeds.notna().any():
                where = speeds.idxmax()
                rows["top_speed"] = {
                    "driver": str(work.loc[where, "driver"]),
                    "value": round(float(speeds.loc[where]), 1),
                }
            metrics = METRICS
        self._replace(label, rows, metrics=metrics, circuit=circuit)
        return self.session_records(label)

    def has_top_speed(self, label: str) -> bool:
        return "top_speed" in self.session_records(label)

    def update_telemetry(
        self, label: str, telemetry_dict: dict[str, pd.DataFrame], circuit: str | None = None
    ) -> dict:
        """Top speed from telemetry frames, for sources without speed traps.

        Telemetry may cover only each driver's fastest lap, so this keeps the
        higher of the stored and the new value.
        """
        best_driver, best_speed = None, 0.0
        for driver, df in (telemetry_dict or {}).items():
            if df is None or df.empty or "Speed" not in df.columns:
                continue
            mx = pd.to_numeric(df["Speed"], errors="coerce").max()
            if pd.notna(mx) and mx > best_speed:
                best_speed, best_driver = float(mx), driver
        current = self.session_records(label).get("top_speed", {}).get("kmh", 0.0)
        if best_driver and best_speed > current:
            self._replace(
                label,
                {"top_speed": {"driver": best_driver, "value": round(best_speed, 1)}},
                metrics=("top_speed",),
                circuit=circuit,
            )
        return self.session_records(label)

    # ------------------------------------------------------------- read
    def session_records(self, label: str) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT metric, driver, value, display, lap FROM records WHERE session = ?",
                (label,),
            ).fetchall()
        return {metric: self._record(metric, *rest) for metric, *rest in rows}

    def all_time(self, circuit: str | None = None) -> dict:
        """Fastest lap / sectors / top speed across the recorded sessions.

        With ``circuit``, only that circuit's sessions: lap and sector times
        mean nothing across different tracks (CACHE-02).
        """
        query = "SELECT session, metric, driver, value, display, lap FROM records"
        args: tuple = ()
        if circuit is not None:
            query += " WHERE circuit = ?"
            args = (circuit,)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        agg: dict[str, dict] = {}
        for session, metric, driver, value, display, lap in rows:
            cur = agg.get(metric)
            if cur is None:
                better = True
            elif metric == "top_speed":
                better = value > cur["_value"]
            else:
                better = value < cur["_value"]
            if better:
                agg[metric] = {
                    **self._record(metric, driver, value, display, lap),
                    "session": session,
                    "_value": value,
                }
        for record in agg.values():
            record.pop("_value")
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------------------------------------- private
    @staticmethod
    def _record(metric, driver, value, display, lap) -> dict:
        if metric == "top_speed":
            return {"driver": driver, "kmh": value}
        record = {"driver": driver, "seconds": value, "display": display}
        if metric == "fastest_lap":
            record["lap"] = lap
        return record

    def _replace(
        self, label: str, rows: dict[str, dict], metrics: tuple[str, ...], circuit: str | None
    ) -> None:
        """Make the stored ``metrics`` of ``label`` equal ``rows``, writing only changes."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            stored = {
                metric: (driver, value, display, lap, stored_circuit)
                for metric, driver, value, display, lap, stored_circuit in self._conn.execute(
                    "SELECT metric, driver, value, display, lap, circuit FROM records "
                    "WHERE session = ?",
                    (label,),
                )
            }
            changed = 0
            for metric in metrics:
                row = rows.get(metric)
                old = stored.get(metric)
                if row is None:
                    if old is not None:
                        self._conn.execute(
                            "DELETE FROM records WHERE session = ? AND metric = ?",
                            (label, metric),
                        )
                        changed += 1
                    continue
                keep_circuit = circuit if circuit is not None else (old[4] if old else None)
                new = (
                    row.get("driver"),
                    float(row["value"]),
                    row.get("display"),
                    row.get("lap"),
                    keep_circuit,
                )
                if old == new:
                    continue
                self._conn.execute(
                    "INSERT OR REPLACE INTO records "
                    "(session, metric, circuit, driver, value, display, lap, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (label, metric, new[4], new[0], new[1], new[2], new[3], now),
                )
                changed += 1
            if changed:
                self._conn.commit()
                self.writes += changed
