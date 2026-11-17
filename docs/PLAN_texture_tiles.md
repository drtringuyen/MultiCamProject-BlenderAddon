# Texture Sets: one work file, one mesh, several final textures

## Context
The 30st Bedroom bed (`Bed_nightstand`) is too big for one 8K ALB_/NOR_. The user wants to keep **one work file and one mesh**, so vertices stay welded across the cut lines and there is only one Decimate. Only the **final** textures should be split, into 3 sets: Bed, Nightstand and Clothes.

The user proposed this design:
- Split Parts gets a "separate texture" toggle.
- `uv_normal` stays for processing (projection, BAo_ and mask mixing).
- A new `uv_final` holds the per-part layout for the final textures.
- The Bake view shows `uv_final` with the right textures and materials.
- Export splits the mesh, keeps only `uv_final` (renamed to `uv_normal`), and wires the materials and textures.

## Review of the user's design

**Good, and it fits the code:**
- **One mesh, one Decimate, one work file:** yes. The cut lines stay welded until export.
- **Projection painting costs the same as today.** It stays on one MCP_ with 1K MSK_/CMX1_/CMX2_ on `uv_normal`, and a stroke only touches the active object (`paint_sync.py:56`). There are not 3 projections to paint: only the final bakes run 3 times.
- **Photos keep their detail.** ALB_ is an EEVEE "uv_render": the mesh is flattened to a UV and MCP_ is rendered (`baking/engine.py:363`). MCP_ reads the photos through `UV_cam{i}` at full photo resolution, so rendering through `uv_final` gives each set its own full-detail 8K.
- **Masks are fine at low resolution.** MSK_/CMX are smooth 1K weights, so `uv_normal` is fine for them.

**Flaws found, and the fix for each:**
1. **BAo_ is the bottleneck.** BAo_ (the original scan colour, used in the Original and Mixed routes) is baked once on `uv_normal` at the object's `tex_size`. MCP_ samples it through `uv_normal` (`camera_project/core.py:455-476`). Rendering 3 sets from it would only upscale the scan parts by 3x and add no detail.
   - BAp_, BNo_ and BNp_ have the same problem.
   - **Fix:** the Final bake also bakes BAo_ (and BNo_ when Bake from Scan is used) **per set onto `uv_final`**. During each set's ALB_ render, MCP_ is temporarily repointed to the set's BAo_ through `uv_final`.
   - `uv_normal` processing can then drop to 4K to save RAM.
2. **`uv_normal` is hardcoded about 270 times in 45 files.** GN-Final keeps only `uv_normal`, MAT_ reads `uv_normal`, export `_trim` deletes every other UV (`export/fbx.py:106`), and Seam Fix Apply makes `uv_new` the only UV (`seam_fix/files.py:118`), which would wipe `uv_final`.
   - **Fix:** don't touch those places. When Final is on, a GN-Final step writes `uv_final`, with each set's tile offset removed, **into the `uv_normal` attribute of the evaluated mesh**. MAT_, export `_trim` and `split.pieces` then keep working unchanged.
   - Seam Fix Apply gets a guard so it keeps `uv_final`.
3. **The image tools and the stamps assume one ALB_.** Single pointers in `MULTICAMPROJECT_BakeData` (`baking/props.py:159`) are used by Inpaint/Patch, Seam Level/Blur, tex_override, Check Project and Receive manifests. The fingerprint hashes only `uv_normal` (`fingerprint.py:65,194`).
   - **Fix:** set 0, the main set, keeps the existing pointers, so nothing changes for normal objects.
   - Extra sets live in a new collection, `multicamproject_bake.texture_sets`.
   - In phase 1 the per-set image tools (Inpaint, Patch, Seam Level, tex_override) are hidden on extra sets. They come later.
4. **Split Parts overlap rule.** Today the first part by name wins on overlap. A texture set needs exactly one set per face, so overlap becomes an error (not a warning) for parts with "Own texture" on.
5. **Shading seam at the export cut** (this also affects Split Parts today). Splitting by face recomputes the normals on each piece, so the cut edge can shade hard in Unity.
   - **Fix:** take the custom split normals from the whole mesh before `split.pieces`.
