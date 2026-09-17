"""Runtime Configuration"""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass
class Config:
    fastf1_cache_dir: str = os.getenv("FASTF1_CACHE_DIR", "./ff1_cache")
    replay_dir: str = os.getenv("REPLAY_DIR", "./replay_sessions")
    default_year: int = int(os.getenv("DEFAULT_YEAR", "2024"))
    default_gp: str = os.getenv("DEFAULT_GP", "Abu Dhabi")
    default_session: str = os.getenv("DEFAULT_SESSION", "R")
    # Logging verbosity for the adapters' degraded paths (REPO-11).
    log_level: str = os.getenv("LOG_LEVEL", "WARNING")
    # Note: the distance-grid step lives on TelemetryProcessor.DISTANCE_STEP,
    # and cache lifetimes are set at each @st.cache_data call site.


config = Config()
