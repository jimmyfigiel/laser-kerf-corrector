import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lxml import etree
from shapely.geometry import Polygon

from kerfcorrector import cli, geometry, svgio, joints

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.svg")
JOINTS_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "joints_sample.svg")


# ---------------------------------------------------------------------------
# Matrix composition (guards against the ancestor/own transform order bug)
# ---------------------------------------------------------------------------

def test_nested_transform_composition():
    svg = etree.fromstring(
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<g transform="translate(10,0)">'
        b'<g transform="scale(2,2)">'
        b'<rect id="r" x="3" y="4" width="1" height="1"/>'
        b'</g></g></svg>'
    )
    rect = svg.find(".//{http://www.w3.org/2000/svg}rect")
    m = svgio.element_transform(rect)
    # local (3,4) -> inner scale(2,2) -> (6,8) -> outer translate(10,0) -> (16,8)
    root_pt = m.point_in_matrix_space((3, 4))
    assert abs(root_pt.x - 16) < 1e-9
    assert abs(root_pt.y - 8) < 1e-9
    # inverse must round-trip
    back = m.inverse().point_in_matrix_space(root_pt)
    assert abs(back.x - 3) < 1e-9
    assert abs(back.y - 4) < 1e-9


# ---------------------------------------------------------------------------
# Nesting depth
# ---------------------------------------------------------------------------

def test_compute_depths_three_levels():
    outer = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)])
    middle = Polygon([(5, 5), (25, 5), (25, 25), (5, 25)])
    inner = Polygon([(10, 10), (20, 10), (20, 20), (10, 20)])
    depths = geometry.compute_depths([outer, middle, inner])
    assert depths == [0, 1, 2]


def test_compute_depths_siblings_are_independent():
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])
    depths = geometry.compute_depths([a, b])
    assert depths == [0, 0]


# ---------------------------------------------------------------------------
# Unit scale detection
# ---------------------------------------------------------------------------

def test_detect_scale_mm():
    root = etree.fromstring(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="850mm" viewBox="0 0 85000 60000"/>'
    )
    scale, _ = svgio._detect_scale(root)
    assert abs(scale - 100.0) < 1e-9


def test_parse_length_mm_units():
    assert abs(svgio.parse_length_mm("10mm") - 10.0) < 1e-9
    assert abs(svgio.parse_length_mm("1cm") - 10.0) < 1e-9
    assert abs(svgio.parse_length_mm("1in") - 25.4) < 1e-9
    assert svgio.parse_length_mm(None) is None


# ---------------------------------------------------------------------------
# Element selection
# ---------------------------------------------------------------------------

def test_selection_excludes_filled_shapes(tmp_path):
    doc = svgio.load(FIXTURE)
    elems = cli.select_elements(doc, None, include_fill=False)
    ids = {e.get("id") for e in elems}
    assert ids == {"plate", "slot", "tag", "nested"}
    assert "logo" not in ids


# ---------------------------------------------------------------------------
# Selective joint correction (joints.py): HOLE / EDGE detection
#
# The guiding principle: every corrected feature ends up at exactly its own
# drawn size after cutting. This falls out of one uniform rule -- every
# member edge of a feature shifts by kerf/2 along its own outward normal,
# sign from the feature's own nesting depth parity -- so a dimension's
# magnitude of change (half vs. full kerf) is just a consequence of how
# many of its own boundary walls are actual member edges of the feature,
# not something hand-coded per kind. `kind` itself is purely a review-GUI
# label (HOLE for a standalone removed-material subpath, worth a careful
# look; EDGE for everything else, since a tab/notch/plain-wall misclassified
# as each other is geometrically harmless) -- `is_container` is the one
# piece of detection metadata that still matters functionally, marking the
# single leftover-boundary entry each subpath gets so the GUI's fold-back
# logic can find it.
# ---------------------------------------------------------------------------

def _feature_by_id(elements, features, elem_id, subpath_index=0, kind=None):
    idx = next(i for i, e in enumerate(elements) if e.get("id") == elem_id)
    return next(
        f for f in features
        if f.element_index == idx and f.subpath_index == subpath_index and (kind is None or f.kind == kind)
    )


