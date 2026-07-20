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
# Selective joint correction (joints.py): HOLE / SLOT / TAB detection
#
# The guiding principle: every corrected feature ends up at exactly its own
# drawn size after cutting. This falls out of one uniform rule -- every
# member edge of a feature shifts by kerf/2 along its own outward normal,
# sign from the feature's own nesting depth parity -- so a dimension's
# magnitude of change (half vs. full kerf) is just a consequence of how
# many of its own boundary walls are actual member edges of the feature,
# not something hand-coded per hole/slot/tab.
# ---------------------------------------------------------------------------

def _feature_by_id(elements, features, elem_id, subpath_index=0, kind=None):
    idx = next(i for i, e in enumerate(elements) if e.get("id") == elem_id)
    return next(
        f for f in features
        if f.element_index == idx and f.subpath_index == subpath_index and (kind is None or f.kind == kind)
    )


def test_find_features_whole_subpath_hole_and_tab():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    # max_feature_mm=18 keeps "multi"'s own 20mm outer boundary out of this
    # (it represents "the rest of the design", not a feature to correct).
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    joint = _feature_by_id(elements, features, "multi", subpath_index=1)
    assert joint.kind == "hole"  # nested inside multi's outer boundary -> odd depth
    assert abs(joint.short_mm - 3.0) < 1e-6
    assert abs(joint.long_mm - 12.0) < 1e-6

    lone = _feature_by_id(elements, features, "lone")
    assert lone.kind == "tab"  # standalone, not nested -> even depth
    assert abs(lone.short_mm - 13.0) < 1e-6
    assert abs(lone.long_mm - 13.0) < 1e-6

    standalone_tab = _feature_by_id(elements, features, "tab")
    assert standalone_tab.kind == "tab"
    assert abs(standalone_tab.short_mm - 3.0) < 1e-6
    assert abs(standalone_tab.long_mm - 12.0) < 1e-6


def test_find_features_embedded_slot():
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    notch = _feature_by_id(elements, features, "edgenotch")
    assert notch.kind == "slot"
    assert abs(notch.short_mm - 3.0) < 1e-6
    assert abs(notch.long_mm - 10.0) < 1e-6
    assert len(notch.member_edges) == 3  # single cap: entry wall, cap, exit wall


def test_find_features_boundary_covers_leftover_edges():
    # The notch's own 3 edges are claimed by the SLOT feature above; every
    # other edge of "edgenotch"'s outer silhouette belongs to no detected
    # joint at all, but still needs the standard kerf offset or the panel
    # comes out undersized -- that's exactly what the BOUNDARY feature is for.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    on_this_subpath = [f for f in features if f.element_index == idx and f.subpath_index == 0]
    assert len(on_this_subpath) == 2  # the slot, plus the leftover boundary
    assert {f.kind for f in on_this_subpath} == {"slot", "boundary"}

    slot = _feature_by_id(elements, features, "edgenotch", kind="slot")
    boundary = _feature_by_id(elements, features, "edgenotch", kind="boundary")
    info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)
    period = len(info.root_segments) - 1
    assert len(boundary.member_edges) == period - len(slot.member_edges)
    assert set(boundary.member_edges).isdisjoint(slot.member_edges)


def test_find_features_boundary_for_plain_panel_with_no_joints():
    # "multi" subpath 0 is a plain 20x15mm rectangle with no notches or
    # tabs of its own -- it should still come back as one whole-perimeter
    # BOUNDARY feature (not silently skipped) so it still gets corrected.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    features = joints.find_features(infos, doc.scale_user_units_per_mm, max_feature_mm=18.0)

    boundary = _feature_by_id(elements, features, "multi", subpath_index=0)
    assert boundary.kind == "boundary"
    assert len(boundary.member_edges) == 4  # all four sides of the rectangle


def test_find_features_step_style_tab():
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
    assert step.kind == "tab"  # bulges outward -> convex -> solid material
    assert abs(step.short_mm - 3.0) < 1e-6
    assert abs(step.long_mm - 10.0) < 1e-6
    assert step.member_edges == [3, 4, 5]


# ---------------------------------------------------------------------------
# Manually adding a feature the auto-detector missed (joints.custom_feature)
# ---------------------------------------------------------------------------

def test_custom_feature_recovers_a_known_slot():
    # A user clicking the two outer corners of "edgenotch"'s slot (vertex 1
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
    assert feature.kind == "slot"
    assert feature.member_edges == [2, 3, 4]
    assert abs(feature.short_mm - 3.0) < 1e-6
    assert abs(feature.long_mm - 10.0) < 1e-6


def test_custom_feature_click_order_is_invariant():
    # Clicking near vertex 4 first and vertex 1 second (reversed order) must
    # give the IDENTICAL result, not just the same edge set -- member_edges
    # order and (critically) the tab/slot classification both derive from
    # the traced point sequence's direction, so a naive implementation can
    # silently flip concave/convex depending purely on click order. This is
    # a regression test for exactly that bug.
    doc = svgio.load(JOINTS_FIXTURE)
    elements = cli.select_elements(doc, None, include_fill=False)
    infos = joints.analyze(doc, elements, tolerance_mm=0.02)
    idx = next(i for i, e in enumerate(elements) if e.get("id") == "edgenotch")
    info = next(i for i in infos if i.element_index == idx and i.subpath_index == 0)

    forward = joints.custom_feature(info, doc.scale_user_units_per_mm, (400, 600), (430, 600))
    reversed_ = joints.custom_feature(info, doc.scale_user_units_per_mm, (430, 600), (400, 600))
    assert forward.member_edges == reversed_.member_edges == [2, 3, 4]
    assert forward.kind == reversed_.kind == "slot"
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


def test_apply_manifest_tab_both_dims_grow_full_kerf(tmp_path):
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


def test_apply_manifest_embedded_slot_asymmetric_kerf(tmp_path):
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


def test_apply_manifest_step_tab_asymmetric_kerf(tmp_path):
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


def test_apply_manifest_boundary_grows_outer_silhouette_by_full_kerf(tmp_path):
    # This is what motivated the BOUNDARY feature: correcting only the
    # detected slot left "edgenotch"'s own outer silhouette untouched, so
    # the finished panel would come out undersized by a full kerf even
    # though its one joint was corrected perfectly. Applying both the slot
    # AND the boundary feature (as the review GUI does by default, since
    # BOUNDARY defaults to selected like any other auto-detected feature)
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

    manifest = [{"element_index": lone_idx, "subpath_index": 0, "kind": "tab", "member_edges": [1, 2, 3, 4]}]
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
