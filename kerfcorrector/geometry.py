"""Core kerf-compensation geometry: flatten SVG path data to polygons,
work out which loops are solid material vs. holes, and offset each one
by the right amount and direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.validation import make_valid
from svgelements import Path, Close, Line, Move


@dataclass
class Subpath:
    """One closed loop belonging to a source element."""

    points: list[tuple[float, float]]
    closed: bool


def flatten_path(path: Path, tolerance: float) -> list[Subpath]:
    """Turn an svgelements Path (already in the coordinate space you want
    the output points in) into closed polygons (line segments only).

    Straight-line segments are kept exact. Curves (Bezier/Arc) are sampled
    adaptively so consecutive points deviate from the true curve by no more
    than `tolerance` (in the path's own coordinate units).
    """
    subpaths: list[Subpath] = []
    current: list[tuple[float, float]] = []
    start_point = None

    def flush(closed: bool):
        nonlocal current
        if len(current) >= 2:
            subpaths.append(Subpath(points=current, closed=closed))
        current = []

    for seg in path.segments():
        if isinstance(seg, Move):
            flush(closed=False)
            start_point = (seg.end.x, seg.end.y)
            current = [start_point]
        elif isinstance(seg, Close):
            if current:
                current.append((seg.end.x, seg.end.y))
            flush(closed=True)
        elif isinstance(seg, Line):
            current.append((seg.end.x, seg.end.y))
        else:
            # Curved segment (CubicBezier, QuadraticBezier, Arc, ...):
            # sample adaptively based on arc length vs. tolerance.
            length = seg.length(error=tolerance)
            steps = max(2, int((length / max(tolerance, 1e-9)) ** 0.5) + 1)
            steps = min(steps, 500)
            for i in range(1, steps + 1):
                t = i / steps
                pt = seg.point(t)
                current.append((pt.x, pt.y))

    flush(closed=False)
    return subpaths


def _closed_points(sp: Subpath) -> list[tuple[float, float]]:
    pts = sp.points
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    return pts


def to_polygon(sp: Subpath) -> Polygon | None:
    pts = _closed_points(sp)
    if len(pts) < 4:  # need at least 3 distinct points + closing point
        return None
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = make_valid(poly)
        if poly.geom_type != "Polygon":
            return None
    if poly.is_empty or poly.area == 0:
        return None
    return poly


def compute_depths(polygons: list[Polygon | None]) -> list[int]:
    """For each polygon, count how many *other* polygons in the list contain
    it (nesting depth under the even-odd rule). None entries get depth 0
    and are otherwise ignored.
    """
    n = len(polygons)
    depths = [0] * n

    for i in range(n):
        if polygons[i] is None:
            continue
        depth = 0
        for j in range(n):
            if i == j or polygons[j] is None:
                continue
            # Full-polygon containment, not just a point: a small descendant
            # polygon can easily contain polygon i's representative point by
            # coincidence (e.g. concentric nested rectangles), which would
            # wrongly count as "j contains i".
            if polygons[j].area > polygons[i].area and polygons[j].contains(polygons[i]):
                depth += 1
        depths[i] = depth
    return depths


@dataclass
class RectFit:
    """How well a polygon fits an oriented rectangle, and that rectangle's
    parameters (all in the polygon's own coordinate space)."""

    ratio: float  # poly.area / rectangle area; 1.0 = perfect rectangle
    center: tuple[float, float]
    long_axis: tuple[float, float]  # unit vector
    short_axis: tuple[float, float]  # unit vector
    long_len: float
    short_len: float


def fit_rectangle(poly: Polygon) -> RectFit | None:
    mrr = poly.minimum_rotated_rectangle
    if mrr.geom_type != "Polygon":
        return None
    coords = list(mrr.exterior.coords)[:-1]
    if len(coords) != 4:
        return None

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1])

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def unit(v):
        length = math.hypot(*v)
        return (v[0] / length, v[1] / length) if length > 0 else (0.0, 0.0)

    e1 = dist(coords[0], coords[1])
    e2 = dist(coords[1], coords[2])
    rect_area = e1 * e2
    if rect_area <= 0:
        return None
    ratio = poly.area / rect_area
    v1 = unit(sub(coords[1], coords[0]))
    v2 = unit(sub(coords[2], coords[1]))
    cx = sum(c[0] for c in coords) / 4
    cy = sum(c[1] for c in coords) / 4
    if e1 >= e2:
        long_axis, long_len, short_axis, short_len = v1, e1, v2, e2
    else:
        long_axis, long_len, short_axis, short_len = v2, e2, v1, e1
    return RectFit(
        ratio=ratio, center=(cx, cy),
        long_axis=long_axis, short_axis=short_axis,
        long_len=long_len, short_len=short_len,
    )


