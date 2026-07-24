"""Tapered-cup etching pattern tool: upload a photo or logo, describe the
cup's taper and how much of its front-facing circumference the design
should cover, and get back a flat raster pre-warped (see cup_etch.py for
the math) so that etching it with a rotary attachment reproduces an
undistorted, correctly-proportioned image when the finished cup is viewed
head-on. A Flask Blueprint mounted alongside the other tools (see hub.py);
follows kerf_tool.py's pattern of in-memory token-keyed upload/result
storage so it stays safe for public hosting -- nothing touches the
server's filesystem.
"""

from __future__ import annotations

import base64
import io
import time
import uuid

import numpy as np
from flask import Blueprint, Response, jsonify, request
from PIL import Image

from . import cup_etch

bp = Blueprint("cup_etch_tool", __name__, url_prefix="/cup-etcher")

_MM_PER_INCH = 25.4  # DPI is the unit laser/engraving software actually asks for;
# cup_etch's own math works in pixels/mm, so this is the one place that converts.

# token -> {"bytes": bytes, "filename": str, "ts": float}. Same in-memory,
# single-process, bounded store as kerf_tool.py -- see its comment for why
# that's the right tradeoff here (safe for a single-worker free-tier host,
# nothing persists across a restart, and nothing needs to).
_UPLOADS: dict[str, dict] = {}
_MAX_UPLOADS = 40


def _store(data: bytes, filename: str) -> str:
    token = uuid.uuid4().hex
    _UPLOADS[token] = {"bytes": data, "filename": filename, "ts": time.time()}
    if len(_UPLOADS) > _MAX_UPLOADS:
        oldest = min(_UPLOADS, key=lambda k: _UPLOADS[k]["ts"])
        del _UPLOADS[oldest]
    return token


