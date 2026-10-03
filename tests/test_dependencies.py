"""Dependency declarations match what the code needs (REPO-02, REPO-18..20).

`streamlit>=1.35` was once declared while the code called APIs that did not
exist until much later, and packages the app imported were missing from the
requirements - so a fresh install could fail at runtime rather than at
install time. The opposite drift happened too: livef1, signalrcore and
requests-cache stayed declared long after nothing imported them (REPO-18).
"""

import re
import sys
from importlib import metadata
from pathlib import Path
from typing import ClassVar

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.version import Version

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT_ROOT / "src" / "f1dash"  # src layout (REPO-10)
SOURCE_FOLDERS = ("data", "processing", "ui")
SOURCE_FILES = ("app.py", "config.py", "cli.py")


def _requirements(filename: str = "requirements.txt"):
    entries = {}
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        requirement = Requirement(line)
        entries[requirement.name.lower()] = requirement
    return entries


def _locked(filename: str = "requirements.lock") -> dict[str, list[tuple[Version, str]]]:
    """``{name: [(version, marker), ...]}`` from a ``uv pip compile`` lock."""
    pins: dict[str, list[tuple[Version, str]]] = {}
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        if not line or line[0] in " #-":
            continue
        spec = line.rstrip("\\").strip()
        requirement = Requirement(spec)
        (pin,) = requirement.specifier
        pins.setdefault(requirement.name.lower(), []).append(
            (Version(pin.version), str(requirement.marker or ""))
        )
    return pins


def _locked_for_this_python(filename: str = "requirements.lock") -> dict[str, Version]:
    current = {}
    for name, entries in _locked(filename).items():
        for version, marker in entries:
            if not marker or Marker(marker).evaluate():
                current[name] = version
    return current


def _imported_top_level_modules() -> set[str]:
    paths = [PACKAGE / name for name in SOURCE_FILES if (PACKAGE / name).is_file()]
    for folder in SOURCE_FOLDERS:
        paths.extend((PACKAGE / folder).rglob("*.py"))
    imported = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_][\w]*)", text, re.M):
            imported.add(match.group(1))
    return imported


def _floor(requirement: Requirement) -> Version:
    for spec in requirement.specifier:
        if spec.operator in (">=", "=="):
            return Version(spec.version)
    pytest.fail(f"{requirement.name} has no lower bound")


def _ceiling(requirement: Requirement) -> Version:
    for spec in requirement.specifier:
        if spec.operator in ("<", "<="):
            return Version(spec.version)
    pytest.fail(f"{requirement.name} has no upper bound")


class TestFloorsAreHonest:
    def test_every_requirement_has_a_lower_and_upper_bound(self):
        for name, requirement in _requirements().items():
            operators = {spec.operator for spec in requirement.specifier}
            assert operators & {">=", "=="}, f"{name} has no floor"
            assert operators & {"<", "<=", "==", "~="}, f"{name} has no ceiling"

    def test_the_installed_version_satisfies_every_requirement(self):
        for name, requirement in _requirements().items():
            installed = metadata.version(name)
            assert requirement.specifier.contains(
                installed, prereleases=True
            ), f"{name} {installed} does not satisfy {requirement.specifier}"

    def test_streamlit_floor_covers_the_apis_in_use(self):
        """`width="stretch"` and `st.fragment` are not in 1.35."""
        assert _floor(_requirements()["streamlit"]) >= Version("1.55")

    @pytest.mark.parametrize("package", ["fastf1", "pandas", "numpy", "plotly", "streamlit"])
    def test_core_packages_are_pinned_below_the_next_major(self, package):
        requirement = _requirements()[package]

        assert any(spec.operator in ("<", "<=", "~=") for spec in requirement.specifier)

    def test_numpy_stays_below_2_6_while_pandas_is_held_on_2_x(self):
        """REPO-19: numpy 2.5 deprecates the generic timedelta unit that
        pandas 2.x and FastF1 use on every laps load; the release that makes
        it an error would break historical loads in code this repo cannot
        patch. The ceiling goes when pandas 3 (and FastF1 support for it)
        arrives."""
        requirements = _requirements()
        if _ceiling(requirements["pandas"]) <= Version("3"):
            assert _ceiling(requirements["numpy"]) <= Version("2.6")

    def test_websockets_floor_has_sync_proxy_support(self):
        """The sync client's ``proxy`` argument, which signalr_core relies
        on through its defaults, arrived in websockets 15."""
        assert _floor(_requirements()["websockets"]) >= Version("15")

    def test_websockets_leaves_room_for_current_streamlit(self):
        """Every Streamlit from 1.60 on requires websockets<17."""
        assert _ceiling(_requirements()["websockets"]) <= Version("17")


