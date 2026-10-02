"""Write the player payloads the jsdom tests load (tests/js/*.test.js).

    python tests/js/build_payloads.py <out_dir>

Built exactly as ``ui.replay_view.replay_payload`` builds them (payload, theme
style, lap marks) from the synthetic sessions in ``tests/replay_fixtures.py``,
so the JavaScript is tested against the real payload format, never a
hand-written copy that could drift from it.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


LAP_CURSOR = 1200.0


def payload_for(session: dict, key: str) -> dict:
    from processing.replay_model import session_clock, tower_series
    from processing.replay_payload import build_replay_payload
    from ui.components.replay_player import player_style
    from ui.replay_view import lap_marks

    series = tower_series(session)
    payload = build_replay_payload(session, series, session_clock(session), key)
    payload["style"] = player_style(payload, session.get("compound_colors"))
    payload["lap_marks"] = lap_marks(session, series)
    return payload


def write_payloads(out_dir: Path) -> None:
    from tests import replay_fixtures as fx

    out_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "race": payload_for(fx.race_session(), "fixture:race"),
        "qualifying": payload_for(fx.qualifying_session(), "fixture:qualifying"),
    }
    for name, payload in payloads.items():
        text = json.dumps(payload, allow_nan=False)
        (out_dir / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
    meta = {
        "lights_out": fx.LIGHTS_OUT,
        "sc_start": fx.SC_START,
        "sc_end": fx.SC_END,
    }
    # Lap fractions the player must draw at LAP_CURSOR, decoded here (FEAT-07).
    from processing.replay_payload import decode_lap_fractions

    pos = payloads["race"]["pos"]
    frame = round((LAP_CURSOR - pos["t0"]) / pos["step"])
    row = decode_lap_fractions(pos)[frame]
    meta["lap_cursor"] = LAP_CURSOR
    meta["lap_fractions"] = {
        code: None if value != value else float(value)
        for code, value in zip(pos["codes"], row, strict=True)
    }
    (out_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_payloads(Path(sys.argv[1]))