def test_find_features_whole_subpath_hole_and_edge():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    # max_feature_mm=18 keeps "multi"'s own 20mm outer boundary out of this
    # (it represents "the rest of the design", not a feature to correct).
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    joint = _feature_by_id(elements, features, "multi", subpath_index=1)
    assert joint.kind == "hole"  # nested inside multi's outer boundary -> odd depth
    assert joint.is_closed_loop  # whole subpath -> genuinely closed loop
    assert abs(joint.short_mm - 3.0) < 1e-6
    assert abs(joint.long_mm - 12.0) < 1e-6

    lone = _feature_by_id(elements, features, "lone")
    assert lone.kind == "edge"  # standalone, not nested -> even depth (solid tab)
    assert lone.is_closed_loop  # whole subpath -> genuinely closed loop, not a window
    assert abs(lone.short_mm - 13.0) < 1e-6
    assert abs(lone.long_mm - 13.0) < 1e-6

    standalone_tab = _feature_by_id(elements, features, "tab")
    assert standalone_tab.kind == "edge"
    assert standalone_tab.is_closed_loop
    assert abs(standalone_tab.short_mm - 3.0) < 1e-6
    assert abs(standalone_tab.long_mm - 12.0) < 1e-6


def test_find_features_embedded_notch_is_edge():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    notch = _feature_by_id(elements, features, "edgenotch")
    assert notch.kind == "edge"
    assert not notch.is_container
    # An open run along the boundary, not a closed loop -- there's no real
    # edge closing its last point back to its first (see kerf_tool.py's
    # shapeTag(), which uses this to render a <polyline>, not a <polygon>,
    # so the review GUI doesn't draw a highlighted "cut line" that was
    # never actually cut).
    assert not notch.is_closed_loop
    assert abs(notch.short_mm - 3.0) < 1e-6
    assert abs(notch.long_mm - 10.0) < 1e-6
    assert len(notch.member_edges) == 3  # single cap: entry wall, cap, exit wall


def test_find_features_container_covers_leftover_edges():
    # The notch's own 3 edges are claimed by the EDGE feature above; every
    # other edge of "edgenotch"'s outer silhouette belongs to no detected
    # joint at all, but still needs the standard kerf offset or the panel
    # comes out undersized -- that's exactly what the CONTAINER feature is
    # for. Both entries share kind "edge" (only `is_container` tells them
    # apart), since which one is which makes no difference to correction.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    on_this_subpath = [f for f in features if f.element_index == idx and f.subpath_index == 0]
    assert len(on_this_subpath) == 2  # the notch, plus the leftover container
    assert {f.kind for f in on_this_subpath} == {"edge"}
    assert sorted(f.is_container for f in on_this_subpath) == [False, True]

    notch = next(f for f in on_this_subpath if not f.is_container)
    container = next(f for f in on_this_subpath if f.is_container)
    assert not notch.is_closed_loop  # open run along the boundary
    assert container.is_closed_loop  # the subpath's own full closed outline
    info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)
    period = len(info.root_segments) - 1
    assert len(container.member_edges) == period - len(notch.member_edges)
    assert set(container.member_edges).isdisjoint(notch.member_edges)


def test_find_features_container_for_plain_panel_with_no_joints():
    # "multi" subpath 0 is a plain 20x15mm rectangle with no notches or
    # tabs of its own -- it should still come back as one whole-perimeter
    # EDGE feature marked `is_container` (not silently skipped) so it
    # still gets corrected.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    container = _feature_by_id(elements, features, "multi", subpath_index=0)
    assert container.kind == "edge"
    assert container.is_container
    assert container.is_closed_loop
    assert len(container.member_edges) == 4  # all four sides of the rectangle


