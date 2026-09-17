"""Persistent session metrics store.

Records derived statistics - fastest lap, fastest sector per sector, and
top speed - per session, and keeps them across app restarts (unlike the
ephemeral :mod:`data.runtime_cache`).

Storage is a small JSON file (default ``./metrics_store.json``, override
with the ``F1_METRICS_STORE`` environment variable).
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from processing.time_utils import to_seconds

SECTORS = ("S1", "S2", "S3")

# Backwards-compatible alias (older call sites imported _to_seconds).
_to_seconds = to_seconds


def _fmt(seconds: float | None) -> str:
    if not seconds:
        return "N/A"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:06.3f}"


class MetricsStore:
    """Load/update/persist per-session performance records."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("F1_METRICS_STORE") or "./metrics_store.json")
        self.persist = str(self.path) != ":memory:"
        self.data: dict = {"sessions": {}, "updated_at": None}
        self.load()

    # ---------------------------------------------------------------- io
    def load(self) -> None:
        if self.persist and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.setdefault("sessions", {})
            except (json.JSONDecodeError, OSError):
                # Corrupt store: start over rather than crash the app
                self.data = {"sessions": {}, "updated_at": None}

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(UTC).isoformat()
        if not self.persist:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

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
        self, label: str, laps_df: pd.DataFrame, driver_map: dict | None = None
    ) -> dict:
        """Record fastest lap + fastest sectors from a laps DataFrame.

        ``driver_map`` optionally maps raw driver identifiers (numbers or
        codes) in the 'Driver' column to display acronyms.
        """
        session = self._session(label)

        df = laps_df
        if df is None or df.empty or "LapTime" not in df.columns:
            return session
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

        valid = work.dropna(subset=["seconds"])
        if valid.empty:
            return session

        best = valid.loc[valid["seconds"].idxmin()]
        cur = session.get("fastest_lap")
        if not cur or best["seconds"] < cur["seconds"]:
            session["fastest_lap"] = {
                "driver": str(best["driver"]),
                "seconds": float(best["seconds"]),
                "display": _fmt(best["seconds"]),
                "lap": int(best["lap_number"]) if pd.notna(best["lap_number"]) else None,
            }

        for _index, sec in enumerate(SECTORS, start=1):
            col = sec.lower()
            sub = work.dropna(subset=[col])
            if sub.empty:
                continue
            row = sub.loc[sub[col].idxmin()]
            key = f"fastest_{sec.lower()}"
            cur_sec = session.get(key)
            if not cur_sec or row[col] < cur_sec["seconds"]:
                session[key] = {
                    "driver": str(row["driver"]),
                    "seconds": float(row[col]),
                    "display": _fmt(row[col]),
                }
        self.save()
        return session

    def update_telemetry(self, label: str, telemetry_dict: dict[str, pd.DataFrame]) -> dict:
        """Record top speed reached by any driver from telemetry DataFrames."""
        session = self._session(label)
        best_driver, best_speed = None, 0.0
        for driver, df in (telemetry_dict or {}).items():
            if df is None or df.empty or "Speed" not in df.columns:
                continue
            mx = pd.to_numeric(df["Speed"], errors="coerce").max()
            if pd.notna(mx) and mx > best_speed:
                best_speed, best_driver = float(mx), driver
        if best_driver and best_speed > session.get("top_speed", {}).get("kmh", 0.0):
            session["top_speed"] = {"driver": best_driver, "kmh": round(best_speed, 1)}
            self.save()
        return session

    # ------------------------------------------------------------- read
    def session_records(self, label: str) -> dict:
        return dict(self._session(label))

    def all_time(self) -> dict:
        """Fastest lap / sectors / top speed across every recorded session."""
        agg: dict[str, dict] = {}
        for label, sess in self.data["sessions"].items():
            for metric in ["fastest_lap", "fastest_s1", "fastest_s2", "fastest_s3", "top_speed"]:
                rec = sess.get(metric)
                if not rec:
                    continue
                cur = agg.get(metric)
                if metric == "top_speed":
                    if not cur or rec["kmh"] > cur["kmh"]:
                        agg[metric] = {**rec, "session": label}
                else:
                    if not cur or rec["seconds"] < cur["seconds"]:
                        agg[metric] = {**rec, "session": label}
        return agg

    def summary_lines(self, records: dict) -> list:
        lines = []
        fl = records.get("fastest_lap")
        if fl:
            lines.append(f"🟣 Fastest Lap: **{fl['display']}** ({fl['driver']})")
        for sec in SECTORS:
            rec = records.get(f"fastest_{sec.lower()}")
            if rec:
                lines.append(f"⚡ Fastest {sec}: **{rec['display']}** ({rec['driver']})")
        ts = records.get("top_speed")
        if ts:
            lines.append(f"🚀 Top Speed: **{ts['kmh']} km/h** ({ts['driver']})")
        return lines

    # ----------------------------------------------------------- private
    def _session(self, label: str) -> dict:
        return self.data["sessions"].setdefault(label, {})
