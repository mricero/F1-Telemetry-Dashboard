"""FastF1 Historical Data Adapter"""

import fastf1
import fastf1.core
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional, Tuple

# FastF1 position channels (X/Y/Z) are expressed in 1/10 meter.
POSITION_UNITS_PER_METRE = 10.0

# Telemetry scopes accepted by get_telemetry()/get_location().
SCOPE_FASTEST = "fastest"
SCOPE_SESSION = "session"


class FastF1Adapter:
    """Loads historical F1 sessions with local caching."""

    def __init__(self, cache_dir: str = "./ff1_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Pass the original string so callers can round-trip the exact path
        fastf1.Cache.enable_cache(cache_dir)

    def get_available_sessions(self, years: list[int] = None) -> pd.DataFrame:
        """Returns DataFrame of all completed race weekends from the schedule.

        Pre-season testing events are excluded: they appear twice per season,
        carry no Race/Qualifying session, and would break session loading if
        picked from the Grand Prix dropdown.
        """
        if years is None:
            years = [2023, 2024, 2025]
        elif isinstance(years, int):
            years = [years]
        all_schedules = []
        for year in years:
            schedule = fastf1.get_event_schedule(year)
            # Add Year column for consistency
            schedule["Year"] = year
            all_schedules.append(schedule)
        if all_schedules:
            combined = pd.concat(all_schedules, ignore_index=True)
        else:
            combined = pd.DataFrame()

        # Drop testing events (EventFormat == 'testing', RoundNumber 0).
        if not combined.empty and "EventFormat" in combined.columns:
            combined = combined[combined["EventFormat"].astype(str) != "testing"]
        elif not combined.empty and "RoundNumber" in combined.columns:
            combined = combined[pd.to_numeric(combined["RoundNumber"], errors="coerce") > 0]

        # Filter to completed sessions only.
        # FastF1 EventDate tz-awareness varies by version, so normalize both sides to UTC-aware.
        if "EventDate" in combined.columns and not combined.empty:
            event_dates = pd.to_datetime(combined["EventDate"], utc=True)
            now = pd.Timestamp.now(tz="UTC")
            completed = combined[event_dates < now]
        else:
            completed = combined
        return completed

    def load_session(self, year: int, gp: str, session_type: str) -> fastf1.core.Session:
        """Load session with automatic caching."""
        session = fastf1.get_session(year, gp, session_type)
        session.load(telemetry=True, laps=True, weather=True, messages=True)
        return session

    def _pick_laps(self, session: fastf1.core.Session, driver: str, scope: str):
        """Select the laps a telemetry request should cover.

        ``scope='fastest'`` returns the driver's fastest :class:`~fastf1.core.Lap`
        so Distance is lap-relative (0 -> lap length) and drivers are directly
        comparable at the same track position. ``scope='session'`` returns every
        lap, whose Distance accumulates across the whole session.

        Returns None when the driver has no usable laps.
        """
        laps = session.laps.pick_drivers(driver)
        if laps is None or laps.empty:
            return None
        if scope == SCOPE_FASTEST:
            # pick_fastest() returns None when no lap has a valid lap time.
            fastest = laps.pick_fastest()
            if fastest is None:
                return None
            return fastest
        return laps

    @staticmethod
    def _merged_telemetry(lap_selection) -> pd.DataFrame:
        """Car + position telemetry with a Distance channel.

        FastF1's ``get_telemetry()`` merges car data and position data and adds
        Distance itself. ``get_pos_data()`` must NOT be distance-integrated
        directly: it carries no Speed channel, so ``add_distance()`` raises
        ``ValueError: Telemetry does not contain required channels``.
        """
        telemetry = lap_selection.get_telemetry()
        if telemetry is None or len(telemetry) == 0:
            return pd.DataFrame()
        if "Distance" not in telemetry.columns and hasattr(telemetry, "add_distance"):
            telemetry = telemetry.add_distance()
        return telemetry

    def get_driver_frames(
        self,
        session: fastf1.core.Session,
        driver: str,
        scope: str = SCOPE_FASTEST,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Telemetry channels and GPS trail for one driver from a single merge.

        ``get_telemetry()`` is the expensive call (car/position merge plus
        interpolation), and it already yields both the channel data and the
        X/Y/Z trail - so a session load does it once per driver rather than
        twice.
        """
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame(), pd.DataFrame()
        merged = self._merged_telemetry(selection)
        if merged.empty:
            return pd.DataFrame(), pd.DataFrame()
        return self._telemetry_columns(merged), self._location_columns(merged)

    @staticmethod
    def _telemetry_columns(telemetry: pd.DataFrame) -> pd.DataFrame:
        """Project merged telemetry onto the unified channel schema."""
        if "Distance" not in telemetry.columns:
            return pd.DataFrame()
        # 'Time' is kept: downstream processing and metrics rely on it.
        cols = ["Distance", "Time", "Speed", "Throttle", "Brake", "RPM", "nGear", "DRS"]
        available = [c for c in cols if c in telemetry.columns]
        return pd.DataFrame(telemetry[available]).reset_index(drop=True)

    @classmethod
    def _location_columns(cls, telemetry: pd.DataFrame) -> pd.DataFrame:
        """Project merged telemetry onto the unified location schema."""
        if not {"X", "Y"}.issubset(telemetry.columns):
            return pd.DataFrame()
        cols = [c for c in ["Distance", "X", "Y", "Z"] if c in telemetry.columns]
        return pd.DataFrame(telemetry[cols]).reset_index(drop=True)

    def get_telemetry(
        self, session: fastf1.core.Session, driver: str, scope: str = SCOPE_FASTEST
    ) -> pd.DataFrame:
        """Get car telemetry (speed, throttle, brake, rpm, gear, drs)."""
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame()
        merged = self._merged_telemetry(selection)
        if merged.empty:
            return pd.DataFrame()
        return self._telemetry_columns(merged)

    def get_location(
        self, session: fastf1.core.Session, driver: str, scope: str = SCOPE_FASTEST
    ) -> pd.DataFrame:
        """Get GPS location data (X/Y/Z plus travelled Distance in metres)."""
        selection = self._pick_laps(session, driver, scope)
        if selection is None:
            return pd.DataFrame()

        merged = self._merged_telemetry(selection)
        if not merged.empty and {"X", "Y"}.issubset(merged.columns):
            return self._location_columns(merged)

        # Fallback: raw position data only. It has no Speed channel, so
        # distance comes from the GPS arc length instead of integration.
        pos_data = selection.get_pos_data()
        if pos_data is None or len(pos_data) == 0:
            return pd.DataFrame()
        pos_data = pd.DataFrame(pos_data)
        if "Distance" not in pos_data.columns:
            distance = self.distance_from_positions(pos_data)
            if distance is not None:
                pos_data = pos_data.assign(Distance=distance)
        cols = [c for c in ["Distance", "X", "Y", "Z"] if c in pos_data.columns]
        return pos_data[cols].reset_index(drop=True)

    @staticmethod
    def distance_from_positions(pos_data: pd.DataFrame) -> Optional[np.ndarray]:
        """Cumulative travelled distance (metres) along an X/Y GPS trail.

        FastF1 position coordinates are in 1/10 m, so the raw arc length is
        divided by :data:`POSITION_UNITS_PER_METRE`.
        """
        if pos_data is None or not {"X", "Y"}.issubset(pos_data.columns):
            return None
        xy = pos_data[["X", "Y"]].to_numpy(dtype=float)
        if len(xy) < 2 or np.isnan(xy).all():
            return None
        steps = np.hypot(*np.diff(xy, axis=0).T)
        steps = np.nan_to_num(steps, nan=0.0)
        return np.concatenate([[0.0], np.cumsum(steps)]) / POSITION_UNITS_PER_METRE

    def get_laps(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Get lap timing data with a boolean pit-out flag.

        FastF1 exposes pit activity as ``PitOutTime``/``PitInTime`` timestamps,
        not as a boolean column, so ``IsPitOutLap`` is derived here - the lap
        chart marks those laps.
        """
        laps = session.laps.copy()
        # 'Position' drives the lap-by-lap position chart; 'Compound'/'Stint'
        # let the lap view be read alongside tyre choice without a second query.
        cols = [
            "Driver",
            "LapNumber",
            "LapTime",
            "Sector1Time",
            "Sector2Time",
            "Sector3Time",
            "Position",
            "Compound",
            "Stint",
            # Speed-trap readings feed the timing tower's Speed column.
            "SpeedI1",
            "SpeedI2",
            "SpeedFL",
            "SpeedST",
            # Pit timestamps drive the IN PIT status badge.
            "PitInTime",
            "PitOutTime",
        ]
        available = [c for c in cols if c in laps.columns]
        result = pd.DataFrame(laps[available]).reset_index(drop=True)

        result["IsPitOutLap"] = self._pit_out_flags(laps).to_numpy()
        return result

    @staticmethod
    def get_race_control(session: fastf1.core.Session) -> pd.DataFrame:
        """Race control messages: flags, safety cars, incidents, penalties."""
        try:
            messages = session.race_control_messages
        except Exception:
            return pd.DataFrame()
        if messages is None or len(messages) == 0:
            return pd.DataFrame()
        cols = ["Time", "Lap", "Category", "Flag", "Scope", "Sector", "Message"]
        available = [c for c in cols if c in messages.columns]
        return pd.DataFrame(messages[available]).reset_index(drop=True)

    @staticmethod
    def get_circuit_info(session: fastf1.core.Session) -> dict:
        """Corner markers and track rotation for the map.

        FastF1 sources this from the MultiViewer API, so it needs network
        access and is absent for some circuits; the map degrades to an
        unlabelled outline when this returns an empty dict.
        """
        try:
            info = session.get_circuit_info()
        except Exception:
            return {}
        if info is None:
            return {}
        corners = getattr(info, "corners", None)
        has_corners = corners is not None and len(corners) > 0
        return {
            "corners": (
                pd.DataFrame(corners).reset_index(drop=True) if has_corners else pd.DataFrame()
            ),
            "rotation": float(getattr(info, "rotation", 0.0) or 0.0),
        }

    @staticmethod
    def compound_colors(session: fastf1.core.Session) -> dict:
        """Official tyre compound colours for the session's season.

        FastF1 tracks the real branding per season, so this beats a hardcoded
        table (and covers compounds a given year may add or drop).
        """
        try:
            import fastf1.plotting

            mapping = fastf1.plotting.get_compound_mapping(session)
        except Exception:
            return {}
        return {str(k).upper(): v for k, v in (mapping or {}).items()}

    @staticmethod
    def _pit_out_flags(laps: pd.DataFrame) -> pd.Series:
        """Boolean pit-out flag per lap, from whichever column the source has."""
        for col in ("IsPitOutLap", "PitOutLap"):
            if col in laps.columns:
                return laps[col].fillna(False).astype(bool)
        if "PitOutTime" in laps.columns:
            return laps["PitOutTime"].notna()
        return pd.Series(False, index=laps.index, dtype=bool)

    def get_stints(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Get tyre stint data derived from laps."""
        laps = session.laps
        if "Stint" not in laps.columns or "Compound" not in laps.columns:
            return pd.DataFrame()

        required = {"Driver", "Stint", "Compound"}
        if not required.issubset(set(laps.columns)):
            return pd.DataFrame()

        if {"LapStart", "LapEnd"}.issubset(set(laps.columns)):
            # Stint boundaries already present (e.g. pre-aggregated data)
            stints = laps[["Driver", "Stint", "Compound", "LapStart", "LapEnd"]].drop_duplicates()
        elif "LapNumber" in laps.columns:
            # Derive stint boundaries from per-lap data
            stints = (
                laps.groupby(["Driver", "Stint"])
                .agg(
                    LapStart=("LapNumber", "min"),
                    LapEnd=("LapNumber", "max"),
                    Compound=("Compound", "first"),
                )
                .reset_index()
            )
        else:
            return pd.DataFrame()

        stints = pd.DataFrame(stints).reset_index(drop=True)
        stints["LapCount"] = stints["LapEnd"] - stints["LapStart"] + 1
        # Lap/stint counters are whole numbers; groupby leaves them as floats
        # (NaN-capable), which would render as "Laps: 12.0" in the chart.
        for col in ("Stint", "LapStart", "LapEnd", "LapCount"):
            if col in stints.columns:
                stints[col] = pd.to_numeric(stints[col], errors="coerce").astype("Int64")
        return stints
