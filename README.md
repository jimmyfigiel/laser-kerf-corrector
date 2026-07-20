# laser-kerf-corrector

Corrects laser-cutting SVG plans for kerf. If a design's slots are drawn at
exactly the material thickness (e.g. 3mm for 3mm plywood), the laser's kerf
(the width of material the beam removes) makes every cut slightly wider than
drawn, so slots end up loose and tabs end up undersized. This tool adjusts
the cut geometry so the *finished* parts come out at the drawn dimensions.

## GUI

```
venv\Scripts\python app.py
```

Opens a browser window at a small hub page listing the available tools
(currently just this one; see "Adding another tool" below for how more get
added later). Open **Laser Kerf Corrector**, upload an SVG (drag-and-drop
or click to choose — nothing is read from or written to disk on the
server's side, it all stays in memory for your session), then review the
auto-detected holes/slots/tabs/boundary and apply. The result comes back
as a download, not a file written next to the input. `Ctrl+C` in the
terminal stops the local server; pass `--port` if 5000 is taken.

This is the same Flask app whether run locally or deployed (see
"Deploying" below) — nothing about it depends on running on your own
machine.

The section below describes the same correction workflow as standalone
command-line scripts (`review_joints.py` + `apply_joints.py`) that operate
on local files directly, useful for automation/scripting, batch
processing, or if you'd rather not use the browser GUI at all.

## How it works

Every corrected feature ends up at *exactly its own drawn dimensions* after
cutting. The tool classifies every edge of every closed cut loop into one
of four kinds — **hole**, **slot**, **tab**, or **boundary** (see the
section below) — and shifts each edge by half the kerf along its own
outward normal: outward for solid material (grows a tab or a panel's
boundary, since cutting shrinks it), inward for removed material (shrinks
a hole or a slot, since cutting enlarges it). Direction is derived from
each subpath's own winding order, not a centroid heuristic, so it's
correct even for concave features like a slot, where "outward" points
back toward the opening rather than deeper into the material.

This is computed purely from geometry (containment, edge adjacency), so it
works whether a part's holes are separate `<path>`/`<polygon>` siblings or
bundled as extra subpaths inside one `<path d="...">` — both conventions
are common exports from CorelDraw/Illustrator/Inkscape.

By default only **unfilled** (`fill:none`/`transparent`) shapes are treated
as cut lines, since laser SVGs conventionally use filled shapes for
engraving/artwork and stroked-only shapes for cuts.

## Setup

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## Finding your laser's kerf

Kerf varies by machine, power/speed settings, and material — measure it
rather than guessing:

1. Cut a simple test shape: a square with a few slots of known width (e.g.
   3.0, 3.1, 3.2mm) cut into it, and matching tabs sized to fit.
2. Measure the actual cut slot width with calipers. If you drew a 3.00mm
   slot and it measures 3.15mm, your kerf is 0.15mm.
3. Alternatively: cut a shape with an outer diameter/width you know exactly
   (e.g. a 50.00mm square), measure the actual piece, and the difference is
   the kerf (the outer piece comes out kerf/2 undersized per edge without
   compensation, so a shortfall of 0.15mm on a 50mm square split evenly
   means ~0.075mm kerf... it's simplest to measure a *slot* width directly,
   since that error is exactly one full kerf, not half).

`apply_joints.py` never modifies the input file, so it's cheap to re-run
with a different `--kerf` value and compare outputs if your first guess
turns out wrong.

## Correcting a file (review_joints.py + apply_joints.py)

Step 1 — auto-detect candidates and review them in a browser:

```
venv\Scripts\python review_joints.py "plan.svg"
```

This looks at every closed cut loop and finds three kinds of feature:

- **Whole small subpath**: a closed subpath whose own bounding box is small
  (under 20mm) is one whole feature — a **hole**
  (orange, material removed) if its nesting depth is odd, a **tab** (blue,
  solid) if even, e.g. a free-standing key/tab shape not attached to
  anything else in the file.
- **Windowed excursion**: for a bigger boundary (like a panel's outer
  silhouette), a short run of edges that locally bulges outward or dents
  inward — bounded on both sides by edges that are themselves parallel to
  each other — is a **tab** (bulges out, adds material) or a **slot**
  (pink, dents in, removes material) embedded in that boundary. This finds
  a feature however it's constructed: two long parallel walls joined by a
  perpendicular cap (common in CorelDraw-style exports), or a simple
  orthogonal step (common in Inkscape-style exports) — both are just "a
  short window bounded by parallel edges" as far as the search cares.
- **Boundary** (dashed green): whatever's left of a big boundary once its
  windowed features are carved out — the plain, joint-free walls of a
  panel's outer silhouette, or of a big hole. Left uncorrected, these
  edges would leave the finished part undersized (or a big hole oversized)
  by the kerf even with every joint on it corrected perfectly, since
  "correct each joint" and "correct the part's own overall size" are
  separate concerns. It's auto-detected and selected by default — a plain
  rectangular panel with no joints at all still comes back as one boundary
  feature, so it still gets corrected.

A browser window opens showing the sheet with the detected features
overlaid and color-coded (scroll to zoom, drag to pan). The sidebar lists
every feature with its detected size and a button to cycle its
classification through `ignored` / `hole` / `slot` / `tab` (a detected
joint's own boundary entry additionally offers `boundary`, but that's not
offered on ordinary hole/slot/tab entries — see below). Click any shape
directly on the canvas to cycle it the same way, including ones never
auto-suggested — clicking always hits the smallest feature under the
pointer, so a small slot nested inside a big boundary polygon stays
individually clickable rather than always selecting the boundary. The
selected shape gets a bright yellow outline, and clicking a shape also
highlights and scrolls to its row in the sidebar; clicking a sidebar row
pans/zooms the canvas to that shape, in both directions. Click empty
canvas space, or press Escape, to clear the selection highlight.

Marking a false-detection `ignored` doesn't just leave its edges untouched
— that would produce a visible step where the rest of the boundary shifts
around it. Instead its edges fold back into that piece's boundary entry
(and get pulled back out if you un-ignore it), so a misidentified tab or
slot ends up corrected as ordinary boundary, exactly as if it had never
been separately detected — shown as a fine dashed green outline (distinct
from the boundary polygon's own coarser dash) so it's clear it's still
being corrected rather than having simply vanished. For the same reason,
`boundary` isn't offered as a manual target on a hole/slot/tab entry: since
it would just draw a second, overlapping boundary-styled shape rather than
actually merging anything, `ignored` is the correct way to say "treat this
as ordinary boundary."

If a real joint wasn't auto-detected at all (its edges just became part of
the surrounding boundary), click **+ Add missed feature**, then click the
joint's two outer corners directly on the canvas. This is most often needed
for a finger-joint tooth positioned right at a corner of a panel, where the
windowed search's core assumption — that the edges immediately before and
after a local excursion are parallel to each other — doesn't hold, since
those two edges are actually the panel's two *different* sides meeting at
that corner. Each click snaps to the nearest actual vertex of whichever
piece you clicked; the run of edges between your two clicks (whichever way
around the loop is shorter) becomes a new feature, classified the same way
auto-detected ones are, and its edges are removed from that piece's
boundary entry so they aren't corrected twice.

**Undo** (button, or Ctrl/Cmd+Z) steps back through classification changes
and added features one at a time, all the way back to the original
auto-detected state — nothing is written to disk until you explicitly
apply, so it's safe to experiment.

When done, click **Save review & finish** — this writes `plan.joints.json`
next to the input file and shuts the server down.

Step 2 — apply the reviewed manifest:

```
venv\Scripts\python apply_joints.py "plan.svg" "plan.joints-corrected.svg" --kerf 0.15
```

Only the specific vertices belonging to an accepted feature move: every one
of its member edges shifts by kerf/2 along its own outward normal (outward
if solid material, inward if material removed). Shared vertices between
adjacent member edges naturally accumulate both edges' shifts, which is
mathematically the same as a mitre-join offset of the whole feature. In
practice this means a dimension bounded by two independent member walls
(e.g. a standalone hole's width, or a slot's width where both side walls
are cut edges) moves by the *full* kerf, while a dimension bounded by only
one member wall — the other side being unrelated boundary, like a slot's
open end or a step-style tab's uncut face — moves by *half* the kerf. This
falls out automatically from the per-edge-shift mechanism; nothing is
hand-coded per hole/slot/tab or per dimension.

Everything else in the file, including *the rest of a much bigger boundary
a feature happens to be embedded in*, is re-emitted from its original
segments — curves are preserved, not flattened, since there's no need to
touch geometry that isn't being corrected. Direction (outward vs. inward)
is derived from each subpath's own winding order, which is correct for
concave features (like a slot) where a naive "away from centroid"
heuristic gives the wrong answer.

`--manifest` overrides the manifest path if you don't want the `<input>.joints.json`
default. Both scripts must see the same file with the same cut-line
selection convention (they both default to "everything with fill:none");
don't hand-edit the SVG between running `review_joints.py` and
`apply_joints.py` or the vertex indices in the manifest will no longer line
up — re-run `review_joints.py` instead.

The only required setting is `--kerf`. Feature-size thresholds are fixed
internally rather than exposed as flags, since they only affect how edges
are *grouped and labeled* for review (and thus what you can individually
mark `ignored`) — not the correction itself. A joint too large to get its
own detected box still gets corrected: it just falls into the surrounding
boundary feature instead, which applies the identical per-edge kerf/2
shift.

## Deploying

The GUI (`kerfcorrector/hub.py` + `kerfcorrector/kerf_tool.py`) is a plain
Flask app with no local-machine dependency — it never touches the server's
filesystem. Uploads are held in memory for the session (keyed by an opaque
token handed to the browser) and results come back as a download, so it's
safe to host somewhere other people can reach.

**PythonAnywhere:**

1. Upload the project (git clone, or upload a zip, from a Bash console)
   and create a virtualenv there: `mkvirtualenv --python=python3.11
   laser-kerf-corrector && pip install -r requirements.txt`.
2. In the **Web** tab, create a new web app, choose "Manual configuration"
   (not one of the framework wizards), and point the virtualenv setting at
   the one you just made.
3. Edit the WSGI configuration file it generates to end with:
   ```python
   import sys
   path = '/home/YOURUSERNAME/laser-kerf-corrector'
   if path not in sys.path:
       sys.path.insert(0, path)
   from wsgi import application
   ```
   (`wsgi.py` at the repo root already does `from kerfcorrector.hub import
   app as application` — PythonAnywhere's convention is to import an
   `application` object, which is why that indirection exists.)
4. Reload the web app from the **Web** tab. It's now live at
   `yourusername.pythonanywhere.com`.

Free-tier PythonAnywhere runs a single worker process, which this app
assumes — the upload/result store is an in-memory dict, not shared across
processes, so multiple workers would randomly 404 on tokens handled by a
different worker. If you ever move to a paid tier with multiple workers,
that store needs to move to something shared (e.g. a database or Redis)
first. Either way, uploads and results don't survive a process restart —
that's expected, not a bug, given nothing is meant to persist here.

**Adding another tool later:** each tool is a Flask Blueprint. Add a new
module next to `kerf_tool.py`, give it a `Blueprint` with its own
`url_prefix`, register it in `hub.py`'s `create_app()` alongside
`kerf_tool.bp`, and add an entry to `hub.py`'s `TOOLS` list so it gets a
card on the landing page. No other file needs to change.

## Limitations

- Only `<path>`, `<polygon>`, `<polyline>`, `<rect>`, `<circle>`, `<ellipse>`,
  and `<line>` elements are considered. `<text>`, `<image>`, `<use>`, and
  nested `<svg>` are ignored.
- CSS selection only understands simple flat class rules
  (`.classname {prop: value; ...}`) inside `<style>` blocks — the same
  convention CorelDraw/Illustrator/Inkscape exports use. Complex selectors
  (combinators, ids, pseudo-classes, `!important`) aren't parsed.
- Open (unclosed) cut paths aren't analyzed as features at all (there's no
  "inside" to offset toward) and are left untouched.
