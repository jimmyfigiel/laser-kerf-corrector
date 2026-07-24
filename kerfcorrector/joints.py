"""Selective, feature-aware kerf correction for interlocking joints.

Two-step workflow (see review_joints.py / apply_joints.py, or app.py for the
combined GUI):

1. Auto-detect HOLE / EDGE features and let a human verify/adjust/add them
   in a browser GUI. The result is a small JSON manifest naming exactly
   which edges to correct.
2. Apply the manifest: only the specific vertices belonging to an accepted
   feature are moved; every other vertex -- including the rest of a much
   bigger boundary a joint happens to be embedded in -- is re-emitted from
   its original segments untouched, curves included.

The guiding principle is simple: every corrected piece should come out at
*exactly its own drawn dimensions* after cutting. Every member edge of
every feature shifts by kerf/2 along its own outward normal, sign taken
from the feature's own nesting depth parity (inward/shrink if material is
removed -- odd depth -- outward/grow if solid -- even depth). That's the
whole correction rule: it applies identically no matter what kind of
feature the edge belongs to, so there's no special-casing between a
feature's two dimensions, or between features of different kinds.

Because of that, a feature's *kind* is purely a human-facing label for the
review GUI -- it plays no part in the correction math. Only two kinds are
exposed:

- HOLE: a closed subpath whose own bounding box is small is treated as one
  whole feature, and flagged HOLE if its nesting depth is odd (material
  removed -- an actual hole in the finished part). This is the one case
  worth double-checking in review, since a missed or misplaced hole is
  visibly wrong; everything else below gets identical treatment regardless
  of its exact shape.
- EDGE: everything else -- a standalone small subpath with even depth (a
  free-standing tab), a local excursion (bump or notch) found by sliding a
  window of 1..N consecutive edges around a bigger boundary looking for a
  parallel-walled run, and the plain leftover walls of that boundary once
  every excursion is claimed. All three are corrected the same offset, so
  splitting them into separate labels added review-screen detail (tab vs.
  slot vs. boundary) without changing any output geometry.

Detection still runs the same three passes internally (whole small
subpath, windowed excursion, leftover boundary) since that's what actually
finds the edges to correct -- `kind` just collapses the result down to
HOLE / EDGE afterward. One implementation detail survives into the
payload: `is_container` marks the single leftover-boundary entry each
subpath gets (if any), since a windowed EDGE feature that's ignored needs
to fold its edges back into that specific sibling or the finished part
gets a gap right where the ignored feature was (see kerf_tool.py's
`boundarySiblingIdx`).

Outward/inward direction is derived from each subpath's own winding order
(`geometry.polygon_signed_area` / `edge_outward_normal`), not a centroid
heuristic -- centroid-based "outward" is wrong for a concave feature like
a notch, where the true outward direction points back toward the opening,
not deeper into the material.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from shapely.geometry import Polygon
from svgelements import Path, Move, Close, Line, Point

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
# Feature detection: HOLE / EDGE
# ---------------------------------------------------------------------------

@dataclass
class Feature:
    element_index: int
    subpath_index: int
    kind: str  # 'hole' | 'edge'
    member_edges: list[int]  # vertex_index values of edges to correct
    short_mm: float
    long_mm: float
    render_rect: list[tuple[float, float]]  # root space, for GUI
    is_container: bool = False  # the subpath's own leftover-boundary entry, if any
    # Whether render_rect is a genuinely closed loop (whole-subpath and
    # leftover-container features: render_rect is the subpath's own full
    # closed polygon) vs. an open run of points along part of a bigger
    # boundary (windowed-excursion and custom-added features: render_rect
    # stops wherever the search/click-pair did, with no real edge closing
    # the last point back to the first). The GUI needs this to know
    # whether to render a closed <polygon> or an open <polyline> -- closing
    # an open run would draw a highlighted "cut line" that was never
    # actually cut.
    is_closed_loop: bool = True


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
    """Detect HOLE/EDGE candidates. See module docstring for the three
    detection passes (whole small subpath, windowed excursion, leftover)."""
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

        # Use the fitted (possibly rotated) rectangle's own long side for the
        # size check, not the axis-aligned bounding box -- a diamond or any
        # shape rotated off-axis has an axis-aligned bbox inflated well past
        # its true size (up to sqrt(2) worse at 45 degrees), which would
        # otherwise kick genuinely small rotated holes/tabs out of this
        # branch and into the windowed search, where they just become an
        # undifferentiated "boundary" despite being well within the cap.
        whole_rect = geometry.fit_rectangle(info.poly)
        whole_size_mm = whole_rect.long_len / scale if whole_rect else max(whole_w_mm, whole_h_mm)

        if whole_size_mm <= max_feature_mm:
            kind = "hole" if info.depth % 2 == 1 else "edge"
            member_edges = list(range(1, period + 1))
            if whole_rect:
                short_mm, long_mm = whole_rect.short_len / scale, whole_rect.long_len / scale
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

            consumed.update(window_edges)
            features.append(Feature(
                info.element_index, info.subpath_index, "edge", window_edges,
                short_mm, long_mm, pts,
                is_closed_loop=False,
            ))

        leftover = sorted(set(range(1, period + 1)) - consumed)
        if leftover:
            rect = geometry.fit_rectangle(info.poly)
            if rect:
                short_mm, long_mm = rect.short_len / scale, rect.long_len / scale
            else:
                short_mm, long_mm = min(whole_w_mm, whole_h_mm), max(whole_w_mm, whole_h_mm)
            features.append(Feature(
                info.element_index, info.subpath_index, "edge", leftover,
                short_mm, long_mm, geometry.polygon_to_points(info.poly),
                is_container=True,
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
    # direction (ascending cyclic index), whichever click came first -- this
    # is what keeps member_edges invariant to click order.
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

    # A manually-added feature is, by construction, a windowed excursion on
    # an existing boundary (never the whole-subpath case) -- always EDGE,
    # and its points are an open run just like the auto-detected windowed
    # case, not a closed loop.
    return Feature(info.element_index, info.subpath_index, "edge", window_edges, short_mm, long_mm, pts,
                    is_closed_loop=False)


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
            "is_container": f.is_container,
            "is_closed_loop": f.is_closed_loop,
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


#: Kinds that get pulled in extra beyond pure kerf correction, for an easier
#: fit -- see apply_manifest's docstring for why this needs to be a
#: manually-chosen kind rather than something the kerf number alone can fix.
#: Maps to its own settings-panel parameter name and whether it's a VOID
#: kind (mortice/slot) rather than a SOLID one (tenon/teeth) -- see
#: _extra_clearance_sign for why "void" alone isn't enough to know the sign.
_EXTRA_CLEARANCE = {
    "tenon": ("tenon_clearance_mm", False),
    "teeth": ("teeth_clearance_mm", False),
    "mortice": ("mortice_clearance_mm", True),
    "slot": ("slot_clearance_mm", True),
}


def _extra_clearance_sign(is_void: bool, is_closed_loop: bool) -> int:
    """The sign that makes "more clearance" actually mean "more room" for
    this specific feature, along the same per-edge-outward-normal mechanism
    kerf itself uses.

    SOLID kinds (tenon/teeth) always use -1 (pull the tab's own edges back
    toward its interior -- shrink it -- regardless of whether it's a
    standalone tab or attached to a boundary): a protruding tab's own
    outward normal always points further out into open space, the same
    physical direction, whichever structural case it is.

    VOID kinds (mortice/slot) are NOT that simple, and get this wrong if
    treated the same way for both structural cases:
    - A CLOSED-LOOP void (a standalone hole/mortice) has its OWN,
      independently-wound boundary, separate from anything it might be
      nested inside -- its outward normal points away from ITS OWN void
      interior, so +1 (push further outward) enlarges it, mirroring how -1
      shrinks a closed-loop solid tab.
    - A WINDOWED void (a notch/slot cut into a bigger boundary) has no
      separate winding of its own -- its member edges are just part of the
      SAME boundary loop as the solid material around them, so their
      outward normal points AWAY FROM THAT SOLID, i.e. into the notch's own
      cavity. Two opposing notch walls pushed further into their SHARED
      cavity move TOWARD each other, not apart -- so +1 here would
      CONVERGE (shrink) the notch, the opposite of "more clearance".
      -1 is what actually enlarges it.
    Verified against a real finger-jointed test file where a windowed slot
    treated with the closed-loop sign silently got *tighter* with more
    clearance requested -- exactly backwards."""
    if not is_void:
        return -1
    return 1 if is_closed_loop else -1


def apply_manifest(
    doc: svgio.Document,
    elements,
    manifest: list[dict],
    kerf_mm: float,
    tenon_clearance_mm: float = 0.0,
    teeth_clearance_mm: float = 0.0,
    mortice_clearance_mm: float = 0.0,
    slot_clearance_mm: float = 0.0,
    chamfer_mm: float = 0.0,
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
    of the whole feature.

    `mortice`/`tenon`/`teeth`/`slot` are the exceptions to "kind is purely
    cosmetic" -- the two-sided vocabulary for interlocking joints, on top of
    the plain `hole`/`edge` labels: a TENON is the tab that plugs into a
    MORTICE's socket; TEETH are a finger/comb joint's individual tabs; a
    SLOT is a sliding-fit channel (e.g. a dado a panel slides into), which
    can show up either as its own enclosed cutout or as a channel open at a
    boundary's edge. What all four share: a feature that's part of a bigger
    boundary (attached on one side, only cut on the other -- e.g. the tip of
    a tab sticking out to plug into a hole elsewhere) has a length axis that
    gets *no* net correction from kerf at all, since the shared/attached
    side and the cut side move together and cancel (this is real geometry,
    not a bug -- verified by hand and numerically). That means the kerf
    number alone can't loosen a too-tight fit on that axis, however
    precisely it's calibrated; these four kinds each let you dial in an
    *additional* clearance, independent of kerf, since a tenon, a repeating
    finger tooth, a mortice socket, and a sliding slot usually all want
    different amounts. It's applied through the exact same
    per-edge-outward-normal mechanism as kerf itself, just as an
    independently-configurable second term -- so a standalone feature's two
    independent width walls get the full clearance value (same as kerf
    would), while an attached feature's single independently-cut length wall
    only gets half of it, mirroring kerf's own asymmetry between those two
    cases.

    tenon/teeth are SOLID (the clearance shrinks them, same direction as
    "make the tab a bit smaller for an easier fit"); mortice/slot are VOIDS
    (the clearance instead enlarges them -- a socket or channel needs to
    grow, not shrink, for an easier fit around a fixed-size mate). For a
    void, WHICH SIGN achieves that depends on structure, not just kind: a
    standalone mortice (its own closed loop, independently wound) enlarges
    with the same sign that shrinks a standalone tenon; a windowed slot cut
    into a bigger boundary shares that boundary's own winding instead, so
    its member edges' outward normals point INTO the shared cavity -- and
    two opposing notch walls pushed further into a cavity they both bound
    move toward each other, not apart. Getting this wrong doesn't just
    undershoot: it silently tightens a windowed void exactly when the user
    asked for it to loosen. See `_extra_clearance_sign`.

    `chamfer_mm` clips a 45-degree-ish lead-in off each tip corner of a
    tenon feature (all corners for a standalone tab, just the two tip
    corners -- not the ones shared with the surrounding boundary -- for an
    attached one), easing insertion. Deliberately tenon-only: not applied to
    teeth or to mortice/slot (the receiving side)."""
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
        kind = entry.get("kind")
        if kind not in ("hole", "edge", "mortice", "tenon", "teeth", "slot"):
            stats.warnings.append(f"{label}: unknown kind {kind!r}, skipped")
            continue
        member_edges = entry.get("member_edges") or []
        if not member_edges:
            stats.warnings.append(f"{label}: no member edges, skipped")
            continue

        clearance_lookup = {
            "tenon_clearance_mm": tenon_clearance_mm,
            "teeth_clearance_mm": teeth_clearance_mm,
            "mortice_clearance_mm": mortice_clearance_mm,
            "slot_clearance_mm": slot_clearance_mm,
        }
        extra_param, is_void_kind = _EXTRA_CLEARANCE.get(kind, (None, False))
        extra_mm = clearance_lookup.get(extra_param, 0.0)
        half_extra = (extra_mm / 2.0) * scale
        period = len(info.root_segments) - 1
        is_closed_loop = len(member_edges) == period
        extra_sign = _extra_clearance_sign(is_void_kind, is_closed_loop)
        # Extra clearance always pulls the edge further along its OWN
        # solid-vs-void direction than pure kerf correction would, regardless
        # of whether this subpath's own depth parity means kerf is growing or
        # shrinking it -- "give this joint a bit more room" is the same
        # instruction either way. See _extra_clearance_sign for why that
        # direction depends on structure (closed-loop vs. windowed), not
        # just solid-vs-void, for the two VOID kinds.
        distance = (half_kerf if info.depth % 2 == 0 else -half_kerf) + extra_sign * half_extra
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

    # --- chamfering: clip a lead-in off each tenon tip corner (not teeth) ---
    # chamfer_points[key][vertex_index] = (point_A_root, point_B_root_or_None),
    # the new point(s) that replace that corner. Computed from the FINAL
    # (already kerf+clearance-shifted) neighbouring points, above, not the
    # pre-correction ones, so the cut lines up with the actual edges the
    # laser will follow. A closed-loop feature (a standalone tab, whole
    # subpath) gets every corner chamfered; a windowed one (attached to a
    # bigger boundary) only gets its own interior corners -- not the two
    # endpoints shared with that boundary, which must stay put or the
    # boundary would gap. "Closed loop" is derived from member_edges
    # covering the whole period, not trusted from the manifest, since
    # that's a structural fact apply_manifest can check for itself.
    #
    # The vertex where the loop closes (index == period) needs special
    # handling: it's the one corner whose "outgoing" edge is the Move at
    # index 0, not a Line -- and a Close segment ignores whatever `.end` is
    # set on it, always snapping back to wherever the Move itself points
    # (that's also why the existing coincident-vertex mirroring above works
    # at all). So for that corner, point_B isn't a new trailing segment
    # (there's nothing to draw after a Close) -- it's what the Move at
    # index 0 needs to become instead, tracked in move_overrides.
    chamfer_points: dict[tuple[int, int], dict[int, tuple[tuple[float, float], tuple[float, float] | None]]] = {}
    move_overrides: dict[tuple[int, int], tuple[float, float]] = {}
    if chamfer_mm > 0:
        chamfer_dist = chamfer_mm * scale
        for entry in manifest:
            if entry.get("kind") != "tenon":
                continue
            key = (entry["element_index"], entry["subpath_index"])
            info = info_by_key.get(key)
            member_edges = entry.get("member_edges") or []
            if info is None or not member_edges:
                continue
            segs = info.root_segments
            period = len(segs) - 1
            is_closed = len(member_edges) == period
            targets = list(range(1, period + 1)) if is_closed else member_edges[:-1]
            if not targets:
                continue

            shifts = vertex_shifts.get(key, {})

            def final_pt(idx):
                base = segs[idx].end
                dx, dy = shifts.get(idx, (0.0, 0.0))
                return (base.x + dx, base.y + dy)

            for v in targets:
                p_prev = final_pt(_cyclic_add(v, -1, period))
                p_v = final_pt(v)
                p_next = final_pt(_cyclic_add(v, 1, period))
                in_dir = geometry._unit(geometry._sub(p_v, p_prev))
                out_dir = geometry._unit(geometry._sub(p_next, p_v))
                point_a = geometry._sub(p_v, geometry._scale(in_dir, chamfer_dist))
                point_b = geometry._add(p_v, geometry._scale(out_dir, chamfer_dist))
                if v == period:
                    chamfer_points.setdefault(key, {})[v] = (point_a, None)
                    move_overrides[key] = point_b
                else:
                    chamfer_points.setdefault(key, {})[v] = (point_a, point_b)

    for key, shifts in vertex_shifts.items():
        info = info_by_key[key]
        inv = info.transform.inverse()
        entry_chamfers = chamfer_points.get(key, {})
        move_pt = move_overrides.get(key)
        period = len(info.root_segments) - 1
        new_local = []
        for i, (lseg, rseg) in enumerate(zip(info.local_segments, info.root_segments)):
            if i == 0 and move_pt is not None:
                m_local = inv.point_in_matrix_space(move_pt)
                seg_copy = copy.copy(lseg)
                seg_copy.end = Point(m_local[0], m_local[1])
                new_local.append(seg_copy)
            elif i in entry_chamfers and i == period:
                # The Close segment ignores whatever `.end` we set on it,
                # always snapping back to wherever the Move points (already
                # redirected to point_B above) -- so instead of touching
                # Close itself, insert a plain line reaching point_A right
                # before it, and let Close's normal behavior draw the
                # A->point_B diagonal that finishes the chamfer.
                point_a, _ = entry_chamfers[i]
                a_local = inv.point_in_matrix_space(point_a)
                new_local.append(Line(end=Point(a_local[0], a_local[1])))
                new_local.append(lseg)
            elif i in entry_chamfers:
                point_a, point_b = entry_chamfers[i]
                a_local = inv.point_in_matrix_space(point_a)
                seg_copy = copy.copy(lseg)
                seg_copy.end = Point(a_local[0], a_local[1])
                new_local.append(seg_copy)
                if point_b is not None:
                    b_local = inv.point_in_matrix_space(point_b)
                    new_local.append(Line(end=Point(b_local[0], b_local[1])))
            elif i in shifts:
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
