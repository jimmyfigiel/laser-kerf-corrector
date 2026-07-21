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
    <label>Bottom circumference <span class="unit">(mm)</span></label>
    <input type="number" id="p-bottom-circ" value="207" step="1" min="1">
    <label>Top circumference <span class="unit">(mm)</span></label>
    <input type="number" id="p-top-circ" value="285" step="1" min="1">
    <div class="sub">Wrap a tape measure around the rim at the bottom and
    top of the design area -- doesn't have to be the whole cup, just the
    band being etched.</div>

    <label>Side length <span class="unit">(mm)</span></label>
    <input type="number" id="p-side" value="158" step="1" min="1">
    <div class="sub">Lay the tape flat along the tapered side, from the
    bottom rim straight up to the top rim -- not the vertical height, which
    isn't directly measurable without already knowing the taper.</div>

    <label>Design width <span class="unit">(mm)</span></label>
    <input type="number" id="p-width" value="60" step="1" min="1">
    <div class="sub">How wide the finished etching should look, viewed
    head-on. The uploaded image is scaled to this width, keeping its own
    proportions (never cropped or stretched) -- its height is whatever that
    scaling works out to, not entered separately.</div>

    <div id="computed-geom" class="sub"></div>

    <label>Resolution <span class="unit">(DPI)</span></label>
    <input type="number" id="p-dpi" value="150" step="10" min="10" max="1200">
    <div class="sub">150 DPI is a reasonable default. Higher looks sharper
    but dithering (below) gets slower on big images.</div>

    <div class="check">
      <input type="checkbox" id="p-dither" checked>
      <label for="p-dither" style="margin:0">Dither for photo engraving</label>
    </div>
    <div class="sub">Error-diffusion dither to pure black/white, so a
    continuous-tone photo still shows shading once etched (a laser can
    only mark or not mark a spot). Leave off for a logo or line art that's
    already high-contrast.</div>

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

<script>
const API = '__API_PREFIX__';
let uploadToken = null, uploadFilename = null;

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
  document.getElementById('screen-pick').style.display = 'none';
  document.getElementById('body').style.display = 'flex';
}
document.getElementById('change-file').addEventListener('click', () => {
  fileInput.value = '';
  document.getElementById('upload-status').textContent = '';
  document.getElementById('body').style.display = 'none';
  document.getElementById('screen-pick').style.display = 'block';
});

// Mirrors CupGeometry's own formulas (see cup_etch.py) purely so the user
// gets instant feedback on the calculated height/coverage/rotary-diameter
// while typing, without a round trip -- the actual pattern generation
// below still goes through the real Python geometry class, which is the
// authority (including its own validation) on whether these numbers work.
function computeGeometryPreview() {
  const bottomCirc = parseFloat(document.getElementById('p-bottom-circ').value);
  const topCirc = parseFloat(document.getElementById('p-top-circ').value);
  const side = parseFloat(document.getElementById('p-side').value);
  const width = parseFloat(document.getElementById('p-width').value);
  const out = document.getElementById('computed-geom');

  if (![bottomCirc, topCirc, side, width].every(v => Number.isFinite(v) && v > 0)) {
    out.innerHTML = '';
    return;
  }
  const bottomR = bottomCirc / (2 * Math.PI);
  const topR = topCirc / (2 * Math.PI);
  const refDiameter = bottomR + topR;
  const deltaR = bottomR - topR;
  const discriminant = side * side - deltaR * deltaR;
  if (discriminant <= 0) {
    out.innerHTML = `<span class="err">Side length is too short for this much taper -- ` +
      `it must be more than ${Math.abs(deltaR).toFixed(1)}mm.</span>`;
    return;
  }
  const height = Math.sqrt(discriminant);
  const maxWidth = refDiameter * Math.sin(87.5 * Math.PI / 180);
  if (width >= maxWidth) {
    out.innerHTML = `<span class="err">Design width must stay under ${maxWidth.toFixed(1)}mm ` +
      `for this cup (can't reach the ${refDiameter.toFixed(1)}mm mid-height diameter).</span>`;
    return;
  }
  const wrapAngle = 2 * Math.asin(width / refDiameter) * 180 / Math.PI;
  out.innerHTML = `Available height along the side: <b>${height.toFixed(1)}mm</b> ` +
    `(the image is scaled to the design width above, keeping its own proportions -- ` +
    `its actual height depends on the image and must fit within this)<br>` +
    `Mid-height (rotary calibration) diameter: <b>${refDiameter.toFixed(1)}mm</b><br>` +
    `Front coverage: <b>${wrapAngle.toFixed(0)}&deg;</b>`;
}
['p-bottom-circ', 'p-top-circ', 'p-side', 'p-width'].forEach(id =>
  document.getElementById(id).addEventListener('input', computeGeometryPreview));
computeGeometryPreview();

document.getElementById('generate').addEventListener('click', async () => {
  const info = document.getElementById('result-info');
  const btn = document.getElementById('generate');
  btn.disabled = true;
  info.innerHTML = 'Generating...';
  const body = {
    token: uploadToken,
    bottom_circumference_mm: parseFloat(document.getElementById('p-bottom-circ').value),
    top_circumference_mm: parseFloat(document.getElementById('p-top-circ').value),
    side_length_mm: parseFloat(document.getElementById('p-side').value),
    design_width_mm: parseFloat(document.getElementById('p-width').value),
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
    `Output: <b>${data.output_w}&times;${data.output_h}px</b> ` +
    `(<b>${data.output_width_mm.toFixed(1)}mm &times; ${data.output_height_mm.toFixed(1)}mm</b>, ` +
    `${data.wrap_angle_deg.toFixed(0)}&deg; front coverage)<br>` +
    `Set your rotary attachment's object/roller diameter to <b>${data.reference_diameter_mm.toFixed(1)}mm</b> ` +
    `to match this pattern (the diameter at the design's mid-height).`;
  const link = document.createElement('a');
  link.className = 'download';
  link.href = API + '/api/download/' + data.download_token;
  link.textContent = 'Download ' + data.download_name;
  link.setAttribute('download', data.download_name);
  info.appendChild(link);
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
        )
        dpi = float(body["dpi"])
        if not (3 <= dpi <= 1270):
            raise ValueError("Resolution must be between 3 and 1270 DPI.")
        px_per_mm = dpi / _MM_PER_INCH
        source = _load_image_array(body["token"])
        out, out_w, out_h, design_height_mm = cup_etch.build_pattern(
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
        "output_width_mm": geom.design_width_mm,
        "output_height_mm": design_height_mm,
        "reference_diameter_mm": geom.reference_diameter_mm,
        "wrap_angle_deg": geom.wrap_angle_deg,
        "preview_data_url": preview_data_url,
        "download_token": download_token,
        "download_name": download_name,
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
