"""Bug/enhancement feedback: a small public submission form linked from every
tool's topbar, and a Basic-Auth-protected admin page to review submissions.

Not listed in hub.py's TOOLS -- it's a utility page, not a tool, so it stays
off the landing page but is still reachable directly at /feedback/.

Submissions are appended as JSON Lines to a flat file living outside the
git-tracked repo (in the process owner's home directory), so `git pull`
never touches it and it survives every deploy/reload -- unlike the
in-memory upload store the tools themselves use (see kerf_tool.py), this
data needs to actually persist.
"""

from __future__ import annotations

import html
import json
import os
import time
from functools import wraps
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

bp = Blueprint("feedback", __name__, url_prefix="/feedback")

DATA_DIR = Path(os.environ.get("FEEDBACK_DATA_DIR", str(Path.home() / "laser-kerf-corrector-data")))
DATA_FILE = DATA_DIR / "feedback.jsonl"

ADMIN_USER = os.environ.get("FEEDBACK_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("FEEDBACK_ADMIN_PASS")  # unset -> admin page refuses to serve

KINDS = ("bug", "enhancement", "other")


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_PASS:
            return Response(
                "Feedback admin page is not configured -- set the FEEDBACK_ADMIN_PASS "
                "environment variable (e.g. in the WSGI config file) and reload.",
                status=503,
            )
        auth = request.authorization
        if not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASS:
            return Response(
                "Authentication required.", status=401,
                headers={"WWW-Authenticate": 'Basic realm="Feedback admin"'},
            )
        return view(*args, **kwargs)
    return wrapped


def _load_entries() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    entries = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries


FORM_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Report a bug / suggest a feature</title>
<style>
  html, body { margin: 0; min-height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  body { padding: 30px 20px; }
  .wrap { max-width: 480px; margin: 0 auto; }
  h1 { font-size: 18px; margin: 0 0 6px; }
  p.hint { font-size: 13px; color: #999; margin: 0 0 20px; line-height: 1.4; }
  label { display: block; font-size: 12px; color: #aaa; margin: 14px 0 4px; }
  input, select, textarea { width: 100%; box-sizing: border-box; background: #2a2a2a; border: 1px solid #444;
    color: #ddd; padding: 8px; border-radius: 4px; font-size: 13px; font-family: inherit; }
  textarea { resize: vertical; min-height: 100px; }
  .btn { margin-top: 18px; background: #3c6e96; color: white; border: none; padding: 9px 16px;
    border-radius: 4px; cursor: pointer; font-size: 13px; }
  .btn:hover { background: #4a84b3; }
  .btn:disabled { background: #333; color: #777; cursor: default; }
  #status { margin-top: 14px; font-size: 13px; }
  #status.err { color: #f88; }
  #status.ok { color: #8f8; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Report a bug / suggest a feature</h1>
  <p class="hint">Goes straight to the developer -- not a public forum. Include
  enough detail to reproduce a bug (what file, what you clicked, what you expected).</p>
  <form id="f">
    <label>Type</label>
    <select id="kind">
      <option value="bug">Bug</option>
      <option value="enhancement">Enhancement idea</option>
      <option value="other">Other</option>
    </select>
    <label>Message</label>
    <textarea id="message" required></textarea>
    <label>Contact (optional -- only if you want a reply)</label>
    <input id="contact" type="text" placeholder="email or leave blank">
    <button class="btn" id="submit" type="submit">Send</button>
    <div id="status"></div>
  </form>
</div>
<script>
const params = new URLSearchParams(location.search);
const tool = params.get('tool') || '';
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submit');
  const status = document.getElementById('status');
  const message = document.getElementById('message').value.trim();
  if (!message) return;
  btn.disabled = true;
  status.className = '';
  status.textContent = 'Sending...';
  const resp = await fetch('__API_PREFIX__/api/submit', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      kind: document.getElementById('kind').value,
      message,
      contact: document.getElementById('contact').value.trim(),
      tool,
      url: document.referrer,
    }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    btn.disabled = false;
    status.className = 'err';
    status.textContent = 'Error: ' + (data.error || resp.statusText);
    return;
  }
  status.className = 'ok';
  status.textContent = 'Thanks -- sent.';
  document.getElementById('f').querySelectorAll('input, textarea, button').forEach(el => el.disabled = true);
});
</script>
</body>
</html>
"""

ADMIN_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Feedback</title>
<style>
  html, body { margin: 0; min-height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  body { padding: 24px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .count { font-size: 13px; color: #999; margin: 0 0 18px; }
  .entry { background: #262626; border: 1px solid #383838; border-radius: 6px; padding: 12px 14px; margin-bottom: 10px; }
  .entry .meta { font-size: 11px; color: #999; margin-bottom: 6px; display: flex; gap: 10px; flex-wrap: wrap; }
  .kind-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; }
  .kind-badge.bug { background: #c0392b; color: #fff; }
  .kind-badge.enhancement { background: #3c96ff; color: #001428; }
  .kind-badge.other { background: #555; color: #ccc; }
  .entry .message { font-size: 13px; white-space: pre-wrap; line-height: 1.4; }
  .empty { color: #999; font-size: 13px; }
</style>
</head>
<body>
<h1>Feedback</h1>
<p class="count">__COUNT__ submissions</p>
__ROWS__
</body>
</html>
"""

ENTRY_TEMPLATE = """<div class="entry">
  <div class="meta">
    <span>{when}</span>
    <span class="kind-badge {kind}">{kind}</span>
    <span>tool: {tool}</span>
    <span>contact: {contact}</span>
    <span>from: {url}</span>
  </div>
  <div class="message">{message}</div>
</div>"""


@bp.route("/")
def form():
    return FORM_PAGE.replace("__API_PREFIX__", bp.url_prefix)


@bp.route("/api/submit", methods=["POST"])
def submit():
    body = request.get_json(force=True)
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Please enter a message."}), 400

    entry = {
        "ts": time.time(),
        "kind": body.get("kind") if body.get("kind") in KINDS else "other",
        "message": message[:4000],
        "contact": (body.get("contact") or "").strip()[:200],
        "tool": (body.get("tool") or "").strip()[:80],
        "url": (body.get("url") or "").strip()[:300],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return jsonify({"ok": True})


@bp.route("/admin")
@require_admin
def admin():
    entries = _load_entries()
    rows = "\n".join(
        ENTRY_TEMPLATE.format(
            when=time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts", 0))),
            kind=_esc(e.get("kind", "other")),
            tool=_esc(e.get("tool") or "-"),
            contact=_esc(e.get("contact") or "-"),
            url=_esc(e.get("url") or "-"),
            message=_esc(e.get("message", "")),
        )
        for e in entries
    )
    return (
        ADMIN_PAGE
        .replace("__COUNT__", str(len(entries)))
        .replace("__ROWS__", rows or '<p class="empty">No feedback yet.</p>')
    )
