import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import kerf_finder


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
    # first rect is the outer sheet boundary (arbitrary size); the feature
    # rect is the second one and must be exactly 25x25 -- no kerf math
    # applied here, since the whole point is measuring undisturbed shrinkage.
    feature_w, feature_h = rects[1]
    assert float(feature_w) == pytest.approx(25.0)
    assert float(feature_h) == pytest.approx(25.0)


def test_square_svg_has_exactly_one_feature_rect_and_one_label():
    square = kerf_finder.build_kerf_square(25.0)
    assert len(re.findall(r"<rect ", square.svg)) == 2  # outer boundary + the square itself
    assert len(re.findall(r"<text ", square.svg)) == 1


def test_square_svg_declares_document_units_in_millimeters():
    square = kerf_finder.build_kerf_square(25.0)
    vb_match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', square.svg)
    width_match = re.search(r'width="([\d.]+)mm"', square.svg)
    assert vb_match is not None and width_match is not None
    assert float(vb_match.group(1)) == pytest.approx(float(width_match.group(1)))


# ---------------------------------------------------------------------------
# tab_hole_clearances_mm
# ---------------------------------------------------------------------------

def test_clearances_start_at_zero_and_increase_by_step():
    assert kerf_finder.tab_hole_clearances_mm(5, 0.05) == [0.0, 0.05, 0.1, 0.15, 0.2]


def test_clearances_never_go_negative():
    values = kerf_finder.tab_hole_clearances_mm(4, 0.1)
    assert all(v >= 0 for v in values)


# ---------------------------------------------------------------------------
# build_tab_hole_ladder -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [dict(nominal_mm=0, kerf_mm=0.15), dict(nominal_mm=-1, kerf_mm=0.15)])
def test_ladder_rejects_non_positive_nominal(kwargs):
    with pytest.raises(ValueError):
        kerf_finder.build_tab_hole_ladder(**kwargs)


@pytest.mark.parametrize("count", [0, 1, 13, 100])
def test_ladder_rejects_count_out_of_range(count):
    with pytest.raises(ValueError):
        kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, count=count)


def test_ladder_rejects_non_positive_step():
    with pytest.raises(ValueError):
        kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, step_mm=0)


def test_ladder_rejects_non_positive_tab_height():
    with pytest.raises(ValueError):
        kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, tab_height_mm=0)


def test_ladder_rejects_kerf_too_large_for_hole():
    # a 1mm hole can't shrink by a 2mm kerf and still have positive size
    with pytest.raises(ValueError):
        kerf_finder.build_tab_hole_ladder(nominal_mm=1.0, kerf_mm=2.0)


def test_ladder_rejects_clearance_that_zeroes_out_the_largest_tab():
    # nominal=1, kerf=0.1 -> tabs start at 1.1mm; a huge step blows through zero
    with pytest.raises(ValueError):
        kerf_finder.build_tab_hole_ladder(nominal_mm=1.0, kerf_mm=0.1, count=5, step_mm=1.0)


# ---------------------------------------------------------------------------
# build_tab_hole_ladder -- geometry mirrors joints.py's apply_manifest formula
# ---------------------------------------------------------------------------

def test_hole_is_corrected_by_kerf_alone_same_as_a_plain_hole():
    # a plain `hole` shrinks by the full kerf so cutting (which enlarges it)
    # lands back on nominal_mm -- no clearance term applies to the hole side.
    ladder = kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, count=3, step_mm=0.05)
    rects = re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*height="15\.0000"', ladder.svg)
    hole_w = float(rects[0])
    assert hole_w == pytest.approx(10.0 - 0.15)


def test_tabs_widen_by_kerf_then_shrink_by_their_own_clearance():
    # mirrors apply_manifest: a standalone tab's two independent width walls
    # each contribute half_kerf - half_extra, totalling kerf - extra_mm.
    ladder = kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, count=3, step_mm=0.05)
    rects = re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*height="15\.0000"', ladder.svg)
    tab_widths = [float(w) for w in rects[1:]]
    expected = [10.0 + 0.15 - c for c in [0.0, 0.05, 0.1]]
    assert tab_widths == pytest.approx(expected)


