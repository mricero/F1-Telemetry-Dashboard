"""Vector track map renderer (``layout.md`` section 4).

Draws the circuit as an SVG path built from real GPS telemetry, overlaid
with numbered corner markers, a per-mini-sector dominance layer showing who
was quickest where, and driver position nodes.

SVG rather than a scatter plot: the geometry is a fixed shape that should
stay crisp at any size, and the overlays need precise placement.
"""

import html
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ui.theme import BORDER, TEXT_DIM, team_color

# Viewport the SVG is drawn into; the track is scaled to fit with padding.
VIEW_W = 1000
VIEW_H = 760
PADDING = 62

# Mini-sectors used for the dominance layer. 3 sectors x 5 segments matches
# the leaderboard's micro-sector strips so the two views agree.
DOMINANCE_SEGMENTS = 15


def rotate_points(xy: np.ndarray, angle_degrees: float) -> np.ndarray:
    """Rotate an (N, 2) array of coordinates about the origin.

    FastF1 publishes a per-circuit ``rotation`` so maps come out in the
    orientation broadcasts use; without it circuits appear on their side.
    """
    angle = np.deg2rad(angle_degrees or 0.0)
    matrix = np.array(
        [[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]], dtype=float
    )
    return np.matmul(np.asarray(xy, dtype=float), matrix)


