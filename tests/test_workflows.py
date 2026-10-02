"""CI, the nightly network run, dependabot and pytest's own config (TEST-03, -04, -07, -09)."""

import configparser
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
yaml = pytest.importorskip("yaml")


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 reads the bare key `on` as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) + str(step.get("uses", "")) for step in job["steps"])


@pytest.fixture(scope="module")
def ci() -> dict:
    return _load(WORKFLOWS / "ci.yml")


class TestPytestConfig:
    @staticmethod
    def _ini() -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read(PROJECT_ROOT / "pytest.ini", encoding="utf-8")
        return parser

    def test_markers_are_strict(self):
        assert "--strict-markers" in self._ini()["pytest"]["addopts"]

    def test_perf_budgets_are_deselected_by_default(self):
        assert '-m "not perf and not smoke"' in self._ini()["pytest"]["addopts"]

    def test_quiet_is_not_doubled(self):
        """A second -q hides the pass/fail summary line."""
        assert self._ini()["pytest"]["addopts"].split().count("-q") == 1

    def test_every_marker_in_use_is_registered(self):
        registered = {
            line.split(":")[0].strip()
            for line in self._ini()["pytest"]["markers"].splitlines()
            if line.strip()
        }
        builtin = {"parametrize", "skipif", "skip", "xfail", "usefixtures", "filterwarnings"}
        used = set()
        for path in (PROJECT_ROOT / "tests").rglob("*.py"):
            used |= set(re.findall(r"pytest\.mark\.([a-z_]+)", path.read_text(encoding="utf-8")))

        assert used - builtin <= registered, f"unregistered: {sorted(used - builtin - registered)}"


class TestContinuousIntegration:
    def test_perf_budgets_have_their_own_job(self, ci):
        assert "pytest -m perf" in _steps_text(ci["jobs"]["perf"])

    def test_the_server_smoke_test_runs_somewhere(self, ci):
        assert "pytest -m smoke" in _steps_text(ci["jobs"]["perf"])

    def test_the_main_run_keeps_the_default_selection(self, ci):
        text = _steps_text(ci["jobs"]["lint-and-test"])

        assert "--cov-fail-under=" in text
        assert "-m perf" not in text

    def test_windows_and_3_14_are_tested(self, ci):
        matrix = ci["jobs"]["lint-and-test"]["strategy"]["matrix"]
        legs = {(leg["os"], leg["python-version"]) for leg in matrix["include"]}
        legs |= {(os, py) for os in matrix["os"] for py in matrix["python-version"]}

        assert ("windows-latest", "3.14") in legs
        assert ("ubuntu-latest", "3.11") in legs

    def test_every_job_has_a_timeout(self, ci):
        for name, job in ci["jobs"].items():
            assert job.get("timeout-minutes"), f"{name} has no timeout"

    def test_permissions_are_read_only_by_default(self, ci):
        assert ci["permissions"] == {"contents": "read"}

    def test_the_player_javascript_is_tested(self, ci):
        text = _steps_text(ci["jobs"]["js"])

        assert "npm ci" in text
        assert "node --test" in text

    def test_no_env_value_relies_on_tilde_expansion(self, ci):
        """TEST-08: "~" is not expanded in an env value, so a literal
        ./~/.cache/fastf1 appeared in the checkout."""
        for job in ci["jobs"].values():
            for step in job["steps"]:
                for value in (step.get("env") or {}).values():
                    assert not str(value).startswith("~"), value


@pytest.fixture(scope="module")
def network() -> dict:
    return _load(WORKFLOWS / "network.yml")


class TestNetworkWorkflow:
    def test_it_runs_nightly_and_on_demand(self, network):
        assert network["on"]["schedule"]
        assert "workflow_dispatch" in network["on"]

    def test_it_opts_in_and_caches_fastf1(self, network):
        job = network["jobs"]["network"]
        (run,) = [step for step in job["steps"] if "pytest" in str(step.get("run", ""))]

        assert run["env"]["F1_NETWORK_TESTS"] == "1"
        assert "-m network" in run["run"]
        assert any("actions/cache" in str(step.get("uses", "")) for step in job["steps"])

    def test_a_failure_opens_an_issue(self, network):
        report = network["jobs"]["report"]

        assert "failure()" in report["if"]
        assert report["permissions"] == {"issues": "write"}
        assert "gh issue create" in _steps_text(report)


class TestDependabot:
    def test_it_watches_pip_actions_and_pre_commit(self):
        config = yaml.safe_load((PROJECT_ROOT / ".github" / "dependabot.yml").read_text("utf-8"))
        ecosystems = {update["package-ecosystem"] for update in config["updates"]}

        assert {"pip", "github-actions", "pre-commit"} <= ecosystems


@pytest.fixture(scope="module")
def release() -> dict:
    path = WORKFLOWS / "release.yml"
    assert path.is_file(), "the release workflow is missing (DIST-06)"
    return _load(path)


class TestReleaseWorkflow:
    """DIST-06: a v* tag builds, checks the version, smoke-installs on both
    platforms and publishes the Release."""

    def test_it_runs_on_version_tags_only(self, release):
        assert release["on"] == {"push": {"tags": ["v*"]}}

    def test_the_tag_is_checked_against_pyproject(self, release):
        text = _steps_text(release["jobs"]["build"])

        assert "pyproject.toml" in text
        assert "GITHUB_REF_NAME" in text
        assert "uv build" in text

    def test_the_wheel_is_smoke_installed_on_windows_and_ubuntu(self, release):
        smoke = release["jobs"]["smoke"]
        text = _steps_text(smoke)

        assert {"windows-latest", "ubuntu-latest"} <= set(smoke["strategy"]["matrix"]["os"])
        assert "uv tool install" in text
        assert "dist/*.whl" in text
        assert "f1dash --version" in text

    def test_the_release_waits_for_the_smoke_install(self, release):
        job = release["jobs"]["github-release"]
        text = _steps_text(job)

        assert "smoke" in job["needs"]
        assert job["permissions"] == {"contents": "write"}
        assert "gh release create" in text
        for asset in ("dist/*.whl", "dist/*.tar.gz", "install.ps1", "install.sh"):
            assert asset in text
        assert "--notes-file" in text

    def test_the_notes_come_from_the_changelog(self, release):
        assert "CHANGELOG.md" in _steps_text(release["jobs"]["build"])

    def test_pypi_is_opt_in_and_uses_trusted_publishing(self, release):
        job = release["jobs"]["pypi"]

        assert "vars.PUBLISH_PYPI" in job["if"]
        assert job["permissions"] == {"id-token": "write"}
        assert "uv publish" in _steps_text(job)

    def test_permissions_are_read_only_by_default(self, release):
        assert release["permissions"] == {"contents": "read"}

    def test_every_job_has_a_timeout(self, release):
        for name, job in release["jobs"].items():
            assert job.get("timeout-minutes"), f"{name} has no timeout"

    def test_actions_are_pinned_to_a_major(self, release):
        for job in release["jobs"].values():
            for step in job["steps"]:
                uses = step.get("uses")
                if uses:
                    assert re.fullmatch(r"[\w./-]+@v\d+", uses), uses
