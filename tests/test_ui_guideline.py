"""The UI guideline, enforced (IMPROVEMENTS.md section 5.12, UI-00).

Scans everything a user sees - ``app.py``, ``ui/**/*.py``, the replay
component's HTML/CSS/JS and ``.streamlit/config.toml`` - for the marks of a
generated interface: emoji used as icons and labels, decorative effects,
large radii, remote requests, stray colour literals, hype copy and a hero
title. A check the current code cannot pass yet is marked ``xfail`` with the
item that fixes it; ``strict=True`` turns it into a failure the moment that
item lands, so the marker has to be removed rather than forgotten.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Pictographs and emoji presentation (guideline 5.12, check 1). Typographic
# characters used in data - en dash, middle dot, degree sign, arrows in
# keyboard help - fall outside these ranges and stay allowed.
EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # emoji and pictographs
    (0x2600, 0x27BF),  # miscellaneous symbols and dingbats
    (0x2B00, 0x2BFF),  # arrows and shapes used as icons
    (0xFE0F, 0xFE0F),  # emoji presentation selector
    (0x23E9, 0x23FA),  # media-control symbols
    (0x25A0, 0x25FF),  # geometric shapes such as the play triangle
)
EMOJI = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in EMOJI_RANGES) + "]")
DECORATION = re.compile(r"gradient\(|backdrop-filter|text-shadow|filter:\s*blur|drop-shadow")
RADIUS = re.compile(r"border-radius:\s*([0-9.]+)px")
REMOTE = re.compile(r"https?://")
HEX = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
HYPE = re.compile(
    r"\b(unlock|seamless|powerful|supercharge|elevate|effortless|magic|oops|let's|dive into"
    r"|ai-powered)\b",
    re.IGNORECASE,
)
MAX_RADIUS_PX = 4.0


def _python_files() -> list[Path]:
    return [ROOT / "app.py", *sorted((ROOT / "ui").rglob("*.py"))]


def _component_files() -> list[Path]:
    folder = ROOT / "ui" / "components"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.suffix in {".js", ".css", ".html"})


def _config_files() -> list[Path]:
    config = ROOT / ".streamlit" / "config.toml"
    return [config] if config.is_file() else []


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _python_strings(path: Path) -> list[tuple[int, str]]:
    """Every string literal (f-string parts included) except docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _strip_root_block(css: str) -> str:
    """The player's ``:root`` token block is where its colours may live."""
    return re.sub(r":root\s*\{[^}]*\}", "", css)


def _offences(pattern: re.Pattern, *, skip: tuple[str, ...] = (), root_block_ok=False):
    found = []
    for path in _python_files():
        if path.name in skip:
            continue
        for line, text in _python_strings(path):
            for match in pattern.finditer(text):
                found.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)!r}")
    for path in _component_files():
        text = path.read_text(encoding="utf-8")
        if root_block_ok and path.suffix == ".css":
            text = _strip_root_block(text)
        for match in pattern.finditer(text):
            found.append(f"{path.relative_to(ROOT)}: {match.group(0)!r}")
    return found


