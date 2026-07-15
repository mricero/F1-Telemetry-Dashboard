"""Tests for FastF1 Adapter"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
import numpy as np
from data.fastf1_adapter import FastF1Adapter


class TestFastF1Adapter:
    """Test FastF1Adapter class"""
    
    def test_init_creates_cache_dir(self, tmp_path):
        """Test that cache directory is created"""
        cache_dir = tmp_path / "test_cache"
        adapter = FastF1Adapter(cache_dir=str(cache_dir))
        assert cache_dir.exists()
    
    @patch('data.fastf1_adapter.fastf1.Cache.enable_cache')
    def test_init_enables_cache(self, mock_enable_cache):
        """Test that FastF1 cache is enabled"""
        adapter = FastF1Adapter(cache_dir="./test_cache")
        mock_enable_cache.assert_called_once_with("./test_cache")
    
    @patch('data.fastf1_adapter.fastf1.get_event_schedule')
    def test_get_available_sessions(self, mock_get_schedule):
        """Test getting available sessions"""
        # Mock schedule data
        mock_schedule = pd.DataFrame({
            'Year': [2024, 2024, 2024],
            'EventName': ['Bahrain', 'Saudi Arabia', 'Australia'],
            'EventDate': [
                pd.Timestamp('2024-03-02', tz='UTC'),
                pd.Timestamp('2024-03-09', tz='UTC'),
                pd.Timestamp('2025-03-16', tz='UTC')  # Future race
            ]
        })
        mock_get_schedule.return_value = mock_schedule
        
        adapter = FastF1Adapter()
        sessions = adapter.get_available_sessions([2024])
        
        assert len(sessions) == 2  # Only completed races
        assert 'Bahrain' in sessions['EventName'].values
        assert 'Saudi Arabia' in sessions['EventName'].values
        assert 'Australia' not in sessions['EventName'].values
    
    @patch('data.fastf1_adapter.fastf1.get_session')
    def test_load_session(self, mock_get_session):
        """Test loading a session"""
        mock_session = Mock()
        mock_session.load = Mock()
        mock_get_session.return_value = mock_session
        
        adapter = FastF1Adapter()
        session = adapter.load_session(2024, "Bahrain", "R")
        
        mock_get_session.assert_called_once_with(2024, "Bahrain", "R")
        mock_session.load.assert_called_once_with(
            telemetry=True, laps=True, weather=True, messages=True
        )
    
    @patch('data.fastf1_adapter.fastf1.get_session')
    def test_get_telemetry(self, mock_get_session):
        """Test getting telemetry data"""
        # Create mock telemetry data
        mock_telemetry = pd.DataFrame({
            'Distance': [0, 100, 200],
            'Speed': [100, 200, 300],
            'Throttle': [50, 80, 100],
            'Brake': [0, 0, 50],
            'RPM': [5000, 10000, 12000],
            'nGear': [2, 3, 4],
            'DRS': [0, 1, 1]
        })
        
        mock_laps = Mock()
        mock_laps.pick_drivers.return_value = mock_laps
        mock_laps.get_telemetry.return_value = mock_telemetry
        mock_laps.add_distance.return_value = mock_telemetry
        
        mock_session = Mock()
        mock_session.laps = mock_laps
        mock_get_session.return_value = mock_session
        
        adapter = FastF1Adapter()
        telemetry = adapter.get_telemetry(mock_session, "VER")
        
        assert not telemetry.empty
        assert list(telemetry.columns) == ['Distance', 'Speed', 'Throttle', 'Brake', 'RPM', 'nGear', 'DRS']
    
    @patch('data.fastf1_adapter.fastf1.get_session')
    def test_get_laps(self, mock_get_session):
        """Test getting lap data"""
        mock_laps = pd.DataFrame({
            'Driver': ['VER', 'HAM', 'VER'],
            'LapNumber': [1, 1, 2],
            'LapTime': pd.to_timedelta(['00:01:30', '00:01:31', '00:01:29']),
            'Sector1Time': pd.to_timedelta(['00:00:25', '00:00:26', '00:00:24']),
            'Sector2Time': pd.to_timedelta(['00:00:35', '00:00:36', '00:00:34']),
            'Sector3Time': pd.to_timedelta(['00:00:30', '00:00:29', '00:00:31']),
            'IsPitOutLap': [False, False, False]
        })
        
        mock_session = Mock()
        mock_session.laps = mock_laps
        mock_get_session.return_value = mock_session
        
        adapter = FastF1Adapter()
        laps = adapter.get_laps(mock_session)
        
        assert len(laps) == 3
        assert 'Driver' in laps.columns
        assert 'LapNumber' in laps.columns
    
    @patch('data.fastf1_adapter.fastf1.get_session')
    def test_get_stints(self, mock_get_session):
        """Test getting stint data"""
        mock_laps = pd.DataFrame({
            'Driver': ['VER', 'VER', 'HAM'],
            'Stint': [1, 2, 1],
            'Compound': ['SOFT', 'MEDIUM', 'SOFT'],
            'LapStart': [1, 20, 1],
            'LapEnd': [19, 57, 57]
        })
        
        mock_session = Mock()
        mock_session.laps = mock_laps
        mock_get_session.return_value = mock_session
        
        adapter = FastF1Adapter()
        stints = adapter.get_stints(mock_session)
        
        assert len(stints) == 3
        assert 'Compound' in stints.columns
    
    @patch('data.fastf1_adapter.fastf1.get_session')
    def test_get_location(self, mock_get_session):
        """Test getting GPS location data"""
        mock_pos = pd.DataFrame({
            'Distance': [0, 1000, 2000],
            'X': [100, 200, 300],
            'Y': [50, 150, 250],
            'Z': [10, 20, 30]
        })
        
        mock_laps = Mock()
        mock_laps.pick_drivers.return_value = mock_laps
        mock_laps.get_pos_data.return_value = mock_pos
        mock_laps.add_distance.return_value = mock_pos
        
        mock_session = Mock()
        mock_session.laps = mock_laps
        mock_get_session.return_value = mock_session
        
        adapter = FastF1Adapter()
        location = adapter.get_location(mock_session, "VER")
        
        assert not location.empty
        assert list(location.columns) == ['Distance', 'X', 'Y', 'Z']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])