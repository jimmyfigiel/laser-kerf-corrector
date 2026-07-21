import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import cup_etch


def _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=30, top_offset=0):
    """Builds a CupGeometry for a cup with the given bottom/top diameter
    and axial height -- translated through to the real (circumference,
    side length) constructor so test scenarios stay easy to reason about
    while still exercising the actual measurable-inputs API end to end."""
    bottom_r, top_r = bottom_diameter / 2, top_diameter / 2
    delta_r = bottom_r - top_r
    axial_to_slant = math.hypot(height, delta_r) / height  # constant along the whole band
    return cup_etch.CupGeometry(
        bottom_circumference_mm=bottom_diameter * math.pi,
        top_circumference_mm=top_diameter * math.pi,
        side_length_mm=height * axial_to_slant,
        design_width_mm=design_width,
        top_offset_mm=top_offset * axial_to_slant,
    )


# ---------------------------------------------------------------------------
# CupGeometry -- derived properties
# ---------------------------------------------------------------------------

def test_available_height_derived_from_side_length_and_taper():
    # A cup with a 20mm radius difference and a 100mm axial height has a
    # side length of hypot(100, 20) by construction (see _geom) -- check
    # the available_height_mm property correctly inverts that back to ~100mm.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100)
    assert geom.available_height_mm == pytest.approx(100, abs=1e-6)


def test_circumference_converts_to_the_expected_radius():
    geom = cup_etch.CupGeometry(
        bottom_circumference_mm=80 * math.pi, top_circumference_mm=60 * math.pi,
        side_length_mm=100, design_width_mm=30,
    )
    assert geom.bottom_radius_mm == pytest.approx(40)
    assert geom.top_radius_mm == pytest.approx(30)


def test_diameter_at_axial_offset_interpolates_linearly():
    geom = cup_etch.CupGeometry(
        bottom_circumference_mm=80 * math.pi, top_circumference_mm=60 * math.pi,  # d_bot=80, d_top=60
        side_length_mm=100, design_width_mm=30,
    )
    assert geom.diameter_at_axial_offset_from_top(0) == pytest.approx(60)  # top rim
    # side_length_mm=100 isn't quite the axial height here (there's a taper),
    # so the true bottom rim sits at available_height_mm, not at 100.
    assert geom.diameter_at_axial_offset_from_top(geom.available_height_mm) == pytest.approx(80)
    assert geom.diameter_at_axial_offset_from_top(geom.available_height_mm / 2) == pytest.approx(70)


def test_axial_top_offset_converts_slant_offset_by_the_bands_own_ratio():
    # A cup where side length (hypot(100,20)=~101.98) is a bit longer than
    # the axial height (100) -- the same ratio should scale a slant offset
    # down to its axial equivalent.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, top_offset=0)
    ratio = geom.available_height_mm / geom.side_length_mm
    geom2 = cup_etch.CupGeometry(
        bottom_circumference_mm=geom.bottom_circumference_mm, top_circumference_mm=geom.top_circumference_mm,
        side_length_mm=geom.side_length_mm, design_width_mm=geom.design_width_mm,
        top_offset_mm=10,
    )
    assert geom2.axial_top_offset_mm == pytest.approx(10 * ratio)


# ---------------------------------------------------------------------------
# CupGeometry -- validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(bottom_circumference_mm=0, top_circumference_mm=188, side_length_mm=100, design_width_mm=30),
    dict(bottom_circumference_mm=251, top_circumference_mm=-5, side_length_mm=100, design_width_mm=30),
    dict(bottom_circumference_mm=251, top_circumference_mm=188, side_length_mm=0, design_width_mm=30),
    dict(bottom_circumference_mm=251, top_circumference_mm=188, side_length_mm=100, design_width_mm=0),
    dict(bottom_circumference_mm=251, top_circumference_mm=188, side_length_mm=100, design_width_mm=30,
         top_offset_mm=-1),
])
def test_invalid_geometry_rejected(kwargs):
    with pytest.raises(ValueError):
        cup_etch.CupGeometry(**kwargs)