def _streamlit_calls(path: Path):
    """``st.*(...)`` calls, with the string arguments a user would read."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        root = node.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if not (isinstance(root, ast.Name) and root.id == "st"):
            continue
        texts = []
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            for part in ast.walk(argument):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    texts.append(part.value)
        yield node, texts


class TestNoEmoji:
    """Check 1: emoji carry no defined meaning and render differently per OS."""

    def test_no_emoji_anywhere_in_the_interface(self):
        offenders = []
        for path in [*_python_files(), *_component_files(), *_config_files()]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if EMOJI.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")

        assert not offenders, f"emoji in the interface: {offenders}"


class TestNoDecoration:
    """Check 2 and 3: gradients, glass, glow, heavy shadows and large radii."""

    @pytest.mark.xfail(strict=True, reason="UI-01 replaces the stylesheet (backdrop-filter)")
    def test_no_gradients_glass_or_glow(self):
        offenders = _offences(DECORATION)

        assert not offenders, offenders

    @pytest.mark.xfail(strict=True, reason="UI-01 replaces the stylesheet (6 px radii)")
    def test_radii_stay_small(self):
        offenders = [
            offence
            for offence in _offences(RADIUS)
            if float(re.search(r"([0-9.]+)px", offence).group(1)) > MAX_RADIUS_PX
        ]

        assert not offenders, offenders


class TestSelfContained:
    """Check 4: no remote fonts, scripts or images - replays work offline."""

    @pytest.mark.xfail(strict=True, reason="UI-01 removes the Google Fonts import")
    def test_no_remote_urls_in_markup_or_styles(self):
        offenders = _offences(REMOTE)

        assert not offenders, offenders


class TestColoursComeFromTheTheme:
    """Check 5: hex literals only in ui/theme.py, the config and the player :root."""

    @pytest.mark.xfail(strict=True, reason="UI-05 moves the remaining literals into tokens")
    def test_no_hex_literals_outside_the_theme(self):
        offenders = _offences(HEX, skip=("theme.py",), root_block_ok=True)

        assert not offenders, offenders


class TestCopy:
    """Check 6: literal, specific labels - no hype, no exclamation marks."""

    def test_no_hype_words_or_exclamations_in_user_facing_text(self):
        offenders = []
        for path in _python_files():
            for node, texts in _streamlit_calls(path):
                for text in texts:
                    if HYPE.search(text) or text.rstrip().endswith("!"):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {text!r}")
        for path in _component_files():
            if path.suffix != ".html":
                continue
            for node_text in re.findall(r">([^<]+)<", path.read_text(encoding="utf-8")):
                if HYPE.search(node_text) or node_text.strip().endswith("!"):
                    offenders.append(f"{path.relative_to(ROOT)}: {node_text.strip()!r}")

        assert not offenders, offenders


class TestNoHeroTitle:
    """Check 7: the session header bar is the title."""

    @pytest.mark.xfail(strict=True, reason="UI-01 deletes st.title and the caption")
    def test_no_page_title_widget(self):
        offenders = [
            f"{path.relative_to(ROOT)}:{node.lineno}"
            for path in _python_files()
            for node, _ in _streamlit_calls(path)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "title"
        ]

        assert not offenders, offenders

    def test_the_page_icon_is_never_an_emoji(self):
        offenders = []
        for path in _python_files():
            for node, _ in _streamlit_calls(path):
                for keyword in node.keywords:
                    if keyword.arg != "page_icon":
                        continue
                    value = keyword.value
                    is_file = isinstance(value, ast.Constant) and str(value.value).endswith(
                        (".png", ".svg", ".ico")
                    )
                    if not is_file:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

        assert not offenders, offenders


class TestTheChecksThemselves:
    """The scanners must catch what they claim to."""

    def test_the_emoji_pattern_catches_icons_but_not_data_glyphs(self):
        assert EMOJI.search("\U0001f3c1 Flag")
        assert EMOJI.search(chr(0x25B6) + " Play")
        assert EMOJI.search(chr(0x23F1) + chr(0xFE0F) + " Lap Times")
        data_glyphs = "".join(chr(c) for c in (0xB7, 0xB0, 0x2013, 0x2190, 0x2192))
        assert not EMOJI.search(f"Lap 3 {data_glyphs} 21C")

    def test_hex_literals_are_found_but_entities_are_not(self):
        assert HEX.search("color:#e10600")
        assert not HEX.search("&#39;")

    def test_hype_words_are_whole_words(self):
        assert HYPE.search("Unlock insights")
        assert not HYPE.search("Unlocked by the stewards")


class TestStatusChips:
    """State indicators are words in the flag colours, not coloured icons."""

    def test_a_chip_says_what_it_means(self):
        from ui.theme import FLAG_STATES, status_chip

        chip = status_chip("Safety car", "SAFETY CAR")

        assert "Safety car" in chip
        assert FLAG_STATES["SAFETY CAR"][0] in chip

    def test_the_label_is_escaped(self):
        from ui.theme import status_chip

        assert "<script>" not in status_chip("<script>", "GREEN")

    def test_the_track_status_table_names_every_state(self):
        from ui.layout import TRACK_STATUS
        from ui.theme import FLAG_STATES

        for state, label in TRACK_STATUS.values():
            assert state in FLAG_STATES
            assert label and not EMOJI.search(label)
