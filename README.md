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
(this kerf corrector and the Tapered Cup Etching Pattern tool -- see its
own section below -- with more addable later; see "Adding another tool"
further down for how). Open **Laser Kerf Corrector**, upload an SVG (drag-and-drop
or click to choose — nothing is read from or written to disk on the
server's side, it all stays in memory for your session), then review the
auto-detected holes/edges and apply. The result comes back
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
cutting. Every edge of every closed cut loop shifts by half the kerf along
its own outward normal: outward for solid material (grows a tab or a
panel's own boundary, since cutting shrinks it), inward for removed
material (shrinks a hole or a notch, since cutting enlarges it). Direction
is derived from each subpath's own winding order, not a centroid
heuristic, so it's correct even for concave features like a notch, where
"outward" points back toward the opening rather than deeper into the
material.

The tool labels every detected feature **hole** or **edge** for review
(see the section below), but that label is purely a review-screen aid —
correction direction comes entirely from the subpath's own nesting depth,
recomputed independently at apply time, not from the label itself.
Relabeling (or even misdetecting) a feature never changes the output
geometry; the label only exists so a hole — the one case worth a careful
look, since a missed or misplaced one is visibly wrong — stands out from
everything else (tab, notch, or a boundary's own plain wall), which are
all handled identically regardless of which one they are.

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

This looks at every closed cut loop and runs three detection passes, then
labels the result **hole** or **edge** for review:

- **Whole small subpath**: a closed subpath whose own true size (its
  fitted rectangle's long side — not the axis-aligned bounding box, which
  overstates a rotated shape's size by up to sqrt(2)) is under 20mm is one
  whole feature — a **hole** (orange, material removed) if its nesting
  depth is odd, an **edge** (blue, solid) if even, e.g. a free-standing
  key/tab shape not attached to anything else in the file.
- **Windowed excursion**: for a bigger boundary (like a panel's outer
  silhouette), a short run of edges that locally bulges outward or dents
  inward — bounded on both sides by edges that are themselves parallel to
  each other — is an **edge** feature embedded in that boundary, whether it
  bulges out (a tab, adds material) or dents in (a notch, removes
  material). This finds a feature however it's constructed: two long
  parallel walls joined by a perpendicular cap (common in CorelDraw-style
  exports), or a simple orthogonal step (common in Inkscape-style exports)
  — both are just "a short window bounded by parallel edges" as far as the
  search cares.
- **Leftover container**: whatever's left of a big boundary once its
  windowed features are carved out — the plain, joint-free walls of a
  panel's outer silhouette, or of a big hole. Left uncorrected, these
  edges would leave the finished part undersized (or a big hole oversized)
  by the kerf even with every joint on it corrected perfectly, since
  "correct each joint" and "correct the part's own overall size" are
  separate concerns. It's also labeled **edge**, auto-detected and
  selected by default — a plain rectangular panel with no joints at all
  still comes back as one container feature, so it still gets corrected.

Only the first pass can produce a **hole** — everything else, including
the tab/notch distinction and the leftover container, is folded into the
single **edge** label, since all of it gets the identical kerf/2-per-edge
offset regardless of the exact shape. Hole is worth a careful look in
review because a missed or misplaced one is visibly wrong; the rest isn't,
so there's nothing to gain from splitting it further.

A browser window opens showing the sheet with the detected features
overlaid and color-coded (scroll to zoom, drag to pan). The sidebar lists
every feature with its detected size and a button to cycle its
classification through `ignored` / `hole` / `edge`. Click any shape
directly on the canvas to cycle it the same way, including ones never
auto-suggested — clicking always hits the smallest feature under the
pointer, so a small notch nested inside a big container polygon stays
individually clickable rather than always selecting the container. The
selected shape gets a bright yellow outline, and clicking a shape also
highlights and scrolls to its row in the sidebar; clicking a sidebar row
pans/zooms the canvas to that shape, in both directions. Click empty
canvas space, or press Escape, to clear the selection highlight.

Marking a false-detection `ignored` doesn't just leave its edges untouched
— that would produce a visible step where the rest of the boundary shifts
around it. Instead its edges fold back into that piece's own leftover
container entry (and get pulled back out if you un-ignore it), so an
ignored tab or notch ends up corrected as an ordinary edge, exactly as if
it had never been separately detected — shown as a fine dashed blue
outline (distinct from a real edge polygon's solid fill) so it's clear
it's still being corrected rather than having simply vanished.

If a real joint wasn't auto-detected at all (its edges just became part of
the surrounding container), click **+ Add missed feature**, then click the
joint's two outer corners directly on the canvas. This is most often needed
for a finger-joint tooth positioned right at a corner of a panel, where the
windowed search's core assumption — that the edges immediately before and
after a local excursion are parallel to each other — doesn't hold, since
those two edges are actually the panel's two *different* sides meeting at
that corner. Each click snaps to the nearest actual vertex of whichever
piece you clicked; the run of edges between your two clicks (whichever way
around the loop is shorter) becomes a new **edge** feature, and its edges
are removed from that piece's container entry so they aren't corrected
twice.

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
(e.g. a standalone hole's width, or a notch's width where both side walls
are cut edges) moves by the *full* kerf, while a dimension bounded by only
one member wall — the other side being unrelated boundary, like a notch's
open end or a step-style tab's uncut face — moves by *half* the kerf. This
falls out automatically from the per-edge-shift mechanism; nothing is
hand-coded per kind or per dimension.

Everything else in the file, including *the rest of a much bigger boundary
a feature happens to be embedded in*, is re-emitted from its original
segments — curves are preserved, not flattened, since there's no need to
touch geometry that isn't being corrected. Direction (outward vs. inward)
is derived from each subpath's own winding order, which is correct for
concave features (like a notch) where a naive "away from centroid"
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
leftover container instead, which applies the identical per-edge kerf/2
shift.

## Deploying

The GUI (`kerfcorrector/hub.py` + `kerfcorrector/kerf_tool.py`) is a plain
Flask app with no local-machine dependency — it never touches the server's
filesystem. Uploads are held in memory for the session (keyed by an opaque
token handed to the browser) and results come back as a download, so it's
safe to host somewhere other people can reach.

**PythonAnywhere:**

1. From a Bash console on PythonAnywhere: `git clone
   https://github.com/jimmyfigiel/laser-kerf-corrector.git`, then create a
   virtualenv and install into it: `mkvirtualenv --python=python3.11
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
5. Set up on-demand redeploys (see **Redeploying** below) so future changes
   don't need you back in the PythonAnywhere UI at all.

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

**Redeploying:** `kerfcorrector/deploy.py` is a Blueprint exposing a single
`POST /deploy` route that does the whole redeploy sequence server-side --
`git pull`, clear `__pycache__`, then reload the web app via
PythonAnywhere's own API (not the web UI; driving that via browser
automation proved unreliable -- the reload button can report success
without the worker actually restarting). Trigger it from your own machine
with the `deploy.py` script at the repo root: `python deploy.py <secret>`
(or set the `DEPLOY_SECRET` environment variable and omit the argument).
It prints a JSON report of each step and exits non-zero on failure.

To set it up, two environment variables need to be set in the WSGI config
file (same place as `FEEDBACK_ADMIN_PASS`, right before `from wsgi import
application`):
```python
os.environ['DEPLOY_SECRET'] = 'choose-a-real-secret-here'
os.environ['PYTHONANYWHERE_API_TOKEN'] = 'your-pythonanywhere-api-token'
```
Get the API token from **Account → API Token** on PythonAnywhere (generate
one if you haven't already). Without both variables set, `/deploy` refuses
to run (503) rather than silently no-op or fall back to something
unauthenticated. The endpoint itself is unauthenticated to the outside
world except for `DEPLOY_SECRET` -- treat it like a password.

**Working on more than one tool at once (e.g. two Claude chats in the same
checkout):** editing in parallel is fine, since each tool lives in its own
module, and sessions can commit/push to `main` independently. What changed
with the `/deploy` endpoint above is *when* code actually goes live: pushing
to `main` no longer ships anything by itself, so two sessions racing to
finish and deploy at the same moment isn't a real problem anymore -- only
running `python deploy.py` puts whatever's currently on `main` into
production, and that's a deliberate, human-triggered action, not something
either session does as a side effect of finishing its own work.

## Feedback

`kerfcorrector/feedback.py` is a small Blueprint (not listed as a tool card
on the landing page, but reachable directly) for collecting bug reports and
enhancement requests from users:

- `/feedback/` — a public submission form. Every tool's topbar links here
  (`?tool=<name>` prefills which tool it's about); the message, an optional
  contact address, and the page the user came from (`document.referrer`)
  are all captured.
- `/feedback/api/submit` — the form posts here; each submission is appended
  as one line of JSON to a flat file living **outside** the git-tracked
  repo, in the deploying user's home directory (`~/laser-kerf-corrector-data/feedback.jsonl`
  by default, overridable via the `FEEDBACK_DATA_DIR` environment variable).
  Outside the repo means `git pull` never touches it and it survives every
  deploy/reload, unlike the tools' own in-memory upload store.
- `/feedback/admin` — lists submissions newest-first. Protected by HTTP
  Basic Auth: set the `FEEDBACK_ADMIN_PASS` environment variable (and
  optionally `FEEDBACK_ADMIN_USER`, default `admin`) before the app starts,
  or the admin page refuses to serve (503) rather than fall back to an
  unprotected or default-password state. On PythonAnywhere, set it in the
  WSGI configuration file, right before the `from wsgi import application`
  line:
  ```python
  import os
  os.environ['FEEDBACK_ADMIN_PASS'] = 'choose-a-real-password-here'
  ```
  then reload the web app. For local testing, set it in your shell before
  running `app.py`.

User-submitted content is HTML-escaped before being rendered on the admin
page, so a submission can't inject script into your own browser session
when you go read it. There's no rate limiting or spam filtering on the
submission endpoint — fine at hobby-project traffic, but worth knowing if
this ever gets linked somewhere with real volume.

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
  surrounding leftover container, same kerf/2-per-edge shift), just without
  individual review; use **+ Add missed feature** to give it its own entry.
  A joint built from an unusually elaborate connecting detail (e.g. several
  chamfer segments, more edges than the search's fixed window looks at) can
  fail to be found for the same underlying reason and has the same fix.
- The review GUI needs a browser; there's no headless/scripted way to author
  a manifest other than hand-writing the JSON (see the format written by
  `review_joints.py` — a list of `{element_index, subpath_index, kind,
  member_edges}` objects, where `kind` is `"hole"` or `"edge"` and
  `member_edges` are the vertex indices of that feature's own edges). An
  unrecognized `kind` or an empty `member_edges` is skipped with a warning
  rather than guessed.

## Tapered Cup Etching Pattern

`kerfcorrector/cup_etch.py` (the math) + `cup_etch_tool.py` (the Blueprint,
mounted at `/cup-etcher/`) turn a photo or logo into an etching pattern for
the **front-facing panel** of a tapered cup or glass, meant to be lasered
with a rotary attachment (the piece spins in place while the laser only
moves along its length).

**The problem it solves:** a rotary attachment can only do one thing to
"wrap" an image around a piece -- rotate it a fixed angle for a fixed
image column, the same at every row. That's a *linear* column-to-angle
relationship. But viewed straight on, a curved surface doesn't foreshorten
linearly: it foreshortens hardest right at its own silhouette (the visible
edges of the piece) and barely at all at its front-center, where the
surface is nearly tangent to your line of sight. Feed the rotary a plain
image and the result looks pinched at the edges once you look at the
finished piece head-on -- exactly like a flat photo taped around a can
looks warped near the can's own left/right edge. This tool pre-warps the
source image (an arcsine curve, not a simple linear stretch) so that once
the rotary's own linear mapping and the surface's own foreshortening are
both applied, the two cancel out and the finished etching reads as the
original, undistorted rectangle.

The taper (top diameter vs. bottom diameter) doesn't actually change that
horizontal correction -- it's driven purely by how much of the
circumference the design covers (the **wrap angle**), not by the cup's
absolute size. What the taper *does* determine is the output's physical
size: the diameter used to convert the rotary's rotation into a real-world
width is the diameter at the *mid-height* of the design (exactly the
average of the top and bottom diameters, since the taper is linear) --
that's the number to enter into your rotary attachment's own calibration
(LightBurn's "Rotary Setup" or equivalent), and the tool reports it
alongside the generated pattern so the two stay in sync. Get that number
wrong (or use a different one than what you calibrated the rotary with)
and the pattern will be the right shape but the wrong physical size.

Because this corrects a *front-view* effect, it's only defined for a
front-facing panel, not a full wrap-around design -- there's no single
"front" once a design covers the entire circumference. The wrap angle is
capped at 175° for that reason (a value approaching 180° would need
near-infinite stretching right at the edge, where the surface is exactly
edge-on to the viewer).

**Using it:** the four cup-geometry inputs are chosen to be things you can
measure directly with a soft tape measure, no math required -- wrap it
around the rim at the bottom and top of the design area for the two
**circumferences**, and lay it flat along the tapered side from the bottom
rim straight up to the top rim for the **side length**. That last one is
deliberately the slant distance along the surface, not the vertical height
between the rims -- the vertical height isn't something you can measure
directly without already knowing the taper, whereas the side length is
just a straight tape measurement. From those three (which fully describe
the frustum) plus a **design width** (how wide the finished etching should
look, viewed head-on), the tool derives the wrap angle and the mid-height
(rotary calibration) diameter, plus how much axial height is *available*
along the cup's side -- there's a live-updating readout of all three as you
type, before you even upload an image. Also pick a resolution in DPI
(converted to the pixels/mm `cup_etch.py`'s own math uses internally) and
whether to dither. Dithering runs a Floyd-Steinberg error diffusion
down to pure black/white, which is what makes a continuous-tone photo
still show shading once etched (a laser can only mark or not mark a given
spot) -- leave it off for a logo or line art that's already high-contrast.

The uploaded image itself is never cropped or stretched: it's scaled to
the design width while keeping its own proportions, and the design's
actual height is whatever that scaling works out to (a wide image ends up
short, a tall one ends up tall) -- not forced to fill some independently-
computed height. If that height would exceed what's actually available
along the cup's side (the side-length-derived figure above), generating
the pattern fails with a clear error rather than silently cropping the
image to fit; the fix is a narrower design width or a differently-shaped
source image. The result comes back as a PNG (alpha channel preserved, so
a transparent background stays transparent/unetched) sized to the exact
physical dimensions reported alongside it.

## Tests

```
venv\Scripts\pip install -r requirements-dev.txt
venv\Scripts\python -m pytest tests/
```
