# Changelog

## Unreleased

### Decimate Brush button (2026-09-29)
- Cutting & Modelling, below 01: **Decimate Brush (Density)** - Sculpt Mode, Blender's
  Density brush (Sculpt brushes > Other), Dyntopo switched on (Blender's own toggle, so it
  warns when mesh data would be lost). In Sculpt Mode a Dyntopo toggle sits next to it
  (red while Density is active without Dyntopo) and the Dyntopo detail settings below.

### PolyCut in Sculpt Mode only; Clear all Face Sets (2026-09-29)
- The PolyCut tool is gone from the Edit Mode toolbar: Edit Mode's L is Blender's Select
  Linked Pick again (and Ctrl+Click extrude to cursor). 01 always goes to Sculpt Mode.
  Set Faces still works in Edit Mode on the selected faces (02, right-click Face menu).
- Set Faces: **Clear all Face Sets** - the whole mesh back to one face set (every PolyCut
  region / Sculpt face set goes, seams stay). Also a button next to 02, in any mode.

### Roughness 1 on every Principled BSDF (2026-09-29)
- Material refreshes set Roughness = 1 (a Roughness link is removed) on MCP_, MAT_ and the
  Bake Source's scan materials; MCP_ and MAT_ are built with it. The Roughness setting
  in Bake Settings (0.8) is gone.

### Fix: pink in ALB_ after Bake from Source (2026-09-29)
- Bake from Source rebuilds MCP_ to show the new BA_, and the rebuild left CamTex_1..n
  without their photos (only apply_slots filled them). An Image Texture without an image
  renders pink in Cycles, so the projected areas baked pink into ALB_. build_material now
  fills the slot cameras' photos itself, and Bake Final fills them again before baking -
  and stops with an error when a slot camera has no usable photo.

### Refresh Materials checks the Bake Source's materials (2026-09-29)
- Every material in the source's slots is checked and wired (not only those its faces
  use), against the Material Output Cycles bakes with (All / Cycles target, the active
  one); a material without nodes or an Output gets them.
- The source keeps only its scan materials: MCP_ / MAT_ (and any other add-on material)
  leave its slots on Refresh - a face still on MCP_ goes back to its scan material by
  uv_index. A new Remesh original is stripped the same way, and every Bake Source (also a
  picked high poly) is treated as an original: material sync never gives it MCP_ / MAT_.
- Every material the Bake Source's faces use must show its image through a Principled
  BSDF: image -> Base Color, BSDF -> Material Output Surface. The material row lists what
  is off ("Surface comes from Emission, not the Principled BSDF"), Export counts it as a
  material problem, and **Refresh** wires it (the Emission node stays, disconnected).
  Scan materials are often shared by several scan objects: the log says how many render
  lit instead of unlit afterwards.

### Fix: Bake from Source gave a black BA_ on unlit scans (2026-09-29)
- The scan materials are unlit (image -> Emission, the Principled BSDF unconnected), which
  a Diffuse Color bake reads as black. BA_ is now an EMIT bake: for the bake, every
  material the source's faces use sends its color (linked Base Color, else Emission
  Color) through a temporary Emission node; the materials are put back after.

### Baking: one resolution, 1K by default (2026-09-29)
- The 1K-8K toggles next to Bake are the only size control: ALB_, NOR_, BA_ and BN_ all
  bake at it. Work Resolution and the Resolution field in Bake Settings are gone.
- A texture baked at another size is outdated: BA_ is re-baked first, and a normal map
  alone at a new size remakes the albedo at that size too. 06 shows "Baked at 8K - bake
  again for 1K".
- New scenes default to 1K (was 8K).
- Fix: a BA_ / BN_ re-bake kept the old packed pixels (packing an already packed image
  keeps its old data). The bake now goes into a fresh image that takes the old one's place
  and name.

