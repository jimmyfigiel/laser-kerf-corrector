#!/usr/bin/env bash
# Redeploy from GitHub, run directly in a PythonAnywhere Bash console.
#
# No secret needed here, unlike /deploy (kerfcorrector/deploy.py) -- that
# one's gated by DEPLOY_SECRET because it's a public HTTP endpoint anyone
# on the internet can reach. This script only runs inside a console
# session you're already authenticated into as the account owner, and
# the request never leaves PythonAnywhere's own network, so there's
# nothing left to authenticate. Use this when triggering deploy.py from
# your own machine isn't working (e.g. local network/antivirus
# interference) -- it does the same steps either way.
set -e
cd ~/laser-kerf-corrector

echo "==> git pull"
git pull

echo "==> pip install -r requirements.txt"
pip install -r requirements.txt

echo "==> clearing __pycache__"
find . -name '__pycache__' -exec rm -rf {} +
touch kerfcorrector/*.py

if [ -n "$PYTHONANYWHERE_API_TOKEN" ]; then
    echo "==> reloading web app"
    curl -s -X POST -H "Authorization: Token $PYTHONANYWHERE_API_TOKEN" \
        "https://www.pythonanywhere.com/api/v0/user/makertools/webapps/makertools.pythonanywhere.com/reload/"
    echo
    echo "Done -- reloaded."
else
    echo "PYTHONANYWHERE_API_TOKEN isn't set in this shell -- click Reload in the Web tab to finish."
    echo "(add 'export PYTHONANYWHERE_API_TOKEN=...' to ~/.bashrc once to automate this step too --"
    echo " that file lives outside the git repo, so it's a safe place to keep it.)"
fi
