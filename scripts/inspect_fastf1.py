#!/usr/bin/env python
"""Inspection script to explore FastF1 data structures (run manually)."""

import fastf1


def inspect_fastf1():
    print("=" * 60)
    print("TESTING FASTF1")
    print("=" * 60)

    # Load a session
    print("\n1. Loading FastF1 session (2024 Bahrain R)...")
    session = fastf1.get_session(2024, "Bahrain", "R")
    session.load()

    # Check laps
    print("\n--- LAPS ---")
    print(f"Laps shape: {session.laps.shape}")
    print(f"Laps columns: {list(session.laps.columns)}")
    if not session.laps.empty:
        print(f"Sample laps:\n{session.laps.head(2).to_string()}")

    # Check if stints exist
    print("\n--- STINTS ---")
    print(f"Has stints attr: {hasattr(session, 'stints')}")
    if hasattr(session, "stints") and session.stints is not None:
        print(f"Stints shape: {session.stints.shape}")
        print(f"Stints columns: {list(session.stints.columns)}")
        if not session.stints.empty:
            print(f"Sample stints:\n{session.stints.head(2).to_string()}")

    # Check results
    print("\n--- RESULTS ---")
    print(f"Results shape: {session.results.shape}")
    print(f"Results columns: {list(session.results.columns)}")
    if not session.results.empty:
        print(f"Sample results:\n{session.results.head(2).to_string()}")

    # Check telemetry
    print("\n--- TELEMETRY ---")
    if not session.laps.empty:
        ver_laps = session.laps.pick_drivers("VER")
        if not ver_laps.empty:
            telemetry = ver_laps.get_telemetry()
            print(f"Telemetry shape: {telemetry.shape}")
            print(f"Telemetry columns: {list(telemetry.columns)}")
            if not telemetry.empty:
                print(f"Sample telemetry:\n{telemetry.head(2).to_string()}")

    # Check location
    print("\n--- LOCATION (GPS) ---")
    if not session.laps.empty:
        ver_laps = session.laps.pick_drivers("VER")
        if not ver_laps.empty:
            pos = ver_laps.get_pos_data()
            print(f"Position shape: {pos.shape}")
            print(f"Position columns: {list(pos.columns)}")
            if not pos.empty:
                print(f"Sample position:\n{pos.head(2).to_string()}")

    # Check weather
    print("\n--- WEATHER ---")
    if hasattr(session, "weather_data") and session.weather_data is not None:
        print(f"Weather shape: {session.weather_data.shape}")
        print(f"Weather columns: {list(session.weather_data.columns)}")
        if not session.weather_data.empty:
            print(f"Sample weather:\n{session.weather_data.head(2).to_string()}")


if __name__ == "__main__":
    inspect_fastf1()
