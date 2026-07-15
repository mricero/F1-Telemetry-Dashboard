"""Data Source Manager - Unified interface with automatic fallback"""
import pandas as pd
import pickle
from pathlib import Path
from typing import Dict, Optional, Union
from datetime import datetime

from data.fastf1_adapter import FastF1Adapter
from data.live_adapter import SignalRLiveAdapter, LiveDataProcessor
from livef1 import get_session


class DataSourceManager:
    """Unified interface with automatic fallback: Live → Historical → Replay"""
    
    def __init__(self):
        self.fastf1 = FastF1Adapter()
        self.live = SignalRLiveAdapter(use_livef1=True)
        self.replay_dir = Path("./replay_sessions")
        self.replay_dir.mkdir(exist_ok=True)
    
    def get_session_data(self, 
                         source: str = "auto",  # "auto", "fastf1", "livef1", "live", "replay"
                         year: int = None, 
                         gp: str = None, 
                         session_type: str = None,
                         replay_file: str = None) -> dict:
        """
        Returns unified data dict:
        {
            'session_info': {...},
            'telemetry': {driver: DataFrame[Distance, Speed, Throttle, Brake, RPM, Gear, DRS]},
            'laps': DataFrame,
            'stints': DataFrame,
            'location': {driver: DataFrame[Distance, X, Y, Z]},
            'weather': DataFrame,
            'drivers': DataFrame[driver_number, name_acronym, team_colour, team_name],
            'source': 'fastf1'|'jolpica'|'live'|'replay',
            'is_live': bool,
            'live_client': SignalRLiveAdapter (if live)
        }
        """
        
        if source == "replay" and replay_file:
            return self._load_replay(replay_file)
        
        if source == "auto":
            # Try live first (during race weekends)
            if self._is_race_weekend():
                return self._load_live_session()
            
            # Fallback: most recent completed race
            if not year:
                recent = self._get_most_recent_completed_race()
                year, gp, session_type = recent['year'], recent['gp'], recent['session_type']
        
        if source in ("auto", "fastf1"):
            return self._load_fastf1_session(year, gp, session_type)
        
        if source == "livef1":
            return self._load_livef1_session(year, gp, session_type)
        
        if source == "live":
            return self._load_live_session()
        
        raise ValueError(f"Unknown source: {source}")
    
    def _is_race_weekend(self) -> bool:
        """Check if there's an active F1 session this weekend."""
        try:
            return self.jolpica.is_race_weekend()
        except:
            return False
    
    def _load_fastf1_session(self, year: int, gp: str, session_type: str) -> dict:
        session = self.fastf1.load_session(year, gp, session_type)
        drivers = session.results['Abbreviation'].tolist()
        
        return {
            'session_info': {
                'year': year, 'gp': gp, 'session_type': session_type,
                'session_name': session.name, 'date': session.date
            },
            'telemetry': {d: self.fastf1.get_telemetry(session, d) for d in drivers},
            'laps': self.fastf1.get_laps(session),
            'stints': self.fastf1.get_stints(session),
            'location': {d: self.fastf1.get_location(session, d) for d in drivers},
            'weather': self._get_weather_from_session(session),
            'drivers': self._get_driver_info(session),
            'source': 'fastf1',
            'is_live': False
        }
    
    def _load_livef1_session(self, year: int, gp: str, session_type: str) -> dict:
        """Load session using LiveF1 (historical data with full telemetry)."""
        # Map session type to LiveF1 format
        session_map = {
            'R': 'Race',
            'Q': 'Qualifying',
            'FP1': 'Practice 1',
            'FP2': 'Practice 2',
            'FP3': 'Practice 3',
            'S': 'Sprint',
            'SQ': 'Sprint Qualifying'
        }
        livef1_session_type = session_map.get(session_type, session_type)
        
        # LiveF1 uses meeting_identifier and session_identifier
        # Need to map gp to circuit short name
        circuit_map = {
            'Bahrain': 'Sakhir',
            'Saudi Arabia': 'Jeddah',
            'Australia': 'Melbourne',
            'Japan': 'Suzuka',
            'China': 'Shanghai',
            'Miami': 'Miami',
            'Emilia Romagna': 'Imola',
            'Monaco': 'Monaco',
            'Canada': 'Montreal',
            'Spain': 'Barcelona',
            'Austria': 'Spielberg',
            'Great Britain': 'Silverstone',
            'Hungary': 'Hungaroring',
            'Belgium': 'Spa',
            'Netherlands': 'Zandvoort',
            'Italy': 'Monza',
            'Azerbaijan': 'Baku',
            'Singapore': 'Singapore',
            'United States': 'Austin',
            'Mexico': 'Mexico City',
            'Brazil': 'Sao Paulo',
            'Las Vegas': 'Las Vegas',
            'Qatar': 'Lusail',
            'Abu Dhabi': 'Yas Marina'
        }
        circuit_short = circuit_map.get(gp, gp)
        
        session = get_session(season=year, meeting_identifier=circuit_short, session_identifier=livef1_session_type)
        
        # Generate silver tables (processed data)
        session.generate(silver=True)
        
        # Get processed data
        laps_df = session.get_laps()
        telemetry_df = session.get_car_telemetry()
        
        # Get driver info from session
        drivers = session.drivers
        drivers_df = pd.DataFrame({
            'driver_number': [d.driver_number for d in drivers.values()],
            'name_acronym': [d.name_acronym for d in drivers.values()],
            'team_colour': [d.team_colour for d in drivers.values()],
            'team_name': [d.team_name for d in drivers.values()],
            'full_name': [f"{d.first_name} {d.last_name}" for d in drivers.values()]
        })
        
        # Build telemetry dict by driver
        telemetry_dict = {}
        if not telemetry_df.empty:
            for driver in drivers_df['name_acronym'].unique():
                driver_telemetry = telemetry_df[telemetry_df['Driver'] == driver]
                if not driver_telemetry.empty:
                    # Add distance if not present
                    if 'Distance' not in driver_telemetry.columns:
                        driver_telemetry = driver_telemetry.copy()
                        driver_telemetry['Distance'] = range(len(driver_telemetry)) * 10  # approximate
                    telemetry_dict[driver] = driver_telemetry[['Distance', 'Speed', 'Throttle', 'Brake', 'RPM', 'nGear', 'DRS']].copy()
        
        # Build location dict (GPS data)
        location_dict = {}
        # LiveF1 doesn't provide GPS easily, use empty for now
        
        # Process laps data
        if not laps_df.empty:
            # Ensure we have the right columns
            laps_df = laps_df.copy()
            if 'IsPitOutLap' not in laps_df.columns and 'PitOutLap' in laps_df.columns:
                laps_df['IsPitOutLap'] = laps_df['PitOutLap']
        
        return {
            'session_info': {
                'year': year, 'gp': gp, 'session_type': session_type,
                'session_name': f"{gp} {session_type}",
                'date': None  # LiveF1 doesn't expose date easily
            },
            'telemetry': telemetry_dict,
            'laps': laps_df,
            'stints': pd.DataFrame(),  # Could be extracted from LiveF1
            'location': {},
            'weather': pd.DataFrame(),
            'drivers': drivers_df,
            'source': 'livef1',
            'is_live': False
        }
    
    def _load_live_session(self) -> dict:
        """Start live SignalR client and return initial data structure."""
        return {
            'session_info': {'is_live': True, 'source': 'signalr_live'},
            'telemetry': {},  # Will be populated via callbacks
            'laps': pd.DataFrame(),
            'stints': pd.DataFrame(),
            'location': {},
            'weather': pd.DataFrame(),
            'drivers': pd.DataFrame(),
            'source': 'live',
            'is_live': True,
            'live_client': self.live  # Pass client for UI to use
        }
    
    def _load_l(self):
        """Load live session - alias for _load_live_session"""
        return self._load_live_session()
    
    def _get_most_recent_completed_race(self) -> dict:
        """Find most recent completed race from FastF1 schedule."""
        schedule = self.fastf1.get_available_sessions()
        if schedule.empty:
            return {'year': 2024, 'gp': 'Abu Dhabi', 'session_type': 'R'}
        last_event = schedule.iloc[-1]
        return {'year': int(last_event['Year']), 'gp': last_event['EventName'], 'session_type': 'R'}
    
    def _get_weather_from_session(self, session) -> pd.DataFrame:
        """Extract weather data from FastF1 session."""
        try:
            return session.weather_data.copy() if hasattr(session, 'weather_data') else pd.DataFrame()
        except:
            return pd.DataFrame()
    
    def _get_driver_info(self, session) -> pd.DataFrame:
        """Extract driver info from FastF1 session."""
        try:
            results = session.results
            drivers = []
            for _, row in results.iterrows():
                team_color = row.get('TeamColor', '#FF0000')
                if not str(team_color).startswith('#'):
                    team_color = f"#{team_color}"
                drivers.append({
                    'driver_number': row['DriverNumber'],
                    'name_acronym': row['Abbreviation'],
                    'team_colour': team_color,
                    'team_name': row['TeamName'],
                    'full_name': f"{row['FirstName']} {row['LastName']}"
                })
            return pd.DataFrame(drivers)
        except:
            return pd.DataFrame()
    
    def _parse_jolpica_laps(self, lap_data: dict, year: int, round_num: int) -> pd.DataFrame:
        """Parse Jolpica lap times into DataFrame."""
        # This is a simplified parser - actual Jolpica format may vary
        return pd.DataFrame()
    
    def save_replay(self, data: dict, name: str) -> str:
        """Save session data for offline replay."""
        filepath = self.replay_dir / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.pkl"
        save_data = {}
        for k, v in data.items():
            if k in ('telemetry', 'location'):
                save_data[k] = {dk: dv.to_dict('records') for dk, dv in v.items()} if isinstance(v, dict) else v
            elif isinstance(v, pd.DataFrame):
                save_data[k] = v.to_dict('records')
            elif k != 'live_client':
                save_data[k] = v
        
        with open(filepath, 'wb') as f:
            pickle.dump(save_data, f)
        return str(filepath)
    
    def _load_replay(self, filepath: str) -> dict:
        with open(filepath, 'rb') as f:
            data = pickle.load(f)
        
        for k in ['laps', 'stints', 'weather', 'drivers']:
            if k in data:
                data[k] = pd.DataFrame(data[k])
        data['telemetry'] = {k: pd.DataFrame(v) for k, v in data.get('telemetry', {}).items()}
        data['location'] = {k: pd.DataFrame(v) for k, v in data.get('location', {}).items()}
        data['source'] = 'replay'
        return data
    
    def get_available_replays(self) -> list:
        """List available replay files."""
        files = list(self.replay_dir.glob("*.pkl"))
        return sorted([f.name for f in files], reverse=True)