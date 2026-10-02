"""Shared test fixtures.

The live ingest adapter is a process-wide singleton (LIVE-09), so without a
reset between tests one test's primed buffers would leak into the next.

Every test also gets its own cache, replay folder and records store under
``tmp_path`` (TEST-08): with the defaults, ``FastF1Adapter()`` enabled a real
``./ff1_cache`` HTTP cache and tests left files in the working tree.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_live_service():
    from data.live_service import reset_live_service

    reset_live_service()
    yield
    reset_live_service()


@pytest.fixture(autouse=True)
def _isolated_user_files(tmp_path_factory, monkeypatch):
    """Point every file location at a per-test temporary folder.

    ``config`` resolves its paths once, at import, so setting the variables
    is not enough on its own: the already-built ``config.config`` object is
    patched too. Code that reloads ``config`` picks the variables up.
    """
    import os

    import config

    root = tmp_path_factory.mktemp("user-files")
    paths = {
        "FASTF1_CACHE_DIR": ("fastf1_cache_dir", str(root / "fastf1")),
        "REPLAY_DIR": ("replay_dir", str(root / "replays")),
        "F1_METRICS_STORE": ("metrics_store_path", str(root / "metrics_store.sqlite")),
    }
    # Some UI test modules ask for an in-memory records store; keep that.
    if os.environ.get("F1_METRICS_STORE") == ":memory:":
        paths["F1_METRICS_STORE"] = ("metrics_store_path", ":memory:")
    for variable, (attribute, value) in paths.items():
        monkeypatch.setenv(variable, value)
        monkeypatch.setattr(config.config, attribute, value, raising=False)
    # An installed copy would check GitHub for a newer release; tests never do.
    monkeypatch.setenv("F1_UPDATE_CHECK", "0")
    yield root
