"""Design tokens and shared CSS (IMPROVEMENTS.md section 5, UI-01).

The colour tokens of guideline 5.4 live here once, as Python constants, and
are emitted once as CSS variables. Every other module takes its colours from
these names; a hex literal anywhere else in ``ui/`` fails
``tests/test_ui_guideline.py``.
"""

import html
import re

# --- Surfaces and text (guideline 5.4) -----------------------------------
BG = "#0b0c0f"  # page background
SURFACE = "#13151a"  # panels, tower, map background
SURFACE_2 = "#1a1d23"  # zebra rows, hovered rows, inputs
LINE = "#262a31"  # 1 px dividers and borders
TEXT = "#eceef1"  # primary text
TEXT_DIM = "#9aa1ab"  # secondary text
ACCENT = "#e10600"  # focus, playhead, cursor marker, primary button only
BLACK = "#000000"
WHITE = "#ffffff"
EDGE = "#07080a"  # the 1 px darker edge under the track ribbon

# --- Timing conventions ---------------------------------------------------
BEST = "#b138dd"  # session best (purple): fills, segments and flashes
# Session-best *text*: BEST itself measures 3.9:1 on SURFACE, under the 4.5:1
# body-text floor (UI-14). This lighter purple reads 5.97:1 / 5.52:1.
BEST_TEXT = "#c56ef0"
PB = "#2fbf5b"  # personal best (green)
SLOWER = "#e6c229"  # slower than personal best (yellow)
SEGMENT_NONE = LINE  # a mini-sector with no usable time

# Names older modules and tests use, kept as aliases of the tokens above.
BG_PRIMARY = BG
BG_SURFACE = SURFACE
BG_ROW_ALT = SURFACE_2
BORDER = LINE
PURPLE_BEST = BEST
GREEN_PB = PB
YELLOW_SLOW = SLOWER

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

# Flag state -> (background, foreground, label). The chip always carries the
# word, so the state never depends on telling the colours apart. The labels
# are the chip words of layout.md section 9.5; the keys stay as they are.
FLAG_GREEN = "#2fbf5b"
FLAG_YELLOW = "#e6c229"
FLAG_AMBER = "#f2a900"
# Dark enough for white chip text at 5.57:1 (UI-14; the old #e5484d gave 3.91:1).
FLAG_RED = "#c62a2f"
FLAG_STATES = {
    "GREEN": (FLAG_GREEN, "#06200e", "GREEN"),
    "YELLOW": (FLAG_YELLOW, "#1f1a00", "YELLOW"),
    "DOUBLE YELLOW": (FLAG_YELLOW, "#1f1a00", "DOUBLE YELLOW"),
    "RED": (FLAG_RED, WHITE, "RED"),
    "SAFETY CAR": (FLAG_AMBER, "#1f1500", "SC"),
    "VSC": (FLAG_AMBER, "#1f1500", "VSC"),
    "CHEQUERED": (TEXT, BG, "CHEQUERED"),
    "FINISHED": (LINE, TEXT_DIM, "ENDED"),
}

# Chart series without a team colour (weather): one warm, one cool.
CHART_WARM = FLAG_AMBER
CHART_COOL = "#4ea8de"

# --- Typography (guideline 5.3) -----------------------------------------
SYSTEM_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
LABEL_STACK = f'"Titillium Web", {SYSTEM_STACK}'


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


def _luminance(colour: str) -> float:
    """WCAG relative luminance of a six-digit hex colour."""
    value = safe_hex(colour).lstrip("#")
    channels = []
    for index in (0, 2, 4):
        channel = int(value[index : index + 2], 16) / 255
        linear = channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        channels.append(linear)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2 contrast ratio between two hex colours (1.0 to 21.0)."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# Colours lap times, gaps and sector times are written in (UI-14): each must
# reach 4.5:1 on both panel surfaces.
TIME_TEXT_TOKENS = {
    "TEXT": TEXT,
    "TEXT_DIM": TEXT_DIM,
    "BEST_TEXT": BEST_TEXT,
    "PB": PB,
    "SLOWER": SLOWER,
}