def test_side_length_too_short_for_taper_rejected():
    # bottom radius 40, top radius 30 -> a straight side can't be shorter
    # than the 10mm difference between them.
    with pytest.raises(ValueError, match="too short"):
        cup_etch.CupGeometry(
            bottom_circumference_mm=80 * math.pi, top_circumference_mm=60 * math.pi,
            side_length_mm=5, design_width_mm=30,
        )


def test_top_offset_at_or_beyond_side_length_rejected():
    with pytest.raises(ValueError, match="Distance from top"):
        cup_etch.CupGeometry(
            bottom_circumference_mm=80 * math.pi, top_circumference_mm=60 * math.pi,
            side_length_mm=100, design_width_mm=30, top_offset_mm=100,
        )


# ---------------------------------------------------------------------------
# output_size_px
# ---------------------------------------------------------------------------

def test_output_size_matches_physical_dimensions_at_given_resolution():
    w, h = cup_etch.output_size_px(width_mm=60, height_mm=30, px_per_mm=5)
    assert w == round(60 * 5)
    assert h == round(30 * 5)


def test_output_size_capped_to_keep_dither_responsive():
    w, h = cup_etch.output_size_px(width_mm=600, height_mm=1000, px_per_mm=20)
    assert max(w, h) <= cup_etch.MAX_OUTPUT_PX


# ---------------------------------------------------------------------------
# design_height_for_image_mm / design_geometry_for_image
# ---------------------------------------------------------------------------

def test_design_height_for_image_mm_preserves_source_aspect_ratio():
    geom = cup_etch.CupGeometry(
        bottom_circumference_mm=80 * math.pi, top_circumference_mm=60 * math.pi,
        side_length_mm=100, design_width_mm=60,
    )
    # A 400x200 (2:1) source scaled to a 60mm design width should come out 30mm tall.
    assert cup_etch.design_height_for_image_mm(geom, src_w=400, src_h=200) == pytest.approx(30)


def test_design_geometry_uses_band_average_diameter_when_design_fills_the_whole_band():
    # A square (1:1) image at design_width_mm=30 fills the whole 100mm
    # available height exactly (30mm wide... wait -- this test wants
    # height == available_height, so pick a source aspect that makes that
    # true: height_mm = width * src_h/src_w = 100 -> src_h/src_w = 100/30).
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=30, top_offset=0)
    design = cup_etch.design_geometry_for_image(geom, src_w=30, src_h=100)
    assert design.height_mm == pytest.approx(100, abs=1e-6)
    # Filling the whole band centers the design's own center at the band's
    # own mid-height -> local diameter equals the plain band average.
    assert design.local_diameter_mm == pytest.approx((80 + 60) / 2)


def test_design_geometry_local_diameter_depends_on_vertical_placement():
    # Same cup, same design width, same (short, square-ish) image -- but
    # placed at the very top vs. offset most of the way down -- must see
    # different local diameters (near top_diameter vs. near bottom_diameter).
    geom_top = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=10, top_offset=0)
    design_top = cup_etch.design_geometry_for_image(geom_top, src_w=100, src_h=10)  # short: 1mm tall

    geom_bottom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=10, top_offset=98)
    design_bottom = cup_etch.design_geometry_for_image(geom_bottom, src_w=100, src_h=10)

    assert design_top.local_diameter_mm < design_bottom.local_diameter_mm
    assert design_top.local_diameter_mm == pytest.approx(60, abs=1)  # near the top rim
    assert design_bottom.local_diameter_mm == pytest.approx(100, abs=1)  # near the bottom rim


def test_design_geometry_rejects_design_taller_than_remaining_space_below_offset():
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=30, top_offset=80)
    with pytest.raises(ValueError, match="remaining"):
        cup_etch.design_geometry_for_image(geom, src_w=30, src_h=300)  # 300mm tall at this width -- way too tall


