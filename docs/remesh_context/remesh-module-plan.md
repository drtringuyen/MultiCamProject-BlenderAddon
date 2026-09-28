---
name: remesh-module-plan
description: "Remesh module v3 (Remesh copy + PolyCut tool + Set Faces) - approved plan and decisions, implemented 2026-09-28 (cloud session)"
metadata:
  node_type: memory
  type: project
  originSessionId: 3bc1398f-0e9f-472b-8752-a9e56ae46c98
  modified: 2026-09-28T10:30:04.520Z
---

Status on 2026-09-28: **implemented** in a cloud session on branch `claude/confident-faraday-9nkzsj` (modules/remesh: workflow.py, marks.py, tool.py, operators.py, ui.py; baking source pointer). Logic tested headless in Blender 5.0; keys, dialog and overlay still need a check in 5.2. Full plan: `C:\Users\Reynard\.claude\plans\mellow-skipping-fox.md`.

**What is in the repo now** (commit 6819406, `modules/remesh/`):
- Poly Cut modal: click to add points, Enter or double-click closes the shape, Esc cancels.
  - An Exact boolean is applied through a temporary `MCP-PolyCut` GN modifier. Cutter faces are dropped and the inside gets a new `.sculpt_face_set`.
  - The border is exact to about 1e-6.
- A `face_set` INT attribute mirrors the face sets for GN. It is synced after each cut and when leaving Sculpt Mode.
- The GN-Remesh modifier has the inputs Face Set, Isolate and Invert.
- v3 replaces all of this, but keeps the boolean cut code (`cutter.py`, `gn_remesh.build_cut`).

**v3 spec, with the user's decisions:**
- **One "Remesh" button**, panel `bl_order = 1` (below Project from Sides; the other panels shift down by 1).
  - Makes a full copy (`obj.copy()` + `data.copy()`, never linked data).
  - The original gets a plain rename to `<name>_original` (not `fixes.rename_object`, so MCP_/MAT_/ALB_/NOR_ stay with the copy). It is moved to an "Original Mesh" collection, unlinked from EXPORT, and all its GN modifiers are removed.
  - The copy is in EXPORT and runs camera_project setup/refresh.
  - The copy gets a per-object `multicamproject_bake.source` pointer to the original, and `normal/mesh_bake.py` prefers it over the scene-wide `hp_object`.
- **Copy's modifier stack:**
  1. GN-Remesh (an empty group; the user builds it)
  2. Decimate Overall (Collapse 0.5)
  3. Decimate Selective (Collapse 0.5, `vg_HighRes`, **not inverted** - the user chose "as written" although weight 1 = decimate more)
  4. GN-CameraProject
  5. GN-Final
- **Then Sculpt Mode with a PolyCut WorkSpaceTool** (also in the Edit Mode toolbar):
  - **L** picks the face set under the mouse (GPU overlay highlight, BVH ray-cast) and opens Set Faces.
  - **Ctrl+LMB** starts a PolyCut (that click is the first point); Enter or double-click cuts, then opens Set Faces on the new face set.
- **Set Faces dialog** in Sculpt and Edit Mode:
  - Dropdown: High Density → `vg_HighRes` = 1; Delete Geo → face BOOL `remesh_delete`; To Separate → face BOOL `remesh_detach` (marked only, GN handles it); Clear.
  - Checkbox "Mark boundary as seam", default on.
  - Previews on `check()`. Edit Mode writes the real data (its undo records it). Sculpt Mode shows an overlay colour and writes once on Enter via Object Mode. Esc restores.
  - Edit Mode gets no new keys: Set Faces comes from the right-click Face menu or the panel.

**Why:** Sculpt undo doesn't record vertex groups or seams, and Sculpt Mode doesn't draw topology modifiers, so a real F9 redo panel breaks there. Edit Mode's L and Ctrl+LMB are Blender defaults.

**How to apply:**
- Read the plan file before implementing.
- Test on copies. Test keys with real key presses (see [[blender-testing-gotchas]]).
- Decimate does not respect seams: warn the user or protect the seams.
- Related: [[baking-export-spec]], [[gn-layout-is-user-owned]], [[work-on-main]].