def text_on(colour: str) -> str:
    """Black or white, whichever reads better on ``colour`` (guideline 5.4)."""
    light = _luminance(colour)
    on_black = (light + 0.05) / 0.05
    on_white = 1.05 / (light + 0.05)
    return BLACK if on_black >= on_white else WHITE


def status_chip(label: str, state: str) -> str:
    """A text chip in a flag state's colours (``FLAG_STATES`` key).

    The label carries the meaning; the colour only reinforces it, so nothing
    is lost for anyone who cannot tell the colours apart (guideline 5.4).
    """
    background, foreground, _ = FLAG_STATES.get(state, FLAG_STATES["FINISHED"])
    return (
        f'<span class="f1-chip" style="background:{background};color:{foreground}">'
        f"{html.escape(label)}</span>"
    )


def chart_layout(series_count: int) -> dict:
    """The one Plotly layout every chart uses (guideline 5.6).

    Transparent paper on a ``--surface`` plot area, 1 px ``--line`` grid,
    11 px ``--text-dim`` ticks, no title inside the figure (the panel label
    is the title), a legend only above three series, and an ``x`` hover. Charts
    override any of it through :func:`ui.layout.styled_figure`.
    """
    axis = {
        "gridcolor": LINE,
        "linecolor": LINE,
        "zerolinecolor": LINE,
        "tickfont": {"size": 11, "color": TEXT_DIM},
        "title": {"font": {"size": 11, "color": TEXT_DIM}},
    }
    return {
        "title": None,
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": SURFACE,
        "font": {"family": SYSTEM_STACK, "size": 13, "color": TEXT},
        "xaxis": axis,
        "yaxis": axis,
        "showlegend": series_count > 3,
        "legend": {"font": {"size": 11, "color": TEXT_DIM}, "orientation": "h", "y": 1.02},
        # One label per series at the cursor; "x unified" stacked a 20-driver
        # box over the chart (UX-02).
        "hovermode": "x",
        "hoverlabel": {"bgcolor": SURFACE_2, "bordercolor": LINE, "font": {"size": 12}},
        "margin": {"l": 48, "r": 16, "t": 16, "b": 40},
    }


def segment_color(state: str) -> str:
    """Micro-sector segment state -> fill colour."""
    return {
        "PURPLE": BEST,
        "GREEN": PB,
        "YELLOW": SLOWER,
    }.get(str(state).upper(), SEGMENT_NONE)


# Every token as a CSS variable, emitted once.
CSS_TOKENS = f"""
:root {{
  --bg: {BG};
  --surface: {SURFACE};
  --surface-2: {SURFACE_2};
  --line: {LINE};
  --text: {TEXT};
  --text-dim: {TEXT_DIM};
  --accent: {ACCENT};
  --best: {BEST};
  --best-text: {BEST_TEXT};
  --pb: {PB};
  --slower: {SLOWER};
  --font-label: {LABEL_STACK};
  --font-body: {SYSTEM_STACK};
}}
"""

# Shell rules for the main document. The only rule aimed at Streamlit's own
# markup is the block padding (guideline 5.6).
APP_CSS = """
.block-container { padding-top: 3.5rem; }
.f1-rc { font-family: var(--font-body); font-size: 13px; line-height: 1.45; color: var(--text); }
.f1-rc-row {
  display: grid; grid-template-columns: 44px 88px 1fr; gap: 8px;
  padding: 4px 0; border-bottom: 1px solid var(--line);
}
.f1-rc-lap { color: var(--text-dim); font-variant-numeric: tabular-nums; text-align: right; }
.f1-rc-flag {
  font-family: var(--font-label); font-size: 11px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; color: var(--text-dim);
}
.f1-rc-msg { overflow-wrap: anywhere; }
"""

