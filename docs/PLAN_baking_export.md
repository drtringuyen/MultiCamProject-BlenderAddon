# Plan: Baking + Export modules

Status: **requirements agreed 2026-09-25; implemented 2026-09-25** (phases 0-9; the AI source needs a model and wheels, see §8). Deviations: each object bakes in its own `bpy.ops.object.bake` call (the scans share scan materials); never-baked EXPORT objects are left out of the FBX and listed in the report; GN-Final gets the Albedo image only when Color is sampled from it (GN reads images as floats, ~1 GB at 8K).
Target: Blender 5.2, Unity 6+ with URP. Work on `main` (see memory `work-on-main`).
Reference: BakeLab 2 (GPL-3, Shahzod Boyxonov), `Blender-BakeLab2-master.zip`. Only a small subset gets ported, with credit.

---

## 1. Goal

Per scan object: bake the camera projection (plus the scan) into one clean texture set on a UV layout the user made (`uv_normal`). Flip each object between the projection setup and the final baked result without destroying anything. Export all finished objects as one FBX that assembles itself in Unity.

Exported object (the one-of-each rule):

| Item | Value |
|---|---|
| Material | 1 × `MAT_<name>` (Unity URP Lit) |
| UV | 1 × `uv_normal` |
| Vertex color | 1 × `Color` |
| Albedo | `ALB_<name>.png`, 8192², **8-bit**, sRGB |
| Normal | `NOR_<name>.png`, 8192², **16-bit**, Non-Color, OpenGL (Y+), same as Unity |

`<name>` is the object name after the **Rename objects** fix (§7.4), e.g. `Model - 12 - Trashbin` → `Model_12_Trashbin`.

## 2. Decisions (all agreed with the user)

1. The projection material is renamed from `MAT_<obj>` to **`MCP_<obj>`**, migrated on file load. `MAT_<obj>` is now only the baked export material.
2. **`uv_normal`** is the bake target: non-overlapping, 0–1, normally made by hand. *Changed 2026-09-25:* Export makes one by Smart UV Project where it is missing (§7.7).
3. No `.baked` duplicate object. A **GN-Final** modifier switches between the projection and the final result.
4. The vertex color `Color` comes from the scan attribute **`Attribute`** (Corner, Byte) by default. Advanced dropdown: `Scan attribute` / `Sampled from ALB`. Missing attribute: fall back to ALB and warn.
5. NOR is generated from the albedo, because the scan geometry is too dirty for a mesh bake. Two builds (§8):
   - **Lite:** High-pass and Bake from mesh.
   - **Full:** adds AI (DeepBump-style, onnxruntime) and Mesh + Albedo. It shows the time per run so the user can compare.
6. ALB 8-bit, NOR 16-bit PNG. The Unity dev compresses the textures.
7. Unity C# postprocessor: ALB **and** NOR max size 8192, NOR type Normal map, sRGB off. It applies on first import only.
8. Export source: the **`EXPORT`** collection (yellow color tag `COLOR_03`), **active scene only, meshes only**. "Add to EXPORT" *links* the objects, it doesn't move them.
9. **All warnings appear in the panel**, in the summary box and inline in the rows. **No popups** for warnings. A confirm dialog is used only for destructive buttons (deleting a scene).
10. Fix Transforms: origin at **bottom center**, all transforms applied, **written into the .blend**.
11. Object names that aren't clean: the panel warns and offers **Rename objects**, which renames the object plus its mesh, MCP_, MAT_, ALB_ and NOR_ (images and files).
12. Outdated bakes: warn in the panel, with an **Update outdated** button. Export is not blocked.
13. Other scenes in the file: warn in the panel, with a **Delete scene** button per scene (§7.5).

## 3. Verified facts (tests run 2026-09-25)

Test scripts are in the session scratchpad, not in the repo. They were run with `blender.exe -b --factory-startup --python <script>` so the user's file stayed untouched.

