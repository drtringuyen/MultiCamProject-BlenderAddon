# Seam Fix module - plan, state, handoff (2026-10-23)

Module: `addons/MultiCamProject/modules/seam_fix/` (Infos Modules row: Seam_fix).
Prototype it grew from: `docs/prototype_seamfix_demo.py` (standalone Suzanne demo, Text Editor
Alt+P) - kept for reference, not loaded by the add-on.

## The idea

Two ways to hide a UV seam in a baked albedo:

1. **Fix Seam** (BOBERT_art's method, github.com/Rink37/BOBJECT_engine, SeamFixer.cpp): across a
   seam the surface just inside the target island B is "where source island A would continue".
   B's faces next to the seam get a second UV (`uv_fixseam`) that unfolds them into A's
   margin; a mask blends B toward that read. **Only works when the margin holds real
   continuation paint** (hand painting past the island edges). Baked / scanned textures have
   bake-margin padding (edge colour ~10 px, then black) - so for scans this does little.
2. **Seam-leveling** (photogrammetry texturing, "Let there be color!"): measure both sides of
   every seam, offset each side toward their average, fade the offset into each island. No
   margin needed, uv_normal untouched. **This is the one that matters for scans** (bed: seam
   jump median 21/255, island 21 vs 23 on the nightstand ~26/255).

## What the panel does (3D view sub-panel of MultiCamProject + Image/UV editor tab)

| Row | Button | Does |
|---|---|---|
| 1 | Fix Seam [blend px] [shrink] | overlaps detected (islands overlapping > 4 px at 2048 are left out, faces in `seamfix_overlap`, selected at the end); `uv_new` = uv_normal with short islands shrunk so each has blend+4 px free around it; `uv_fixseam`; `SF_mask_<obj>` (4K, Gaussian of the target-side seam lines, faded out per pixel where the read lands on another island / outside 0-1 / black padding). Always rebuilt from uv_normal. |
| 2 | Seam-leveling [falloff px] [max jump] | `SF_level_<obj>` (2K, 0.5 + offset in sRGB values). Per corner (vertex + UV position) offset = mean - own side, averaged over its seam edges; spread per island by normalised Gaussian convolution. Seams with jump > max jump (default 64/255) run along a real colour edge and are skipped. |
| 3 | Bake [margin] | Cycles Emission bakes onto `uv_new`, Preview as shown: `<image>_seamfix` (same size / colour space / float-ness), `<image>_seamfix_alpha` for an RGBA albedo. One target per material per pass (dummy target for materials without one). Packed. |
| 4 | Original / Mask / Preview / Baked | object prop `seamfix_mode` (0-3) read by an Attribute node in one node group per image (`SF_<mat>_<node>`), inserted after the image node; original links saved in the material's `seamfix` JSON. Mask: white = blend weight, blue = leveling offset (x4), red = overlap faces. |
| 5 | Reset / Apply to Files | Reset: groups out, links back, uv_new / uv_fixseam / images / attribute / props removed (uv_normal never changed). Apply (dialog lists the files): copy originals to `_seamfix_backup/` next to them, write the bakes over them with OpenImageIO keeping channels + bit depth, delete uv_normal, rename uv_new -> uv_normal, delete every other UV map, plain material back. |

Files: `geometry.py` (islands, seams incl. cuts inside one island, overlaps, unfold, shrink,
uv_fixseam), `maps.py` (mask + level images, numpy box-blur Gaussians, OIIO loading),
`shader.py` (groups / install / uninstall / modes), `bake.py`, `files.py` (Reset, Apply),
`operators.py`, `props.py` (Scene `multicamproject_seamfix`), `ui.py`.

Design decisions (from the plan review):
- Renames and file overwrites only in Apply; between, other modules still see the original
  uv_normal (baking / export use "uv_normal" by name).
- Preview is live on the ORIGINAL texture via uv_normal + uv_fixseam; uv_new matters only as
  the bake target.
- Masks are images with a pixel width, not one-face-row corner masks (scan faces are 45-105 px
  at 8K, margins ~10 px).
- Refuses a mesh shared by several objects, or an image another object uses (Apply would
  change it too). The module is not behind the add-on's start gate (post-process on any mesh).
- Temp names `<image stem>_seamfix` (packed, no file) - matsync only recycles ALB_/NOR_ files
  of deleted objects, so they are safe.

## Test state (tests/BlendSeams.blend - git-ignored, copy it by hand)

Bed `ENV_04_KR_BoysBR.10_Bed_middle`: 28,736 faces, 47 islands, 8K ALB (RGBA) + 8K NOR
(16-bit), textures on the team Google Drive.
- Fix Seam: 24 s, 1196/1318 seam edges, 5 overlapping islands left out (35 faces),
  23 islands shrunk (big ones 1-5 %, tiny 1-5-face islands to 0.25), 12 % of reads faded out.
- Seam-leveling: 11 s, 1053 edges leveled, 141 skipped as colour edges.
- Bake: 234 s on CPU (3 passes). One earlier attempt ended with Blender restarting (no crash
  log) while an MCP call blocked on it; run long bakes from a `bpy.app.timers` callback.
- Preview on the nightstand top/front seam: the light/dark step becomes a smooth transition.

## Not done yet

- [ ] Look at Baked mode on the bed (bake exists in the unsaved session only - rerun).
- [ ] Test Apply - ONLY on a copy whose images point to copied files (never the Drive files).
- [ ] Test Reset end to end.
- [ ] Panel visually checked by the user (draw verified headless only).
- [ ] Margin continuation bake (fill uv_new's margins with the neighbour's real paint, so the
      shrink pays off) - not built.
- [ ] Narrow the strip in tight notches instead of fading out (prototype idea).
- [ ] CHANGELOG entry is in place; README not updated.
