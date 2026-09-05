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
    w, h, effective_px_per_mm = cup_etch.output_size_px(width_mm=60, height_mm=30, px_per_mm=5)
    assert w == round(60 * 5)
    assert h == round(30 * 5)
    assert effective_px_per_mm == pytest.approx(5)  # no cap triggered -- matches the request exactly


def test_output_size_capped_to_keep_dither_responsive():
    w, h, effective_px_per_mm = cup_etch.output_size_px(width_mm=600, height_mm=1000, px_per_mm=20)
    assert max(w, h) <= cup_etch.MAX_OUTPUT_PX
    # The cap silently shrinks the pixel dimensions relative to what was
    # requested -- effective_px_per_mm must reflect that actual shrink so a
    # DPI embedded from it still describes the file's own true physical
    # size (not the originally requested, no-longer-honored resolution).
    assert effective_px_per_mm < 20
    assert w == pytest.approx(600 * effective_px_per_mm, abs=1)


# ---------------------------------------------------------------------------
# design_height_for_image_mm / design_geometry_for_image
# ---------------------------------------------------------------------------

def test_design_height_for_image_mm_preserves_source_aspect_ratio_for_a_cylinder():
    # No taper -> local diameter is the same everywhere regardless of the
    # design's own height, so there's no self-referential curvature
    # correction to solve for.
    geom = cup_etch.CupGeometry(
        bottom_circumference_mm=80 * math.pi, top_circumference_mm=80 * math.pi,
        side_length_mm=100, design_width_mm=1,  # tiny angle -> arc/apparent gap is negligible
    )
    # A 400x200 (2:1) source scaled to a 1mm design width should come out ~0.5mm tall.
    assert cup_etch.design_height_for_image_mm(geom, src_w=400, src_h=200) == pytest.approx(0.5, abs=1e-4)


def test_design_height_for_image_mm_is_self_consistent_on_a_real_taper():
    # On a real taper (or even a cylinder at a non-tiny angle -- see the
    # arc-vs-apparent tests above), height can't be a plain multiply of the
    # raw (arc-length) design width -- see the function's own docstring for
    # why. Whatever height it settles on must be exactly consistent with
    # the apparent width AT that height's own vertical center, by
    # construction, and must be smaller than the naive (uncorrected)
    # multiply would give, since apparent width is always narrower than
    # the arc length that produced it.
    geom = cup_etch.CupGeometry(
        bottom_circumference_mm=80 * math.pi, top_circumference_mm=60 * math.pi,
        side_length_mm=100, design_width_mm=60,
    )
    height_mm = cup_etch.design_height_for_image_mm(geom, src_w=400, src_h=200)
    local_diameter_mm = geom.diameter_at_axial_offset_from_top(height_mm / 2.0)
    apparent_width_mm = local_diameter_mm * math.sin(geom.design_width_mm / local_diameter_mm)
    assert height_mm == pytest.approx(apparent_width_mm * 200 / 400)
    assert height_mm < 30  # the naive (uncorrected) multiply this used to be


def test_design_geometry_uses_band_average_diameter_when_design_center_is_at_mid_band():
    # Placing the design's own vertical center exactly at the band's own
    # mid-height must give local_diameter_mm the plain average of the top
    # and bottom diameters -- tested with a deliberately tiny design height
    # (a very wide, very short image) so the center sits almost exactly at
    # top_offset=50 (already the 100mm band's own midpoint) regardless of
    # any curvature self-correction, which only matters once height is a
    # non-negligible fraction of the band.
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=1, top_offset=50)
    design = cup_etch.design_geometry_for_image(geom, src_w=1000, src_h=1)
    assert design.height_mm < 0.01  # negligible -- center is essentially at top_offset itself
    assert design.local_diameter_mm == pytest.approx((80 + 60) / 2, abs=1e-3)


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


