"""Vector track map renderer (``layout.md`` section 4).

Draws the circuit as an SVG path built from real GPS telemetry, overlaid
with numbered corner markers, a per-mini-sector dominance layer showing who
was quickest where, and driver position nodes.

SVG rather than a scatter plot: the geometry is a fixed shape that should
stay crisp at any size, and the overlays need precise placement.
"""

import base64
import html
from collections.abc import Sequence

import numpy as np
import pandas as pd

from processing.timing import segment_boundaries
from processing.track_geometry import (  # noqa: F401 - re-exported for callers and tests
    MAX_OUTLINE_POINTS,
    VIEW_H,
    VIEW_W,
    path_from,
    reference_driver,
    rotate_points,
    track_geometry,
)
from ui.theme import BG, EDGE, SURFACE_2, TEXT, TEXT_DIM, WHITE, safe_hex, team_color

# Mini-sectors used for the dominance layer. 3 sectors x 5 segments matches
# the leaderboard's micro-sector strips so the two views agree.
DOMINANCE_SEGMENTS = 15

# A standalone SVG document needs its namespace. It is an identifier, not a
# request: nothing is fetched (guideline 5.13 records the exception).
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

# Text inside the map is drawn by the image's own document, which sees
# neither the page's CSS variables nor its embedded fonts.
SVG_FONT = "Titillium Web, system-ui, Segoe UI, Roboto, Arial, sans-serif"


def dominance_segments(
    micro_times: dict[str, np.ndarray], segments: int = DOMINANCE_SEGMENTS
) -> list[str | None]:
    """Which driver was fastest through each mini-sector.

    ``micro_times`` comes from :func:`processing.timing.micro_sector_times`,
    so the split matches the leaderboard's strips exactly.
    """
    if not micro_times:
        return [None] * segments
    codes = list(micro_times)
    stacked = np.vstack([micro_times[c] for c in codes])
    if stacked.shape[1] != segments:
        return [None] * segments
    return [codes[i] for i in np.argmin(stacked, axis=0)]


def build_track_svg(
    location: dict[str, pd.DataFrame],
    circuit_info: dict | None = None,
    driver_meta: dict[str, dict] | None = None,
    dominance: Sequence[str | None] | None = None,
    markers: Sequence[dict] | None = None,
    segment_distances: Sequence[float] | None = None,
    title: str = "Track map",
) -> str | None:
    """Render the circuit to an SVG string.

    ``dominance`` colours each mini-sector by the fastest driver's team.
    ``segment_distances`` are the distances those mini-sectors are split at
    (from :func:`processing.timing.micro_sector_marks`), so the map and the
    timing strips cover the same stretches; without them the lap is split
    evenly. ``markers`` places driver nodes:
    ``[{'code','x','y','team_colour'}]``. Returns None without usable GPS.
    """
    geometry = track_geometry(location, circuit_info)
    if geometry is None:
        return None
    projected, distance = geometry.outline, geometry.distance

    layers: list[str] = []

    # Track body (guideline 5.6): a 14 px ribbon in --surface-2 over a
    # 1 px darker edge, so thin sections stay legible without decoration.
    outline = path_from(projected)
    layers.append(
        f'<path d="{outline}" fill="none" stroke="{EDGE}" stroke-width="16" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    layers.append(
        f'<path d="{outline}" fill="none" stroke="{SURFACE_2}" stroke-width="14" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # Dominance layer: split the projected polyline into equal slices and
    # tint each by the team colour of whoever was quickest through it.
    if dominance:
        meta = driver_meta or {}
        # Distance-based slices, matching how micro_sector_times splits the
        # lap: an index split would place them where the samples are dense.
        if segment_distances is not None and len(segment_distances) == len(dominance) + 1:
            bounds = np.searchsorted(distance, np.asarray(segment_distances, dtype=float))
            bounds = np.clip(bounds, 0, len(distance) - 1)
        else:
            bounds = segment_boundaries(distance, len(dominance))
        for index, code in enumerate(dominance):
            if not code:
                continue
            start, end = int(bounds[index]), min(int(bounds[index + 1]) + 1, len(projected))
            if end - start < 2:
                continue
            info = meta.get(code, {})
            colour = team_color(info.get("team_name"), info.get("team_colour"))
            slice_path = path_from(projected[start:end], close=False)
            layers.append(
                f'<path d="{slice_path}" fill="none" stroke="{colour}" stroke-width="7" '
                'stroke-linejoin="round" stroke-linecap="round" opacity="0.95"/>'
            )

    # Start/finish line, drawn perpendicular to the opening direction.
    if len(projected) > 2:
        x1, y1, x2, y2 = geometry.start_finish()
        layers.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{WHITE}" stroke-width="3"/>'
        )

    # Corner numbers in --text-dim, nudged outward from the circuit
    # centroid so they do not sit on the racing line; no circles.
    layers.extend(
        f'<text class="corner" x="{corner["lx"]:.1f}" y="{corner["ly"] + 5:.1f}" '
        f'text-anchor="middle" fill="{TEXT_DIM}" font-size="16" '
        f'font-family="{SVG_FONT}">{html.escape(corner["label"])}</text>'
        for corner in geometry.corners(circuit_info)
    )

    # Driver position nodes.
    for marker in markers or []:
        point = geometry.project([[marker["x"], marker["y"]]])[0]
        colour = safe_hex(marker.get("team_colour"))
        code = html.escape(str(marker.get("code", "")))
        layers.append(
            f'<circle cx="{point[0]:.1f}" cy="{point[1]:.1f}" r="9" fill="{colour}" '
            f'stroke="{BG}" stroke-width="2"/>'
        )
        layers.append(
            f'<text x="{point[0]:.1f}" y="{point[1] - 14:.1f}" text-anchor="middle" '
            f'fill="{TEXT}" font-size="16" font-weight="600" '
            f'font-family="{SVG_FONT}">{code}</text>'
        )

    body = "\n".join(layers)
    return (
        f'<svg viewBox="0 0 {VIEW_W} {VIEW_H}" width="100%" role="img" '
        'style="display:block;max-height:560px;">\n'
        f"<title>{html.escape(title)}</title>\n{body}\n</svg>"
    )


def dominance_legend(
    dominance: Sequence[str | None], driver_meta: dict[str, dict] | None = None
) -> str:
    """Legend HTML naming each driver holding a mini-sector."""
    meta = driver_meta or {}
    counts: dict[str, int] = {}
    for code in dominance or []:
        if code:
            counts[code] = counts.get(code, 0) + 1
    if not counts:
        return ""
    chips = []
    for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        info = meta.get(code, {})
        colour = team_color(info.get("team_name"), info.get("team_colour"))
        chips.append(
            f'<span><i style="background:{colour}"></i>{html.escape(code)} <b>{count}</b></span>'
        )
    return (
        '<div class="f1-legend"><span>Fastest per mini-sector:</span>' + "".join(chips) + "</div>"
    )


def svg_image(svg: str, label: str) -> str:
    """The map as an ``<img>`` of a standalone SVG document.

    ``st.html`` sanitises its markup and drops inline ``<svg>``, so the map
    never reached the screen as inline markup; an image with a ``data:`` URI
    passes the sanitiser. ``label`` is the alternative text.
    """
    standalone = svg.replace("<svg ", f'<svg xmlns="{SVG_NAMESPACE}" ', 1)
    encoded = base64.b64encode(standalone.encode("utf-8")).decode("ascii")
    return (
        f'<img class="f1-map" src="data:image/svg+xml;base64,{encoded}" '
        f'alt="{html.escape(label)}" style="display:block;width:100%;height:auto">'
    )
