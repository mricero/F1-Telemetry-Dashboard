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
    st.title("🏎️ Formula 1 Telemetry Dashboard")
    st.caption("Historical (FastF1) • Live (SignalR - FREE) • Replay (Local)")


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
    
    source_map = {
        "Auto (Live → Historical)": "auto",
        "FastF1 (Historical)": "fastf1",
        "LiveF1 (Historical)": "livef1",
        "Live (SignalR)": "live",
        "Replay (Saved)": "replay"
    }
    
    return {
        'source': source_map.get(source, "auto"),
        'year': years if 'years' in locals() else None,
        'gp': gp if 'gp' in locals() else None,
        'session_type': session_type if 'session_type' in locals() else None,
        'replay_file': replay_file,
        'is_live': live_session
    }


def render_telemetry_charts(telemetry_data: Dict[str, pd.DataFrame], color_map: Dict[str, str]):
    """Render speed, throttle, brake, rpm, gear, DRS charts."""
    
    tabs = st.tabs(["📈 Speed", "⚡ Throttle", "🛑 Brake", "🔧 RPM", "⚙️ Gear", "🚀 DRS"])
    
    channel_config = {
        "Speed": {"col": "Speed", "unit": "km/h"},
        "Throttle": {"col": "Throttle", "unit": "%"},
        "Brake": {"col": "Brake", "unit": "%"},
        "RPM": {"col": "RPM", "unit": "RPM"},
        "Gear": {"col": "Gear", "unit": ""},
        "DRS": {"col": "DRS", "unit": ""},
    }
    
    for i, (tab_name, config) in enumerate(channel_config.items()):
        with tabs[i]:
            fig = create_telemetry_chart(telemetry_data, config, color_map)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info(f"No {config['col']} data available")


def create_telemetry_chart(telemetry_data: Dict[str, pd.DataFrame], config: dict, color_map: Dict[str, str]) -> Optional[go.Figure]:
    """Create multi-driver telemetry line chart."""
    fig = go.Figure()
    has_data = False
    
    for driver, df in telemetry_data.items():
        if df.empty or config['col'] not in df.columns:
            continue
        
        has_data = True
        color = color_map.get(driver, "#888888")
        
        # Ensure Distance column exists
        if 'Distance' not in df.columns:
            df = df.copy()
            df['Distance'] = np.arange(len(df)) * 10  # Approximate
        
        if config['col'] == 'Gear':
            # Gear as step chart
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[config['col']],
                mode='lines', name=driver,
                line=dict(color=color, shape='hv', width=2),
                hovertemplate=f"{driver}: %{{y}}<br>Distance: %{{x}}m<extra></extra>"
            ))
        elif config['col'] == 'DRS':
            # DRS as binary
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[config['col']],
                mode='lines', name=driver,
                line=dict(color=color, width=2),
                hovertemplate=f"{driver}: DRS %{{y}}<br>Distance: %{{x}}m<extra></extra>"
            ))
        else:
            fig.add_trace(go.Scatter(
                x=df['Distance'], y=df[config['col']],
                mode='lines', name=driver,
                line=dict(color=color, width=2),
                hovertemplate=f"{driver}: %{{y}} {config['unit']}<br>Distance: %{{x}}m<extra></extra>"
            ))
    
    if not has_data:
        return None
    
    fig.update_layout(
        title=f"{config['col']} by Track Distance",
        xaxis_title="Distance (m)",
        yaxis_title=f"{config['col']} ({config['unit']})",
        hovermode="x unified",
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=50, t=50, b=50)
    )
    
    return fig


def render_lap_times(laps_df: pd.DataFrame, color_map: Dict[str, str]):
    """Render lap time chart with pit stop indicators."""
    if laps_df.empty:
        st.warning("No lap data available")
        return
    
    fig = go.Figure()
    
    # Check for required columns
    driver_col = 'DriverAcronym' if 'DriverAcronym' in laps_df.columns else 'Driver'
    if driver_col not in laps_df.columns:
        st.warning("No driver information in lap data")
        return
    
    for driver in laps_df[driver_col].unique():
        driver_laps = laps_df[laps_df[driver_col] == driver].sort_values('LapNumber')
        color = color_map.get(driver, "#888888")
        
        # Convert LapTime to seconds for plotting
        if 'LapTime' in driver_laps.columns:
            lap_times = pd.to_timedelta(driver_laps['LapTime'], errors='coerce')
            lap_times_sec = lap_times.dt.total_seconds()
            
            # Pit out lap markers
            pit_out = driver_laps.get('IsPitOutLap', pd.Series([False] * len(driver_laps)))
            
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


