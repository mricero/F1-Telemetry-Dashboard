"""Dependency floors match what the code actually needs (REPO-02).

`streamlit>=1.35` was declared while the code calls APIs that did not exist
until much later, and the packages the app imports were missing from the
requirements entirely - so a fresh install could fail at runtime rather than
at install time.
"""

import re
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _requirements(filename: str = "requirements.txt"):
    entries = {}
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        requirement = Requirement(line)
        entries[requirement.name.lower()] = requirement
    return entries


def _floor(requirement: Requirement) -> Version:
    for spec in requirement.specifier:
        if spec.operator in (">=", "=="):
            return Version(spec.version)
    pytest.fail(f"{requirement.name} has no lower bound")


class TestFloorsAreHonest:
    def test_every_requirement_has_a_lower_and_upper_bound(self):
        for name, requirement in _requirements().items():
            operators = {spec.operator for spec in requirement.specifier}
            assert operators & {">=", "=="}, f"{name} has no floor"
            assert operators & {"<", "<=", "==", "~="}, f"{name} has no ceiling"

    def test_the_installed_version_satisfies_every_floor(self):
        for name, requirement in _requirements().items():
            installed = Version(metadata.version(name))
            assert installed >= _floor(requirement), f"{name} floor is above what is installed"

    def test_streamlit_floor_covers_the_apis_in_use(self):
        """`width="stretch"` and `st.fragment` are not in 1.35."""
        assert _floor(_requirements()["streamlit"]) >= Version("1.55")

    @pytest.mark.parametrize("package", ["fastf1", "livef1", "pandas", "numpy", "plotly"])
    def test_core_packages_are_pinned_below_the_next_major(self, package):
        requirement = _requirements()[package]

        assert any(spec.operator in ("<", "<=", "~=") for spec in requirement.specifier)


class TestEverythingImportedIsDeclared:
    THIRD_PARTY = {
        "streamlit",
        "plotly",
        "pandas",
        "numpy",
        "fastf1",
        "livef1",
        "requests",
        "dotenv",
    }
    DISTRIBUTION_NAMES = {"dotenv": "python-dotenv"}

    def test_each_imported_package_is_a_requirement(self):
        declared = set(_requirements())
        imported = set()
        for folder in ("data", "processing", "ui"):
            for path in (PROJECT_ROOT / folder).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for match in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_][\w]*)", text, re.M):
                    imported.add(match.group(1))

        missing = []
        for package in imported & self.THIRD_PARTY:
            name = self.DISTRIBUTION_NAMES.get(package, package)
            if name.lower() not in declared:
                missing.append(name)

        assert not missing, f"imported but not declared: {missing}"


class TestLockFile:
    def test_a_lock_file_exists(self):
        assert (PROJECT_ROOT / "requirements.lock").is_file()

    def test_the_lock_pins_exact_versions(self):
        lines = [
            line.strip()
            for line in (PROJECT_ROOT / "requirements.lock").read_text("utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

        assert lines, "the lock file is empty"
        for line in lines:
            assert "==" in line, f"{line} is not pinned"

    def test_the_lock_covers_every_direct_requirement(self):
        locked = {
            Requirement(line.split("#")[0].strip()).name.lower()
            for line in (PROJECT_ROOT / "requirements.lock").read_text("utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

        assert set(_requirements()) <= locked


class TestContinuousIntegration:
    def test_ci_installs_from_the_lock(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")

        assert "requirements.lock" in workflow
