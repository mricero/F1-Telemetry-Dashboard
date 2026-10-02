"""Is a newer release out? (DIST-05)

The app asks GitHub's releases API at most once a day, with a 3 s timeout,
and remembers the answer in the user cache directory. Offline, rate-limited
or anything else unexpected: it says nothing. ``F1_UPDATE_CHECK=0`` turns the
check off entirely.

``f1dash update`` uses :func:`latest_release_tag` and :func:`update_command`
to reinstall the newest release with uv.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from importlib import metadata
from pathlib import Path

import platformdirs
import requests

logger = logging.getLogger(__name__)

APP_NAME = "f1dash"
REPOSITORY = "mricero/F1-Telemetry-Dashboard"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
DISABLE_ENV = "F1_UPDATE_CHECK"
CHECK_INTERVAL_S = 24 * 3600
TIMEOUT_S = 3.0
_RELEASE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


def parse_version(tag: str | None) -> tuple[int, ...] | None:
    """``"v0.10.0"`` -> ``(0, 10, 0)``; ``None`` for anything that is not a plain release."""
    match = _RELEASE.match(str(tag or "").strip())
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(latest: str | None, current: str) -> bool:
    latest_version, current_version = parse_version(latest), parse_version(current)
    if latest_version is None or current_version is None:
        return False
    return latest_version > current_version


def checks_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(DISABLE_ENV, "1")).strip().lower() not in {"0", "false", "no", "off"}


def default_cache_path() -> Path:
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)) / "update_check.json"


def latest_release_tag(
    get: Callable = requests.get, timeout: float = TIMEOUT_S, user_agent: str = APP_NAME
) -> str | None:
    """The newest release's tag from GitHub, or ``None`` when it cannot be had."""
    try:
        response = get(
            LATEST_RELEASE_API,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json", "User-Agent": user_agent},
        )
        if response.status_code != 200:
            logger.debug("release check answered HTTP %s", response.status_code)
            return None
        tag = response.json().get("tag_name")
        return str(tag) if tag else None
    except Exception as error:  # offline, DNS, timeout, bad JSON: stay silent
        logger.debug("release check failed: %s", error)
        return None


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, latest: str | None, now: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": now, "latest": latest}), encoding="utf-8")
    except OSError as error:
        logger.debug("could not cache the release check: %s", error)


def cached_latest_tag(
    cache_path: Path | None = None,
    now: float | None = None,
    get: Callable = requests.get,
    user_agent: str = APP_NAME,
) -> str | None:
    """The latest tag, asking GitHub only when the cached answer is a day old.

    A failed check is cached too, so an offline machine does not retry on
    every Streamlit rerun.
    """
    path = cache_path or default_cache_path()
    now = time.time() if now is None else now
    cached = _read_cache(path)
    if cached is not None:
        try:
            fresh = 0 <= now - float(cached.get("checked_at", 0)) < CHECK_INTERVAL_S
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            return cached.get("latest")
    latest = latest_release_tag(get=get, user_agent=user_agent)
    _write_cache(path, latest, now)
    return latest


def update_notice(
    current_version: str,
    cache_path: Path | None = None,
    now: float | None = None,
    get: Callable = requests.get,
    environ: dict | None = None,
) -> str | None:
    """``"Update available: v0.10.0 \u2013 run f1dash update"``, or ``None``.

    ``None`` when checks are disabled, the check failed, or the running
    version is current.
    """
    if not checks_enabled(environ):
        return None
    latest = cached_latest_tag(
        cache_path, now=now, get=get, user_agent=f"{APP_NAME}/{current_version}"
    )
    if not is_newer(latest, current_version):
        return None
    tag = latest if str(latest).startswith("v") else f"v{latest}"
    return f"Update available: {tag} \u2013 run f1dash update"


def installed_from_registry(distribution: str = APP_NAME) -> bool:
    """True for a PyPI install; False for git/local installs and checkouts.

    pip and uv record ``direct_url.json`` for anything not installed from an
    index (PEP 610).
    """
    try:
        dist = metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        return False
    return dist.read_text("direct_url.json") is None


def update_command(tag: str | None, from_registry: bool = False) -> list[str]:
    """The uv command that installs the newest release."""
    if from_registry:
        return ["uv", "tool", "upgrade", APP_NAME]
    ref = tag or "main"
    return ["uv", "tool", "install", "--reinstall", f"git+{REPOSITORY_URL}@{ref}"]
