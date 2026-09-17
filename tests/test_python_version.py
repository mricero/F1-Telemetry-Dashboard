"""One Python version story across the repo (REPO-03).

The readme said 3.11+, the tool configs targeted py312 and CI ran 3.12 only.
Black then warned that "Python 3.11 cannot parse code formatted for Python
3.12" whenever it ran on the version the readme told people to use.
"""

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLOOR = (3, 11)


def _pyproject() -> str:
    return (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _workflow() -> str:
    return (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class TestOneVersionStory:
    def test_the_readme_states_the_floor(self):
        readme = (PROJECT_ROOT / "readme.md").read_text(encoding="utf-8")

        assert f"Python {FLOOR[0]}.{FLOOR[1]}+" in readme

    @pytest.mark.parametrize("tool", ["black", "ruff"])
    def test_tool_targets_match_the_floor(self, tool):
        text = _pyproject()
        section = text[text.index(f"[tool.{tool}]") :]
        target = re.search(r'target-version\s*=\s*\[?"?(py\d+)', section)

        assert target, f"{tool} has no target-version"
        assert target.group(1) == f"py{FLOOR[0]}{FLOOR[1]}"

    def test_ci_runs_a_matrix_not_a_single_version(self):
        matrix = re.search(r"python-version:\s*\[([^\]]+)\]", _workflow())

        assert matrix, "no python-version matrix in CI"
        versions = [v.strip().strip('"') for v in matrix.group(1).split(",")]
        assert len(versions) >= 3, f"CI only covers {versions}"
        assert f"{FLOOR[0]}.{FLOOR[1]}" in versions, "the declared floor is not tested"

    def test_this_interpreter_is_at_or_above_the_floor(self):
        assert sys.version_info[:2] >= FLOOR
