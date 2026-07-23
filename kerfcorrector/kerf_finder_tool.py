"""Kerf-finding calibration tool: a three-stage flow that ends with a
settings profile ready for the Kerf Corrector.

1. Cut a plain nominal-size square, measure what's left, get the basic
   kerf (see kerf_finder.build_kerf_square).
2. Cut a "ladder" -- one fixed hole plus several tabs at increasing extra
   clearance -- and note which tab press-fits the way you want, to get
   tab_hole_clearance_mm (see kerf_finder.build_tab_hole_ladder).
3. Fill in the two settings this tool doesn't yet calibrate physically
   (tab_finger_clearance_mm, chamfer_mm -- see kerf_finder.py's module
   docstring for why finger-joint clearance isn't generated here) and
   download all four numbers as one kerf-settings.json.

A Flask Blueprint mounted alongside the other tools (see hub.py). Like
cup_etch_tool.py/kerf_tool.py's own upload store, nothing here needs
session state: every generated file is a pure function of its query
parameters, and the arithmetic (kerf from measurements, the final JSON
profile) is plain client-side math on numbers the user types in.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from . import kerf_finder

bp = Blueprint("kerf_finder_tool", __name__, url_prefix="/kerf-finder")


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Kerf finder</title>
<style>
  html, body { margin: 0; min-height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  #topbar { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: #262626; border-bottom: 1px solid #444; }
  #topbar h1 { font-size: 14px; margin: 0; font-weight: 600; }
  #topbar h1 a { color: #ddd; text-decoration: none; }
  #topbar a.action { color: #6cf; font-size: 12px; cursor: pointer; text-decoration: none; margin-left: auto; }
  #body { max-width: 720px; margin: 0 auto; padding: 20px; }
  p { font-size: 13px; color: #aaa; line-height: 1.5; }
  section { background: #262626; border: 1px solid #383838; border-radius: 8px; padding: 16px 18px; margin-bottom: 18px; }
  section h2 { font-size: 15px; margin: 0 0 10px; color: #8fd0ff; }
  .fields { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
  .field { display: flex; flex-direction: column; gap: 3px; }
  .field label { font-size: 12px; color: #aaa; }
  .field input { width: 110px; box-sizing: border-box; background: #2a2a2a; border: 1px solid #444; color: #ddd; padding: 7px; border-radius: 4px; font-size: 13px; }
  .btn { background: #3c6e96; color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; text-decoration: none; display: inline-block; }
  .btn:hover { background: #4a84b3; }
  .btn:disabled { background: #333; color: #777; cursor: default; }
  .btn.secondary { background: #3a3a3a; }
  .btn.secondary:hover { background: #454545; }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  #square-preview-wrap, #ladder-preview-wrap { margin-top: 12px; background: #fff; border-radius: 6px; padding: 10px; overflow: auto; }
  #square-preview-wrap img, #ladder-preview-wrap img { display: block; max-width: 100%; }
  .gen-error { color: #f88; font-size: 12px; margin-top: 8px; }
  .note { font-size: 12px; color: #888; margin-top: 6px; line-height: 1.4; }
  #kerf-result, #profile-summary { margin-top: 14px; padding: 12px; background: #1e1e1e; border-radius: 6px; }
  #kerf-result .value { font-size: 22px; font-weight: 700; color: #8fd0ff; }
  #kerf-result .hint { font-size: 12px; color: #999; margin-top: 6px; }
  #profile-summary table { border-collapse: collapse; width: 100%; }
  #profile-summary td { padding: 3px 0; font-size: 13px; }
  #profile-summary td.k { color: #aaa; }
  #profile-summary td.v { text-align: right; font-family: monospace; color: #8fd0ff; }
</style>
</head>
<body>
<div id="topbar">
  <h1><a href="__HUB_URL__">&larr; Tools</a> / Kerf finder</h1>
  <a class="action" href="/feedback/?tool=Kerf%20Finder" target="_blank">report a bug / suggest a feature</a>
</div>
<div id="body">
  <p>Works out the numbers the <a href="/kerf-corrector/" style="color:#6cf">Laser Kerf Corrector</a>
  needs for your machine/material combination: cut two small test pieces
  below, measure them, and get a settings file with all four values ready
  to use.</p>

  <section>
    <h2>1. Find your basic kerf</h2>
    <p>Cut this square out, measure what's left with calipers, and enter
    what you got. The shortfall from the drawn size is your kerf &mdash;
    cutting always removes a strip this wide from every edge, so a solid
    square comes out smaller by exactly one kerf on each dimension.</p>
    <div class="fields">
      <div class="field"><label>Square size (mm)</label><input type="number" id="sq-nominal" value="25" step="0.1" min="0.1"></div>
    </div>
    <div class="actions">
      <a class="btn" id="sq-download" href="#">Download test square</a>
    </div>
    <div class="gen-error" id="sq-error"></div>
    <div id="square-preview-wrap"><img id="sq-preview" alt="test square preview"></div>
    <div class="fields" style="margin-top:14px">
      <div class="field"><label>Measured width (mm)</label><input type="number" id="sq-measured-w" step="0.001"></div>
      <div class="field"><label>Measured height (mm)</label><input type="number" id="sq-measured-h" step="0.001"></div>
    </div>
    <div class="note">Only one measurement is required &mdash; fill in both for a steadier average if the cut isn't perfectly square.</div>
    <div id="kerf-result">
      <span class="value" id="kerf-value">&mdash;</span>
      <div class="hint">This carries down to step 2 automatically, and into the final settings file.</div>
    </div>
  </section>

  <section>
    <h2>2. Fine-tune tab-into-hole fit</h2>
    <p>One hole at a fixed size, plus several free-standing tabs cut a bit
    smaller each time (kerf correction alone, then an increasing amount of
    <em>extra</em> clearance on top). Try each tab in the hole and see
    which one gives the press-fit you want &mdash; then read its printed
    clearance value off the piece and enter it below.</p>
    <div class="fields">
      <div class="field"><label>Kerf (mm)</label><input type="number" id="ladder-kerf" step="0.001"></div>
      <div class="field"><label>Hole/tab size (mm)</label><input type="number" id="ladder-nominal" value="10" step="0.1" min="0.1"></div>
      <div class="field"><label>Number of tabs</label><input type="number" id="ladder-count" value="5" step="1" min="2" max="12"></div>
      <div class="field"><label>Clearance step (mm)</label><input type="number" id="ladder-step" value="0.05" step="0.01" min="0.01"></div>
      <div class="field"><label>Tab height (mm)</label><input type="number" id="ladder-height" value="15" step="1" min="1"></div>
    </div>
    <div class="actions">
      <a class="btn" id="ladder-download" href="#">Download test ladder</a>
    </div>
    <div class="gen-error" id="ladder-error"></div>
    <div id="ladder-preview-wrap"><img id="ladder-preview" alt="tab/hole ladder preview"></div>
    <div class="fields" style="margin-top:14px">
      <div class="field"><label>Clearance that fit best (mm)</label><input type="number" id="tab-hole-clearance" value="0" step="0.01"></div>
    </div>
  </section>

  <section>
    <h2>3. Remaining settings &amp; download profile</h2>
    <p>These two aren't physically calibrated by this tool yet:
    finger-joint tabs have trickier correction math than a plain
    tab-into-hole (their length and width axes shift by different
    amounts), and chamfer only eases insertion rather than changing overall
    tightness. Enter values you're comfortable with &mdash; 0 leaves either
    one off.</p>
    <div class="fields">
      <div class="field"><label>Finger-joint tab clearance (mm)</label><input type="number" id="tab-finger-clearance" value="0" step="0.01"></div>
      <div class="field"><label>Chamfer (mm)</label><input type="number" id="chamfer" value="0" step="0.01"></div>
    </div>
    <div id="profile-summary">
      <table>
        <tr><td class="k">Kerf</td><td class="v" id="ps-kerf">&mdash;</td></tr>
        <tr><td class="k">Tab (hole) clearance</td><td class="v" id="ps-tab-hole">&mdash;</td></tr>
        <tr><td class="k">Tab (finger) clearance</td><td class="v" id="ps-tab-finger">&mdash;</td></tr>
        <tr><td class="k">Chamfer</td><td class="v" id="ps-chamfer">&mdash;</td></tr>
      </table>
    </div>
    <div class="actions" style="margin-top:14px">
      <button class="btn" id="download-profile">Download kerf-settings.json</button>
    </div>
    <div class="note">Uses the same format the Kerf Corrector's Save/Load Settings feature reads, so once that's on the version you're running, its "Load settings" button imports all four numbers from this file in one step.</div>
  </section>
</div>

<script>
const API = '__API_PREFIX__';

function debounced(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------- 1. kerf square ----------------
function sqParams() {
  return { nominal_mm: document.getElementById('sq-nominal').value };
}

const refreshSquare = debounced(async () => {
  const errBox = document.getElementById('sq-error');
  const p = new URLSearchParams(sqParams());
  const resp = await fetch(API + '/api/generate-square?' + p.toString());
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    errBox.textContent = data.error || 'Could not generate that square.';
    return;
  }
  errBox.textContent = '';
  const blob = await resp.blob();
  document.getElementById('sq-preview').src = URL.createObjectURL(blob);
  const nominal = parseFloat(sqParams().nominal_mm) || 0;
  const link = document.getElementById('sq-download');
  link.href = API + '/api/generate-square?' + new URLSearchParams({ ...sqParams(), download: 1 }).toString();
  link.setAttribute('download', `kerf-test-square-${nominal}mm.svg`);
}, 250);

document.getElementById('sq-nominal').addEventListener('input', refreshSquare);
refreshSquare();

function recalcKerf() {
  const nominal = parseFloat(sqParams().nominal_mm);
  const w = parseFloat(document.getElementById('sq-measured-w').value);
  const h = parseFloat(document.getElementById('sq-measured-h').value);
  const measurements = [w, h].filter(v => isFinite(v));
  const resultBox = document.getElementById('kerf-value');
  if (!isFinite(nominal) || measurements.length === 0) {
    resultBox.textContent = '—';
    return;
  }
  const avgMeasured = measurements.reduce((a, b) => a + b, 0) / measurements.length;
  const kerf = nominal - avgMeasured;
  resultBox.textContent = `Kerf: ${kerf.toFixed(3)}mm`;
  document.getElementById('ladder-kerf').value = kerf.toFixed(3);
  refreshLadder();
  updateProfileSummary();
}
['sq-measured-w', 'sq-measured-h', 'sq-nominal'].forEach(id =>
  document.getElementById(id).addEventListener('input', recalcKerf));

// ---------------- 2. tab/hole ladder ----------------
function ladderParams() {
  return {
    nominal_mm: document.getElementById('ladder-nominal').value,
    kerf_mm: document.getElementById('ladder-kerf').value || '0',
    count: document.getElementById('ladder-count').value,
    step_mm: document.getElementById('ladder-step').value,
    tab_height_mm: document.getElementById('ladder-height').value,
  };
}

const refreshLadder = debounced(async () => {
  const errBox = document.getElementById('ladder-error');
  const p = new URLSearchParams(ladderParams());
  const resp = await fetch(API + '/api/generate-ladder?' + p.toString());
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    errBox.textContent = data.error || 'Could not generate that ladder.';
    return;
  }
  errBox.textContent = '';
  const blob = await resp.blob();
  document.getElementById('ladder-preview').src = URL.createObjectURL(blob);
  const nominal = parseFloat(ladderParams().nominal_mm) || 0;
  const link = document.getElementById('ladder-download');
  link.href = API + '/api/generate-ladder?' + new URLSearchParams({ ...ladderParams(), download: 1 }).toString();
  link.setAttribute('download', `kerf-test-ladder-${nominal}mm.svg`);
}, 250);

['ladder-kerf', 'ladder-nominal', 'ladder-count', 'ladder-step', 'ladder-height'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshLadder));
refreshLadder();

// ---------------- 3. profile ----------------
function updateProfileSummary() {
  document.getElementById('ps-kerf').textContent = (parseFloat(document.getElementById('ladder-kerf').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-tab-hole').textContent = (parseFloat(document.getElementById('tab-hole-clearance').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-tab-finger').textContent = (parseFloat(document.getElementById('tab-finger-clearance').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-chamfer').textContent = (parseFloat(document.getElementById('chamfer').value) || 0).toFixed(3) + 'mm';
}
['ladder-kerf', 'tab-hole-clearance', 'tab-finger-clearance', 'chamfer'].forEach(id =>
  document.getElementById(id).addEventListener('input', updateProfileSummary));
updateProfileSummary();

document.getElementById('download-profile').addEventListener('click', () => {
  const profile = {
    type: 'kerf-corrector-settings',
    kerf_mm: parseFloat(document.getElementById('ladder-kerf').value) || 0,
    tab_hole_clearance_mm: parseFloat(document.getElementById('tab-hole-clearance').value) || 0,
    tab_finger_clearance_mm: parseFloat(document.getElementById('tab-finger-clearance').value) || 0,
    chamfer_mm: parseFloat(document.getElementById('chamfer').value) || 0,
  };
  const blob = new Blob([JSON.stringify(profile, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'kerf-settings.json';
  link.click();
  URL.revokeObjectURL(url);
});
</script>
</body>
</html>
"""


@bp.route("/")
def index():
    return PAGE.replace("__HUB_URL__", "/").replace("__API_PREFIX__", bp.url_prefix)


@bp.route("/api/generate-square")
def generate_square():
    try:
        nominal_mm = float(request.args.get("nominal_mm", 25.0))
        square = kerf_finder.build_kerf_square(nominal_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    resp = Response(square.svg, mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="kerf-test-square-{nominal_mm:g}mm.svg"'
    return resp


@bp.route("/api/generate-ladder")
def generate_ladder():
    try:
        nominal_mm = float(request.args.get("nominal_mm", 10.0))
        kerf_mm = float(request.args.get("kerf_mm", 0.0))
        count = int(float(request.args.get("count", 5)))
        step_mm = float(request.args.get("step_mm", 0.05))
        tab_height_mm = float(request.args.get("tab_height_mm", 15.0))
        ladder = kerf_finder.build_tab_hole_ladder(nominal_mm, kerf_mm, count, step_mm, tab_height_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    resp = Response(ladder.svg, mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="kerf-test-ladder-{nominal_mm:g}mm.svg"'
    return resp