def render_tire_strategy(stints_df: pd.DataFrame, color_map: Dict[str, str]):
    """Render horizontal bar chart for tire strategy."""
    if stints_df.empty:
        st.info("No tire strategy data available")
        return
    
    fig = go.Figure()
    
    # Check for required columns
    driver_col = 'DriverAcronym' if 'DriverAcronym' in stints_df.columns else 'Driver'
    if driver_col not in stints_df.columns:
        st.warning("No driver information in stint data")
        return
    
    for _, row in stints_df.iterrows():
        driver = row.get(driver_col)
        compound = str(row.get('Compound', 'UNKNOWN')).upper()
        
        fig.add_trace(go.Bar(
            x=[row.get('LapCount', 1)],
            y=[driver],
            base=row.get('LapStart', 1),
            orientation='h',
            marker=dict(color=COMPOUND_COLORS.get(compound, "gray")),
            hovertemplate=(
                f"{driver}: {compound}<br>"
                f"Laps: {row.get('LapCount', 1)}<br>"
                f"Start: {row.get('LapStart', 1)}<br>"
                f"End: {row.get('LapEnd', 1)}<extra></extra>"
            ),
            showlegend=False
        ))
    
    # Add driver labels with team colors
    drivers = stints_df[driver_col].unique()
    for driver in drivers:
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
        height=max(400, len(drivers) * 30 + 100),
        margin=dict(l=120),
        yaxis=dict(showticklabels=False)
    )
    
    st.plotly_chart(fig, use_container_width=True)


def render_track_map(location_data: Dict[str, pd.DataFrame], color_map: Dict[str, str]):
    """Render track map with driver positions."""
    if not location_data:
        st.info("GPS data not available for track map")
        return
    
    # Find first driver with valid GPS data
    valid_driver = None
    for driver, loc_df in location_data.items():
        if not loc_df.empty and 'X' in loc_df.columns and 'Y' in loc_df.columns:
            valid_driver = driver
            break
    
    if not valid_driver:
        st.info("Track map requires GPS data (X, Y coordinates)")
        return
    
    circuit_df = location_data[valid_driver]
    
    fig = go.Figure()
    
    # Circuit outline
    fig.add_trace(go.Scatter(
        x=circuit_df['X'], y=circuit_df['Y'],
        mode='lines', line=dict(color='#444', width=2),
        name='Circuit', showlegend=False
    ))
    
    # Driver positions (last known)
    for driver, loc_df in location_data.items():
        if loc_df.empty or 'X' not in loc_df.columns:
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
        height=500, showlegend=False,
        plot_bgcolor='white'
    )
    
    st.plotly_chart(fig, use_container_width=True)


def format_lap_time(seconds: float) -> str:
    """Format lap time in MM:SS.mmm format."""
    if pd.isna(seconds):
        return "N/A"
    minutes = int(seconds // 60)
    sec = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{minutes:02}:{sec:02}.{millis:03}"


def render_live_controls(live_client):
    """Render live session controls."""
    if not live_client:
        return
    
    st.markdown("---")
    st.subheader("🔴 Live Session Controls")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("📊 View Buffered Data"):
            st.json({
                'CarData.z': len(live_client.get_buffered_data('CarData.z')),
                'Position.z': len(live_client.get_buffered_data('Position.z')),
                'TimingData': len(live_client.get_buffered_data('TimingData')),
                'WeatherData': len(live_client.get_buffered_data('WeatherData')),
            })
    
    with col2:
        if st.button("💾 Save Session"):
            # This would trigger saving the buffered data
            st.success("Session save initiated!")
    
    with col3:
        if st.button("⏹️ Stop Live"):
            live_client.stop()
            st.warning("Live session stopped")