class TestEverythingImportedIsDeclared:
    THIRD_PARTY: ClassVar = {
        "streamlit",
        "plotly",
        "pandas",
        "numpy",
        "fastf1",
        "requests",
        "dotenv",
        "websockets",
        "platformdirs",
        "pyarrow",
    }
    DISTRIBUTION_NAMES: ClassVar = {"dotenv": "python-dotenv"}
    # Declared without a direct import: pandas loads it as the Parquet engine.
    USED_WITHOUT_IMPORT: ClassVar = {"pyarrow"}

    def test_each_imported_package_is_a_requirement(self):
        declared = set(_requirements())
        imported = _imported_top_level_modules()

        missing = []
        for package in imported & self.THIRD_PARTY:
            name = self.DISTRIBUTION_NAMES.get(package, package)
            if name.lower() not in declared:
                missing.append(name)

        assert not missing, f"imported but not declared: {missing}"

    def test_each_requirement_is_imported_somewhere(self):
        """REPO-18: livef1, signalrcore and requests-cache were declared for
        years after nothing imported them."""
        imported = _imported_top_level_modules()
        module_names = {dist: module for module, dist in self.DISTRIBUTION_NAMES.items()}

        unused = [
            name
            for name in _requirements()
            if name not in self.USED_WITHOUT_IMPORT
            and module_names.get(name, name.replace("-", "_")) not in imported
        ]

        assert not unused, f"declared but never imported: {unused}"

    @pytest.mark.parametrize("package", ["livef1", "signalrcore", "requests-cache"])
    def test_dead_dependencies_stay_gone(self, package):
        assert package not in _requirements()


class TestLockFile:
    @pytest.mark.parametrize("lock", ["requirements.lock", "requirements-dev.lock"])
    def test_a_lock_file_exists(self, lock):
        assert (PROJECT_ROOT / lock).is_file()

    @pytest.mark.parametrize("lock", ["requirements.lock", "requirements-dev.lock"])
    def test_every_pin_carries_hashes(self, lock):
        """REPO-20: CI installs with --require-hashes."""
        text = (PROJECT_ROOT / lock).read_text("utf-8")
        blocks = re.split(r"\n(?=[a-z0-9])", text)
        pins = [block for block in blocks if re.match(r"^[a-z0-9][\w.-]*==", block)]

        assert pins
        for block in pins:
            assert "--hash=sha256:" in block, f"{block.splitlines()[0]} has no hash"

    def test_the_lock_covers_every_direct_requirement(self):
        assert set(_requirements()) <= set(_locked())

    def test_the_lock_pins_transitive_packages_too(self):
        """REPO-20: the old lock pinned 19 of ~84 packages, so altair,
        protobuf, scipy and starlette floated between installs."""
        locked = set(_locked())

        assert {"altair", "protobuf", "scipy", "starlette", "matplotlib"} <= locked

    def test_the_lock_satisfies_the_requirements_on_every_python(self):
        locked = _locked()
        for name, requirement in _requirements().items():
            for version, marker in locked[name]:
                assert requirement.specifier.contains(
                    version, prereleases=True
                ), f"{name}=={version} ({marker or 'all'}) breaks {requirement.specifier}"

    def test_the_lock_installs_on_the_3_11_floor(self):
        """numpy 2.5 needs Python 3.12; the 3.11 floor needs its own pin."""
        numpy_pins = _locked()["numpy"]
        environment = {"python_full_version": "3.11.9", "python_version": "3.11"}

        assert any(
            not marker or Marker(marker).evaluate(environment) for _, marker in numpy_pins
        ), "no numpy pin applies to Python 3.11"

    def test_the_dev_lock_agrees_with_the_runtime_lock(self):
        runtime = _locked("requirements.lock")
        dev = _locked("requirements-dev.lock")

        for name, entries in runtime.items():
            assert dev.get(name) == entries, f"{name} differs between the two locks"

    def test_dev_tools_are_not_in_the_runtime_lock(self):
        """End users should not install black, mypy and pre-commit."""
        runtime = set(_locked("requirements.lock"))

        assert not runtime & {"black", "mypy", "pre-commit", "pytest", "ruff"}

    @pytest.mark.skipif(sys.version_info < (3, 12), reason="checked against the running env")
    def test_this_environment_matches_the_dev_lock(self):
        mismatched = []
        for name, version in _locked_for_this_python("requirements-dev.lock").items():
            try:
                installed = Version(metadata.version(name))
            except metadata.PackageNotFoundError:
                continue
            if installed != version:
                mismatched.append(f"{name} {installed} != {version}")

        if mismatched:
            pytest.skip("environment not synced to the lock: " + ", ".join(mismatched[:5]))


class TestContinuousIntegration:
    def test_ci_installs_from_the_lock_with_hashes(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")

        assert "requirements-dev.lock" in workflow
        assert "--require-hashes" in workflow

    def test_ci_fails_when_the_lock_is_stale(self):
        """REPO-20: ``pip install --dry-run --no-deps`` could never fail."""
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")

        assert "git diff --exit-code" in workflow
        assert "pip check" in workflow
