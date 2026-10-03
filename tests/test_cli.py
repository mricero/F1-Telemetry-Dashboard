"""The f1dash command (DIST-02, DIST-05)."""

import io
import os
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from f1dash import cli as f1dash_cli

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT_ROOT / "src" / "f1dash"  # src layout (REPO-10)


@pytest.fixture
def streamlit_calls(monkeypatch):
    """Capture what would be handed to ``streamlit run``."""
    from streamlit.web import cli as streamlit_cli

    calls = []

    def fake_main(args, **kwargs):
        calls.append((list(args), kwargs))
        return 0

    monkeypatch.setattr(streamlit_cli, "main", fake_main)
    return calls


def _expected_theme_flags() -> list[str]:
    data = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text("utf-8"))
    flags = []
    for section, values in data.items():
        for key, value in values.items():
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            flags.append(f"--{section}.{key}={text}")
    return flags


class TestRun:
    def test_it_runs_the_installed_app_with_every_config_key(self, streamlit_calls):
        assert f1dash_cli.main(["--port", "8599"]) == 0

        ((argv, kwargs),) = streamlit_calls
        assert argv[:2] == ["run", str(PACKAGE / "app.py")]
        for flag in _expected_theme_flags():
            assert flag in argv
        assert argv[-2:] == ["--server.port", "8599"]
        assert kwargs["prog_name"] == "streamlit"

    def test_a_checkout_reads_the_root_streamlit_config(self):
        """No bundled copy in src/ (only the wheel has one), so the checkout's
        own .streamlit/config.toml is the one passed on."""
        assert f1dash_cli.STREAMLIT_CONFIG == PROJECT_ROOT / ".streamlit" / "config.toml"

    def test_usage_statistics_stay_off_when_installed(self, streamlit_calls):
        f1dash_cli.main(["--port", "8599"])

        ((argv, _),) = streamlit_calls
        assert "--browser.gatherUsageStats=false" in argv

    def test_no_browser_runs_headless(self, streamlit_calls):
        f1dash_cli.main(["--no-browser", "--port", "8599"])

        ((argv, _),) = streamlit_calls
        assert "--server.headless=true" in argv

    def test_the_browser_opens_by_default(self, streamlit_calls):
        f1dash_cli.main(["--port", "8599"])

        ((argv, _),) = streamlit_calls
        assert "--server.headless=true" not in argv

    def test_the_default_port_falls_back_to_the_next_free_one(self):
        taken = {8501, 8502}

        assert f1dash_cli.choose_port(None, is_free=lambda port: port not in taken) == 8503

    def test_a_requested_port_is_used_as_given(self):
        assert f1dash_cli.choose_port(9000, is_free=lambda port: False) == 9000

    def test_the_default_port_is_8501(self):
        assert f1dash_cli.choose_port(None, is_free=lambda port: True) == 8501

    def test_a_missing_config_gives_no_flags(self, tmp_path):
        assert f1dash_cli.streamlit_flags(tmp_path / "absent.toml") == []


class TestVersion:
    def test_version_prints_the_package_version(self, capsys):
        from f1dash.config import __version__

        with pytest.raises(SystemExit) as exit_info:
            f1dash_cli.main(["--version"])

        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip() == f"f1dash {__version__}"


class TestPaths:
    def test_paths_prints_the_four_locations(self, monkeypatch, capsys, tmp_path):
        from f1dash import config

        for attribute in ("fastf1_cache_dir", "replay_dir", "metrics_store_path", "env_path"):
            monkeypatch.setattr(config.config, attribute, str(tmp_path / attribute))

        assert f1dash_cli.main(["paths"]) == 0

        out = capsys.readouterr().out
        for label in ("FastF1 cache", "Replays", "Records", ".env"):
            assert label in out
        for attribute in ("fastf1_cache_dir", "replay_dir", "metrics_store_path", "env_path"):
            assert str(tmp_path / attribute) in out

    def test_paths_does_not_start_streamlit(self, streamlit_calls):
        f1dash_cli.print_paths(out=io.StringIO())

        assert streamlit_calls == []


class TestUpdate:
    @staticmethod
    def _runner(calls):
        def run(argv, check):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        return run

    def test_a_git_install_reinstalls_the_latest_tag(self, monkeypatch):
        from f1dash.data import update_check

        monkeypatch.setattr(update_check, "installed_from_registry", lambda: False)
        monkeypatch.setattr(update_check, "latest_release_tag", lambda: "v0.10.0")
        calls = []

        code = f1dash_cli.run_update(run=self._runner(calls), which=lambda name: "/bin/uv")

        assert code == 0
        assert calls == [
            [
                "uv",
                "tool",
                "install",
                "--reinstall",
                "git+https://github.com/mricero/F1-Telemetry-Dashboard@v0.10.0",
            ]
        ]

    def test_a_registry_install_upgrades(self, monkeypatch):
        from f1dash.data import update_check

        monkeypatch.setattr(update_check, "installed_from_registry", lambda: True)
        calls = []

        f1dash_cli.run_update(run=self._runner(calls), which=lambda name: "/bin/uv")

        assert calls == [["uv", "tool", "upgrade", "f1dash"]]

    def test_without_a_release_it_installs_main(self, monkeypatch):
        from f1dash.data import update_check

        monkeypatch.setattr(update_check, "installed_from_registry", lambda: False)
        monkeypatch.setattr(update_check, "latest_release_tag", lambda: None)
        calls = []

        f1dash_cli.run_update(run=self._runner(calls), which=lambda name: "/bin/uv")

        assert calls[0][-1].endswith("@main")

    def test_without_uv_it_explains(self, capsys):
        calls = []

        code = f1dash_cli.run_update(run=self._runner(calls), which=lambda name: None)

        assert code == 1
        assert calls == []
        assert "uv" in capsys.readouterr().err

    def test_the_update_subcommand_is_wired(self, monkeypatch):
        monkeypatch.setattr(f1dash_cli, "run_update", lambda: 7)

        assert f1dash_cli.main(["update"]) == 7


def _get(url: str) -> int:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status


@pytest.mark.smoke
def test_f1dash_serves_the_app_from_a_temp_directory(tmp_path):
    """DIST-02 acceptance: ``f1dash --no-browser --port 8599`` answers 200 on /."""
    port = 8599
    # python -m f1dash from the checkout's src/, without an install (REPO-10).
    pythonpath = [str(PROJECT_ROOT / "src"), os.environ.get("PYTHONPATH", "")]
    env = {
        **os.environ,
        "F1_UPDATE_CHECK": "0",
        "PYTHONPATH": os.pathsep.join(filter(None, pythonpath)),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "f1dash", "--no-browser", "--port", str(port)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        status = None
        while time.monotonic() < deadline and status is None:
            try:
                status = _get(f"http://localhost:{port}/")
            except OSError:
                time.sleep(0.5)
        assert status == 200
        assert not any(tmp_path.iterdir()), "f1dash wrote into the working directory"
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