def test_design_geometry_accepts_design_within_remaining_space_below_offset():
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=10, top_offset=80)
    design = cup_etch.design_geometry_for_image(geom, src_w=10, src_h=15)  # 15mm tall, fits in the 20mm left
    assert design.height_mm == pytest.approx(15)


def test_design_geometry_rejects_width_too_close_to_local_diameter():
    # No taper (bottom == top diameter) -> local diameter is always exactly
    # 70mm regardless of placement or height, making max_width easy to reason
    # about: 70 * sin(87.5deg) =~ 69.93mm. A square image at 69.965mm keeps
    # comfortably clear of the "doesn't fit the available height" check
    # while still tripping the width-vs-diameter one.
    geom = _geom(bottom_diameter=70, top_diameter=70, height=100, design_width=69.965, top_offset=0)
    with pytest.raises(ValueError, match="too close"):
        cup_etch.design_geometry_for_image(geom, src_w=1, src_h=1)  # square -> design_height == design_width


# ---------------------------------------------------------------------------
# fit_cover
# ---------------------------------------------------------------------------

def test_fit_cover_returns_exact_target_dimensions():
    src = np.zeros((50, 200, 4), dtype=np.uint8)  # wide image
    out = cup_etch.fit_cover(src, target_w=100, target_h=100)
    assert out.shape == (100, 100, 4)


def test_fit_cover_center_crops_without_stretching_aspect():
    # A square red dot at the horizontal center of a wide source image
    # should still be at the horizontal center after a cover-fit crop to
    # a taller aspect ratio (cover scales up until it fills, then crops
    # symmetrically -- it must not smear the dot off-center).
    src = np.zeros((40, 200, 4), dtype=np.uint8)
    src[:, 95:105, :] = 255
    out = cup_etch.fit_cover(src, target_w=40, target_h=80)
    col_has_content = out[..., 0].sum(axis=0) > 0
    center = len(col_has_content) // 2
    assert col_has_content[center - 2:center + 2].any()


# ---------------------------------------------------------------------------
# warp_for_rotary -- the front-projection correction
# ---------------------------------------------------------------------------

def _ramp_image(w=400, h=200):
    """RGBA image whose red channel is a horizontal ramp 0..255, useful for
    tracking exactly which source column ends up at a given output column."""
    ramp = np.linspace(0, 255, w, dtype=np.uint8)
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[:, :, 0] = ramp[np.newaxis, :]
    img[:, :, 3] = 255
    return img


_PHI_MAX_70DEG = math.radians(70)  # a 140 degree wrap angle


