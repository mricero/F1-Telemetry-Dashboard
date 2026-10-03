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

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "f1dash"  # src layout (REPO-10)

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
# The SVG namespace names the document type; nothing is fetched from it
# (guideline 5.13 exception, recorded with REPLAY-04).
REMOTE = re.compile(r"https?://(?!www\.w3\.org/2000/svg)")
HEX = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
HYPE = re.compile(
    r"\b(unlock|seamless|powerful|supercharge|elevate|effortless|magic|oops|let's|dive into"
    r"|ai-powered)\b",
    re.IGNORECASE,
)
MAX_RADIUS_PX = 4.0
# CSS colour keywords slip past the hex check (UI-13): "red" for a pit marker,
# "gray" for an unknown compound. A whole lowercase string literal, or a
# colour property set to a keyword, counts. Upper-case state names ("RED",
# "GREEN") are flag and segment states, not colours.
_COLOUR_NAMES = (
    "red|gray|grey|green|blue|yellow|orange|purple|white|black|pink|silver|gold|navy|teal"
    "|lime|cyan|magenta|maroon|olive|violet|brown|crimson|darkgray|darkgrey|lightgray|lightgrey"
)
COLOUR_WORD = re.compile(rf"^(?:{_COLOUR_NAMES})$")
COLOUR_PROPERTY = re.compile(
    rf"(?:color|background|fill|stroke)\s*[:=]\s*[\"']?(?:{_COLOUR_NAMES})\b", re.IGNORECASE
)


def _python_files() -> list[Path]:
    files = [PACKAGE / "app.py", *sorted((PACKAGE / "ui").rglob("*.py"))]
    assert len(files) > 1, f"no ui modules under {PACKAGE}"
    return files


def _component_files() -> list[Path]:
    folder = PACKAGE / "ui" / "components"
    assert folder.is_dir(), folder
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
    return re.sub(r":root[^{]*\{[^}]*\}", "", css)


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

    def test_no_gradients_glass_or_glow(self):
        offenders = _offences(DECORATION)

        assert not offenders, offenders

    def test_radii_stay_small(self):
        offenders = [
            offence
            for offence in _offences(RADIUS)
            if float(re.search(r"([0-9.]+)px", offence).group(1)) > MAX_RADIUS_PX
        ]

        assert not offenders, offenders


class TestSelfContained:
    """Check 4: no remote fonts, scripts or images - replays work offline."""

    def test_no_remote_urls_in_markup_or_styles(self):
        offenders = _offences(REMOTE)

        assert not offenders, offenders


class TestColoursComeFromTheTheme:
    """Check 5: hex literals only in ui/theme.py, the config and the player :root."""

    def test_no_hex_literals_outside_the_theme(self):
        offenders = _offences(HEX, skip=("theme.py",), root_block_ok=True)

        assert not offenders, offenders

    def test_no_css_colour_names(self):
        offenders = []
        for path in _python_files():
            for line, text in _python_strings(path):
                if COLOUR_WORD.match(text.strip()) or COLOUR_PROPERTY.search(text):
                    offenders.append(f"{path.relative_to(ROOT)}:{line}: {text[:40]!r}")
        for path in _component_files():
            for match in COLOUR_PROPERTY.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)!r}")

        assert not offenders, offenders

    def test_the_colour_name_check_catches_keywords_not_states(self):
        assert COLOUR_WORD.match("red") and COLOUR_WORD.match("gray")
        assert not COLOUR_WORD.match("RED")
        assert COLOUR_PROPERTY.search("color: red;")
        assert not COLOUR_PROPERTY.search("color: var(--text)")


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
        from f1dash.ui.theme import FLAG_STATES, status_chip

        chip = status_chip("Safety car", "SAFETY CAR")

        assert "Safety car" in chip
        assert FLAG_STATES["SAFETY CAR"][0] in chip

    def test_the_label_is_escaped(self):
        from f1dash.ui.theme import status_chip

        assert "<script>" not in status_chip("<script>", "GREEN")

    def test_the_track_status_table_names_every_state(self):
        from f1dash.ui.layout import TRACK_STATUS
        from f1dash.ui.theme import FLAG_STATES

        for state, label in TRACK_STATUS.values():
            assert state in FLAG_STATES
            assert label and not EMOJI.search(label)


