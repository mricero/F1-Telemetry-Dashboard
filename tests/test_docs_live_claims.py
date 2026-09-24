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
