"""Kerf-corrector calibration helpers: generates two physical test cuts --
a plain nominal-size square for finding a machine's basic kerf, and a
tab-into-hole "ladder" (one fixed hole plus several tabs at increasing
extra clearance) for dialing in how loose a press-fit tab should feel. See
the "Finding your laser's kerf" section of README.md for the manual
procedure this automates, and kerf_finder_tool.py for the hub Blueprint
wrapping this.

Finger-joint clearance isn't generated here: that correction has a length
axis that gets only half the shift its width axis does (only one of its
two ends is independently cut -- see joints.py's apply_manifest docstring,
on the not-yet-merged taper-corrector branch), and getting that right
depends on reusing the real correction engine rather than reimplementing
it by hand here from a description. tab_finger_clearance_mm stays a plain
manual field in the tool until that branch lands on main and this module
can import the real thing.

Both test cuts feed the same settings profile the Kerf Corrector's
Save/Load Settings feature reads: kerf_mm, tab_hole_clearance_mm,
tab_finger_clearance_mm, chamfer_mm.
"""

from __future__ import annotations

from dataclasses import dataclass

_MIN_TAB_COUNT = 2
_MAX_TAB_COUNT = 12
_FONT_SIZE_MM = 3.2
_LABEL_GAP_MM = 6.0  # vertical room reserved below each feature for its label
_TOP_MARGIN_MM = 10.0
_BOTTOM_MARGIN_MM = 6.0


@dataclass
class KerfSquare:
    nominal_mm: float
    svg: str


@dataclass
class TabHoleLadder:
    nominal_mm: float
    kerf_mm: float
    clearances_mm: list[float]
    svg: str


def build_kerf_square(nominal_mm: float) -> KerfSquare:
    """A single solid square, drawn at exactly nominal_mm -- no kerf
    correction applied, since the whole point is to measure how much a
    known, uncorrected drawn size shrinks once actually cut."""
    if nominal_mm <= 0:
        raise ValueError("Square size must be positive.")

    x = _TOP_MARGIN_MM
    y = _TOP_MARGIN_MM
    rects = [(x, y, nominal_mm, nominal_mm)]
    labels = [(x + nominal_mm / 2, y + nominal_mm + _LABEL_GAP_MM - 1.5, f"{nominal_mm:g}mm square")]
    total_w = nominal_mm + 2 * _TOP_MARGIN_MM
    total_h = nominal_mm + _TOP_MARGIN_MM + _LABEL_GAP_MM + _BOTTOM_MARGIN_MM

    svg = _render_svg(total_w, total_h, rects, labels)
    return KerfSquare(nominal_mm=nominal_mm, svg=svg)


def tab_hole_clearances_mm(count: int, step_mm: float) -> list[float]:
    """Clearance values to test, starting at 0 (kerf-only correction, the
    tightest fit the ladder offers) and increasing by step_mm -- unlike the
    kerf square's own straddle-the-nominal spacing, there's no reason to
    test a *negative* clearance here: that would mean an intentionally
    tighter-than-kerf-alone interference fit, which isn't what this ladder
    is for."""
    return [round(i * step_mm, 4) for i in range(count)]


def build_tab_hole_ladder(
    nominal_mm: float,
    kerf_mm: float,
    count: int = 5,
    step_mm: float = 0.05,
    tab_height_mm: float = 15.0,
) -> TabHoleLadder:
    """One shared hole at nominal_mm (corrected by kerf alone, exactly like
    the real corrector's plain `hole` kind: hole shrinks by the full kerf
    so cutting -- which enlarges it -- brings it back to nominal_mm) plus
    `count` free-standing tabs, each corrected by kerf *and* an increasing
    extra clearance (tab grows by the full kerf, same as the real
    corrector's standalone `tab_hole` kind, then shrinks again by its own
    clearance value) -- see joints.py's apply_manifest for the shared
    formula this mirrors: distance = (+-half_kerf) - half_extra per edge,
    which for a rectangle's two independent parallel walls totals kerf-extra
    across the whole dimension.
    """
    if nominal_mm <= 0:
        raise ValueError("Nominal size must be positive.")
    if not (_MIN_TAB_COUNT <= count <= _MAX_TAB_COUNT):
        raise ValueError(f"Number of test tabs must be between {_MIN_TAB_COUNT} and {_MAX_TAB_COUNT}.")
    if step_mm <= 0:
        raise ValueError("Clearance step must be positive.")
    if tab_height_mm <= 0:
        raise ValueError("Tab height must be positive.")

    drawn_hole = nominal_mm - kerf_mm
    if drawn_hole <= 0:
        raise ValueError(
            "That kerf is too large for this nominal size -- the corrected hole "
            "would be zero or negative. Use a bigger nominal size."
        )

    clearances = tab_hole_clearances_mm(count, step_mm)
    drawn_tabs = [nominal_mm + kerf_mm - c for c in clearances]
    if drawn_tabs[-1] <= 0:
        raise ValueError(
            "The largest clearance value would shrink a tab to zero or negative "
            "width -- lower the step or the tab count."
        )

    gap_mm = max(nominal_mm * 1.5, 8.0)

    x = gap_mm
    rects = [(x, _TOP_MARGIN_MM, drawn_hole, tab_height_mm)]
    labels = [(x + drawn_hole / 2, _TOP_MARGIN_MM + tab_height_mm + _LABEL_GAP_MM - 1.5, f"hole {nominal_mm:g}")]
    x += drawn_hole + gap_mm

    for c, w in zip(clearances, drawn_tabs):
        rects.append((x, _TOP_MARGIN_MM, w, tab_height_mm))
        labels.append((x + w / 2, _TOP_MARGIN_MM + tab_height_mm + _LABEL_GAP_MM - 1.5, f"+{c:g}"))
        x += w + gap_mm

    total_w = x
    total_h = _TOP_MARGIN_MM + tab_height_mm + _LABEL_GAP_MM + _BOTTOM_MARGIN_MM

    svg = _render_svg(total_w, total_h, rects, labels)
    return TabHoleLadder(nominal_mm=nominal_mm, kerf_mm=kerf_mm, clearances_mm=clearances, svg=svg)


def _render_svg(
    width_mm: float,
    height_mm: float,
    rects: list[tuple[float, float, float, float]],
    labels: list[tuple[float, float, str]],
) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.3f}mm" height="{height_mm:.3f}mm" '
        f'viewBox="0 0 {width_mm:.3f} {height_mm:.3f}">',
        # Outer boundary, drawn unfilled like every cut line here -- this is
        # the sheet's own silhouette, so every nested rect below (the hole,
        # each tab) ends up genuinely cut *out of* solid material rather
        # than floating in space with nothing to test-fit against.
        f'<rect x="0" y="0" width="{width_mm:.3f}" height="{height_mm:.3f}" fill="none" stroke="black" stroke-width="0.1"/>',
    ]
    for x, y, w, h in rects:
        parts.append(
            f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" fill="none" stroke="black" stroke-width="0.1"/>'
        )
    for x, y, text in labels:
        # Filled text, unlike the fill:none cut geometry above -- if this
        # file is ever run through the kerf corrector by mistake, the
        # unfilled-only selection convention (see svgio/kerf_tool) skips it
        # rather than treating a label as a cut line.
        parts.append(
            f'<text x="{x:.4f}" y="{y:.4f}" font-size="{_FONT_SIZE_MM}" font-family="monospace" '
            f'fill="black" text-anchor="middle">{text}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)
