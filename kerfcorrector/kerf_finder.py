"""Kerf calibration test-pattern generator: builds a small SVG comb of
slots at several widths straddling a nominal size, for cutting once on a
particular machine/material/settings and measuring the results with
calipers to find the actual kerf (see the "Finding your laser's kerf"
section of README.md, which this tool automates). Also includes one
free-standing tab at the nominal width, for the alternate press-fit method
described there. Pure geometry/SVG-string logic, no Flask -- see
kerf_finder_tool.py for the hub Blueprint wrapping this.
"""

from __future__ import annotations

from dataclasses import dataclass

_MIN_COUNT = 2
_MAX_COUNT = 15
_FONT_SIZE_MM = 3.2
_LABEL_GAP_MM = 6.0  # vertical room reserved below each slot for its label
_TOP_MARGIN_MM = 10.0
_BOTTOM_MARGIN_MM = 6.0


@dataclass
class TestPattern:
    nominal_mm: float
    count: int
    step_mm: float
    slot_height_mm: float
    widths_mm: list[float]
    svg: str


def slot_widths_mm(nominal_mm: float, count: int, step_mm: float) -> list[float]:
    """Widths of the `count` test slots, evenly spaced by step_mm and
    centered on nominal_mm -- an odd count always includes the nominal
    value exactly; an even count straddles it symmetrically instead."""
    offset = (count - 1) / 2
    return [round(nominal_mm + (i - offset) * step_mm, 4) for i in range(count)]


def build_test_pattern(
    nominal_mm: float,
    count: int = 5,
    step_mm: float = 0.05,
    slot_height_mm: float = 20.0,
) -> TestPattern:
    if nominal_mm <= 0:
        raise ValueError("Nominal width must be positive.")
    if not (_MIN_COUNT <= count <= _MAX_COUNT):
        raise ValueError(f"Number of slots must be between {_MIN_COUNT} and {_MAX_COUNT}.")
    if step_mm <= 0:
        raise ValueError("Step must be positive.")
    if slot_height_mm <= 0:
        raise ValueError("Slot height must be positive.")

    widths = slot_widths_mm(nominal_mm, count, step_mm)
    if widths[0] <= 0:
        raise ValueError("Smallest slot would be zero or negative width -- lower the step or slot count.")

    # Material left between/around slots -- generous relative to the slot
    # size itself so thin webs between adjacent slots don't crack out
    # during cutting, with a floor for very small nominal widths.
    gap_mm = max(nominal_mm * 1.5, 8.0)

    x = gap_mm
    slot_rects: list[tuple[float, float, float, float]] = []
    labels: list[tuple[float, float, str]] = []
    for w in widths:
        slot_rects.append((x, _TOP_MARGIN_MM, w, slot_height_mm))
        labels.append((x + w / 2, _TOP_MARGIN_MM + slot_height_mm + _LABEL_GAP_MM - 1.5, f"{w:g}"))
        x += w + gap_mm

    tab_w = round(nominal_mm, 4)
    tab_rects = [(x, _TOP_MARGIN_MM, tab_w, slot_height_mm)]
    labels.append((x + tab_w / 2, _TOP_MARGIN_MM + slot_height_mm + _LABEL_GAP_MM - 1.5, f"tab {tab_w:g}"))
    x += tab_w + gap_mm

    total_w = x
    total_h = _TOP_MARGIN_MM + slot_height_mm + _LABEL_GAP_MM + _BOTTOM_MARGIN_MM

    svg = _render_svg(total_w, total_h, slot_rects + tab_rects, labels)
    return TestPattern(
        nominal_mm=nominal_mm, count=count, step_mm=step_mm,
        slot_height_mm=slot_height_mm, widths_mm=widths, svg=svg,
    )


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
        # Outer boundary, drawn unfilled like every cut line here -- purely
        # a reference outline for the sheet, not itself a measured feature.
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