def test_ladder_records_the_requested_clearances():
    ladder = kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, count=4, step_mm=0.1)
    assert ladder.clearances_mm == [0.0, 0.1, 0.2, 0.3]


def test_ladder_svg_has_one_rect_per_tab_plus_hole_plus_boundary():
    ladder = kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, count=5, step_mm=0.05)
    rects = re.findall(r"<rect ", ladder.svg)
    assert len(rects) == 1 + 1 + 5  # boundary + hole + 5 tabs


def test_ladder_svg_has_one_label_per_tab_plus_hole():
    ladder = kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15, count=5, step_mm=0.05)
    labels = re.findall(r"<text ", ladder.svg)
    assert len(labels) == 1 + 5


def test_ladder_labels_and_cut_geometry_use_distinct_fill_conventions():
    ladder = kerf_finder.build_tab_hole_ladder(nominal_mm=10.0, kerf_mm=0.15)
    assert all('fill="none"' in line for line in re.findall(r"<rect [^>]*>", ladder.svg))
    assert all('fill="black"' in line for line in re.findall(r"<text [^>]*>", ladder.svg))


# ---------------------------------------------------------------------------
# build_tab_finger_ladder -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [dict(nominal_mm=0, kerf_mm=0.16), dict(nominal_mm=-1, kerf_mm=0.16)])
def test_finger_ladder_rejects_non_positive_nominal(kwargs):
    with pytest.raises(ValueError):
        kerf_finder.build_tab_finger_ladder(**kwargs)


@pytest.mark.parametrize("count", [0, 1, 13, 100])
def test_finger_ladder_rejects_count_out_of_range(count):
    with pytest.raises(ValueError):
        kerf_finder.build_tab_finger_ladder(nominal_mm=10.0, kerf_mm=0.16, count=count)


def test_finger_ladder_rejects_non_positive_step():
    with pytest.raises(ValueError):
        kerf_finder.build_tab_finger_ladder(nominal_mm=10.0, kerf_mm=0.16, step_mm=0)


def test_finger_ladder_rejects_non_positive_engagement_depth():
    with pytest.raises(ValueError):
        kerf_finder.build_tab_finger_ladder(nominal_mm=10.0, kerf_mm=0.16, engagement_depth_mm=0)


def test_finger_ladder_rejects_clearance_that_zeroes_out_the_largest_tab():
    with pytest.raises(ValueError):
        kerf_finder.build_tab_finger_ladder(nominal_mm=1.0, kerf_mm=0.1, count=5, step_mm=1.0)


# ---------------------------------------------------------------------------
# build_tab_finger_ladder -- geometry, via the real apply_manifest engine
# ---------------------------------------------------------------------------

def _rect_bboxes(d):
    """Parse an M/L/.../Z path's vertices and return (min_x, max_x, min_y, max_y)."""
    import re as _re
    nums = _re.findall(r"(-?[\d.]+),(-?[\d.]+)", d)
    xs = [float(x) for x, y in nums]
    ys = [float(y) for x, y in nums]
    return min(xs), max(xs), min(ys), max(ys)


def test_finger_ladder_records_the_requested_clearances():
    ladder = kerf_finder.build_tab_finger_ladder(nominal_mm=10.0, kerf_mm=0.16, count=4, step_mm=0.1)
    assert ladder.clearances_mm == [0.0, 0.1, 0.2, 0.3]


