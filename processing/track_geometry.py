"""Circuit geometry shared by the SVG map and the replay player (REPLAY-05).

The outline comes from one driver's GPS trace, rotated by FastF1's
per-circuit ``rotation`` and fitted into a fixed viewBox. The server-drawn
map and the browser player's car positions must use the *same* transform,
or the cars drift off the track; this module is that one transform. Pure:
no Streamlit, no network.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from processing.timing import segment_boundaries

# Viewport the map is drawn into; the track is scaled to fit with padding.
VIEW_W = 1000
VIEW_H = 760
PADDING = 62

# A full-session trace holds every lap (~300 km, tens of thousands of points)
# and is drawn three times over. Resampling at uniform distance keeps the
# shape while bounding the SVG the browser has to parse.
MAX_OUTLINE_POINTS = 1500


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


def reference_driver(location: dict[str, pd.DataFrame]) -> str | None:
    """Whose GPS trace the outline is drawn from (the most complete one)."""
    best_code, best_len = None, 0
    for code, frame in (location or {}).items():
        if frame is None or frame.empty or not {"X", "Y"}.issubset(frame.columns):
            continue
        usable = len(frame.dropna(subset=["X", "Y"]))
        if usable > best_len:
            best_code, best_len = code, usable
    return best_code


def reference_trace(location: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """The GPS trace that best describes the circuit outline."""
    code = reference_driver(location)
    if code is None:
        return None
    return location[code].dropna(subset=["X", "Y"])


def trace_distance(reference: pd.DataFrame, track: np.ndarray) -> np.ndarray:
    """Distance along the reference trace, for equal-distance slicing.

    Prefers the frame's own ``Distance`` channel (FastF1 integrates it from
    speed); falls back to the GPS arc length, which is proportional to it.
    """
    if "Distance" in reference.columns:
        values = pd.to_numeric(reference["Distance"], errors="coerce").to_numpy(float)
        if np.isfinite(values).all() and np.all(np.diff(values) >= 0) and values[-1] > values[0]:
            return values
    steps = np.hypot(*np.diff(track, axis=0).T)
    return np.concatenate([[0.0], np.cumsum(np.nan_to_num(steps))])


def decimate(
    track: np.ndarray, distance: np.ndarray, limit: int = MAX_OUTLINE_POINTS
) -> tuple[np.ndarray, np.ndarray]:
    """Resample a trace to at most ``limit`` points, uniformly by distance."""
    if len(track) <= limit:
        return track, distance
    keep = np.unique(segment_boundaries(distance, limit))
    return track[keep], distance[keep]


def path_from(points: np.ndarray, close: bool = True) -> str:
    """SVG path data for a polyline."""
    if len(points) == 0:
        return ""
    parts = [f"M {points[0, 0]:.2f} {points[0, 1]:.2f}"]
    parts += [f"L {x:.2f} {y:.2f}" for x, y in points[1:]]
    if close:
        parts.append("Z")
    return " ".join(parts)


@dataclass(frozen=True)
class TrackGeometry:
    """One circuit fitted into the viewBox, and the transform that did it."""

    rotation: float
    scale: float
    dx: float
    dy: float
    outline: np.ndarray  # (N, 2) projected outline points
    distance: np.ndarray  # distance along the outline, same length

    def project(self, xy) -> np.ndarray:
        """Raw FastF1 X/Y (1/10 m) -> viewBox coordinates.

        SVG's y-axis grows downward, so y is flipped after the fit.
        """
        rotated = rotate_points(np.asarray(xy, dtype=float).reshape(-1, 2), self.rotation)
        out = rotated * self.scale
        out[:, 0] += self.dx
        out[:, 1] = VIEW_H - (out[:, 1] + self.dy)
        return out

    def start_finish(self, half_length: float = 13.0) -> tuple[float, float, float, float]:
        """A short line across the track at the start of the outline."""
        p0 = self.outline[0]
        p1 = self.outline[min(4, len(self.outline) - 1)]
        direction = p1 - p0
        norm = float(np.hypot(*direction)) or 1.0
        perp = np.array([-direction[1], direction[0]]) / norm * half_length
        return (
            float(p0[0] - perp[0]),
            float(p0[1] - perp[1]),
            float(p0[0] + perp[0]),
            float(p0[1] + perp[1]),
        )

    def corners(self, circuit_info: dict | None, offset: float = 26.0) -> list[dict]:
        """Corner labels, nudged outward from the circuit centroid."""
        corners = (circuit_info or {}).get("corners")
        if corners is None or len(corners) == 0:
            return []
        frame = pd.DataFrame(corners)
        points = self.project(frame[["X", "Y"]].to_numpy(float))
        centre = self.outline.mean(axis=0)
        found = []
        for (px, py), (_, corner) in zip(points, frame.iterrows(), strict=False):
            away = np.array([px, py]) - centre
            away = away / (float(np.hypot(*away)) or 1.0) * offset
            number = corner.get("Number")
            letter = corner.get("Letter") or ""
            label = f"{int(number)}{letter}" if pd.notna(number) else str(letter)
            found.append(
                {
                    "x": float(px),
                    "y": float(py),
                    "lx": float(px + away[0]),
                    "ly": float(py + away[1]),
                    "label": label,
                }
            )
        return found


def track_geometry(
    location: dict[str, pd.DataFrame], circuit_info: dict | None = None
) -> TrackGeometry | None:
    """The circuit outline and its viewBox transform; None without usable GPS."""
    reference = reference_trace(location)
    if reference is None or len(reference) < 10:
        return None

    rotation = float((circuit_info or {}).get("rotation") or 0.0)
    track = rotate_points(reference[["X", "Y"]].to_numpy(float), rotation)
    distance = trace_distance(reference, track)
    track, distance = decimate(track, distance)

    min_xy, max_xy = track.min(axis=0), track.max(axis=0)
    span = np.maximum(max_xy - min_xy, 1e-6)
    scale = float(min((VIEW_W - 2 * PADDING) / span[0], (VIEW_H - 2 * PADDING) / span[1]))
    # Centre the shape in the viewport.
    dx = float((VIEW_W - span[0] * scale) / 2 - min_xy[0] * scale)
    dy = float((VIEW_H - span[1] * scale) / 2 - min_xy[1] * scale)

    outline = track * scale
    outline[:, 0] += dx
    outline[:, 1] = VIEW_H - (outline[:, 1] + dy)
    return TrackGeometry(rotation, scale, dx, dy, outline, distance)