6. **Memory.** 3 × 8K ALB_ + NOR_ + BAo_ live in RAM at bake time.
   - **Fix:** bake one set at a time and free each set's images after saving. The Preview 2K cap keeps the view light.

## Design (user decisions 2026-11-17: one tile per texture, a per-object switch, tiles chosen by the user)

### Tiles mode (per object)
- A **Texture Tiles** switch on the object, in the Split Parts block and in 09. Export.
- With it on, the block shows a **tile list**: + / − / rename / reorder, each with a colour. Every Split Part row gets a **Tile** dropdown.
  - Several parts can share one tile.
  - Faces in no part go to tile 1, "Main".
- **Switching it on:**
  - starts with one tile for each Split Part, plus "Main" if any faces are left over;
  - builds `uv_final` at once;
  - the user then merges or moves parts between tiles with the dropdowns.
- **Tile = texture set:**
  - Tile 1 keeps today's names (MAT_/ALB_/NOR_ `<obj>`) and pointers.
  - Tile k > 1 is `<obj>_<TileName>`, stored in a new `multicamproject_bake.texture_tiles` collection: name, colour, images, stamps.
  - The part → tile map is a collection keyed by part name, so it survives a renamed `SPLIT_` attribute through a rename hook in `split.py`.
- **`uv_final` is automatic:**
  - It is rebuilt whenever the tiles change: the switch is turned on, a part's tile changes, tiles are added or removed, or the parts' faces change. A short timer debounces the rebuilds.
  - Each tile's faces get Smart UV (reuse `export/autofix.make_uv_normal` on a temporary per-tile copy), packed into u ∈ [k−1, k].
  - A rebuild changes the `uv_final` hash, so that tile's bakes read Outdated.
  - Only tiles whose faces changed are re-unwrapped, so the other tiles keep their layout and their bakes stay current.
- **Switching it off** keeps `uv_final` and the tile data (hidden) until Clean, so switching back on loses nothing.

### GPU drawing of the tiles
- **UV Editor overlay** (`gpu` + `blf` draw handler, the same kind of handler as the Seams tint):
  - every tile square side by side, outlined in its colour with its name and image size;
  - the `uv_final` wireframe of each tile's faces, tinted with the tile colour;
  - fill-rate % per tile, to show wasted texture space.
- **3D viewport overlay** in Tiles mode: faces tinted by tile colour. It is toggled from the tile list, so it's easy to see which part sits on which texture.

### Export naming
- Textures and MAT_ follow the tile name (`..._<obj>_<TileName>`).
- FBX children still follow the Split Parts.
- Each child gets the MAT_ of its tile. Parts that share a tile share one MAT_ and its textures in Unity.
- **Final bake loop**, one set at a time, in `baking/engine.py`:
  - **BAo_/BNo_:** Cycles selected-to-active onto a temporary copy of that set's faces, with `uv_final` shifted to 0-1 and made `uv_normal`. Reuse `_bake_from_source`.
  - **ALB_:** `_uv_render` with `_flat_group` reading `uv_final`, the ortho camera on tile k, and the other sets' faces deleted in the temporary GN. MCP_ is repointed to the set's BAo_.
  - **NOR_:** high-pass of the set's ALB_ (`normal/__init__.generate_steps`, per image).
  - **Stamps:** hash `uv_final` and the set's face selection as well.
- **Bake view:** GN-Final gets the remap step (flaw 2) and a Set Material per set from its `SPLIT_` attribute, so each set shows its own MAT_ on the one mesh.
  - GN-Final is user-owned: add it as a small group node and don't rebuild the user's layout.
- **Export** (`export/fbx.py` make_copies and `split.pieces`):
  - textured parts become children with their own MAT_ copy;
  - `copy_textures` copies every set's ALB_/NOR_;
  - `_trim` keeps one material per piece;
  - Unity gets plain `uv_normal`.
