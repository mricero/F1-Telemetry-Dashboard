"""Design tokens and shared CSS for the timing dashboard.

Values come from the dashboard specification in ``layout.md`` (section 6).
Keeping them in one module means the timing tower, track map and header all
resolve the same colours rather than each hardcoding hexes.
"""

import re

# --- Surfaces -----------------------------------------------------------
BG_PRIMARY = "#0a0a0a"
BG_SURFACE = "#121212"
BG_ROW_ALT = "#1a1a1a"
BG_KO_ROW = "rgba(42, 8, 8, 0.6)"
BORDER = "#2a2a2a"
TEXT = "#ffffff"
TEXT_DIM = "#8a8a8a"

# --- Telemetry status ---------------------------------------------------
PURPLE_BEST = "#d000ff"
GREEN_PB = "#00e676"
YELLOW_SLOW = "#ffea00"
PIT_RED = "#d32f2f"
KO_YELLOW = "#cddc39"
RED_FLAG = "#ff1744"

# --- Team branding ------------------------------------------------------
# Fallback only: a loaded session carries real per-season colours from
# FastF1 (results.TeamColor), which take precedence in team_color().
TEAM_COLORS: dict[str, str] = {
    "mclaren": "#ff8000",
    "mercedes": "#00d2be",
    "ferrari": "#e80020",
    "red bull": "#3671c6",
    "red bull racing": "#3671c6",
    "williams": "#64c4ff",
    "aston martin": "#229971",
    "alpine": "#0093cc",
    "racing bulls": "#6692ff",
    "rb": "#6692ff",
    "kick sauber": "#52e252",
    "sauber": "#52e252",
    "haas": "#b6babd",
    "haas f1 team": "#b6babd",
    "audi": "#00e701",
    "cadillac": "#c8a95a",
}

# Tyre compound single-letter badges (spec section 3.11).
COMPOUND_LETTER = {
    "SOFT": "S",
    "MEDIUM": "M",
    "HARD": "H",
    "INTERMEDIATE": "I",
    "WET": "W",
    "UNKNOWN": "?",
    "TEST-UNKNOWN": "?",
}

COMPOUND_RING = {
    "SOFT": "#da291c",
    "MEDIUM": "#ffd12e",
    "HARD": "#f0f0ec",
    "INTERMEDIATE": "#43b02a",
    "WET": "#0067ad",
    "UNKNOWN": "#8a8a8a",
    "TEST-UNKNOWN": "#434649",
}

# Flag state -> (background, foreground, label). Spec section 2.
FLAG_STATES = {
    "GREEN": (GREEN_PB, "#00320f", "GREEN FLAG"),
    "YELLOW": (YELLOW_SLOW, "#221d00", "YELLOW FLAG"),
    "DOUBLE YELLOW": (YELLOW_SLOW, "#221d00", "DOUBLE YELLOW"),
    "RED": (RED_FLAG, "#ffffff", "RED FLAG"),
    "SAFETY CAR": ("#ffb300", "#221d00", "SAFETY CAR"),
    "VSC": ("#ffb300", "#221d00", "VIRTUAL SC"),
    "CHEQUERED": ("#e0e0e0", "#111111", "CHEQUERED"),
    "FINISHED": ("#3a3a3a", "#e0e0e0", "SESSION ENDED"),
}


# Colours reach the renderers from the live feed, FastF1 and third-party
# replay files, and are interpolated into `style="..."` and SVG stroke/fill
# attributes. Only a plain six-digit hex is ever let through.
NEUTRAL_GREY = "#8a8a8a"
_HEX_COLOUR = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def safe_hex(colour, fallback: str = NEUTRAL_GREY) -> str:
    """A six-digit hex colour, or ``fallback`` for anything else.

    Guards the HTML/SVG builders against values like
    ``"red;background:url(x)"`` arriving in a shared replay.
    """
    if not isinstance(colour, str):
        return fallback
    value = colour.strip()
    if not _HEX_COLOUR.match(value):
        return fallback
    return value if value.startswith("#") else f"#{value}"


