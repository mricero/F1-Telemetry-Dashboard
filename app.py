"""Main Streamlit Application - F1 Telemetry Dashboard"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from typing import Optional
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from data.source_manager import DataSourceManager
from processing.telemetry_processor import TelemetryProcessor
from config import config


COMPOUND_COLORS = {
    "SOFT": "red",
    "MEDIUM": "yellow", 
    "HARD": "white",
    "INTERMEDIATE": "green",
    "WET": "blue",
    "UNKNOWN": "gray"
}


def render_session_selector(data_manager) -> dict:
    """Session selection with live detection."""
    
    # Check for live session
    is_race_weekend = data_manager._is_race_weekend()
    
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        source = st.selectbox(
            "Data Source",
            ["Auto (Live → Historical)", "FastF1 (Historical)", "LiveF1 (Historical)", "Live (SignalR)", "Replay (Saved)"],
            index=0
        )
    
    # Live indicator
    live_session = None
    if "Auto" in source and is_race_weekend:
        with col2:
            st.success("🔴 LIVE SESSION DETECTED - Race weekend active!")
            live_session = True
    elif "Live" in source:
        with col2:
            st.warning("🔴 LIVE MODE - Attempting SignalR connection...")
            live_session = True
    
    # Historical selection
    if not live_session or "Replay" in source:
        with col2:
            years = st.selectbox("Season", [2025, 2024, 2023], index=0)
        
        with col3:
            # Get available GPs for selected year
            if "FastF1" in source or "Auto" in source:
                meetings = data_manager.fastf1.get_available_sessions(years)
                gps = sorted(meetings['EventName'].unique())
            else:
                schedule = data_manager.jolpica.get_schedule(years)
                gps = sorted(schedule['race_name'].unique())
            
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
        "Auto (Live → Historical)": "auto",
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
    }


def create_telemetry_chart(telemetry_data: dict, config: dict, color_map: dict) -> Optional[go.Figure]:
    """Create multi-driver telemetry line chart."""
    col = config['col']
    unit = config['unit']
    
    fig = go.Figure()
    has_data = False
    
    for driver, df in telemetry_data.items():
        if df.empty or col not in df.columns or 'Distance' not in df.columns:
            continue
        
        has_data = True
        color = color_map.get(driver, "#888888")
        
        if col == 'Gear':
            # Gear as step chart
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[col],
                mode='lines', name=driver,
                line=dict(color=color, shape='hv'),
                hovertemplate=f"{driver}: %{{y}}<br>Distance: %{{x}}m<extra></extra>"
            ))
        else:
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[col],
                mode='lines', name=driver,
                line=dict(color=color, width=2),
                hovertemplate=f"{driver}: %{{y}} {unit}<br>Distance: %{{x}}m<extra></extra>"
            ))
    
    if not has_data:
        return None
    
    fig.update_layout(
        title=f"{col} by Track Distance",
        xaxis_title="Distance (m)",
        yaxis_title=f"{col} ({unit})" if unit else col,
        hovermode="x unified",
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    return fig


def render_telemetry_charts(telemetry_data: dict, color_map: dict):
    """Render speed, throttle, brake, rpm, gear, DRS charts."""
    
    if not telemetry_data:
        st.info("No telemetry data available")
        return
    
    tabs = st.tabs(["📈 Speed", "⚡ Throttle", "🛑 Brake", "🔧 RPM", "⚙️ Gear", "🚀 DRS"])
    
    channel_config = {
        "Speed": {"col": "Speed", "unit": "km/h"},
        "Throttle": {"col": "Throttle", "unit": "%"},
        "Brake": {"col": "Brake", "unit": "%"},
        "RPM": {"col": "RPM", "unit": "RPM"},
        "Gear": {"col": "Gear", "unit": ""},
        "DRS": {"col": "DRS", "unit": ""},
    }
    
    for i, (tab_name, cfg) in enumerate(channel_config.items()):
        with tabs[i]:
            fig = create_telemetry_chart(telemetry_data, cfg, color_map)
            if fig:
                st.plotly_chart(fig, use_container_width=True)


def render_lap_times(laps_df: pd.DataFrame, color_map: dict):
    """Render lap time chart with pit stop indicators."""
    if laps_df.empty:
        st.warning("No lap data available")
        return
    
    fig = go.Figure()
    
    for driver in laps_df['DriverAcronym'].unique():
        driver_laps = laps_df[laps_df['DriverAcronym'] == driver].sort_values('LapNumber')
        color = color_map.get(driver, "#888888")
        
        # Convert LapTime to seconds
        lap_times_sec = driver_laps['LapTime'].dt.total_seconds()
        
        # Pit out lap markers
        pit_out = driver_laps['IsPitOutLap'] == True
        
        fig.add_trace(go.Scatter(
            x=driver_laps['LapNumber'],
            y=lap_times_sec,
            mode='lines+markers',
            name=driver,
            line=dict(color=color),
            marker=dict(
                color=['red' if p else color for p in pit_out],
                size=8,
                symbol=['diamond' if p else 'circle' for p in pit_out]
            ),
            hovertemplate=(
                f"{driver}: Lap %{{x}}<br>"
                f"Time: %{{customdata}}<br>"
                f"Pit: %{{text}}<extra></extra>"
            ),
            customdata=driver_laps['LapTime'].astype(str),
            text=['🔧 PIT OUT' if p else '' for p in pit_out]
        ))
    
    fig.update_layout(
        title="Lap Times by Driver",
        xaxis_title="Lap Number",
        yaxis_title="Lap Time (seconds)",
        hovermode="x unified",
        height=500
    )
    
    st.plotly_chart(fig, use_container_width=True)


def render_tire_strategy_strategy(stints_df: pd.DataFrame, color_map: dict):
    """Render horizontal bar chart for tire strategy."""
    
    if stints_df.empty:
        st.warning("No tire stint data available")
        return
    
    fig = go.Figure()
    
    for _, row in stints_df.iterrows():
        driver = row.get('DriverAcronym') or row.get('Driver', '')
        compound = row['Compound'].upper()
        
        fig.add_trace(go.Bar(
            x=[row['LapCount']],
            y=[driver],
            base=row['LapStart'],
            orientation='h',
            marker=dict(color=COMPOUND_COLORS.get(compound, "gray")),
            hovertemplate=(
                f"{driver}: {compound}<br>"
                f"Laps: {row['LapCount']}<br>"
                f"Start: {row['LapStart']}<br>"
                f"End: {row['LapEnd']}<extra></extra>"
            ),
            showlegend=False
        ))
    
    # Add driver labels with team colors
    for driver in stints_df['DriverAcronym'].unique():
        fig.add_annotation(
            x=-2, y=driver, xref="x", yref="y",
            text=f"<b>{driver}</b>", showarrow=False,
            font=dict(color=color_map.get(driver, "#AAA"), size=12),
            align="right", xanchor="right"
        )
    
    fig.update_layout(
        title="Tire Strategy by Driver",
        xaxis_title="Lap Number",
        barmode="stack",
        height=max(400, len(stints_df['DriverAcronym'].unique()) * 30 + 100),
        margin=dict(l=120),
        yaxis=dict(showticklabels=False)
    )
    
    st.plotly_chart(fig, use_container_width=True)


def render_track_map(location_data: dict, color_map: dict):
    """Render track map with driver positions."""
    if not location_data:
        st.info("GPS data not available for track map")
        return
    
    first_driver = next(iter(location_data))
    df = location_data[first_driver]
    
    if df.empty or 'X' not in df.columns:
        st.info("Track map requires GPS data")
        return
    
    fig = go.Figure()
    
    # Circuit outline
    fig.add_trace(go.Scatter(
        x=df['X'], y=df['Y'],
        mode='lines', line=dict(color='#444', width=2),
        name='Circuit', showlegend=False
    ))
    
    # Driver positions (last known)
    for driver, loc_df in location_data.items():
        if loc_df.empty:
            continue
        last_pos = loc_df.iloc[-1]
        fig.add_trace(go.Scatter(
            x=[last_pos['X']], y=[last_pos['Y']],
            mode='markers+text',
            marker=dict(color=color_map.get(driver, "#888"), size=12),
            text=[driver], textposition="top center",
            name=driver, showlegend=False
        ))
    
    fig.update_layout(
        title="Track Map - Driver Positions",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        height=500, showlegend=False
    )
    
    st.plotly_chart(fig, use_container_width=True)


def main():
    """Main Streamlit application."""
    # Page config
    st.set_page_config(page_title="F1 Telemetry Dashboard", layout="wide")
    st.title("🏎️ Formula 1 Telemetry Dashboard")
    st.caption("Historical (FastF1) • Live (SignalR - FREE) • Replay (Local)")
    
    # Initialize managers
    if 'data_manager' not in st.session_state:
        st.session_state.data_manager = DataSourceManager()
    if 'processor' not in st.session_state:
        st.session_state.processor = TelemetryProcessor()
    
    data_manager = st.session_state.data_manager
    processor = st.session_state.processor
    
    # Session Selection
    with st.expander("📋 Session Selection", expanded=True):
        selection = render_session_selector(data_manager)
    
    # Load Data
    with st.spinner("Loading session data..."):
        try:
            session_data = data_manager.get_session_data(**selection)
        except Exception as e:
            st.error(f"Failed to load session: {e}")
            st.stop()
    
    # Build color map
    color_map = processor.build_driver_color_map(session_data['drivers'])
    
    # Process telemetry
    telemetry_aligned = processor.align_drivers_by_distance(session_data['telemetry'])
    telemetry_processed = {d: processor.normalize_units(df) for d, df in telemetry_aligned.items()}
    
    # Process laps & stints
    laps_processed = processor.process_laps(session_data['laps'], session_data['drivers'])
    stints_processed = processor.process_stints(session_data['stints'])
    
    # Display Session Info
    info = session_data['session_info']
    live_badge = " 🔴 **LIVE**" if session_data.get('is_live') else ""
    st.markdown(f"### {info.get('gp', '')} {info.get('year', '')} - {info.get('session_type', '')}{live_badge}")
    st.caption(f"Source: {session_data.get('source', 'unknown')}")
    
    # Live mode handling
    if session_data.get('is_live'):
        st.warning("🔴 **LIVE MODE** - Connecting to F1 SignalR feed...")
        live_client = session_data.get('live_client')
        if live_client:
            if st.button("Start Live Stream"):
                live_client.start_async()
                st.success("Live stream started! Data will appear below.")
    
    # Telemetry Charts
    st.markdown("---")
    st.subheader("📊 Telemetry Channels")
    render_telemetry_charts(telemetry_processed, color_map)
    
    # Lap Times
    st.markdown("---")
    st.subheader("⏱️ Lap Times")
    render_lap_times(laps_processed, color_map)
    
    # Tire Strategy
    st.markdown("---")
    st.subheader("🛞 Tire Strategy")
    render_tire_strategy_strategy(stints_processed, color_map)
    
    # Track Map
    st.markdown("---")
    st.subheader("🗺️ Track Map")
    render_track_map(session_data['location'], color_map)
    
    # Replay Save Option
    if not session_data.get('is_live') and st.button("💾 Save Session for Replay"):
        name = f"{info.get('gp', 'race')}_{info.get('session_type', 'R')}"
        path = data_manager.save_replay(session_data, name)
        st.success(f"Saved to {path}")


if __name__ == "__main__":
    main()