def test_find_features_rotated_shape_uses_fitted_size_not_axis_aligned_bbox():
    # "rotated-diamond" is a 45-degree-rotated square: its axis-aligned
    # bounding box is 20x20mm, but its own true side length is ~14.14mm.
    # The whole-small-subpath size check must compare max_feature_mm
    # against the fitted (rotated) rectangle's size, not the inflated
    # axis-aligned bbox -- otherwise this genuinely-small diamond gets
    # kicked into the windowed search and mislabeled "edge" instead of
    # "hole", purely as a function of its rotation angle. Real-world
    # finding: this is exactly what happened to several diamond cutouts in
    # a decorative lattice panel, inconsistently labeled hole vs. boundary
    # (now edge) depending only on incidental rotation, not actual size.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    # max_feature_mm=15: under the true ~14.14mm side length, but well
    # under the inflated 20mm axis-aligned bbox too -- so this only passes
    # if the check is comparing against the fitted size.
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=15.0)

    diamond = _feature_by_id(elements, features, "rotated-diamond")
    assert diamond.kind == "hole"
    assert abs(diamond.short_mm - 14.142) < 0.01
    assert abs(diamond.long_mm - 14.142) < 0.01
    assert len(diamond.member_edges) == 4  # whole subpath, not a windowed/boundary fragment


def test_find_features_step_style_edge():
    # Regression test for the construction style the original edge-pair
    # detector could not find at all: one wall simply interrupted, stepping
    # out then back in, rather than two full-length parallel walls joined by
    # a perpendicular cap. The edge immediately before and after the window
    # are parallel to each other, which is what the windowed search keys on.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    step = _feature_by_id(elements, features, "stepboundary")
    assert step.kind == "edge"
    assert not step.is_container
    assert not step.is_closed_loop
    assert abs(step.short_mm - 3.0) < 1e-6
    assert abs(step.long_mm - 10.0) < 1e-6
    assert step.member_edges == [3, 4, 5]


# ---------------------------------------------------------------------------
# Manually adding a feature the auto-detector missed (joints.custom_feature)
# ---------------------------------------------------------------------------

def test_custom_feature_recovers_a_known_notch():
    # A user clicking the two outer corners of "edgenotch"'s notch (vertex 1
    # at (400,600) and vertex 4 at (430,600)) should recover exactly the
    # same feature find_features already detects automatically for it --
    # custom_feature just skips the parallel-neighbor prefilter that a
    # corner-adjacent joint (this test's real motivation) would fail.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)

    feature = joints.custom_feature(info, doc.scale_user_units_per_mm, (400, 600), (430, 600))
    assert feature is not None
    assert feature.kind == "edge"
    assert not feature.is_closed_loop
    assert feature.member_edges == [2, 3, 4]
    assert abs(feature.short_mm - 3.0) < 1e-6
    assert abs(feature.long_mm - 10.0) < 1e-6


def test_custom_feature_click_order_is_invariant():
    # Clicking near vertex 4 first and vertex 1 second (reversed order) must
    # give the IDENTICAL member_edges, not just the same edge set -- a naive
    # implementation can silently reverse the traced point sequence and
    # produce a differently-ordered (or wrongly-sized) result depending
    # purely on click order. This is a regression test for exactly that bug.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)

    forward = joints.custom_feature(info, doc.scale_user_units_per_mm, (400, 600), (430, 600))
    reversed_ = joints.custom_feature(info, doc.scale_user_units_per_mm, (430, 600), (400, 600))
    assert forward.member_edges == reversed_.member_edges == [2, 3, 4]
    assert forward.kind == reversed_.kind == "edge"
    assert abs(forward.short_mm - 3.0) < 1e-6
    assert abs(forward.long_mm - 10.0) < 1e-6


def test_custom_feature_same_point_twice_returns_none():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)

    assert joints.custom_feature(info, doc.scale_user_units_per_mm, (400, 600), (401, 601)) is None