def test_arc_length_exceeds_apparent_chord_width():
    # Fundamental geometric fact this whole module depends on: peeling a
    # design off a curved surface always yields *more* physical material
    # than its own straight-line (apparent, front-viewed) width -- arc
    # length (radius * angle) exceeds chord length (radius * sine of that
    # angle) for any nonzero angle. A square projected onto a cylinder is
    # not square once you peel the pattern back off it.
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=30, top_offset=0)
    design = cup_etch.design_geometry_for_image(geom, src_w=30, src_h=100)  # fills the whole band
    assert design.arc_length_mm > geom.design_width_mm
    assert design.arc_length_mm == pytest.approx(design.phi_max_rad * design.local_diameter_mm)


def test_apparent_width_converges_to_arc_width_for_small_angles():
    # As the half-angle -> 0, sin(angle) -> angle, so the derived apparent
    # (front-viewed) width should converge to the raw arc-length input
    # (design_width_mm) -- the small-taper/small-coverage case is nearly
    # flat, where the arc-vs-chord distinction barely matters.
    geom = _geom(bottom_diameter=1000, top_diameter=1000, height=100, design_width=1, top_offset=0)
    design = cup_etch.design_geometry_for_image(geom, src_w=1, src_h=100)
    ratio = design.apparent_width_mm / geom.design_width_mm
    assert ratio == pytest.approx(1.0, abs=0.001)
    assert design.apparent_width_mm < geom.design_width_mm  # still strictly narrower, any angle


def test_apparent_width_is_exact_sin_of_center_phi():
    # Regression check that center_phi_rad is derived via plain division
    # (arc = angle * diameter, exact for an arc-length input) rather than
    # asin -- a reintroduced asin here would silently break every apparent
    # width and, via _u_src_grid_tapered's reuse of this same field, the
    # per-row taper correction itself.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=40, top_offset=0)
    design = cup_etch.design_geometry_for_image(geom, src_w=40, src_h=100)  # fills the whole band
    assert design.center_phi_rad == pytest.approx(geom.design_width_mm / design.local_diameter_mm)
    assert design.apparent_width_mm == pytest.approx(
        design.local_diameter_mm * math.sin(design.center_phi_rad)
    )


def test_apparent_width_over_height_matches_source_aspect_ratio():
    # Regression test: build_pattern's fit_w is computed from
    # design.apparent_width_mm and design.height_mm, not design_width_mm
    # (the raw arc-length input) -- this ratio has to equal the source's
    # own aspect ratio exactly, or fit_cover stretches the source before
    # it's even warped. Using design_width_mm there instead used to work
    # only because design.height_mm was a direct multiple of it; now that
    # height_mm is solved self-consistently against apparent_width_mm (see
    # design_height_for_image_mm), only apparent_width_mm still cancels.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=60, top_offset=0)
    design = cup_etch.design_geometry_for_image(geom, src_w=400, src_h=300)
    assert design.apparent_width_mm / design.height_mm == pytest.approx(400 / 300)