### Workflow v4, phases 2-5: steps 0A-07 (2026-09-29)
- **Setup** panel: **0A. Project from Sides** (settings behind its gear; highlighted while the
  scene has no camera; runs 0B after), **0B. Setup Camera Projection** (off without cameras),
  **0C. Remesh** + **Use existing high poly** (link icon: the active low poly bakes from the
  other selected mesh). Any order; 0C sets up the projection on the copy only after 0B.
- **Cutting & Modelling** (only for a low poly): Bake Source + Cage, **01. Poly Cut & Seams**,
  **02. Select & Set (L)** - Projected / Baked (both VCMix alphas), Protect from Decimate,
  Unprotect, Delete (right away), Seam only; seam Mark / Clear / Leave. Previews as overlay
  only. **03. Decimate amount** + **Apply** (one `Decimate`, `vg_Protect` inverted: weight 1 =
  kept); optional **Snap to Source** (Shrinkwrap on `vg_Snap`). **04. Bake from Source**:
  BA_ + BN_ at the Work Resolution, packed, enabled only with a clean `uv_normal`.
- The v3 stack (GN-Remesh + Decimate Overall / Selective) becomes one Decimate on load;
  the GN-Remesh group and the old vertex groups are left alone.
- **05. Projection Painting** (the Camera Project panel, shown once 0B is done): mode line
  (Mix / Projection only) + All Projected / All Baked.
- **06. Bake Final**: ALB_ = the Processing material baked onto the object itself with a
  temporary Simple Subdivision before GN-CameraProject (photos no longer slide on big
  triangles); no projection = ALB_ from BA_. A missing / outdated BA_ is baked first. The
  full-resolution twin and "Albedo from" are gone. NOR_ = BN_ + the albedo's detail
  (High-pass or AI) where the mask says projected; detail alone without BN_. "Bake from
  mesh" / "Mesh + Albedo" and the scene High Poly are gone (the Bake Source replaces them).
- Two fingerprints: BA_ (low poly shape + uv_normal + source + cage) and ALB_ (which now
  includes the BA_ bake).
- **Make uv_normal** (Smart UV, non-overlapping, 0-1): a fix button on "missing uv_normal" in
  the Export summary, in 06 Bake Final and in 04; Fix + Export still makes it where missing
  (never on a low poly whose Decimate is not applied).
- **07. Final Export**: a live Decimate is an error and the object is left out of the FBX;
  BA_ outdated counts as outdated (Update outdated re-bakes BA_ first); a low poly takes
  Color from ALB and its scan UV is not "known".

### Workflow v4, phase 1: Processing material + BA_ / BN_ (2026-09-29)
- MCP_ is now the **Processing** material: a **BAKED** frame (BA_ = albedo baked from the
  Bake Source, on `uv_normal`, BN_ through a tangent Normal Map; flat grey before a bake)
  replaces ORIGINAL MATERIALS. The **Original Scan** slider is gone: the blend mask alone
  decides (0 = baked, 1 = projected).
- New per-object `ba_image` / `bn_image` (packed work textures, named like ALB_/NOR_:
  `BA_<name>`, `BN_<name>`), `ba_fingerprint`, `ba_size`; scene `work_resolution` (4K).
  Material sync renames, releases (duplicates) and removes them with their object.
- Slots: an object with a Bake Source keeps only MCP_ + MAT_; one without keeps its scan
  materials after them (it may still become a Remesh original).

### Workflow v4, phase 0: blend mask = VCMix alpha x VCMix2 alpha (2026-09-29)
- The material multiplies both alphas. The GN writes camera coverage into VCMix2 alpha too.
- Camera 4-6 painting keeps VCMix2 alpha as the stroke marker: the real alpha waits in the
  hidden `_mcp_mask2` (the GN shows the larger of the two) and returns when another layer is
  painted or Vertex Paint is left. VCMix strokes (cameras 1-3, Erase) move VCMix2 alpha by
  the same amount; camera 4-6 strokes raise VCMix alpha where they claim.
- Load migration: a painted VCMix2 with no alpha anywhere (the old marker) takes VCMix's.