- **Linking/Receive and Check Project:** the set images join the manifests and owned lists (`linking/core.py:60,1206-1336`, `linking/check.py:270`).

## BAo_ at full resolution per tile (live rebake inside Final bake)
- Today's BAo_ on `uv_normal` stays the **processing** copy: preview, mask painting, the Mixed view. It can drop to 4K.
- **Final bake, for each tile k:**
  1. **BAo_k:** Cycles Bake from Source (`engine._bake_from_source`). The ORIGINAL high-poly scan with its own scan textures is baked, selected-to-active, onto a temporary copy of tile k's faces whose `uv_final` is shifted to 0-1.
     - The result is a fresh 8K BAo_ for that tile only, so the scan regions get up to 3x the old density.
     - The same applies to BNo_k when NOR_ uses Bake from Scan.
  2. **ALB_k:** EEVEE `_uv_render`, flattened on `uv_final` tile k. The MCP_ material is temporarily rewired so that:
     - the BAo_ node reads **BAo_k through `uv_final`**;
     - photos still come through `UV_cam{i}` at full photo resolution;
     - MSK_/CMX weights still come through `uv_normal` (smooth 1K masks, so their resolution doesn't matter).
     - The mix is therefore recomputed at full resolution and nothing is upscaled.
  3. **NOR_k:** high-pass of ALB_k.
  4. Save BAo_k, ALB_k and NOR_k, then free them from RAM before tile k+1.
- **Limit:** detail can't exceed what the source scan textures hold. Before building, check how many and how large the bed sub-scan's own textures are (the Room1_Bed_MaterialN images). Several 8K source maps mean a real gain; a single 8K source would cap it.
- **Time:** about 22-52 s per BAo_ at 8K (measured 2026-11-16), so about 3x for the bed.

## Way of working
- **New branch `texture-tiles`.** The user asked for it explicitly, which overrides the usual work-on-main rule.
- **Development and tests run headless on COPIES** of the bed work file. The user's running Blender is NOT reloaded with branch code.
- Merge into main and install only when the user says so, and check `jobs.busy()` first.
- **Meanwhile the user prepares the bed work file on the current main build:**
  - Split Parts tags: Bed / Nightstand / Clothes, no overlaps (use Select Overlaps / Unassigned);
  - Decimate;
  - projection and mask painting.
  - All of this carries over. Don't Finalize or Export the bed until the feature is merged.

## Phases
1. The Texture Tiles switch, the tile list and the per-part Tile dropdown, plus:
   - automatic `uv_final` (only changed tiles re-unwrapped);
   - the GPU overlays in the UV Editor and the 3D viewport;
   - the Seam Fix guard;
   - overlap as an error in Tiles mode.
2. Per-set Final bake (BAo_, ALB_, NOR_) and stamps.
3. GN-Final remap + per-set MAT_ in the Bake view.
4. Export children with their own MAT_/textures, plus normals carried across the cut.
5. Receive, Check Project and owned lists.
6. Later: per-set Inpaint/Patch/Seam Level/tex_override (a set picker in the image editor).

## What the user can do in the work file now
- Tag Bed / Nightstand / Clothes with Split Parts.
- Decide and apply the Decimate.
- Do the projection painting and mask work on `uv_normal`.

All of this stays valid. Don't Finalize or Export the bed until phase 4. BAo_ is rebaked per set anyway.

## Verification
- Run headless on a copy of the bed work file (never the original, and check `jobs.busy()` before `install.py`):
  - 3 sets;
  - the Final bake writes 3 ALB_/NOR_ at 8K;
  - scan regions at 3x the old detail (compare a crop with the BAo_ on `uv_normal`).
- Bake view: each set shows the right texture and there are no gaps at the cut.
- FBX round trip: 3 children, each with only `uv_normal`, its own MAT_ and textures, and the vertex positions matching the source along the cut.
- Receive in a copy of the main file: the sets' images are relinked, and Check Project is clean.
