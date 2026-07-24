# Joint types and settings — reference for calibration design

This is a handoff doc for whoever is designing the process for figuring out
*what values* the settings below should actually be set to (per material,
per machine). It explains what each joint type physically is, what each
setting controls, and — most importantly — the underlying physics that
makes per-kind settings necessary at all instead of one global kerf number.
Everything here reflects `kerfcorrector/joints.py`'s current, tested
behavior as of this writing (70 passing tests, plus verification against a
real multi-joint test file).

## The two structural kinds (automatic, no settings)

Every cut feature the tool finds gets auto-labeled one of two ways. Neither
has a settings knob — they're always corrected the same way, by the kerf
value alone:

- **Hole** — a fully enclosed, standalone cutout (a closed subpath on its
  own). All 4 of its walls are independently cut, so both its dimensions
  shrink by the *full* kerf value.
- **Edge** — everything else: a plain wall, a panel's own outer boundary,
  or any joint feature the user hasn't specifically reclassified. Corrected
  by the standard per-edge kerf shift with no special treatment.

These two are what the tool auto-detects. The four kinds below are never
auto-detected — a human has to look at the shape and manually reclassify it
in the review GUI, because "is this a load-bearing joint, and which side of
it, and what fit does it need" isn't something detectable from geometry
alone.

## The four joint kinds (manual classification, each with its own settings)

These describe the two-sided vocabulary of an interlocking joint. Which
options a given feature can be reclassified as depends on its own structure
— see "Structural forms" below.

| Kind | What it is | Solid or void | Structural forms it can take |
|---|---|---|---|
| **Mortice** | The socket a tenon plugs into | Void (material removed) | Closed loop only |
| **Tenon** | The tab that plugs into a mortice | Solid | Closed loop (standalone tab) or windowed (attached to a boundary) |
| **Teeth** | A finger/comb joint's individual tabs | Solid | Windowed only (a finger joint is by definition attached to something) |
| **Slot** | A sliding-fit channel, e.g. a dado a panel slides into | Void | Closed loop (standalone cutout) or windowed (a channel open at a boundary's edge) |

### Structural forms, precisely

- **Closed loop**: the feature is a complete, independently-cut boundary —
  either its own separate subpath (a standalone hole/mortice/slot-shaped
  cutout) or a whole standalone tab shape (a free-floating tenon not
  attached to anything). Every one of its edges is an independently cut
  wall.
- **Windowed**: the feature is embedded in a bigger boundary — attached on
  one side (shared with that boundary, not independently cut), only cut on
  the rest. A tenon sticking out from a panel edge, a finger-joint tooth, or
  a notch cut into a panel edge are all windowed.

## Settings

- **`kerf_mm`** — total width the laser beam removes. One global number,
  the only one every job strictly needs. Applies uniformly to every
  feature via the base per-edge correction (independent of kind).
- **`mortice_clearance_mm`**, **`tenon_clearance_mm`**,
  **`teeth_clearance_mm`**, **`slot_clearance_mm`** — one independent value
  per kind, in mm, layered *on top of* the kerf correction. Only applies to
  features explicitly marked as that kind; `hole`/`edge` are never
  affected regardless of these values. Default 0 (no effect beyond pure
  kerf).
- **`chamfer_mm`** — a lead-in bevel clipped off each tip corner of a
  **tenon only** (not the corners shared with the surrounding boundary,
  for an attached tenon — just its own free tip). Deliberately does **not**
  apply to teeth, mortice, or slot. Default 0 (off).

All of these are set once per apply-run and apply uniformly to every
feature marked with the matching kind in that run. They can be saved to /
loaded from a small JSON settings file per material.

## Why per-kind clearance is needed at all (the core physics)

Kerf correction alone gets every *independently-cut* wall back to its own
drawn size after cutting. But a **windowed** feature (attached on one side)
has an axis — the one running along its attachment/protrusion direction —
where the shared/attached end and the cut end move *together* under kerf
correction and cancel out. That axis gets **zero net correction from kerf,
at all**, no matter how precisely kerf is calibrated. Any kerf miscalibration
error on that axis has nothing to compensate it and shows up entirely as
extra (or missing) length.

This is why "just measure kerf more carefully" has a hard ceiling, and why
each joint kind gets its own independent clearance number: a lone tenon, a
repeating finger tooth, a mortice socket, and a sliding slot all typically
need *different* amounts of that extra nudge, and the amount has nothing to
do with the kerf value itself — it depends on the material, the specific
joint, and how loose/tight the builder wants that particular kind of
connection.

## Which direction clearance moves things

This is the part most worth double-checking in any calibration process,
because it's easy to get backwards and a wrong sign doesn't fail loudly —
it just makes the joint tighter when you asked for it to be looser.

- **Tenon / Teeth (solid)**: more clearance → **shrinks** the tab. Less
  material, easier to insert into a fixed-size socket.
- **Mortice / Slot (void)**: more clearance → **enlarges** the opening.
  More material removed, easier to fit a fixed-size tab into it.

Both directions are "make the fit easier" from the user's point of view —
they just require opposite physical moves depending on which side of the
joint you're on. **This direction is guaranteed to be correct and consistent
regardless of a feature's structural form** (closed-loop vs. windowed) —
that consistency was the subject of a real bug found and fixed against an
actual test file (a windowed slot was initially enlarging in the wrong
direction because closed-loop and windowed voids need opposite internal
signs to produce the *same* observable "loosen" behavior; see
`_extra_clearance_sign` in `joints.py` for the full mechanics if needed —
nothing about it needs to leak into a calibration process, since the
user-facing behavior is simple and now verified: **positive clearance
always means an easier fit, for every kind, in every structural form**).

## Full vs. half sensitivity (why the same clearance number moves different amounts on different axes)

Within one feature, its two dimensions aren't always equally sensitive to
kerf or clearance. It depends on how many of that axis's own walls are
independently cut:

- **Closed-loop feature** (standalone mortice, standalone tenon, standalone
  slot cutout): all 4 walls independent → **both** dimensions move by the
  **full** kerf/clearance value (each of the 2 opposing walls on an axis
  contributes half).
- **Windowed feature** (attached tenon/teeth, a slot notch cut into an
  edge): the axis along the attachment/protrusion direction has only
  **one** independently-cut wall (the free tip/cap) → that axis moves by
  only **half** the kerf/clearance value. The perpendicular axis (the
  width, with two independent side walls) still gets the **full** value.

Concretely, for a windowed feature with `kerf_mm=1.0` and
`<kind>_clearance_mm=0.4`: the width axis (2 walls) shifts by the full 0.4mm
clearance on top of the full 1.0mm kerf effect; the length axis (1 wall)
shifts by only half of each — 0.2mm clearance on top of 0.5mm kerf.

This means the *same* clearance number produces a *different* absolute mm
change depending on which axis and which structural form you're looking
at. A calibration process should account for this rather than assuming one
clearance value maps to one consistent mm delta everywhere.

## Chamfer, specifically

- Applies **only** to `tenon`. Not teeth (explicit decision — a finger
  joint's repeating teeth are not chamfered even with a nonzero
  `chamfer_mm`), not mortice/slot (chamfering the *receiving* side isn't
  implemented at all currently — only the protruding tenon tip).
- For a standalone (closed-loop) tenon, all 4 corners get chamfered. For an
  attached (windowed) tenon, only its own two free tip corners get
  chamfered — the corners shared with the surrounding boundary are left
  alone, or the boundary would gap.
- Chamfer is computed from the *already kerf+clearance-corrected* geometry,
  so the bevel lines up with the actual final cut, not the original drawing.

## Practical notes for a calibration process

- **Kerf should be calibrated first**, independent of everything else —
  it's a machine/material constant (cut a test slot, measure it, kerf =
  measured − drawn), not a per-joint-type choice. Every clearance value
  below is layered *on top of* whatever kerf is already set.