### Baking: albedo from the Remesh original (2026-09-28)
- A Remesh copy bakes its albedo from the original's full-resolution mesh: a temporary twin
  (the original's mesh, copied, + the copy's GN-CameraProject with its cameras, shifts and lens
  drivers; no GN-Remesh / Decimate / GN-Final) is baked onto the copy's `uv_normal` with
  Selected to Active, then deleted. The original is never touched.
- Baking panel: **Albedo from <original>** toggle (on by default) + **Cage** (shared with Bake
  from mesh). Off = the copy bakes its own decimated projection as before.

### Camera Project: cameras on the modifier again (2026-09-28)
- **Camera 1-n** are exposed on the GN-CameraProject modifier again (Group Input -> the shared
  group), no longer kept as the wrapper node's defaults. Material / Image inputs stay on the
  node. Measured in 5.2.1: the save leak that wrapper.py works around adds users in memory
  only (a reload resets them), so for cameras it only delays freeing a deleted camera.
- Wrappers carry a layout version; older ones are rebuilt on load with every value kept.

### Camera Project: Resort / Refresh (2026-09-28)
- The Selected Cameras header's one button is split: **Resort** (first, sort icon) scores every
  camera again and picks the best for the slots (the old Auto); **Refresh** (at the end)
  measures the camera list again (Selected + Other Cameras), keeps the picks that still see the
  object and have a photo, fills empty slots from the list, then loads them into the material
  and GN: the object's own MCP_ back in its slot, each Cam texture holding its slot camera's
  photo (fetched from the folder when missing), GN Camera 1-n rewired. It reports each fix.
- Fix: camera lists saved empty. Scoring right after a file load (the coverage migration)
  read every `matrix_world` as zeros, so no camera saw the object; the view layer is updated
  before scoring now. Press Measure Coverage on an object with an empty list.
- A duplicate kept the original's MCP_ in material slot 1, so the node editor showed the
  original's Cam textures. Refresh and Reload All now take that slot over with the object's own.

### Remesh fixes (2026-09-28)
- **Original's textures scrambled:** an object in Final has `uv_normal` as its render UV;
  Remesh stripped GN-Final from the original, so it stayed there and the scan textures read
  `uv_normal`. Remesh now puts the original's remembered UVs back.
- **Original's faces on the projection material:** MCP_ only draws through GN-CameraProject,
  which the original no longer has. Its faces go back to their scan material (by `uv_index`).
- Both repairs (`repair_original`) run at Remesh time and on every file load for the
  originals made before, so an existing broken original fixes itself when the file opens.
- **PolyCut follows the view:** the points are kept in 3D (on the surface under the mouse, or
  at the last point's depth) and re-projected on every redraw, so orbit / pan / zoom (mouse,
  trackpad, numpad, NDOF) keep the polygon on the object. The cut uses the view it is closed in.
- **Set Faces vertex groups:** Delete Geo also writes `vg_toDelete`, To Separate `vg_toSeparate`
  (next to the face attributes), exclusive with `vg_HighRes`. Remesh creates all three groups.
- **PolyCut Tool button:** in Sculpt / Edit Mode the Remesh panel shows it while the PolyCut
  tool is not active (each workspace keeps its own tool, so L / Ctrl+Click went to the brush).

### Remesh module v3 (2026-09-28)
Plan: `docs/PLAN_remesh_v3.md`. Replaces the Poly Cut button and the GN-Remesh face set picker
below (the boolean cut itself is kept).
- **Remesh** (one button, the panel is now 2nd, below Project from Sides): a full copy of the
  scan (own mesh) takes over its name, collections, EXPORT and MCP_/MAT_/ALB_/NOR_. The original
  is renamed `<name>_original` (plain rename, textures stay with the copy), moved to the
  **Original Mesh** collection, unlinked from EXPORT, hidden, and loses its GN modifiers and
  their drivers. The copy runs Camera Project Setup, then opens in Sculpt Mode with the
  **PolyCut** tool.
