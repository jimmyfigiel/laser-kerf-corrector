"""Kerf-corrector calibration helpers: generates three physical test cuts
-- a plain nominal-size square for finding a machine's basic kerf, a
tab-into-hole "ladder" (one fixed hole plus several tabs at increasing
extra clearance) for dialing in how loose a press-fit tab should feel, and
a tab-into-finger-joint ladder (one fixed mating slot plus several
free-standing tab/carrier pieces) for the same thing on a finger joint.
See the "Finding your laser's kerf" section of README.md for the manual
procedure this automates, and kerf_finder_tool.py for the hub Blueprint
wrapping this.

The finger-joint ladder reuses the real correction engine (joints.analyze
/ joints.apply_manifest) rather than reimplementing its math: an attached
tab's length axis shifts by only half of what its width axis does (only
one of its two ends is independently cut -- see apply_manifest's
docstring), and that asymmetry is easy to get subtly wrong from a
description alone. Each candidate clearance value gets its own isolated
single-shape document -- built from scratch with known vertex/winding
structure, analyzed, corrected, then read back out -- rather than
reimplementing the shift math directly in this module.

All three test cuts feed the same settings profile the Kerf Corrector's
Save/Load Settings feature reads: kerf_mm, tab_hole_clearance_mm,
tab_finger_clearance_mm, chamfer_mm.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from . import joints, svgio

_MIN_TAB_COUNT = 2
_MAX_TAB_COUNT = 12
_FONT_SIZE_MM = 3.2
_LABEL_GAP_MM = 6.0  # vertical room reserved below each feature for its label
_TOP_MARGIN_MM = 10.0
_BOTTOM_MARGIN_MM = 6.0
_STROKE_WIDTH_MM = 0.001 * 25.4  # 0.001in hairline, in the document's own mm units


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


@dataclass
class TabFingerLadder:
    nominal_mm: float
    kerf_mm: float
    clearances_mm: list[float]
    svg: str


def _panel_with_feature_d(
    panel_w_mm: float, panel_h_mm: float, feature_w_mm: float, feature_depth_mm: float, protrude: bool
) -> tuple[str, list[int]]:
    """Path 'd' for a solid rectangular panel spanning (0,0)-(panel_w,panel_h)
    with a single feature centered on its top edge (y=0): a protruding tab
    (protrude=True, tip above y=0) or an inward notch (protrude=False, tip
    below y=0, into the panel). Vertex order/winding is the same shape any
    real uploaded panel with a windowed excursion on its boundary has, so
    joints.py's winding-derived outward-normal math treats it identically
    -- there's nothing special-cased for being a generated test shape.

    Returns (d, member_edges): member_edges are the vertex indices of the
    feature's own 3 walls (entry wall, cap, exit wall) -- the same shape
    an apply_manifest manifest entry expects.
    """
    cx = panel_w_mm / 2.0
    half = feature_w_mm / 2.0
    y_tip = -feature_depth_mm if protrude else feature_depth_mm
    pts = [
        (0.0, panel_h_mm),
        (0.0, 0.0),
        (cx - half, 0.0),
        (cx - half, y_tip),
        (cx + half, y_tip),
        (cx + half, 0.0),
        (panel_w_mm, 0.0),
        (panel_w_mm, panel_h_mm),
    ]
    d = "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in pts) + " Z"
    return d, [3, 4, 5]


def _correct_single_feature_d(
    panel_w_mm: float,
    panel_h_mm: float,
    feature_w_mm: float,
    feature_depth_mm: float,
    protrude: bool,
    kind: str,
    kerf_mm: float,
    tab_finger_clearance_mm: float = 0.0,
) -> str:
    """Build one isolated panel+feature shape, run it through the real
    joints.apply_manifest, and return the corrected path 'd'. Isolated
    (single-element, single-subpath) on purpose: with nothing else in the
    document, this feature is always at nesting depth 0 -- treated as a
    standalone solid piece, exactly what a small carrier/tab or reference
    slot test piece actually is, not a hole cut into a bigger sheet."""
    d, member_edges = _panel_with_feature_d(panel_w_mm, panel_h_mm, feature_w_mm, feature_depth_mm, protrude)
    y_min = min(-feature_depth_mm, 0.0)
    height = panel_h_mm - y_min
    svg_str = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{panel_w_mm:.4f}mm" height="{height:.4f}mm" '
        f'viewBox="0 {y_min:.4f} {panel_w_mm:.4f} {height:.4f}">'
        f'<path d="{d}" fill="none" stroke="black"/></svg>'
    )
    doc = svgio.load(io.BytesIO(svg_str.encode("utf-8")))
    elements = list(svgio.iter_shape_elements(doc.root))
    manifest = [{"element_index": 0, "subpath_index": 0, "kind": kind, "member_edges": member_edges}]
    joints.apply_manifest(doc, elements, manifest, kerf_mm, tab_finger_clearance_mm=tab_finger_clearance_mm)
    return elements[0].get("d")


def build_tab_finger_ladder(
    nominal_mm: float,
    kerf_mm: float,
    count: int = 5,
    step_mm: float = 0.05,
    engagement_depth_mm: float = 8.0,
) -> TabFingerLadder:
    """One reference slot (a notch cut into a small panel, corrected by
    kerf alone -- same as a plain `edge` feature, no clearance term applies
    to the mating slot side) plus `count` free-standing tab/carrier pieces,
    each a tab protruding from its own small carrier, corrected by kerf and
    an increasing tab_finger_clearance_mm. Unlike build_tab_hole_ladder's
    closed-form rectangle math, every corrected shape here comes from the
    real apply_manifest -- see that function's docstring for why an
    attached tab's length and width axes need to go through it rather than
    be reimplemented from a description.
    """
    if nominal_mm <= 0:
        raise ValueError("Nominal size must be positive.")
    if not (_MIN_TAB_COUNT <= count <= _MAX_TAB_COUNT):
        raise ValueError(f"Number of test tabs must be between {_MIN_TAB_COUNT} and {_MAX_TAB_COUNT}.")
    if step_mm <= 0:
        raise ValueError("Clearance step must be positive.")
    if engagement_depth_mm <= 0:
        raise ValueError("Engagement depth must be positive.")

    clearances = tab_hole_clearances_mm(count, step_mm)
    # Fast, closed-form pre-check before paying for the real correction
    # engine: a standalone tab's width axis shifts by the full kerf then
    # shrinks by the full clearance (2 independent walls), the same total
    # as build_tab_hole_ladder's tabs -- see apply_manifest's docstring.
    if nominal_mm + kerf_mm - clearances[-1] <= 0:
        raise ValueError(
            "The largest clearance value would shrink a tab to zero or negative "
            "width -- lower the step or the tab count."
        )

    margin_mm = max(nominal_mm * 0.75, 6.0)
    socket_w = nominal_mm + 2 * margin_mm
    socket_h = engagement_depth_mm + 10.0
    tab_w = nominal_mm + 2 * margin_mm
    tab_h = 10.0

    gap_mm = max(nominal_mm * 1.5, 8.0)
    baseline_y = engagement_depth_mm + 6.0  # room above the canvas top for a tab's protrusion

    socket_d = _correct_single_feature_d(socket_w, socket_h, nominal_mm, engagement_depth_mm, False, "edge", kerf_mm)

    pieces_svg = [f'<g transform="translate({gap_mm:.4f},{baseline_y:.4f})"><path d="{socket_d}" '
                  f'fill="none" stroke="black" stroke-width="{_STROKE_WIDTH_MM:.5f}"/></g>']
    labels = [(gap_mm + socket_w / 2, baseline_y + socket_h + _LABEL_GAP_MM - 1.5, f"socket {nominal_mm:g}")]

    x = gap_mm + socket_w + gap_mm
    for c in clearances:
        tab_d = _correct_single_feature_d(tab_w, tab_h, nominal_mm, engagement_depth_mm, True, "tab_finger", kerf_mm,
                                           tab_finger_clearance_mm=c)
        pieces_svg.append(f'<g transform="translate({x:.4f},{baseline_y:.4f})"><path d="{tab_d}" '
                           f'fill="none" stroke="black" stroke-width="{_STROKE_WIDTH_MM:.5f}"/></g>')
        labels.append((x + tab_w / 2, baseline_y + tab_h + _LABEL_GAP_MM - 1.5, f"+{c:g}"))
        x += tab_w + gap_mm

    total_w = x
    total_h = baseline_y + max(socket_h, tab_h) + _LABEL_GAP_MM + _BOTTOM_MARGIN_MM

    label_parts = [
        f'<text x="{lx:.4f}" y="{ly:.4f}" font-size="{_FONT_SIZE_MM}" font-family="monospace" '
        f'fill="black" text-anchor="middle">{text}</text>'
        for lx, ly, text in labels
    ]
    svg = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:.3f}mm" height="{total_h:.3f}mm" '
        f'viewBox="0 0 {total_w:.3f} {total_h:.3f}">',
        f'<rect x="0" y="0" width="{total_w:.3f}" height="{total_h:.3f}" fill="none" stroke="black" stroke-width="{_STROKE_WIDTH_MM:.5f}"/>',
        *pieces_svg,
        *label_parts,
        "</svg>",
    ])
    return TabFingerLadder(nominal_mm=nominal_mm, kerf_mm=kerf_mm, clearances_mm=clearances, svg=svg)


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
        f'<rect x="0" y="0" width="{width_mm:.3f}" height="{height_mm:.3f}" fill="none" stroke="black" stroke-width="{_STROKE_WIDTH_MM:.5f}"/>',
    ]
    for x, y, w, h in rects:
        parts.append(
            f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" fill="none" stroke="black" stroke-width="{_STROKE_WIDTH_MM:.5f}"/>'
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
