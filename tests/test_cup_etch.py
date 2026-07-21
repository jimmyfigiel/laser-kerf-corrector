import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import cup_etch


# ---------------------------------------------------------------------------
# CupGeometry validation
# ---------------------------------------------------------------------------

def test_reference_diameter_is_average_of_rims():
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    assert geom.reference_diameter_mm == pytest.approx(70)


@pytest.mark.parametrize("kwargs", [
    dict(bottom_diameter_mm=0, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140),
    dict(bottom_diameter_mm=80, top_diameter_mm=-5, height_mm=100, wrap_angle_deg=140),
    dict(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=0, wrap_angle_deg=140),
    dict(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=0),
    dict(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=180),
])
def test_invalid_geometry_rejected(kwargs):
    with pytest.raises(ValueError):
        cup_etch.CupGeometry(**kwargs)


# ---------------------------------------------------------------------------
# output_size_px
# ---------------------------------------------------------------------------

def test_output_size_matches_physical_dimensions_at_given_resolution():
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    w, h = cup_etch.output_size_px(geom, px_per_mm=5)
    assert w == round(geom.output_width_mm * 5)
    assert h == round(geom.output_height_mm * 5)


def test_output_size_capped_to_keep_dither_responsive():
    geom = cup_etch.CupGeometry(bottom_diameter_mm=800, top_diameter_mm=600, height_mm=1000, wrap_angle_deg=140)
    w, h = cup_etch.output_size_px(geom, px_per_mm=20)
    assert max(w, h) <= cup_etch.MAX_OUTPUT_PX


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


def test_center_column_is_untouched_by_the_warp():
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    src = _ramp_image()
    out_w, out_h = 400, 200
    warped = cup_etch.warp_for_rotary(src, geom, out_w, out_h)
    # Source center value (red ~127) should land at the output's center column.
    center_out = warped[out_h // 2, out_w // 2, 0]
    center_src = src[0, src.shape[1] // 2, 0]
    assert abs(int(center_out) - int(center_src)) < 5


def test_edges_map_to_edges():
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    src = _ramp_image()
    out_w, out_h = 400, 200
    warped = cup_etch.warp_for_rotary(src, geom, out_w, out_h)
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
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    src = _ramp_image(w=4000, h=10)  # high-res source for a precise derivative estimate
    out_w, out_h = 400, 10
    warped = cup_etch.warp_for_rotary(src, geom, out_w, out_h).astype(np.int64)
    row = warped[5, :, 0]
    center_step = abs(int(row[out_w // 2 + 1]) - int(row[out_w // 2]))
    edge_step = abs(int(row[-2]) - int(row[-3]))
    assert center_step > edge_step


def test_narrower_wrap_angle_produces_narrower_physical_output():
    narrow = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=60)
    wide = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=170)
    assert narrow.output_width_mm < wide.output_width_mm


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
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    src = np.zeros((300, 300, 4), dtype=np.uint8)
    src[:, :, 0] = 128
    src[:, :, 3] = 200  # partial alpha, should survive resampling
    out, out_w, out_h = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
    assert out.shape == (out_h, out_w, 4)
    assert out[..., 3].mean() == pytest.approx(200, abs=2)


def test_build_pattern_with_dither_keeps_alpha_channel_undithered():
    geom = cup_etch.CupGeometry(bottom_diameter_mm=80, top_diameter_mm=60, height_mm=100, wrap_angle_deg=140)
    src = np.random.default_rng(1).integers(0, 256, size=(300, 300, 4)).astype(np.uint8)
    src[:, :, 3] = 255
    out, out_w, out_h = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=True)
    assert set(np.unique(out[..., 0]).tolist()) <= {0, 255}
    assert (out[..., 3] == 255).all()
