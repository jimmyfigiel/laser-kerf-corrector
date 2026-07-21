"""On-demand deploy: pull the latest `main` from GitHub and reload the web
app via PythonAnywhere's own API, triggered by a single authenticated
request instead of driving the PythonAnywhere web UI (browser automation
proved unreliable in practice -- unannounced no-ops, and the UI's own
reload button reporting success without actually restarting the worker).

Deliberately a manual trigger, not automatic-on-push: when more than one
tool/session is being worked on in the same checkout, whoever pushes first
shouldn't unilaterally decide what goes live. A human calls this (see
deploy.py at the repo root) after checking what's actually on `main`.
"""

from __future__ import annotations

import hmac
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, jsonify, request

bp = Blueprint("deploy", __name__, url_prefix="/deploy")

REPO_DIR = Path(os.environ.get("DEPLOY_REPO_DIR", str(Path.home() / "laser-kerf-corrector")))
DEPLOY_SECRET = os.environ.get("DEPLOY_SECRET")

PA_USERNAME = os.environ.get("PA_USERNAME", "makertools")
PA_DOMAIN = os.environ.get("PA_DOMAIN", "makertools.pythonanywhere.com")
PA_API_TOKEN = os.environ.get("PYTHONANYWHERE_API_TOKEN")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=60)


def _clear_pycache() -> int:
    cleared = 0
    for d in list(REPO_DIR.rglob("__pycache__")):
        shutil.rmtree(d, ignore_errors=True)
        cleared += 1
    return cleared


def _reload_webapp() -> dict:
    url = f"https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/webapps/{PA_DOMAIN}/reload/"
    req = urllib.request.Request(url, method="POST", headers={"Authorization": f"Token {PA_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode(errors="replace")[:500]}
    except urllib.error.URLError as e:
        return {"ok": False, "error": str(e.reason)}


@bp.route("", methods=["POST"])
def deploy():
    if not DEPLOY_SECRET or not PA_API_TOKEN:
        return jsonify({"error": "Deploy is not configured -- set DEPLOY_SECRET and "
                                  "PYTHONANYWHERE_API_TOKEN (see README)."}), 503

    given = request.headers.get("X-Deploy-Secret", "")
    if not hmac.compare_digest(given, DEPLOY_SECRET):
        return jsonify({"error": "Unauthorized"}), 401

    steps = {}

    pull = _run(["git", "pull"])
    steps["git_pull"] = {"ok": pull.returncode == 0, "stdout": pull.stdout.strip(), "stderr": pull.stderr.strip()}
    if pull.returncode != 0:
        return jsonify({"ok": False, "steps": steps}), 500

    steps["pycache_cleared"] = _clear_pycache()

    commit = _run(["git", "log", "-1", "--format=%h %s"])
    steps["deployed_commit"] = commit.stdout.strip()

    steps["reload"] = _reload_webapp()

    return jsonify({"ok": steps["reload"].get("ok", False), "steps": steps})
