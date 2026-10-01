# PLAN — Image Editor module: Liquify with live camera/mesh preview

Status: planned, not implemented. Decisions locked 2026-10-01.

## Goal
A Photoshop-style Liquify tool in the Image Editor that warps a camera's photo.
While painting, the camera background in the 3D Viewport **and** the photo projected
on the mesh update live, so alignment can be checked. Work always happens on a 1K
preview; the original-size result is produced only on **Bake**.

## Decisions
| # | Decision |
|---|---|
| Output | New file next to the photo: `<stem>_lq.png`, assigned as the camera's image. Original file never touched. Warp field saved as sidecar `<stem>_lq.npz` for re-edit. |
| Brushes v1 | Forward Warp, Reconstruct, Smooth, Pucker/Bloat |
| Mesh preview | Yes — projection materials show the preview live |
| Resolution | Preview is always 1024 px on the long side. Source photos < 4K. Bake writes the original size. |

## Benchmark (Blender 5.2.1, numpy 2.3.4, OpenGL, this machine)
| Operation | 1024×768 | 3840×2160 |
|---|---|---|
| `pixels.foreach_set` (whole image) | 0.8 ms | 8.5 ms |
| `pixels.foreach_get` | 0.5 ms | 14 ms |
| `image.update()` | ~0 ms | ~0 ms |
| Bilinear remap, whole image (float32) | 103 ms | 1048 ms |
| Bilinear remap, 200×200 brush window | 4–6 ms | 4–6 ms |
| Upsample field 512×384 → full | 32 ms | 447 ms |
| **Live tick at 1K** (window remap + full write + update) | **4.9 ms** | — |

Takeaways:
- Live preview is cheap **only if we recompute just the area under the brush**. A whole-image
  remap per tick (100 ms) is off-limits; a dirty-rect tick is ~5 ms → plenty of headroom at 30–60 Hz.
- Keep everything float32 — a stray float64 promotion (`float32 - int32`) doubled the remap cost.
- Bake at 4K ≈ 1.5 s compute + file save. Do it in row strips to cap RAM.
- Not measured: viewport redraw when the 1K texture re-uploads for the mesh material
  (expected small; verify in phase 2).

## Data model
- **Source preview** `S` — float32 (h, w, 4), the original downscaled to 1K once at session start.
  Never modified.
- **Warp field** `D` — float32 (h, w, 2) at preview resolution, offsets in UV units
  (resolution independent). Output pixel `p` = `S` sampled at `p + D(p)` (backward map).
- **Output buffer** `O` — float32 (h, w, 4), mirrored into the preview `bpy.types.Image`.
- **Undo stack** — per stroke, the stroke's bounding box of `D` before the stroke (≈ KB–MB each),
  ~50 steps. Blender's global undo does not cover this.

## Brushes (all edit `D` inside the brush bbox, falloff `w` = smooth radial × strength × pressure)
- **Forward Warp**: semi-Lagrangian — `D'(p) = D(p − w·δ) − w·δ` (δ = mouse delta); no tearing.
- **Reconstruct**: `D' = D · (1 − w·k)`.
- **Smooth**: `D' = lerp(D, blur(D), w)` (box blur on the bbox + margin).
- **Pucker/Bloat**: `D' = D ± w·k·(p − c)` (Ctrl/Alt or a toggle flips direction).

Then recompute `O` for the bbox (+ 1 px margin) from `S` and `D`, using full `S` for sampling.

## Session flow
1. In the Image Editor, with a camera photo shown → **Start Liquify**.
2. Reverse lookup: cameras whose `core.cam_image(cam)` is that image (several allowed),
   plus every `ShaderNodeTexImage` (`CamTex_i`) using it.
3. Create `MCP_LQ_<stem>` (1K, same aspect), fill from `S` (+ the existing field if a `<stem>_lq.npz`
   sidecar exists → re-edit). Swap it into: camera BG(s), the CamTex nodes, the Image Editor.
4. Store the original image name on each camera (`cam.data["mcp_lq_orig"]`) so a crash, save or
   reload mid-session can be restored (load_post handler + module unregister both restore).
5. Paint. Each tick: brush → `D` bbox → remap `O` bbox → `foreach_set` + `update()` →
   tag redraw of IMAGE_EDITOR + VIEW_3D areas. Coalesce mouse moves, at most one tick per event batch.
6. **Bake**: read the original full-res pixels, upsample `D` to full size (UV → px),
   remap in strips of ~256 rows, write `<stem>_lq.png` next to the original, save `<stem>_lq.npz`
   (field, original path, preview size), assign via `core.set_cam_image_path`, restore nodes.
   **Cancel**: restore the original everywhere, drop the preview.

Reload All compatibility: `core._find_in_folder` tries the current image's basename first, so a
`_lq.png` in the same folder is kept on reload.

