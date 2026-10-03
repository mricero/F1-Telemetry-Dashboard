"""Type checking is enforced, not informational (REPO-04).

21 mypy errors sat unchecked because mypy was not in CI - mostly implicit
`Optional` defaults, which is also what let a `None` year reach a loader that
required an int.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The whole package (REPO-10); the root app.py is a three-line shim.
CHECKED = ("src",)


class TestMypy:
    def test_the_checked_packages_are_clean(self):
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--ignore-missing-imports", *CHECKED],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout or result.stderr

    def test_ci_runs_mypy(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")

        assert "python -m mypy --ignore-missing-imports src" in workflow

    def test_implicit_optional_is_rejected(self):
        config = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        assert "no_implicit_optional = true" in config

    def test_processing_is_checked_more_strictly(self):
        config = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        section = config[config.index("[[tool.mypy.overrides]]") :]

        assert 'module = "f1dash.processing.*"' in section
        assert "check_untyped_defs = true" in section