def _reference_trace(location: Dict[str, pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Pick the GPS trace that best describes the circuit outline."""
    best, best_len = None, 0
    for frame in (location or {}).values():
        if frame is None or frame.empty or not {"X", "Y"}.issubset(frame.columns):
            continue
        usable = frame.dropna(subset=["X", "Y"])
        if len(usable) > best_len:
            best, best_len = usable, len(usable)
    return best


def _fit_transform(points: np.ndarray) -> Tuple[float, float, float]:
    """Scale/offset mapping rotated track coordinates into the viewBox."""
    min_xy, max_xy = points.min(axis=0), points.max(axis=0)
    span = np.maximum(max_xy - min_xy, 1e-6)
    scale = min((VIEW_W - 2 * PADDING) / span[0], (VIEW_H - 2 * PADDING) / span[1])
    # Centre the shape in the viewport.
    offset_x = (VIEW_W - span[0] * scale) / 2 - min_xy[0] * scale
    offset_y = (VIEW_H - span[1] * scale) / 2 - min_xy[1] * scale
    return scale, offset_x, offset_y


def _project(points: np.ndarray, scale: float, dx: float, dy: float) -> np.ndarray:
    """Apply the fit transform; SVG's y-axis grows downward, so flip it."""
    out = np.asarray(points, dtype=float) * scale
    out[:, 0] += dx
    out[:, 1] = VIEW_H - (out[:, 1] + dy)
    return out


def _path_from(points: np.ndarray, close: bool = True) -> str:
    """SVG path data for a polyline."""
    if len(points) == 0:
        return ""
    parts = [f"M {points[0, 0]:.2f} {points[0, 1]:.2f}"]
    parts += [f"L {x:.2f} {y:.2f}" for x, y in points[1:]]
    if close:
        parts.append("Z")
    return " ".join(parts)


def dominance_segments(
    micro_times: Dict[str, np.ndarray], segments: int = DOMINANCE_SEGMENTS
) -> List[Optional[str]]:
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
    location: Dict[str, pd.DataFrame],
    circuit_info: Optional[dict] = None,
    driver_meta: Optional[Dict[str, dict]] = None,
    dominance: Optional[Sequence[Optional[str]]] = None,
    markers: Optional[Sequence[dict]] = None,
) -> Optional[str]:
    """Render the circuit to an SVG string.

    ``dominance`` colours each mini-sector by the fastest driver's team.
    ``markers`` places driver nodes: ``[{'code','x','y','team_colour'}]``.
    Returns None when there is no usable GPS data.
    """
    reference = _reference_trace(location)
    if reference is None or len(reference) < 10:
        return None

    rotation = float((circuit_info or {}).get("rotation") or 0.0)
    track = rotate_points(reference[["X", "Y"]].to_numpy(float), rotation)
    scale, dx, dy = _fit_transform(track)
    projected = _project(track.copy(), scale, dx, dy)

    layers: List[str] = []

    # Track body: a wide dark casing under a lighter ribbon reads as tarmac
    # and keeps thin sections legible.
    outline = _path_from(projected)
    layers.append(
        f'<path d="{outline}" fill="none" stroke="#000000" stroke-width="22" '
        'stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>'
    )
    layers.append(
        f'<path d="{outline}" fill="none" stroke="#2f2f2f" stroke-width="16" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # Dominance layer: split the projected polyline into equal slices and
    # tint each by the team colour of whoever was quickest through it.
    if dominance:
        meta = driver_meta or {}
        bounds = np.linspace(0, len(projected), len(dominance) + 1).astype(int)
        for index, code in enumerate(dominance):
            if not code:
                continue
            start, end = bounds[index], min(bounds[index + 1] + 1, len(projected))
            if end - start < 2:
                continue
            info = meta.get(code, {})
            colour = team_color(info.get("team_name"), info.get("team_colour"))
            slice_path = _path_from(projected[start:end], close=False)
            layers.append(
                f'<path d="{slice_path}" fill="none" stroke="{colour}" stroke-width="7" '
                'stroke-linejoin="round" stroke-linecap="round" opacity="0.95"/>'
            )

    # Start/finish line, drawn perpendicular to the opening direction.
    if len(projected) > 2:
        p0, p1 = projected[0], projected[min(4, len(projected) - 1)]
        direction = p1 - p0
        norm = float(np.hypot(*direction)) or 1.0
        perp = np.array([-direction[1], direction[0]]) / norm * 13
        layers.append(
            f'<line x1="{p0[0] - perp[0]:.1f}" y1="{p0[1] - perp[1]:.1f}" '
            f'x2="{p0[0] + perp[0]:.1f}" y2="{p0[1] + perp[1]:.1f}" '
            'stroke="#ffffff" stroke-width="4"/>'
        )

    # Corner markers, nudged outward from the circuit centroid so the
    # numbers do not sit on top of the racing line.
    corners = (circuit_info or {}).get("corners")
    if corners is not None and len(corners) > 0:
        corner_df = pd.DataFrame(corners)
        corner_xy = rotate_points(corner_df[["X", "Y"]].to_numpy(float), rotation)
        corner_pts = _project(corner_xy.copy(), scale, dx, dy)
        centre = projected.mean(axis=0)
        for (px, py), (_, corner) in zip(corner_pts, corner_df.iterrows()):
            away = np.array([px, py]) - centre
            away = away / (float(np.hypot(*away)) or 1.0) * 26
            lx, ly = px + away[0], py + away[1]
            number = corner.get("Number")
            letter = corner.get("Letter") or ""
            label = f"{int(number)}{letter}" if pd.notna(number) else str(letter)
            layers.append(
                f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{lx:.1f}" y2="{ly:.1f}" '
                f'stroke="{TEXT_DIM}" stroke-width="1" opacity="0.5"/>'
            )
            layers.append(
                f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="10" fill="#0a0a0a" '
                f'stroke="{BORDER}" stroke-width="1.5"/>'
            )
            layers.append(
                f'<text x="{lx:.1f}" y="{ly + 3.5:.1f}" text-anchor="middle" '
                'fill="#cfcfcf" font-size="11" font-weight="700" '
                f'font-family="Inter, sans-serif">{html.escape(label)}</text>'
            )

    # Driver position nodes.
    for marker in markers or []:
        raw = np.array([[marker["x"], marker["y"]]], dtype=float)
        point = _project(rotate_points(raw, rotation), scale, dx, dy)[0]
        colour = marker.get("team_colour") or "#ffffff"
        code = html.escape(str(marker.get("code", "")))
        layers.append(
            f'<circle cx="{point[0]:.1f}" cy="{point[1]:.1f}" r="9" fill="{colour}" '
            'stroke="#0a0a0a" stroke-width="2"/>'
        )
        layers.append(
            f'<text x="{point[0]:.1f}" y="{point[1] - 14:.1f}" text-anchor="middle" '
            'fill="#ffffff" font-size="11" font-weight="700" '
            f'font-family="Inter, sans-serif">{code}</text>'
        )

    body = "\n".join(layers)
    return (
        f'<svg viewBox="0 0 {VIEW_W} {VIEW_H}" width="100%" height="100%" '
        'xmlns="http://www.w3.org/2000/svg" style="display:block;max-height:560px;">\n'
        f"{body}\n</svg>"
    )


def dominance_legend(
    dominance: Sequence[Optional[str]], driver_meta: Optional[Dict[str, dict]] = None
) -> str:
    """Legend HTML naming each driver holding a mini-sector."""
    meta = driver_meta or {}
    counts: Dict[str, int] = {}
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
