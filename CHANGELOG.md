# Changelog

## Unreleased

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