def test_center_column_is_untouched_by_the_warp():
    src = _ramp_image()
    out_w, out_h = 400, 200
    warped = cup_etch.warp_for_rotary(src, _PHI_MAX_70DEG, out_w, out_h)
    # Source center value (red ~127) should land at the output's center column.
    center_out = warped[out_h // 2, out_w // 2, 0]
    center_src = src[0, src.shape[1] // 2, 0]
    assert abs(int(center_out) - int(center_src)) < 5


def test_edges_map_to_edges():
    src = _ramp_image()
    out_w, out_h = 400, 200
    warped = cup_etch.warp_for_rotary(src, _PHI_MAX_70DEG, out_w, out_h)
    assert int(warped[out_h // 2, 0, 0]) < 10
    assert int(warped[out_h // 2, -1, 0]) > 245


def test_warp_spreads_edge_content_over_more_output_pixels_than_center():
    # The whole point of the correction: near the cup's own silhouette
    # (the output edges), physical surface is heavily foreshortened once
    # viewed from the front, so a given bit of source content has to be
    # etched across *more* physical angle (more output pixels) there than
    # the same amount of source content needs at the front-center -- once
    # foreshortening compresses it back down, it reads at the right size.
    # That shows up here as a *smaller* per-output-pixel jump in the
    # (ramped) source value near the edges than near the center.
    src = _ramp_image(w=4000, h=10)  # high-res source for a precise derivative estimate
    out_w, out_h = 400, 10
    warped = cup_etch.warp_for_rotary(src, _PHI_MAX_70DEG, out_w, out_h).astype(np.int64)
    row = warped[5, :, 0]
    center_step = abs(int(row[out_w // 2 + 1]) - int(row[out_w // 2]))
    edge_step = abs(int(row[-2]) - int(row[-3]))
    assert center_step > edge_step


def test_narrower_wrap_angle_produces_narrower_physical_output():
    narrow = _geom(design_width=20)
    wide = _geom(design_width=65)
    assert narrow.design_width_mm < wide.design_width_mm


# ---------------------------------------------------------------------------
# floyd_steinberg_dither
# ---------------------------------------------------------------------------

def test_dither_output_is_pure_black_or_white():
    rng = np.random.default_rng(0)
    gray = rng.integers(0, 256, size=(30, 40)).astype(np.float64)
    out = cup_etch.floyd_steinberg_dither(gray)
    assert set(np.unique(out).tolist()) <= {0, 255}
    assert out.shape == gray.shape


def test_dither_of_solid_field_stays_that_extreme():
    black = np.zeros((10, 10))
    white = np.full((10, 10), 255.0)
    assert cup_etch.floyd_steinberg_dither(black).sum() == 0
    assert cup_etch.floyd_steinberg_dither(white).min() == 255


# ---------------------------------------------------------------------------
# build_pattern -- end to end
# ---------------------------------------------------------------------------

def test_build_pattern_shape_and_alpha_preserved():
    geom = _geom(design_width=30)
    src = np.zeros((300, 300, 4), dtype=np.uint8)
    src[:, :, 0] = 128
    src[:, :, 3] = 200  # partial alpha, should survive resampling
    out, out_w, out_h, design = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
    assert out.shape == (out_h, out_w, 4)
    assert out[..., 3].mean() == pytest.approx(200, abs=2)
    assert design.height_mm == pytest.approx(geom.design_width_mm)  # square source -> square design


def test_build_pattern_with_dither_keeps_alpha_channel_undithered():
    geom = _geom(design_width=30)
    src = np.random.default_rng(1).integers(0, 256, size=(300, 300, 4)).astype(np.uint8)
    src[:, :, 3] = 255
    out, out_w, out_h, design = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=True)
    assert set(np.unique(out[..., 0]).tolist()) <= {0, 255}
    # LANCZOS resizing + bilinear resampling of a uniform field can round a
    # handful of edge pixels to 254 instead of 255 -- not a functional bug,
    # so allow a small tolerance rather than requiring bit-exact 255.
    assert (out[..., 3] >= 250).all()


def test_build_pattern_preserves_full_image_width_no_cropping_for_wide_source():
    # Regression test: build_pattern used to size the output canvas purely
    # from the cup's own geometry, independent of the image, so fit_cover
    # ended up center-cropping away large chunks of any image whose aspect
    # ratio didn't match that canvas. The canvas is now sized from the
    # image's own aspect ratio (see design_height_for_image_mm), so a wide
    # source's left/right edges must survive even though the cup's
    # available height (100mm, from _geom's default) is much taller than
    # the resulting (image-aspect-driven) design.
    geom = _geom(design_width=30)
    src = np.zeros((100, 400, 4), dtype=np.uint8)
    src[:, :5, 1] = 255  # green marker at the far left edge
    src[:, -5:, 1] = 255  # green marker at the far right edge
    src[:, :, 3] = 255
    out, out_w, out_h, design = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
    assert design.height_mm == pytest.approx(geom.design_width_mm * 100 / 400)
    assert out[out_h // 2, :5, 1].max() > 100
    assert out[out_h // 2, -5:, 1].max() > 100


def test_build_pattern_raises_when_image_is_too_tall_for_the_cup():
    geom = _geom(height=20, design_width=30)  # available_height_mm ~20mm
    src = np.zeros((400, 100, 4), dtype=np.uint8)  # tall/narrow -> a big design height at this width
    src[:, :, 3] = 255
    with pytest.raises(ValueError, match="remaining"):
        cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
