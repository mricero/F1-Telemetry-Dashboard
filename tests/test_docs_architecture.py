"""The docs describe the code that exists (DOC-02, DOC-06, DOC-07)."""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def _modules() -> list[str]:
    found = []
    for package in ("data", "processing", "ui"):
        for path in sorted((PROJECT_ROOT / package).glob("*.py")):
            if path.name != "__init__.py":
                found.append(path.stem)
    return found


class TestArchitecture:
    @pytest.mark.parametrize("module", _modules())
    def test_every_module_is_described(self, module):
        assert re.search(rf"\b{module}\b", _text("ARCHITECTURE.md")), module

    def test_no_pickle_replays_or_removed_config(self):
        text = _text("ARCHITECTURE.md")
        assert ".pkl" not in text
        assert "distance_step" not in text
        assert "cache_ttl_seconds" not in text

    def test_the_replay_schema_matches_the_code(self):
        from data.source_manager import DataSourceManager

        assert f"schema {DataSourceManager.REPLAY_SCHEMA_VERSION}" in _text("ARCHITECTURE.md")
        assert f"**{DataSourceManager.REPLAY_SCHEMA_VERSION}**" in _text("CLAUDE.md")


class TestReadme:
    def test_install_comes_first(self):
        text = _text("readme.md")
        headings = re.findall(r"^## (.+)$", text, re.M)

        assert headings[:6] == [
            "Install",
            "Run",
            "Update",
            "Uninstall",
            "Where your data lives",
            "Live timing token",
        ]

    @pytest.mark.parametrize(
        "phrase",
        [
            "uv tool install git+https://github.com/mricero/F1-Telemetry-Dashboard",
            "install.ps1",
            "f1dash update",
            "uv tool uninstall f1dash",
            "f1dash paths",
            "NOTICE",
            "Data sources and terms",
        ],
    )
    def test_says(self, phrase):
        assert phrase in _text("readme.md")

    @pytest.mark.parametrize(
        "stale",
        ["your-username", ".pkl", "distance_step", "zlib", "1.35+", "LIVE SESSION DETECTED"],
    )
    def test_no_stale_claims(self, stale):
        assert stale not in _text("readme.md")

    def test_livef1_is_not_advertised(self):
        for line in _text("readme.md").splitlines():
            if "livef1" in line.lower():
                assert "no longer" in line.lower(), line


class TestHistory:
    def test_the_research_note_is_marked_superseded(self):
        assert "Superseded" in _text("docs/history/PHASE1_RESEARCH_SUMMARY.md")
        assert not (PROJECT_ROOT / "PHASE1_RESEARCH_SUMMARY.md").exists()
