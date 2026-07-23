"""Kerf-finding calibration tool: generate a downloadable test-cut SVG (see
kerf_finder.py) and, once it's been cut and measured, calculate the actual
kerf from caliper readings. A Flask Blueprint mounted alongside the other
tools (see hub.py). Unlike kerf_tool.py/cup_etch_tool.py there's no upload
or session state to manage here -- the generated file is a pure function of
the form inputs, and the kerf calculation from measurements is plain
arithmetic done client-side, so nothing needs to be held in memory between
requests.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from . import kerf_finder

bp = Blueprint("kerf_finder_tool", __name__, url_prefix="/kerf-finder")


def _parse_params():
    nominal_mm = float(request.args.get("nominal_mm", 3.0))
    count = int(float(request.args.get("count", 5)))
    step_mm = float(request.args.get("step_mm", 0.05))
    slot_height_mm = float(request.args.get("slot_height_mm", 20.0))
    return nominal_mm, count, step_mm, slot_height_mm


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
  #preview-wrap { margin-top: 12px; background: #fff; border-radius: 6px; padding: 10px; overflow: auto; }
  #preview { display: block; max-width: 100%; }
  #gen-error { color: #f88; font-size: 12px; margin-top: 8px; }
  table { border-collapse: collapse; width: 100%; margin-top: 4px; }
  th, td { padding: 6px 8px; font-size: 12px; text-align: left; }
  th { color: #999; font-weight: 500; border-bottom: 1px solid #444; }
  td input { width: 100%; box-sizing: border-box; background: #2a2a2a; border: 1px solid #444; color: #ddd; padding: 6px; border-radius: 4px; font-size: 12px; }
  td.kerf-cell { color: #ddd; font-family: monospace; }
  td.rm { text-align: center; }
  td.rm button { background: none; border: none; color: #888; cursor: pointer; font-size: 14px; }
  td.rm button:hover { color: #f88; }
  #summary { margin-top: 14px; padding: 12px; background: #1e1e1e; border-radius: 6px; }
  #summary .value { font-size: 22px; font-weight: 700; color: #8fd0ff; }
  #summary .n { font-size: 12px; color: #999; margin-left: 8px; }
  #summary .warn { color: #e0a030; font-size: 12px; margin-top: 6px; }
  #summary .hint { color: #999; font-size: 12px; margin-top: 6px; }
</style>
</head>
<body>
<div id="topbar">
  <h1><a href="__HUB_URL__">&larr; Tools</a> / Kerf finder</h1>
  <a class="action" href="/feedback/?tool=Kerf%20Finder" target="_blank">report a bug / suggest a feature</a>
</div>
<div id="body">
  <p>Kerf varies by machine, power/speed settings, and material, so it's
  worth measuring rather than guessing. Generate a test cut below, run it
  through your laser at the settings you actually plan to use, measure the
  results with calipers, then enter what you measured to get a kerf number
  you can paste straight into the <a href="/kerf-corrector/" style="color:#6cf">Laser Kerf Corrector</a>.</p>

  <section>
    <h2>1. Generate a test cut</h2>
    <p>A row of slots at widths straddling your nominal size, plus one
    solid tab cut at exactly the nominal width for a press-fit test. Each
    feature is labeled with its own drawn width.</p>
    <div class="fields">
      <div class="field"><label>Nominal width (mm)</label><input type="number" id="f-nominal" value="3.0" step="0.01" min="0.01"></div>
      <div class="field"><label>Number of slots</label><input type="number" id="f-count" value="5" step="1" min="2" max="15"></div>
      <div class="field"><label>Step between slots (mm)</label><input type="number" id="f-step" value="0.05" step="0.01" min="0.01"></div>
      <div class="field"><label>Slot height (mm)</label><input type="number" id="f-height" value="20" step="1" min="1"></div>
    </div>
    <div class="actions">
      <a class="btn" id="download-link" href="#">Download test SVG</a>
      <button class="btn secondary" id="use-widths">Use these widths in the calculator below &darr;</button>
    </div>
    <div id="gen-error"></div>
    <div id="preview-wrap"><img id="preview" alt="test pattern preview"></div>
  </section>

  <section>
    <h2>2. Calculate your kerf</h2>
    <p>For each slot you measured, enter the width you drew it at and the
    width it actually came out at. Kerf is measured &minus; drawn for a
    slot (cutting always makes a slot wider) &mdash; enter as many rows as
    you have measurements for; one is enough, more gives a steadier
    average.</p>
    <table>
      <thead><tr><th>Drawn (mm)</th><th>Measured (mm)</th><th>Kerf</th><th></th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="actions" style="margin-top:8px">
      <button class="btn secondary" id="add-row">+ Add row</button>
    </div>
    <div id="summary">
      <span class="value" id="avg-kerf">&mdash;</span><span class="n" id="avg-n"></span>
      <div class="warn" id="spread-warn" style="display:none"></div>
      <div class="actions" style="margin-top:10px">
        <button class="btn" id="copy-kerf" disabled>Copy value</button>
        <span class="hint">Paste this into the Kerf field on the Laser Kerf Corrector page.</span>
      </div>
    </div>
  </section>
</div>

<script>
const API = '__API_PREFIX__';

// ---------------- 1. generate / preview ----------------
function genParams() {
  return {
    nominal_mm: document.getElementById('f-nominal').value,
    count: document.getElementById('f-count').value,
    step_mm: document.getElementById('f-step').value,
    slot_height_mm: document.getElementById('f-height').value,
  };
}

function genUrl(extra) {
  const p = new URLSearchParams({ ...genParams(), ...extra });
  return API + '/api/generate?' + p.toString();
}

let debounceTimer = null;
function refreshPreview() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    const errBox = document.getElementById('gen-error');
    const url = genUrl({});
    const resp = await fetch(url);
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      errBox.textContent = data.error || 'Could not generate that pattern.';
      document.getElementById('download-link').classList.add('disabled');
      return;
    }
    errBox.textContent = '';
    const blob = await resp.blob();
    document.getElementById('preview').src = URL.createObjectURL(blob);
    const nominal = parseFloat(genParams().nominal_mm) || 0;
    const link = document.getElementById('download-link');
    link.href = genUrl({ download: 1 });
    link.setAttribute('download', `kerf-test-${nominal}mm.svg`);
  }, 250);
}

['f-nominal', 'f-count', 'f-step', 'f-height'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshPreview));
refreshPreview();

document.getElementById('use-widths').addEventListener('click', async () => {
  const resp = await fetch(API + '/api/widths?' + new URLSearchParams(genParams()).toString());
  const data = await resp.json();
  if (!resp.ok) {
    document.getElementById('gen-error').textContent = data.error || 'Could not compute widths.';
    return;
  }
  setRows(data.widths_mm.map(w => ({ drawn: w, measured: '' })));
});

// ---------------- 2. measurement calculator ----------------
const rowsBody = document.getElementById('rows');

function addRow(drawn = '', measured = '') {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="number" step="0.001" class="r-drawn" value="${drawn}"></td>
    <td><input type="number" step="0.001" class="r-measured" value="${measured}"></td>
    <td class="kerf-cell">&mdash;</td>
    <td class="rm"><button title="Remove row">&times;</button></td>
  `;
  tr.querySelectorAll('input').forEach(inp => inp.addEventListener('input', recalc));
  tr.querySelector('.rm button').addEventListener('click', () => {
    if (rowsBody.children.length <= 1) return;
    tr.remove();
    recalc();
  });
  rowsBody.appendChild(tr);
}

function setRows(entries) {
  rowsBody.innerHTML = '';
  entries.forEach(e => addRow(e.drawn, e.measured));
  recalc();
}

document.getElementById('add-row').addEventListener('click', () => { addRow(); recalc(); });

let lastAvg = null;

function recalc() {
  const kerfs = [];
  [...rowsBody.children].forEach(tr => {
    const drawn = parseFloat(tr.querySelector('.r-drawn').value);
    const measured = parseFloat(tr.querySelector('.r-measured').value);
    const cell = tr.querySelector('.kerf-cell');
    if (isFinite(drawn) && isFinite(measured)) {
      const k = measured - drawn;
      cell.textContent = k.toFixed(3) + 'mm';
      kerfs.push(k);
    } else {
      cell.textContent = '—';
    }
  });

  const avgBox = document.getElementById('avg-kerf');
  const nBox = document.getElementById('avg-n');
  const warnBox = document.getElementById('spread-warn');
  const copyBtn = document.getElementById('copy-kerf');

  if (kerfs.length === 0) {
    avgBox.textContent = '—';
    nBox.textContent = '';
    warnBox.style.display = 'none';
    copyBtn.disabled = true;
    lastAvg = null;
    return;
  }

  const avg = kerfs.reduce((a, b) => a + b, 0) / kerfs.length;
  lastAvg = avg;
  avgBox.textContent = avg.toFixed(3) + 'mm';
  nBox.textContent = `average of ${kerfs.length} row${kerfs.length === 1 ? '' : 's'}`;
  copyBtn.disabled = false;

  const spread = Math.max(...kerfs) - Math.min(...kerfs);
  if (kerfs.length > 1 && spread > 0.05) {
    warnBox.style.display = 'block';
    warnBox.textContent = `Rows disagree by ${spread.toFixed(3)}mm -- check for a measurement mistake or a slot that didn't cut cleanly before trusting this average.`;
  } else {
    warnBox.style.display = 'none';
  }
}

document.getElementById('copy-kerf').addEventListener('click', async () => {
  if (lastAvg === null) return;
  await navigator.clipboard.writeText(lastAvg.toFixed(3));
  const btn = document.getElementById('copy-kerf');
  const original = btn.textContent;
  btn.textContent = 'Copied!';
  setTimeout(() => { btn.textContent = original; }, 1200);
});

setRows([{ drawn: '', measured: '' }, { drawn: '', measured: '' }, { drawn: '', measured: '' }]);
</script>
</body>
</html>
"""


@bp.route("/")
def index():
    return PAGE.replace("__HUB_URL__", "/").replace("__API_PREFIX__", bp.url_prefix)


@bp.route("/api/generate")
def generate():
    try:
        nominal_mm, count, step_mm, slot_height_mm = _parse_params()
        pattern = kerf_finder.build_test_pattern(nominal_mm, count, step_mm, slot_height_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    resp = Response(pattern.svg, mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="kerf-test-{nominal_mm:g}mm.svg"'
    return resp


@bp.route("/api/widths")
def widths():
    try:
        nominal_mm, count, step_mm, _ = _parse_params()
        w = kerf_finder.slot_widths_mm(nominal_mm, count, step_mm)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"widths_mm": w})
