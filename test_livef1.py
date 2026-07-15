#!/usr/bin/env python
"""Test script to inspect LiveF1 data structures"""
from livef1 import get_session
import pandas as pd

def test_livef1():
    print("=" * 60)
    print("TESTING LIVEF1")
    print("=" * 60)
    
    # Load a session - use 'Race' not 'R'
    print("\n1. Loading LiveF1 session (20 (2024 Bahrain Race)...")
    try:
        session = get_session(2024, 'Bahrain', 'Race')
        session.generate(silver=True)
        
        # Check laps
        print("\n--- LAPS ---")
        laps = session.get_laps()
        print(f"Laps shape: {laps.shape}")
        print(f"Laps columns: {list(laps.columns)}")
        if not laps.empty:
            print(f"Sample laps:\n{laps.head(2).to_string()}")
        
        # Check telemetry
        print("\n--- TELEMETRY ---")
        telemetry = session.get_car_telemetry()
        print(f"Telemetry shape: {telemetry.shape}")
        print(f"Telemetry columns: {list(telemetry.columns)}")
        if not telemetry.empty:
            print(f"Sample telemetry:\n{telemetry.head(2).to_string()}")
        
        # Check drivers
        print("\n--- DRIVERS ---")
        drivers = session.drivers
        print(f"Drivers type: {type(drivers)}")
        print(f"Number of drivers: {len(drivers)}")
        if drivers:
            for num, driver in list(drivers.items())[:3]:
                print(f"  Driver {num}: {driver.name_acronym}, Team: {driver.team_name}, Colour: {driver.team_colour}")
        
        # Check if there's position/GPS data
        print("\n--- POSITION/GPS ---")
        try:
            pos = session.get_position()
            print(f"Position shape: {pos.shape if hasattr(pos, 'shape') else 'N/A'}")
            print(f"Position columns: {list(pos.columns) if hasattr(pos, 'columns') else 'N/A'}")
        except Exception as e:
            print(f"Position error: {e}")
        
        # Check if there's stint data
        print("\n--- STINTS ---")
        try:
            stints = session.get_stints()
            print(f"Stints shape: {stints.shape if hasattr(stints, 'shape') else 'N/A'}")
            print(f"Stints columns: {list(stints.columns) if hasattr(stints, 'columns') else 'N/A'}")
        except Exception as e:
            print(f"Stints error: {e}")
            
        # Check weather
        print("\n--- WEATHER ---")
        try:
            weather = session.get_weather()
            print(f"Weather shape: {weather.shape if hasattr(weather, 'shape') else 'N/A'}")
            print(f"Weather columns: {list(weather.columns) if hasattr(weather, 'columns') else 'N/A'}")
        except Exception as e:
            print(f"Weather error: {e}")
            
    except Exception as e:
        print(f"Error loading LiveF1 session: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_livef1()