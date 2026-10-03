"""UI-14: every chip and every time-text colour reaches WCAG 4.5:1.

Guideline 5.11 states that the tokens pass; this test is what makes the
statement true. 13 px lap times and 11 px chips are body text, so the floor
is 4.5:1, not the 3:1 allowed for large text.
"""

import pytest

from f1dash.ui import theme
from f1dash.ui.theme import FLAG_STATES, SURFACE, SURFACE_2, TIME_TEXT_TOKENS, contrast_ratio

FLOOR = 4.5


class TestContrastRatio:
    def test_black_on_white_is_twenty_one(self):
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)

    def test_order_does_not_matter(self):
        assert contrast_ratio(theme.TEXT, theme.BG) == contrast_ratio(theme.BG, theme.TEXT)

    def test_a_colour_on_itself_is_one(self):
        assert contrast_ratio(theme.SURFACE, theme.SURFACE) == pytest.approx(1.0)


@pytest.mark.parametrize("state", sorted(FLAG_STATES))
def test_every_flag_chip_is_readable(state):
    background, foreground, _ = FLAG_STATES[state]

    assert contrast_ratio(foreground, background) >= FLOOR, state


@pytest.mark.parametrize("surface", [SURFACE, SURFACE_2], ids=["surface", "surface-2"])
@pytest.mark.parametrize("token", sorted(TIME_TEXT_TOKENS))
def test_every_time_text_token_is_readable_on_both_surfaces(token, surface):
    assert contrast_ratio(TIME_TEXT_TOKENS[token], surface) >= FLOOR, token


def test_session_best_text_uses_the_lighter_purple():
    # BEST stays for fills and flashes; it would fail as text.
    assert contrast_ratio(theme.BEST, SURFACE) < FLOOR
    assert TIME_TEXT_TOKENS["BEST_TEXT"] == theme.BEST_TEXT
    assert "color: var(--best-text)" in theme.DASHBOARD_CSS
    assert f"--best-text: {theme.BEST_TEXT}" in theme.CSS_TOKENS


def test_the_red_chip_keeps_white_text():
    background, foreground, label = FLAG_STATES["RED"]

    assert (background, foreground, label) == (theme.FLAG_RED, theme.WHITE, "RED")
