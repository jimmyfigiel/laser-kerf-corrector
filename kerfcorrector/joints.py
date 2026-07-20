"""Selective, feature-aware kerf correction for interlocking joints.

Two-step workflow (see review_joints.py / apply_joints.py, or app.py for the
combined GUI):

1. Auto-detect HOLE / SLOT / TAB features and let a human verify/adjust/add
   them in a browser GUI. The result is a small JSON manifest naming exactly
   which edges to correct.
2. Apply the manifest: only the specific vertices belonging to an accepted
   feature are moved; every other vertex -- including the rest of a much
   bigger boundary a joint happens to be embedded in -- is re-emitted from
   its original segments untouched, curves included.

The guiding principle is simple: every corrected piece should come out at
*exactly its own drawn dimensions* after cutting. Solid material (a TAB)
loses width to the kerf, so it's drawn oversized by half the kerf on every
edge. Removed material (a HOLE or a SLOT notched into a boundary) gains
size from the kerf, so it's drawn undersized by half the kerf on every
edge. Both are the same operation -- offset every edge of the feature
outward or inward by kerf/2, sign taken from the feature's own nesting
depth parity -- so there's no special-casing between them, or between a
feature's two dimensions. (An earlier version of this tool tried to leave
one "material thickness" dimension untouched; that turned out to be wrong
in general and has been removed. Every dimension is corrected to match
what's drawn.)

Detection has three cases:

- HOLE / standalone TAB: a closed subpath whose own bounding box is small
  is treated as one whole feature -- HOLE if its nesting depth is odd
  (material removed), TAB if even (solid, e.g. a free-standing key/tab
  shape not attached to anything else in the file).
- SLOT / embedded TAB: for a big boundary (like a panel's outer
  silhouette), slide a window of 1..N consecutive edges around it. Where
  the edge immediately before the window and the edge immediately after
  it are parallel to each other, the window is a local excursion from an
  otherwise-straight run -- fit a rectangle to it, and if the size is
  plausible, it's a feature: TAB if it bulges outward (convex, adds
  material), SLOT if it dents inward (concave, removes material). This
  works whether the excursion is drawn as two long parallel walls with a
  short perpendicular cap (common in CorelDraw-style exports) or as a
  simple orthogonal step (common in Inkscape-style exports) -- both are
  just "a window bounded by parallel edges" as far as this search cares.
- BOUNDARY: a big boundary's own edges that aren't claimed by any SLOT/TAB
  above -- e.g. the plain, joint-free walls of a panel's outer silhouette.
  Left uncorrected, these edges would leave the finished part undersized
  (or a big hole oversized) by the kerf even though every joint on it was
  handled correctly, since "correct every joint" and "correct the part's
  own overall size" are separate concerns. This is the same offset every
  other feature gets -- half the kerf along each edge's own outward
  normal, sign from the subpath's own nesting depth -- just applied to
  whatever's left of the loop, so a plain rectangular panel with no joints
  at all still comes out to size.

Outward/inward direction is derived from each subpath's own winding order
(`geometry.polygon_signed_area` / `edge_outward_normal`), not a centroid
heuristic -- centroid-based "outward" is wrong for a concave feature like
a slot, where the true outward direction points back toward the opening,
not deeper into the material.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from shapely.geometry import Polygon
from svgelements import Path, Move, Close, Point

from . import svgio, geometry


@dataclass
class SubpathInfo:
    element_index: int
    subpath_index: int
    elem: object
    transform: object
    local_segments: list
    root_segments: list
    closed: bool
    poly: object = None  # shapely Polygon in root space, or None
    depth: int = 0
    signed_area: float = 0.0


def _split_into_subpaths(path: Path) -> list[list]:
    groups = []
    current: list = []
    for seg in path.segments():
        if isinstance(seg, Move):
            if current:
                groups.append(current)
            current = [seg]
        else:
            current.append(seg)
    if current:
        groups.append(current)
    return groups


def _is_closed(segments) -> bool:
    return any(isinstance(s, Close) for s in segments)


def _segments_to_d(segments: list) -> str:
    """Serialize an extracted subpath's segments back to a 'd' fragment.

    svgelements' Path.d() has a bug for a subpath pulled out of a larger
    path: it keeps the leading Move's original lowercase/relative flag but
    prints its *absolute* end coordinates verbatim, producing a fragment
    that -- when re-parsed as relative-to-whatever-precedes-it, which is
    exactly what happens once fragments are concatenated -- lands in the
    wrong place. Rebuilding the leading Move fresh (no inherited `start`)
    forces it to serialize as an unambiguous absolute 'M'.
    """
    if segments and isinstance(segments[0], Move):
        segments = [Move(end=segments[0].end)] + segments[1:]
    return Path(segments).d()


def analyze(doc: svgio.Document, elements, tolerance_mm: float) -> list[SubpathInfo]:
    """Split every selected element into its subpaths (kept as raw,
    unflattened segments in both local and root space), flatten just enough
    to build polygons for nesting-depth analysis and winding direction."""
    scale = doc.scale_user_units_per_mm
    tolerance_root = tolerance_mm * scale

    infos: list[SubpathInfo] = []
    for idx, elem in enumerate(elements):
        transform = svgio.element_transform(elem)
        local_path = svgio.element_to_path(elem)
        if local_path is None:
            continue
        root_path = local_path * transform
        local_groups = _split_into_subpaths(local_path)
        root_groups = _split_into_subpaths(root_path)
        for sp_i, (lseg, rseg) in enumerate(zip(local_groups, root_groups)):
            infos.append(SubpathInfo(
                element_index=idx, subpath_index=sp_i, elem=elem, transform=transform,
                local_segments=lseg, root_segments=rseg, closed=_is_closed(rseg),
            ))

    polys = []
    for info in infos:
        if not info.closed:
            polys.append(None)
            continue
        subpaths = geometry.flatten_path(Path(info.root_segments), tolerance_root)
        polys.append(geometry.to_polygon(subpaths[0]) if subpaths else None)

    depths = geometry.compute_depths(polys)
    for info, poly, depth in zip(infos, polys, depths):
        info.poly = poly
        info.depth = depth
        if poly is not None:
            info.signed_area = geometry.polygon_signed_area(geometry.polygon_to_points(poly))

    return infos


# ---------------------------------------------------------------------------
# Feature detection: HOLE / SLOT / TAB
# ---------------------------------------------------------------------------

@dataclass
class Feature:
    element_index: int
    subpath_index: int
    kind: str  # 'hole' | 'slot' | 'tab' | 'boundary'
    member_edges: list[int]  # vertex_index values of edges to correct
    short_mm: float
    long_mm: float
    render_rect: list[tuple[float, float]]  # root space, for GUI


def _cyclic_add(v: int, k: int, period: int) -> int:
    return ((v - 1 + k) % period) + 1


def _edge_direction(segs, vertex_index) -> tuple[float, float]:
    p0 = (segs[vertex_index - 1].end.x, segs[vertex_index - 1].end.y)
    p1 = (segs[vertex_index].end.x, segs[vertex_index].end.y)
    return geometry._unit(geometry._sub(p1, p0))


def _window_points(segs, v: int, length: int, period: int) -> list[tuple[float, float]]:
    """Positions of vertices v, v+1, ..., v+length (inclusive), cyclically."""
    pts = []
    cur = v
    for _ in range(length + 1):
        pts.append((segs[cur].end.x, segs[cur].end.y))
        cur = _cyclic_add(cur, 1, period)
    return pts


def find_features(
    infos: list[SubpathInfo],
    scale: float,
    min_feature_mm: float = 0.3,
    max_feature_mm: float = 20.0,
    max_window_edges: int = 4,
    min_rect_ratio: float = 0.7,
    angle_tol_cos: float = 0.05,
) -> list[Feature]:
    """Detect HOLE/SLOT/TAB candidates. See module docstring for the two
    detection cases (whole small subpath vs. windowed excursion)."""
    features: list[Feature] = []

    for info in infos:
        if not info.closed or info.poly is None:
            continue
        segs = info.root_segments
        period = len(segs) - 1
        if period < 3:
            continue

        minx, miny, maxx, maxy = info.poly.bounds
        whole_w_mm = (maxx - minx) / scale
        whole_h_mm = (maxy - miny) / scale

        if max(whole_w_mm, whole_h_mm) <= max_feature_mm:
            kind = "hole" if info.depth % 2 == 1 else "tab"
            member_edges = list(range(1, period + 1))
            rect = geometry.fit_rectangle(info.poly)
            if rect:
                short_mm, long_mm = rect.short_len / scale, rect.long_len / scale
            else:
                short_mm, long_mm = min(whole_w_mm, whole_h_mm), max(whole_w_mm, whole_h_mm)
            features.append(Feature(
                info.element_index, info.subpath_index, kind, member_edges,
                short_mm, long_mm, geometry.polygon_to_points(info.poly),
            ))
            continue

        consumed: set[int] = set()
        for v in range(1, period + 1):
            if v in consumed:
                continue
            prev_dir = _edge_direction(segs, v)
            match = None
            for length in range(1, max_window_edges + 1):
                window_edges = [_cyclic_add(v, k, period) for k in range(1, length + 1)]
                if any(e in consumed for e in window_edges):
                    break
                next_vertex = _cyclic_add(v, length + 1, period)
                if next_vertex == v:
                    break  # window wrapped the whole loop
                next_dir = _edge_direction(segs, next_vertex)
                if abs(geometry._cross(prev_dir, next_dir)) > angle_tol_cos:
                    continue

                pts = _window_points(segs, v, length, period)
                try:
                    wpoly = Polygon(pts + [pts[0]])
                except Exception:
                    continue
                if not wpoly.is_valid or wpoly.area == 0:
                    continue
                rect = geometry.fit_rectangle(wpoly)
                if rect is None or rect.ratio < min_rect_ratio:
                    continue
                short_mm, long_mm = rect.short_len / scale, rect.long_len / scale
                if short_mm < min_feature_mm or long_mm > max_feature_mm:
                    continue
                match = (window_edges, short_mm, long_mm, pts)
                break  # prefer the shortest valid window

            if match is None:
                continue
            window_edges, short_mm, long_mm, pts = match

            # Classify convex (tab, bulges outward) vs concave (slot, dents
            # inward): compare the window's own interior points to the
            # straight line that would connect its two boundary points.
            baseline_dir = geometry._unit(geometry._sub(pts[-1], pts[0]))
            outward = geometry.edge_outward_normal(info.signed_area, baseline_dir)
            interior = pts[1:-1] or pts
            avg = (sum(p[0] for p in interior) / len(interior), sum(p[1] for p in interior) / len(interior))
            to_avg = geometry._sub(avg, pts[0])
            kind = "tab" if geometry._dot(outward, to_avg) > 0 else "slot"

            consumed.update(window_edges)
            features.append(Feature(
                info.element_index, info.subpath_index, kind, window_edges,
                short_mm, long_mm, pts,
            ))

        leftover = sorted(set(range(1, period + 1)) - consumed)
        if leftover:
            rect = geometry.fit_rectangle(info.poly)
            if rect:
                short_mm, long_mm = rect.short_len / scale, rect.long_len / scale
            else:
                short_mm, long_mm = min(whole_w_mm, whole_h_mm), max(whole_w_mm, whole_h_mm)
            features.append(Feature(
                info.element_index, info.subpath_index, "boundary", leftover,
                short_mm, long_mm, geometry.polygon_to_points(info.poly),
            ))

    return features


def custom_feature(
    info: SubpathInfo,
    scale: float,
    p1: tuple[float, float],
    p2: tuple[float, float],
    min_rect_ratio: float = 0.3,
) -> Feature | None:
    """Build a Feature from two points a user clicked on a subpath's own
    outline, for joints the auto-detector's windowed search misses -- most
    commonly a finger-joint tooth positioned right at a corner, where the
    edges immediately before/after it aren't parallel to each other and so
    fail that search's core assumption. Each point snaps to its nearest
    vertex; the shorter of the two arcs between them becomes the feature's
    member edges. Returns None if the points don't resolve to a sane
    rectangle (e.g. both snapped to the same vertex, or the arc is
    degenerate)."""
    if not info.closed or info.poly is None:
        return None
    segs = info.root_segments
    period = len(segs) - 1
    if period < 3:
        return None

    def nearest_vertex(p):
        best_v, best_d = None, None
        for v in range(1, period + 1):
            vp = (segs[v].end.x, segs[v].end.y)
            d = (vp[0] - p[0]) ** 2 + (vp[1] - p[1]) ** 2
            if best_d is None or d < best_d:
                best_v, best_d = v, d
        return best_v

    v1, v2 = nearest_vertex(p1), nearest_vertex(p2)
    if v1 is None or v2 is None or v1 == v2:
        return None

    def vertex_seq(direction):
        seq, v = [v1], v1
        while v != v2:
            v = _cyclic_add(v, direction, period)
            seq.append(v)
        return seq

    forward, backward = vertex_seq(1), vertex_seq(-1)
    # Always end up with `seq` walking the polygon's own forward winding
    # direction (ascending cyclic index), whichever click came first -- the
    # tab/slot classification below depends on point order, and this is
    # what keeps it (and member_edges) invariant to click order.
    seq = forward if len(forward) <= len(backward) else list(reversed(backward))
    if len(seq) < 2 or len(seq) > period:
        return None

    # Edge k always connects vertex k-1 and vertex k, regardless of which
    # direction `seq` walks through it.
    window_edges = [b if _cyclic_add(a, 1, period) == b else a for a, b in zip(seq, seq[1:])]
    pts = [(segs[v].end.x, segs[v].end.y) for v in seq]
    try:
        wpoly = Polygon(pts + [pts[0]])
    except Exception:
        return None
    if not wpoly.is_valid or wpoly.area == 0:
        return None
    rect = geometry.fit_rectangle(wpoly)
    if rect is None or rect.ratio < min_rect_ratio:
        return None
    short_mm, long_mm = rect.short_len / scale, rect.long_len / scale

    baseline_dir = geometry._unit(geometry._sub(pts[-1], pts[0]))
    outward = geometry.edge_outward_normal(info.signed_area, baseline_dir)
    interior = pts[1:-1] or pts
    avg = (sum(p[0] for p in interior) / len(interior), sum(p[1] for p in interior) / len(interior))
    to_avg = geometry._sub(avg, pts[0])
    kind = "tab" if geometry._dot(outward, to_avg) > 0 else "slot"

    return Feature(info.element_index, info.subpath_index, kind, window_edges, short_mm, long_mm, pts)


def to_payload(features: list[Feature]) -> list[dict]:
    """JSON-able rendering/interaction payload for the review GUI. Points
    are left in the document's root user units (the SVG's own viewBox
    space) so the overlay aligns with the rendered SVG directly."""
    items = []
    for f in features:
        items.append({
            "element_index": f.element_index,
            "subpath_index": f.subpath_index,
            "kind": f.kind,
            "member_edges": list(f.member_edges),
            "short_mm": round(f.short_mm, 4),
            "long_mm": round(f.long_mm, 4),
            "points": [[round(x, 2), round(y, 2)] for x, y in f.render_rect],
        })
    return items


# ---------------------------------------------------------------------------
# Applying a reviewed manifest
# ---------------------------------------------------------------------------

@dataclass
class ApplyStats:
    total_in_manifest: int = 0
    corrected: int = 0
    elements_touched: int = 0
    warnings: list = field(default_factory=list)


def _edge_outward_normal(info: SubpathInfo, vertex_index: int) -> tuple[float, float]:
    """Outward-facing unit normal for the edge ending at `vertex_index`."""
    segs = info.root_segments
    p0 = (segs[vertex_index - 1].end.x, segs[vertex_index - 1].end.y)
    p1 = (segs[vertex_index].end.x, segs[vertex_index].end.y)
    direction = (p1[0] - p0[0], p1[1] - p0[1])
    return geometry.edge_outward_normal(info.signed_area, direction)


def apply_manifest(
    doc: svgio.Document,
    elements,
    manifest: list[dict],
    kerf_mm: float,
    tolerance_mm: float = 0.02,
) -> ApplyStats:
    """Apply a reviewed feature manifest in place on `doc`. Only the specific
    vertices belonging to an accepted feature move; everything else in every
    touched subpath -- including curves -- is re-emitted exactly as
    originally drawn.

    Every feature is corrected the same way regardless of kind: every one of
    its member edges shifts by kerf/2 along its own outward normal, sign
    taken from the subpath's nesting depth (outward/grow if solid material
    -- even depth -- inward/shrink if material is removed -- odd depth).
    Shared vertices between adjacent member edges naturally accumulate both
    edges' shifts, which is mathematically the same as a mitre-join offset
    of the whole feature."""
    scale = doc.scale_user_units_per_mm
    half_kerf = (kerf_mm / 2.0) * scale

    infos = analyze(doc, elements, tolerance_mm)
    info_by_key = {(i.element_index, i.subpath_index): i for i in infos}

    stats = ApplyStats(total_in_manifest=len(manifest))

    # vertex_shifts[(element_index, subpath_index)] = {vertex_index: [dx, dy]}
    vertex_shifts: dict[tuple[int, int], dict[int, list[float]]] = {}
    # edge_owners[key][edge_index] = {manifest entry indices} -- two adjacent
    # features sharing a seam VERTEX is expected (their shifts are meant to
    # sum, same as a mitre-join) and not warned about; only warn if the same
    # EDGE is independently claimed as a member edge by more than one entry,
    # since that's a genuine conflicting classification, not just adjacency.
    edge_owners: dict[tuple[int, int], dict[int, set]] = {}

    def add_shift(key, vertex_index, delta):
        shifts = vertex_shifts.setdefault(key, {})
        if vertex_index in shifts:
            shifts[vertex_index][0] += delta[0]
            shifts[vertex_index][1] += delta[1]
        else:
            shifts[vertex_index] = [delta[0], delta[1]]

    for entry_idx, entry in enumerate(manifest):
        key = (entry["element_index"], entry["subpath_index"])
        info = info_by_key.get(key)
        label = f"element/subpath {key} ({entry.get('kind')})"
        if info is None:
            stats.warnings.append(f"{label}: element/subpath not found, skipped")
            continue
        if entry.get("kind") not in ("hole", "slot", "tab", "boundary"):
            stats.warnings.append(f"{label}: unknown kind {entry.get('kind')!r}, skipped")
            continue
        member_edges = entry.get("member_edges") or []
        if not member_edges:
            stats.warnings.append(f"{label}: no member edges, skipped")
            continue

        distance = half_kerf if info.depth % 2 == 0 else -half_kerf
        for v in member_edges:
            edge_owners.setdefault(key, {}).setdefault(v, set()).add(entry_idx)
            normal = _edge_outward_normal(info, v)
            delta = geometry._scale(normal, distance)
            add_shift(key, v, delta)
            add_shift(key, v - 1, delta)
        stats.corrected += 1

    for key, per_edge in edge_owners.items():
        for v, owners in per_edge.items():
            if len(owners) > 1:
                stats.warnings.append(f"element/subpath {key} edge {v}: claimed as a member edge by "
                                       f"{len(owners)} different manifest entries, shifts were summed")

    # --- rebuild touched subpaths ---
    by_element: dict[int, list[SubpathInfo]] = {}
    for info in infos:
        by_element.setdefault(info.element_index, []).append(info)

    # Multiple vertex indices can be the *same physical point*: vertex 0
    # (the leading Move) and the last vertex (the closing Close) always
    # coincide, and some exporters (CorelDraw does this) additionally emit
    # a redundant explicit Line back to the start point right before the
    # Close, adding a third coincident index. A cap can reference any one
    # of these depending on which side of the loop it's on; whichever
    # ends up shifted must be mirrored onto every other vertex at that same
    # original position, or the loop stops actually closing.
    for key, shifts in vertex_shifts.items():
        info = info_by_key[key]
        segs = info.root_segments
        groups: dict[tuple[float, float], list[int]] = {}
        for i, seg in enumerate(segs):
            pos = (round(seg.end.x, 6), round(seg.end.y, 6))
            groups.setdefault(pos, []).append(i)
        for indices in groups.values():
            if len(indices) < 2:
                continue
            touched = [i for i in indices if i in shifts]
            if not touched:
                continue
            combined = [0.0, 0.0]
            for i in touched:
                combined[0] += shifts[i][0]
                combined[1] += shifts[i][1]
            for i in indices:
                shifts[i] = list(combined)

    for key, shifts in vertex_shifts.items():
        info = info_by_key[key]
        inv = info.transform.inverse()
        new_local = []
        for i, (lseg, rseg) in enumerate(zip(info.local_segments, info.root_segments)):
            if i in shifts:
                dx, dy = shifts[i]
                new_root = (rseg.end.x + dx, rseg.end.y + dy)
                new_local_pt = inv.point_in_matrix_space(new_root)
                seg_copy = copy.copy(lseg)
                seg_copy.end = Point(new_local_pt[0], new_local_pt[1])
                new_local.append(seg_copy)
            else:
                new_local.append(lseg)
        info.local_segments = new_local  # in case the same subpath is touched by later entries too

    elements_touched = set()
    for el_idx, subinfos in by_element.items():
        subinfos = sorted(subinfos, key=lambda i: i.subpath_index)
        if not any((i.element_index, i.subpath_index) in vertex_shifts for i in subinfos):
            continue
        fragments = [_segments_to_d(i.local_segments) for i in subinfos]
        svgio.replace_with_path(subinfos[0].elem, " ".join(fragments))
        elements_touched.add(el_idx)

    stats.elements_touched = len(elements_touched)
    return stats
