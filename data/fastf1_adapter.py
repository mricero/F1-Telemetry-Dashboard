"""FastF1 Historical Data Adapter"""
import fastf1
import fastf1.core
import pandas as pd
from pathlib import Path


class FastF1Adapter:
    """Loads historical F1 sessions with local caching."""
    
    def __init__(self, cache_dir: str = "./ff1_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(self.cache_dir))
    
    def get_available_sessions(self, years: list[int] = None) -> pd.DataFrame:
        """Returns DataFrame of all available sessions from schedule."""
        if years is None:
            years = [2023, 2024, 2025]
        elif isinstance(years, int):
            years = [years]
        all_schedules = []
        for year in years:
            schedule = fastf1.get_event_schedule(year)
            # Add Year column for consistency
            schedule['Year'] = year
            all_schedules.append(schedule)
        if all_schedules:
            combined = pd.concat(all_schedules, ignore_index=True)
        else:
            combined = pd.DataFrame()
        # Filter to completed sessions only
        # FastF1 EventDate is timezone-naive, so use timezone-naive now
        now = pd.Timestamp.now()  # timezone-naive to match EventDate
        if 'EventDate' in combined.columns:
            completed = combined[combined['EventDate'] < now]
        else:
            completed = combined
        return completed
    
    def load_session(self, year: int, gp: str, session_type: str) -> fastf1.core.Session:
        """Load session with automatic caching."""
        session = fastf1.get_session(year, gp, session_type)
        session.load(telemetry=True, laps=True, weather=True, messages=True)
        return session
    
    def get_telemetry(self, session: fastf1.core.Session, driver: str) -> pd.DataFrame:
        """Get car telemetry (speed, throttle, brake, rpm, gear, drs)."""
        laps = session.laps.pick_drivers(driver)
        if laps.empty:
            return pd.DataFrame()
        telemetry = laps.get_telemetry().add_distance()
        # Include 'Time' column as it's required by telemetry processor
        cols = ['Distance', 'Time', 'Speed', 'Throttle', 'Brake', 'RPM', 'nGear', 'DRS']
        available_cols = [c for c in cols if c in telemetry.columns]
        return telemetry[available_cols]
    
    def get_laps(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Get lap timing data."""
        laps = session.laps.copy()
        # Check for pit out lap column (may be 'IsPitOutLap' or 'PitOutLap')
        pit_col = 'IsPitOutLap' if 'IsPitOutLap' in laps.columns else ('PitOutLap' if 'PitOutLap' in laps.columns else None)
        cols = ['Driver', 'LapNumber', 'LapTime', 'Sector1Time', 'Sector2Time', 'Sector3Time']
        if pit_col:
            cols.append(pit_col)
        return laps[cols]
    
    def get_stints(self, session: fastf1.core.Session) -> pd.DataFrame:
        """Get tyre stint data derived from laps."""
        # FastF1 doesn't have a separate stints attribute, derive from laps
        if 'Stint' not in session.laps.columns or 'Compound' not in session.laps.columns:
            return pd.DataFrame()
        
        # Group by driver and stint to get stint info
        stints = session.laps.groupby(['Driver', 'Stint']).agg(
            LapStart=('LapNumber', 'min'),
            LapEnd=('LapNumber', 'max'),
            Compound=('Compound', 'first')
        ).reset_index()
        
        stints['LapCount'] = stints['LapEnd'] - stints['LapStart'] + 1
        return stints
    
    def get_location(self, session: fastf1.core.Session, driver: str) -> pd.DataFrame:
        """Get GPS location data."""
        laps = session.laps.pick_drivers(driver)
        if laps.empty:
            return pd.DataFrame()
        return laps.get_pos_data().add_distance()[['Distance', 'X', 'Y', 'Z']].copy()