def _load_image_array(token: str) -> np.ndarray:
    entry = _UPLOADS.get(token)
    if entry is None:
        raise KeyError("That upload has expired or the server restarted -- please upload the image again.")
    img = Image.open(io.BytesIO(entry["bytes"])).convert("RGBA")
    return np.array(img)


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Tapered cup etching pattern</title>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  #topbar { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: #262626; border-bottom: 1px solid #444; }
  #topbar h1 { font-size: 14px; margin: 0; font-weight: 600; }
  #topbar h1 a { color: #ddd; text-decoration: none; }
  #topbar .file { font-size: 12px; color: #9c9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  #topbar a.action { color: #6cf; font-size: 12px; cursor: pointer; text-decoration: none; }
  body { padding: 0; }
  #body { display: flex; height: calc(100% - 41px); overflow: hidden; }
  #screen-pick { padding: 20px; max-width: 560px; margin: 40px auto; overflow-y: auto; }
  #screen-pick p { font-size: 13px; color: #aaa; line-height: 1.5; }
  #drop-zone { border: 2px dashed #444; border-radius: 8px; padding: 40px 20px; text-align: center; cursor: pointer; margin-top: 16px; }
  #drop-zone:hover, #drop-zone.dragover { border-color: #6cf; background: #232733; }
  #drop-zone .hint { font-size: 12px; color: #888; margin-top: 8px; }
  #upload-status { margin-top: 14px; font-size: 13px; }
  #upload-status.err { color: #f88; }
  .btn { background: #3c6e96; color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .btn:hover { background: #4a84b3; }
  .btn:disabled { background: #333; color: #777; cursor: default; }
  #panel { width: 360px; flex: none; background: #262626; border-right: 1px solid #444; overflow-y: auto; padding: 16px; box-sizing: border-box; }
  #panel label { display: block; font-size: 12px; color: #aaa; margin: 12px 0 3px; }
  #panel label .unit { color: #777; }
  #panel input[type=number] { width: 100%; box-sizing: border-box; background: #2a2a2a; border: 1px solid #444; color: #ddd; padding: 7px; border-radius: 4px; font-size: 13px; }
  #panel input[type=range] { width: 100%; }
  #panel .row2 { display: flex; gap: 10px; }
  #panel .row2 > div { flex: 1; }
  #panel .check { display: flex; align-items: center; gap: 8px; margin: 14px 0 0; font-size: 12px; color: #ccc; }
  #panel .check input { width: auto; }
  #panel .sub { font-size: 11px; color: #888; margin: 4px 0 0; line-height: 1.4; }
  #panel .actions { margin-top: 18px; }
  #computed-geom { margin: 14px 0; padding: 10px 12px; background: #202020; border: 1px solid #383838; border-radius: 6px; line-height: 1.6; }
  #computed-geom b { color: #8fd0ff; }
  #computed-geom .err { color: #f88; }
  #preview-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #preview-canvas-wrap { flex: 1; display: flex; align-items: center; justify-content: center; overflow: auto;
    background-image: linear-gradient(45deg, #333 25%, transparent 25%), linear-gradient(-45deg, #333 25%, transparent 25%),
      linear-gradient(45deg, transparent 75%, #333 75%), linear-gradient(-45deg, transparent 75%, #333 75%);
    background-size: 20px 20px; background-position: 0 0, 0 10px, 10px -10px, -10px 0; background-color: #444; }
  #preview-canvas-wrap img { max-width: 90%; max-height: 90%; box-shadow: 0 4px 24px rgba(0,0,0,0.5); }
  #preview-placeholder { color: #888; font-size: 13px; text-align: center; padding: 40px; }
  #result-info { padding: 14px 16px; background: #262626; border-top: 1px solid #444; font-size: 12px; line-height: 1.6; }
  #result-info .err { color: #f88; }
  #result-info b { color: #8fd0ff; }
  #result-info a.download { display: inline-block; margin-top: 8px; color: #6cf; }
  #view3d-btn { margin-top: 10px; }
  #three-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 50;
    align-items: center; justify-content: center; }
  #three-overlay.open { display: flex; }
  #three-panel { background: #1e1e1e; border: 1px solid #444; border-radius: 8px; width: min(900px, 92vw);
    height: min(680px, 88vh); display: flex; flex-direction: column; overflow: hidden; }
  #three-panel-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px;
    border-bottom: 1px solid #444; font-size: 13px; }
  #three-panel-head .hint { color: #999; font-size: 11px; }
  #three-close { background: none; border: none; color: #ccc; font-size: 18px; cursor: pointer; line-height: 1; }
  #three-canvas-wrap { flex: 1; position: relative; }
  #three-canvas-wrap canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  #three-canvas-wrap canvas.dragging { cursor: grabbing; }
  #three-status { position: absolute; top: 10px; left: 10px; font-size: 12px; color: #ccc; background: rgba(0,0,0,0.5);
    padding: 5px 9px; border-radius: 4px; pointer-events: none; }
  #three-flat-preview { flex: none; border-top: 1px solid #444; padding: 8px 14px; display: flex; align-items: center; gap: 12px; }
  #three-flat-preview .cap { font-size: 11px; color: #999; flex: none; width: 130px; line-height: 1.4; }
  #three-flat-preview img { max-height: 90px; max-width: 260px;
    background-image: linear-gradient(45deg, #333 25%, transparent 25%), linear-gradient(-45deg, #333 25%, transparent 25%),
      linear-gradient(45deg, transparent 75%, #333 75%), linear-gradient(-45deg, transparent 75%, #333 75%);
    background-size: 14px 14px; background-position: 0 0, 0 7px, 7px -7px, -7px 0; background-color: #444;
    border: 1px solid #444; border-radius: 4px; }
  .help-icon { display: inline-flex; align-items: center; justify-content: center; width: 14px; height: 14px;
    border-radius: 50%; background: #3c3c3c; color: #9c9c9c; font-size: 10px; font-weight: 700;
    cursor: pointer; margin-left: 5px; vertical-align: middle; user-select: none; line-height: 1; }
  .help-icon:hover, .help-icon.active { background: #3c6e96; color: #fff; }
  #help-popover { display: none; position: fixed; max-width: 260px; background: #2f2f2f; border: 1px solid #555;
    border-radius: 6px; padding: 10px 12px; font-size: 12px; color: #ccc; line-height: 1.45; z-index: 200;
    box-shadow: 0 6px 20px rgba(0,0,0,0.6); }
  #help-popover.open { display: block; }
  .unit-toggle { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
  .unit-toggle-label { font-size: 11px; color: #888; margin-right: 2px; }
  .unit-btn { background: #2a2a2a; border: 1px solid #444; color: #aaa; padding: 4px 12px; border-radius: 4px;
    font-size: 12px; cursor: pointer; }
  .unit-btn.active { background: #3c6e96; border-color: #3c6e96; color: #fff; }
  .unit-btn:hover:not(.active) { background: #333; }
</style>
</head>
<body>
<div id="topbar">
  <h1><a href="__HUB_URL__">&larr; Tools</a> / Tapered cup etching pattern</h1>
  <div class="file" id="topbar-file"></div>
  <a class="action" id="change-file" style="display:none">upload a different image</a>
  <a class="action" href="/feedback/?tool=Tapered%20Cup%20Etching%20Pattern" target="_blank">report a bug / suggest a feature</a>
</div>

<div id="screen-pick" class="screen">
  <p>Turns a photo or logo into an etching pattern for the <b>front-facing
  panel</b> of a tapered cup or glass, warped so that once it's etched
  using a rotary attachment (which spins the piece in place while the
  laser only moves along its length), the finished etching looks like the
  original, undistorted image when the piece is viewed head-on -- not
  pinched at the edges the way a plain wrap would look on a curved
  surface. Upload a PNG or JPG to get started; nothing is stored beyond
  this session.</p>
  <div id="drop-zone">
    <div>Drop an image here, or click to choose one</div>
    <div class="hint">PNG (with transparency) or JPG</div>
    <input type="file" id="file-input" accept="image/*" style="display:none">
  </div>
  <div id="upload-status"></div>
</div>

<div id="body" style="display:none">
  <div id="panel">
    <div class="unit-toggle">
      <span class="unit-toggle-label">Units</span>
      <button type="button" class="unit-btn active" data-unit="mm">mm</button>
      <button type="button" class="unit-btn" data-unit="in">in</button>
    </div>

    <label>Bottom circumference <span class="unit length-unit">(mm)</span><span class="help-icon" data-help="Wrap a tape measure around the rim at the bottom and top of the design area -- doesn't have to be the whole cup, just the band being etched.">?</span></label>
    <input type="number" id="p-bottom-circ" value="207" step="1" min="1">
    <label>Top circumference <span class="unit length-unit">(mm)</span><span class="help-icon" data-help="Wrap a tape measure around the rim at the bottom and top of the design area -- doesn't have to be the whole cup, just the band being etched.">?</span></label>
    <input type="number" id="p-top-circ" value="285" step="1" min="1">

    <label>Side length <span class="unit length-unit">(mm)</span><span class="help-icon" data-help="Lay the tape flat along the tapered side, from the bottom rim straight up to the top rim -- not the vertical height, which isn't directly measurable without already knowing the taper.">?</span></label>
    <input type="number" id="p-side" value="148" step="1" min="1">

    <label>Distance from top <span class="unit length-unit">(mm)</span><span class="help-icon" data-help="Same kind of measurement as side length -- lay the tape flat along the side, from the top rim down to where the design should start. Matters because the diameter at the design's own position (not just the cup's overall taper) is what the projection below is corrected against.">?</span></label>
    <input type="number" id="p-offset" value="0" step="1" min="0">

    <label>Design width <span class="unit length-unit">(mm)</span><span class="help-icon" data-help="How wide the finished etching should look, viewed head-on. The uploaded image is scaled to this width, keeping its own proportions (never cropped or stretched) -- its height is whatever that scaling works out to, not entered separately.">?</span></label>
    <input type="number" id="p-width" value="60" step="1" min="1">

    <div id="computed-geom" class="sub"></div>

    <label>Resolution <span class="unit">(DPI)</span><span class="help-icon" data-help="150 DPI is a reasonable default. Higher looks sharper but dithering (below) gets slower on big images. Always measured in DPI regardless of the units toggle above -- DPI is already inch-based by definition.">?</span></label>
    <input type="number" id="p-dpi" value="150" step="10" min="10" max="1200">

    <div class="check">
      <input type="checkbox" id="p-dither" checked>
      <label for="p-dither" style="margin:0">Dither for photo engraving</label>
      <span class="help-icon" data-help="Error-diffusion dither to pure black/white, so a continuous-tone photo still shows shading once etched (a laser can only mark or not mark a spot). Leave off for a logo or line art that's already high-contrast.">?</span>
    </div>

    <div class="actions">
      <button class="btn" id="generate">Generate pattern</button>
    </div>
  </div>

  <div id="preview-area">
    <div id="preview-canvas-wrap">
      <div id="preview-placeholder">Set the cup's dimensions and click "Generate pattern".</div>
    </div>
    <div id="result-info"></div>
  </div>
</div>

<div id="three-overlay">
  <div id="three-panel">
    <div id="three-panel-head">
      <div>3D preview <span class="hint">-- drag to rotate, scroll to zoom</span></div>
      <label style="font-size:12px; color:#ccc; display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="checkbox" id="three-overlay-toggle" checked>
        Show original image overlay (semi-transparent)
      </label>
      <button id="three-close">&times;</button>
    </div>
    <div id="three-canvas-wrap">
      <div id="three-status">Loading 3D viewer...</div>
    </div>
    <div id="three-flat-preview" style="display:none">
      <div class="cap">Flat pattern (as etched) -- for comparison</div>
      <img id="three-flat-preview-img" alt="flat warped pattern">
    </div>
  </div>
</div>

<script>
const API = '__API_PREFIX__';
let uploadToken = null, uploadFilename = null;
let uploadedImgW = null, uploadedImgH = null, uploadedFile = null;

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

// ---------------- help popovers ----------------
// One shared popover element (rather than one per icon) positioned next to
// whichever "?" was clicked -- simpler than managing per-field tooltip
// elements, and works the same for every field without repeating markup.
const helpPopover = document.createElement('div');
helpPopover.id = 'help-popover';
document.body.appendChild(helpPopover);
let activeHelpIcon = null;

function closeHelpPopover() {
  helpPopover.classList.remove('open');
  if (activeHelpIcon) activeHelpIcon.classList.remove('active');
  activeHelpIcon = null;
}

document.querySelectorAll('.help-icon').forEach(icon => {
  icon.addEventListener('click', (e) => {
    e.stopPropagation();
    if (activeHelpIcon === icon) { closeHelpPopover(); return; }
    closeHelpPopover();
    helpPopover.textContent = icon.dataset.help;
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
});
document.addEventListener('click', closeHelpPopover);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHelpPopover(); });

// ---------------- mm / inch unit toggle ----------------
// The backend API and all internal math always work in mm (see
// cup_etch.py) -- this only affects what's displayed and typed. Every
// length field is converted (not just relabeled) when the unit changes,
// so the physical size represented stays the same either way.
let currentUnit = 'mm';
const MM_PER_INCH = 25.4;
const LENGTH_FIELD_IDS = ['p-bottom-circ', 'p-top-circ', 'p-side', 'p-offset', 'p-width'];

function toMm(value) { return currentUnit === 'in' ? value * MM_PER_INCH : value; }
function fromMm(mm) { return currentUnit === 'in' ? mm / MM_PER_INCH : mm; }
function fmtLen(mm) { return fromMm(mm).toFixed(currentUnit === 'in' ? 3 : 1); }
function roundTo(v, decimals) { const f = Math.pow(10, decimals); return Math.round(v * f) / f; }

document.querySelectorAll('.unit-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const newUnit = btn.dataset.unit;
    if (newUnit === currentUnit) return;
    LENGTH_FIELD_IDS.forEach(id => {
      const el = document.getElementById(id);
      const val = parseFloat(el.value);
      if (Number.isFinite(val)) {
        const mm = currentUnit === 'in' ? val * MM_PER_INCH : val;
        const converted = newUnit === 'in' ? mm / MM_PER_INCH : mm;
        el.value = roundTo(converted, newUnit === 'in' ? 3 : 1);
      }
      el.step = newUnit === 'in' ? '0.05' : '1';
    });
    document.getElementById('p-offset').min = '0';  // offset can always be zero, regardless of unit
    ['p-bottom-circ', 'p-top-circ', 'p-side', 'p-width'].forEach(id =>
      document.getElementById(id).min = newUnit === 'in' ? '0.05' : '1');
    currentUnit = newUnit;
    document.querySelectorAll('.unit-btn').forEach(b => b.classList.toggle('active', b.dataset.unit === currentUnit));
    document.querySelectorAll('.length-unit').forEach(el => { el.textContent = `(${currentUnit})`; });
    computeGeometryPreview();
  });
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
  uploadedFile = file;
  document.getElementById('topbar-file').textContent = uploadFilename;
  document.getElementById('change-file').style.display = 'inline';
  document.getElementById('screen-pick').style.display = 'none';
  document.getElementById('body').style.display = 'flex';

  // Read the image's own natural dimensions client-side (no server round
  // trip needed) so the live geometry preview below can mirror the real
  // Python math -- which needs the image's aspect ratio -- as you type.
  const objectUrl = URL.createObjectURL(file);
  const probe = new Image();
  probe.onload = () => {
    uploadedImgW = probe.naturalWidth;
    uploadedImgH = probe.naturalHeight;
    URL.revokeObjectURL(objectUrl);
    computeGeometryPreview();
  };
  probe.src = objectUrl;
}
document.getElementById('change-file').addEventListener('click', () => {
  fileInput.value = '';
  document.getElementById('upload-status').textContent = '';
  document.getElementById('body').style.display = 'none';
  document.getElementById('screen-pick').style.display = 'block';
});

// Mirrors CupGeometry/design_geometry_for_image's own formulas (see
// cup_etch.py) purely so the user gets instant feedback while typing,
// without a round trip -- the actual pattern generation below still goes
// through the real Python geometry code, which is the authority (including
// its own validation) on whether these numbers work.
function computeGeometryPreview() {
  // Every input is read in whatever unit is currently selected and
  // immediately converted to mm -- all the geometry math below (and the
  // real Python code it mirrors) works in mm throughout; only the display
  // at the end converts back to the selected unit.
  const bottomCirc = toMm(parseFloat(document.getElementById('p-bottom-circ').value));
  const topCirc = toMm(parseFloat(document.getElementById('p-top-circ').value));
  const side = toMm(parseFloat(document.getElementById('p-side').value));
  const offset = toMm(parseFloat(document.getElementById('p-offset').value));
  const width = toMm(parseFloat(document.getElementById('p-width').value));
  const out = document.getElementById('computed-geom');

  if (![bottomCirc, topCirc, side, width].every(v => Number.isFinite(v) && v > 0) ||
      !(Number.isFinite(offset) && offset >= 0)) {
    out.innerHTML = '';
    return;
  }
  if (offset >= side) {
    out.innerHTML = `<span class="err">Distance from top must be less than the side length.</span>`;
    return;
  }
  const bottomR = bottomCirc / (2 * Math.PI);
  const topR = topCirc / (2 * Math.PI);
  const deltaR = bottomR - topR;
  const discriminant = side * side - deltaR * deltaR;
  if (discriminant <= 0) {
    out.innerHTML = `<span class="err">Side length is too short for this much taper -- ` +
      `it must be more than ${fmtLen(Math.abs(deltaR))} ${currentUnit}.</span>`;
    return;
  }
  const availableHeight = Math.sqrt(discriminant);
  const axialOffset = offset * (availableHeight / side);

  if (!uploadedImgW || !uploadedImgH) {
    out.innerHTML = `Available height along the side: <b>${fmtLen(availableHeight)} ${currentUnit}</b><br>` +
      `(design height, local diameter, and front coverage need the uploaded image's own ` +
      `proportions -- shown once it's loaded)`;
    return;
  }

  const designHeight = width * uploadedImgH / uploadedImgW;
  if (axialOffset + designHeight > availableHeight) {
    const remaining = availableHeight - axialOffset;
    out.innerHTML = `<span class="err">At this width, the image would be ` +
      `${fmtLen(designHeight)} ${currentUnit} tall -- more than the ${fmtLen(remaining)} ${currentUnit} remaining ` +
      `below the offset on this ${fmtLen(availableHeight)} ${currentUnit}-tall side.</span>`;
    return;
  }
  const topD = 2 * topR, bottomD = 2 * bottomR;
  const diameterAt = (axialFromTop) => topD + (bottomD - topD) * (axialFromTop / availableHeight);
  const centerOffset = axialOffset + designHeight / 2;
  const localDiameter = diameterAt(centerOffset);

  // Diameter is linear along the taper, so its extremes across the
  // design's own height span are just at the design's own top and bottom
  // -- this mirrors cup_etch.py's design_geometry_for_image exactly (see
  // its comments for why the narrowest of the two governs the canvas's
  // own angular range, and why sin_needed simplifies to width/narrowest
  // without an asin/sin round trip).
  const narrowestDiameter = Math.min(diameterAt(axialOffset), diameterAt(axialOffset + designHeight));
  const sinNeeded = width / narrowestDiameter;
  const maxSin = Math.sin(87.5 * Math.PI / 180);
  if (sinNeeded >= maxSin) {
    out.innerHTML = `<span class="err">This design's narrowest point (diameter ` +
      `${fmtLen(narrowestDiameter)} ${currentUnit}) can't show the full ${fmtLen(width)} ${currentUnit} design ` +
      `width without its edges needing near-infinite stretching there -- try a narrower design width, a ` +
      `shorter design, or a different position.</span>`;
    return;
  }
  const phiMaxCanvas = Math.asin(sinNeeded);
  const wrapAngle = 2 * phiMaxCanvas * 180 / Math.PI;
  const arcLength = phiMaxCanvas * localDiameter;  // always > width -- see cup_etch.py's arc_length_mm
  out.innerHTML = `Design height: <b>${fmtLen(designHeight)} ${currentUnit}</b> ` +
    `(available: ${fmtLen(availableHeight)} ${currentUnit})<br>` +
    `Diameter at design's position (rotary calibration): <b>${fmtLen(localDiameter)} ${currentUnit}</b><br>` +
    `Front coverage: <b>${wrapAngle.toFixed(0)}&deg;</b><br>` +
    `Pattern's true physical width (enter this in your rotary job): <b>${fmtLen(arcLength)} ${currentUnit}</b> ` +
    `(looks ${fmtLen(width)} ${currentUnit} wide viewed head-on, but the etched pattern itself is wider)`;
}
['p-bottom-circ', 'p-top-circ', 'p-side', 'p-offset', 'p-width'].forEach(id =>
  document.getElementById(id).addEventListener('input', computeGeometryPreview));
computeGeometryPreview();

document.getElementById('generate').addEventListener('click', async () => {
  const info = document.getElementById('result-info');
  const btn = document.getElementById('generate');
  btn.disabled = true;
  info.innerHTML = 'Generating...';
  const body = {
    token: uploadToken,
    bottom_circumference_mm: toMm(parseFloat(document.getElementById('p-bottom-circ').value)),
    top_circumference_mm: toMm(parseFloat(document.getElementById('p-top-circ').value)),
    side_length_mm: toMm(parseFloat(document.getElementById('p-side').value)),
    top_offset_mm: toMm(parseFloat(document.getElementById('p-offset').value)),
    design_width_mm: toMm(parseFloat(document.getElementById('p-width').value)),
    dpi: parseFloat(document.getElementById('p-dpi').value),
    dither: document.getElementById('p-dither').checked,
  };
  let resp, data;
  try {
    resp = await fetch(API + '/api/generate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    data = await resp.json();
  } catch (e) {
    btn.disabled = false;
    info.innerHTML = '<span class="err">Error: request failed.</span>';
    return;
  }
  btn.disabled = false;
  if (!resp.ok) {
    info.innerHTML = '<span class="err">Error: ' + (data.error || resp.statusText) + '</span>';
    return;
  }
  const wrap = document.getElementById('preview-canvas-wrap');
  wrap.innerHTML = '';
  const img = document.createElement('img');
  img.src = data.preview_data_url;
  wrap.appendChild(img);

  info.innerHTML =
    `Output: <b>${data.output_w}&times;${data.output_h}px</b> -- ` +
    `<b>${fmtLen(data.output_width_mm)} ${currentUnit} &times; ${fmtLen(data.output_height_mm)} ${currentUnit}</b>, ` +
    `${data.wrap_angle_deg.toFixed(0)}&deg; front coverage<br>` +
    `This is the pattern's own true physical size (wider than it looks from ` +
    `the front, since peeling any design off a curved surface always yields ` +
    `more material than its straight-line width) -- looks ` +
    `<b>${fmtLen(data.apparent_width_mm)} ${currentUnit}</b> wide viewed head-on, but ` +
    `<b>enter ${fmtLen(data.output_width_mm)} ${currentUnit} as the image width in your rotary job</b> ` +
    `-- a rotary converts image width to a rotation angle via that true physical size, not the apparent one.<br>` +
    `Set your rotary attachment's object/roller diameter to <b>${fmtLen(data.local_diameter_mm)} ${currentUnit}</b> ` +
    `to match this pattern (the diameter at this design's own vertical position).`;
  const link = document.createElement('a');
  link.className = 'download';
  link.href = API + '/api/download/' + data.download_token;
  link.textContent = 'Download ' + data.download_name;
  link.setAttribute('download', data.download_name);
  info.appendChild(link);

  lastGenerateData = data;
  const view3dBtn = document.createElement('button');
  view3dBtn.className = 'btn';
  view3dBtn.id = 'view3d-btn';
  view3dBtn.textContent = 'View in 3D';
  view3dBtn.addEventListener('click', open3DPreview);
  info.appendChild(document.createElement('br'));
  info.appendChild(view3dBtn);
});

// ---------------- 3D preview ----------------
// Renders the cup as a tapered frustum (real mm dimensions) and composites
// the exact same warped pattern already generated above onto the correct
// sub-region of its surface -- the same wrap angle and vertical offset the
// 2D pattern used -- so this is a direct visualization of the same math,
// not a separate approximation. Three.js is loaded lazily (only once you
// ask for the 3D view) straight from a CDN, since this is a large library
// most users of this tool will never touch the button for.
let lastGenerateData = null;
let threeState = null;

document.getElementById('three-close').addEventListener('click', close3DPreview);
document.getElementById('three-overlay').addEventListener('click', (e) => {
  if (e.target.id === 'three-overlay') close3DPreview();
});

function close3DPreview() {
  document.getElementById('three-overlay').classList.remove('open');
  if (threeState) {
    cancelAnimationFrame(threeState.animId);
    window.removeEventListener('resize', threeState.onResize);
    threeState.renderer.dispose();
    threeState = null;
  }
  document.getElementById('three-canvas-wrap').innerHTML = '<div id="three-status"></div>';
  document.getElementById('three-flat-preview').style.display = 'none';
}

async function open3DPreview() {
  if (!lastGenerateData) return;
  const data = lastGenerateData;
  document.getElementById('three-overlay').classList.add('open');
  document.getElementById('three-flat-preview-img').src = data.preview_data_url;
  document.getElementById('three-flat-preview').style.display = 'flex';
  const wrap = document.getElementById('three-canvas-wrap');
  wrap.innerHTML = '<div id="three-status">Loading 3D viewer...</div>';

  let THREE;
  try {
    THREE = await import('https://unpkg.com/three@0.160.0/build/three.module.js');
  } catch (e) {
    wrap.innerHTML = '<div id="three-status">Could not load the 3D viewer ' +
      '(needs internet access to unpkg.com).</div>';
    return;
  }

  const texCanvas = await buildCupTexture(data);
  if (!document.getElementById('three-overlay').classList.contains('open')) return; // closed while loading

  wrap.innerHTML = '';
  const width = wrap.clientWidth, height = wrap.clientHeight;

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  wrap.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a2a2a);

  const bottomR = data.bottom_diameter_mm / 2;
  const topR = data.top_diameter_mm / 2;
  const fullHeight = data.available_height_mm;
  const geometry = buildFrustumGeometry(THREE, bottomR, topR, fullHeight, 64, 24);

  const texture = new THREE.CanvasTexture(texCanvas);
  // Three.js flips canvas textures on the V axis by default (WebGL's
  // texture-space convention vs. a canvas's top-row-first pixel data) --
  // our own UV mapping (buildFrustumGeometry) already puts v=0 at the top
  // rim to directly match the canvas's own row 0, so undo that default flip.
  texture.flipY = false;
  const material = new THREE.MeshStandardMaterial({ map: texture, roughness: 0.65, metalness: 0.0, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);

  // A flat, semi-transparent plane holding the *original, unwarped* source
  // image, hovering just in front of the design's own position -- a direct
  // visual check that the projection is correct: viewed from straight on
  // (the default camera angle), this flat original should line up with
  // the curved, warped pattern showing through/around it. If the two don't
  // match, the correction is wrong; if they do, the curvature is doing
  // exactly what it's supposed to.
  let overlayPlane = null;
  if (uploadedFile) {
    const halfH = fullHeight / 2;
    const centerOffsetFromTop = data.axial_top_offset_mm + data.output_height_mm / 2;
    const planeY = halfH - centerOffsetFromTop;
    const localRadius = data.local_diameter_mm / 2;
    const planeZ = localRadius + Math.max(5, localRadius * 0.15);

    const overlayTexture = await loadOriginalImageTexture(THREE, uploadedFile);
    if (!document.getElementById('three-overlay').classList.contains('open')) return; // closed while loading
    const overlayGeom = new THREE.PlaneGeometry(data.apparent_width_mm, data.output_height_mm);
    const overlayMat = new THREE.MeshBasicMaterial({
      map: overlayTexture, transparent: true, opacity: 0.5, side: THREE.DoubleSide, depthWrite: false,
    });
    overlayPlane = new THREE.Mesh(overlayGeom, overlayMat);
    overlayPlane.position.set(0, planeY, planeZ);
    overlayPlane.visible = document.getElementById('three-overlay-toggle').checked;
    scene.add(overlayPlane);
  }
  document.getElementById('three-overlay-toggle').onchange = (e) => {
    if (overlayPlane) overlayPlane.visible = e.target.checked;
  };

  scene.add(new THREE.AmbientLight(0xffffff, 0.8));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.6);
  dirLight.position.set(150, 200, 250);
  scene.add(dirLight);

  const maxDim = Math.max(bottomR, topR, fullHeight / 2);
  const camera = new THREE.PerspectiveCamera(40, width / height, maxDim * 0.05, maxDim * 50);
  let dist = maxDim * 4.2, azimuth = 0, polar = Math.PI / 2;

  function updateCamera() {
    camera.position.set(
      dist * Math.sin(polar) * Math.sin(azimuth),
      dist * Math.cos(polar),
      dist * Math.sin(polar) * Math.cos(azimuth),
    );
    camera.lookAt(0, 0, 0);
  }
  updateCamera();

  const canvas = renderer.domElement;
  let dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener('mousedown', (e) => {
    dragging = true; lastX = e.clientX; lastY = e.clientY; canvas.classList.add('dragging');
  });
  window.addEventListener('mouseup', () => { dragging = false; canvas.classList.remove('dragging'); });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    azimuth -= (e.clientX - lastX) * 0.008;
    polar -= (e.clientY - lastY) * 0.008;
    polar = Math.max(0.15, Math.min(Math.PI - 0.15, polar));
    lastX = e.clientX; lastY = e.clientY;
    updateCamera();
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    dist *= (e.deltaY > 0 ? 1.1 : 0.9);
    dist = Math.max(maxDim * 1.5, Math.min(maxDim * 14, dist));
    updateCamera();
  }, { passive: false });

  const onResize = () => {
    const w = wrap.clientWidth, h = wrap.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  window.addEventListener('resize', onResize);

  threeState = { renderer, animId: null, onResize };
  function animate() {
    threeState.animId = requestAnimationFrame(animate);
    renderer.render(scene, camera);
  }
  animate();
}

// A hand-rolled grid mesh rather than THREE.CylinderGeometry so the UV
// mapping is explicit and known (u: 0..1 around the full circumference,
// v: 0 at the top rim to 1 at the bottom rim) instead of relying on
// library-internal conventions -- buildCupTexture below is written against
// this exact mapping. u=0.5 (texture's horizontal center) is placed at
// world +Z, which is also where the default camera (azimuth=0) looks from,
// so the pattern's own front-center is what you see first.
function buildFrustumGeometry(THREE, bottomR, topR, height, radialSegments, heightSegments) {
  const positions = [], uvs = [], indices = [];
  const halfH = height / 2;

  for (let j = 0; j <= heightSegments; j++) {
    const v = j / heightSegments;
    const y = halfH - v * height;
    const r = topR + (bottomR - topR) * v;
    for (let i = 0; i <= radialSegments; i++) {
      const u = i / radialSegments;
      const theta = (u - 0.5) * Math.PI * 2;
      positions.push(r * Math.sin(theta), y, r * Math.cos(theta));
      uvs.push(u, v);
    }
  }

  const rowSize = radialSegments + 1;
  for (let j = 0; j < heightSegments; j++) {
    for (let i = 0; i < radialSegments; i++) {
      const a = j * rowSize + i, b = a + 1, c = a + rowSize, d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

// Composites the already-generated (flat, warped) pattern onto a plain
// "bare glass" background at the correct fractional position -- the same
// wrap angle (horizontal) and axial offset/height (vertical) used to
// generate it -- so what's pasted here is pixel-for-pixel the same
// projection as the etching pattern itself, just placed in cylindrical UV
// space instead of a flat rectangle.
function buildCupTexture(data) {
  return new Promise((resolve) => {
    const refDiameter = (data.bottom_diameter_mm + data.top_diameter_mm) / 2;
    const texW = 2048;
    const texH = Math.max(256, Math.round(texW * data.available_height_mm / (Math.PI * refDiameter)));
    const canvas = document.createElement('canvas');
    canvas.width = texW;
    canvas.height = texH;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#cfd8de';
    ctx.fillRect(0, 0, texW, texH);

    const wrapFrac = data.wrap_angle_deg / 360;
    const patX = (0.5 - wrapFrac / 2) * texW;
    const patW = wrapFrac * texW;
    const vTop = data.axial_top_offset_mm / data.available_height_mm;
    const vBottom = (data.axial_top_offset_mm + data.output_height_mm) / data.available_height_mm;
    const patY = vTop * texH;
    const patH = (vBottom - vTop) * texH;

    const img = new Image();
    img.onload = () => {
      ctx.drawImage(img, patX, patY, patW, patH);
      resolve(canvas);
    };
    img.src = data.preview_data_url;
  });
}

// Loads the original, never-warped source file as a plain Three.js texture
// (ordinary image orientation -- no flipY override, unlike buildCupTexture's
// custom UV convention -- since this just goes on a normal PlaneGeometry).
function loadOriginalImageTexture(THREE, file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    new THREE.TextureLoader().load(url, (tex) => {
      URL.revokeObjectURL(url);
      resolve(tex);
    });
  });
}
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
    data = f.read()
    try:
        Image.open(io.BytesIO(data)).verify()  # validate it actually decodes before accepting
    except Exception as e:
        return jsonify({"error": f"Could not read that as an image: {e}"}), 400
    token = _store(data, f.filename)
    return jsonify({"token": token, "filename": f.filename})


@bp.route("/api/generate", methods=["POST"])
def generate():
    body = request.get_json(force=True)
    try:
        geom = cup_etch.CupGeometry(
            bottom_circumference_mm=float(body["bottom_circumference_mm"]),
            top_circumference_mm=float(body["top_circumference_mm"]),
            side_length_mm=float(body["side_length_mm"]),
            design_width_mm=float(body["design_width_mm"]),
            top_offset_mm=float(body.get("top_offset_mm", 0.0)),
        )
        dpi = float(body["dpi"])
        if not (3 <= dpi <= 1270):
            raise ValueError("Resolution must be between 3 and 1270 DPI.")
        px_per_mm = dpi / _MM_PER_INCH
        source = _load_image_array(body["token"])
        out, out_w, out_h, design = cup_etch.build_pattern(
            source, geom, px_per_mm, bool(body.get("dither")))
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    png_bytes = _encode_png(out)
    base = _UPLOADS[body["token"]]["filename"].rsplit(".", 1)[0]
    download_name = f"{base}.etch-pattern.png"
    download_token = _store(png_bytes, download_name)
    preview_data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")

    return jsonify({
        "output_w": out_w,
        "output_h": out_h,
        # The output PNG's true physical size -- what the pattern actually
        # measures once peeled/etched onto the curved surface. This is wider
        # than apparent_width_mm (arc length always exceeds chord length for
        # any nonzero wrap angle) and is the number to enter into the rotary
        # attachment's own calibration, since that's the width a rotary
        # converts into a rotation angle via arc length.
        "output_width_mm": design.arc_length_mm,
        "output_height_mm": design.height_mm,
        # How wide the design *looks* viewed head-on -- the original input,
        # kept separately since it's a different number than the pattern's
        # own physical width (see output_width_mm above).
        "apparent_width_mm": geom.design_width_mm,
        "local_diameter_mm": design.local_diameter_mm,
        "wrap_angle_deg": design.wrap_angle_deg,
        "preview_data_url": preview_data_url,
        "download_token": download_token,
        "download_name": download_name,
        # For the 3D preview -- everything needed to build the cup mesh and
        # place this same pattern on it, independent of the flat 2D preview.
        "bottom_diameter_mm": 2 * geom.bottom_radius_mm,
        "top_diameter_mm": 2 * geom.top_radius_mm,
        "available_height_mm": geom.available_height_mm,
        "axial_top_offset_mm": geom.axial_top_offset_mm,
    })


def _encode_png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


@bp.route("/api/download/<token>")
def download(token):
    entry = _UPLOADS.get(token)
    if entry is None:
        return "That download has expired -- please generate the pattern again.", 404
    resp = Response(entry["bytes"], mimetype="image/png")
    resp.headers["Content-Disposition"] = f'attachment; filename="{entry["filename"]}"'
    return resp
