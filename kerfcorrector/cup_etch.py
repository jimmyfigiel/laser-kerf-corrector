"""Front-projection warp for etching a design onto a tapered cup's
front-facing panel with a rotary laser attachment.

A rotary attachment replaces one machine axis with rotation: the cup spins
in place while the laser head only moves along the other (axial) axis.
That means an output pixel *column* always corresponds to the same
rotation angle at every row (given a fixed calibration diameter and a
fixed total-image-width, both set once for the whole job) -- a rotary
simply can't apply a different angle to the same column at different
rows. What varies by row is the cup's own *local radius* wherever that
row actually sits, and that's exactly what a naive, single-diameter
correction misses.

Derivation: an orthographic front view of a circular cross-section of
local radius r(v) at height v projects a point etched at angle phi to
screen position r(v)*sin(phi). Requiring that screen position be
proportional to source column u, *at every row*, not just one reference
row (so a straight vertical line in the source stays straight -- not
diagonal -- at every height, not merely proportioned correctly at the
design's own center) means the etched angle for a given source column
has to depend on r(v):

    phi(u, v) = asin(u * sin(phi_max) * r_center / r(v))

where r_center is the radius at the design's own vertical center and
phi_max (called center_phi_rad below) is the half-angle needed there
alone -- derived from design_width_mm, which is the arc length measured
directly along the curved glass at the design's own vertical center (what
you get wrapping a tape measure against the actual surface there; the
apparent/front-viewed width at that same spot is a *derived* quantity,
DesignGeometry.apparent_width_mm, always a bit narrower). Because a rotary
maps output column
linearly to rotation angle *uniformly across every row*
(phi = u_out * phi_max_canvas, a single canvas-wide constant -- see
DesignGeometry.phi_max_rad, which is wider than the phi_max implied by
design_width_mm alone whenever the design spans any taper), inverting for
the source column to sample at a given (output column, output row) gives:

    u_src(u_out, v) = sin(u_out * phi_max_canvas) * r(v) / (r_center * sin(phi_max))

implemented in warp_for_rotary_tapered below. Rows narrower than r_center
reach their own source edges before the canvas's own angular range runs
out (values fall outside [-1, 1], left as transparent, undecorated glass);
the canvas is sized so the design's single *narrowest* row is exactly the
one that uses the full available angular range, so no row's content ever
gets clipped. warp_for_rotary (the single-phi_max, row-independent
version) remains as the simpler building block this generalizes from --
exact for a true cylinder, where r(v) is constant and the two formulas
coincide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

MAX_OUTPUT_PX = 2200  # keeps Floyd-Steinberg (a per-pixel Python loop) responsive

MAX_WRAP_ANGLE_DEG = 175.0  # see design_width_mm's validation below for why
_MAX_PHI_RAD = math.radians(MAX_WRAP_ANGLE_DEG / 2.0)
_MAX_SIN_PHI_MAX = math.sin(_MAX_PHI_RAD)


@dataclass
class CupGeometry:
    """All five inputs are things you can measure directly on a real cup
    with a soft tape measure and no math: wrap the tape around the top and
    bottom rims for the two circumferences, and lay it flat along the
    tapered side for the side length and the top offset (both the true
    along-the-surface slant distance, not vertical height -- that's not
    directly measurable without already knowing the taper). design_width_mm
    is likewise a direct, along-the-surface measurement: wrap the tape
    across the curved glass at the design's own vertical center, the same
    way you would to gauge how wide a label would need to be cut to wrap
    that band -- NOT how wide the design looks from the front (that's a
    derived quantity, DesignGeometry.apparent_width_mm, always a bit
    narrower than this arc length for any curved surface). The design's
    own axial height and how much of the circumference it covers are both
    derived, not entered separately -- see design_geometry_for_image, since
    both also depend on the source image's own aspect ratio, not on this
    geometry alone."""

    bottom_circumference_mm: float
    top_circumference_mm: float
    side_length_mm: float
    design_width_mm: float  # arc length along the glass at the design's own vertical center -- see above
    top_offset_mm: float = 0.0  # slant distance from the top rim to the top of the design

    def __post_init__(self):
        if self.bottom_circumference_mm <= 0 or self.top_circumference_mm <= 0:
            raise ValueError("Circumferences must be positive.")
        if self.side_length_mm <= 0:
            raise ValueError("Side length must be positive.")
        if self.design_width_mm <= 0:
            raise ValueError("Design width must be positive.")
        if self.top_offset_mm < 0:
            raise ValueError("Distance from top can't be negative.")

        delta_r = abs(self.bottom_radius_mm - self.top_radius_mm)
        if self.side_length_mm <= delta_r:
            raise ValueError(
                f"A side length of {self.side_length_mm:g}mm is too short for this much taper -- "
                f"it has to be more than the difference in the two radii ({delta_r:.1f}mm), or "
                "a straight side of that length can't reach between the two rims."
            )

        if self.top_offset_mm >= self.side_length_mm:
            raise ValueError(
                f"Distance from top ({self.top_offset_mm:g}mm) has to be less than the side "
                f"length ({self.side_length_mm:g}mm), or there's no room left for a design at all."
            )

    @property
    def bottom_radius_mm(self) -> float:
        return self.bottom_circumference_mm / (2 * math.pi)

    @property
    def top_radius_mm(self) -> float:
        return self.top_circumference_mm / (2 * math.pi)

    @property
    def available_height_mm(self) -> float:
        # The side length is the slant (along-the-surface) distance between
        # the rims; together with the difference in radii it forms a right
        # triangle whose other leg is the axial height actually available
        # for a design on this cup's tapered side. This bounds how tall a
        # design can be (see design_geometry_for_image below) -- it isn't
        # itself the design's height, since the design is scaled to fit the
        # chosen width while keeping the source image's own proportions,
        # not stretched/cropped to fill this available height.
        delta_r = self.bottom_radius_mm - self.top_radius_mm
        return math.sqrt(self.side_length_mm ** 2 - delta_r ** 2)

    @property
    def axial_top_offset_mm(self) -> float:
        # top_offset_mm is a slant distance (tape-measurable); convert to
        # the equivalent axial distance using the band's own slant-to-axial
        # ratio, which is constant along a straight taper.
        return self.top_offset_mm * (self.available_height_mm / self.side_length_mm)

    def diameter_at_axial_offset_from_top(self, axial_offset_mm: float) -> float:
        """Diameter is linear along the taper -- interpolate between the
        top and bottom rim diameters by how far down (in axial mm from the
        top) the given point sits."""
        frac = axial_offset_mm / self.available_height_mm
        top_d, bottom_d = 2 * self.top_radius_mm, 2 * self.bottom_radius_mm
        return top_d + (bottom_d - top_d) * frac


@dataclass
class DesignGeometry:
    """Everything about how a specific image, placed at geom.top_offset_mm,
    actually sits on the cup -- depends on the image's own aspect ratio and
    where vertically it's placed, not on CupGeometry alone (a shorter image
    higher up the taper sees a different local diameter than the same
    image lower down, even on an identical cup)."""

    height_mm: float
    local_diameter_mm: float  # diameter at the design's own vertical center
    center_phi_rad: float  # design_width_mm / local_diameter_mm -- the design's own (un-widened)
    # half-angle at its vertical center; arc = angle * diameter is exact (design_width_mm IS an
    # arc length), so this needs no trig, unlike apparent_width_mm below.
    phi_max_rad: float  # the output CANVAS's own angular half-range (see module docstring) -- can
    # exceed center_phi_rad whenever the design spans any taper, since rows narrower than
    # local_diameter_mm need more angular range to show their own full (apparent) width.

    @property
    def wrap_angle_deg(self) -> float:
        return math.degrees(self.phi_max_rad) * 2.0

    @property
    def apparent_width_mm(self) -> float:
        # How wide the design actually *looks*, viewed head-on, at its own
        # vertical center -- always narrower than design_width_mm (the arc
        # length you measured on the glass) for any nonzero angle, the same
        # arc-vs-chord gap as arc_length_mm below, just at the design's own
        # angular range (center_phi_rad) rather than the wider canvas one.
        return self.local_diameter_mm * math.sin(self.center_phi_rad)

    @property
    def arc_length_mm(self) -> float:
        # The pattern's own true physical width once actually applied to the
        # curved surface -- wider than apparent_width_mm above for the same
        # reason, but measured across the canvas's own (possibly wider,
        # anti-clip) angular range rather than just the center's. This is
        # the number to feed into the rotary attachment's own calibration:
        # a rotary converts "image width in mm" to a rotation angle via arc
        # length (angle = width / radius), not via chord length, since arc
        # length is the only relationship rotation can physically produce.
        return self.phi_max_rad * self.local_diameter_mm


def design_height_for_image_mm(geom: CupGeometry, src_w: int, src_h: int) -> float:
    """The physical height the design occupies once the source image is
    scaled -- preserving its own aspect ratio, never cropped or stretched
    -- to the chosen design width."""
    return geom.design_width_mm * src_h / src_w


def design_geometry_for_image(geom: CupGeometry, src_w: int, src_h: int) -> DesignGeometry:
    height_mm = design_height_for_image_mm(geom, src_w, src_h)
    top_offset_mm = geom.axial_top_offset_mm

    if top_offset_mm + height_mm > geom.available_height_mm + 1e-9:
        remaining = geom.available_height_mm - top_offset_mm
        raise ValueError(
            f"At a design width of {geom.design_width_mm:g}mm, this image would be "
            f"{height_mm:.1f}mm tall -- more than the {remaining:.1f}mm remaining below the "
            f"{geom.top_offset_mm:g}mm offset from the top on this "
            f"{geom.available_height_mm:.1f}mm-tall side. Use a narrower design width, a smaller "
            "offset, or an image with a wider (less tall-and-narrow) aspect ratio."
        )

    center_offset_mm = top_offset_mm + height_mm / 2.0
    local_diameter_mm = geom.diameter_at_axial_offset_from_top(center_offset_mm)

    # design_width_mm is an arc length (see CupGeometry's docstring), so its
    # relationship to the design's own half-angle at its vertical center is
    # exact and needs no trig: arc = angle * diameter.
    center_phi_rad = geom.design_width_mm / local_diameter_mm
    if center_phi_rad >= _MAX_PHI_RAD:
        raise ValueError(
            f"An arc length of {geom.design_width_mm:g}mm at this design's own diameter "
            f"({local_diameter_mm:.1f}mm) would wrap more than {MAX_WRAP_ANGLE_DEG:g} degrees "
            "around the cup -- use a smaller design width, or reposition the design somewhere "
            "wider (a larger local diameter)."
        )
    apparent_width_mm = local_diameter_mm * math.sin(center_phi_rad)

    # Diameter is linear along the taper, so its extremes across the
    # design's own height span are just at the design's own top and bottom
    # -- no need to sample in between. The narrowest of the two needs the
    # most angular range to show the design's own full (apparent) width at
    # that row (see module docstring); the canvas has to be sized to that
    # row so no row's content ever gets clipped -- wider rows then use less
    # than the full canvas width, leaving plain, undecorated glass either side.
    top_d = geom.diameter_at_axial_offset_from_top(top_offset_mm)
    bottom_d = geom.diameter_at_axial_offset_from_top(top_offset_mm + height_mm)
    narrowest_diameter = min(top_d, bottom_d)

    sin_needed = apparent_width_mm / narrowest_diameter
    if sin_needed >= _MAX_SIN_PHI_MAX:
        raise ValueError(
            f"This design's narrowest point (diameter {narrowest_diameter:.1f}mm, within its own "
            f"height range) can't show the full {apparent_width_mm:.1f}mm apparent width without "
            "its edges needing near-infinite stretching there -- keep the design width smaller, "
            "make it shorter (less of the taper), or reposition it somewhere with less taper."
        )
    phi_max_canvas = math.asin(sin_needed)

    return DesignGeometry(
        height_mm=height_mm, local_diameter_mm=local_diameter_mm,
        center_phi_rad=center_phi_rad, phi_max_rad=phi_max_canvas,
    )


def output_size_px(width_mm: float, height_mm: float, px_per_mm: float) -> tuple[int, int, float]:
    """Returns (w, h, effective_px_per_mm). effective_px_per_mm matches the
    requested px_per_mm exactly unless the MAX_OUTPUT_PX cap kicked in, in
    which case it's scaled down along with w/h -- callers need this actual
    achieved value (not the originally requested px_per_mm) to embed a DPI
    in the output file that's consistent with its own pixel dimensions, so
    the physical size the file reports is always correct even when the
    resolution had to be capped."""
    w = max(1, round(width_mm * px_per_mm))
    h = max(1, round(height_mm * px_per_mm))
    effective_px_per_mm = px_per_mm
    if max(w, h) > MAX_OUTPUT_PX:
        scale = MAX_OUTPUT_PX / max(w, h)
        w = max(1, round(w * scale))
        h = max(1, round(h * scale))
        effective_px_per_mm = px_per_mm * scale
    return w, h, effective_px_per_mm


def fit_cover(image_rgba: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Scale + center-crop so the source fills the target aspect ratio
    exactly, without letterboxing or stretching -- the only resizing this
    module does that isn't the deliberate front-projection warp."""
    src_h, src_w = image_rgba.shape[:2]
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = Image.fromarray(image_rgba, mode="RGBA").resize((new_w, new_h), Image.LANCZOS)
    arr = np.array(resized)
    x0 = (new_w - target_w) // 2
    y0 = (new_h - target_h) // 2
    return arr[y0:y0 + target_h, x0:x0 + target_w]


