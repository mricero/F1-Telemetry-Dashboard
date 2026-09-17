"""Nothing subscribed or exported that nothing reads (IMPROVEMENTS.md LIVE-16).

`start_fastf1_client` ignored its topics and blocked, `check_live_session_available`
and `process_live_telemetry` had no callers (the latter inventing an
index-based pseudo-distance), and three topics were subscribed whose messages
were never looked at.
"""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("data", "processing")

# Modules LIVE-16 covers. The unused Jolpica `*_df` helpers belong to REPO-05,
# which decides whether to wire them up (FEAT-06 standings) or delete them.
LIVE_MODULES = (
    "data/live_adapter.py",
    "data/live_state.py",
    "data/live_recorder.py",
    "data/live_service.py",
    "processing/telemetry_processor.py",
)


def _public_functions(path: Path):
    """Top-level and class-level public defs in one module.

    Functions nested inside other functions are callbacks and locals, not
    API, so they are not counted.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    containers = [tree] + [n for n in tree.body if isinstance(n, ast.ClassDef)]
    for container in containers:
        for node in container.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                node.name.startswith("_")
            ):
                yield node.name


def _sources(include_tests: bool = True) -> str:
    folders = [*PACKAGES, "ui", "scripts"]
    if include_tests:
        folders.append("tests")
    parts = []
    for folder in folders:
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            parts.append(path.read_text(encoding="utf-8"))
    parts.append((PROJECT_ROOT / "app.py").read_text(encoding="utf-8"))
    return chr(10).join(parts)


class TestNoUnusedPublicFunctions:
    def test_every_public_function_is_referenced(self):
        corpus = _sources()
        orphans = []
        for module in LIVE_MODULES:
            path = PROJECT_ROOT / module
            for name in _public_functions(path):
                # One definition plus at least one use.
                if corpus.count(name) <= 1:
                    orphans.append(f"{module}::{name}")

        assert not orphans, f"unused public functions: {orphans}"

    @pytest.mark.parametrize(
        "name",
        ["start_fastf1_client", "check_live_session_available", "process_live_telemetry"],
    )
    def test_the_named_dead_functions_are_gone(self, name):
        # Tests are excluded: this very file names them.
        corpus = _sources(include_tests=False)

        assert name not in corpus, f"{name} was supposed to be removed"

    def test_no_demo_main_blocks_in_library_modules(self):
        offenders = []
        for package in PACKAGES:
            for path in (PROJECT_ROOT / package).rglob("*.py"):
                if "__main__" in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))

        assert not offenders, f"library modules with demo blocks: {offenders}"


class TestEverySubscribedTopicIsRead:
    def test_no_topic_is_subscribed_that_nothing_reads(self):
        from data.live_adapter import SignalRLiveAdapter
        from data.live_state import STATE_TOPICS

        corpus = _sources()
        unread = []
        for topic in SignalRLiveAdapter.TELEMETRY_TOPICS:
            if topic in STATE_TOPICS:
                continue  # merged into state, readable by any consumer
            # A series topic must be named by something that reads buffers.
            if corpus.count(f'"{topic}"') <= 1:
                unread.append(topic)

        assert not unread, f"subscribed but never read: {unread}"

    def test_the_tower_gets_its_tyre_source(self):
        from data.live_adapter import SignalRLiveAdapter

        assert "TimingAppData" in SignalRLiveAdapter.TELEMETRY_TOPICS

    def test_lap_series_is_not_subscribed(self):
        from data.live_adapter import SignalRLiveAdapter

        # Lap progression comes from TimingData.NumberOfLaps; LapSeries only
        # duplicated it and nothing parsed it.
        assert "LapSeries" not in SignalRLiveAdapter.TELEMETRY_TOPICS
