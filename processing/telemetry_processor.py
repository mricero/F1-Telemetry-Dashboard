"""Telemetry Processor - Distance alignment, normalization, color mapping"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional


class TelemetryProcessor:
    """Processes raw telemetry into visualization-ready format."""
    
    DISTANCE_STEP = 5  # meters - uniform distance grid for alignment
    
    def __init__(self):
        self.driver_color_map = {}
    
    def build_driver_color_map(self, drivers_df: pd.DataFrame) -> dict:
        """Map driver acronyms to team colors."""
        color_map = {}
        for _, row in drivers_df.iterrows():
            acronym = row.get('name_acronym') or row.get('TeamName', '')[:3].upper()
            color = row.get('team_colour') or row.get('TeamColour', '#888888')
            if not str(color).startswith('#'):
                color = f"#{color}"
            color_map[acronym] = color
        self.driver_color_map = color_map
        return color_map
    
    def resample_to_distance_grid(self, 
                                   telemetry_df: pd.DataFrame, 
                                   distance_col: str = 'Distance') -> pd.DataFrame:
        """
        Resample telemetry to uniform 5m distance grid.
        Enables accurate multi-driver comparison at same track position.
        """
        if telemetry_df.empty or distance_col not in telemetry_df.columns:
            return telemetry_df
        
        # Sort by distance
        df = telemetry_df.sort_values(distance_col).reset_index(drop=True)
        
        # Create uniform grid
        max_dist = df[distance_col].max()
        if pd.isna(max_dist) or max_dist == 0:
            return df
        
        grid = np.arange(0, max_dist, self.DISTANCE_STEP)
        
        # Interpolate each channel
        result = pd.DataFrame({distance_col: grid})
        for col in ['Speed', 'Throttle', 'Brake', 'RPM', 'nGear', 'DRS']:
            if col in df.columns:
                # Use linear interpolation
                result[col] = np.interp(grid, df[distance_col], df[col])
        
        return result
    
    def align_drivers_by_distance(self, 
                                   telemetry_dict: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """Align all drivers to same distance grid."""
        aligned = {}
        for driver, df in telemetry_dict.items():
            aligned[driver] = self.resample_to_distance_grid(df)
        return aligned
    
    def normalize_units(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize units for consistent display."""
        df = df.copy()
        if 'Brake' in df.columns:
            # FastF1: boolean → 0/100, LiveF1: already 0/100
            if df['Brake'].max() <= 1:
                df['Brake'] = df['Brake'] * 100
        if 'Throttle' in df.columns:
            if df['Throttle'].max() <= 1:
                df['Throttle'] = df['Throttle'] * 100
        if 'nGear' in df.columns:
            df['Gear'] = df['nGear'].replace(0, 'N').astype(str)
        return df
    
    def process_laps(self, laps_df: pd.DataFrame, drivers_df: pd.DataFrame) -> pd.DataFrame:
        """Clean and enrich lap data for visualization."""
        if laps_df.empty:
            return laps_df
        df = laps_df.copy()
        # Merge driver info
        if 'Driver' in df.columns and 'driver_number' in drivers_df.columns:
            driver_map = drivers_df.set_index('driver_number')['name_acronym'].to_dict()
            df['DriverAcronym'] = df['Driver'].map(driver_map)
        return df
    
    def process_stints(self, stints_df: pd.DataFrame) -> pd.DataFrame:
        """Prepare stint data for tire strategy chart."""
        if stints_df.empty:
            return stints_df
        df = stints_df.copy()
        df['Compound'] = df['Compound'].fillna('Unknown').str.upper()
        df['LapCount'] = df['LapEnd'] - df['LapStart'] + 1
        return df
    
    def process_live_telemetry(self, live_adapter, max_buffer_size: int = 1000) -> Dict[str, pd.DataFrame]:
        """Process buffered live telemetry data."""
        from data.live_adapter import LiveDataProcessor
        
        car_data = live_adapter.get_buffered_data('CarData.z')
        position_data = live_adapter.get_buffered_data('Position.z')
        timing_data = live_adapter.get_buffered_data('TimingData')
        
        processed = {}
        
        if car_data:
            df = LiveDataProcessor.parse_car_data(car_data)
            if not df.empty:
                # Group by driver and resample
                for driver in df['driver_number'].unique():
                    driver_df = df[df['driver_number'] == driver].copy()
                    if len(driver_df) > 1:
                        # Add distance if position data available
                        if position_data:
                            pos_df = LiveDataProcessor.parse_position_data(position_data)
                            driver_pos = pos_df[pos_df['driver_number'] == driver]
                            if not driver_pos.empty:
                                # Simple distance approximation
                                driver_df['Distance'] = np.arange(len(driver_df)) * 100
                        processed[driver] = self.normalize_units(driver_df)
        
        return processed


if __name__ == "__main__":
    # Test the processor
    processor = TelemetryProcessor()
    
    # Test resampling
    test_df = pd.DataFrame({
        'Distance': [0, 100, 200, 300, 400],
        'Speed': [100, 150, 200, 250, 300],
        'Throttle': [50, 60, 70, 80, 90],
        'Brake': [0, 0, 0, 0, 0],
        'RPM': [5000, 7000, 9000, 11000, 13000],
        'nGear': [2, 3, 4, 5, 6],
        'DRS': [0, 0, 1, 1, 1]
    })
    
    resampled = processor.resample_to_distance_grid(test_df)
    print(f"Original: {len(test_df)} rows")
    print(f"Resampled: {len(resampled)} rows")
    print(resampled.head())