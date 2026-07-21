"""Front-projection warp for etching a design onto a tapered cup's
front-facing panel with a rotary laser attachment.

A rotary attachment replaces one machine axis with rotation: the cup spins
in place while the laser head only moves along the other (axial) axis.
That means an output pixel *column* always corresponds to the same
rotation angle at every row, regardless of the cup's local diameter at
that row -- the taper never enters the horizontal mapping. What the taper
*does* affect is the physical size of the output (the diameter used to
convert a rotation angle into a real width is the cup's diameter at the
mid-height of the design -- exact for a linear taper, since the midpoint
diameter is just the average of top and bottom) and, naturally, how wide
the finished etching *appears* at the narrow vs. wide end once it's
actually on the cup -- that's the taper's own true shape showing through,
not a distortion to correct.

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
    """All four inputs are things you can measure directly on a real cup
    with a soft tape measure and no math: wrap the tape around the top and
    bottom rims for the two circumferences, and lay it flat along the
    tapered side from the bottom rim to the top rim for the side length
    (the true along-the-surface slant distance, not the vertical height
    between rims -- that's not directly measurable without already knowing
    the taper). The design's own axial height and how much of the
    circumference it covers are both derived, not entered separately -- see
    height_mm and phi_max_rad below."""

    bottom_circumference_mm: float
    top_circumference_mm: float
    side_length_mm: float
    design_width_mm: float

    def __post_init__(self):
        if self.bottom_circumference_mm <= 0 or self.top_circumference_mm <= 0:
            raise ValueError("Circumferences must be positive.")
        if self.side_length_mm <= 0:
            raise ValueError("Side length must be positive.")
        if self.design_width_mm <= 0:
            raise ValueError("Design width must be positive.")

        delta_r = abs(self.bottom_radius_mm - self.top_radius_mm)
        if self.side_length_mm <= delta_r:
            raise ValueError(
                f"A side length of {self.side_length_mm:g}mm is too short for this much taper -- "
                f"it has to be more than the difference in the two radii ({delta_r:.1f}mm), or "
                "a straight side of that length can't reach between the two rims."
            )

        max_width = self.reference_diameter_mm * _MAX_SIN_PHI_MAX
        if self.design_width_mm >= max_width:
            raise ValueError(
                f"Design width ({self.design_width_mm:g}mm) is too close to the cup's own "
                f"mid-height diameter ({self.reference_diameter_mm:.1f}mm) -- keep it under "
                f"{max_width:.1f}mm, or the edges would need near-infinite stretching (a front "
                "view can never be wider than the diameter itself)."
            )

    @property
    def bottom_radius_mm(self) -> float:
        return self.bottom_circumference_mm / (2 * math.pi)

    @property
    def top_radius_mm(self) -> float:
        return self.top_circumference_mm / (2 * math.pi)

    @property
    def reference_diameter_mm(self) -> float:
        # Linear taper -> the mid-height diameter is exactly the average of
        # the two rim diameters, i.e. (d_bot+d_top)/2 == r_bot+r_top. This
        # is the diameter to enter into the rotary attachment's own
        # calibration, since it's the one the output's physical width below
        # is computed from.
        return self.bottom_radius_mm + self.top_radius_mm

    @property
    def height_mm(self) -> float:
        # The side length is the slant (along-the-surface) distance between
        # the rims; together with the difference in radii it forms a right
        # triangle whose other leg is the axial height actually needed for
        # the rotary's linear travel.
        delta_r = self.bottom_radius_mm - self.top_radius_mm
        return math.sqrt(self.side_length_mm ** 2 - delta_r ** 2)

    @property
    def phi_max_rad(self) -> float:
        return math.asin(self.design_width_mm / self.reference_diameter_mm)

    @property
    def wrap_angle_deg(self) -> float:
        return math.degrees(self.phi_max_rad) * 2.0

    @property
    def output_width_mm(self) -> float:
        return self.design_width_mm

    @property
    def output_height_mm(self) -> float:
        return self.height_mm


def output_size_px(geom: CupGeometry, px_per_mm: float) -> tuple[int, int]:
    w = max(1, round(geom.output_width_mm * px_per_mm))
    h = max(1, round(geom.output_height_mm * px_per_mm))
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


def warp_for_rotary(source: np.ndarray, geom: CupGeometry, out_w: int, out_h: int) -> np.ndarray:
    """source must already be fit (see fit_cover) to the out_w:out_h aspect
    ratio -- this only applies the front-projection horizontal correction
    (see module docstring) plus a direct proportional vertical resample;
    the vertical axis needs no warp since axial position on a rotary job
    maps straight through with no foreshortening."""
    phi_max = geom.phi_max_rad
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
                   dither: bool) -> tuple[np.ndarray, int, int]:
    """Returns (rgba_uint8_array, out_w, out_h)."""
    out_w, out_h = output_size_px(geom, px_per_mm)
    fitted = fit_cover(image_rgba, out_w, out_h)
    warped = warp_for_rotary(fitted, geom, out_w, out_h)

    if dither:
        gray = warped[..., :3].astype(np.float64) @ _GRAY_WEIGHTS
        dithered = floyd_steinberg_dither(gray)
        out = np.dstack([dithered, dithered, dithered, warped[..., 3]]).astype(np.uint8)
    else:
        out = warped

    return out, out_w, out_h
