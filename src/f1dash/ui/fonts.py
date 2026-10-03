"""Self-contained typography (IMPROVEMENTS.md UI-01, guideline 5.3).

Titillium Web 600/700 (SIL Open Font License, ``ui/assets/fonts/OFL.txt``)
is committed with the app and embedded as base64 ``@font-face`` rules, so the
interface makes no font request at runtime - a saved replay opened offline
looks the same, and the viewer's address is not sent to a font host.

The rules go into the main document: a browser ignores ``@font-face``
declared inside a component's shadow root, so the replay player refers to
the family by name instead.
"""

import base64
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FAMILY = "Titillium Web"
FACES = {600: "titillium-web-latin-600-normal.woff2", 700: "titillium-web-latin-700-normal.woff2"}


@lru_cache(maxsize=4)
def font_face_css(folder: Path = FONT_DIR) -> str:
    """``@font-face`` rules for every committed weight, or ``""`` without them.

    Missing files are not an error: the system stack in ``ui.theme`` takes
    over, and nothing is fetched to make up for them.
    """
    rules = []
    for weight, name in FACES.items():
        path = Path(folder) / name
        if not path.is_file():
            logger.info("Font file %s is missing; using the system font stack", path.name)
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            "@font-face {"
            f" font-family: '{FAMILY}'; font-style: normal; font-weight: {weight};"
            " font-display: swap;"
            f" src: url(data:font/woff2;base64,{encoded}) format('woff2'); }}"
        )
    return "\n".join(rules)
