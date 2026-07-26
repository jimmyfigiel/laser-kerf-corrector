"""Laser kerf corrector tool: upload an SVG, review/select joints in the
browser, download the corrected file. A Flask Blueprint so it can be
mounted alongside other tools on a shared hub app (see hub.py) -- safe to
host publicly, unlike the old local-only version, since it never touches
the server's filesystem: uploads live in memory, keyed by an opaque token,
and results come back as a download rather than a server-side file write.
"""

from __future__ import annotations

import io
import time
import uuid

from flask import Blueprint, Response, jsonify, request
from lxml import etree

from . import cli, joints, svgio

bp = Blueprint("kerf_tool", __name__, url_prefix="/kerf-corrector")

# token -> {"bytes": bytes, "filename": str, "ts": float}. In-memory and
# per-process: fine for a single-worker deployment (PythonAnywhere's free/
# hobby tiers run one), but entries won't be shared across multiple worker
# processes, and everything is lost on restart. Bounded so a long-running
# process doesn't grow without limit.
_UPLOADS: dict[str, dict] = {}
_MAX_UPLOADS = 40


def _store(data: bytes, filename: str) -> str:
    token = uuid.uuid4().hex
    _UPLOADS[token] = {"bytes": data, "filename": filename, "ts": time.time()}
    if len(_UPLOADS) > _MAX_UPLOADS:
        oldest = min(_UPLOADS, key=lambda k: _UPLOADS[k]["ts"])
        del _UPLOADS[oldest]
    return token


def _load(token: str) -> svgio.Document:
    entry = _UPLOADS.get(token)
    if entry is None:
        raise KeyError("That upload has expired or the server restarted -- please upload the file again.")
    return svgio.load(io.BytesIO(entry["bytes"]))


