"""Runtime configuration, the app version and where the app keeps its files.

Importing this module loads ``.env`` (from :data:`Config.env_path`) before
anything reads the environment, which is why ``app.py`` imports it ahead of
the adapters.

Where files live (DIST-03, CACHE-04):

* **A git checkout** (a ``.git`` next to ``app.py``) keeps the repo-local
  paths development has always used: ``ff1_cache/``, ``replay_sessions/``,
  ``metrics_store.sqlite`` and ``.env`` in the project folder.
* **An installed copy** (``uv tool install``, a wheel) has no project folder
  worth writing into - it would be the tool's site-packages or wherever the
  user typed ``f1dash`` - so it uses the per-user directories from
  ``platformdirs``.

``FASTF1_CACHE_DIR``, ``REPLAY_DIR`` and ``F1_METRICS_STORE`` override either.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import platformdirs

APP_NAME = "f1dash"
PROJECT_ROOT = Path(__file__).resolve().parent
UNKNOWN_VERSION = "0+unknown"


def is_checkout(root: Path = PROJECT_ROOT) -> bool:
    """True when the code runs from a git checkout rather than an install.

    ``.git`` is a directory in a normal clone and a file in a worktree.
    """
    return (root / ".git").exists()


def _version_from_pyproject(root: Path = PROJECT_ROOT) -> str | None:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return None


def read_version(root: Path = PROJECT_ROOT) -> str:
    """The app version (REPO-23).

    An installed copy reads its own distribution metadata. A checkout reads
    ``pyproject.toml`` first, because the metadata of an older install in the
    same environment would name a stale version; the metadata is the
    fallback there and the other way round.
    """
    from_file = _version_from_pyproject(root) if is_checkout(root) else None
    if from_file:
        return from_file
    try:
        return metadata.version(APP_NAME)
    except metadata.PackageNotFoundError:
        return _version_from_pyproject(root) or UNKNOWN_VERSION


__version__ = read_version()


def default_paths(checkout: bool, root: Path = PROJECT_ROOT) -> dict[str, Path]:
    """Where cache, replays, the records store and ``.env`` live by default."""
    if checkout:
        return {
            "fastf1_cache_dir": root / "ff1_cache",
            "replay_dir": root / "replay_sessions",
            "metrics_store_path": root / "metrics_store.sqlite",
            "env_path": root / ".env",
        }
    return {
        "fastf1_cache_dir": Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))
        / "fastf1",
        "replay_dir": Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)) / "replays",
        "metrics_store_path": Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
        / "metrics_store.sqlite",
        "env_path": Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / ".env",
    }


# Environment variables that override a default path, by Config field.
PATH_OVERRIDES = {
    "fastf1_cache_dir": "FASTF1_CACHE_DIR",
    "replay_dir": "REPLAY_DIR",
    "metrics_store_path": "F1_METRICS_STORE",
}


def resolve_paths(
    environ: Mapping[str, str] | None = None,
    checkout: bool | None = None,
    root: Path = PROJECT_ROOT,
) -> dict[str, str]:
    """The four locations after environment overrides, as strings."""
    environ = os.environ if environ is None else environ
    checkout = is_checkout(root) if checkout is None else checkout
    paths = default_paths(checkout, root)
    resolved = {name: str(path) for name, path in paths.items()}
    for name, variable in PATH_OVERRIDES.items():
        value = (environ.get(variable) or "").strip()
        if value:
            resolved[name] = str(Path(value).expanduser())
    return resolved


def load_env_file(env_path: str | Path) -> bool:
    """Load ``.env`` without overriding variables already set."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is a declared dependency; stay usable without it
        return False
    return bool(load_dotenv(Path(env_path), override=False))


@dataclass
class Config:
    fastf1_cache_dir: str
    replay_dir: str
    metrics_store_path: str
    env_path: str
    default_year: int = 2024
    default_gp: str = "Abu Dhabi"
    default_session: str = "R"
    # Logging verbosity for the adapters' degraded paths (REPO-11).
    log_level: str = "WARNING"
    # Note: the distance-grid step lives on TelemetryProcessor.DISTANCE_STEP,
    # and cache lifetimes are set at each @st.cache_data call site.

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        environ = os.environ if environ is None else environ
        paths = resolve_paths(environ)
        return cls(
            **paths,
            default_year=int(environ.get("DEFAULT_YEAR") or "2024"),
            default_gp=environ.get("DEFAULT_GP") or "Abu Dhabi",
            default_session=environ.get("DEFAULT_SESSION") or "R",
            log_level=environ.get("LOG_LEVEL") or "WARNING",
        )


# .env first, so the values it holds (FASTF1_CACHE_DIR, F1TV_SUBSCRIPTION_TOKEN,
# ...) are in the environment before the paths are resolved and before the
# adapters read their variables. Its own location cannot come from it.
load_env_file(resolve_paths()["env_path"])
config = Config.from_env()
