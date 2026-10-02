"""Repository hygiene checks (IMPROVEMENTS.md REPO-01).

A virtualenv was committed before `.gitignore` covered it; the Windows
launcher `.exe` files trip security scanners and bloat `.git`. These tests
assert the working tree stays free of tracked environment directories.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _in_git_work_tree() -> bool:
    """False in a source ZIP or sdist, where ``git ls-files`` cannot run (TEST-08)."""
    if shutil.which("git") is None:
        return False
    result = subprocess.run(
        ["git", "--no-optional-locks", "rev-parse", "--is-inside-work-tree"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _tracked_files() -> list[str]:
    if not _in_git_work_tree():
        pytest.skip("not a git work tree")
    result = subprocess.run(
        ["git", "--no-optional-locks", "ls-files"],
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


def test_line_endings_are_normalised():
    """REPO-14: without `.gitattributes` a Windows checkout showed ~55 files
    as modified with no content change (CRLF worktree vs LF index)."""
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "eol=lf" in attributes
    for binary in ("*.parquet", "*.gz"):
        assert f"{binary} binary" in attributes


@pytest.mark.parametrize(
    "pattern",
    [
        "test_cache/",
        "tests/js/node_modules/",
        "_to_delete/",
        ".hypothesis/",
        "metrics_store.sqlite",
        "ff1_cache/",
        "replay_sessions/",
    ],
)
def test_generated_folders_are_ignored(pattern):
    """TEST-08 / REPO-24: what tests, tools and the app write stays out of git."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert pattern in gitignore.splitlines()


def test_the_env_example_is_not_ignored():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!.env.example" in gitignore.splitlines()


def test_no_legacy_dashboard_preview_is_tracked():
    """DOC-04: scripts/preview_replay_player.py builds a current preview."""
    assert "dashboard_preview.html" not in _tracked_files() or not (
        REPO_ROOT / "dashboard_preview.html"
    ).exists()
