import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import kerf_finder


def _points(d):
    """Parse an M/L/.../Z path's vertices into a list of (x, y) floats."""
    return [(float(x), float(y)) for x, y in re.findall(r"(-?[\d.]+),(-?[\d.]+)", d)]


# ---------------------------------------------------------------------------
# build_kerf_square
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nominal_mm", [0, -1])
def test_square_rejects_non_positive_nominal(nominal_mm):
    with pytest.raises(ValueError):
        kerf_finder.build_kerf_square(nominal_mm)


def test_square_is_drawn_at_exactly_the_nominal_size_uncorrected():
    square = kerf_finder.build_kerf_square(25.0)
    rects = re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*height="([\d.]+)"', square.svg)
    feature_w, feature_h = rects[1]
    assert float(feature_w) == pytest.approx(25.0)
    assert float(feature_h) == pytest.approx(25.0)


def test_square_svg_has_exactly_one_feature_rect_and_one_label():
    square = kerf_finder.build_kerf_square(25.0)
    assert len(re.findall(r"<rect ", square.svg)) == 2
    assert len(re.findall(r"<text ", square.svg)) == 1


def test_square_svg_declares_document_units_in_millimeters():
    square = kerf_finder.build_kerf_square(25.0)
    vb_match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', square.svg)
    width_match = re.search(r'width="([\d.]+)mm"', square.svg)
    assert vb_match is not None and width_match is not None
    assert float(vb_match.group(1)) == pytest.approx(float(width_match.group(1)))


# ---------------------------------------------------------------------------
# clearances_mm
# ---------------------------------------------------------------------------

def test_clearances_start_at_zero_and_increase_by_step():
    assert kerf_finder.clearances_mm(5, 0.05) == [0.0, 0.05, 0.1, 0.15, 0.2]


def test_clearances_never_go_negative():
    assert all(v >= 0 for v in kerf_finder.clearances_mm(4, 0.1))


# ---------------------------------------------------------------------------
# build_mortice_tenon_ladder -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [dict(nominal_mm=0, kerf_mm=0.16), dict(nominal_mm=-1, kerf_mm=0.16)])
def test_mortice_tenon_rejects_non_positive_nominal(kwargs):
    with pytest.raises(ValueError):
        kerf_finder.build_mortice_tenon_ladder(**kwargs)


@pytest.mark.parametrize("count", [0, 1, 13, 100])
def test_mortice_tenon_rejects_count_out_of_range(count):
    with pytest.raises(ValueError):
        kerf_finder.build_mortice_tenon_ladder(nominal_mm=10.0, kerf_mm=0.16, count=count)


def test_mortice_tenon_rejects_non_positive_step():
    with pytest.raises(ValueError):
        kerf_finder.build_mortice_tenon_ladder(nominal_mm=10.0, kerf_mm=0.16, step_mm=0)


def test_mortice_tenon_rejects_non_positive_engagement_depth():
    with pytest.raises(ValueError):
        kerf_finder.build_mortice_tenon_ladder(nominal_mm=10.0, kerf_mm=0.16, engagement_depth_mm=0)


def test_mortice_tenon_rejects_clearance_that_zeroes_out_largest_tenon():
    with pytest.raises(ValueError):
        kerf_finder.build_mortice_tenon_ladder(nominal_mm=1.0, kerf_mm=0.1, count=5, step_mm=1.0)


# ---------------------------------------------------------------------------
# build_mortice_tenon_ladder -- geometry via the real apply_manifest engine
# ---------------------------------------------------------------------------

def test_mortice_hole_is_a_closed_loop_and_shrinks_full_kerf_on_both_axes():
    # mortice is ALWAYS closed-loop (a real enclosed socket) -- both its
    # dimensions are bounded by 2 independent walls each, so both shrink by
    # the full kerf, mirroring a plain `hole`.
    d = kerf_finder._correct_mortice_hole_d(30.0, 24.0, 10.0, 8.0, kerf_mm=0.16)
    xs = sorted({round(x, 4) for x, y in _points(d)})
    ys = sorted({round(y, 4) for x, y in _points(d)})
    assert len(xs) == 2 and len(ys) == 2
    assert xs[1] - xs[0] == pytest.approx(10.0 - 0.16)
    assert ys[1] - ys[0] == pytest.approx(8.0 - 0.16)


