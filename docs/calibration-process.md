# Calibration process — figuring out the six settings for a material/machine

This is the process, not just the reference. It assumes you've read
`joint-types-and-settings.md` first — this doc doesn't re-derive the physics,
it uses it. Six settings, in the order you actually calibrate them:
`kerf_mm` → `tenon_clearance_mm` → `teeth_clearance_mm` → `slot_clearance_mm`
→ `chamfer_mm`, with `mortice_clearance_mm` fixed at 0 throughout (see
"Convention: mortice stays fixed" below). End state is a saved settings JSON,
same schema the tool's Save/Load Settings feature already reads.

## Why two phases, not one

Every clearance ladder below needs `kerf_mm` baked into its own geometry
before it's generated — a tenon test piece is drawn at
`nominal + kerf + clearance`, not just `nominal + clearance`, so if kerf is
wrong the whole ladder is testing the wrong numbers even before clearance
enters the picture. That makes a single one-shot "cut everything at once"
file impossible: kerf has to be measured from a real cut before anything
downstream can be generated correctly. So:

- **Phase 1** — one small cut, alone, to get `kerf_mm`.
- **Phase 2** — one combined cut, generated *using* that now-known kerf,
  exercising `tenon_clearance_mm`, `teeth_clearance_mm`, and
  `slot_clearance_mm` all at once on a single sheet.

Two laser runs total, not four or five. `chamfer_mm` is a separate,
optional, qualitative step (see below) — it doesn't need its own dedicated
phase.

## Phase 1 — `kerf_mm`