## Module layout
```
modules/image_editor/
  __init__.py   register/unregister; ALL_MODULES entry ("image_editor", icon IMAGE_DATA)
  field.py      D, brushes, undo, upsample, remap, warp_image (bake maths)
                (pure numpy - testable without Blender)
  session.py    start/stop, reverse lookup, swap/restore, crash recovery, sidecar IO
  bake.py       read original pixels, field.warp_image, save + assign
  tool.py       WorkSpaceTool (IMAGE_EDITOR, VIEW mode) + modal operator, brush cursor
                (POST_PIXEL draw handler), mouse -> UV via region.view2d.region_to_view
  operators.py  Start, Bake, Cancel, Reset Field, Undo/Redo
  ui.py         Image Editor sidebar, "MultiCamProject" tab: brush, size, strength,
                pressure toggle, session buttons
```
UI/tools only register for `IMAGE_EDITOR`, so nothing shows up in the 3D Viewport panels.

## Phases
1. ✅ `field.py` + numpy tests (`py tests/test_liquify_field.py`, 20 tests).
   Measured in Blender, 1024×576 preview, one dab (field update + remap):
   r=50 2 ms · r=100 8 ms · r=200 32 ms · r=300 76 ms (Smooth slightly less). Cost grows with r².
   → Phase 3 must coalesce mouse events to one dab per tick and space dabs by radius, so big
   brushes do fewer, larger dabs. If r≥200 feels slow, compute the field change at half
   resolution for large brushes.
2. ✅ Session swap/restore incl. mesh materials (`session.py`, Start/Cancel, Image Editor panel).
   One `photo.user_remap(preview)` swaps every user at once (camera BGs, CamTex/slot nodes,
   Image Editors); the preview's ID property `mcp_lq_orig` points back at the photo and keeps
   it alive. save_pre/save_post keep previews out of .blend files; load_post / add-on start
   recover leftovers; undo_post re-swaps or ends the session.
   Measured on the tribal-warrior scene (Material Preview, 4 cameras, 5 materials, 3168×1344
   photo → 1024×434 preview): Start 150 ms · push + redraw 6.6 ms vs plain redraw 6.4 ms
   (texture re-upload ≈ free) · dab r=100 + push + redraw ≈ 16 ms.
   Not yet exercised for real: Ctrl+Z, saving and reopening a file mid-session (handlers were
   called directly in tests).
3. ✅ Tool + modal + cursor + undo; tablet pressure; `[` / `]` and F-drag for brush size
   (`tool.py`, `operators.py`). Two WorkSpaceTools (View and UV mode). Stroke: dabs every
   ¼ radius, at most 4 per mouse event; a 30 Hz timer repeats non-directional brushes while
   the pen rests. The tool keymap takes Ctrl+Z while a session runs, so Blender's undo cannot
   step back past Start by accident. Tested with real mouse input on the tribal-warrior scene:
   warp, Ctrl+Z, P + `]`, Pucker hold - the mesh follows live. PUCKER_RATE lowered 0.1 → 0.03
   after the test (0.1 collapsed the torso in under a second).
4. ✅ Bake + sidecar + re-edit (`bake.py`). Photos that live only in Blender also get a
   `<photo>_lq_orig.png` so the bake can be re-edited. Tested end to end: bake writes the
   files and switches users; re-edit reloads the field exactly; Reset + Bake gives back the
   original pixels.
5. ✅ Edge cases: several cameras sharing one photo (user_remap), missing file (clear error,
   no session), module toggled off mid-session (cancels), editor showing another image (stroke
   refuses with a message), session ended under a running stroke (stroke stops).

## Camera view (added 2026-10-01)
Liquify button on each camera row → solo that camera → modal `multicamproject.liquify_camera`
(`camera_op.py`) paints in the 3D Viewport. `camview.py` maps region pixels to photo UV: the
camera frame from `view_frame` projected into the region; the photo rect from frame method and
scale; offset X × frame width, offset Y × frame width / photo aspect; rotation clockwise.
Validated by reading the viewport framebuffer in a POST_PIXEL handler with marker photos
(7 configurations, < 0.5 px). Enter bakes, Esc cancels; losing solo for any reason (camera,
camera view, local view, area, image) cancels. Testing note: the computer-use helper keeps
Esc presses from Blender (only the release arrives), so Esc has to be checked by hand.

## Risks / notes
- Preview aspect rounding (1024 × round(1024/aspect)) is ≤ 0.1 % → ≤ 1 px at 1K; bake uses exact size.
- `bpy` image pixels are in the image's stored colour space; bilinear in that space matches
  what Photoshop does for 8-bit images - fine.
- Test isolation: pass the scene explicitly and restore global camera state (see project memory).