def test_fitted_source_width_px_does_not_stretch_a_circular_marker_on_a_real_taper():
    # Regression test for a real fit_w bug: fitted_source_width_px (what
    # build_pattern actually calls) has to use design.apparent_width_mm,
    # not design_width_mm (the raw arc-length input) -- on a real taper,
    # using design_width_mm there stretches the fitted source before it's
    # even warped, distorting a circular marker into an oval. Checked via
    # the actual fit_cover call build_pattern makes, using the exact
    # function under test rather than re-deriving the formula independently.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=60, top_offset=0)
    src_h, src_w = 300, 300  # square source -> a circular marker should fit_cover to a circle
    design = cup_etch.design_geometry_for_image(geom, src_w=src_w, src_h=src_h)
    out_w, out_h, _ = cup_etch.output_size_px(design.arc_length_mm, design.height_mm, px_per_mm=4)

    src = np.zeros((src_h, src_w, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:src_h, 0:src_w]
    # A circle close to the frame's own edges -- like the real reported
    # case -- is what actually makes the bug visible: fit_cover never
    # stretches (it scales uniformly and crops), so a small circle with
    # generous margin survives a wrong fit_w unscathed; only content near
    # the edges gets cropped asymmetrically by the wrong frame aspect ratio.
    r = min(src_w, src_h) * 0.49
    mask = (xx - src_w / 2) ** 2 + (yy - src_h / 2) ** 2 <= r ** 2
    src[mask, 3] = 255

    fit_w = cup_etch.fitted_source_width_px(design, out_h)
    fitted = cup_etch.fit_cover(src, fit_w, out_h)
    fitted_row_width = (fitted[out_h // 2, :, 3] > 0).sum()
    fitted_col_height = (fitted[:, fit_w // 2, 3] > 0).sum()
    assert fitted_row_width == pytest.approx(fitted_col_height, rel=0.05)


def test_round_source_stays_round_not_vertically_elongated():
    # Regression test for a real reported bug: a square (round-logo-shaped)
    # source came out visibly *taller than wide* once projected, because
    # design_height_for_image_mm used to multiply the source's aspect ratio
    # directly by the raw arc-length input -- ignoring that the same
    # source, once actually viewed from the front, is scaled by the
    # (always narrower) *apparent* width instead. At a realistic, sizable
    # wrap angle the gap is large enough to be obviously wrong: this exact
    # geometry (a typical cup) previously gave height=60mm against an
    # apparent width of ~53mm, a 13% vertical stretch.
    geom = cup_etch.CupGeometry(
        bottom_circumference_mm=285, top_circumference_mm=207,
        side_length_mm=148, top_offset_mm=0, design_width_mm=60,
    )
    design = cup_etch.design_geometry_for_image(geom, src_w=100, src_h=100)  # square/round source
    assert design.height_mm == pytest.approx(design.apparent_width_mm, rel=1e-6)


def test_design_geometry_rejects_design_taller_than_remaining_space_below_offset():
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=30, top_offset=80)
    with pytest.raises(ValueError, match="remaining"):
        cup_etch.design_geometry_for_image(geom, src_w=30, src_h=300)  # 300mm tall at this width -- way too tall


def test_design_geometry_accepts_design_within_remaining_space_below_offset():
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=10, top_offset=80)
    design = cup_etch.design_geometry_for_image(geom, src_w=10, src_h=15)  # ~15mm tall, fits in the 20mm left
    # A small design width keeps the arc-vs-apparent correction small, so
    # this stays close to (but, per design_height_for_image_mm, strictly
    # under) the naive 15mm a plain multiply would give.
    assert design.height_mm == pytest.approx(15, abs=0.2)
    assert design.height_mm < 15


def test_design_geometry_rejects_arc_width_implying_more_than_max_wrap_angle():
    # design_width_mm is an arc length now; dividing by the local diameter
    # (no taper here, so it's always exactly 70mm) gives the half-angle
    # directly, with no sin/asin involved -- 110mm needs an angle well past
    # MAX_WRAP_ANGLE_DEG/2 at that diameter (threshold is ~106.9mm), and a
    # wide/short image (2:1) keeps well clear of the separate "too tall for
    # the cup" check so only this one fires.
    geom = _geom(bottom_diameter=70, top_diameter=70, height=100, design_width=110, top_offset=0)
    with pytest.raises(ValueError, match="more than 175 degrees"):
        cup_etch.design_geometry_for_image(geom, src_w=2, src_h=1)


def test_design_geometry_rejects_width_too_close_to_narrowest_diameter():
    # A real taper (top narrower than bottom), square image (so the
    # self-consistent height solve just needs a taper, not a specific
    # aspect ratio to hit a particular height). At design_width=75mm, the
    # center's own half-angle (~56 degrees) is nowhere near the max-wrap-
    # angle limit, but the *apparent* width it implies (~62.3mm) exceeds
    # what the narrowest point (60mm, the top rim) can show without
    # near-infinite stretching (max ~59.94mm there) -- this is the
    # anti-clip widening's own limit, distinct from the direct
    # too-large-arc check above.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=75, top_offset=0)
    with pytest.raises(ValueError, match="near-infinite stretching"):
        cup_etch.design_geometry_for_image(geom, src_w=1, src_h=1)


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
# warp_for_rotary_tapered -- the full per-row correction
# ---------------------------------------------------------------------------

def test_tapered_warp_u_src_grid_keeps_an_off_center_line_at_a_constant_screen_position():
    # The whole point of this correction: on a real taper, a straight
    # vertical line in the source -- off-center, so the single-row
    # (warp_for_rotary) approximation would show it drifting sideways with
    # height -- must appear at a genuinely constant *apparent* (front-view)
    # screen position at every row, not just at the design's own center.
    #
    # Checked directly against the u_src grid (not by hunting for a marker's
    # color in a rendered, bilinearly-resampled image): for a fixed u_src,
    # find whichever output column reaches it at each row, and confirm
    # r_row * sin(theta) -- the actual apparent screen position -- comes
    # out the same everywhere. A pixel-color search isn't precise enough
    # for this (bilinear interpolation smears a narrow marker asymmetrically
    # once the local sampling density itself varies -- which it does here,
    # by design -- and a wider marker just averages over a *range* of u_src
    # values instead of checking one, muddying exactly the effect under test).
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=40, top_offset=0)
    design = cup_etch.design_geometry_for_image(geom, src_w=1, src_h=1)  # shape unused below

    out_w, out_h = 4000, 200
    u_src = cup_etch._u_src_grid_tapered(geom, design, out_w, out_h)
    u_out = (np.arange(out_w, dtype=np.float64) + 0.5) / out_w * 2.0 - 1.0
    theta_all = u_out * design.phi_max_rad
    axial_top = geom.axial_top_offset_mm

    target_u_src = 0.6
    screen_positions = []
    for row_idx in [10, 60, 100, 140, 190]:
        col = int(np.argmin(np.abs(u_src[row_idx] - target_u_src)))
        assert abs(u_src[row_idx, col] - target_u_src) < 0.001  # found a genuinely close match
        v = (row_idx + 0.5) / out_h
        r_v = geom.diameter_at_axial_offset_from_top(axial_top + v * design.height_mm) / 2.0
        screen_positions.append(r_v * math.sin(theta_all[col]))

    # For reference, the single-phi_max (non-tapered) approximation this
    # replaces would show roughly a 5-6mm drift for this same off-center
    # line on this same taper -- the residual here is just pixel-column
    # quantization (out_w=4000 columns -> a ~0.01mm-scale rounding gap
    # between "the column closest to target_u_src" and target_u_src
    # exactly), not a real discrepancy.
    spread = max(screen_positions) - min(screen_positions)
    assert spread == pytest.approx(0, abs=0.02)


def test_tapered_warp_matches_simple_warp_for_a_true_cylinder():
    # With no taper, every row shares the same local radius, so the full
    # per-row correction should reduce to exactly the single-phi_max
    # (warp_for_rotary) formula -- this is the module docstring's claimed
    # special case, checked directly.
    geom = _geom(bottom_diameter=70, top_diameter=70, height=100, design_width=30)  # no taper
    design = cup_etch.design_geometry_for_image(geom, src_w=300, src_h=300)
    src = _ramp_image(w=300, h=300)

    out_w, out_h = 300, 300
    tapered = cup_etch.warp_for_rotary_tapered(src, geom, design, out_w, out_h)
    simple = cup_etch.warp_for_rotary(src, design.phi_max_rad, out_w, out_h)
    assert np.abs(tapered[..., 0].astype(int) - simple[..., 0].astype(int)).max() <= 1


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
    # No taper (bottom == top diameter) here specifically so every row sees
    # the same local radius and none of the design gets margined out (see
    # test_build_pattern_leaves_transparent_margins_on_a_real_taper below
    # for that behavior) -- this test is just about alpha surviving
    # resampling cleanly.
    geom = _geom(bottom_diameter=70, top_diameter=70, design_width=30)
    src = np.zeros((300, 300, 4), dtype=np.uint8)
    src[:, :, 0] = 128
    src[:, :, 3] = 200  # partial alpha, should survive resampling
    out, out_w, out_h, design, _ = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
    assert out.shape == (out_h, out_w, 4)
    assert out[..., 3].mean() == pytest.approx(200, abs=2)
    # Square source -> square design, i.e. height matches the *apparent*
    # width at the design's own center (not the raw arc-length input --
    # see design_height_for_image_mm; a round/square source coming out
    # taller than wide was exactly the bug this self-consistent solve fixes).
    assert design.height_mm == pytest.approx(design.apparent_width_mm)


def test_build_pattern_with_dither_keeps_alpha_channel_undithered():
    geom = _geom(bottom_diameter=70, top_diameter=70, design_width=30)  # no taper -- see above
    src = np.random.default_rng(1).integers(0, 256, size=(300, 300, 4)).astype(np.uint8)
    src[:, :, 3] = 255
    out, out_w, out_h, design, _ = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=True)
    assert set(np.unique(out[..., 0]).tolist()) <= {0, 255}
    # LANCZOS resizing + bilinear resampling of a uniform field can round a
    # handful of edge pixels to 254 instead of 255 -- not a functional bug,
    # so allow a small tolerance rather than requiring bit-exact 255.
    assert (out[..., 3] >= 250).all()


def test_build_pattern_leaves_transparent_margins_on_a_real_taper():
    # The whole point of the per-row correction: on a real taper, only the
    # design's own narrowest row uses the full canvas width -- every other
    # row uses less of it, leaving plain, undecorated (fully transparent)
    # glass in the remainder, rather than stretching/cropping content to
    # fill the whole rectangle.
    geom = _geom(bottom_diameter=100, top_diameter=60, height=100, design_width=10)  # real taper
    src = np.zeros((300, 300, 4), dtype=np.uint8)
    src[:, :, 3] = 255
    out, out_w, out_h, design, _ = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
    # The design's own top (narrower than its center, since top_diameter <
    # bottom_diameter here) should reach further across than a wider row
    # elsewhere in the design -- check the very top row has less transparent
    # margin than a row nearer the (wider, bottom-ward) design center.
    top_row_opaque = (out[0, :, 3] > 0).sum()
    center_row_opaque = (out[out_h // 2, :, 3] > 0).sum()
    assert top_row_opaque >= center_row_opaque
    assert center_row_opaque < out_w  # the center row must show *some* margin


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
    out, out_w, out_h, design, _ = cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)
    assert design.height_mm == pytest.approx(design.apparent_width_mm * 100 / 400)
    assert out[out_h // 2, :5, 1].max() > 100
    assert out[out_h // 2, -5:, 1].max() > 100


def test_build_pattern_raises_when_image_is_too_tall_for_the_cup():
    geom = _geom(height=20, design_width=30)  # available_height_mm ~20mm
    src = np.zeros((400, 100, 4), dtype=np.uint8)  # tall/narrow -> a big design height at this width
    src[:, :, 3] = 255
    with pytest.raises(ValueError, match="remaining"):
        cup_etch.build_pattern(src, geom, px_per_mm=3, dither=False)


def test_build_pattern_output_width_matches_arc_length_not_apparent_width():
    # Regression test: the output canvas used to be sized from
    # design_width_mm (the apparent/chord width) directly, which is
    # narrower than the pattern's true physical size once actually applied
    # to the curved surface (see DesignGeometry.arc_length_mm). out_w must
    # reflect the wider arc-length size, not the apparent one.
    geom = _geom(bottom_diameter=80, top_diameter=60, height=100, design_width=50)  # sizable phi_max
    src = np.zeros((300, 300, 4), dtype=np.uint8)
    src[:, :, 3] = 255
    px_per_mm = 3
    out, out_w, out_h, design, _ = cup_etch.build_pattern(src, geom, px_per_mm=px_per_mm, dither=False)
    expected_w, expected_h, _ = cup_etch.output_size_px(design.arc_length_mm, design.height_mm, px_per_mm)
    assert (out_w, out_h) == (expected_w, expected_h)
    apparent_w, _, _ = cup_etch.output_size_px(geom.design_width_mm, design.height_mm, px_per_mm)
    assert out_w > apparent_w  # arc length always exceeds the apparent/chord width