Already built and verified (Kerf Finder's "Find your basic kerf" step):
download a plain square at a known nominal size (25mm by default), cut it
at the exact machine settings/material you intend to use for real work,
measure the actual piece with calipers, `kerf_mm = nominal − measured`
(a solid square shrinks by the full kerf across each dimension, so this is
exact, not an approximation). No changes needed here — this is the
prerequisite everything else depends on.

## Convention: mortice stays fixed, tenon does the tuning

`joint-types-and-settings.md` flags this explicitly: mortice and tenon are
a mated pair with **redundant control** — enlarging the mortice by 0.1mm
and shrinking the tenon by 0.1mm produce the identical net fit. Left
undecided, a calibration process would let both drift independently and
double-compensate, with no way to tell afterward which side is "really"
carrying the adjustment.

**Convention: leave `mortice_clearance_mm` at 0 always. Do all
mortice/tenon fit-tuning through `tenon_clearance_mm`.**

This is a coin flip physics-wise — the doc is explicit that either
direction works identically. The reason to pick tenon specifically is
practical, not physical: it's the side this process already has a proven,
tested ladder design for (one fixed reference void, a swept solid side) —
reusing that pattern for tenon is free; building and separately verifying
a mirror-image "sweep the void, fix the solid" variant for mortice would
just be duplicate work for a mathematically identical outcome. If a
different convention is ever preferred, the same ladder mechanics apply
with the fixed/swept roles reversed — nothing about the *process* changes,
only which JSON field ends up nonzero.

One consequence: `mortice_clearance_mm` never needs its own calibration
cut at all. It's just always 0 in the saved profile.

## Phase 2 — the combined clearance sheet

One SVG, generated after kerf is known, containing three independent
ladders side by side. Each ladder follows the same shape: one **fixed
reference piece** (corrected by kerf alone, clearance = 0) plus a **swept
series** of pieces at increasing clearance, all labeled with their own
clearance value directly on the sheet. You're not measuring and computing
anything here, unlike kerf — you're physically test-fitting each rung and
reading off whichever label already matches the fit you want. That's a
real methodological difference from Phase 1 worth being explicit about:
Phase 1 is *measure → compute*; Phase 2 is *try → select*.

### Tenon ladder

- Fixed: one mortice-shaped hole, size `nominal`, drawn at
  `nominal − kerf` (kerf-only correction, `mortice_clearance_mm=0`).
- Swept: N free-standing tenon pieces, drawn at
  `nominal + kerf − tenon_clearance_mm[i]` for an increasing sequence of
  clearance values starting at 0 (0 = tightest, the pure-kerf fit).
- This is exactly the existing tab-into-hole ladder the tool already
  generates — only the kind label changes (`tab_hole` → `mortice`/`tenon`
  in the manifest, `tab_hole_clearance_mm` → `tenon_clearance_mm` in the
  `apply_manifest` call). No new geometry design needed.
- Procedure: cut once, try each tenon piece in the one hole, note which
  clearance value gave the fit you want → that's `tenon_clearance_mm`.

### Teeth ladder

- Fixed: one notch (a windowed void cut into a small carrier's edge),
  size `nominal`, kind **`edge`** — not `slot` — so it's corrected by kerf
  alone. This is a deliberate isolation choice: a real finger joint's
  mating gap doesn't have to be reclassified `slot` at all in normal use
  (it's fine as a plain edge), and marking it `edge` here means this test
  measures `teeth_clearance_mm` alone, with nothing else in the loop.
- Swept: N free-standing teeth pieces (a tab protruding from a small
  carrier), drawn using the real correction engine at increasing
  `teeth_clearance_mm[i]` — this is the asymmetric one (width axis gets
  the full kerf/clearance delta, the protrusion-length axis only half),
  which is exactly what the existing finger-joint ladder already builds by
  running an isolated single-shape document through
  `joints.analyze`/`joints.apply_manifest` directly rather than
  reimplementing the shift math. Only the kind string changes
  (`tab_finger` → `teeth`, `tab_finger_clearance_mm` → `teeth_clearance_mm`).
- Procedure: same as tenon — try each tooth in the one notch, note the
  clearance value that fits.

### Slot ladder — the one genuinely new piece

Slot doesn't have a fixed/swept asymmetry the way tenon/teeth do. Per
`joint-types-and-settings.md`, both mating pieces of a slot joint are
classified `slot` and share **one** clearance value — this reads as a
cross-lap/halving joint: two panels, each with a notch removed from an
opposing edge, that interlock where the notches overlap. Both notches are
voids, both move the *same* direction (enlarge) with more clearance, so
there's no redundant-control problem here the way there is for
mortice/tenon — nothing to "fix" on one side, because both sides are
supposed to move together.

That means the ladder has to sweep **pairs**, not a fixed-plus-swept
series:

- For each candidate `slot_clearance_mm[i]` in the sequence, generate
  **two** notch pieces (call them slot-A and slot-B, on separate small
  carrier panels, notches on opposing-facing edges so they can be
  overlapped/interlocked), both built with kind `slot` and the *same*
  `slot_clearance_mm[i]` — mirroring how both sides really do get
  identical treatment in normal use.
- Lay out N such pairs on the sheet (one pair per clearance value), each
  pair clearly labeled with its shared clearance number.
- Procedure: cut once, interlock each pair in turn (not against a shared
  fixed reference — there isn't one), note which pair's fit you want →
  that's `slot_clearance_mm`.
- Geometry-wise this reuses the exact same isolated-single-shape +
  `apply_manifest` technique the teeth ladder uses (kind=`slot` instead of
  `teeth`, both member pieces corrected independently but with the same
  clearance argument), it's just generated twice per rung instead of once,
  with no shared fixed piece.

### Layout

All three ladders fit on one sheet, stacked or arranged left-to-right,
same visual convention as the existing ladders (outer sheet boundary,
each piece a separate closed path, labels engraved directly below what
they belong to). One cut, one measurement/test-fitting session.

## Chamfer — qualitative, not measured

`chamfer_mm` only affects tenon, and only affects how easily it *starts*
sliding in — not the steady-state tightness `tenon_clearance_mm` already
controls. There's no dimension to measure here, so this isn't a "cut,
measure, compute" step at all:

- Optional: once `tenon_clearance_mm` is known, generate 3–4 tenon
  samples at that fixed clearance with increasing chamfer values (e.g. 0,
  0.3, 0.6mm), all correctly kerf+clearance-corrected, try inserting each,
  and pick whichever lead-in feels right. Purely a preference call.
- Fine to just leave at 0 (off) if lead-in ease isn't a concern for the
  material/joint in question.

## Assembling and saving the profile

Six values, same JSON schema the tool already saves/loads:

```json
{
  "type": "kerf-corrector-settings",
  "kerf_mm": 0.16,
  "mortice_clearance_mm": 0.0,
  "tenon_clearance_mm": 0.10,
  "teeth_clearance_mm": 0.05,
  "slot_clearance_mm": 0.10,
  "chamfer_mm": 0.3
}
```

One file per material (e.g. `3mm-birch-ply.json`), same convention the
tool's Save Settings button already establishes.

## Verification cut + iteration loop

The ladders above test each joint kind in isolation. A real part combines
several joints at once, which can surface interaction effects a lone
ladder rung won't (accumulated tolerance across many joints in one piece,
material behaving slightly differently at a different cut path length,
etc.). Before trusting a saved profile for real work:

1. Cut one small real assembly that uses at least one of each calibrated
   joint kind (a small box with a finger-jointed corner, a mortice/tenon
   foot, a sliding-lid slot — whatever's representative of the actual
   project) using the saved settings.
2. If everything fits as intended, done — save and move on.
3. If exactly one joint kind feels off, nudge *only* that one clearance
   value by about ±0.02–0.05mm, re-cut a single ladder-of-one for that
   kind alone (not the whole Phase 2 sheet) to confirm, then update and
   re-save the profile. This is the "iterative cut, measure, adjust" loop
   — but it's a targeted, single-variable nudge against an already-good
   baseline, not a restart of the whole process.

## Implementation notes (not yet built)

Phase 1 is already live (Kerf Finder's square generator). Phase 2 needs:

- Renaming the existing tab-into-hole ladder's kind strings/parameter
  names to `mortice`/`tenon`/`tenon_clearance_mm` (mechanically identical
  otherwise).
- Renaming the existing finger-joint ladder's kind strings/parameter names
  to `edge`/`teeth`/`teeth_clearance_mm` (mechanically identical
  otherwise).
- A new slot-pair generator: same isolated single-shape +
  `apply_manifest` technique, called twice per clearance rung (kind=
  `slot`) instead of once, with no fixed/swept asymmetry.
- Combining all three into one Phase 2 sheet/download, and updating the
  settings-profile JSON assembly to the new six-field schema
  (`mortice_clearance_mm` always 0, `slot_clearance_mm` added).

This is a design doc, not a changelog — none of the above is implemented
yet. Flagging it as the natural next step once this process itself is
reviewed.

## Checklist

1. Cut the kerf square alone. Measure. Compute `kerf_mm`.
2. Generate the Phase 2 sheet using that kerf value. Cut it once.
3. Try each tenon in the fixed mortice hole → `tenon_clearance_mm`.
4. Try each tooth in the fixed notch → `teeth_clearance_mm`.
5. Try each slot pair together → `slot_clearance_mm`.
6. `mortice_clearance_mm` stays 0. `chamfer_mm` stays 0 unless you did the
   optional tactile chamfer check.
7. Save all six as `<material>.json`.
8. Cut one real small assembly with the saved profile. If one joint kind
   is off, nudge that single value, re-verify with a single-piece re-cut,
   re-save.
