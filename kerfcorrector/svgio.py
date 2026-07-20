"""SVG reading/writing helpers.

Uses lxml so the original document (comments, DOCTYPE, attribute order,
unrelated elements) survives untouched except for the 'd'/geometry of the
elements we actually kerf-correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree
from svgelements import Path, Matrix, Rect, Circle, Ellipse, Polygon as SEPolygon, Polyline as SEPolyline

SVG_NS = "http://www.w3.org/2000/svg"


def qn(tag: str) -> str:
    return f"{{{SVG_NS}}}{tag}"


SHAPE_TAGS = {"path", "polygon", "polyline", "rect", "circle", "ellipse", "line"}

UNIT_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72,
    "pc": 25.4 / 6,
    "px": 25.4 / 96,
    "": 25.4 / 96,  # bare number -> px per SVG spec
}

_LENGTH_RE = re.compile(r"^\s*([0-9.eE+-]+)\s*([a-zA-Z%]*)\s*$")


def parse_length_mm(value: str | None) -> float | None:
    if not value:
        return None
    m = _LENGTH_RE.match(value)
    if not m:
        return None
    num, unit = m.groups()
    unit = unit.lower()
    if unit == "%" or unit not in UNIT_TO_MM:
        return None
    try:
        return float(num) * UNIT_TO_MM[unit]
    except ValueError:
        return None


@dataclass
class Document:
    tree: etree._ElementTree
    root: etree._Element
    scale_user_units_per_mm: float
    scale_source: str
    class_styles: dict = field(default_factory=dict)


def load(path: str) -> Document:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    tree = etree.parse(path, parser)
    root = tree.getroot()
    scale, source = _detect_scale(root)
    class_styles = _parse_style_blocks(root)
    return Document(tree=tree, root=root, scale_user_units_per_mm=scale, scale_source=source, class_styles=class_styles)


def save(doc: Document, path: str) -> None:
    doc.tree.write(path, xml_declaration=True, encoding=doc.tree.docinfo.encoding or "UTF-8", standalone=doc.tree.docinfo.standalone)


def _detect_scale(root: etree._Element) -> tuple[float, str]:
    view_box = root.get("viewBox")
    vb_w = None
    if view_box:
        parts = re.split(r"[ ,]+", view_box.strip())
        if len(parts) == 4:
            try:
                vb_w = float(parts[2])
            except ValueError:
                vb_w = None

    width_mm = parse_length_mm(root.get("width"))
    if vb_w and width_mm:
        return vb_w / width_mm, f"viewBox width {vb_w} / document width {width_mm:.4f}mm"

    return 1.0, "no usable width+viewBox found; assuming 1 user unit = 1mm"


_CLASS_RULE_RE = re.compile(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}")


def _parse_style_blocks(root: etree._Element) -> dict:
    classes: dict[str, dict[str, str]] = {}
    for style_el in root.iter(qn("style")):
        text = style_el.text or ""
        for name, body in _CLASS_RULE_RE.findall(text):
            props = {}
            for decl in body.split(";"):
                if ":" not in decl:
                    continue
                k, v = decl.split(":", 1)
                props[k.strip().lower()] = v.strip()
            classes.setdefault(name, {}).update(props)
    return classes


def _inline_style_props(elem: etree._Element) -> dict:
    style = elem.get("style")
    if not style:
        return {}
    props = {}
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        k, v = decl.split(":", 1)
        props[k.strip().lower()] = v.strip()
    return props


def resolve_fill(elem: etree._Element, class_styles: dict) -> str:
    """Walk element -> ancestors resolving the effective 'fill' value.

    Checks (per element, in CSS-precedence order): inline style, presentation
    attribute, class rules. Falls back up the tree since fill is inherited,
    then to the SVG initial value 'black'.
    """
    node = elem
    while node is not None:
        inline = _inline_style_props(node)
        if "fill" in inline:
            return inline["fill"]
        attr = node.get("fill")
        if attr:
            return attr
        for cls in (node.get("class") or "").split():
            props = class_styles.get(cls, {})
            if "fill" in props:
                return props["fill"]
        if node.tag == qn("svg"):
            break
        node = node.getparent()
    return "black"


def is_unfilled(fill_value: str) -> bool:
    return fill_value.strip().lower() in ("none", "transparent")


def ancestor_transform(elem: etree._Element) -> Matrix:
    """Combined transform mapping points from elem's parent's local space
    to document root space (elem's own transform, if any, is NOT included --
    see element_transform).

    svgelements composes `A * B` such that `(A * B).point(p) == B(A(p))`
    (left operand applies first), so we accumulate starting with the
    immediate parent and multiply outward toward the root.
    """
    chain = []
    node = elem.getparent()
    while node is not None:
        t = node.get("transform")
        if t:
            chain.append(t)
        node = node.getparent()
    # chain is currently [immediate_parent, ..., root] -- that is already
    # the correct application order (immediate parent first).
    m = Matrix()
    for t in chain:
        m *= Matrix(t)
    return m


def element_to_path(elem: etree._Element) -> Path | None:
    """Convert a supported shape element to an svgelements Path in its own
    local coordinate space (element's own 'transform' attribute NOT applied
    here -- callers combine with ancestor_transform + the element's own
    transform as needed)."""
    tag = etree.QName(elem).localname

    def f(name, default=0.0):
        v = elem.get(name)
        return float(v) if v not in (None, "") else default

    if tag == "path":
        d = elem.get("d")
        if not d:
            return None
        return Path(d)
    if tag == "polygon":
        pts = elem.get("points", "")
        return Path(SEPolygon(points=_parse_points(pts)))
    if tag == "polyline":
        pts = elem.get("points", "")
        return Path(SEPolyline(points=_parse_points(pts)))
    if tag == "rect":
        rx = elem.get("rx")
        ry = elem.get("ry")
        return Path(Rect(
            x=f("x"), y=f("y"), width=f("width"), height=f("height"),
            rx=float(rx) if rx not in (None, "") else None,
            ry=float(ry) if ry not in (None, "") else None,
        ))
    if tag == "circle":
        return Path(Circle(cx=f("cx"), cy=f("cy"), r=f("r")))
    if tag == "ellipse":
        return Path(Ellipse(cx=f("cx"), cy=f("cy"), rx=f("rx"), ry=f("ry")))
    if tag == "line":
        d = f"M{f('x1')},{f('y1')} L{f('x2')},{f('y2')}"
        return Path(d)
    return None


def _parse_points(points_attr: str) -> list[float]:
    nums = [float(n) for n in re.split(r"[ ,]+", points_attr.strip()) if n]
    return nums


def replace_with_path(elem: etree._Element, d: str) -> None:
    """Turn elem into a <path> with the given d, preserving id/class/style
    and any other non-shape-specific attributes; drop shape-specific ones
    and any 'transform' (callers only call this after baking transforms
    into absolute coordinates)."""
    shape_specific = {"points", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2", "transform"}
    for attr in list(elem.attrib):
        if attr in shape_specific:
            del elem.attrib[attr]
    elem.tag = qn("path")
    elem.set("d", d)


def iter_shape_elements(root: etree._Element):
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue  # skip comments / processing instructions
        if etree.QName(elem).localname in SHAPE_TAGS:
            yield elem


def element_transform(elem: etree._Element) -> Matrix:
    """Full local-to-root transform: elem's own 'transform' attribute
    applied first, then the ancestor chain out to the root."""
    own = elem.get("transform")
    m = Matrix(own) if own else Matrix()
    m *= ancestor_transform(elem)
    return m
