#!/usr/bin/env python
"""Local dev entry point for the tool hub (kerf corrector + whatever else
gets added later) -- runs it on 127.0.0.1 and opens a browser tab. For
hosted deployment (e.g. PythonAnywhere), see wsgi.py and README.md instead.
"""

import argparse
import threading
import webbrowser

from kerfcorrector.hub import app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Tools running at {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
