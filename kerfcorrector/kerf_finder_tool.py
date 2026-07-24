"""Kerf-finding calibration tool: a five-stage flow that ends with a
settings profile ready for the Kerf Corrector.

1. Cut a plain nominal-size square, measure what's left, get the basic
   kerf (see kerf_finder.build_kerf_square).
2. Cut a mortice+tenon ladder -- one fixed mortice socket plus several
   attached tenon pieces at increasing extra clearance -- and note which
   tenon press-fits the way you want, to get tenon_clearance_mm
   (see kerf_finder.build_mortice_tenon_ladder). mortice_clearance_mm
   always stays 0 -- see docs/calibration-process.md's "mortice stays
   fixed" convention.
3. Cut a teeth ladder -- pairs of actual interlocking finger-joint combs,
   both sides of each pair swept together -- to get teeth_clearance_mm
   (see kerf_finder.build_teeth_ladder).
4. Cut a slot ladder -- pairs of mating notched panels, both sides of
   each pair swept together -- to get slot_clearance_mm
   (see kerf_finder.build_slot_ladder).
5. Fill in chamfer_mm (not physically calibrated here -- it eases
   insertion rather than changing tightness) and download all six
   numbers as one kerf-settings.json.

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
  .preview-wrap { margin-top: 12px; background: #fff; border-radius: 6px; padding: 10px; overflow: auto; }
  .preview-wrap img { display: block; max-width: 100%; }
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
  needs for your machine/material combination: cut four small test pieces
  below, measure/test-fit them, and get a settings file with all six
  values ready to use. <code>mortice_clearance_mm</code> always stays 0 --
  see the note in step 2.</p>

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
    <div class="preview-wrap"><img id="sq-preview" alt="test square preview"></div>
    <div class="fields" style="margin-top:14px">
      <div class="field"><label>Measured width (mm)</label><input type="number" id="sq-measured-w" step="0.001"></div>
      <div class="field"><label>Measured height (mm)</label><input type="number" id="sq-measured-h" step="0.001"></div>
    </div>
    <div class="note">Only one measurement is required &mdash; fill in both for a steadier average if the cut isn't perfectly square.</div>
    <div id="kerf-result">
      <span class="value" id="kerf-value">&mdash;</span>
      <div class="hint">This carries down to every ladder below automatically, and into the final settings file.</div>
    </div>
  </section>

  <section>
    <h2>2. Fine-tune mortice/tenon fit</h2>
    <p>One fixed mortice socket, plus several rails cut with an actual
    <em>attached</em> tenon (not a free-floating tab) protruding a bit less
    each time. Mortice and tenon are a mated pair with redundant control --
    enlarging the mortice or shrinking the tenon achieve the same fit -- so
    this tool always leaves the mortice at kerf-only (0 extra clearance)
    and does all the tuning through the tenon side. Try each tenon in the
    one mortice and read the clearance value off whichever fits.</p>
    <div class="fields">
      <div class="field"><label>Kerf (mm)</label><input type="number" id="mt-kerf" step="0.001"></div>
      <div class="field"><label>Mortice/tenon size (mm)</label><input type="number" id="mt-nominal" value="10" step="0.1" min="0.1"></div>
      <div class="field"><label>Number of tenons</label><input type="number" id="mt-count" value="5" step="1" min="2" max="12"></div>
      <div class="field"><label>Clearance step (mm)</label><input type="number" id="mt-step" value="0.05" step="0.01" min="0.01"></div>
      <div class="field"><label>Engagement depth (mm)</label><input type="number" id="mt-depth" value="8" step="1" min="1"></div>
    </div>
    <div class="actions">
      <a class="btn" id="mt-download" href="#">Download test ladder</a>
    </div>
    <div class="gen-error" id="mt-error"></div>
    <div class="preview-wrap"><img id="mt-preview" alt="mortice/tenon ladder preview"></div>
    <div class="fields" style="margin-top:14px">
      <div class="field"><label>Tenon clearance that fit best (mm)</label><input type="number" id="tenon-clearance" value="0" step="0.01"></div>
    </div>
  </section>

  <section>
    <h2>3. Fine-tune finger-joint (teeth) fit</h2>
    <p>Actual interlocking finger-joint combs, not one isolated tooth: each
    rung is a pair of complementary combs (one starts/ends with a tooth,
    the other with a gap, so they mesh across the full width) cut at the
    same clearance on both sides -- real finger joints normally have both
    mating panels' teeth classified <code>teeth</code>, so there's no fixed
    side here the way mortice/tenon has. Interlock each pair and judge the
    fit mainly from the middle teeth -- the very first/last tooth of each
    comb is bounded on one side by its own carrier's plain edge rather than
    a matching tooth, so it's slightly looser than the interior ones by
    design, not a defect.</p>
    <div class="fields">
      <div class="field"><label>Finger width (mm)</label><input type="number" id="teeth-nominal" value="10" step="0.1" min="0.1"></div>
      <div class="field"><label>Number of pairs</label><input type="number" id="teeth-count" value="5" step="1" min="2" max="12"></div>
      <div class="field"><label>Clearance step (mm)</label><input type="number" id="teeth-step" value="0.05" step="0.01" min="0.01"></div>
      <div class="field"><label>Teeth per comb</label><input type="number" id="teeth-per-comb" value="3" step="1" min="2"></div>
      <div class="field"><label>Engagement depth (mm)</label><input type="number" id="teeth-depth" value="8" step="1" min="1"></div>
    </div>
    <div class="actions">
      <a class="btn" id="teeth-download" href="#">Download test ladder</a>
    </div>
    <div class="gen-error" id="teeth-error"></div>
    <div class="preview-wrap"><img id="teeth-preview" alt="finger-joint ladder preview"></div>
    <div class="fields" style="margin-top:14px">
      <div class="field"><label>Clearance that fit best (mm)</label><input type="number" id="teeth-clearance" value="0" step="0.01"></div>
    </div>
  </section>

  <section>
    <h2>4. Fine-tune slot fit</h2>
    <p>Pairs of notched panels -- a cross-lap/halving-joint style slot,
    where both mating pieces are voids that interlock where their notches
    overlap. Both notches in a pair are cut with the same clearance (the
    tool's own rule: both sides of a slot joint share one value, unlike
    mortice/tenon). Interlock each pair and read the clearance off
    whichever fits.</p>
    <div class="fields">
      <div class="field"><label>Slot width (mm)</label><input type="number" id="slot-nominal" value="10" step="0.1" min="0.1"></div>
      <div class="field"><label>Number of pairs</label><input type="number" id="slot-count" value="5" step="1" min="2" max="12"></div>
      <div class="field"><label>Clearance step (mm)</label><input type="number" id="slot-step" value="0.05" step="0.01" min="0.01"></div>
      <div class="field"><label>Engagement depth (mm)</label><input type="number" id="slot-depth" value="8" step="1" min="1"></div>
    </div>
    <div class="actions">
      <a class="btn" id="slot-download" href="#">Download test ladder</a>
    </div>
    <div class="gen-error" id="slot-error"></div>
    <div class="preview-wrap"><img id="slot-preview" alt="slot ladder preview"></div>
    <div class="fields" style="margin-top:14px">
      <div class="field"><label>Clearance that fit best (mm)</label><input type="number" id="slot-clearance" value="0" step="0.01"></div>
    </div>
  </section>

  <section>
    <h2>5. Remaining settings &amp; download profile</h2>
    <p>Chamfer isn't physically calibrated by this tool: it eases insertion
    rather than changing overall tightness (and only ever applies to
    tenon), so a ladder test doesn't map as cleanly. Enter a value you're
    comfortable with &mdash; 0 leaves it off.</p>
    <div class="fields">
      <div class="field"><label>Chamfer (mm)</label><input type="number" id="chamfer" value="0" step="0.01"></div>
    </div>
    <div id="profile-summary">
      <table>
        <tr><td class="k">Kerf</td><td class="v" id="ps-kerf">&mdash;</td></tr>
        <tr><td class="k">Mortice clearance</td><td class="v" id="ps-mortice">0.000mm (fixed)</td></tr>
        <tr><td class="k">Tenon clearance</td><td class="v" id="ps-tenon">&mdash;</td></tr>
        <tr><td class="k">Teeth clearance</td><td class="v" id="ps-teeth">&mdash;</td></tr>
        <tr><td class="k">Slot clearance</td><td class="v" id="ps-slot">&mdash;</td></tr>
        <tr><td class="k">Chamfer</td><td class="v" id="ps-chamfer">&mdash;</td></tr>
      </table>
    </div>
    <div class="actions" style="margin-top:14px">
      <button class="btn" id="download-profile">Download kerf-settings.json</button>
    </div>
    <div class="note">Uses the same format the Kerf Corrector's Save/Load Settings feature reads, so once that's on the version you're running, its "Load settings" button imports all six numbers from this file in one step.</div>
  </section>
</div>

<script>
const API = '__API_PREFIX__';

function debounced(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function refreshPreview(endpoint, params, previewId, downloadId, errorId, filenamePrefix) {
  const errBox = document.getElementById(errorId);
  const p = new URLSearchParams(params);
  const resp = await fetch(API + endpoint + '?' + p.toString());
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    errBox.textContent = data.error || 'Could not generate that pattern.';
    return;
  }
  errBox.textContent = '';
  const blob = await resp.blob();
  document.getElementById(previewId).src = URL.createObjectURL(blob);
  const nominal = parseFloat(params.nominal_mm) || 0;
  const link = document.getElementById(downloadId);
  link.href = API + endpoint + '?' + new URLSearchParams({ ...params, download: 1 }).toString();
  link.setAttribute('download', `${filenamePrefix}-${nominal}mm.svg`);
}

// ---------------- 1. kerf square ----------------
function sqParams() {
  return { nominal_mm: document.getElementById('sq-nominal').value };
}
const refreshSquare = debounced(() =>
  refreshPreview('/api/generate-square', sqParams(), 'sq-preview', 'sq-download', 'sq-error', 'kerf-test-square'), 250);
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
  document.getElementById('mt-kerf').value = kerf.toFixed(3);
  refreshMorticeTenon();
  refreshTeeth();
  refreshSlot();
  updateProfileSummary();
}
['sq-measured-w', 'sq-measured-h', 'sq-nominal'].forEach(id =>
  document.getElementById(id).addEventListener('input', recalcKerf));

// ---------------- 2. mortice/tenon ladder ----------------
function mtParams() {
  return {
    nominal_mm: document.getElementById('mt-nominal').value,
    kerf_mm: document.getElementById('mt-kerf').value || '0',
    count: document.getElementById('mt-count').value,
    step_mm: document.getElementById('mt-step').value,
    engagement_depth_mm: document.getElementById('mt-depth').value,
  };
}
const refreshMorticeTenon = debounced(() =>
  refreshPreview('/api/generate-mortice-tenon-ladder', mtParams(), 'mt-preview', 'mt-download', 'mt-error',
                 'kerf-test-mortice-tenon'), 250);
['mt-kerf', 'mt-nominal', 'mt-count', 'mt-step', 'mt-depth'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshMorticeTenon));
refreshMorticeTenon();

// ---------------- 3. teeth ladder ----------------
function teethParams() {
  return {
    nominal_mm: document.getElementById('teeth-nominal').value,
    kerf_mm: document.getElementById('mt-kerf').value || '0',
    count: document.getElementById('teeth-count').value,
    step_mm: document.getElementById('teeth-step').value,
    teeth_per_comb: document.getElementById('teeth-per-comb').value,
    engagement_depth_mm: document.getElementById('teeth-depth').value,
  };
}
const refreshTeeth = debounced(() =>
  refreshPreview('/api/generate-teeth-ladder', teethParams(), 'teeth-preview', 'teeth-download', 'teeth-error',
                 'kerf-test-teeth'), 250);
['mt-kerf', 'teeth-nominal', 'teeth-count', 'teeth-step', 'teeth-per-comb', 'teeth-depth'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshTeeth));
refreshTeeth();

// ---------------- 4. slot ladder ----------------
function slotParams() {
  return {
    nominal_mm: document.getElementById('slot-nominal').value,
    kerf_mm: document.getElementById('mt-kerf').value || '0',
    count: document.getElementById('slot-count').value,
    step_mm: document.getElementById('slot-step').value,
    engagement_depth_mm: document.getElementById('slot-depth').value,
  };
}
const refreshSlot = debounced(() =>
  refreshPreview('/api/generate-slot-ladder', slotParams(), 'slot-preview', 'slot-download', 'slot-error',
                 'kerf-test-slot'), 250);
['mt-kerf', 'slot-nominal', 'slot-count', 'slot-step', 'slot-depth'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshSlot));
refreshSlot();

// ---------------- 5. profile ----------------
function updateProfileSummary() {
  document.getElementById('ps-kerf').textContent = (parseFloat(document.getElementById('mt-kerf').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-tenon').textContent = (parseFloat(document.getElementById('tenon-clearance').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-teeth').textContent = (parseFloat(document.getElementById('teeth-clearance').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-slot').textContent = (parseFloat(document.getElementById('slot-clearance').value) || 0).toFixed(3) + 'mm';
  document.getElementById('ps-chamfer').textContent = (parseFloat(document.getElementById('chamfer').value) || 0).toFixed(3) + 'mm';
}
['mt-kerf', 'tenon-clearance', 'teeth-clearance', 'slot-clearance', 'chamfer'].forEach(id =>
  document.getElementById(id).addEventListener('input', updateProfileSummary));
updateProfileSummary();

document.getElementById('download-profile').addEventListener('click', () => {
  const profile = {
    type: 'kerf-corrector-settings',
    kerf_mm: parseFloat(document.getElementById('mt-kerf').value) || 0,
    mortice_clearance_mm: 0,
    tenon_clearance_mm: parseFloat(document.getElementById('tenon-clearance').value) || 0,
    teeth_clearance_mm: parseFloat(document.getElementById('teeth-clearance').value) || 0,
    slot_clearance_mm: parseFloat(document.getElementById('slot-clearance').value) || 0,
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


@bp.route("/api/generate-mortice-tenon-ladder")
def generate_mortice_tenon_ladder():
    try:
        nominal_mm = float(request.args.get("nominal_mm", 10.0))
        kerf_mm = float(request.args.get("kerf_mm", 0.0))
        count = int(float(request.args.get("count", 5)))
        step_mm = float(request.args.get("step_mm", 0.05))
        engagement_depth_mm = float(request.args.get("engagement_depth_mm", 8.0))
        ladder = kerf_finder.build_mortice_tenon_ladder(nominal_mm, kerf_mm, count, step_mm, engagement_depth_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    resp = Response(ladder.svg, mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="kerf-test-mortice-tenon-{nominal_mm:g}mm.svg"'
    return resp


@bp.route("/api/generate-teeth-ladder")
def generate_teeth_ladder():
    try:
        nominal_mm = float(request.args.get("nominal_mm", 10.0))
        kerf_mm = float(request.args.get("kerf_mm", 0.0))
        count = int(float(request.args.get("count", 5)))
        step_mm = float(request.args.get("step_mm", 0.05))
        teeth_per_comb = int(float(request.args.get("teeth_per_comb", 3)))
        engagement_depth_mm = float(request.args.get("engagement_depth_mm", 8.0))
        ladder = kerf_finder.build_teeth_ladder(nominal_mm, kerf_mm, count, step_mm, teeth_per_comb,
                                                 engagement_depth_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    resp = Response(ladder.svg, mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="kerf-test-teeth-{nominal_mm:g}mm.svg"'
    return resp


@bp.route("/api/generate-slot-ladder")
def generate_slot_ladder():
    try:
        nominal_mm = float(request.args.get("nominal_mm", 10.0))
        kerf_mm = float(request.args.get("kerf_mm", 0.0))
        count = int(float(request.args.get("count", 5)))
        step_mm = float(request.args.get("step_mm", 0.05))
        engagement_depth_mm = float(request.args.get("engagement_depth_mm", 8.0))
        ladder = kerf_finder.build_slot_ladder(nominal_mm, kerf_mm, count, step_mm, engagement_depth_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    resp = Response(ladder.svg, mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="kerf-test-slot-{nominal_mm:g}mm.svg"'
    return resp
