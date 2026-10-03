"""Write the player payloads the jsdom tests load (tests/js/*.test.js).

    python tests/js/build_payloads.py <out_dir>

Built exactly as ``f1dash.ui.replay_view.replay_payload`` builds them (payload, theme
style, lap marks) from the synthetic sessions in ``tests/replay_fixtures.py``,
so the JavaScript is tested against the real payload format, never a
hand-written copy that could drift from it.
"""

import json
import sys
from pathlib import Path

# harness.js puts src/ on PYTHONPATH for f1dash (REPO-10).
ROOT = Path(__file__).resolve().parents[2]


LAP_CURSOR = 1200.0


def _replay_fixtures():
    """tests/replay_fixtures.py by its path: ``tests`` is not on sys.path here."""
    import importlib.util

    path = ROOT / "tests" / "replay_fixtures.py"
    spec = importlib.util.spec_from_file_location("replay_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload_for(session: dict, key: str) -> dict:
    from f1dash.processing.replay_model import session_clock, tower_series
    from f1dash.processing.replay_payload import build_replay_payload
    from f1dash.ui.components.replay_player import player_style
    from f1dash.ui.replay_view import lap_marks

    series = tower_series(session)
    payload = build_replay_payload(session, series, session_clock(session), key)
    payload["style"] = player_style(payload, session.get("compound_colors"))
    payload["lap_marks"] = lap_marks(session, series)
    return payload


def write_payloads(out_dir: Path) -> None:
    fx = _replay_fixtures()

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
    from f1dash.processing.replay_payload import decode_lap_fractions

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