- **Stack of the copy:** GN-Remesh (an empty group, yours to build; an existing GN-Remesh is
  never overwritten) -> **Decimate Overall** (Collapse 0.5) -> **Decimate Selective** (Collapse
  0.5 on `vg_HighRes`, not inverted: weight 1 = decimated further) -> GN-CameraProject -> GN-Final.
  The panel shows the ratios and the Invert toggle. Decimate collapses across UV seams.
- **PolyCut tool** (Sculpt and Edit Mode toolbars; its keys only work while it is active, so
  Edit Mode keeps Blender's L and Ctrl+Click on the other tools):
  - **Ctrl+Click** starts a PolyCut (the click is the first point); Enter / double click cuts,
    then Set Faces opens for the new face set.
  - **L** picks the face set under the mouse (BVH of the base mesh, rebuilt when it changes)
    and opens Set Faces.
- **Set Faces** (dialog, Sculpt and Edit Mode; also in Edit Mode's right-click and Face menus):
  **High Density** (`vg_HighRes` = 1), **Delete Geo** (face BOOL `remesh_delete`), **To
  Separate** (face BOOL `remesh_detach`, marked only) or **Clear**; **Mark boundary as seam**
  (on by default; Clear removes that seam). The options replace each other on a region. The
  region is drawn in the option's color while the dialog is open. Edit Mode previews on the
  real data and Esc puts it back; Sculpt Mode writes once on Set, through Object Mode (Sculpt
  undo does not record vertex groups or seams).
- **Baking:** a per-object bake source (`multicamproject_bake.source`, set by Remesh) is used
  by Bake from mesh before the scene's High Poly; the hidden original is shown for the bake.
- Camera Project, Baking and Export move one place down in the sidebar.

### Remesh module (2026-09-28)
- **Poly Cut** (the one button in the Remesh panel, any mode): click points in the viewport,
  **Enter** or **double click** closes the polygon back to the first point, **Esc** cancels. The
  mesh is cut **exactly along the drawn lines** (Exact boolean, not along the scan's triangles)
  and the inside becomes a **new Sculpt face set**. The cut depth covers what the polygon shows.
- **face_set** (face INT): a copy of the Sculpt face sets that GN can read (GN cannot read
  `.sculpt_face_set`). Refreshed after every cut and whenever the object leaves Sculpt Mode.
- **Add GN-Remesh** (Remesh panel): a modifier at the top of the stack that reads the face sets.
  **Face Set** (number), **Isolate** (show only it), **Invert** (everything but it); it also
  stores `remesh_selection` (face BOOL) for modifiers after it. The panel shows its inputs and
  the face sets with their face counts; a Poly Cut switches it to the new face set.

### Baking + Export modules (2026-09-25)
Plan: `docs/PLAN_baking_export.md`.

#### Camera Project
- The projection material is now **`MCP_<object>`** (was `MAT_<object>`), renamed on file load.
  `MAT_<object>` is the baked export material. The leftover `MCP-MatIndex_to_uv` group is removed.
- The scan UV lookup skips `uv_normal`, the user's bake UV.

#### Baking (new module `baking`)
- **GN-Final** modifier, always last: **Projection / Final**. Final = `Color` (corner, byte) from
  the scan's `Attribute` (or sampled from ALB), `UV_cam*` / `VCMix*` / `uv_index` / scan UV / scan
  color removed, Set Material `MAT_`. The projection modifier is switched off and `uv_normal`
  becomes the active + render UV while Final is on; Projection puts everything back.
- **Bake Albedo**: Cycles Diffuse Color into `ALB_<name>.png` (8-bit, sRGB) on `uv_normal`, always
  the same file and image (no `.001`). Render settings are saved and restored (BakeLab 2 subset).
- **Normal map** `NOR_<name>.png` (16-bit, Non-Color, OpenGL): High-pass from the albedo, Bake from
  mesh (Selected to Active, optional smoothed copy); Full build adds Mesh + Albedo (RNM) and AI
  (onnxruntime). Preview at 2K; each run shows its size, source and time.
- **MAT_<name>**: ALB -> Base Color, NOR -> Normal Map, both on `uv_normal`. Nodes the user adds
  survive a rebuild.
- **Outdated** detection: a fingerprint of cameras, photos, shifts, Mode, Previous Bake, Original
  Scan, VCMix and `uv_normal` at the bake.

#### Export (new module `export`)
- **EXPORT** collection (yellow), active scene only, meshes only; Add links, Remove unlinks.
- Status list (click = select + frame) with stages, summary box with one fix per problem:
  **Rename objects** (object, mesh, MCP_, MAT_, ALB_/NOR_ images and files), **Fix transforms**
  (applied, origin at the bottom center; projection unchanged), **Update outdated**, **Bake
  missing**, per-scene **Delete** (the only confirm dialog). All warnings stay in the panel.
- **Export FBX** from temporary copies: one material `MAT_`, one UV `uv_normal`, one color `Color`,
  original names; textures copied to `Textures/`, `Editor/MCPTexturePostprocessor.cs` for Unity,
  `export_report.txt`. One FBX or one per object.
- **Fix + Export**: one confirm dialog lists the plan, then Export renames, fixes transforms,
  makes a Smart UV `uv_normal` where missing, rebakes outdated / not baked objects, exports, and
  leaves every exported object in Final. What cannot be fixed is warned about (dialog + report).
- **Name Prefix** `<code>_<scene>` (top of the Export panel, saved in the .blend), e.g. `00_30stBR`:
  objects `30stBR.00_Desk` (`##` numbered by the add-on), `MAT_00_30stBR.00_Desk`,
  `ALB_/NOR_00_30stBR.00_Desk`, FBX `ENV_00_30stBR.fbx` (the FBX Name field is gone). Only the
  `<name>` part is typed: F2 / Outliner renames are fixed live, and double-clicking a name in the
  status list edits just that part (the rest is shown greyed). MCP_, MAT_, ALB_/NOR_ and the
  texture files follow every rename. A copied object (Shift+D) lets go of the original's bake.
  Older names ending in `_##_Name` keep their number.
- **Textures folders stay clean**: `ALB_*`/`NOR_*` PNGs in the bake folder that no image of the
  .blend uses are warned about (Clean button) and removed by Fix + Export; `<export>/Textures/`
  keeps only the textures of the last export. Removed files go to the Recycle Bin. Other PNGs are
  never touched - the folder may be shared with other assets.

- **Normal Map: Lite | AI switch** with the last time of each source and size, to compare
  speed. **Set up AI** installs onnxruntime (PyPI, into Blender's user modules) and copies a chosen
  `.onnx` model into Blender's user data folder (survives add-on reinstalls).

#### Build
- `build_extension.py` puts `blender_manifest.toml` and `__init__.py` at the zip root (the old
  layout failed `extension validate`); `--full` bundles `./wheels/*.whl` and the model.
- `manifest.toml` follows the extension schema (tagline, maintainer, SPDX license, file permission).

### Project from Sides, up to 6 cameras, adaptive paint (2026-09-24)

#### Project from Sides (new module `project_sides`)
- Own collapsible sub-panel **Project from Sides**, above Camera Project. Its settings live on
  the scene, so they are saved with the file and shared by every object:
  - **Sheet** image - a reference sheet with the views side by side (Left, Right, Front, Back).
  - **Orthographic / Perspective**, **Height**, **Distance**, **Ortho Scale** or **Focal Length** -
    one set for all sides cameras. Dragging a field moves the cameras live.
- **Project from 4 Sides** creates any missing camera **Ortho.Front / Left / Right / Back**
  (`Persp.*` when perspective; Blender view names, Right = +X) in `Sides.Cameras` and resets all of
  them to the panel: image, type, lens, position around the active object's origin, looking level
  at it. It does not score or assign cameras - Setup / Reload All pick them up.
- **Defaults** (first Project, or the reframe button next to it): Height = half the Z of the
  object's highest point, Distance = Height, Ortho Scale / Focal Length fit ground..top into 85%
  of the sheet's height.
- **The camera frame has the sheet's aspect**: Blender has no per-camera resolution, so Project
  keeps the scene's resolution X and sets Y to the sheet's aspect (warns when it changes).
- Each sides camera shows the whole sheet; its UV shift starts on its panel (equal panels:
  Left +0.375, Right +0.125, Front -0.125, Back -0.375) and a re-run keeps a tuned shift.

#### Global cameras (Camera Project)
- A toggle at the end of every camera's slot buttons: **globe = global**, box = attached to the
  objects (default). Stored on the camera, so it belongs to the file.
- A global camera has **one UV shift for every object**; switching carries the active object's
  shift over, so the photo does not jump. **Reload All** leaves its image and clipping alone.
- Sides cameras are created global and tagged with their side. Switched to object-attached, a
  sides camera keeps its tag: Project neither resets it nor makes a new one.
- Otherwise they are ordinary cameras: scored, slotted, shifted, painted, soloed.

#### Orthographic projection
- The projection and camera scoring support **orthographic cameras**: per slot `Ortho N` and
  `Ortho Scale N` inputs, driven from the camera; facing and occlusion use the camera's view axis.
  Perspective is unchanged, and both types can be mixed on one object.

#### Up to 6 cameras
- **Cameras 3-6 dropdown** per object, in the Selected Cameras header. Slot buttons `1..N` and
  keys 1-6 over the sidebar tab follow it (keys above the count keep their usual job). Raising the
  count fills the new slots with the best-scoring unused cameras; lowering it keeps cameras 4-6
  stored, not projected.
- **VCMix2**: R, G, B = cameras 4-6. **Cameras 1-3 (VCMix) stay on top of 4-6** where both have
  weight; the GN stores VCMix2 first and VCMix last. Automatic weights: VCMix holds cameras 1-3's
  share of all cameras, VCMix2 the best of 4-6 - so the result is exactly the plain 6-way Sharp /
  Smooth blend, and a mesh painted with 3 cameras keeps its paint when the count goes up.
- **Node groups per count**: `GN-CameraProject` / `MCP-MixResult` for 3 (as before),
  `GN-CameraProject_N` / `MCP-MixResult_N` for 4-6, built when first used (node group version 12).
  The material is rebuilt with CamTex_1..N when the count changes (material version 5). A rebuild
  rewires every set-up object, whatever its count.
- Soft edges blend 1-3 into 4-6 without dark seams; where no camera covers a face the original
  scan shows.

#### Adaptive paint (4+ cameras)
- Switched by the camera painted with:
  - **Cameras 4-6: on** - a stroke into VCMix2 clears VCMix (cameras 1-3) under it, so 4-6 show.
  - **Cameras 1-3: off** - they are on top anyway; VCMix2 underneath is kept.
- Blender paints one color layer per stroke, so the clear runs right after each stroke (a timer
  that never touches the mesh while a stroke runs): a camera 4-6 stroke over painted 1-3 appears
  when the mouse is released. Soft strokes clear in proportion.
- **Only faces turned toward the painted camera are claimed**, so a brush reaching through the
  mesh or around its silhouette cannot hand the far side to a camera that cannot see it. Paint and
  Flood also turn on the brush's **Front Faces Only**.
- VCMix2 alpha (otherwise unused) marks where a stroke went, so painting a camera 4-6 over its own
  color still claims the area.
- **Undo: two steps per stroke** - Ctrl+Z once undoes the clear, twice the stroke.
- **Erase works on all layers**, from any camera row: it erases VCMix alpha, the blend mask of
  every camera, so the original scan shows (Shift+click brings the projection back).

#### UI and fixes
- Flood and Erase use standard UI icons (fill, eraser), centered like the brush - the toolbar
  icons were drawn large and clipped.
- Slot buttons and the global toggle are equal-width columns, so nothing is clipped at 4-6 slots.
- Solo refuses a camera that is no longer in the scene (e.g. its collection was deleted) with a
  clear message, instead of a misleading "hidden collection" warning.

### Camera Project - paint VCMix per camera (2026-09-24)
- The placeholder buttons on each selected camera now paint the mesh's **VCMix** in Vertex Paint
  (switching there from Object/Edit Mode), in the camera's channel: Camera 1 red, 2 green, 3 blue.
  - **Paint**: Draw brush with the camera color. Shift+drag smooths (Blender's own keymap).
  - **Flood**: fills the selected faces (Edit Mode with faces selected, or Vertex Paint with the
    face mask on) with the camera color, alpha 1.
  - **Erase**: brush erases VCMix alpha (original scan shows); Shift+click adds alpha instead.
  - The strokes go into the mesh's VCMix - the layer Previous Bake blends in. Without one it is
    copied from what the modifier shows, and Previous Bake is set to 1 so the paint shows 1:1.
- The shift X/Y fields have the buttons' height, so both rows end on the same line.
- Solo works in Edit Mode and Vertex Paint too, so the solo camera can be switched while
  painting; the eye buttons are never dimmed.
- **Up/Down arrows** over the MultiCamProject sidebar tab solo the camera above/below the soloed
  one (list order: Selected, then All). Elsewhere, or with no camera soloed, the keys keep their
  usual job. Bound in the "Frames" keymap: it handles Up/Down (Jump to Keyframe) before any 3D
  view keymap, so a binding anywhere else never sees the keys.
- **1 / 2 / 3** (number row or numpad) over the same tab make the soloed camera Camera 1/2/3,
  like its 1/2/3 buttons (swapping if it already sits in another slot).
- **All Cameras is a list widget** (12 rows, resizable, own scrollbar, name filter). Its active
  row is the soloed camera, so the list scrolls to every new solo; clicking a name solos it.
  **Numpad .** over the tab scrolls the list back to the soloed camera.
- **Solo shows the photo even when the cameras' collection is excluded/hidden**: the collection
  (and its parents) is included while soloed and restored on leaving solo. Only the object and
  the soloed camera stay in the solo view - Blender puts every object of a collection that gets
  re-included during local view into that view, so the others are taken out again.
- Flood and Erase icons: a paint bucket and an eraser (Blender toolbar icons).

### Camera Project - one material for scan + projection (2026-09-24)
- **Setup Camera Projection** now also merges the scan's materials (was a separate Convert
  Material button, never released). One button, one GN (`GN-CameraProject`), one material:
  - `MAT_<object>` sits in material slot 1; the scan materials move down, faces follow.
  - `uv_index` (face, int) is written into the mesh = the face's material slot. Setup and Reload
    All refresh it; faces already on slot 1 (after Bake View Mix) keep their value.
  - The material has three frames: **ORIGINAL MATERIALS** (each scan material's Base Color image,
    picked per face by `uv_index`, with an explicit UV Map node on the scan UV), **PROJECTION**
    (Cam 1/2/3 weighted by VCMix) and **BLEND**.
  - **Original Scan** slider (sidebar, stored in the material): 0 = projection, 1 = scan.
  - **Blend mask** in VCMix alpha = how much the cameras see the face (R+G+B, max 1). Faces no
    camera sees always show the scan. Baked with VCMix.
  - Migration: a `MATMCP_` slot is taken over in place and the material deleted; the
    `MCP-MatIndex_to_uv` modifier is removed (and its group, once unused).
- **Original Blend renamed Previous Bake** (value carried over on update).
- **Reload All** is now an icon at the end of the Folder row.
- **Camera blocks**: every camera is its own box, 1.25x taller. While a camera is soloed every
  other block is dimmed. The shift X field starts exactly under the image field.
- **Paint / Smear / Erase** icon buttons (1.4x) left of the shift fields, in place of the "Shift"
  label - placeholders, not implemented yet.
- GN-CameraProject inputs Occlusion, UV Shift Cam1-3 and Focal/Sensor/Aspect 1-3 are single values
  (structure type `SINGLE`), node group version 6.
- Fix: rebuilding the shared node groups reset the *other* set-up objects (Mode, Previous Bake,
  Occlusion, cameras, shifts) and broke their lens drivers. All users are now restored and
  rewired. Mode also no longer comes back empty after a rebuild.

### Camera Project - readable node layout, bake fixes (2026-09-24)
- **GN layout adopted from the hand-cleaned file** (node group version 4):
  - `GN-CameraProject` inputs grouped in panels: General Settings, Camera Shift, Cameras, Lens (driven).
    Shift inputs renamed `UV Shift Cam1/2/3` and are 2D vectors.
  - New sub-group `MCP-MixResult` holds the UV_cam store, Sharp/Smooth/Combined mix, VCMix blend and
    material. Output is identical to version 3.
- **Bake View Mix**: checks the node group's inputs *before* applying, so a mismatch can no longer
  leave the re-added modifier half-wired (lost cameras). A fake user on the mesh no longer counts
  as "shared by several objects".
- **Camera list** split into foldable *Selected Cameras* and *All Cameras* sections.

### Camera Project - shift in UV range, wider slot buttons (2026-09-23)
- **Shift is now in UV range** (-1..1, 5 decimals): -1 = one full photo to the left/down, 1 = to the
  right/up. Used as-is for the GN `UV Shift N` input and the camera background offset (no pixel
  conversion). Shift+drag for finer steps; 1 px on an 8192 px photo is ~0.00012.
- **Migration**: shifts stored in pixels by the previous version are converted once per object
  (`shift_version`), on addon load, on file load, and before any rewire.
- **Slot buttons double width**; row widths now come from the panel's pixel width, so the name and
  `1 2 3` keep their size in narrow panels and only the image field shrinks.

### Camera Project - shifting & list layout (2026-09-23)
- **Sorted camera list**: Camera 1/2/3 pinned on top (slot order), the rest alphabetical.
- **Per-camera pixel shift** under each slotted camera (`Shift X / Y`, sliders, 0.1 px steps):
  - Stored per object *and* per camera (`Object.multicamproject_cam.shifts`), initialised to (0, 0),
    never cleared by Reload All.
  - Live while dragging: updates the GN input `UV Shift N` (projection samples at `UV - shift`)
    and the camera's background image offset. After release nothing touches the camera, so other
    objects sharing it can set their own shift.
  - **Load Shifting** button copies the active object's stored shift onto the camera's background offset.
  - Conversion: `offset = px / image size` on both axes. Measured in Blender 5.2 - the background
    offset is in frame width (X) / frame height (Y); photos fill the frame, so it equals the UV shift.
- **Row layout**: eye, fixed-width name, image field fills the row, folder, narrow `1 2 3` on the right.
- GN node group bumped to version 3 (`UV Shift 1/2/3` inputs). Rebuild keeps Mode, Original Blend
  and Occlusion.
- Fix: solo state is stored on the window manager, so leaving solo after an addon reload restores
  overlays, background depth and camera visibility instead of crashing.

### Camera Project module (2026-09-23)
- **Setup Camera Projection**: builds shared GN groups `GN-CamProject_Single` / `GN-CameraProject`
  (built only when missing/outdated, rebuilt in place), per-object `MAT_<name>` with
  `CamTex_1/2/3` + `CamUV_1/2/3`, and the modifier with lens drivers.
- **Camera detection**: every scene camera scored by coverage x facing of the mesh; only cameras
  that hit the object are listed. Images live on each camera (`background_images[0]`), resolved
  from the object's folder by filename / camera name.
- **Exclusive slots 1/2/3**: reassigning swaps; top 3 auto-filled on first run only, afterwards user
  picks are kept unless a camera loses its image or the object (auto-replaced with a warning).
- **Reload All**: reload images, apply default clipping (0.01 / 100 m), re-score, check slots, rewire.
- **Solo view (eye)**: local view with the object, looks through the camera and draws its photo on
  top (overlays on, camera shown, depth Front) - all restored on exit.
- **Bake View Mix**: applies the modifier (bakes `UV_cam1/2/3` + `VCMix`) and re-adds it with the
  same settings so the baked mix is the base for the next blend.
- Module system scaffold, prototype kept in `docs/`, minimum Blender 5.2.