def test_apply_manifest_hole_both_dims_shrink_full_kerf(tmp_path):
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    joint = _feature_by_id(elements, features, "multi", subpath_index=1)

    manifest = joints.to_payload([joint])
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 1
    assert not stats.warnings

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == joint.element_index and i.subpath_index == 1)
    minx, miny, maxx, maxy = after.poly.bounds
    scale = doc.scale_user_units_per_mm
    # A standalone hole: all four walls are independent cut edges, so both
    # dimensions narrow by the full kerf (both opposing walls move half each).
    # The fixture's "multi" joint spans 12mm in x (long) and 3mm in y (short).
    assert abs((maxx - minx) / scale - 11.0) < 1e-6  # 12.0 - 1.0
    assert abs((maxy - miny) / scale - 2.0) < 1e-6   # 3.0 - 1.0


def test_apply_manifest_edge_both_dims_grow_full_kerf(tmp_path):
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    lone = _feature_by_id(elements, features, "lone")

    manifest = joints.to_payload([lone])
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == lone.element_index and i.subpath_index == 0)
    minx, miny, maxx, maxy = after.poly.bounds
    scale = doc.scale_user_units_per_mm
    assert abs((maxx - minx) / scale - 14.0) < 1e-6  # 13.0 + 1.0
    assert abs((maxy - miny) / scale - 14.0) < 1e-6


def test_apply_manifest_embedded_notch_asymmetric_kerf(tmp_path):
    # A slot capped at only one end: its two walls are both independent
    # cuts (width narrows by the full kerf), but its length has only one
    # real cut line closing it off -- the other end is just the rest of
    # the boundary -- so length only narrows by half the kerf.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    notch = _feature_by_id(elements, features, "edgenotch")

    manifest = joints.to_payload([notch])
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    features2 = joints.find_features(infos2, doc2.scale_user_units_per_mm, max_feature_mm=18.0)
    after = _feature_by_id(els2, features2, "edgenotch")
    assert abs(after.short_mm - 2.0) < 1e-6   # 3.0 - 1.0 (full kerf, both walls)
    assert abs(after.long_mm - 9.5) < 1e-6    # 10.0 - 0.5 (half kerf, one cap)


def test_apply_manifest_step_edge_asymmetric_kerf(tmp_path):
    # The mirror image of the embedded-slot case: a step-style tab has
    # only one wall that's a member edge (its own outer face), so width
    # grows by half the kerf, while both perpendicular caps ARE members,
    # so length grows by the full kerf.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    step = _feature_by_id(elements, features, "stepboundary")

    manifest = joints.to_payload([step])
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    features2 = joints.find_features(infos2, doc2.scale_user_units_per_mm, max_feature_mm=18.0)
    after = _feature_by_id(els2, features2, "stepboundary")
    assert abs(after.short_mm - 3.5) < 1e-6   # 3.0 + 0.5 (half kerf, one wall)
    assert abs(after.long_mm - 11.0) < 1e-6   # 10.0 + 1.0 (full kerf, both caps)


def test_apply_manifest_container_grows_outer_silhouette_by_full_kerf(tmp_path):
    # This is what motivated the leftover CONTAINER feature: correcting
    # only the detected notch left "edgenotch"'s own outer silhouette
    # untouched, so the finished panel would come out undersized by a full
    # kerf even though its one joint was corrected perfectly. Applying both
    # the notch AND the container feature (as the review GUI does by
    # default, since every auto-detected feature defaults to selected)
    # should grow the panel's overall footprint by the full kerf on both
    # dimensions, exactly like whole-file mode does for a plain boundary.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    on_this_subpath = [f for f in features if f.element_index == idx and f.subpath_index == 0]

    scale = doc.scale_user_units_per_mm
    before_info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)
    minx, miny, maxx, maxy = before_info.poly.bounds
    original_w, original_h = (maxx - minx) / scale, (maxy - miny) / scale

    manifest = joints.to_payload(on_this_subpath)
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 2
    assert not stats.warnings

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after_info = next(i for i in infos2 if i.element_index == idx and i.subpath_index == 0)
    minx2, miny2, maxx2, maxy2 = after_info.poly.bounds
    new_w, new_h = (maxx2 - minx2) / scale, (maxy2 - miny2) / scale

    assert abs(new_w - (original_w + 1.0)) < 1e-6
    assert abs(new_h - (original_h + 1.0)) < 1e-6


