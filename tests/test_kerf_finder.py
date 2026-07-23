import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import kerf_finder


# ---------------------------------------------------------------------------
# slot_widths_mm
# ---------------------------------------------------------------------------

def test_odd_count_is_centered_exactly_on_nominal():
    widths = kerf_finder.slot_widths_mm(3.0, 5, 0.05)
    assert widths == [2.9, 2.95, 3.0, 3.05, 3.1]


def test_even_count_straddles_nominal_symmetrically():
    widths = kerf_finder.slot_widths_mm(3.0, 4, 0.1)
    # centered offset is 1.5 steps either side of the middle
    assert widths == pytest.approx([2.85, 2.95, 3.05, 3.15])


def test_widths_are_evenly_spaced_by_step():
    widths = kerf_finder.slot_widths_mm(5.0, 6, 0.2)
    diffs = [round(b - a, 6) for a, b in zip(widths, widths[1:])]
    assert diffs == pytest.approx([0.2] * 5)


# ---------------------------------------------------------------------------
# build_test_pattern -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(nominal_mm=0),
    dict(nominal_mm=-1),
])
def test_rejects_non_positive_nominal(kwargs):
    with pytest.raises(ValueError):
        kerf_finder.build_test_pattern(**kwargs)


@pytest.mark.parametrize("count", [0, 1, 16, 100])
def test_rejects_count_out_of_range(count):
    with pytest.raises(ValueError):
        kerf_finder.build_test_pattern(nominal_mm=3.0, count=count)


def test_rejects_non_positive_step():
    with pytest.raises(ValueError):
        kerf_finder.build_test_pattern(nominal_mm=3.0, step_mm=0)


def test_rejects_non_positive_slot_height():
    with pytest.raises(ValueError):
        kerf_finder.build_test_pattern(nominal_mm=3.0, slot_height_mm=0)


def test_rejects_step_large_enough_to_zero_out_smallest_slot():
    # nominal=1.0, count=5, step=0.5 -> smallest width is 1.0 - 2*0.5 == 0
    with pytest.raises(ValueError):
        kerf_finder.build_test_pattern(nominal_mm=1.0, count=5, step_mm=0.5)


# ---------------------------------------------------------------------------
# build_test_pattern -- generated SVG content
# ---------------------------------------------------------------------------

def test_pattern_records_the_requested_widths():
    pattern = kerf_finder.build_test_pattern(nominal_mm=3.0, count=5, step_mm=0.05, slot_height_mm=20.0)
    assert pattern.widths_mm == [2.9, 2.95, 3.0, 3.05, 3.1]


def test_svg_has_one_rect_per_slot_plus_tab_plus_boundary():
    pattern = kerf_finder.build_test_pattern(nominal_mm=3.0, count=5, step_mm=0.05)
    rects = re.findall(r"<rect ", pattern.svg)
    # 1 outer boundary + 5 slots + 1 nominal-width tab
    assert len(rects) == 1 + 5 + 1


def test_svg_has_one_label_per_slot_plus_tab():
    pattern = kerf_finder.build_test_pattern(nominal_mm=3.0, count=5, step_mm=0.05)
    labels = re.findall(r"<text ", pattern.svg)
    assert len(labels) == 5 + 1


def test_slot_rects_have_the_expected_widths():
    pattern = kerf_finder.build_test_pattern(nominal_mm=3.0, count=3, step_mm=0.1, slot_height_mm=15.0)
    widths_in_svg = [float(w) for w in re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*height="15\.0000"', pattern.svg)]
    assert sorted(widths_in_svg) == pytest.approx(sorted(pattern.widths_mm + [3.0]))


def test_svg_declares_document_units_in_millimeters():
    pattern = kerf_finder.build_test_pattern(nominal_mm=3.0)
    assert 'width="' in pattern.svg and 'mm"' in pattern.svg
    # viewBox width must match the mm width numerically so 1 user unit == 1mm,
    # consistent with how svgio._detect_scale infers scale for the corrector.
    vb_match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', pattern.svg)
    width_match = re.search(r'width="([\d.]+)mm"', pattern.svg)
    assert vb_match is not None and width_match is not None
    assert float(vb_match.group(1)) == pytest.approx(float(width_match.group(1)))


def test_labels_and_cut_geometry_use_distinct_fill_conventions():
    pattern = kerf_finder.build_test_pattern(nominal_mm=3.0)
    # every rect (cut geometry) is unfilled...
    assert all('fill="none"' in line for line in re.findall(r"<rect [^>]*>", pattern.svg))
    # ...while every text label is filled, so an unfilled-only selector
    # (like the kerf corrector's own convention) never mistakes a label
    # for a cut line.
    assert all('fill="black"' in line for line in re.findall(r"<text [^>]*>", pattern.svg))