def team_color(team_name: str | None, fallback: str | None = None) -> str:
    """Resolve a team's accent colour.

    ``fallback`` is the session's own colour (FastF1 ``TeamColor``) and wins
    when present, since it tracks the real livery for that season - but only
    once :func:`safe_hex` has vouched for it.
    """
    if fallback:
        sanitised = safe_hex(fallback, fallback="")
        if sanitised:
            return sanitised
    key = str(team_name or "").strip().lower()
    return TEAM_COLORS.get(key, NEUTRAL_GREY)


def segment_color(state: str) -> str:
    """Micro-sector segment state -> fill colour."""
    return {
        "PURPLE": PURPLE_BEST,
        "GREEN": GREEN_PB,
        "YELLOW": YELLOW_SLOW,
    }.get(str(state).upper(), "#3a3a3a")


# Injected once per render. Scoped under .f1-dash so it cannot leak into
# Streamlit's own chrome.
DASHBOARD_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');

.f1-dash {{
  --bg-primary: {BG_PRIMARY};
  --bg-surface: {BG_SURFACE};
  --bg-row-alt: {BG_ROW_ALT};
  --border: {BORDER};
  --text: {TEXT};
  --dim: {TEXT_DIM};
  --purple: {PURPLE_BEST};
  --green: {GREEN_PB};
  --yellow: {YELLOW_SLOW};
  --pit-red: {PIT_RED};
  --ko: {KO_YELLOW};
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color: var(--text);
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}}

.f1-dash *, .f1-dash *::before, .f1-dash *::after {{ box-sizing: border-box; }}
.f1-mono {{ font-family: 'JetBrains Mono', 'Roboto Mono', ui-monospace, monospace; }}

/* ---------- header bar ---------- */
.f1-header {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 10px 16px; background: var(--bg-surface);
  border-bottom: 1px solid var(--border); flex-wrap: wrap;
}}
.f1-event {{ display: flex; align-items: baseline; gap: 10px; min-width: 0; }}
.f1-event-name {{ font-size: 15px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }}
.f1-event-country {{
  font-size: 11px; color: var(--dim); letter-spacing: .08em; text-transform: uppercase;
}}
.f1-event-session {{ font-size: 12px; color: var(--dim); text-transform: uppercase; letter-spacing: .08em; }}
.f1-clock {{
  font-size: 18px; font-weight: 700; letter-spacing: .06em;
  padding: 2px 10px; background: #000; border: 1px solid var(--border); border-radius: 4px;
}}
.f1-flag {{
  font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
  padding: 5px 12px; border-radius: 3px; white-space: nowrap;
}}
.f1-env {{ display: flex; gap: 18px; flex-wrap: wrap; align-items: center; }}
.f1-env-item {{ display: flex; flex-direction: column; gap: 1px; line-height: 1.1; }}
.f1-env-label {{ font-size: 9px; color: var(--dim); text-transform: uppercase; letter-spacing: .1em; }}
.f1-env-value {{ font-size: 13px; font-weight: 600; }}
.f1-env-value.rain-yes {{ color: {RED_FLAG}; }}

