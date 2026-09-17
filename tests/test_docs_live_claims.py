"""Docs must not promise a live endpoint the code does not use (DOC-01).

livef1's RealF1Client connects to the legacy `/signalr/` endpoint, but the
readme, the adapter docstring, the smoke script and ARCHITECTURE.md all
claimed the `/signalrcore` endpoint FastF1 moved to - which is the one this
app will use only once LIVE-01 lands.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS = ("readme.md", "ARCHITECTURE.md")
CODE = ("data/live_adapter.py", "scripts/live_smoke.py")


def _text(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


class TestEndpointClaims:
    @pytest.mark.parametrize("path", DOCS + CODE)
    def test_signalrcore_is_never_claimed_unqualified(self, path):
        text = _text(path)
        for line in text.splitlines():
            if "signalrcore" not in line:
                continue
            # Mentioning the endpoint is fine; claiming this app connects to
            # it is not, until LIVE-01 replaces the client.
            assert any(
                marker in line.lower()
                for marker in ("not", "legacy", "live-01", "would", "planned", "fastf1 uses")
            ), f"{path}: unqualified /signalrcore claim: {line.strip()}"

    @pytest.mark.parametrize("path", DOCS)
    def test_the_legacy_endpoint_is_named(self, path):
        assert "/signalr/" in _text(path), "docs should say which endpoint is actually used"

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
