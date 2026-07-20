"""Shared cut-line element selection, used by the review/apply joint workflow."""

from __future__ import annotations

from . import svgio


def select_elements(doc: svgio.Document, select_classes, include_fill: bool):
    chosen = []
    for elem in svgio.iter_shape_elements(doc.root):
        if select_classes:
            classes = set((elem.get("class") or "").split())
            if not classes & set(select_classes):
                continue
        else:
            fill = svgio.resolve_fill(elem, doc.class_styles)
            if not include_fill and not svgio.is_unfilled(fill):
                continue
        chosen.append(elem)
    return chosen