# Injected with every dashboard render. Scoped under .f1-dash so it cannot
# leak into Streamlit's own chrome.
DASHBOARD_CSS = f"""
<style>
{CSS_TOKENS}
.f1-dash {{
  font-family: var(--font-body);
  font-size: 13px;
  line-height: 1.25;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}}
.f1-dash *, .f1-dash *::before, .f1-dash *::after {{ box-sizing: border-box; }}
.f1-num {{ font-variant-numeric: tabular-nums; }}
.f1-chip {{
  display: inline-block; padding: 2px 8px; border-radius: 2px;
  font-family: var(--font-label); font-size: 11px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; white-space: nowrap;
}}

/* ---------- header bar ---------- */
.f1-header {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 8px 16px; min-height: 48px; background: var(--surface);
  border-bottom: 1px solid var(--line); flex-wrap: wrap;
}}
.f1-event {{ display: flex; align-items: baseline; gap: 12px; min-width: 0; }}
.f1-event-name {{
  font-family: var(--font-label); font-size: 22px; font-weight: 700;
  letter-spacing: .02em; text-transform: uppercase;
}}
.f1-event-country, .f1-event-session {{
  font-family: var(--font-label); font-size: 11px; font-weight: 600;
  color: var(--text-dim); letter-spacing: .06em; text-transform: uppercase;
}}
.f1-clock {{ font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.f1-flag {{
  font-family: var(--font-label); font-size: 11px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase;
  padding: 4px 10px; border-radius: 2px; white-space: nowrap;
}}
.f1-env {{ display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }}
.f1-env-item {{ display: flex; flex-direction: column; gap: 2px; line-height: 1.1; }}
.f1-env-label {{
  font-family: var(--font-label); font-size: 11px; font-weight: 600;
  color: var(--text-dim); text-transform: uppercase; letter-spacing: .06em;
}}
.f1-env-value {{ font-size: 13px; font-variant-numeric: tabular-nums; }}
.f1-env-value.rain-yes {{ color: var(--text); font-weight: 600; }}

/* ---------- timing tower ---------- */
.f1-tower {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.f1-tower thead th {{
  position: sticky; top: 0; z-index: 2; background: var(--surface);
  color: var(--text-dim); font-family: var(--font-label);
  font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap;
}}
.f1-tower tbody tr:nth-child(even) {{ background: var(--surface-2); }}
.f1-tower tbody tr.ko {{ background: var(--surface-2); color: var(--text-dim); }}
.f1-tower td {{ padding: 4px 8px; height: 28px; vertical-align: middle; white-space: nowrap; }}
/* Sticky header row and sticky first two columns (position, driver), so the
   tower stays readable when it scrolls either way. */
.f1-tower th:nth-child(1), .f1-tower td:nth-child(1),
.f1-tower th:nth-child(2), .f1-tower td:nth-child(2) {{
  position: sticky; z-index: 1; background: inherit;
}}
.f1-tower tbody tr {{ background: var(--surface); }}
.f1-tower th:nth-child(1), .f1-tower td:nth-child(1) {{ left: 0; }}
.f1-tower th:nth-child(2), .f1-tower td:nth-child(2) {{ left: 34px; }}
.f1-tower thead th:nth-child(1), .f1-tower thead th:nth-child(2) {{ z-index: 3; }}
@media (max-width: 1199px) {{
  .f1-tower .col-compact {{ display: none; }}
}}
.f1-empty {{ padding: 24px; color: var(--text-dim); text-align: center; }}

.f1-pos {{
  font-weight: 600; text-align: right; width: 34px;
  border-left: 4px solid var(--team, var(--line)); padding-left: 8px;
  font-variant-numeric: tabular-nums;
}}
.f1-code {{ font-family: var(--font-label); font-weight: 600; letter-spacing: .04em; }}
.f1-team {{
  font-family: var(--font-label); font-size: 11px; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: .06em;
}}

.f1-badge {{
  display: inline-block; font-family: var(--font-label); font-size: 11px; font-weight: 600;
  letter-spacing: .06em; padding: 1px 6px; border-radius: 2px; text-transform: uppercase;
  border: 1px solid var(--line); color: var(--text-dim);
}}
.f1-badge.pit {{ border-color: var(--text); color: var(--text); }}
.f1-badge.track {{ border-color: transparent; color: var(--text-dim); }}
.f1-badge.ko {{ border-color: var(--line); color: var(--text-dim); }}
.f1-badge.out {{ border-color: var(--line); color: var(--text-dim); }}

.f1-time {{ font-variant-numeric: tabular-nums; }}
.f1-time.best {{ color: var(--best-text); font-weight: 600; }}
.f1-time.pb {{ color: var(--pb); }}
.f1-dim {{ color: var(--text-dim); }}

/* micro-sector strip: 5 segments under each sector time */
.f1-seg {{ display: flex; gap: 2px; margin-top: 3px; }}
.f1-seg span {{ height: 3px; flex: 1 1 0; border-radius: 1px; background: var(--line); }}

/* tyre history badges */
.f1-tyres {{ display: flex; gap: 4px; align-items: center; }}
.f1-tyre {{
  position: relative; width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid var(--text-dim); background: var(--bg);
  display: inline-flex; align-items: center; justify-content: center;
  font-family: var(--font-label); font-size: 10px; font-weight: 700; line-height: 1;
}}
/* A scrubbed set: dashed ring, so "used" is not conveyed by colour alone. */
.f1-tyre.used {{ border-style: dashed; }}
.f1-tyre em {{
  position: absolute; bottom: -5px; right: -6px; font-style: normal;
  font-size: 9px; font-weight: 600; color: var(--text-dim);
  background: var(--surface); padding: 0 2px; border-radius: 2px;
}}

/* ---------- section divider ---------- */
.f1-split {{
  padding: 4px 12px; font-family: var(--font-label); font-size: 11px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; color: var(--text-dim);
  background: var(--surface); border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}}

/* ---------- sector top-3 widgets ---------- */
/* auto-fit: three cards side by side on a desktop, fewer per row on a phone,
   so the sector-3 times are never clipped by .f1-dash's overflow (UI-20). */
.f1-sectors {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px; padding: 8px;
}}
.f1-sector-card {{ border-top: 1px solid var(--line); }}
.f1-sector-head {{
  color: var(--text-dim); font-family: var(--font-label); font-size: 11px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; padding: 6px 0 4px;
}}
.f1-sector-row {{ display: flex; align-items: center; gap: 8px; padding: 4px 0; }}
.f1-rank {{ color: var(--text-dim); width: 12px; font-variant-numeric: tabular-nums; }}
.f1-pill {{
  font-family: var(--font-label); font-size: 11px; font-weight: 600; letter-spacing: .04em;
  padding: 1px 6px; border-radius: 2px; min-width: 40px; text-align: center;
}}
.f1-sector-time {{ margin-left: auto; font-variant-numeric: tabular-nums; }}

/* ---------- track map ---------- */
.f1-map-wrap {{ position: relative; background: var(--surface); padding: 8px; }}
.f1-bench {{
  position: absolute; top: 12px; left: 12px; z-index: 3; background: var(--surface);
  border: 1px solid var(--line); border-radius: 4px; padding: 6px 10px;
}}
.f1-bench-label {{
  font-family: var(--font-label); font-size: 11px; font-weight: 600;
  color: var(--text-dim); text-transform: uppercase; letter-spacing: .06em;
}}
.f1-bench-time {{ font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.f1-legend {{
  display: flex; gap: 12px; padding: 6px 8px 2px; font-size: 11px;
  color: var(--text-dim); flex-wrap: wrap;
}}
.f1-legend i {{
  display: inline-block; width: 10px; height: 3px; border-radius: 1px;
  margin-right: 4px; vertical-align: middle;
}}
.f1-note {{
  padding: 4px 16px; font-size: 11px; color: var(--text-dim);
  border-bottom: 1px solid var(--line);
}}
</style>
"""
