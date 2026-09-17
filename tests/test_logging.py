"""Degraded paths say why (REPO-11).

A dozen `except Exception: return pd.DataFrame()` handlers turned every
upstream failure into an empty panel with no explanation - in the app or in
the logs - so "No data" could mean a network error, a FastF1 change or a
genuinely empty session.
"""

import logging
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("data", "processing", "ui")


class TestLoggersExist:
    def test_modules_that_swallow_exceptions_have_a_logger(self):
        missing = []
        for package in PACKAGES:
            for path in (PROJECT_ROOT / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "except Exception" in text and "getLogger(__name__)" not in text:
                    missing.append(str(path.relative_to(PROJECT_ROOT)))

        assert not missing, f"swallow exceptions with no logger: {missing}"

    def test_no_handler_silently_discards_its_exception(self):
        """Every `except Exception` must log, re-raise, or say why not."""
        offenders = []
        for package in PACKAGES:
            for path in (PROJECT_ROOT / package).rglob("*.py"):
                lines = path.read_text(encoding="utf-8").splitlines()
                for index, line in enumerate(lines):
                    if "except Exception" not in line:
                        continue
                    # A handler may explain itself in a comment first.
                    body = " ".join(lines[index + 1 : index + 7])
                    if not any(
                        marker in body
                        for marker in ("logger.", "log.", "raise", "noqa", "# deliberate")
                    ):
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{index + 1}")

        assert not offenders, f"silent handlers: {offenders}"


class TestDegradedPathsLog:
    def test_a_failed_weather_load_is_logged(self, caplog):
        from data.source_manager import DataSourceManager

        class Boom:
            @property
            def weather_data(self):
                raise RuntimeError("upstream is down")

        with caplog.at_level(logging.WARNING):
            frame = DataSourceManager._get_weather_from_session(None, Boom())

        assert frame.empty
        assert any("upstream is down" in record.message for record in caplog.records)

    def test_a_failed_results_load_is_logged(self, caplog):
        from data.fastf1_adapter import FastF1Adapter

        class Boom:
            @property
            def results(self):
                raise RuntimeError("no results yet")

        with caplog.at_level(logging.WARNING):
            frame = FastF1Adapter.get_results(None, Boom())

        assert frame.empty
        assert any("no results yet" in record.message for record in caplog.records)

    def test_the_log_level_follows_the_environment(self, monkeypatch):
        import importlib

        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        import config

        importlib.reload(config)

        assert config.config.log_level == "DEBUG"


class TestFrameShapeIsStillHonoured:
    def test_degraded_calls_still_return_a_frame(self):
        from data.source_manager import DataSourceManager

        class Boom:
            @property
            def weather_data(self):
                raise RuntimeError("down")

        assert isinstance(DataSourceManager._get_weather_from_session(None, Boom()), pd.DataFrame)


class TestAppConfiguresLogging:
    def test_app_sets_up_logging_from_config(self):
        source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

        assert "logging.basicConfig" in source
        assert "config.log_level" in source

    def test_the_default_level_is_quiet(self, monkeypatch):
        import importlib

        monkeypatch.delenv("LOG_LEVEL", raising=False)
        import config

        importlib.reload(config)

        assert config.config.log_level == "WARNING"