def _svg_inner_and_viewbox(data: bytes) -> tuple[str, str]:
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(io.BytesIO(data), parser)
    root = tree.getroot()
    view_box = root.get("viewBox") or "0 0 1000 1000"
    inner = b"".join(
        etree.tostring(child) for child in root
        if isinstance(child.tag, str)
    )
    return inner.decode("utf-8"), view_box


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Laser kerf corrector</title>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  #topbar { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: #262626; border-bottom: 1px solid #444; }
  #topbar h1 { font-size: 14px; margin: 0; font-weight: 600; }
  #topbar h1 a { color: #ddd; text-decoration: none; }
  #topbar .file { font-size: 12px; color: #9c9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  #topbar a.action { color: #6cf; font-size: 12px; cursor: pointer; text-decoration: none; }
  .screen { display: none; height: calc(100% - 41px); }
  .screen.active { display: block; }
  #screen-pick { padding: 20px; max-width: 560px; margin: 0 auto; overflow-y: auto; }
  #screen-pick p { font-size: 13px; color: #aaa; line-height: 1.5; }
  #drop-zone { border: 2px dashed #444; border-radius: 8px; padding: 40px 20px; text-align: center; cursor: pointer; margin-top: 16px; }
  #drop-zone:hover, #drop-zone.dragover { border-color: #6cf; background: #232733; }
  #drop-zone .hint { font-size: 12px; color: #888; margin-top: 8px; }
  #upload-status { margin-top: 14px; font-size: 13px; }
  #upload-status.err { color: #f88; }
  .btn { background: #3c6e96; color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .btn:hover { background: #4a84b3; }
  #add-mode.active { background: #b3701f; }
  #add-mode.active:hover { background: #cc7f23; }
  #sidebar button:disabled, .btn:disabled { background: #333; color: #777; cursor: default; }
  .click-marker { fill: #fff; stroke: #ff3355; stroke-width: 2; vector-effect: non-scaling-stroke; pointer-events: none; }
  .panel { padding: 16px; max-width: 480px; }
  .panel label { display: block; font-size: 12px; color: #aaa; margin: 10px 0 3px; }
  .panel input, .panel select { width: 100%; box-sizing: border-box; background: #2a2a2a; border: 1px solid #444; color: #ddd; padding: 7px; border-radius: 4px; font-size: 13px; }
  .panel .actions { margin-top: 16px; display: flex; gap: 8px; }
  .report { margin-top: 16px; padding: 12px; background: #262626; border-radius: 6px; font-size: 12px; white-space: pre-wrap; font-family: monospace; max-height: 300px; overflow-y: auto; }
  .report.ok { border-left: 3px solid #3c9650; }
  .report.err { border-left: 3px solid #c0392b; }
  .report a.download { display: inline-block; margin-top: 10px; color: #6cf; }
  #review-body { display: flex; height: calc(100% - 0px); }
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
  /* Class selectors only (not "polygon.xxx") -- a windowed EDGE feature
     renders as a <polyline>, not a <polygon> (see shapeTag()), so a
     tag-qualified selector would silently stop matching it. */
  #overlay-svg polygon, #overlay-svg polyline { cursor: pointer; stroke-width: 1.2; vector-effect: non-scaling-stroke; fill: rgba(0,0,0,0); }
  #overlay-svg .ignored { stroke: rgba(120,180,255,0.35); }
  #overlay-svg .hole { fill: rgba(255,150,30,0.45); stroke: #ff9622; }
  /* No fill on EDGE -- its shape traces the feature's own cut geometry,
     which for a notch/slot is exactly the void that's physically cut
     away. A solid fill there paints over the real cut lines underneath
     and makes an actually-open cutout look uncut/still-filled. Outline
     only, same treatment the old dashed-only BOUNDARY style used, so the
     real geometry underneath stays visible for review. Covers the
     leftover container (which can span a whole panel) just as well, for
     the same reason -- a big panel-wide fill would otherwise wash over
     everything. */
  #overlay-svg .edge { stroke: #3c96ff; }
  /* mortice/tenon/teeth/slot each get their own color so a clearance-tuned
     joint stands out from a plain hole/edge. teeth keeps the color this
     tool has used since tab_finger; mortice/slot (voids) get two new hues
     since they're new distinctions, not renames of anything. tenon was
     originally an orange similar enough to hole's own orange to be easy to
     mix up at a glance, so it's green instead -- distinct from every other
     kind's hue, including mortice's teal. Filled (like hole) rather than
     outline-only like edge -- these are always individual small tabs/
     notches, never the whole-panel leftover boundary, so a translucent
     fill makes them easy to spot at a glance without ever washing out the
     artwork underneath the way filling the container edge would. */
  #overlay-svg .tenon { fill: rgba(76,175,80,0.45); stroke: #4caf50; stroke-dasharray: 5 2; }
  #overlay-svg .teeth { fill: rgba(185,103,255,0.45); stroke: #b967ff; stroke-dasharray: 5 2; }
  #overlay-svg .mortice { fill: rgba(43,184,179,0.45); stroke: #2bb8b3; stroke-dasharray: 5 2; }
  #overlay-svg .slot { fill: rgba(255,111,174,0.45); stroke: #ff6fae; stroke-dasharray: 5 2; }
  #overlay-svg .ignored-merged { stroke: rgba(60,150,255,0.55); stroke-dasharray: 2 3; }
  #overlay-svg .hot { stroke: #ffd400; stroke-width: 2; }
  #sidebar { width: 360px; background: #262626; display: flex; flex-direction: column; border-left: 1px solid #444; overflow-y: auto; }
  #sidebar h2 { font-size: 13px; margin: 12px 14px 4px; }
  #sidebar .sub { font-size: 11px; color: #999; margin: 0 14px 10px; line-height: 1.4; }
  #counts { font-size: 12px; color: #bbb; padding: 8px 14px; border-bottom: 1px solid #444; }
  #list { border-bottom: 1px solid #444; max-height: 300px; overflow-y: auto; }
  .row { padding: 7px 14px; border-bottom: 1px solid #333; font-size: 12px; cursor: pointer; }
  .row:hover { background: #303030; }
  .row.selected { background: #35405a; }
  .row .dims { color: #aaa; }
  .row .cls-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; margin-left: 6px; }
  .cls-badge.hole { background: #ff9622; color: #201400; }
  .cls-badge.edge { background: #3c96ff; color: #001428; }
  .cls-badge.tenon { background: #4caf50; color: #0f2b10; }
  .cls-badge.teeth { background: #b967ff; color: #1c0029; }
  .cls-badge.mortice { background: #2bb8b3; color: #04211f; }
  .cls-badge.slot { background: #ff6fae; color: #2e0016; }
  .cls-badge.ignored { background: #555; color: #ccc; }
  .row-controls { margin-top: 6px; display: flex; gap: 4px; flex-wrap: wrap; }
  .row-controls button { font-size: 11px; padding: 3px 8px; border: 1px solid #555; background: #333; color: #ddd; border-radius: 3px; cursor: pointer; }
  .row-controls button.active { background: #567; border-color: #789; }
  #mode-toggle { display: flex; gap: 0; margin: 0 14px 6px; border: 1px solid #555; border-radius: 4px; overflow: hidden; }
  .mode-btn { flex: 1; font-size: 12px; padding: 6px 0; border: none; background: #333; color: #bbb; cursor: pointer; }
  .mode-btn:first-child { border-right: 1px solid #555; }
  .mode-btn.active { background: #3c6e96; color: #fff; }
  #apply-panel { padding: 14px; }
  #status { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.6); padding: 6px 10px; border-radius: 4px; font-size: 12px; pointer-events: none; }
  .help-icon { display: inline-flex; align-items: center; justify-content: center; width: 14px; height: 14px;
    border-radius: 50%; background: #3c3c3c; color: #9c9c9c; font-size: 10px; font-weight: 700;
    cursor: pointer; margin-left: 5px; vertical-align: middle; user-select: none; line-height: 1; }
  .help-icon:hover, .help-icon.active { background: #3c6e96; color: #fff; }
  #help-popover { display: none; position: fixed; max-width: 320px; background: #2f2f2f; border: 1px solid #555;
    border-radius: 6px; padding: 10px 12px; font-size: 12px; color: #ccc; line-height: 1.45; z-index: 200;
    box-shadow: 0 6px 20px rgba(0,0,0,0.6); }
  #help-popover.open { display: block; }
  #help-popover b { color: #fff; }
</style>
</head>
<body>
<div id="topbar">
  <h1><a href="__HUB_URL__">&larr; Tools</a> / Laser kerf corrector</h1>
  <div class="file" id="topbar-file"></div>
  <a class="action" id="change-file" style="display:none">upload a different file</a>
  <a class="action" href="/feedback/?tool=Laser%20Kerf%20Corrector" target="_blank">report a bug / suggest a feature</a>
</div>

<div id="screen-pick" class="screen active">
  <p>Corrects laser-cutting SVG plans for kerf: shrinks holes and grows or
  shrinks every other edge as needed so the finished part matches the
  drawing after cutting. Upload an SVG to get started -- nothing is stored
  beyond this session.</p>
  <div id="drop-zone">
    <div>Drop an .svg file here, or click to choose one</div>
    <div class="hint">Only unfilled (fill:none) shapes are treated as cut lines</div>
    <input type="file" id="file-input" accept=".svg" style="display:none">
  </div>
  <div id="upload-status"></div>
</div>

<div id="screen-review" class="screen">
  <div id="review-status" class="panel"></div>
  <div id="review-body" style="display:none">
    <div id="canvas-wrap">
      <div id="viewport">
        <svg id="bg-svg"><g id="bg-g"></g></svg>
        <svg id="overlay-svg"><g id="overlay-g"></g></svg>
      </div>
      <div id="status"></div>
    </div>
    <div id="sidebar">
      <h2>Candidates <span class="help-icon" id="candidates-help">?</span></h2>
      <div id="mode-toggle">
        <button class="mode-btn active" data-mode="basic" type="button">Basic</button>
        <button class="mode-btn" data-mode="advanced" type="button">Advanced</button>
      </div>
      <div class="sub" id="mode-caption" style="margin:0 14px 10px;">Basic: hole/edge only, kerf correction alone. Advanced: also detects mortice/tenon/teeth/slot for per-kind extra clearance.</div>
      <button id="reset-view" style="margin:0 14px 4px; width:calc(100% - 28px)">Reset view</button>
      <button id="add-mode" style="margin:0 14px 4px; width:calc(100% - 28px)">+ Add missed feature</button>
      <button id="undo-btn" style="margin:0 14px 10px; width:calc(100% - 28px)" disabled>Undo</button>
      <div id="add-mode-status" class="sub" style="display:none; margin:0 14px 10px;"></div>
      <div id="counts"></div>
      <div id="list"></div>
      <div id="apply-panel">
        <label>Kerf (total, mm)</label>
        <input type="number" step="0.01" id="r-kerf" value="0.15">
        <label>Mortice extra clearance (mm)</label>
        <input type="number" step="0.01" id="r-mortice-clearance" value="0">
        <label>Tenon extra clearance (mm)</label>
        <input type="number" step="0.01" id="r-tenon-clearance" value="0">
        <label>Teeth extra clearance (mm)</label>
        <input type="number" step="0.01" id="r-teeth-clearance" value="0">
        <label>Slot extra clearance (mm)</label>
        <input type="number" step="0.01" id="r-slot-clearance" value="0">
        <label>Tenon chamfer (mm, 0 = off)</label>
        <input type="number" step="0.01" id="r-chamfer" value="0">
        <div class="actions"><button class="btn" id="r-apply">Apply correction</button></div>
        <div id="r-apply-report"></div>
        <div class="actions" style="margin-top:14px">
          <button class="btn" id="r-save-settings" type="button">Save settings</button>
          <button class="btn" id="r-load-settings" type="button">Load settings</button>
          <input type="file" id="r-load-settings-file" accept="application/json" style="display:none">
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const API = '__API_PREFIX__';
let uploadToken = null, uploadFilename = null;

// ---------------- help popover ----------------
// Same shared-popover pattern as the cup-etch tool's help icons: one
// popover element positioned next to whichever "?" was clicked, rather
// than a per-field tooltip. Only one icon here (the whole classification
// explanation), so its content is set directly in the click handler
// instead of read from a per-icon data attribute.
const CANDIDATES_HELP_HTML = `Auto-detected features. Every one is corrected the
same way by default: it ends up at exactly its own drawn size after
cutting. Orange = <b>hole</b> (material removed &mdash; worth a careful
look, since a missed or misplaced hole is visibly wrong). Blue =
<b>edge</b> (everything else plain: a notch, or a boundary's own
walls). A joint that's too snug or too loose can instead be marked as
one of four dashed kinds, each with its own extra-clearance number
below (independent of kerf, since kerf alone can't fix a fit issue on
an attached feature's length axis &mdash; see the README): teal
<b>mortice</b> (the socket a tenon plugs into &mdash; clearance grows the
opening), green <b>tenon</b> (the tab that plugs in &mdash; clearance
shrinks it), purple <b>teeth</b> (a finger/comb joint's tabs &mdash;
clearance shrinks them, same as a tenon), and pink <b>slot</b> (a
sliding-fit channel, e.g. a dado a panel slides into &mdash; clearance
grows it, same direction as a mortice). Tenon also gets chamfered per
the chamfer setting below, for an easier lead-in (teeth don't). Click
any shape on the canvas to cycle through all seven kinds &mdash; the
cycle never restricts which kind fits which shape, since you know the
design's intent better than the geometry does. If a joint wasn't auto-detected
(it stayed part of a boundary), use "+ Add missed feature" and click
its two corners directly. Ctrl/Cmd+Z undoes the last change. Click
empty canvas space or press Escape to clear a selection. Scroll to
zoom, drag to pan.`;

const helpPopover = document.createElement('div');
helpPopover.id = 'help-popover';
document.body.appendChild(helpPopover);
let activeHelpIcon = null;

function closeHelpPopover() {
  helpPopover.classList.remove('open');
  if (activeHelpIcon) activeHelpIcon.classList.remove('active');
  activeHelpIcon = null;
}

document.getElementById('candidates-help').addEventListener('click', (e) => {
  e.stopPropagation();
  const icon = e.currentTarget;
  if (activeHelpIcon === icon) { closeHelpPopover(); return; }
  closeHelpPopover();
  helpPopover.innerHTML = CANDIDATES_HELP_HTML;
  helpPopover.classList.add('open');
  icon.classList.add('active');
  activeHelpIcon = icon;
  const iconRect = icon.getBoundingClientRect();
  const popRect = helpPopover.getBoundingClientRect();
  let left = iconRect.left;
  if (left + popRect.width > window.innerWidth - 10) left = window.innerWidth - popRect.width - 10;
  helpPopover.style.left = Math.max(10, left) + 'px';
  helpPopover.style.top = (iconRect.bottom + 6) + 'px';
});
document.addEventListener('click', closeHelpPopover);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHelpPopover(); });

function screen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ---------------- upload ----------------
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => { if (fileInput.files[0]) uploadFile(fileInput.files[0]); });
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
});

async function uploadFile(file) {
  const status = document.getElementById('upload-status');
  status.className = '';
  status.textContent = 'Uploading...';
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(API + '/api/upload', { method: 'POST', body: form });
  const data = await resp.json();
  if (!resp.ok) {
    status.className = 'err';
    status.textContent = 'Error: ' + (data.error || resp.statusText);
    return;
  }
  status.textContent = '';
  uploadToken = data.token;
  uploadFilename = data.filename;
  document.getElementById('topbar-file').textContent = uploadFilename;
  document.getElementById('change-file').style.display = 'inline';
  document.getElementById('review-body').style.display = 'none';
  screen('screen-review');
  analyzeFile();
}
document.getElementById('change-file').addEventListener('click', () => {
  fileInput.value = '';
  document.getElementById('upload-status').textContent = '';
  screen('screen-pick');
});

// ---------------- review mode ----------------
let DATA = [], state = [], polys = [], selectedIdx = null;

// Basic (default): plain hole/edge only -- for when the machine's kerf
// number alone already gets the fit right and the mortice/tenon/teeth/slot
// detection pass (and the extra-clearance settings it implies) is pure
// overhead. Advanced re-enables that detection. Switching modes re-runs
// analysis from scratch (see the click handler below), since it's a
// different classification of the same file, not a filter over one set of
// results -- any manual reclassifications made under the old mode are
// lost, same as re-uploading would do.
let detectJoints = false;
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const basic = btn.dataset.mode === 'basic';
    if (basic === !detectJoints) return;
    detectJoints = !basic;
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === btn.dataset.mode));
    document.getElementById('add-mode').style.display = detectJoints ? '' : 'none';
    if (basic && typeof addMode !== 'undefined' && addMode) document.getElementById('add-mode').click();
    if (uploadToken) analyzeFile();
  });
});
document.getElementById('add-mode').style.display = detectJoints ? '' : 'none';

// Every feature -- including the leftover-boundary container -- offers the
// full set of kinds to cycle through. apply_manifest doesn't derive its
// correction sign from a feature's is_container/is_closed_loop label at
// all; it recomputes the real structural shape from the manifest's own
// member_edges vs. the subpath's period at apply time (see
// _extra_clearance_sign in joints.py), so any kind is geometrically safe to
// assign to any feature. Restricting the cycle by structure was a UI-only
// guess at what's "sensible", and it made auto-detection misses (a joint
// the windowed search failed to carve out, left sitting in the container as
// unclassified boundary) impossible to fix by cycling -- the only escape
// was the separate "+ Add missed feature" tool. Full override removes that
// dead end.
function cycleOptions(d) {
  return ['ignored', 'hole', 'edge', 'mortice', 'tenon', 'teeth', 'slot'];
}

async function analyzeFile() {
  const status = document.getElementById('review-status');
  status.className = 'panel';
  status.textContent = 'Analyzing...';
  const resp = await fetch(API + '/api/analyze', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ token: uploadToken, detect_joints: detectJoints }) });
  const data = await resp.json();
  if (!resp.ok) {
    status.className = 'panel report err';
    status.textContent = 'Error: ' + (data.error || resp.statusText);
    return;
  }
  status.textContent = '';
  loadReview(data);
}

function loadReview(data) {
  document.getElementById('bg-svg').setAttribute('viewBox', data.view_box);
  document.getElementById('overlay-svg').setAttribute('viewBox', data.view_box);
  document.getElementById('bg-g').innerHTML = data.bg_content;
  document.getElementById('review-body').style.display = 'flex';

  DATA = data.payload;
  state = DATA.map(d => ({ classification: d.kind || 'ignored' }));
  undoStack = [];
  updateUndoButton();
  rebuildPolys();
  renderCounts();
  renderList();
}

// A windowed EDGE feature's `points` are an open chain along the real cut
// path (entry wall, cap, exit wall, ...) -- there is no real edge closing
// the last point back to the first, that's just wherever the search
// stopped scanning the boundary. A <polygon> draws that non-existent edge
// anyway as part of auto-closing the shape, which reads as a highlighted
// cut line where there isn't one. HOLE, standalone whole-subpath EDGE
// features (e.g. a free-standing tab), and the leftover CONTAINER, by
// contrast, always trace a subpath's own genuinely closed loop, so a
// <polygon> is correct for them -- <polyline> never auto-closes, so it's
// the right element for anything the server flags as not closed.
function shapeTag(d) {
  return d.is_closed_loop ? 'polygon' : 'polyline';
}

function rebuildPolys() {
  const overlayG = document.getElementById('overlay-g');
  overlayG.innerHTML = '';
  polys = DATA.map((d, i) => {
    const p = document.createElementNS('http://www.w3.org/2000/svg', shapeTag(d));
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
  DATA.forEach((_, i) => renderPoly(i));
}

// ---------------- undo ----------------
// Every state-mutating action (classification change, adding a missed
// feature) snapshots DATA+state first. Undo just restores the most recent
// snapshot and rebuilds everything derived from it -- simpler and less
// error-prone than trying to invert each action individually, especially
// since "add missed feature" mutates two entries at once (the new feature
// plus trimming the boundary it was carved out of).
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

function polyArea(points) {
  let area = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}

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

function renderPoly(i) {
  polys[i].setAttribute('class', displayClass(i) + (i === selectedIdx ? ' hot' : ''));
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
  const opts = cycleOptions(DATA[i]);
  const cur = opts.indexOf(state[i].classification);
  setClassification(i, opts[(cur + 1) % opts.length]);
  renderPoly(i);
  renderList();
  renderCounts();
}

// Clicking a shape on the canvas both cycles its classification and
// highlights/scrolls to its row in the sidebar, mirroring what clicking a
// sidebar row already does for the canvas (focusOn).
function selectFeature(i) {
  cycle(i);
  selectIdx(i);
  renderList();
  const row = list_row(i);
  if (row) row.scrollIntoView({ block: 'nearest' });
}

function selectIdx(i) {
  const prev = selectedIdx;
  selectedIdx = i;
  if (prev !== null && prev !== i) renderPoly(prev);
  renderPoly(i);
}

function list_row(i) {
  return document.querySelector(`#list .row[data-idx="${i}"]`);
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

function docPointFromEvent(svg, e) {
  const pt = svg.createSVGPoint();
  pt.x = e.clientX;
  pt.y = e.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

function handleAddModeClick(i, e) {
  const svg = document.getElementById('overlay-svg');
  const p = docPointFromEvent(svg, e);
  const elemSub = DATA[i].element_index + ':' + DATA[i].subpath_index;

  if (!pendingP1) {
    pendingP1 = [p.x, p.y];
    pendingElemSub = elemSub;
    marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    marker.setAttribute('cx', p.x);
    marker.setAttribute('cy', p.y);
    marker.setAttribute('r', 6);
    marker.setAttribute('class', 'click-marker');
    document.getElementById('overlay-g').appendChild(marker);
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
  const resp = await fetch(API + '/api/custom-feature', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ token: uploadToken, element_index, subpath_index, p1, p2 }),
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
  const poly = document.createElementNS('http://www.w3.org/2000/svg', shapeTag(feature));
  poly.setAttribute('points', feature.points.map(pt => pt.join(',')).join(' '));
  poly.dataset.idx = newIdx;
  poly.addEventListener('click', (e) => {
    e.stopPropagation();
    if (addMode) { handleAddModeClick(newIdx, e); return; }
    selectFeature(newIdx);
  });
  polys.push(poly);
  document.getElementById('overlay-g').appendChild(poly); // small feature -- fine on top

  DATA.forEach((_, j) => renderPoly(j));
  renderList();
  renderCounts();
  status.textContent = `Added a ${feature.short_mm}mm x ${feature.long_mm}mm ${feature.kind}. `
    + 'Click the two corners of another missed joint, or toggle "Add missed feature" off.';
}

function renderCounts() {
  const counts = { hole: 0, edge: 0, mortice: 0, tenon: 0, teeth: 0, slot: 0, ignored: 0 };
  state.forEach(s => counts[s.classification]++);
  let text = `${DATA.length} features analyzed — ${counts.hole} hole, ${counts.edge} edge`;
  if (counts.mortice) text += `, ${counts.mortice} mortice`;
  if (counts.tenon) text += `, ${counts.tenon} tenon`;
  if (counts.teeth) text += `, ${counts.teeth} teeth`;
  if (counts.slot) text += `, ${counts.slot} slot`;
  document.getElementById('counts').textContent = text + ' selected';
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
    cycleOptions(d).forEach(c => {
      const b = document.createElement('button');
      b.textContent = c;
      if (state[i].classification === c) b.classList.add('active');
      b.addEventListener('click', (e) => { e.stopPropagation(); pushUndo(); setClassification(i, c); renderPoly(i); renderList(); renderCounts(); });
      ctrl.appendChild(b);
    });
    row.appendChild(ctrl);
    row.addEventListener('click', () => { selectIdx(i); focusOn(i); renderList(); });
    list.appendChild(row);
  });
}

// pan / zoom
const wrap = document.getElementById('canvas-wrap');
const viewport = document.getElementById('viewport');
let tx = 0, ty = 0, zscale = 1;
function applyT() { viewport.style.transform = `translate(${tx}px,${ty}px) scale(${zscale})`; }
wrap.addEventListener('wheel', (e) => {
  e.preventDefault();
  const rect = wrap.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.15 : 1/1.15;
  const newScale = Math.min(Math.max(zscale * factor, 0.2), 200);
  tx = mx - (mx - tx) * (newScale / zscale);
  ty = my - (my - ty) * (newScale / zscale);
  zscale = newScale;
  applyT();
}, { passive: false });
let dragging = false, lastX, lastY, dragMoved = false;
wrap.addEventListener('mousedown', (e) => { dragging = true; dragMoved = false; lastX = e.clientX; lastY = e.clientY; wrap.classList.add('dragging'); });
window.addEventListener('mouseup', () => { dragging = false; wrap.classList.remove('dragging'); });
window.addEventListener('mousemove', (e) => {
  if (!dragging) return;
  dragMoved = true;
  tx += e.clientX - lastX; ty += e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY;
  applyT();
});
document.getElementById('reset-view').addEventListener('click', () => { tx = 0; ty = 0; zscale = 1; applyT(); });

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
  renderPoly(prev);
  renderList();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { if (addMode) cancelPendingAdd(); else deselect(); }
});

function bboxOf(points) {
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
function focusOn(i) {
  const [minx, miny, maxx, maxy] = bboxOf(DATA[i].points);
  const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
  const w = wrap.clientWidth, h = wrap.clientHeight;
  const featW = Math.max(maxx - minx, 1), featH = Math.max(maxy - miny, 1);
  let s = Math.min(w / (featW * 8), h / (featH * 8), 40);
  const vb = document.getElementById('bg-svg').viewBox.baseVal;
  const pxPerUnit = wrap.clientWidth / vb.width;
  tx = w/2 - cx * pxPerUnit * s;
  ty = h/2 - cy * pxPerUnit * s;
  zscale = s * pxPerUnit;
  applyT();
}

document.getElementById('r-apply').addEventListener('click', async () => {
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
  const body = {
    token: uploadToken,
    kerf: parseFloat(document.getElementById('r-kerf').value),
    mortice_clearance_mm: parseFloat(document.getElementById('r-mortice-clearance').value) || 0,
    tenon_clearance_mm: parseFloat(document.getElementById('r-tenon-clearance').value) || 0,
    teeth_clearance_mm: parseFloat(document.getElementById('r-teeth-clearance').value) || 0,
    slot_clearance_mm: parseFloat(document.getElementById('r-slot-clearance').value) || 0,
    chamfer_mm: parseFloat(document.getElementById('r-chamfer').value) || 0,
    manifest,
  };
  const resp = await fetch(API + '/api/apply', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  const data = await resp.json();
  const rep = document.getElementById('r-apply-report');
  if (!resp.ok) {
    rep.className = 'report err';
    rep.textContent = 'Error: ' + (data.error || resp.statusText);
    return;
  }
  rep.className = 'report ok';
  const warnings = data.warnings.length ? `\\nWarnings:\\n` + data.warnings.map(w => `  - ${w}`).join('\\n') : '';
  rep.innerHTML = `Corrected: ${data.corrected} / ${data.total_in_manifest}\\nElements touched: ${data.elements_touched}${warnings}`
    .replace(/\\n/g, '<br>');
  const link = document.createElement('a');
  link.className = 'download';
  link.href = API + '/api/download/' + data.download_token;
  link.textContent = 'Download ' + data.download_name;
  link.setAttribute('download', data.download_name);
  rep.appendChild(link);
});

// Settings profiles are just the 6 apply-panel numbers, saved as a
// downloadable JSON file rather than localStorage -- these are meant to
// travel with a material (e.g. "3mm birch ply.json"), reused across
// different jobs/machines/browsers, not tied to this one browser's storage.
function settingsProfile() {
  return {
    type: 'kerf-corrector-settings',
    kerf_mm: parseFloat(document.getElementById('r-kerf').value) || 0,
    mortice_clearance_mm: parseFloat(document.getElementById('r-mortice-clearance').value) || 0,
    tenon_clearance_mm: parseFloat(document.getElementById('r-tenon-clearance').value) || 0,
    teeth_clearance_mm: parseFloat(document.getElementById('r-teeth-clearance').value) || 0,
    slot_clearance_mm: parseFloat(document.getElementById('r-slot-clearance').value) || 0,
    chamfer_mm: parseFloat(document.getElementById('r-chamfer').value) || 0,
  };
}
document.getElementById('r-save-settings').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(settingsProfile(), null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'kerf-settings.json';
  link.click();
  URL.revokeObjectURL(url);
});
document.getElementById('r-load-settings').addEventListener('click', () => {
  document.getElementById('r-load-settings-file').click();
});
document.getElementById('r-load-settings-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  let profile;
  try {
    profile = JSON.parse(await file.text());
  } catch (err) {
    alert('Could not read that file as JSON settings: ' + err.message);
    return;
  }
  const fields = { kerf_mm: 'r-kerf', mortice_clearance_mm: 'r-mortice-clearance',
                    tenon_clearance_mm: 'r-tenon-clearance', teeth_clearance_mm: 'r-teeth-clearance',
                    slot_clearance_mm: 'r-slot-clearance', chamfer_mm: 'r-chamfer' };
  for (const [key, id] of Object.entries(fields)) {
    if (typeof profile[key] === 'number' && Number.isFinite(profile[key])) {
      document.getElementById(id).value = profile[key];
    }
  }
});
</script>
</body>
</html>
"""


@bp.route("/")
def index():
    return PAGE.replace("__HUB_URL__", "/").replace("__API_PREFIX__", bp.url_prefix)


@bp.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "No file received."}), 400
    if not f.filename.lower().endswith(".svg"):
        return jsonify({"error": "Please choose an .svg file."}), 400
    data = f.read()
    try:
        svgio.load(io.BytesIO(data))  # validate it actually parses before accepting
    except Exception as e:
        return jsonify({"error": f"Could not parse that as SVG: {e}"}), 400
    token = _store(data, f.filename)
    return jsonify({"token": token, "filename": f.filename})


@bp.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.get_json(force=True)
    try:
        doc = _load(body["token"])
        elements = cli.select_elements(doc, None, include_fill=False)
        if not elements:
            return jsonify({"error": "No cut-line elements found (nothing has fill:none/transparent)."}), 400
        infos = joints.analyze(doc, elements, tolerance_mm=0.3)
        detect_joints = bool(body.get("detect_joints", True))
        features = joints.find_features(infos, doc.scale_user_units_per_mm, detect_joints=detect_joints)
        payload = joints.to_payload(features)
        bg_content, view_box = _svg_inner_and_viewbox(_UPLOADS[body["token"]]["bytes"])
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"payload": payload, "view_box": view_box, "bg_content": bg_content})


@bp.route("/api/custom-feature", methods=["POST"])
def custom_feature():
    body = request.get_json(force=True)
    try:
        doc = _load(body["token"])
        elements = cli.select_elements(doc, None, include_fill=False)
        infos = joints.analyze(doc, elements, tolerance_mm=0.3)
        info = next(
            (i for i in infos if i.element_index == body["element_index"]
             and i.subpath_index == body["subpath_index"]),
            None,
        )
        if info is None:
            return jsonify({"error": "Element/subpath not found -- re-analyze and try again."}), 400
        feature = joints.custom_feature(info, doc.scale_user_units_per_mm, tuple(body["p1"]), tuple(body["p2"]))
        if feature is None:
            return jsonify({"error": "Couldn't fit a feature to those two points -- try clicking "
                                      "closer to the corners of the joint you want to add."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(joints.to_payload([feature])[0])


@bp.route("/api/apply", methods=["POST"])
def apply():
    body = request.get_json(force=True)
    try:
        token = body["token"]
        doc = _load(token)
        elements = cli.select_elements(doc, None, include_fill=False)
        stats = joints.apply_manifest(
            doc, elements, body["manifest"], float(body["kerf"]),
            mortice_clearance_mm=float(body.get("mortice_clearance_mm") or 0),
            tenon_clearance_mm=float(body.get("tenon_clearance_mm") or 0),
            teeth_clearance_mm=float(body.get("teeth_clearance_mm") or 0),
            slot_clearance_mm=float(body.get("slot_clearance_mm") or 0),
            chamfer_mm=float(body.get("chamfer_mm") or 0),
        )
        buf = io.BytesIO()
        svgio.save(doc, buf)
        result_bytes = buf.getvalue()
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    base = _UPLOADS[token]["filename"].rsplit(".", 1)[0]
    download_name = f"{base}.joints-corrected.svg"
    download_token = _store(result_bytes, download_name)
    return jsonify({
        "total_in_manifest": stats.total_in_manifest,
        "corrected": stats.corrected,
        "elements_touched": stats.elements_touched,
        "warnings": stats.warnings,
        "download_token": download_token,
        "download_name": download_name,
    })


@bp.route("/api/download/<token>")
def download(token):
    entry = _UPLOADS.get(token)
    if entry is None:
        return "That download has expired -- please apply the correction again.", 404
    resp = Response(entry["bytes"], mimetype="image/svg+xml")
    resp.headers["Content-Disposition"] = f'attachment; filename="{entry["filename"]}"'
    return resp