def test_apply_manifest_regression_untouched_subpath_position(tmp_path):
    # Regression test: correcting one subpath inside a multi-subpath element
    # must not move any other subpath in that element. This specifically
    # covers a real bug found via manual testing where svgelements Path.d()
    # re-serialized an extracted subpath's leading relative moveto with
    # absolute-magnitude coordinates but kept the lowercase (relative)
    # command letter, silently displacing every subpath that followed a
    # corrected one in the same element.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)

    multi_infos = sorted([i for i in infos if i.element_index == 0], key=lambda i: i.subpath_index)
    assert len(multi_infos) == 3
    original_poly = multi_infos[2].poly  # subpath 2: unrelated to the joint
    assert original_poly is not None

    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    joint = _feature_by_id(elements, features, "multi", subpath_index=1)
    manifest = joints.to_payload([joint])

    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 1
    assert not stats.warnings

    doc2 = svgio.load(out_path)
    elements2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, elements2, tolerance_mm=0.02)
    multi_infos2 = sorted([i for i in infos2 if i.element_index == 0], key=lambda i: i.subpath_index)

    untouched_after = multi_infos2[2].poly
    assert untouched_after is not None
    assert original_poly.equals_exact(untouched_after, tolerance=1e-6)

    joint_before = multi_infos[1].poly
    joint_after = multi_infos2[1].poly
    assert not joint_before.equals_exact(joint_after, tolerance=1e-6)


def test_apply_manifest_leaves_unlisted_elements_untouched(tmp_path):
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    lone_idx = next(i for i, e in enumerate(elements) if e.get("id") == "lone")

    manifest = [{"element_index": lone_idx, "subpath_index": 0, "kind": "edge", "member_edges": [1, 2, 3, 4]}]
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=0.2, tolerance_mm=0.02)
    svgio.save(doc, out_path)
    assert stats.corrected == 1
    assert stats.elements_touched == 1

    orig_root = etree.parse(JOINTS_FIXTURE).getroot()
    new_root = etree.parse(out_path).getroot()
    orig_multi_d = orig_root.find(".//{http://www.w3.org/2000/svg}path[@id='multi']").get("d")
    new_multi_d = new_root.find(".//{http://www.w3.org/2000/svg}path[@id='multi']").get("d")
    assert orig_multi_d == new_multi_d


# ---------------------------------------------------------------------------
# mortice / tenon / teeth / slot: extra clearance + tip chamfering
#
# All four kinds get the exact same depth-parity-based kerf shift as
# hole/edge -- `kind` still isn't what decides the sign or magnitude of that
# part. What's new is a SECOND nudge (extra clearance) layered on top, plus
# optional corner chamfering, both driven by user-chosen mm values rather
# than derived from the kerf number -- see apply_manifest's docstring for why
# an attached feature's own length axis has zero net kerf sensitivity and so
# needs this manual escape hatch to compensate for a miscalibrated kerf value
# at all. tenon/teeth are SOLID (clearance shrinks them); mortice/slot are
# VOIDS (clearance instead ENLARGES them) -- the opposite sign is the whole
# point of the mortice/slot tests below, since naively reusing the
# tenon/teeth formula on a void would tighten it instead of loosening it.
# ---------------------------------------------------------------------------

def test_apply_manifest_tenon_clearance_matches_plain_edge_when_zero(tmp_path):
    # tenon with clearance=0 must be numerically identical to plain "edge"
    # (its base kind before this session's classifications existed) -- the
    # extra term should vanish, not merely become negligible.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    lone = _feature_by_id(elements, features, "lone")

    manifest = joints.to_payload([lone])
    manifest[0]["kind"] = "tenon"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=1.0, tenon_clearance_mm=0.0, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1
    assert not stats.warnings

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == lone.element_index and i.subpath_index == 0)
    minx, miny, maxx, maxy = after.poly.bounds
    scale = doc.scale_user_units_per_mm
    assert abs((maxx - minx) / scale - 14.0) < 1e-6  # same as plain edge: 13.0 + 1.0
    assert abs((maxy - miny) / scale - 14.0) < 1e-6