def test_mortice_hole_is_never_affected_by_clearance_by_construction():
    # _correct_mortice_hole_d has no clearance parameter at all -- it's
    # always corrected with mortice_clearance_mm=0.0 internally, per the
    # "mortice stays fixed" convention. Same kerf -> same hole regardless
    # of what tenon_clearance_mm ends up being elsewhere in the ladder.
    d1 = kerf_finder._correct_mortice_hole_d(30.0, 24.0, 10.0, 8.0, kerf_mm=0.16)
    d2 = kerf_finder._correct_mortice_hole_d(30.0, 24.0, 10.0, 8.0, kerf_mm=0.16)
    assert d1 == d2


def test_tenon_is_windowed_width_gets_full_shift_length_gets_half():
    # an attached tenon (protruding from a rail, not free-floating): width
    # (2 independent walls) gets the full kerf+clearance shift; protrusion
    # length (1 independently-cut wall, the tip) gets only half.
    for clearance in (0.0, 0.1, 0.2):
        d, edges = kerf_finder._panel_with_feature_d(30.0, 10.0, 10.0, 8.0, protrude=True)
        corrected = kerf_finder._correct_shape_d(30.0, d, [("tenon", edges)], 0.16, tenon_clearance_mm=clearance)
        xs = sorted({round(x, 4) for x, y in _points(corrected)} - {0.0, 30.0})
        ys = sorted({round(y, 4) for x, y in _points(corrected)})
        assert xs[1] - xs[0] == pytest.approx(10.0 + 0.16 - clearance)
        tip_y = ys[0]
        assert -tip_y == pytest.approx(8.0 + 0.16 / 2 - clearance / 2)


def test_mortice_tenon_ladder_records_the_requested_clearances():
    ladder = kerf_finder.build_mortice_tenon_ladder(nominal_mm=10.0, kerf_mm=0.16, count=4, step_mm=0.1)
    assert ladder.clearances_mm == [0.0, 0.1, 0.2, 0.3]


def test_mortice_tenon_ladder_svg_has_mortice_and_all_tenon_labels():
    ladder = kerf_finder.build_mortice_tenon_ladder(nominal_mm=10.0, kerf_mm=0.16, count=5, step_mm=0.05)
    assert "mortice 10" in ladder.svg
    for c in ladder.clearances_mm:
        assert f"+{c:g}" in ladder.svg


def test_mortice_tenon_ladder_labels_and_cut_geometry_use_distinct_fill_conventions():
    ladder = kerf_finder.build_mortice_tenon_ladder(nominal_mm=10.0, kerf_mm=0.16)
    shapes = re.findall(r"<(?:rect|path) [^>]*>", ladder.svg)
    assert all('fill="none"' in line for line in shapes)
    assert all('fill="black"' in line for line in re.findall(r"<text [^>]*>", ladder.svg))


# ---------------------------------------------------------------------------
# build_teeth_ladder -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [dict(nominal_mm=0, kerf_mm=0.16), dict(nominal_mm=-1, kerf_mm=0.16)])
def test_teeth_ladder_rejects_non_positive_nominal(kwargs):
    with pytest.raises(ValueError):
        kerf_finder.build_teeth_ladder(**kwargs)


@pytest.mark.parametrize("count", [0, 1, 13, 100])
def test_teeth_ladder_rejects_count_out_of_range(count):
    with pytest.raises(ValueError):
        kerf_finder.build_teeth_ladder(nominal_mm=10.0, kerf_mm=0.16, count=count)


def test_teeth_ladder_rejects_fewer_than_two_teeth_per_comb():
    with pytest.raises(ValueError):
        kerf_finder.build_teeth_ladder(nominal_mm=10.0, kerf_mm=0.16, teeth_per_comb=1)


def test_teeth_ladder_rejects_clearance_that_zeroes_out_largest_tooth():
    with pytest.raises(ValueError):
        kerf_finder.build_teeth_ladder(nominal_mm=1.0, kerf_mm=0.1, count=5, step_mm=1.0)


# ---------------------------------------------------------------------------
# _comb_patterns -- the two combs are complementary and equal length
# ---------------------------------------------------------------------------

def test_comb_patterns_are_complementary():
    a, b = kerf_finder._comb_patterns(3)
    assert a == [True, False, True, False, True]
    assert b == [False, True, False, True, False]
    assert len(a) == len(b)
    assert all(x != y for x, y in zip(a, b))