class TestSelfContainedTypography:
    """UI-01: the fonts ship with the app and are embedded, never fetched."""

    def test_the_committed_fonts_become_font_face_rules(self):
        from f1dash.ui.fonts import FONT_DIR, font_face_css

        css = font_face_css(FONT_DIR)

        assert "@font-face" in css
        assert "font/woff2" in css
        assert "Titillium Web" in css
        assert (FONT_DIR / "OFL.txt").is_file(), "the licence travels with the fonts"

    def test_missing_fonts_fall_back_to_nothing(self, tmp_path):
        from f1dash.ui.fonts import font_face_css

        assert font_face_css(tmp_path) == ""

    def test_the_theme_is_dark(self):
        import tomllib

        config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

        assert config["theme"]["base"] == "dark"
        assert config["theme"]["backgroundColor"].lower() == "#0b0c0f"

    def test_text_on_team_colours_is_black_or_white_by_contrast(self):
        from f1dash.ui.theme import BLACK, WHITE, text_on

        assert text_on("#ffffff") == BLACK
        assert text_on("#3671c6") == WHITE
        assert text_on("#ffd12e") == BLACK


class TestReplayScreenSpec:
    """UI-04: the replay screen contract is written down, and the chips match it."""

    def test_the_flag_chips_say_the_chip_words(self):
        from f1dash.ui.theme import FLAG_STATES

        labels = {key: label for key, (_, _, label) in FLAG_STATES.items()}

        assert labels["GREEN"] == "GREEN"
        assert labels["YELLOW"] == "YELLOW"
        assert labels["SAFETY CAR"] == "SC"
        assert labels["VSC"] == "VSC"
        assert labels["RED"] == "RED"
        assert labels["CHEQUERED"] == "CHEQUERED"

    def test_layout_md_has_the_replay_screen_section(self):
        text = (ROOT / "layout.md").read_text(encoding="utf-8")
        section = text[text.index("## 9. Replay screen") :]

        for value in (
            "48",
            "30",
            "350 ms",
            "600 ms",
            "0.5x 1x 2x 4x 8x 16x 32x 64x",
            "900",
            "1200",
        ):
            assert value in section, value
        assert "prefers-reduced-motion" in section


# Chip words a tower may show (guideline 5.6); "ON TRACK" is an empty cell.
TOWER_CHIPS = {"PIT", "OUT", "FIN", "DNF", "DSQ", "DNS", "KO"}


def _fixture_towers():
    from f1dash.processing.timing import build_timing_rows
    from f1dash.ui.dashboard import header_html, tower_html
    from tests import replay_fixtures as fx

    for build in (fx.race_session, fx.qualifying_session, fx.practice_session):
        session = build()
        yield build.__name__, tower_html(build_timing_rows(session)) + header_html(session)


class TestTowerVocabulary:
    """UI-13: the tower's words and formats, over the race, qualifying and practice fixtures."""

    def test_only_the_status_vocabulary_appears_as_chips(self):
        for name, markup in _fixture_towers():
            chips = set(re.findall(r'class="f1-badge[^"]*">([^<]*)<', markup))
            assert chips <= TOWER_CHIPS, (name, chips - TOWER_CHIPS)

    def test_no_classified_or_in_pit_words(self):
        for name, markup in _fixture_towers():
            assert "CLASSIFIED" not in markup, name
            assert "IN PIT" not in markup, name

    def test_missing_values_are_an_en_dash(self):
        for name, markup in _fixture_towers():
            texts = [t.strip() for t in re.findall(r">([^<]+)<", markup) if t.strip()]
            for text in texts:
                assert not re.fullmatch(r"-{2,}(:-{2})*", text), (name, text)
                assert text.lower() not in {"nan", "none", "nat", "<na>"}, (name, text)

    def test_a_lapped_finish_is_not_a_chip(self):
        from f1dash.ui.dashboard import _status_html

        assert ">FIN<" in _status_html("+1L")
        assert "+1" not in _status_html("+2 LAPS")

    def test_a_car_in_the_pit_has_no_trap_speed(self):
        from f1dash.processing.timing import MISSING
        from f1dash.ui.dashboard import _speed_text

        assert _speed_text(0.0, "PIT") == MISSING
        assert _speed_text(None, "") == MISSING
        assert _speed_text(301.4, "") == "301"

    def test_live_track_status_chips_use_the_flag_words(self):
        from f1dash.ui.layout import TRACK_STATUS

        assert {label for _, label in TRACK_STATUS.values()} <= {
            "GREEN",
            "YELLOW",
            "SC",
            "VSC",
            "RED",
        }

    def test_race_control_text_is_escaped_not_markdown(self):
        from f1dash.ui.layout import race_control_html

        markup = race_control_html([{"lap": "L3", "flag": "", "message": "CAR 1 *VER* $5$ <b>"}])

        assert "*VER* $5$ &lt;b&gt;" in markup