def test_apply_manifest_tenon_extra_clearance_shrinks_both_dims_further(tmp_path):
    # A standalone closed loop has 4 independent walls, so extra clearance
    # (unlike kerf-only) pulls EVERY wall inward regardless of depth parity --
    # both dimensions shrink by the full clearance value relative to the
    # kerf-only (grown) size, same as they're fully kerf-sensitive.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    lone = _feature_by_id(elements, features, "lone")

    manifest = joints.to_payload([lone])
    manifest[0]["kind"] = "tenon"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=1.0, tenon_clearance_mm=0.4, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == lone.element_index and i.subpath_index == 0)
    minx, miny, maxx, maxy = after.poly.bounds
    scale = doc.scale_user_units_per_mm
    # kerf-only would give 14.0 (see test above); the extra 0.4mm clearance
    # shrinks both dims by a further full 0.4mm (2 walls x 0.2mm) SINCE
    # tenon is solid -- clearance subtracts.
    assert abs((maxx - minx) / scale - 13.6) < 1e-6  # 13.0 + 1.0 - 0.4
    assert abs((maxy - miny) / scale - 13.6) < 1e-6


def test_apply_manifest_mortice_extra_clearance_enlarges_both_dims(tmp_path):
    # The mirror of tenon: a mortice is a VOID (the socket a tenon plugs
    # into), so extra clearance must ENLARGE it relative to the kerf-only
    # size, not shrink it -- reusing tenon's subtract-clearance formula here
    # would tighten the socket instead of loosening it, the opposite of what
    # "give this joint more clearance" means for a hole.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    joint = _feature_by_id(elements, features, "multi", subpath_index=1)
    assert joint.kind == "hole"

    manifest = joints.to_payload([joint])
    manifest[0]["kind"] = "mortice"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=1.0, mortice_clearance_mm=0.4, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == joint.element_index and i.subpath_index == 1)
    minx, miny, maxx, maxy = after.poly.bounds
    scale = doc.scale_user_units_per_mm
    # kerf-only alone gives 11.0 / 2.0 (see test_apply_manifest_hole_both_
    # dims_shrink_full_kerf). The 0.4mm mortice clearance ENLARGES both dims
    # by a further full 0.4mm (2 walls x 0.2mm) -- opposite sign from tenon.
    assert abs((maxx - minx) / scale - 11.4) < 1e-6  # 12.0 - 1.0 + 0.4
    assert abs((maxy - miny) / scale - 2.4) < 1e-6    # 3.0 - 1.0 + 0.4


def test_apply_manifest_teeth_extra_clearance_is_asymmetric(tmp_path):
    # "stepboundary" is a windowed (attached) feature with only ONE
    # independent wall on its short axis but TWO independent caps on its
    # long axis (see test_apply_manifest_step_edge_asymmetric_kerf for the
    # kerf-only baseline). Extra clearance nudges every member edge inward
    # by the same half-clearance amount regardless of which axis it's on, so
    # the single-wall axis loses half the clearance value while the two-cap
    # axis loses the full clearance value (half from each cap).
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    step = _feature_by_id(elements, features, "stepboundary")

    manifest = joints.to_payload([step])
    manifest[0]["kind"] = "teeth"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=1.0, teeth_clearance_mm=0.4, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    features2 = joints.find_features(infos2, doc2.scale_user_units_per_mm, max_feature_mm=18.0)
    after = _feature_by_id(els2, features2, "stepboundary")
    assert abs(after.short_mm - 3.3) < 1e-6   # 3.0 + 0.5 (kerf) - 0.2 (half clearance, one wall)
    assert abs(after.long_mm - 10.6) < 1e-6   # 10.0 + 1.0 (kerf) - 0.4 (half clearance x 2 caps)


