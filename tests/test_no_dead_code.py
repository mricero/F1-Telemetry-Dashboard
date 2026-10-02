"""Nothing subscribed or exported that nothing reads (IMPROVEMENTS.md LIVE-16).

`start_fastf1_client` ignored its topics and blocked, `check_live_session_available`
and `process_live_telemetry` had no callers (the latter inventing an
index-based pseudo-distance), and three topics were subscribed whose messages
were never looked at.
"""

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("data", "processing")

# REPO-05 removed the orphans, so the rule now covers both packages.
#
# Allow-list, with the reason each entry is kept. Anything not listed here and
# not referenced anywhere is dead code and fails the test.
KEPT_WITHOUT_CALLERS = {
    # Jolpica is an Ergast-compatible REST adapter: these are its query
    # surface, paged and rate-limited by HIST-07. The standings queries have
    # callers since FEAT-06; the rest stay until a feature reads them.
    # Deleting them would mean rewriting the same requests against the same
    # endpoints.
    "data/jolpica_adapter.py": {
        "get_session_results",
        "get_qualifying_results",
        "get_practice_results",
        "get_driver_info",
        "get_constructor_info",
    },
}


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
    """Every source file, as one string, for cheap reference counting.

    This module is always excluded: it names the very functions it checks.
    """
    folders = [*PACKAGES, "ui", "scripts"]
    if include_tests:
        folders.append("tests")
    parts = []
    for folder in folders:
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            parts.append(path.read_text(encoding="utf-8"))
    parts.append((PROJECT_ROOT / "app.py").read_text(encoding="utf-8"))
    return chr(10).join(parts)


class TestNoUnusedPublicFunctions:
    def test_every_public_function_is_referenced(self):
        corpus = _sources()
        orphans = []
        for package in PACKAGES:
            for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
                relative = path.relative_to(PROJECT_ROOT).as_posix()
                allowed = KEPT_WITHOUT_CALLERS.get(relative, set())
                for name in _public_functions(path):
                    # One definition plus at least one use.
                    if corpus.count(name) <= 1 and name not in allowed:
                        orphans.append(f"{relative}::{name}")

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


class TestUnusedConfiguration:
    """REPO-05: config fields nothing reads are a promise the app does not keep."""

    def test_every_config_field_is_read(self):
        import config as config_module

        corpus = _sources()
        unread = [
            field
            for field in config_module.Config.__dataclass_fields__
            if f"config.{field}" not in corpus
        ]

        assert not unread, f"config fields nothing reads: {unread}"

    def test_the_app_does_not_patch_sys_path(self):
        """streamlit run puts the script's directory on sys.path itself."""
        source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

        assert "sys.path.insert" not in source


class TestTheAllowListStaysHonest:
    """An allow-list is only useful while every entry is still true."""

    def test_every_allowed_name_still_exists(self):
        for module, names in KEPT_WITHOUT_CALLERS.items():
            defined = set(_public_functions(PROJECT_ROOT / module))
            stale = names - defined
            assert not stale, f"{module}: allow-listed but gone: {stale}"

    def test_nothing_allow_listed_has_quietly_gained_callers(self):
        """Once something is used, it should leave the list."""
        corpus = _sources()
        for module, names in KEPT_WITHOUT_CALLERS.items():
            # Word-boundary match: `_get_driver_info` is not `get_driver_info`.
            used = {name for name in names if len(re.findall(rf"(?<![\w.]){name}", corpus)) > 1}
            assert not used, f"{module}: now referenced, remove from the list: {used}"