def rectangle_corners(rect: RectFit) -> list[tuple[float, float]]:
    cx, cy = rect.center
    lx, ly = rect.long_axis
    sx, sy = rect.short_axis
    hl, hw = rect.long_len / 2, rect.short_len / 2
    corners = []
    for sl, sw in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        corners.append((cx + sl * hl * lx + sw * hw * sx, cy + sl * hl * ly + sw * hw * sy))
    return corners


def offset_rectangle_one_axis(rect: RectFit, axis: str, distance: float) -> list[tuple[float, float]] | None:
    """Grow/shrink only the given axis ('long' or 'short') by `distance` per
    end (so the total change in that dimension is 2*distance); the other
    axis is left completely unchanged. Used for joints where one dimension
    is fixed at the material thickness and must not be kerf-corrected."""
    long_len, short_len = rect.long_len, rect.short_len
    if axis == "long":
        long_len += 2 * distance
    else:
        short_len += 2 * distance
    if long_len <= 0 or short_len <= 0:
        return None
    new_rect = RectFit(
        ratio=1.0, center=rect.center,
        long_axis=rect.long_axis, short_axis=rect.short_axis,
        long_len=long_len, short_len=short_len,
    )
    return rectangle_corners(new_rect)


@dataclass
class Edge:
    """One straight segment of a subpath. `vertex_index` is the position of
    this segment's *endpoint* within the subpath's raw segment list -- i.e.
    the segment whose `.end` must be mutated to move this edge's end
    vertex. The edge's *start* vertex is `vertex_index - 1`'s segment end
    (or the leading Move if vertex_index == 1)."""

    start: tuple[float, float]
    end: tuple[float, float]
    vertex_index: int


