#!/usr/bin/env python
"""Step 2 of the selective joint-correction workflow: apply a manifest saved
by review_joints.py. Only the specific vertices belonging to an accepted
HOLE/SLOT/TAB feature move; everything else in the file -- other elements,
and other parts of a subpath a feature happens to be embedded in -- is
re-emitted exactly as originally drawn, curves included.
"""

import argparse
import json
import sys

from kerfcorrector import cli, joints, svgio


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="input SVG file")
    parser.add_argument("output", help="output SVG file")
    parser.add_argument("--manifest", help="joint manifest from review_joints.py "
                                            "(default: <input>.joints.json)")
    parser.add_argument("--kerf", type=float, required=True,
                         help="total kerf width in mm (the full width the laser removes)")
    args = parser.parse_args(argv)

    manifest_path = args.manifest or (args.input.rsplit(".", 1)[0] + ".joints.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    doc = svgio.load(args.input)
    elements = cli.select_elements(doc, None, include_fill=False)

    stats = joints.apply_manifest(doc, elements, manifest, args.kerf)
    svgio.save(doc, args.output)

    print(f"Manifest entries: {stats.total_in_manifest}")
    print(f"Corrected: {stats.corrected}")
    print(f"Elements touched: {stats.elements_touched} (everything else in the file is untouched)")
    if stats.warnings:
        print(f"Warnings ({len(stats.warnings)}):", file=sys.stderr)
        for w in stats.warnings:
            print(f"  - {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