| Question | Result |
|---|---|
| GN Remove Named Attribute with wildcard `UV_cam*`, `VCMix*` | ✅ Works. The node has a **"Pattern Mode" input socket**, set to `"Wildcard"`. |
| GN removes real mesh UV layers (e.g. `DiffuseUV`) | ✅ |
| GN Store Named Attribute `Color` (BYTE_COLOR, CORNER) from `Attribute` | ✅ Survives the FBX export and re-import, value kept |
| FBX keeps only the UVs left after GN | ✅ only `uv_normal` |
| FBX writes Base Color image + Normal Map image of `MAT_` | ✅ both come back on re-import, relative paths |
| **GN Set Material results in a single material** | ❌ **No.** It *appends* `MAT_`, and the old materials stay in the mesh's material list and in the FBX. Tried: removing `material_index`, Geometry to Instance + Realize, Set Material None first. None of them clears the list. → **The exporter trims the list on temporary mesh copies (§7.6).** |

Test file `TEST MultiCcam inaction.blend`:
- 4 meshes:
  - Model 11 Desk (set up)
  - Model 12 Trashbin (set up)
  - Model 13 Chair (not set up)
  - MESH_Trashbin (set up, scan UV `UVMap`, no `Attribute`, scale 0.645 + rotation)
- The scans use UV `DiffuseUV` and color `Attribute` (CORNER BYTE_COLOR), plus `custom_normal` and `uv_index`.
- All three scans **share** the scan materials `material0–5.002`.
- No object has `uv_normal` yet.
- The file has **3 scenes**. Each has its own copy of the 490 cameras and their image entries (1,478 images in total, all pointing to the same JPGs). Most likely the photogrammetry import was done three times.
- The legacy group `MCP-MatIndex_to_uv` is still in the file.
- Meshes have 2 users only because of a fake user. They aren't shared.

**Live-test rules** (memory `blender-testing-gotchas`):
- Never read `image.size` or pixels of camera photos from MCP. It decodes all 1,478 images; the query reached 77 GB of RAM and the user had to restart.
- Start with a trivial query to confirm Blender responds.
- Test on copies, inside try/finally.

## 4. Architecture

```
modules/
  camera_project/   existing: MCP_ rename + uv_normal fixes only (§5)
  baking/           new
    __init__.py
    props.py        Object.multicamproject_bake + Scene.multicamproject_bake_settings
    gn_final.py     builds GN-Final group, ensure/get modifier, Projection/Final toggle
    engine.py       ALB bake (BakeLab subset: save/restore render + bake settings)
    material.py     builds/updates MAT_<name>
    normal/
      __init__.py   source registry: available_sources() -> [(id, label)]
      highpass.py   numpy, Lite + Full
      mesh_bake.py  Cycles NORMAL, Selected to Active, Lite + Full
      blend.py      mesh + albedo (Reoriented Normal Mapping), Full
      ai.py         onnxruntime, Full only - imports lazily, hidden when onnxruntime is missing
    fingerprint.py  state hash for "Outdated"
    operators.py
    ui.py
  export/           new, requires baking
    props.py        Scene.multicamproject_export
    checks.py       one pure function per check -> list[Issue(obj, code, text, fix_op)]
    status.py       stage per object (§7.2)
    fixes.py        rename, transforms, scene delete
    fbx.py          temp copies + FBX + textures + C# + report
    unity/MCPTexturePostprocessor.cs   template, copied to Editor/ on export
    operators.py
    ui.py
```

- Scaffold both modules with the `blender-addon-modules` skill (registers them in `ALL_MODULES` and adds the Infos toggle row).
- `export` checks `module_manager.is_loaded("baking")`. If baking is off, the export panel only shows a note.
- `baking` works without `camera_project`: GN-Final and the bake work on any mesh with `uv_normal`. The projection-specific steps are skipped when the object has no `GN-CameraProject`.

## 5. Phase 0: camera_project changes (do first)

