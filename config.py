"""Runtime Configuration"""

from dataclasses import dataclass
import os

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
    distance_step: int = 5  # meters for distance grid alignment
    cache_ttl_seconds: int = 3600  # Streamlit cache TTL


config = Config()
