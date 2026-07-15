"""Streamlit UI Layout Components"""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from typing import Dict, Optional


# Color mappings for tyre compounds
COMPOUND_COLORS = {
    "SOFT": "red",
    "MEDIUM": "yellow", 
    "HARD": "white",
    "INTERMEDIATE": "green",
    "WET": "blue",
    "UNKNOWN": "gray"
}


def render_header():
    """Render page header and configuration."""
    st.set_page_config(page_title="F1 Telemetry Dashboard", layout="wide")
    st.title("F1 Telemetry Dashboard")
    st.caption("Historical (FastF1) - Live (SignalR - FREE) - Replay (Local)")


def render_session_selector(data_manager) -> dict:
    """Session selection with live detection."""
    
    # Check for live session
    is_race_weekend = data_manager._is_race_weekend()
    
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        source = st.selectbox(
            "Data Source",
            ["Auto (Live - Historical)", "FastF1 (Historical)", "LiveF1 (Historical)", "Live (SignalR)", "Replay (Saved)"],
            index=0
        )
    
    # Live indicator
    live_session = None
    if "Auto" in source and is_race_weekend:
        with col2:
            st.success("LIVE SESSION DETECTED - Race weekend active!")
            live_session = True
    elif "Live" in source:
        with col2:
            st.warning("LIVE MODE - Attempting SignalR connection...")
        live_session = True
    
    # Historical selection
    if not live_session or "Replay" in source:
        with col2:
            years = st.selectbox("Season", [2025, 2024, 2023], index=0)
        
        with col3:
            # Get available GPs for selected year - use FastF1 for all historical sources
            meetings = data_manager.fastf1.get_available_sessions(years)
            gps = sorted(meetings['EventName'].unique())
            
            gp = st.selectbox("Grand Prix", gps) if gps else st.selectbox("Grand Prix", ["No data"])
        
        session_types = ['FP1', 'FP2', 'FP3', 'Q', 'S', 'R']
        session_type = st.selectbox("Session", session_types, index=len(session_types)-1)
        
        # Replay file selection
        if "Replay" in source:
            replays = data_manager.get_available_replays()
            if replays:
                replay_file = st.selectbox("Replay File", replays)
            else:
                st.info("No replay files available")
                replay_file = None
        else:
            replay_file = None
    else:
        years = None
        gp = None
        session_type = None
        replay_file = None
    
    source_map = {
        "Auto (Live - Historical)": "auto",
        "FastF1 (Historical)": "fastf1",
        "LiveF1 (Historical)": "livef1",
        "Live (SignalR)": "live",
        "Replay (Saved)": "replay"
    }
    
    return {
        'source': source_map.get(source, "auto"),
        'year': years,
        'gp': gp,
        'session_type': session_type,
        'replay_file': replay_file,
        'is_live': live_session
    }