def test_comb_a_starts_and_ends_with_a_tooth_comb_b_with_a_gap():
    a, b = kerf_finder._comb_patterns(4)
    assert a[0] and a[-1]
    assert not b[0] and not b[-1]


# ---------------------------------------------------------------------------
# build_teeth_ladder -- geometry via the real apply_manifest engine
# ---------------------------------------------------------------------------

def test_comb_middle_gap_shrinks_by_full_kerf_matching_a_middle_tooth_growing_by_full_kerf():
    # a MIDDLE tooth (bounded on both sides by other teeth, not the
    # panel's own edge) widens by the full kerf; the MIDDLE gap right next
    # to it -- sharing that same cut line -- correspondingly narrows by the
    # same full kerf. Use comb A (starts with a tooth) so its middle
    # tooth/gap pair (index 2, the third segment) isn't edge-adjacent.
    pattern, _ = kerf_finder._comb_patterns(3)  # [T, F, T, F, T]
    d, w, edges = kerf_finder._comb_path_d(pattern, 10.0, 10.0, 8.0)
    corrected = kerf_finder._correct_shape_d(w, d, [("teeth", e) for e in edges], 0.16, teeth_clearance_mm=0.0)
    xs = [x for x, y in _points(corrected) if y == 0.0]
    xs = sorted(set(round(x, 4) for x in xs))
    # middle tooth (2nd of 3, spanning the 3rd pattern segment) is bounded
    # by xs[2] and xs[3] in this 6-unique-x layout; its own width:
    widths = [round(b - a, 4) for a, b in zip(xs, xs[1:])]
    # alternating tooth/gap widths across the whole comb -- every one of
    # them (middle or not, since only the very first/last x isn't
    # touched by two independent corrected walls) should reflect a full
    # kerf's worth of shift at each internal boundary.
    assert widths[1] == pytest.approx(10.0 - 0.16)  # a middle gap


def test_teeth_get_full_width_shift_and_half_depth_shift():
    for clearance in (0.0, 0.1):
        pattern, _ = kerf_finder._comb_patterns(3)
        d, w, edges = kerf_finder._comb_path_d(pattern, 10.0, 10.0, 8.0)
        corrected = kerf_finder._correct_shape_d(w, d, [("teeth", e) for e in edges], 0.16,
                                                  teeth_clearance_mm=clearance)
        # first tooth (comb A starts with a tooth, so its own two walls
        # are both independently corrected, unaffected by the panel-edge
        # limitation documented on _comb_path_d)
        pts = _points(corrected)
        tip_ys = sorted({round(y, 4) for x, y in pts if y < 0})
        assert -tip_ys[0] == pytest.approx(8.0 + 0.16 / 2 - clearance / 2)


def test_teeth_ladder_records_the_requested_clearances():
    ladder = kerf_finder.build_teeth_ladder(nominal_mm=10.0, kerf_mm=0.16, count=4, step_mm=0.1)
    assert ladder.clearances_mm == [0.0, 0.1, 0.2, 0.3]


def test_teeth_ladder_svg_has_one_label_per_clearance_pair():
    ladder = kerf_finder.build_teeth_ladder(nominal_mm=10.0, kerf_mm=0.16, count=5, step_mm=0.05)
    assert len(re.findall(r"<text ", ladder.svg)) == 5
    for c in ladder.clearances_mm:
        assert f"+{c:g}" in ladder.svg


def test_teeth_ladder_has_two_paths_per_clearance_pair():
    ladder = kerf_finder.build_teeth_ladder(nominal_mm=10.0, kerf_mm=0.16, count=3, step_mm=0.05)
    assert len(re.findall(r"<path ", ladder.svg)) == 3 * 2


def test_teeth_ladder_labels_and_cut_geometry_use_distinct_fill_conventions():
    ladder = kerf_finder.build_teeth_ladder(nominal_mm=10.0, kerf_mm=0.16)
    shapes = re.findall(r"<(?:rect|path) [^>]*>", ladder.svg)
    assert all('fill="none"' in line for line in shapes)
    assert all('fill="black"' in line for line in re.findall(r"<text [^>]*>", ladder.svg))


