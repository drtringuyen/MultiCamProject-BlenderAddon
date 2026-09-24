# Changelog

## Unreleased

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
