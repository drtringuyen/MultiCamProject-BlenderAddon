# Remesh module v3: review of the proposed workflow

**Status (2026-09-28): implemented** - see CHANGELOG "Remesh module v3". Still to verify in
Blender 5.2 with real key presses: the tool keys, the dialog preview / Esc, the overlay.

## Context
This replaces the current Remesh module (Poly Cut button + GN-Remesh face set modifier) with a full "remesh copy" workflow:
- One **Remesh** button duplicates the scan into a working copy, sets up its modifier stack, and enters Sculpt Mode with a **PolyCut tool**.
- The tool marks face sets as High Density / Delete / Separate, which drive the Decimate modifiers and the user's own GN-Remesh.
- The original becomes the high-poly bake source.

This document reviews whether each step works as proposed, and what conflicts, bugs and performance problems to expect.

Facts from the code (see `baking/common.py`, `export/fixes.py`, `camera_project/core.py`, `baking/normal/mesh_bake.py`):
- The EXPORT collection is found by name `"EXPORT"`. Objects are linked into it, not moved.
- `fixes.rename_object` renames `MCP_`/`MAT_`/`ALB_`/`NOR_` and the PNG files together with the object.
- `MCP_<obj.name>` is looked up by the object's name.
- The Normal "Bake from mesh" source is **scene-wide** (`bake_settings.hp_object`), not per object. Albedo bakes the object onto itself (evaluated, GN-CameraProject on).
- GN-Final passes vertex groups and `face_set` through untouched.
- In Sculpt Mode, **L is free**. Ctrl+LMB is the brush's invert stroke, but a tool's own keymap takes priority while the tool is active.

## Step 1: Remesh button (duplicate)

| Proposed | Works? | Notes |
|---|---|---|
| Duplicate + "link" | Needs a **full copy** (`obj.copy()` + `data.copy()`) | A linked duplicate shares the mesh, so a cut would also cut the original. "Link" should mean the relationship pointer, not shared data. |
| Rename the original `<name>_original`, move it to "Original Mesh" | Yes | Rename with a plain `obj.name = ...`, **not** `rename_object`. That way `MCP_`/`MAT_`/`ALB_`/`NOR_` keep their names and go to the copy, which takes over the original name. The original must be **unlinked from EXPORT**: `_original` breaks the `ENV_…` naming check. Collection name: "Original Mesh" (the typo "oringinal" is fixed). |
| Remove all GN modifiers from the original | Yes | This is safe: the albedo bakes on the copy itself, and the original is only the high-poly source for Normal "Bake from mesh". |
| Copy registers the original for baking | Needs a code change | `hp_object` is one scene setting. Add a per-object `multicamproject_bake.source` pointer, and make `mesh_bake` prefer it over `hp_object`. |
| Copy goes into EXPORT, "check all passes from Setup" | Yes | Run `camera_project.core.setup/refresh` on the copy. It keeps GN-CameraProject, the material and the drivers. It will show **Outdated** (the topology changed) until it is baked again. |
| Panel order: 2nd, below Project from Sides | Yes | Remesh `bl_order = 1`; Camera Project, Baking and Export shift by 1. |

## Step 2: Modifier stack

Order: GN-Remesh (yours) → Decimate Overall (Collapse 0.5) → Decimate Selective (Collapse 0.5, `vg_HighRes`) → GN-CameraProject → GN-Final.

**Problems:**
1. **Weight semantics:** tested in 5.2, in Collapse mode weight 1 = collapse allowed and weight 0 = kept. **Decided: as written.** Selective therefore decimates `vg_HighRes` further; you can tick Invert yourself.
2. **Overall runs first on everything**, including the high-res area. **Decided: as written.**
3. **Vertex groups after GN-Remesh.** If GN-Remesh deletes or splits geometry, the vertex groups still reach the Decimate modifiers, because GN keeps them. That is fine.
4. **Collapse ignores UV seams**: the Decimate modifier has no seam delimit. The boundary seams you mark will be collapsed and moved unless the seam vertices are protected by a vertex-group weight. Overall has no vertex group, so it will move them.
5. **Name mismatch**: `vg_HighRes` (stack) vs `vg_HighDensity` (dropdown). Also `vg_detatch` should be spelled `vg_detach`.

## Step 3: Sculpt Mode PolyCut tool

- **Registering the tool**: a `WorkSpaceTool` in the Sculpt toolbar with its own keymap. It can't literally "duplicate" the built-in Polyline Face Set tool, but it can look and behave like it. **L** as the key works.
- **"Press Ctrl → start drawing"**: a keymap can't fire on Ctrl alone. Bind **Ctrl+LMB**: that click is the first point, then drawing continues. The result is the same as described.
- **Hover + L = "select linked"**: Sculpt Mode has **no face selection**. Options are to highlight the face set with a GPU overlay drawn by the tool, or to temporarily set the Sculpt mask (which then affects brushes). Recommendation: the overlay. Finding the face under the mouse needs a ray-cast BVH of the base mesh (about 0.3 s to build on 236k; rebuilt after each cut).