def test_finger_ladder_socket_width_shrinks_by_full_kerf():
    # the socket is a plain `edge` notch -- both its side walls are
    # independent member edges, so its width (bounded by both) shrinks by
    # the FULL kerf, same as any ordinary hole/notch correction.
    d, _ = kerf_finder._panel_with_feature_d(25.0, 18.0, 10.0, 8.0, protrude=False)
    corrected = kerf_finder._correct_single_feature_d(25.0, 18.0, 10.0, 8.0, False, "edge", 0.16)
    min_x, max_x, _, _ = _rect_bboxes(corrected)
    # the notch's own walls sit strictly inside the panel -- isolate them
    # from the panel's own untouched outer edges by excluding x=0/x=25.
    import re as _re
    xs = sorted(set(round(float(x), 4) for x, y in _re.findall(r"(-?[\d.]+),(-?[\d.]+)", corrected)) - {0.0, 25.0})
    assert len(xs) == 2
    assert xs[1] - xs[0] == pytest.approx(10.0 - 0.16)


def test_finger_ladder_socket_depth_shrinks_by_half_kerf():
    corrected = kerf_finder._correct_single_feature_d(25.0, 18.0, 10.0, 8.0, False, "edge", 0.16)
    import re as _re
    ys = sorted(set(round(float(y), 4) for x, y in _re.findall(r"(-?[\d.]+),(-?[\d.]+)", corrected)) - {0.0, 18.0})
    assert len(ys) == 1
    # notch depth is bounded by a single independently-cut wall (the tip
    # cap) -- half the kerf, not the full amount, per apply_manifest's
    # documented length/width asymmetry for a windowed feature.
    assert ys[0] == pytest.approx(8.0 - 0.08)


def test_finger_ladder_tab_width_tracks_kerf_minus_clearance():
    # a standalone tab's width IS bounded by two independent walls (both
    # sides are cut free), so it behaves like build_tab_hole_ladder's tabs:
    # full kerf grows it, full clearance shrinks it back.
    for clearance in (0.0, 0.1, 0.2):
        corrected = kerf_finder._correct_single_feature_d(
            25.0, 10.0, 10.0, 8.0, True, "tab_finger", 0.16, tab_finger_clearance_mm=clearance)
        import re as _re
        xs = sorted(set(round(float(x), 4) for x, y in _re.findall(r"(-?[\d.]+),(-?[\d.]+)", corrected)) - {0.0, 25.0})
        assert xs[1] - xs[0] == pytest.approx(10.0 + 0.16 - clearance)


def test_finger_ladder_tab_length_gets_half_kerf_and_half_clearance():
    # the tab's protrusion length is bounded by a single independently-cut
    # wall (its tip cap) -- half the kerf AND half the clearance apply
    # there, not the full amount.
    for clearance in (0.0, 0.1, 0.2):
        corrected = kerf_finder._correct_single_feature_d(
            25.0, 10.0, 10.0, 8.0, True, "tab_finger", 0.16, tab_finger_clearance_mm=clearance)
        import re as _re
        ys = sorted(set(round(float(y), 4) for x, y in _re.findall(r"(-?[\d.]+),(-?[\d.]+)", corrected)))
        tip_y = ys[0]  # most negative y is the protruding tip
        assert -tip_y == pytest.approx(8.0 + 0.16 / 2 - clearance / 2)


def test_finger_ladder_svg_contains_socket_and_all_tab_labels():
    ladder = kerf_finder.build_tab_finger_ladder(nominal_mm=10.0, kerf_mm=0.16, count=5, step_mm=0.05)
    assert "socket 10" in ladder.svg
    for c in ladder.clearances_mm:
        assert f"+{c:g}" in ladder.svg


def test_finger_ladder_labels_and_cut_geometry_use_distinct_fill_conventions():
    ladder = kerf_finder.build_tab_finger_ladder(nominal_mm=10.0, kerf_mm=0.16)
    assert all('fill="none"' in line for line in re.findall(r"<path [^>]*>", ladder.svg))
    assert all('fill="black"' in line for line in re.findall(r"<text [^>]*>", ladder.svg))
