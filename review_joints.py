#!/usr/bin/env python
"""Step 1 of the selective joint-correction workflow: auto-detect HOLE/EDGE
features in an SVG, open a browser GUI to verify/adjust them, and save a
manifest for apply_joints.py.
"""

import argparse
import os
import threading
import webbrowser

from werkzeug.serving import make_server

from kerfcorrector import cli, joints, review_app, svgio


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="input SVG file")
    parser.add_argument("--manifest", help="where to save the reviewed feature list "
                                            "(default: <input>.joints.json)")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)

    manifest_path = args.manifest or (os.path.splitext(args.input)[0] + ".joints.json")

    doc = svgio.load(args.input)
    elements = cli.select_elements(doc, None, include_fill=False)
    if not elements:
        print("No cut-line elements found (nothing has fill:none/transparent).")
        return 1

    infos = joints.analyze(doc, elements, tolerance_mm=0.3)
    features = joints.find_features(infos, doc.scale_user_units_per_mm)
    payload = joints.to_payload(features)

    counts = {"hole": 0, "edge": 0}
    for f in features:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    print(f"Found {len(features)} features: {counts['hole']} hole, {counts['edge']} edge.")

    app, saved_event = review_app.build_app(args.input, payload, manifest_path)
    server = make_server("127.0.0.1", args.port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Review GUI running at {url}")
    print("Verify/adjust the features in the browser, then click 'Save review & finish'.")
    webbrowser.open(url)

    try:
        saved_event.wait()
    except KeyboardInterrupt:
        print("\nCancelled -- no manifest written.")
        server.shutdown()
        thread.join()
        return 1

    server.shutdown()
    thread.join()
    print(f"Saved manifest to {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
