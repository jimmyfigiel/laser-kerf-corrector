"""Front-projection warp for etching a design onto a tapered cup's
front-facing panel with a rotary laser attachment.

A rotary attachment replaces one machine axis with rotation: the cup spins
in place while the laser head only moves along the other (axial) axis.
That means an output pixel *column* always corresponds to the same
rotation angle at every row, regardless of the cup's local diameter at
that row -- the taper never enters the horizontal mapping. What the taper
*does* affect is the physical size of the output (the diameter used to
convert a rotation angle into a real width is the cup's diameter at the
design's own vertical center -- see DesignGeometry.local_diameter_mm,
which depends on both the image's own aspect ratio and where on the taper
it's placed) and, naturally, how wide the finished etching *appears* at
the narrow vs. wide end once it's actually on the cup -- that's the
taper's own true shape showing through, not a distortion to correct.

The correction implemented here targets a different effect: viewed
straight on (an orthographic front view), a curved surface foreshortens
much more sharply near its own silhouette edges than at its front-center,
where the surface is nearly tangent to the view direction -- the same
reason a photo wrapped around any round object looks "pinched" at the
edges. A plain linear wrap (equal rotation per output column, which is all
a rotary attachment can physically do) would carry that foreshortening
straight through to the finished etching. The fix has to happen in the
source image instead: pre-warp column positions through an arcsine so
that, once the front-view foreshortening is applied on top of it, the
combined result reads as the original, undistorted rectangle.

Derivation: an orthographic front view of a circular cross-section of
radius r projects a point at angle phi (0 = facing the viewer) to screen
position r*sin(phi). Requiring that screen position be proportional to
source column u (so the finished etching, viewed head-on, reproduces the
source image's own proportions) gives phi(u) = asin(u * sin(phi_max)),
where phi_max is the half-angle of the front-facing window in use. Because
a rotary attachment maps output column linearly to rotation angle
(u_out = phi / phi_max), inverting for the source column to sample at a
given output column gives:

    u_src(u_out) = sin(u_out * phi_max) / sin(phi_max)

which is exactly the r-independent (taper-independent) formula used below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

MAX_OUTPUT_PX = 2200  # keeps Floyd-Steinberg (a per-pixel Python loop) responsive

MAX_WRAP_ANGLE_DEG = 175.0  # see design_width_mm's validation below for why
_MAX_SIN_PHI_MAX = math.sin(math.radians(MAX_WRAP_ANGLE_DEG / 2.0))


@dataclass
class CupGeometry:
    """All five inputs are things you can measure directly on a real cup
    with a soft tape measure and no math: wrap the tape around the top and
    bottom rims for the two circumferences, and lay it flat along the
    tapered side for the side length and the top offset (both the true
    along-the-surface slant distance, not vertical height -- that's not
    directly measurable without already knowing the taper). The design's
    own axial height and how much of the circumference it covers are both
    derived, not entered separately -- see design_geometry_for_image, since
    both also depend on the source image's own aspect ratio, not on this
    geometry alone."""

    bottom_circumference_mm: float
    top_circumference_mm: float
    side_length_mm: float
    design_width_mm: float
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
    local_diameter_mm: float
    phi_max_rad: float

    @property
    def wrap_angle_deg(self) -> float:
        return math.degrees(self.phi_max_rad) * 2.0


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

    max_width = local_diameter_mm * _MAX_SIN_PHI_MAX
    if geom.design_width_mm >= max_width:
        raise ValueError(
            f"Design width ({geom.design_width_mm:g}mm) is too close to the diameter at this "
            f"design's own position ({local_diameter_mm:.1f}mm) -- keep it under {max_width:.1f}mm, "
            "or the edges would need near-infinite stretching (a front view can never be wider "
            "than the diameter itself)."
        )

    phi_max_rad = math.asin(geom.design_width_mm / local_diameter_mm)
    return DesignGeometry(height_mm=height_mm, local_diameter_mm=local_diameter_mm, phi_max_rad=phi_max_rad)


def output_size_px(width_mm: float, height_mm: float, px_per_mm: float) -> tuple[int, int]:
    w = max(1, round(width_mm * px_per_mm))
    h = max(1, round(height_mm * px_per_mm))
    if max(w, h) > MAX_OUTPUT_PX:
        scale = MAX_OUTPUT_PX / max(w, h)
        w = max(1, round(w * scale))
        h = max(1, round(h * scale))
    return w, h


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
    """sx: (out_w,) source x-coordinates. sy: (out_h,) source y-coordinates.
    Returns (out_h, out_w, channels) bilinearly resampled from source."""
    src_h, src_w = source.shape[:2]

    x0 = np.clip(np.floor(sx).astype(np.int64), 0, src_w - 1)
    x1 = np.clip(x0 + 1, 0, src_w - 1)
    fx = (sx - x0).reshape(1, -1, 1)

    y0 = np.clip(np.floor(sy).astype(np.int64), 0, src_h - 1)
    y1 = np.clip(y0 + 1, 0, src_h - 1)
    fy = (sy - y0).reshape(-1, 1, 1)

    src_f = source.astype(np.float64)
    top = src_f[y0][:, x0] * (1 - fx) + src_f[y0][:, x1] * fx
    bottom = src_f[y1][:, x0] * (1 - fx) + src_f[y1][:, x1] * fx
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
                   dither: bool) -> tuple[np.ndarray, int, int, DesignGeometry]:
    """Returns (rgba_uint8_array, out_w, out_h, design)."""
    src_h, src_w = image_rgba.shape[:2]
    design = design_geometry_for_image(geom, src_w, src_h)

    out_w, out_h = output_size_px(geom.design_width_mm, design.height_mm, px_per_mm)
    # fit_cover only has rounding-sized slack to take up here, since out_w:out_h
    # is (by design_height_for_image_mm's construction) already the source's
    # own aspect ratio -- the whole image ends up visible, none of it cropped.
    fitted = fit_cover(image_rgba, out_w, out_h)
    warped = warp_for_rotary(fitted, design.phi_max_rad, out_w, out_h)

    if dither:
        gray = warped[..., :3].astype(np.float64) @ _GRAY_WEIGHTS
        dithered = floyd_steinberg_dither(gray)
        out = np.dstack([dithered, dithered, dithered, warped[..., 3]]).astype(np.uint8)
    else:
        out = warped

    return out, out_w, out_h, design
