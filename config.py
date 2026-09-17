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
    distance_step: int = 5  # meters for distance grid alignment
    cache_ttl_seconds: int = 3600  # Streamlit cache TTL


config = Config()
