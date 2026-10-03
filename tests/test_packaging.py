"""The project installs as the f1dash package (DIST-01)."""

import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirements_txt() -> set[str]:
    entries = set()
    for line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            entries.add(str(Requirement(line)))
    return entries


class TestMetadata:
    def test_dependencies_equal_requirements_txt(self):
        """One source of truth: the locks compile requirements.txt, the wheel
        installs pyproject's list; they must never disagree."""
        declared = {str(Requirement(entry)) for entry in _pyproject()["project"]["dependencies"]}

        assert declared == _requirements_txt()

    def test_the_command_is_declared(self):
        assert _pyproject()["project"]["scripts"] == {"f1dash": "f1dash.cli:main"}

    def test_hatchling_builds_it(self):
        assert _pyproject()["build-system"]["build-backend"] == "hatchling.build"

    def test_the_licence_files_ship(self):
        files = _pyproject()["project"]["license-files"]

        assert {"LICENSE", "NOTICE"} <= set(files)
        for name in files:
            assert (PROJECT_ROOT / name).is_file()


PACKAGE = PROJECT_ROOT / "src" / "f1dash"

# src layout (REPO-10): everything ships inside the one f1dash package.
EXPECTED_IN_WHEEL = [
    "f1dash/__init__.py",
    "f1dash/__main__.py",
    "f1dash/app.py",
    "f1dash/config.py",
    "f1dash/cli.py",
    "f1dash/data/__init__.py",
    "f1dash/data/source_manager.py",
    "f1dash/data/update_check.py",
    "f1dash/processing/__init__.py",
    "f1dash/processing/replay_payload.py",
    "f1dash/ui/__init__.py",
    "f1dash/ui/layout.py",
    "f1dash/ui/components/replay_player/player.js",
    "f1dash/ui/components/replay_player/player.css",
    "f1dash/ui/components/replay_player/player.html",
    "f1dash/ui/assets/fonts/OFL.txt",
    "f1dash/.streamlit/config.toml",
]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> list[str]:
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    out = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), str(PROJECT_ROOT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        if "network" in result.stderr.lower() or "connect" in result.stderr.lower():
            pytest.skip("uv build needs to fetch hatchling and is offline")
        pytest.fail(result.stderr)
    (path,) = out.glob("f1dash-*.whl")
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


class TestWheel:
    @pytest.mark.parametrize("name", EXPECTED_IN_WHEEL)
    def test_it_contains(self, wheel, name):
        assert name in wheel

    def test_every_source_module_ships(self, wheel):
        for path in PACKAGE.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            assert path.relative_to(PACKAGE.parent).as_posix() in wheel

    def test_only_the_f1dash_package_is_top_level(self, wheel):
        """No flat ``data`` / ``ui`` / ``config`` modules shadowing other
        installs' names (REPO-10): just the package and its metadata."""
        tops = {name.split("/")[0] for name in wheel}

        assert {top for top in tops if not top.endswith(".dist-info")} == {"f1dash"}

    def test_the_fonts_ship(self, wheel):
        assert any(name.endswith(".woff2") for name in wheel)

    def test_no_tests_caches_or_secrets_ship(self, wheel):
        for name in wheel:
            assert not name.startswith(("tests/", "scripts/", "replay_sessions/", "ff1_cache/"))
            assert "__pycache__" not in name
            assert not name.endswith((".pyc", ".env", "secrets.toml"))

    def test_the_entry_point_is_registered(self, wheel):
        assert any(name.endswith(".dist-info/entry_points.txt") for name in wheel)
