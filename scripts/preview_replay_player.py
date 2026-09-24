"""Write a self-contained page that runs the replay player without Streamlit.

    python scripts/preview_replay_player.py --fixture
    python scripts/preview_replay_player.py --year 2023 --gp "Bahrain Grand Prix" --session R

The page inlines the player's HTML, CSS and JavaScript, the embedded fonts
and the payload, and calls the component's default export on a shadow root
the way Streamlit does - so a person or an automated browser can open it and
see exactly what the Replay page shows (IMPROVEMENTS.md REPLAY-05).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHIM = """
<script type="module">
const source = document.getElementById("player-js").textContent;
const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
const module = await import(url);
const host = document.getElementById("host");
const shadow = host.attachShadow({ mode: "open" });
const wrapper = document.createElement("div");
wrapper.innerHTML = document.getElementById("player-html").textContent;
const style = document.createElement("style");
style.textContent = document.getElementById("player-css").textContent;
shadow.append(wrapper, style);
const data = JSON.parse(document.getElementById("payload").textContent);
window.replayState = {};
module.default({
  name: "f1_replay_player",
  key: "preview",
  data,
  parentElement: shadow,
  setStateValue: (name, value) => { window.replayState[name] = value; },
  setTriggerValue: () => {},
});
window.replayReady = true;
</script>
"""


def _session(args):
    if args.fixture:
        from tests.replay_fixtures import race_session

        return race_session(), "fixture:race"
    from data.source_manager import DataSourceManager

    session = DataSourceManager().get_session_data(
        source="fastf1", year=args.year, gp=args.gp, session_type=args.session
    )
    return session, f"fastf1:{args.year}:{args.gp}:{args.session}"


def build_page(session: dict, key: str) -> str:
    """The preview page for one session."""
    from processing.replay_model import session_clock, tower_series
    from processing.replay_payload import build_replay_payload
    from ui.components.replay_player import component_source, player_style
    from ui.fonts import font_face_css
    from ui.theme import BG

    series = tower_series(session)
    payload = build_replay_payload(session, series, session_clock(session), key)
    payload["style"] = player_style(payload, session.get("compound_colors"))
    source = component_source()

    def raw(tag_id: str, kind: str, text: str) -> str:
        # Script bodies are inert data here; "</" is split so none can end
        # the element early.
        safe = text.replace("</", "<\\/")
        return f'<script id="{tag_id}" type="{kind}">{safe}</script>'

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Replay player preview</title>",
            f"<style>{font_face_css()} body {{ margin: 0; padding: 12px; background: {BG}; }}</style>",
            "</head><body>",
            '<div id="host"></div>',
            raw("player-html", "text/html", source["html"]),
            raw("player-css", "text/css", source["css"]),
            raw("player-js", "text/plain", source["js"]),
            raw("payload", "application/json", json.dumps(payload, allow_nan=False)),
            SHIM,
            "</body></html>",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", action="store_true", help="use the synthetic test race")
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--gp", default="Bahrain Grand Prix")
    parser.add_argument("--session", default="R")
    parser.add_argument("--out", default=str(ROOT / "replay_preview.html"))
    args = parser.parse_args()

    session, key = _session(args)
    Path(args.out).write_text(build_page(session, key), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