def _bilinear_sample(source: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """sx: (out_w,) source x-coordinates shared by every row, or (out_h,
    out_w) a different source x-coordinate per row (needed once the warp
    accounts for a row-varying local radius -- see warp_for_rotary_tapered).
    sy: (out_h,) source y-coordinates. Returns (out_h, out_w, channels)
    bilinearly resampled from source."""
    src_h, src_w = source.shape[:2]
    sx = np.broadcast_to(sx, (sy.shape[0], sx.shape[-1]))

    x0 = np.clip(np.floor(sx).astype(np.int64), 0, src_w - 1)
    x1 = np.clip(x0 + 1, 0, src_w - 1)
    fx = (sx - x0)[..., None]

    y0 = np.clip(np.floor(sy).astype(np.int64), 0, src_h - 1)
    y1 = np.clip(y0 + 1, 0, src_h - 1)
    fy = (sy - y0).reshape(-1, 1, 1)
    y0_2d = np.broadcast_to(y0[:, None], x0.shape)
    y1_2d = np.broadcast_to(y1[:, None], x0.shape)

    src_f = source.astype(np.float64)
    top = src_f[y0_2d, x0] * (1 - fx) + src_f[y0_2d, x1] * fx
    bottom = src_f[y1_2d, x0] * (1 - fx) + src_f[y1_2d, x1] * fx
    out = top * (1 - fy) + bottom * fy
    return np.clip(out, 0, 255).astype(np.uint8)


def warp_for_rotary(source: np.ndarray, phi_max_rad: float, out_w: int, out_h: int) -> np.ndarray:
    """source must already be fit (see fit_cover) to the out_w:out_h aspect
    ratio -- this only applies the front-projection horizontal correction
    (see module docstring) plus a direct proportional vertical resample;
    the vertical axis needs no warp since axial position on a rotary job
    maps straight through with no foreshortening."""
    phi_max = phi_max_rad
    sin_phi_max = math.sin(phi_max)

    u_out = (np.arange(out_w, dtype=np.float64) + 0.5) / out_w * 2.0 - 1.0
    u_src = np.clip(np.sin(u_out * phi_max) / sin_phi_max, -1.0, 1.0)

    src_h, src_w = source.shape[:2]
    sx = (u_src + 1.0) / 2.0 * (src_w - 1)
    sy = (np.arange(out_h, dtype=np.float64) + 0.5) / out_h * (src_h - 1)

    return _bilinear_sample(source, sx, sy)


def _u_src_grid_tapered(geom: CupGeometry, design: DesignGeometry,
                         out_w: int, out_h: int) -> np.ndarray:
    """The (out_h, out_w) grid of normalized source column positions (see
    warp_for_rotary_tapered and the module docstring for the derivation),
    split out on its own so it's directly checkable without going through
    pixel-color detection on a rendered, bilinearly-resampled image (which
    isn't precise enough to verify a sub-pixel geometric claim like "this
    stays at a constant screen position across rows"). Values outside
    [-1, 1] mean "no design content here" (see warp_for_rotary_tapered)."""
    phi_max_canvas = design.phi_max_rad
    center_phi_max = design.center_phi_rad
    r_center = design.local_diameter_mm / 2.0

    v = (np.arange(out_h, dtype=np.float64) + 0.5) / out_h  # 0..1, top to bottom of the design
    axial = geom.axial_top_offset_mm + v * design.height_mm
    r_row = geom.diameter_at_axial_offset_from_top(axial) / 2.0  # (out_h,)

    u_out = (np.arange(out_w, dtype=np.float64) + 0.5) / out_w * 2.0 - 1.0  # (out_w,)
    theta = u_out * phi_max_canvas  # (out_w,)

    # u_src varies by both row and column: a row narrower than r_center
    # reaches its own source edges (|u_src| = 1) before the canvas's own
    # angular range runs out; a row wider than r_center would need *more*
    # than the canvas's own range to reach its edges, so it simply can't --
    # values beyond [-1, 1] mark "no design content here."
    return (r_row[:, None] * np.sin(theta)[None, :]) / (r_center * math.sin(center_phi_max))  # (out_h, out_w)


def warp_for_rotary_tapered(source: np.ndarray, geom: CupGeometry, design: DesignGeometry,
                             out_w: int, out_h: int) -> np.ndarray:
    """Like warp_for_rotary, but accounts for the cup's actual local radius
    at *every* row, not just a single center-row snapshot -- necessary to
    keep vertical lines in the source genuinely straight (not merely
    proportioned correctly) at every height, not only at the design's own
    vertical center. See the module docstring for the derivation.

    source must already be fit (see fit_cover) to the design's own aspect
    ratio (design_width_mm x design.height_mm), NOT to out_w:out_h --
    design.phi_max_rad (the canvas's own angular range) is generally wider
    than what design_width_mm alone implies, and rows away from the
    design's own narrowest point use less than that full range, leaving the
    remainder fully transparent (plain, undecorated glass there)."""
    u_src = _u_src_grid_tapered(geom, design, out_w, out_h)
    valid = (u_src >= -1.0) & (u_src <= 1.0)
    u_src_clipped = np.clip(u_src, -1.0, 1.0)

    src_h, src_w = source.shape[:2]
    sx = (u_src_clipped + 1.0) / 2.0 * (src_w - 1)  # (out_h, out_w)
    sy = (np.arange(out_h, dtype=np.float64) + 0.5) / out_h * (src_h - 1)  # (out_h,)

    sampled = _bilinear_sample(source, sx, sy)
    # Force both RGB and alpha to a blank state where the design doesn't
    # reach -- not just alpha -- in case downstream engraving software
    # doesn't respect the alpha channel and would otherwise etch a stray
    # copy of whatever edge pixel got clamped into range there.
    sampled[~valid] = (255, 255, 255, 0)
    return sampled


def floyd_steinberg_dither(gray: np.ndarray) -> np.ndarray:
    """Error-diffusion dither to pure black/white, for engraving
    continuous-tone photos with a laser that can only mark or not mark a
    given spot -- a flat threshold alone would lose all the shading."""
    buf = gray.astype(np.float64).copy()
    h, w = buf.shape
    for y in range(h):
        row_is_last = y + 1 >= h
        for x in range(w):
            old = buf[y, x]
            new = 255.0 if old >= 128.0 else 0.0
            buf[y, x] = new
            err = old - new
            if err == 0.0:
                continue
            if x + 1 < w:
                buf[y, x + 1] += err * (7 / 16)
            if not row_is_last:
                if x > 0:
                    buf[y + 1, x - 1] += err * (3 / 16)
                buf[y + 1, x] += err * (5 / 16)
                if x + 1 < w:
                    buf[y + 1, x + 1] += err * (1 / 16)
    return np.clip(buf, 0, 255).astype(np.uint8)


_GRAY_WEIGHTS = np.array([0.299, 0.587, 0.114])


def build_pattern(image_rgba: np.ndarray, geom: CupGeometry, px_per_mm: float,
                   dither: bool) -> tuple[np.ndarray, int, int, DesignGeometry, float]:
    """Returns (rgba_uint8_array, out_w, out_h, design, effective_px_per_mm).
    effective_px_per_mm is what the output's pixel dimensions actually
    correspond to (see output_size_px) -- use it, not the requested
    px_per_mm, to compute a DPI to embed in the saved file, so the file's
    own physical size always matches its pixel dimensions exactly."""
    src_h, src_w = image_rgba.shape[:2]
    design = design_geometry_for_image(geom, src_w, src_h)

    # Fit the source at its own aspect ratio (design_width_mm x
    # design.height_mm -- matches the source's own proportions exactly, so
    # fit_cover has only rounding-sized slack to take up, never cropping).
    # The final output is wider than that: out_w is sized to the arc length
    # (see DesignGeometry.arc_length_mm), not the design width alone, since
    # that's the pattern's real physical size once actually on the cup.
    # warp_for_rotary_tapered already samples from the fitted source by its
    # own dimensions regardless of the requested output width, so simply
    # asking it for the wider out_w directly (no separate resize step)
    # produces the correctly-warped result at the physically-correct size.
    out_w, out_h, effective_px_per_mm = output_size_px(design.arc_length_mm, design.height_mm, px_per_mm)
    fit_w = max(1, round(out_h * geom.design_width_mm / design.height_mm))
    fitted = fit_cover(image_rgba, fit_w, out_h)
    warped = warp_for_rotary_tapered(fitted, geom, design, out_w, out_h)

    if dither:
        gray = warped[..., :3].astype(np.float64) @ _GRAY_WEIGHTS
        dithered = floyd_steinberg_dither(gray)
        out = np.dstack([dithered, dithered, dithered, warped[..., 3]]).astype(np.uint8)
    else:
        out = warped

    return out, out_w, out_h, design, effective_px_per_mm
