"""The lint rule set stays broad (REPO-06).

`select = ["E4", "E7", "E9", "F"]` caught undefined names and unused imports
and nothing else - not the pickle loader, not the naive datetimes behind
earlier timezone bugs, not a mutable default.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RULES = ("B", "UP", "SIM", "I", "PD", "NPY", "PERF", "RUF", "S", "DTZ")


def _config() -> str:
    return (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")


class TestRuleSelection:
    @pytest.mark.parametrize("rule", REQUIRED_RULES)
    def test_rule_family_is_selected(self, rule):
        config = _config()
        section = config[config.index("[tool.ruff.lint]") :]

        assert f'"{rule}"' in section.split("ignore")[0], f"{rule} is not selected"

    def test_every_ignore_carries_a_reason(self):
        config = _config()
        block = config[config.index("ignore = [") : config.index("]", config.index("ignore = ["))]
        for line in block.splitlines()[1:]:
            code = line.strip().strip('",')
            if not code or code.startswith("#"):
                continue
            assert "#" in block[: block.index(line)], f"{code} is ignored without a reason"


class TestTheTreeIsClean:
    def test_ruff_reports_nothing(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout or result.stderr

    def test_no_stale_rule_codes_are_configured(self):
        """Ruff warns about removed rules; the config should not name any."""
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        assert "have been removed" not in (result.stderr + result.stdout)