def extract_straight_edges(segments: list) -> list[Edge]:
    """Pull out Line/Close edges from a subpath's raw segments (Move first).
    Curves are skipped entirely -- a kerf joint is always a straight wall."""
    edges = []
    prev_end = None
    for i, seg in enumerate(segments):
        end = (seg.end.x, seg.end.y)
        if isinstance(seg, Move):
            prev_end = end
            continue
        if isinstance(seg, (Line, Close)) and prev_end is not None:
            edges.append(Edge(start=prev_end, end=end, vertex_index=i))
        prev_end = end
    return edges


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _scale(a, s):
    return (a[0] * s, a[1] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _unit(v):
    length = math.hypot(*v)
    return (v[0] / length, v[1] / length) if length > 0 else (0.0, 0.0)


def edge_length(edge: Edge) -> float:
    return math.hypot(*_sub(edge.end, edge.start))


@dataclass
class PairMatch:
    gap: float  # perpendicular distance between the two edge lines (unsigned)
    signed_gap: float  # same, signed: positive means edge_b is on the `normal` side of edge_a
    axis: tuple[float, float]  # unit vector along edge_a's direction
    normal: tuple[float, float]  # unit vector perpendicular to axis, positive-gap side
    origin: tuple[float, float]  # point on edge_a's line used as the projection origin (edge_a.start)
    overlap_lo: float  # overlap interval, projected onto axis, relative to `origin`
    overlap_hi: float

    def point_on_a(self, s: float) -> tuple[float, float]:
        return _add(self.origin, _scale(self.axis, s))

    def point_on_b(self, s: float) -> tuple[float, float]:
        return _add(self.point_on_a(s), _scale(self.normal, self.signed_gap))

    def render_rect(self) -> list[tuple[float, float]]:
        return [
            self.point_on_a(self.overlap_lo), self.point_on_a(self.overlap_hi),
            self.point_on_b(self.overlap_hi), self.point_on_b(self.overlap_lo),
        ]


def match_parallel_edges(
    edge_a: Edge, edge_b: Edge,
    target_gap: float, gap_tol: float,
    angle_tol_cos: float = 0.03,  # ~ sin of the max allowed angle deviation
    min_overlap: float = 0.0,
) -> PairMatch | None:
    """Check whether two edges are parallel, `target_gap` +/- `gap_tol`
    apart, and overlap by at least `min_overlap` when projected onto their
    shared axis. Returns None if not a match."""
    va = _sub(edge_a.end, edge_a.start)
    vb = _sub(edge_b.end, edge_b.start)
    la, lb = math.hypot(*va), math.hypot(*vb)
    if la < 1e-9 or lb < 1e-9:
        return None
    dir_a, dir_b = _unit(va), _unit(vb)
    if abs(_cross(dir_a, dir_b)) > angle_tol_cos:
        return None  # not parallel enough

    normal = (-dir_a[1], dir_a[0])
    gap = _dot(_sub(edge_b.start, edge_a.start), normal)
    # average the perpendicular offset of both of B's endpoints for robustness
    gap_end = _dot(_sub(edge_b.end, edge_a.start), normal)
    gap = (gap + gap_end) / 2
    if abs(abs(gap) - target_gap) > gap_tol:
        return None

    def proj(pt):
        return _dot(_sub(pt, edge_a.start), dir_a)

    a_lo, a_hi = 0.0, proj(edge_a.end)
    if a_lo > a_hi:
        a_lo, a_hi = a_hi, a_lo
    b_lo, b_hi = sorted([proj(edge_b.start), proj(edge_b.end)])

    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    if hi - lo < min_overlap:
        return None

    return PairMatch(
        gap=abs(gap), signed_gap=gap, axis=dir_a, normal=normal,
        origin=edge_a.start, overlap_lo=lo, overlap_hi=hi,
    )


def bucket_edges(edges: list[Edge], cell_size: float) -> dict:
    """Spatial grid over edge midpoints, for pruning O(n^2) pair search."""
    grid: dict[tuple[int, int], list[int]] = {}
    for idx, e in enumerate(edges):
        mx, my = (e.start[0] + e.end[0]) / 2, (e.start[1] + e.end[1]) / 2
        key = (int(mx // cell_size), int(my // cell_size))
        grid.setdefault(key, []).append(idx)
    return grid


def nearby_edge_indices(edges: list[Edge], grid: dict, cell_size: float, idx: int):
    e = edges[idx]
    mx, my = (e.start[0] + e.end[0]) / 2, (e.start[1] + e.end[1]) / 2
    cx, cy = int(mx // cell_size), int(my // cell_size)
    seen = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in grid.get((cx + dx, cy + dy), ()):
                if j > idx:
                    seen.add(j)
    return seen


def polygon_signed_area(coords: list[tuple[float, float]]) -> float:
    """Shoelace signed area. Sign (not magnitude) encodes winding direction,
    consistently, regardless of whether the polygon is convex or has
    concave dents -- this is what makes `edge_outward_normal` correct for
    a notch's cap edge, where a naive "away from centroid" heuristic gives
    the wrong answer."""
    s = 0.0
    n = len(coords)
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2


def edge_outward_normal(poly_signed_area: float, direction: tuple[float, float]) -> tuple[float, float]:
    """Outward-facing unit normal for an edge running along `direction`,
    within a polygon of the given signed area. Correct for both convex
    boundary edges and concave (notch) edges, since it's a purely local
    rule tied to winding direction rather than any global shape heuristic."""
    dx, dy = direction
    normal = (dy, -dx) if poly_signed_area > 0 else (-dy, dx)
    return _unit(normal)


def polygon_to_points(poly: Polygon) -> list[tuple[float, float]]:
    coords = list(poly.exterior.coords)
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def points_to_d(loops: list[list[tuple[float, float]]], precision: int = 4) -> str:
    parts = []
    for loop in loops:
        if not loop:
            continue
        fmt = lambda v: f"{v:.{precision}f}".rstrip("0").rstrip(".")
        pts = [f"{fmt(x)},{fmt(y)}" for x, y in loop]
        parts.append("M" + " L".join(pts) + " Z")
    return " ".join(parts)