def test_apply_manifest_windowed_slot_extra_clearance_enlarges_asymmetrically(tmp_path):
    # Regression test for a real bug found against an actual finger-jointed
    # test file: a WINDOWED void (a notch/slot cut into a bigger boundary,
    # e.g. "edgenotch" here) does NOT use the same clearance sign as a
    # standalone/closed-loop void like a mortice hole. A windowed void's
    # member edges share the surrounding boundary's own winding rather than
    # having an independently-wound loop of their own, so their outward
    # normal points INTO the shared cavity -- pushing two opposing notch
    # walls further into a cavity they both bound moves them TOWARD each
    # other, not apart. Using the closed-loop sign here would silently
    # TIGHTEN the notch when the user asked for more clearance -- exactly
    # backwards. See _extra_clearance_sign's docstring for the full
    # derivation. The half/full sensitivity split (from
    # test_apply_manifest_embedded_notch_asymmetric_kerf's kerf-only
    # baseline) still applies on top of the correct sign.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    notch = _feature_by_id(elements, features, "edgenotch")
    assert not notch.is_closed_loop

    manifest = joints.to_payload([notch])
    manifest[0]["kind"] = "slot"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=1.0, slot_clearance_mm=0.4, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    features2 = joints.find_features(infos2, doc2.scale_user_units_per_mm, max_feature_mm=18.0)
    after = _feature_by_id(els2, features2, "edgenotch")
    # kerf-only baseline (see test_apply_manifest_embedded_notch_asymmetric_
    # kerf): short 3.0 -> 2.0 (shrinks a full kerf, both walls independent),
    # long 10.0 -> 9.5 (shrinks half a kerf, one cap independent). 0.4mm
    # slot clearance must ENLARGE both relative to that kerf-only shrink --
    # by the full clearance value on the two-wall axis, half on the one-cap
    # axis -- not shrink them further.
    assert abs(after.short_mm - 2.4) < 1e-6   # 2.0 + 0.4 (full clearance, two walls)
    assert abs(after.long_mm - 9.7) < 1e-6    # 9.5 + 0.2 (half clearance, one cap)


def test_apply_manifest_closed_loop_vs_windowed_void_use_opposite_clearance_sign(tmp_path):
    # The core regression case in one test: the SAME slot_clearance_mm value
    # must move a closed-loop void (mortice-shaped) and a windowed void
    # (notch-shaped) in OPPOSITE directions relative to their own kerf-only
    # baseline -- both "enlarge" in their own frame, but that means opposite
    # signs in the shared per-edge-outward-normal mechanism. If both moved
    # the same way, one of them would be tightening instead of loosening.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    closed_hole = _feature_by_id(elements, features, "multi", subpath_index=1)
    windowed_notch = _feature_by_id(elements, features, "edgenotch")
    assert closed_hole.is_closed_loop
    assert not windowed_notch.is_closed_loop

    def corrected_short_mm(feature, elem_id, subpath_index, clearance_mm):
        doc_ = svgio.load(JOINTS_FIXTURE)
        elements_ = cli.select_elements(doc_, None, include_fill=False)
        manifest = joints.to_payload([feature])
        manifest[0]["kind"] = "slot"
        stats = joints.apply_manifest(
            doc_, elements_, manifest, kerf_mm=1.0, slot_clearance_mm=clearance_mm, tolerance_mm=0.02,
        )
        assert stats.corrected == 1
        out_path = str(tmp_path / f"out_{elem_id}_{clearance_mm}.svg")
        svgio.save(doc_, out_path)
        doc2 = svgio.load(out_path)
        els2 = cli.select_elements(doc2, None, include_fill=False)
        infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
        features2 = joints.find_features(infos2, doc2.scale_user_units_per_mm, max_feature_mm=18.0)
        after = _feature_by_id(els2, features2, elem_id, subpath_index=subpath_index)
        return after.short_mm

    hole_kerf_only = corrected_short_mm(closed_hole, "multi", 1, 0.0)
    hole_with_clearance = corrected_short_mm(closed_hole, "multi", 1, 0.4)
    notch_kerf_only = corrected_short_mm(windowed_notch, "edgenotch", 0, 0.0)
    notch_with_clearance = corrected_short_mm(windowed_notch, "edgenotch", 0, 0.4)

    assert hole_with_clearance > hole_kerf_only      # closed-loop void: enlarges
    assert notch_with_clearance > notch_kerf_only    # windowed void: also enlarges


