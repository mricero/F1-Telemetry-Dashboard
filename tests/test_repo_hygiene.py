"""Repository hygiene checks (IMPROVEMENTS.md REPO-01).

A virtualenv was committed before `.gitignore` covered it; the Windows
launcher `.exe` files trip security scanners and bloat `.git`. These tests
assert the working tree stays free of tracked environment directories.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_no_virtualenv_files_are_tracked():
    """REPO-01 acceptance: `git ls-files | grep -c venv` -> 0."""
    tracked = _tracked_files()
    venv_files = [path for path in tracked if "venv" in path.lower()]
    assert venv_files == [], f"virtualenv files are tracked: {venv_files[:10]}"


def test_virtualenv_directories_are_ignored():
    """The ignore rules that should have prevented REPO-01 in the first place."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".venv/", ".venv311/", "venv/"):
        assert pattern in gitignore, f"{pattern} missing from .gitignore"
