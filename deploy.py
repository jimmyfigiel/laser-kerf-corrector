#!/usr/bin/env python
"""Trigger a deploy of the live site: pulls the latest `main` on
PythonAnywhere and reloads the web app, via the /deploy endpoint in
kerfcorrector/deploy.py. Requires the same secret configured server-side
(the DEPLOY_SECRET environment variable set in the WSGI config file) --
pass it as an argument or set DEPLOY_SECRET in your own shell first.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

DOMAIN = "makertools.pythonanywhere.com"


def _https_context():
    """Prefer certifi's CA bundle over the OS trust store. Windows'
    built-in certificate engine can fail Let's Encrypt's chain validation
    on machines that still have the old, expired `DST Root CA X3`
    cross-sign cached as a trusted root (a well-known issue since that
    root expired in September 2021) -- certifi ships Mozilla's own
    curated bundle and sidesteps that path entirely. Falls back to the
    default (OS) context if certifi isn't installed."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    secret = argv[0] if argv else os.environ.get("DEPLOY_SECRET")
    if not secret:
        print("Usage: python deploy.py <secret>  (or set the DEPLOY_SECRET env var)", file=sys.stderr)
        return 1

    req = urllib.request.Request(
        f"https://{DOMAIN}/deploy", method="POST",
        headers={"X-Deploy-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=60, context=_https_context()) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Deploy request failed: HTTP {e.code}")
        try:
            print(json.dumps(json.loads(e.read()), indent=2))
        except Exception:
            pass
        return 1
    except urllib.error.URLError as e:
        print(f"Could not reach {DOMAIN}: {e.reason}", file=sys.stderr)
        return 1

    print(json.dumps(body, indent=2))
    return 0 if body.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