def test_apply_manifest_chamfer_adds_two_points_per_corner(tmp_path):
    # A standalone closed-loop rectangle has all 4 corners as chamfer
    # targets (including the tricky wraparound corner where the loop's
    # Close meets its Move) -- each corner becomes 2 points, so a 4-vertex
    # rectangle comes out as an 8-vertex octagon.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    lone = _feature_by_id(elements, features, "lone")

    manifest = joints.to_payload([lone])
    manifest[0]["kind"] = "tenon"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=0.2, chamfer_mm=0.3, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1
    assert not stats.warnings

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == lone.element_index and i.subpath_index == 0)
    # shapely's exterior.coords repeats the first point as the last, so
    # 8 unique vertices show up as 9 coordinates.
    assert len(after.poly.exterior.coords) == 9


def test_apply_manifest_chamfer_does_not_apply_to_mortice(tmp_path):
    # A mortice -- the receiving socket side of a joint -- must come out
    # untouched by a nonzero chamfer_mm. Chamfering eases a protruding tip
    # into its socket, so it's only meaningful (and only implemented) for
    # the solid tenon side.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    joint = _feature_by_id(elements, features, "multi", subpath_index=1)
    assert joint.kind == "hole"

    manifest = joints.to_payload([joint])
    manifest[0]["kind"] = "mortice"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=0.2, chamfer_mm=0.3, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == joint.element_index and i.subpath_index == 1)
    assert len(after.poly.exterior.coords) == 5  # still a plain 4-vertex rectangle


def test_apply_manifest_chamfer_does_not_apply_to_teeth(tmp_path):
    # Deliberately different from tenon: chamfering is tenon-only. A finger
    # joint's teeth must come out with sharp (unchamfered) corners even with
    # chamfer_mm set, since a nonzero chamfer_mm alone shouldn't silently
    # reshape a kind that isn't supposed to have one.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)
    step = _feature_by_id(elements, features, "stepboundary")
    # Captured before apply_manifest mutates `doc` -- chamfering is the only
    # thing that changes a polygon's own VERTEX COUNT (plain kerf correction
    # just moves existing vertices), so comparing counts before vs. after is
    # a direct chamfer-happened-or-not check.
    before = next(i for i in infos if i.element_index == step.element_index and i.subpath_index == step.subpath_index)
    before_count = len(before.poly.exterior.coords)

    manifest = joints.to_payload([step])
    manifest[0]["kind"] = "teeth"
    out_path = str(tmp_path / "out.svg")
    stats = joints.apply_manifest(
        doc, elements, manifest, kerf_mm=0.2, chamfer_mm=0.3, tolerance_mm=0.02,
    )
    svgio.save(doc, out_path)
    assert stats.corrected == 1

    doc2 = svgio.load(out_path)
    els2 = cli.select_elements(doc2, None, include_fill=False)
    infos2 = joints.analyze(doc2, els2, tolerance_mm=0.02)
    after = next(i for i in infos2 if i.element_index == step.element_index and i.subpath_index == step.subpath_index)
    assert len(after.poly.exterior.coords) == before_count


def test_apply_manifest_unknown_kind_is_skipped():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    manifest = [{"element_index": 0, "subpath_index": 1, "kind": "bogus", "member_edges": [1, 2, 3, 4]}]
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    assert stats.corrected == 0
    assert any("unknown kind" in w for w in stats.warnings)


def test_apply_manifest_missing_member_edges_is_skipped():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    manifest = [{"element_index": 0, "subpath_index": 1, "kind": "hole", "member_edges": []}]
    stats = joints.apply_manifest(doc, elements, manifest, kerf_mm=1.0, tolerance_mm=0.02)
    assert stats.corrected == 0
    assert any("no member edges" in w for w in stats.warnings)