1. `core.material_name(obj)` → `f"MCP_{obj.name}"`.
2. Migration in `migrate_all()` (runs on load): every material with `MAT_TAG` whose name starts with `MAT_` is renamed to `MCP_…`. If an `MCP_` with that name already exists, keep the tagged one.
3. `_scan_uv()` must skip `uv_normal`: `u.name.startswith("UV_cam") or u.name == "uv_normal"`.
4. Delete the leftover `MCP-MatIndex_to_uv` group when it has no users (`_remove_legacy` already handles it per object; also do it once in the migration).
5. Update memory `convert-material-merge-plan` (the MAT_ naming is superseded).

Acceptance:
- Open the test file: the materials are `MCP_*`, projection unchanged.
- Setup and Reload All still work.
- Also update the UI labels and docstrings that mention `MAT_`.

## 6. Baking module

### 6.1 Properties
`Object.multicamproject_bake`:
- `alb_image`, `nor_image`: Pointer Image
- `material`: Pointer Material
- `fingerprint`: String (state at the last ALB bake)
- `nor_source_used`: String
- `last_bake_seconds`, `last_nor_seconds`: Float

`Scene.multicamproject_bake_settings`:
- `resolution` (default 8192; Advanced: 1024–16384)
- `margin` (16 px at 8K), `device` (GPU/CPU), `anti_alias` (1)
- `output_dir` (default `//Textures/`)
- `nor_source` (enum, filled from `normal.available_sources()`)
- high-pass settings: `nor_strength`, `nor_radius`, `nor_invert`, `nor_preview_2k`
- mesh bake settings: `hp_object` (Pointer), `cage_extrusion`, `smooth_source` (bool + iterations)
- `color_source` (`SCAN_ATTRIBUTE` / `FROM_ALB`), `scan_color_name` (default `Attribute`)
- `png_compression` (Advanced)

### 6.2 GN-Final (`gn_final.py`)
- Group `GN-Final` (version key like `gn_builder`), modifier `GN-Final`, always the **last** in the stack.
- Inputs: `Final` (bool), `Baked Material` (material), `Keep UV` (string, `uv_normal`), `Color Source` (menu: Scan Attribute / From ALB), `Scan Color` (string, `Attribute`), `Albedo` (image, for From ALB).
- Chain, used only when `Final` is on (Switch node; only the chosen branch is evaluated):
  1. Store `Color` (CORNER, BYTE_COLOR). Scan Attribute: Named Attribute `Scan Color`. From ALB: GN Image Texture node on `Albedo` sampled at the `uv_normal` attribute. If the attribute doesn't exist, it falls back to ALB.
  2. Remove Named Attribute, wildcard: `UV_cam*`, `VCMix*`.
  3. Remove Named Attribute, exact names: `uv_index`, `Scan Color`, and the scan UV name. Python writes it into a hidden string input `Scan UV` when the toggle runs (e.g. `DiffuseUV`/`UVMap`).
  4. Set Material `Baked Material`.
- The toggle operator `multicamproject.bake_set_final(state)` for selected objects, or all of EXPORT:
  - sets `Final`
  - `GN-CameraProject.show_viewport`/`show_render` = not Final (skips the projection cost)
  - makes `uv_normal` both the active and the active-render UV while Final is on, and restores the previous values afterwards (stored on the object)