- If the kerf is larger than a feature's own width, correction can push
  opposite walls past each other, producing self-intersecting geometry —
  there's no collapse detection here, so sanity-check the result visually
  for an unreasonably large `--kerf` relative to your smallest feature.
- Elements can carry `transform` attributes (including nested `<g
  transform="...">` ancestors) and are corrected correctly in absolute
  space, but this path is less exercised than the no-transform case — check
  the output visually (or its reported `short_mm`/`long_mm`) on files that
  use transforms.
- The windowed excursion search assumes a local joint sits along an
  otherwise-straight run — specifically, that the edges immediately before
  and after it are parallel to each other. A joint positioned right at a
  corner of a panel (where those two neighboring edges are actually the
  panel's two *different* sides meeting there, not a continuation of the
  same wall) fails that assumption and won't get its own labeled entry —
  in practice this is the single most common reason a real joint goes
  undetected. It's still corrected either way (its edges fall into the
  surrounding boundary feature, same kerf/2-per-edge shift), just without
  individual review; use **+ Add missed feature** to give it its own entry.
  A joint built from an unusually elaborate connecting detail (e.g. several
  chamfer segments, more edges than the search's fixed window looks at) can
  fail to be found for the same underlying reason and has the same fix.
- The review GUI needs a browser; there's no headless/scripted way to author
  a manifest other than hand-writing the JSON (see the format written by
  `review_joints.py` — a list of `{element_index, subpath_index, kind,
  member_edges}` objects, where `kind` is `"hole"`, `"slot"`, `"tab"`, or
  `"boundary"` and `member_edges` are the vertex indices of that feature's
  own edges). An unrecognized `kind` or an empty `member_edges` is skipped
  with a warning rather than guessed.

## Tests

```
venv\Scripts\pip install -r requirements-dev.txt
venv\Scripts\python -m pytest tests/
```