- Each clearance value can then be tuned independently, in any order,
  without needing to re-touch kerf — cut a test piece of that *specific*
  joint kind and adjust just its one clearance number until the fit is
  right.
- **Mortice and tenon are a mated pair with redundant control**: making the
  mortice 0.1mm more enlarged achieves the same net looseness as making the
  tenon 0.1mm more shrunk. The tool lets you set both independently, but a
  calibration process will want to decide a convention up front — e.g.
  "always leave `mortice_clearance_mm` at 0 and do all the fit-tuning via
  `tenon_clearance_mm`" — rather than let both drift independently and
  double-compensate.
- **Slot's two mating pieces use the same clearance value**, not a pair of
  independent ones the way mortice/tenon do (both sides of a slot joint are
  classified `slot` and share `slot_clearance_mm`). There's no equivalent
  "which side do we tune" decision to make for slot.
- Classification is entirely manual — there is no way to auto-detect which
  of the four kinds a feature is, only whether it's structurally eligible
  (see the "Structural forms" table above for what the review GUI will and
  won't offer for a given shape). Any calibration process that wants to
  batch-test many joints will need the classifications supplied
  explicitly (e.g. by element/feature id), not inferred.

## Worked example (verified against a real file)

A real test file with a finger-jointed cube (6 panels × 4 teeth each),
one mortice/tenon pair, and one slot pair (two matching notched panels)
was used to verify all of the above. Numbers below are directly measured
(re-analyzed from the corrected output), not hand-derived, with
`kerf_mm=0.15`:

| Feature | Kind | Pre-correction | Kerf-only | + clearance |
|---|---|---|---|---|
| Cube panel tooth | teeth | 3.00 × 10.00mm | 3.00 × 10.15mm | `teeth_clearance_mm=0.05` → 2.98 × 10.10mm (shrinks) |
| Mortice hole | mortice | 3.00 × 10.00mm | 2.85 × 9.85mm | `mortice_clearance_mm=0.1` → 2.95 × 9.95mm (enlarges) |
| Tenon tab | tenon | 3.00 × 10.00mm | 3.00 × 10.15mm | `tenon_clearance_mm=0.1` → 2.95 × 10.05mm (shrinks) |
| Slot notch (windowed) | slot | 3.00 × 12.50mm | 2.85 × 12.50mm | `slot_clearance_mm=0.1` → 2.95 × 12.55mm (enlarges) |

All four moved in their correct, verified direction (teeth/tenon tighter,
mortice/slot looser) relative to their own kerf-only baseline. Note that
the exact mm delta per axis (full vs. half the clearance value) depends on
each specific shape's own wall topology, which can differ from the simple
single-notch/single-tab fixtures used to establish the general full/half
rule above — always confirm against the actual shape rather than assuming
a fixed ratio.
