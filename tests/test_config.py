"""Version and file locations (REPO-23, DIST-03, CACHE-04)."""

import tomllib
from importlib import metadata
from pathlib import Path

import pytest

import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


class TestVersion:
    def test_the_version_is_importable_from_config(self):
        from config import __version__

        assert __version__ == _pyproject_version()

    def test_pyproject_declares_the_project(self):
        project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))["project"]

        assert project["name"] == "f1dash"
        assert project["requires-python"] == ">=3.11"

    def test_an_install_reads_its_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config.metadata, "version", lambda name: "1.2.3")

        assert config.read_version(tmp_path) == "1.2.3"

    def test_a_checkout_prefers_pyproject_over_stale_metadata(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n', "utf-8")
        monkeypatch.setattr(config.metadata, "version", lambda name: "1.0.0")

        assert config.read_version(tmp_path) == "2.0.0"

    def test_without_metadata_it_falls_back_to_pyproject(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "3.1.0"\n', "utf-8")

        def missing(name):
            raise metadata.PackageNotFoundError(name)

        monkeypatch.setattr(config.metadata, "version", missing)

        assert config.read_version(tmp_path) == "3.1.0"

    def test_with_neither_it_says_unknown(self, tmp_path, monkeypatch):
        def missing(name):
            raise metadata.PackageNotFoundError(name)

        monkeypatch.setattr(config.metadata, "version", missing)

        assert config.read_version(tmp_path) == config.UNKNOWN_VERSION


class TestCheckoutDetection:
    def test_this_repository_is_a_checkout(self):
        if not (PROJECT_ROOT / ".git").exists():
            pytest.skip("not running from a git checkout")
        assert config.is_checkout()

    def test_a_folder_without_git_is_an_install(self, tmp_path):
        assert not config.is_checkout(tmp_path)

    def test_a_worktree_git_file_counts(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: elsewhere\n", "utf-8")

        assert config.is_checkout(tmp_path)


class TestPaths:
    def test_a_checkout_keeps_the_repo_local_paths(self, tmp_path):
        paths = config.resolve_paths(environ={}, checkout=True, root=tmp_path)

        assert paths == {
            "fastf1_cache_dir": str(tmp_path / "ff1_cache"),
            "replay_dir": str(tmp_path / "replay_sessions"),
            "metrics_store_path": str(tmp_path / "metrics_store.sqlite"),
            "env_path": str(tmp_path / ".env"),
        }

    def test_an_install_uses_the_user_directories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config.platformdirs, "user_cache_dir", lambda *a, **k: "/c/f1dash")
        monkeypatch.setattr(config.platformdirs, "user_data_dir", lambda *a, **k: "/d/f1dash")
        monkeypatch.setattr(config.platformdirs, "user_config_dir", lambda *a, **k: "/e/f1dash")

        paths = config.resolve_paths(environ={}, checkout=False, root=tmp_path)

        assert paths == {
            "fastf1_cache_dir": str(Path("/c/f1dash") / "fastf1"),
            "replay_dir": str(Path("/d/f1dash") / "replays"),
            "metrics_store_path": str(Path("/d/f1dash") / "metrics_store.sqlite"),
            "env_path": str(Path("/e/f1dash") / ".env"),
        }

    def test_an_install_writes_nothing_inside_the_code_folder(self, tmp_path):
        paths = config.resolve_paths(environ={}, checkout=False, root=tmp_path)

        for path in paths.values():
            assert tmp_path not in Path(path).parents

    @pytest.mark.parametrize("checkout", [True, False])
    def test_environment_variables_override_either(self, tmp_path, checkout):
        environ = {
            "FASTF1_CACHE_DIR": str(tmp_path / "cache"),
            "REPLAY_DIR": str(tmp_path / "replays"),
            "F1_METRICS_STORE": str(tmp_path / "records.sqlite"),
        }

        paths = config.resolve_paths(environ=environ, checkout=checkout, root=tmp_path)

        assert paths["fastf1_cache_dir"] == str(tmp_path / "cache")
        assert paths["replay_dir"] == str(tmp_path / "replays")
        assert paths["metrics_store_path"] == str(tmp_path / "records.sqlite")

    def test_an_empty_override_is_ignored(self, tmp_path):
        paths = config.resolve_paths(environ={"REPLAY_DIR": "  "}, checkout=True, root=tmp_path)

        assert paths["replay_dir"] == str(tmp_path / "replay_sessions")

    def test_the_config_object_carries_all_four(self):
        for name in ("fastf1_cache_dir", "replay_dir", "metrics_store_path", "env_path"):
            assert isinstance(getattr(config.config, name), str)


class TestEnvFile:
    def test_values_come_from_the_env_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("F1DASH_TEST_VALUE", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("F1DASH_TEST_VALUE=from-file\n", "utf-8")

        assert config.load_env_file(env_file)
        import os

        assert os.environ["F1DASH_TEST_VALUE"] == "from-file"
        monkeypatch.delenv("F1DASH_TEST_VALUE")

    def test_the_environment_wins_over_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("F1DASH_TEST_VALUE", "from-env")
        env_file = tmp_path / ".env"
        env_file.write_text("F1DASH_TEST_VALUE=from-file\n", "utf-8")

        config.load_env_file(env_file)
        import os

        assert os.environ["F1DASH_TEST_VALUE"] == "from-env"

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert config.load_env_file(tmp_path / "absent.env") is False

    def test_defaults_follow_the_environment(self):
        loaded = config.Config.from_env(
            {"DEFAULT_YEAR": "2025", "DEFAULT_GP": "Monza", "DEFAULT_SESSION": "Q"}
        )

        assert (loaded.default_year, loaded.default_gp, loaded.default_session) == (
            2025,
            "Monza",
            "Q",
        )
