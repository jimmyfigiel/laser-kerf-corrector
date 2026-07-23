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