- UI state is read from the modifier. There's no separate flag.
- Any UV layers left besides `uv_normal` are reported by the exporter's check. They can't be removed generically in GN ("all except X" can't be expressed).
- Once the user hand-edits the GN-Final layout, port it into the builder (memory `gn-layout-is-user-owned`).

### 6.3 ALB bake (`engine.py`)
The BakeLab subset: `save_defaults`/`restore_defaults` of the render and bake settings, plus image preparation.
- The operator runs synchronously (`EXEC_DEFAULT`) with `wm.progress_begin/update/end`. No modal timer.
- Steps for all selected objects (one `bpy.ops.object.bake` call covers all of them):
  1. Check: has `uv_normal`. Otherwise the panel shows the problem and the button is disabled (poll).
  2. Final off for these objects. Store and set `uv_layers.active = uv_normal`; `active_render` stays as it is.
  3. `alb_image`: reuse it by pointer. Otherwise `bpy.data.images.new(f"ALB_{name}", res, res, alpha=False)`, byte, sRGB. If the size differs, `scale()`.
  4. In the material that's actually evaluated (`MCP_<obj>`, set through the GN "Material" input; for non-projection objects, every slot material), add a temporary `ShaderNodeTexImage` named `MCP_BAKE_TMP`, active, image = alb.
  5. Settings:
     - Cycles, device, samples 1
     - `bake_type='DIFFUSE'`, `use_pass_direct=False`, `use_pass_indirect=False`, `use_pass_color=True`
     - `use_selected_to_active=False`, `margin`, `target='IMAGE_TEXTURES'`, `use_clear=True`
  6. Bake. Remove the temporary nodes. Save to `output_dir/ALB_<name>.png` (PNG 8-bit), always the same path, **no .001**, and `reload()`.
  7. Build or update `MAT_` (§6.4). Store the fingerprint. Restore the UV and render settings. Final on.
- Metallic is 0 in MCP_, so the Diffuse Color pass equals the albedo. For non-MCP objects with metallic materials, BakeLab's emit trick (`passes_to_rgb` + `ungroup_nodes`) would be needed. Leave that out until it's needed.
- Record the time → `last_bake_seconds`.

### 6.4 MAT_ material (`material.py`)
- Found by pointer, then by name, otherwise created.
- Manages only nodes tagged `mcp_bake_*`; other nodes the user added survive a rebuild.
- UV Map (`uv_normal`) → Image `ALB_` → Principled Base Color.
- UV Map → Image `NOR_` (Non-Color) → Normal Map (tangent, uv_map `uv_normal`) → Principled Normal.
- Principled: Roughness ~0.8 (setting), Metallic 0.
- The FBX exporter's Principled wrapper reads exactly this structure (verified).

### 6.5 NOR
Common rules:
- Output `NOR_<name>`, 16-bit PNG, Non-Color, `output_dir/NOR_<name>.png`, same path on every run.
- Each run stores and shows `NOR 8192² · <source> · <seconds> s`.
- `Preview at 2K` runs the same thing at 2048 for tuning.

| Source | Build | How |
|---|---|---|
| High-pass (albedo) | Lite + Full | numpy: brightness → high-pass (running-sum box blur ×3 ≈ Gaussian; cost doesn't depend on radius) → Sobel dX/dY × strength → normalize → RGB. Before filtering, fill outside the islands with the margin (or read the ALB margin) so seams stay clean. Estimated 5–15 s and 1.5–2 GB of RAM at 8K. Process in float32, in stripes if memory is tight. |
| Bake from mesh | Lite + Full | Cycles `NORMAL`, tangent space, Selected to Active from `hp_object` onto the object, cage extrusion. Optional `smooth_source`: temporary copy of the high-poly with a Smooth / Corrective Smooth modifier, deleted afterwards. An object baked onto itself gives flat normals, so the panel warns if `hp_object` is missing. |
| Mesh + Albedo | Full | Mesh bake + high-pass, combined with Reoriented Normal Mapping in numpy. |
| AI (albedo) | Full | onnxruntime (CPU; DirectML optional) with a DeepBump-style color→normal model. Tiles 256 px with overlap, blended. Estimated 2–5 min at 8K on CPU; measure. |

## 7. Export module

### 7.1 Panel layout
```
EXPORT                                   [Add to EXPORT] [Remove]
Scene: Scene  ⚠ 2 other scenes (980 cameras, 980 images)   [details ▸]
   Scene.001  490 cams  [Delete scene]
   Scene.002  490 cams  shares: Desk, Chair  [Delete scene]
15 meshes · 12 ready · ⚠ 3 problems
   ⚠ 2 names need renaming        [Rename objects]
   ⚠ 1 transform not applied      [Fix transforms]
   ⚠ 1 outdated bake              [Update outdated]
   ⚠ 1 missing uv_normal          (create it by hand)
[All → Final] [All → Projection]
┌ status list (UIList, sorted by stage) ──────────────────┐
│ 🟢 Model_12_Trashbin   Ready                             │
│ 🔴 Model_11_Desk       Outdated                          │
│ 🟠 Model_13_Chair      no uv_normal                      │
└──────────────────────────────────────────────────────────┘
Folder [//Export/]  FBX name [scene]  ( ) one FBX  ( ) one per object
[Export (1 outdated)]
Advanced ▸  color source, png compression, write C# script, report
```
- Clicking a row selects the object and frames it.

### 7.2 Stages (`status.py`)
Computed live and cheaply. **Never touch image pixels or sizes of unloaded images.** Size checks use a stored value from the bake, or file headers.

| Stage | Condition |
|---|---|
| ⚪ Not set up | no GN-CameraProject and no ALB |
| 🟠 Projection | `uv_normal` missing |
| 🟡 Ready to bake | uv_normal present, no ALB file |
| 🔵 Baked | ALB present, no NOR file |
| 🟢 Ready | ALB + NOR + MAT_ present, all checks pass |
| 🔴 Outdated | fingerprint ≠ current |

**Fingerprint** (`fingerprint.py`): hash of the slot cameras and their image paths, shifts, Mode, Previous Bake, Original Scan value, slot count, a VCMix checksum (numpy sum of the mesh's own VCMix/VCMix2, cheap), and the object/mesh names.

### 7.3 Checks (`checks.py`), each an `Issue` shown in the panel
- **Scene:** other scenes present (count cameras/images, list objects shared with the active scene).
- **Names:**
  - object name not clean (`[^A-Za-z0-9_]`, `.001` suffix)
  - `MAT_`/`ALB_`/`NOR_` ≠ object name
  - duplicates after cleaning
  - files missing on disk
- **UV:** `uv_normal` missing; other real UV layers besides the scan UV / UV_cam* (can't be removed in GN); `uv_normal` outside 0–1 or overlapping (numpy check of the triangles, cached).
- **Color:** `Attribute` missing → Color comes from ALB (info, not an error).
- **Transform:** location/rotation/scale not identity-applied; origin not at bottom center (tolerance 1 mm); negative scale; mesh shared by several objects (can't be applied).
- **Mesh:** n-gons (warning, tangent export); triangle count shown.
- **Textures:** ALB 8-bit sRGB, NOR 16-bit Non-Color, 8K (from the stored bake info).
- **State:** outdated.

### 7.4 Fixes (`fixes.py`)
- **Rename objects:**
  - clean name: spaces and `-` runs → `_`, remove other illegal characters, collapse `_`, drop the `.001` suffix. `Model - 12 - Trashbin` → `Model_12_Trashbin`.
  - on conflict, append `_2`.
  - renames in one go: object, mesh data, `MCP_`, `MAT_`, images `ALB_`/`NOR_`, and the **files on disk** (+ `filepath` + reload).
  - camera_project finds its material by `material_name(obj)`, so the MCP_ material must be renamed before anything calls `ensure_material`.
- **Fix transforms:**
  - `mesh.transform(matrix_world)`, `matrix_world = identity`
  - pivot = (bbox center X, bbox center Y, bbox min Z)
  - `mesh.transform(Translation(-pivot))`, `location = pivot`
  - skip shared meshes (warning)
  - **verify:** custom normals (`custom_normal`) survive `mesh.transform`
  - **verify:** the projection is unchanged (compare evaluated `UV_cam1` before/after on a copy)
  - camera_project projects in world space, so it should be unaffected
- **Delete scene:** confirm dialog, the only popup.
  - Before removal, collect the IDs used **only** by that scene: its objects, their data, cameras' images, collections.
  - `bpy.data.scenes.remove(scene)`, then remove exactly those IDs (a targeted cleanup, not a global purge).
  - Objects also in the active scene are kept. The active scene can't be deleted.
- **Update outdated:** runs the ALB bake (+ NOR with the stored `nor_source_used`) for those objects only.
- **Add to EXPORT:** creates the `EXPORT` collection in the active scene if it's missing (`color_tag = 'COLOR_03'`) and links the selected meshes. Non-meshes are ignored and reported in the panel.

### 7.5 Export flow (`fbx.py`)
1. Objects = meshes in the active scene's `EXPORT` collection. Other scenes are ignored.
2. Store each object's Final state, then Final on for all of them, then `view_layer.update()`.
3. For each object, a **temporary copy**:
   - `bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True)` in a new object at the same transform
   - trim the material list to `[MAT_]`, material_index = 0
   - check that exactly `uv_normal` and `Color` are there
4. Textures: copy/save `ALB_`/`NOR_` into `<folder>/Textures/`. Skip files whose fingerprint and size are unchanged.
5. FBX with the verified settings:
   ```python
   bpy.ops.export_scene.fbx(filepath=..., use_selection=True, object_types={'MESH'},
       use_mesh_modifiers=False,  # the copies are already evaluated
       colors_type='SRGB', prioritize_active_color=False,
       path_mode='RELATIVE', embed_textures=False,
       apply_scale_options='FBX_SCALE_ALL', bake_space_transform=True,
       add_leaf_bones=False, use_tspace=True, mesh_smooth_type='OFF')
   ```
   - The temporary copies carry the **original object names** (rename the original temporarily, or name the copies correctly and export only the copies).
   - Option: one FBX per object.
6. Remove the temporary copies. Restore each object's Final state.
7. Write `<folder>/Editor/MCPTexturePostprocessor.cs` once (don't overwrite if it's unchanged) and `export_report.txt` (objects, stages, issues, timings).

### 7.6 Unity postprocessor (template)
```csharp
using UnityEditor;
public class MCPTexturePostprocessor : AssetPostprocessor {
    void OnPreprocessTexture() {
        if (!assetImporter.importSettingsMissing) return;   // first import only, keep dev changes
        var ti = (TextureImporter)assetImporter;
        var n = System.IO.Path.GetFileName(assetPath);
        if (n.StartsWith("ALB_")) { ti.sRGBTexture = true; ti.maxTextureSize = 8192; }
        if (n.StartsWith("NOR_")) { ti.textureType = TextureImporterType.NormalMap; ti.maxTextureSize = 8192; }
    }
    void OnPreprocessModel() {
        if (!assetImporter.importSettingsMissing) return;
        ((ModelImporter)assetImporter).materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
    }
}
```
Unity notes:
- URP Lit ignores vertex colors. `Color` only shows up with a Shader Graph.
- Budget about 85 MB per 8K texture (BC7 + mips).
- Blender and Unity both use OpenGL normals, so NOR needs no flip.

## 8. Builds: Lite / Full
- `build_extension.py --full`: add `wheels = ["./wheels/onnxruntime-…-win_amd64.whl", …]` and `platforms = ["windows-x64"]` to the manifest. Bundle the model under `modules/baking/normal/models/`. The Lite build excludes that folder and the wheels.
- Same extension ID for both builds.
- **Check first:** the current zip puts `manifest.toml` at the root and `__init__.py` in `MultiCamProject/`. Blender extensions expect them at the same level. Verify with `blender --command extension build` / validate, and fix.
- **Before bundling the model,** check the license of DeepBump's model weights (the code is GPL-3).
- Estimated size: Lite unchanged; Full +25–40 MB (onnxruntime CPU ~12–15 MB + model). DirectML GPU is much bigger, so only add it after measuring.

## 9. Order of work
| # | Phase | Result |
|---|---|---|
| 0 | camera_project: MCP_ rename, migration, `_scan_uv` | test file opens with MCP_ materials |
| 1 | Scaffold baking + export (skill), props | empty panels, toggles in Infos |
| 2 | GN-Final + Projection/Final toggle | flipping works, viewport faster with Final on |
| 3 | ALB bake + MAT_ | rebake overwrites, no .001 |
| 4 | NOR high-pass + preview/timing | Lite normal |
| 5 | Export: EXPORT collection, stages, checks, panel warnings | status list |
| 6 | Fixes: rename, transforms, scene delete, update outdated | |
| 7 | FBX export via temporary copies, textures, C#, report | re-import test passes the one-of-each rule |
| 8 | NOR mesh bake (+ smooth source) | |
| 9 | Full build: AI + Mesh+Albedo, `--full` build | user compares speed and quality |

After each phase: test in a background Blender (`-b --factory-startup`) with synthetic objects. Test live in the user's file only with read-only queries, or on copies. Commit on `main` only when asked (message via `-F` file).

## 10. Risks / things to verify
- The FBX exporter with `use_mesh_modifiers=False` on the temporary copies: object names, custom normals, tangents.
- A GN Image Texture at 8K (From ALB color): memory of the byte buffer (~256 MB). Only evaluated with Final on.
- An 8K bake with 1 sample on objects with 230k faces: check time and VRAM. Blender's interface freezes during the synchronous bake.
- `Delete scene`: images are shared by name only (`.jpg.001` are separate IDs), so the targeted removal gets them. Double-check that nothing the active scene uses is removed.
- Rename: the camera_project material pointer (`data(obj).material`) and GN inputs must follow the rename.

## 7.7 Fix + Export (user request 2026-09-25, supersedes "fixes are separate buttons only")
- **Export** first shows one confirm dialog (`autofix.plan`): what will be fixed and what cannot be.
- On OK it fixes automatically (`autofix.run`), in this order: rename objects, fix transforms,
  Smart UV `uv_normal` where missing (margin = 2 × bake margin; the active/render UV stay), then bake
  ALB + NOR for everything outdated, not baked, or with missing/wrong textures (normal source =
  the one used last; High-pass when that one cannot run, e.g. no High Poly).
- Not fixed, only warned (dialog + report): shared meshes, hand-made UVs outside 0–1 or
  overlapping, n-gons, extra UV maps, missing scan color, other scenes.
- **After the export every exported object stays in Final** (Blender shows what the FBX holds);
  nothing is applied, Projection brings the setup back. The report lists every automatic fix.

## 7.8 Name prefix + clean textures folders (user request 2026-09-25)
- `Scene.multicamproject_export.name_prefix` (saved in the .blend): objects must be `<prefix>_<clean name>`;
  MCP_/MAT_/ALB_/NOR_ and the texture files follow the object name. Checked (NAME issues) and
  fixed by Rename objects and Fix + Export (`fixes.rename_all`). Empty prefix = not required.
- Bake folder: `ALB_*`/`NOR_*` PNGs no image of the .blend uses -> panel warning + Clean button,
  removed by Fix + Export. `<export>/Textures/`: only the last export's textures stay.
  Files go to the Recycle Bin (SHFileOperation, FOF_ALLOWUNDO). **Only ALB_/NOR_ files are ever
  touched**: in the test file the bake folder is a shared team Google Drive textures folder.

## 7.9 Naming scheme + Lite/AI switch (user request 2026-09-25, replaces the 7.8 prefix rule)
- Name Prefix = `<code>_<scene>` (split at the first `_`). Object `<scene>.<##>_<name>`, MAT_/ALB_/NOR_
  `<type>_<code>_<object>`, FBX `ENV_<code>_<scene>.fbx`; FBX Name field removed. `baking/naming.py`.
- `##`: kept when valid (also from another scene's form, or an older `..._##_Name`), else the next
  free number. Two-phase rename when names swap. Duplicates release the original's bake pointers.
- Live: msgbus on Object.name + prefix update -> timer -> `fixes.rename_all` (export/live.py). The
  status list shows `<scene>.<##>_` greyed and edits only `<name>` (Object.multicamproject_short_name).
- Normal Map section: Lite (High-pass) | AI switch, per source/size timings (`nor_times`), Set up AI
  (pip onnxruntime --target user modules, --no-deps without numpy; model -> datafiles/multicamproject/models).
