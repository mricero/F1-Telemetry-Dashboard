"""Docs must describe the live endpoint the code actually uses (DOC-01, LIVE-01).

Until LIVE-01 the app ran LiveF1's RealF1Client against the legacy `/signalr/`
hub and the docs had to say so. Since LIVE-01 the app connects to
`/signalrcore` itself (`data/signalr_core.py`); the classic negotiate now
answers 401, so any doc still telling people live mode goes through LiveF1 or
the legacy hub is wrong.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS = ("readme.md", "ARCHITECTURE.md")
CODE = ("data/live_adapter.py", "data/signalr_core.py", "scripts/live_smoke.py")


def _text(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


class TestEndpointClaims:
    @pytest.mark.parametrize("path", DOCS + CODE)
    def test_the_signalr_core_endpoint_is_named(self, path):
        assert "signalrcore" in _text(path)

    @pytest.mark.parametrize("path", DOCS + CODE)
    def test_live_mode_is_not_said_to_run_through_livef1(self, path):
        for line in _text(path).splitlines():
            lowered = line.lower()
            if "realf1client" not in lowered:
                continue
            # Naming RealF1Client is fine only to say it is no longer used.
            assert any(
                marker in lowered for marker in ("no longer", "not used", "401", "legacy")
            ), f"{path}: live mode described as LiveF1's RealF1Client: {line.strip()}"

    @pytest.mark.parametrize("path", DOCS)
    def test_the_token_requirement_is_documented(self, path):
        text = _text(path)

        assert "F1TV_SUBSCRIPTION_TOKEN" in text
        assert "subscription" in text.lower()

    @pytest.mark.parametrize("path", DOCS)
    def test_the_project_states_it_is_unofficial(self, path):
        assert "unofficial" in _text(path).lower()

    def test_ip_blocking_risk_is_mentioned(self):
        assert "ip" in _text("readme.md").lower()
        assert "block" in _text("readme.md").lower()


class TestArchitectureMatchesTheCode:
    """DOC-06: ARCHITECTURE.md listed 5 of the data/ modules, 4 of processing/
    and 1 of ui/, and described replays as pickles."""

    @staticmethod
    def _modules() -> list[str]:
        return sorted(
            path.relative_to(PROJECT_ROOT).as_posix()
            for package in ("data", "processing", "ui")
            for path in (PROJECT_ROOT / package).rglob("*.py")
            if path.name != "__init__.py"
        )

    def test_every_module_is_described(self):
        text = _text("ARCHITECTURE.md")
        missing = [module for module in self._modules() if module not in text]

        assert missing == [], f"ARCHITECTURE.md does not mention: {missing}"

    def test_replays_are_not_described_as_pickles(self):
        for line in _text("ARCHITECTURE.md").splitlines():
            lowered = line.lower()
            if ".pkl" in lowered or "pickled" in lowered:
                assert "legacy" in lowered, f"pickle replay claim: {line.strip()}"

    def test_the_replay_format_is_parquet_with_meta_json(self):
        text = _text("ARCHITECTURE.md")

        assert "Parquet" in text
        assert "meta.json" in text

    def test_config_fields_that_do_not_exist_are_not_documented(self):
        text = _text("ARCHITECTURE.md")

        for stale in ("distance_step", "cache_ttl_seconds", "metrics_store.json"):
            assert stale not in text

    def test_the_superseded_research_note_lives_under_docs_history(self):
        assert not (PROJECT_ROOT / "PHASE1_RESEARCH_SUMMARY.md").exists()
        note = _text("docs/history/PHASE1_RESEARCH_SUMMARY.md")

        assert "superseded" in note.splitlines()[0].lower()