**Main conflict: the "Set Faces" popup like F9 (Adjust Last Operation) in Sculpt Mode.**
- The redo panel works by **undoing and re-running** the operator. In Sculpt Mode, undo is the Sculpt undo system, which does **not** record vertex-group or edge-seam changes. Each dropdown change would **pile up on the previous one**, and Ctrl+Z would not remove it.
- **Live preview**: modifiers that change topology (Decimate, GN delete) don't show in Sculpt Mode, which draws the base mesh. So a "preview of the result" isn't possible there. The cut itself already switches to Object Mode and back (about 1.3 s + a full stack evaluation).
- **Decided:** a dialog in both modes; see "Set Faces in both modes" under Decisions.

## Decisions (2026-09-28)
- **Decimate: as written.**
  - Overall: Collapse 0.5, no vertex group.
  - Selective: Collapse 0.5 on `vg_HighRes`, not inverted, so weight 1 = decimated further.
  - The dropdown writes `vg_HighRes` (one name everywhere). You can tick Invert on the modifier yourself.
- **Delete / Separate: face attributes** `remesh_delete` and `remesh_detach` (BOOL, face).
- **Separate: marked only.** Your GN-Remesh decides what to do with it.
- **Set Faces works in Sculpt and Edit Mode.** Proposal below.

### Set Faces in both modes without clashing with Edit Mode
- **One operator, `Set Faces`**, working on a face list. In Edit Mode the list is the current face selection. In Sculpt Mode it is the picked or cut face set.
  - It opens as a **dialog**: a dropdown with **High Density / Delete Geo / To Separate / Clear**, plus a **Mark boundary as seam** option that is on by default.
  - **Enter** confirms and **Esc** cancels, in both modes.
  - Changing an option previews at once through the dialog's `check()`:
    - **Edit Mode:** the data is really written, because Edit Mode undo records vertex groups, attributes and seams. Esc restores the values saved when the dialog opened.
    - **Sculpt Mode:** the region is drawn in the option's colour as an overlay. The data is written once on Enter, by a short switch to Object Mode and back, as one undo step.
- **Keys, avoiding Edit Mode defaults** (in Edit Mode, L = Select Linked Pick and Ctrl+LMB = extrude to cursor):
  - **Sculpt Mode, PolyCut tool active:** L = pick the face set under the mouse → Set Faces. Ctrl+LMB = start a PolyCut → Enter or double-click → Set Faces on the new face set.
  - **Edit Mode:** no new keys. Select the way you normally do (L, Ctrl+L, box and so on), then run **Set Faces** from the right-click **Face context menu** or the Remesh panel. The PolyCut tool is also added to the Edit Mode toolbar; its L and Ctrl+LMB work only while that tool is active, so the normal Select tool keeps Blender's L and Ctrl+LMB.
- **Seams:** the boundary of the face list goes into `use_seam`, which Edit Mode undo also records.

## Where to store Delete / Separate
- Vertex groups are per **vertex**: a vertex on the face set border belongs to both sides. `vg_delete = 1` on a region deletes the **neighbouring faces** too when GN deletes by point.
- Recommended: **face attributes** `remesh_delete` and `remesh_detach` (BOOL, face), which GN reads cleanly. Keep a vertex group only for High Density, because the Decimate modifier can only read vertex groups.
- "To Separate": GN cannot create a new object. Inside one object it can only split the region off as a loose part.

## Performance (236k-poly scan)
- Every change re-evaluates the whole stack: GN-Remesh + 2 × Collapse Decimate (roughly 0.5–1 s each) + GN-CameraProject. Expect a few seconds per cut or confirm in Object Mode.
- The PolyCut boolean itself: 1.3 s measured.
- In Sculpt Mode the stack is not drawn, so sculpting and drawing stay fast.

## Files (when implemented)
- `modules/remesh/`: new `workflow.py` (duplicate, rename, collections, stack), `tool.py` (WorkSpaceTool + keymap), a rewritten `operators.py` (L pick / Ctrl+LMB PolyCut / Set Faces modal), and `ui.py` (one Remesh button, `bl_order = 1`). The Poly Cut boolean is kept in `cutter.py` / `gn_remesh.py`. The GN-Remesh modifier becomes an empty user-owned group.
- `modules/baking/props.py` + `normal/mesh_bake.py`: a per-object `source` pointer that takes priority over `hp_object`.
- `bl_order` +1 in the `camera_project`, `baking` and `export` ui.py files.

## Verification
- On a copy of the .blend:
  - Remesh the Desk. Check the names, collections, EXPORT membership, stack order and Setup, and that the original has no GN.
  - Draw a PolyCut with Ctrl+LMB, Set Faces → High Density, Enter. Check the vertex group and seams, then the Decimate result in Object Mode.
  - Press L on an existing face set → Delete. Check the attribute.
  - Bake Normal "from mesh". Check that the original is the source.
- Test the keys with real key presses (computer-use), not by calling the operators.
