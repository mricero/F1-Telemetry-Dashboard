"""The pre-commit config runs the gates CI runs (REPO-07).

`pre-commit` was in requirements-dev.txt with no config for it to run, so
the hooks everyone installed did nothing.
"""

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

        for required in ("ruff", "black", "mypy"):
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
            assert repo.get("rev"), f"{repo['repo']} is not pinned"

    def test_it_is_mentioned_in_the_readme(self):
        readme = (PROJECT_ROOT / "readme.md").read_text(encoding="utf-8")

        assert "pre-commit install" in readme
