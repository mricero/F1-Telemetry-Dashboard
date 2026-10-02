"""`.env.example` documents every environment variable the app reads (REPO-13)."""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("app.py", "config.py", "f1dash_cli.py")
SOURCE_FOLDERS = ("data", "processing", "ui")
# Set and read by app.py itself to stop a relaunch loop; not configuration.
INTERNAL = {"F1_DASHBOARD_RELAUNCHED"}

READ = re.compile(
    r"""(?:os\.getenv|environ\.get|environ\.setdefault|environ\.pop|os\.environ\[)\s*\(?\s*"""
    r"""(?:(?P<quote>["'])(?P<literal>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)|(?P<name>[A-Za-z_]\w*))"""
)
CONSTANT = re.compile(
    r"""^\s*(?P<name>[A-Z_][A-Z0-9_]*)\s*(?::\s*[\w\[\], |]+)?=\s*["'](?P<value>[A-Z][A-Z0-9_]+)["']""",
    re.M,
)


def _sources() -> list[Path]:
    paths = [PROJECT_ROOT / name for name in SOURCE_FILES if (PROJECT_ROOT / name).is_file()]
    for folder in SOURCE_FOLDERS:
        paths.extend(sorted((PROJECT_ROOT / folder).rglob("*.py")))
    return paths


def _variables_read() -> tuple[set[str], set[str]]:
    texts = {path: path.read_text(encoding="utf-8") for path in _sources()}
    constants = {
        match["name"]: match["value"]
        for text in texts.values()
        for match in CONSTANT.finditer(text)
    }
    found, unresolved = set(), set()
    for path, text in texts.items():
        for match in READ.finditer(text):
            if match["literal"]:
                found.add(match["literal"])
            elif match["name"] in constants:
                found.add(constants[match["name"]])
            elif match["name"].isupper():
                unresolved.add(f"{path.relative_to(PROJECT_ROOT)}: {match['name']}")
    return found - INTERNAL, unresolved


def _documented() -> set[str]:
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))


def test_the_scan_finds_the_known_variables():
    """Guards the scanner itself: an empty result would pass vacuously."""
    found, _ = _variables_read()

    assert {"DEFAULT_YEAR", "F1TV_SUBSCRIPTION_TOKEN", "LOG_LEVEL"} <= found


def test_every_variable_the_code_reads_is_documented():
    found, unresolved = _variables_read()

    assert not unresolved, f"cannot resolve the variable name: {sorted(unresolved)}"
    missing = found - _documented()
    assert not missing, f"read by the code but missing from .env.example: {sorted(missing)}"


def test_path_overrides_are_documented():
    """config reads these through PATH_OVERRIDES, which the scan cannot see."""
    import config

    assert set(config.PATH_OVERRIDES.values()) <= _documented()


@pytest.mark.parametrize(
    "name",
    [
        "F1_NETWORK_TESTS",
        "F1_LIVE_CONTROLS",
        "F1_LIVE_AUTORECORD",
        "F1_UPDATE_CHECK",
        "F1_REPLAY_PLAYER",
        "F1_CACHE_MAX_ENTRIES",
        "F1_CACHE_MAX_BYTES",
        "DEFAULT_YEAR",
        "DEFAULT_GP",
        "DEFAULT_SESSION",
    ],
)
def test_planned_and_test_only_flags_are_documented(name):
    assert name in _documented()


def test_no_value_is_filled_in():
    """The example is committed; a real token must never land in it."""
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("F1TV_SUBSCRIPTION_TOKEN") or "F1TV_SUBSCRIPTION_TOKEN=" in line:
            assert line.strip().endswith("="), "the token example must stay empty"