# ---------------------------------------------------------------------------
# build_slot_ladder -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [dict(nominal_mm=0, kerf_mm=0.16), dict(nominal_mm=-1, kerf_mm=0.16)])
def test_slot_ladder_rejects_non_positive_nominal(kwargs):
    with pytest.raises(ValueError):
        kerf_finder.build_slot_ladder(**kwargs)


@pytest.mark.parametrize("count", [0, 1, 13, 100])
def test_slot_ladder_rejects_count_out_of_range(count):
    with pytest.raises(ValueError):
        kerf_finder.build_slot_ladder(nominal_mm=10.0, kerf_mm=0.16, count=count)


def test_slot_ladder_rejects_clearance_that_zeroes_out_largest_notch():
    with pytest.raises(ValueError):
        kerf_finder.build_slot_ladder(nominal_mm=1.0, kerf_mm=0.1, count=5, step_mm=1.0)


# ---------------------------------------------------------------------------
# build_slot_ladder -- geometry: both sides of a pair get the SAME
# clearance, and more clearance ENLARGES the void (unlike tenon/teeth)
# ---------------------------------------------------------------------------

def test_slot_notch_enlarges_with_more_clearance_unlike_a_solid_feature():
    widths = []
    for clearance in (0.0, 0.05, 0.1):
        d, edges = kerf_finder._panel_with_feature_d(30.0, 10.0, 10.0, 8.0, protrude=False)
        corrected = kerf_finder._correct_shape_d(30.0, d, [("slot", edges)], 0.16, slot_clearance_mm=clearance)
        xs = sorted({round(x, 4) for x, y in _points(corrected)} - {0.0, 30.0})
        widths.append(xs[1] - xs[0])
    assert widths == pytest.approx([10.0 - 0.16, 10.0 - 0.16 + 0.05, 10.0 - 0.16 + 0.1])
    assert widths[0] < widths[1] < widths[2]  # enlarging, not shrinking


def test_slot_notch_depth_gets_half_the_kerf_and_half_the_clearance():
    d, edges = kerf_finder._panel_with_feature_d(30.0, 10.0, 10.0, 8.0, protrude=False)
    corrected = kerf_finder._correct_shape_d(30.0, d, [("slot", edges)], 0.16, slot_clearance_mm=0.1)
    ys = sorted({round(y, 4) for x, y in _points(corrected)} - {0.0, 10.0})
    assert ys[0] == pytest.approx(8.0 - 0.16 / 2 + 0.1 / 2)


def test_slot_ladder_pairs_use_identical_geometry_at_each_clearance():
    # both mating pieces of a slot joint share ONE clearance value -- the
    # two paths generated for a given rung should be identical (allowing
    # for the row-2 vs row-1 y-translate offset, which is stripped by
    # comparing only the path 'd' strings, not the <g transform>).
    ladder = kerf_finder.build_slot_ladder(nominal_mm=10.0, kerf_mm=0.16, count=2, step_mm=0.05)
    ds = re.findall(r'<path d="([^"]+)"', ladder.svg)
    assert len(ds) == 4  # 2 pairs x 2 pieces each
    assert ds[0] == ds[1]  # first pair: both pieces identical
    assert ds[2] == ds[3]  # second pair: both pieces identical
    assert ds[0] != ds[2]  # different clearance rungs differ from each other


def test_slot_ladder_records_the_requested_clearances():
    ladder = kerf_finder.build_slot_ladder(nominal_mm=10.0, kerf_mm=0.16, count=4, step_mm=0.1)
    assert ladder.clearances_mm == [0.0, 0.1, 0.2, 0.3]


def test_slot_ladder_svg_has_one_label_per_clearance_pair():
    ladder = kerf_finder.build_slot_ladder(nominal_mm=10.0, kerf_mm=0.16, count=5, step_mm=0.05)
    assert len(re.findall(r"<text ", ladder.svg)) == 5


def test_slot_ladder_labels_and_cut_geometry_use_distinct_fill_conventions():
    ladder = kerf_finder.build_slot_ladder(nominal_mm=10.0, kerf_mm=0.16)
    shapes = re.findall(r"<(?:rect|path) [^>]*>", ladder.svg)
    assert all('fill="none"' in line for line in shapes)
    assert all('fill="black"' in line for line in re.findall(r"<text [^>]*>", ladder.svg))