/* ---------- timing tower ---------- */
.f1-tower {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.f1-tower thead th {{
  position: sticky; top: 0; z-index: 2;
  background: #171717; color: var(--dim);
  font-size: 9px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
  padding: 7px 8px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap;
}}
.f1-tower tbody tr {{ border-bottom: 1px solid #191919; }}
.f1-tower tbody tr:nth-child(even) {{ background: var(--bg-row-alt); }}
.f1-tower tbody tr.ko {{ background: {BG_KO_ROW}; opacity: .55; }}
.f1-tower td {{ padding: 5px 8px; vertical-align: middle; white-space: nowrap; }}

.f1-pos {{
  font-weight: 700; font-size: 13px; text-align: center; width: 34px;
  border-left: 4px solid var(--accent, #444); padding-left: 8px;
}}
.f1-code {{ font-weight: 700; font-size: 13px; letter-spacing: .06em; }}
.f1-team {{ font-size: 9px; color: var(--dim); text-transform: uppercase; letter-spacing: .06em; }}

.f1-badge {{
  display: inline-block; font-size: 9px; font-weight: 700; letter-spacing: .08em;
  padding: 2px 7px; border-radius: 3px; text-transform: uppercase;
}}
.f1-badge.pit {{ background: {PIT_RED}; color: #fff; }}
.f1-badge.track {{ background: rgba(0,230,118,.15); color: {GREEN_PB}; border: 1px solid {GREEN_PB}; }}
.f1-badge.ko {{ background: transparent; color: {KO_YELLOW}; border: 1px solid {KO_YELLOW}; }}
.f1-badge.out {{ background: transparent; color: var(--dim); border: 1px solid #444; }}

.f1-time {{ font-size: 12px; font-variant-numeric: tabular-nums; }}
.f1-time.best {{ color: var(--purple); font-weight: 700; }}
.f1-time.pb {{ color: var(--green); }}
.f1-dim {{ color: var(--dim); }}

/* micro-sector strip: 5 segments under each sector time */
.f1-seg {{ display: flex; gap: 2px; margin-top: 3px; }}
.f1-seg span {{ height: 3px; flex: 1 1 0; border-radius: 1px; background: #333; }}

/* tyre history badges */
.f1-tyres {{ display: flex; gap: 3px; align-items: center; }}
.f1-tyre {{
  position: relative; width: 20px; height: 20px; border-radius: 50%;
  border: 2px solid #666; background: #101010;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 700; line-height: 1;
}}
/* A scrubbed set: dashed ring, so "used" is not conveyed by colour alone. */
.f1-tyre.used {{ border-style: dashed; }}
.f1-tyre em {{
  position: absolute; bottom: -5px; right: -4px; font-style: normal;
  font-size: 8px; font-weight: 700; color: var(--dim);
  background: var(--bg-primary); padding: 0 2px; border-radius: 2px;
}}

/* ---------- section divider ---------- */
.f1-split {{
  padding: 4px 12px; font-size: 9px; font-weight: 700; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim);
  background: #141414; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
}}

/* ---------- sector top-3 widgets ---------- */
.f1-sectors {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }}
.f1-sector-card {{ background: var(--bg-surface); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }}
.f1-sector-head {{
  background: #006064; color: #fff; font-size: 10px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase; padding: 5px 8px; text-align: center;
}}
.f1-sector-row {{ display: flex; align-items: center; gap: 6px; padding: 4px 8px; font-size: 11px; }}
.f1-sector-row + .f1-sector-row {{ border-top: 1px solid #1e1e1e; }}
.f1-rank {{ color: var(--dim); width: 10px; font-weight: 700; }}
.f1-pill {{
  font-size: 10px; font-weight: 700; letter-spacing: .05em;
  padding: 1px 6px; border-radius: 3px; color: #fff; min-width: 38px; text-align: center;
}}
.f1-sector-time {{ margin-left: auto; font-variant-numeric: tabular-nums; }}

/* ---------- track map ---------- */
.f1-map-wrap {{ position: relative; background: var(--bg-surface); border-radius: 6px; padding: 8px; }}
.f1-bench {{
  position: absolute; top: 14px; left: 14px; z-index: 3;
  background: rgba(10,10,10,.85); border: 1px solid var(--border); border-radius: 4px;
  padding: 6px 10px; backdrop-filter: blur(4px);
}}
.f1-bench-label {{ font-size: 9px; color: var(--dim); text-transform: uppercase; letter-spacing: .1em; }}
.f1-bench-time {{ font-size: 16px; font-weight: 700; letter-spacing: .04em; }}
.f1-legend {{ display: flex; gap: 14px; padding: 6px 10px 2px; font-size: 10px; color: var(--dim); flex-wrap: wrap; }}
.f1-legend i {{ display: inline-block; width: 10px; height: 3px; border-radius: 1px; margin-right: 5px; vertical-align: middle; }}
</style>
"""
