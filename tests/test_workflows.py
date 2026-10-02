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
    return _load(WORKFLOWS / "release.yml")


class TestReleaseWorkflow:
    """DIST-06: a v* tag builds, checks the version, smoke-installs, releases."""

    def test_runs_on_version_tags(self, release):
        assert release["on"]["push"]["tags"] == ["v*"]

    def test_the_tag_must_match_the_version(self, release):
        text = _steps_text(release["jobs"]["build"])
        assert "uv build" in text
        assert 'GITHUB_REF_NAME}" != "v' in text

    def test_smoke_installs_on_windows_and_linux(self, release):
        smoke = release["jobs"]["smoke"]
        assert set(smoke["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
        assert "f1dash --version" in _steps_text(smoke)

    def test_the_release_carries_the_installers(self, release):
        text = _steps_text(release["jobs"]["release"])
        assert "install.ps1" in text and "install.sh" in text
        assert release["jobs"]["release"]["permissions"] == {"contents": "write"}

    def test_pypi_is_opt_in(self, release):
        assert "PUBLISH_TO_PYPI" in release["jobs"]["pypi"]["if"]
