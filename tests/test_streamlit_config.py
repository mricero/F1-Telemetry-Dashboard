"""The bundled Streamlit config (REPO-22, DIST-02).

f1dash passes every key of this file to ``streamlit run`` as a flag, so what
is written here is what an installed copy runs with too.
"""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / ".streamlit" / "config.toml"


def _config() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_usage_statistics_are_off():
    """``browser.gatherUsageStats`` defaults to true: every viewer's browser
    would report to Streamlit, an external request the guideline bans."""
    assert _config()["browser"]["gatherUsageStats"] is False


def test_the_theme_radius_stays_within_the_guideline():
    assert _config()["theme"]["baseRadius"] == "4px"
