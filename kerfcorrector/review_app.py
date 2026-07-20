"""Local Flask app for reviewing auto-detected HOLE/EDGE features in a
browser: verify/reclassify/remove suggestions, save a manifest for
apply_joints.py to consume."""

from __future__ import annotations

import json
import threading

from flask import Flask, request, jsonify
from lxml import etree

from . import cli, joints, svgio


PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Kerf joint review</title>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  #app { display: flex; height: 100%; }
  #canvas-wrap { flex: 1; position: relative; overflow: hidden; background: #2a2a2a; cursor: grab; }
  #canvas-wrap.dragging { cursor: grabbing; }
  #viewport { position: absolute; top: 0; left: 0; width: 100%; height: 100%; transform-origin: 0 0; }
  #viewport svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
  #bg-svg { background: white; }
  /* The source SVG's own stroke-width is in document units (often a fraction
     of a mm) -- at the default zoomed-out view of a whole sheet that's well
     under 1 screen pixel and effectively invisible. Force a visible minimum
     and make it scale-independent of the SVG's own internal viewBox mapping
     (it still scales with our own pan/zoom transform on #viewport, which is
     what we want -- readable both zoomed out and in). */
  #bg-svg * { vector-effect: non-scaling-stroke !important; }
  #bg-svg path, #bg-svg polygon, #bg-svg polyline, #bg-svg line,
  #bg-svg circle, #bg-svg ellipse, #bg-svg rect { stroke-width: 1.2px !important; }
  #overlay-svg polygon { cursor: pointer; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  #overlay-svg polygon.ignored { fill: rgba(0,0,0,0); stroke: rgba(120,180,255,0.35); }
  #overlay-svg polygon.hole { fill: rgba(255,150,30,0.45); stroke: #ff9622; }
  #overlay-svg polygon.edge { fill: rgba(60,150,255,0.45); stroke: #3c96ff; }
  #overlay-svg polygon.ignored-merged { fill: rgba(0,0,0,0); stroke: rgba(60,150,255,0.55); stroke-dasharray: 2 3; }
  #overlay-svg polygon.hot { stroke: #ffd400; stroke-width: 3; }
  .click-marker { fill: #fff; stroke: #ff3355; stroke-width: 2; vector-effect: non-scaling-stroke; pointer-events: none; }
  #add-mode.active { background: #b3701f; }
  #add-mode.active:hover { background: #cc7f23; }
  #sidebar { width: 340px; background: #262626; display: flex; flex-direction: column; border-left: 1px solid #444; }
  #sidebar h1 { font-size: 15px; margin: 12px 14px 4px; }
  #sidebar .sub { font-size: 12px; color: #999; margin: 0 14px 10px; line-height: 1.4; }
  #controls { padding: 0 14px 10px; border-bottom: 1px solid #444; }
  #controls button { width: 100%; padding: 8px; margin-bottom: 6px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  #save-btn { background: #3c9650; color: white; font-weight: 600; }
  #save-btn:hover { background: #46ad5d; }
  #reset-view { background: #444; color: #ddd; }
  #undo-btn { background: #444; color: #ddd; }
  #controls button:disabled { background: #333; color: #777; cursor: default; }
  #counts { font-size: 12px; color: #bbb; padding: 8px 14px; border-bottom: 1px solid #444; }
  #list { flex: 1; overflow-y: auto; }
  .row { padding: 8px 14px; border-bottom: 1px solid #333; font-size: 12px; cursor: pointer; }
  .row:hover { background: #303030; }
  .row.selected { background: #35405a; }
  .row .dims { color: #aaa; }
  .row .cls-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; margin-left: 6px; }
  .cls-badge.hole { background: #ff9622; color: #201400; }
  .cls-badge.edge { background: #3c96ff; color: #001428; }
  .cls-badge.ignored { background: #555; color: #ccc; }
  .row-controls { margin-top: 6px; display: flex; gap: 4px; flex-wrap: wrap; }
  .row-controls button { font-size: 11px; padding: 3px 8px; border: 1px solid #555; background: #333; color: #ddd; border-radius: 3px; cursor: pointer; }
  .row-controls button.active { background: #567; border-color: #789; }
  #help { font-size: 11px; color: #888; padding: 8px 14px; border-top: 1px solid #444; }
  #status { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.6); padding: 6px 10px; border-radius: 4px; font-size: 12px; pointer-events: none; }
</style>
</head>
<body>
<div id="app">
  <div id="canvas-wrap">
    <div id="viewport">
      <svg id="bg-svg" viewBox="__VIEWBOX__">__BG_CONTENT__</svg>
      <svg id="overlay-svg" viewBox="__VIEWBOX__"><g id="overlay-g"></g></svg>
    </div>
    <div id="status"></div>
  </div>
  <div id="sidebar">
    <h1>Joint review</h1>
    <div class="sub">Auto-detected features on the left canvas. Every one
    is corrected the same way: it ends up at exactly its own drawn size
    after cutting. Orange = <b>hole</b> (material removed — worth a
    careful look, since a missed or misplaced hole is visibly wrong).
    Blue = <b>edge</b> (everything else: a solid tab, a notch, or a
    boundary's own plain walls — all handled identically, so it doesn't
    matter which one it is). Click any shape on the canvas to cycle
    ignored/hole/edge. If a joint wasn't auto-detected (it stayed part
    of a boundary), use "+ Add missed feature" and click its two corners
    directly. Ctrl/Cmd+Z undoes the last change.</div>
    <div id="controls">
      <button id="save-btn">Save review &amp; finish</button>
      <button id="reset-view">Reset view</button>
      <button id="add-mode">+ Add missed feature</button>
      <button id="undo-btn" disabled>Undo</button>
    </div>
    <div id="add-mode-status" class="sub" style="display:none;"></div>
    <div id="counts"></div>
    <div id="list"></div>
    <div id="help">Scroll to zoom, drag to pan. Click a list row to jump to it.</div>
  </div>
</div>
<script id="data" type="application/json">__DATA_JSON__</script>
<script>
let DATA = JSON.parse(document.getElementById('data').textContent);
const CYCLE = ['ignored', 'hole', 'edge'];
let state = DATA.map(d => ({ classification: d.kind || 'ignored' }));
let selectedIdx = null;
let polys = [];

const overlayG = document.getElementById('overlay-g');

function polyArea(points) {
  let area = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}

function rebuildPolys() {
  overlayG.innerHTML = '';
  polys = DATA.map((d, i) => {
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    p.setAttribute('points', d.points.map(pt => pt.join(',')).join(' '));
    p.dataset.idx = i;
    p.addEventListener('click', (e) => {
      e.stopPropagation();
      if (addMode) { handleAddModeClick(i, e); return; }
      selectFeature(i);
    });
    return p;
  });
  // SVG has no z-index -- paint (and hit-test) order is DOM order. A big
  // CONTAINER polygon spans a whole panel and would otherwise sit on top of
  // (and steal clicks from) any smaller edge feature nested inside it, since
  // it gets appended after them. Append largest-area features first so
  // smaller ones always paint on top and stay individually clickable.
  const paintOrder = DATA.map((d, i) => i).sort((a, b) => polyArea(DATA[b].points) - polyArea(DATA[a].points));
  paintOrder.forEach(i => overlayG.appendChild(polys[i]));
  DATA.forEach((_, i) => render(i));
}

// `ignored` on a feature that got folded into a sibling container reads
// misleadingly as "gone" if it's shown with the near-invisible ignored
// style -- it's still corrected, just as an ordinary edge. Show it with
// the same blue language EDGE uses (a finer dash so it doesn't look
// identical to a real edge polygon) instead. Only a feature with nothing
// to fold into (no sibling container at all) is truly excluded and shown
// with the faint ignored style.
function displayClass(i) {
  const d = DATA[i];
  const cls = state[i].classification;
  if (cls === 'ignored' && !d.is_container && containerSiblingIdx(d.element_index, d.subpath_index) !== -1) {
    return 'ignored-merged';
  }
  return cls;
}

function render(i) {
  polys[i].setAttribute('class', displayClass(i) + (i === selectedIdx ? ' hot' : ''));
}
rebuildPolys();

// ---------------- undo ----------------
// Every state-mutating action (classification change, adding a missed
// feature) snapshots DATA+state first. Undo just restores the most recent
// snapshot and rebuilds everything derived from it.
let undoStack = [];
const UNDO_LIMIT = 50;

function pushUndo() {
  undoStack.push({
    DATA: JSON.parse(JSON.stringify(DATA)),
    state: JSON.parse(JSON.stringify(state)),
  });
  if (undoStack.length > UNDO_LIMIT) undoStack.shift();
  updateUndoButton();
}

function undo() {
  if (!undoStack.length) return;
  const snap = undoStack.pop();
  DATA = snap.DATA;
  state = snap.state;
  selectedIdx = null;
  rebuildPolys();
  renderList();
  renderCounts();
  updateUndoButton();
}

function updateUndoButton() {
  document.getElementById('undo-btn').disabled = undoStack.length === 0;
}

document.getElementById('undo-btn').addEventListener('click', undo);
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo(); }
});

// A windowed EDGE feature (or a manually-added one) was carved out of its
// subpath's CONTAINER entry -- the one leftover-boundary feature each
// subpath gets for "whatever wasn't separately detected" (see
// find_features / addCustomFeature) -- its edges were removed from the
// container's member_edges when it was created. Marking it `ignored`
// without undoing that would leave those specific edges completely
// uncorrected while their neighbors shift around them, producing a visible
// step/gap in the finished part right at that spot. Instead, fold the
// edges back into the sibling container entry (so they get ordinary
// container correction) when ignoring, and reclaim them back out of the
// container if the feature is un-ignored later.
function containerSiblingIdx(elementIndex, subpathIndex) {
  return DATA.findIndex(d => d.element_index === elementIndex && d.subpath_index === subpathIndex && d.is_container);
}

function setClassification(i, newCls) {
  const d = DATA[i];
  const oldCls = state[i].classification;
  if (oldCls === newCls) return;

  if (!d.is_container) {
    const bIdx = containerSiblingIdx(d.element_index, d.subpath_index);
    if (bIdx !== -1 && bIdx !== i) {
      const bEdges = new Set(DATA[bIdx].member_edges);
      if (newCls === 'ignored' && oldCls !== 'ignored') {
        d.member_edges.forEach(v => bEdges.add(v));
      } else if (oldCls === 'ignored' && newCls !== 'ignored') {
        d.member_edges.forEach(v => bEdges.delete(v));
      }
      DATA[bIdx].member_edges = [...bEdges];
    }
  }

  state[i].classification = newCls;
}

function cycle(i) {
  pushUndo();
  const cur = CYCLE.indexOf(state[i].classification);
  setClassification(i, CYCLE[(cur + 1) % CYCLE.length]);
  render(i);
  renderList();
  renderCounts();
}

function selectIdx(i) {
  const prev = selectedIdx;
  selectedIdx = i;
  if (prev !== null && prev !== i) render(prev);
  render(i);
}

// Clicking a shape on the canvas both cycles its classification and
// highlights/scrolls to its row in the sidebar, mirroring what clicking a
// sidebar row already does for the canvas (focusOn).
function selectFeature(i) {
  cycle(i);
  selectIdx(i);
  renderList();
  const row = document.querySelector(`#list .row[data-idx="${i}"]`);
  if (row) row.scrollIntoView({ block: 'nearest' });
}

// ---------------- add a missed feature ----------------
// The auto-detector can miss a real joint (most commonly a finger-joint
// tooth sitting right at a corner, where the edges on either side of it
// aren't parallel to each other -- the core assumption the windowed search
// relies on). This lets a user click the two corners of a joint directly on
// the canvas; the server snaps each click to the nearest actual vertex of
// that subpath and fits a feature to the run of edges between them.
let addMode = false, pendingP1 = null, pendingElemSub = null, marker = null;

document.getElementById('add-mode').addEventListener('click', () => {
  addMode = !addMode;
  document.getElementById('add-mode').classList.toggle('active', addMode);
  const status = document.getElementById('add-mode-status');
  status.style.display = addMode ? 'block' : 'none';
  status.textContent = addMode ? 'Click the two corners of the missed joint on the canvas.' : '';
  cancelPendingAdd();
});

function cancelPendingAdd() {
  pendingP1 = null;
  pendingElemSub = null;
  if (marker) { marker.remove(); marker = null; }
  if (addMode) document.getElementById('add-mode-status').textContent = 'Click the two corners of the missed joint on the canvas.';
}

function docPointFromEvent(svgEl, e) {
  const pt = svgEl.createSVGPoint();
  pt.x = e.clientX;
  pt.y = e.clientY;
  return pt.matrixTransform(svgEl.getScreenCTM().inverse());
}

function handleAddModeClick(i, e) {
  const svgEl = document.getElementById('overlay-svg');
  const p = docPointFromEvent(svgEl, e);
  const elemSub = DATA[i].element_index + ':' + DATA[i].subpath_index;

  if (!pendingP1) {
    pendingP1 = [p.x, p.y];
    pendingElemSub = elemSub;
    marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    marker.setAttribute('cx', p.x);
    marker.setAttribute('cy', p.y);
    marker.setAttribute('r', 6);
    marker.setAttribute('class', 'click-marker');
    overlayG.appendChild(marker);
    document.getElementById('add-mode-status').textContent = 'Now click the other corner.';
    return;
  }

  if (elemSub !== pendingElemSub) {
    document.getElementById('add-mode-status').textContent =
      'Both corners must be on the same piece -- try again.';
    cancelPendingAdd();
    return;
  }

  const p1 = pendingP1;
  cancelPendingAdd();
  document.getElementById('add-mode-status').textContent = 'Adding...';
  addCustomFeature(DATA[i].element_index, DATA[i].subpath_index, p1, [p.x, p.y]);
}

async function addCustomFeature(element_index, subpath_index, p1, p2) {
  const status = document.getElementById('add-mode-status');
  const resp = await fetch('/custom-feature', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ element_index, subpath_index, p1, p2 }),
  });
  const feature = await resp.json();
  if (!resp.ok) {
    status.textContent = 'Error: ' + (feature.error || resp.statusText);
    return;
  }

  pushUndo();
  // The new feature's edges were previously claimed by that same subpath's
  // CONTAINER entry (the catch-all for "whatever wasn't separately
  // detected") -- trim them out there so they aren't corrected twice.
  const claimed = new Set(feature.member_edges);
  const bIdx = containerSiblingIdx(element_index, subpath_index);
  if (bIdx !== -1) {
    const trimmed = DATA[bIdx].member_edges.filter(v => !claimed.has(v));
    DATA[bIdx].member_edges = trimmed;
    if (trimmed.length === 0) state[bIdx].classification = 'ignored';
  }

  const newIdx = DATA.length;
  DATA.push(feature);
  state.push({ classification: feature.kind });
  const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
  poly.setAttribute('points', feature.points.map(pt => pt.join(',')).join(' '));
  poly.dataset.idx = newIdx;
  poly.addEventListener('click', (e) => {
    e.stopPropagation();
    if (addMode) { handleAddModeClick(newIdx, e); return; }
    selectFeature(newIdx);
  });
  polys.push(poly);
  overlayG.appendChild(poly); // small feature -- fine on top

  DATA.forEach((_, j) => render(j));
  renderList();
  renderCounts();
  status.textContent = `Added a ${feature.short_mm}mm x ${feature.long_mm}mm ${feature.kind}. `
    + 'Click the two corners of another missed joint, or toggle "Add missed feature" off.';
}

function renderCounts() {
  const counts = { hole: 0, edge: 0, ignored: 0 };
  state.forEach(s => counts[s.classification]++);
  document.getElementById('counts').textContent =
    `${DATA.length} features analyzed — ${counts.hole} hole, ${counts.edge} edge selected`;
}

function renderList() {
  const list = document.getElementById('list');
  list.innerHTML = '';
  DATA.forEach((d, i) => {
    const row = document.createElement('div');
    row.className = 'row' + (i === selectedIdx ? ' selected' : '');
    row.dataset.idx = i;
    row.innerHTML = `#${i} <span class="dims">${d.short_mm}mm x ${d.long_mm}mm</span>
      <span class="cls-badge ${state[i].classification}">${state[i].classification}</span>`;
    const ctrl = document.createElement('div');
    ctrl.className = 'row-controls';
    CYCLE.forEach(c => {
      const b = document.createElement('button');
      b.textContent = c;
      if (state[i].classification === c) b.classList.add('active');
      b.addEventListener('click', (e) => { e.stopPropagation(); pushUndo(); setClassification(i, c); render(i); renderList(); renderCounts(); });
      ctrl.appendChild(b);
    });
    row.appendChild(ctrl);
    row.addEventListener('click', () => { selectIdx(i); focusOn(i); renderList(); });
    list.appendChild(row);
  });
}
renderCounts();
renderList();

// --- pan / zoom ---
const wrap = document.getElementById('canvas-wrap');
const viewport = document.getElementById('viewport');
let tx = 0, ty = 0, scale = 1;
function apply() { viewport.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`; }
wrap.addEventListener('wheel', (e) => {
  e.preventDefault();
  const rect = wrap.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.15 : 1/1.15;
  const newScale = Math.min(Math.max(scale * factor, 0.2), 200);
  tx = mx - (mx - tx) * (newScale / scale);
  ty = my - (my - ty) * (newScale / scale);
  scale = newScale;
  apply();
}, { passive: false });
let dragging = false, lastX, lastY, dragMoved = false;
wrap.addEventListener('mousedown', (e) => { dragging = true; dragMoved = false; lastX = e.clientX; lastY = e.clientY; wrap.classList.add('dragging'); });
window.addEventListener('mouseup', () => { dragging = false; wrap.classList.remove('dragging'); });
window.addEventListener('mousemove', (e) => {
  if (!dragging) return;
  dragMoved = true;
  tx += e.clientX - lastX; ty += e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY;
  apply();
});
document.getElementById('reset-view').addEventListener('click', () => { tx = 0; ty = 0; scale = 1; apply(); });
apply();

// A click on empty canvas (not a shape -- those stopPropagation) clears
// the persistent selection highlight, or cancels a pending "add missed
// feature" first click if one is in progress.
wrap.addEventListener('click', () => {
  if (dragMoved) return;
  if (addMode) { if (pendingP1) cancelPendingAdd(); return; }
  deselect();
});

function deselect() {
  if (selectedIdx === null) return;
  const prev = selectedIdx;
  selectedIdx = null;
  render(prev);
  renderList();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { if (addMode) cancelPendingAdd(); else deselect(); }
});

function bbox(points) {
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
function focusOn(i) {
  const [minx, miny, maxx, maxy] = bbox(DATA[i].points);
  const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
  const w = wrap.clientWidth, h = wrap.clientHeight;
  const featW = Math.max(maxx - minx, 1), featH = Math.max(maxy - miny, 1);
  scale = Math.min(w / (featW * 8), h / (featH * 8), 40);
  // bg-svg viewBox maps document units 1:1 to its own box before CSS scaling
  // to 100% of wrap; compute the CSS-pixels-per-document-unit factor.
  const vb = document.getElementById('bg-svg').viewBox.baseVal;
  const pxPerUnit = wrap.clientWidth / vb.width;
  tx = w/2 - cx * pxPerUnit * scale;
  ty = h/2 - cy * pxPerUnit * scale;
  scale = scale * pxPerUnit;
  apply();
}

document.getElementById('save-btn').addEventListener('click', async () => {
  const manifest = [];
  state.forEach((s, i) => {
    if (s.classification === 'ignored') return;
    manifest.push({
      element_index: DATA[i].element_index,
      subpath_index: DATA[i].subpath_index,
      kind: s.classification,
      member_edges: DATA[i].member_edges,
    });
  });
  const resp = await fetch('/save', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(manifest) });
  if (resp.ok) {
    document.getElementById('status').textContent = `Saved ${manifest.length} features. You can close this tab.`;
  } else {
    document.getElementById('status').textContent = 'Save failed -- check the terminal.';
  }
});
</script>
</body>
</html>
"""


def _svg_inner_and_viewbox(svg_path: str) -> tuple[str, str]:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(svg_path, parser)
    root = tree.getroot()
    view_box = root.get("viewBox") or "0 0 1000 1000"
    inner = b"".join(
        etree.tostring(child) for child in root
        if isinstance(child.tag, str)
    )
    return inner.decode("utf-8"), view_box


def build_app(svg_path: str, payload: list[dict], manifest_out_path: str) -> tuple[Flask, threading.Event]:
    """Returns (app, saved_event). The caller runs `app` with a real WSGI
    server (see review_joints.py) and should shut it down once `saved_event`
    is set -- Werkzeug no longer supports triggering its own shutdown from
    inside a request handler."""
    app = Flask(__name__)
    bg_content, view_box = _svg_inner_and_viewbox(svg_path)
    html = (
        PAGE_TEMPLATE
        .replace("__VIEWBOX__", view_box)
        .replace("__BG_CONTENT__", bg_content)
        .replace("__DATA_JSON__", json.dumps(payload))
    )

    saved_event = threading.Event()

    @app.route("/")
    def index():
        return html

    @app.route("/save", methods=["POST"])
    def save():
        manifest = request.get_json(force=True)
        with open(manifest_out_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        saved_event.set()
        return jsonify({"ok": True, "count": len(manifest)})

    @app.route("/custom-feature", methods=["POST"])
    def custom_feature():
        body = request.get_json(force=True)
        try:
            doc = svgio.load(svg_path)
            elements = cli.select_elements(doc, None, include_fill=False)
            infos = joints.analyze(doc, elements, tolerance_mm=0.3)
            info = next(
                (i for i in infos if i.element_index == body["element_index"]
                 and i.subpath_index == body["subpath_index"]),
                None,
            )
            if info is None:
                return jsonify({"error": "Element/subpath not found."}), 400
            feature = joints.custom_feature(info, doc.scale_user_units_per_mm, tuple(body["p1"]), tuple(body["p2"]))
            if feature is None:
                return jsonify({"error": "Couldn't fit a feature to those two points -- try clicking "
                                          "closer to the corners of the joint you want to add."}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(joints.to_payload([feature])[0])

    return app, saved_event
