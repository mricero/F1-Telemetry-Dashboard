"""The pre-commit config runs the gates CI runs (REPO-07, REPO-21).

`pre-commit` was in requirements-dev.txt with no config for it to run, so
the hooks everyone installed did nothing. Later the mirrors-mypy hook ran in
an isolated env with unpinned pandas-stubs and reported 156 errors CI never
saw, which blocked every commit touching processing/.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"


@pytest.fixture(scope="module")
def config() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


class TestPreCommitConfig:
    def test_the_config_exists(self):
        assert CONFIG.is_file()

    def test_it_runs_the_ci_gates(self, config):
        hooks = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}

        for required in ("ruff-check", "black", "mypy"):
            assert required in hooks, f"{required} is not a pre-commit hook"

    def test_large_files_are_blocked(self, config):
        """A committed virtualenv is how REPO-01 happened."""
        hooks = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}

        assert "check-added-large-files" in hooks

    def test_hygiene_hooks_are_present(self, config):
        hooks = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}

        assert {"end-of-file-fixer", "trailing-whitespace"} <= hooks

    def test_every_repo_is_pinned_to_a_revision(self, config):
        for repo in config["repos"]:
            if repo["repo"] == "local":
                continue
            assert repo.get("rev"), f"{repo['repo']} is not pinned"

    def test_it_is_mentioned_in_the_readme(self):
        readme = (PROJECT_ROOT / "readme.md").read_text(encoding="utf-8")

        assert "pre-commit install" in readme

    def test_ruff_uses_the_current_hook_id(self, config):
        """``id: ruff`` is the legacy alias of ``ruff-check``."""
        hooks = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}

        assert "ruff" not in hooks

    def test_mypy_runs_the_ci_command_with_the_project_interpreter(self, config):
        (local,) = [repo for repo in config["repos"] if repo["repo"] == "local"]
        (mypy,) = [hook for hook in local["hooks"] if hook["id"] == "mypy"]

        assert mypy["language"] == "system"
        assert mypy["pass_filenames"] is False
        assert mypy["entry"] == "python -m mypy --ignore-missing-imports app.py data processing ui"

    @pytest.mark.parametrize(
        ("repo_url", "package"),
        [
            ("https://github.com/astral-sh/ruff-pre-commit", "ruff"),
            ("https://github.com/psf/black", "black"),
        ],
    )
    def test_hook_revisions_equal_the_dev_lock(self, config, repo_url, package):
        lock = (PROJECT_ROOT / "requirements-dev.lock").read_text(encoding="utf-8")
        match = re.search(rf"^{package}==([\w.]+)", lock, re.M)
        assert match, f"{package} is not in requirements-dev.lock"
        (repo,) = [repo for repo in config["repos"] if repo["repo"] == repo_url]

        assert repo["rev"].lstrip("v") == match.group(1)
