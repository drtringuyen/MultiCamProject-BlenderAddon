# Hand-off: PolyCut + Surface Cut (2026-11-06)

Start here in a new session. Everything below is merged to `main`.

## What exists
MultiCamProject add-on, `addons/MultiCamProject/modules/remesh/`:

| File | What |
|---|---|
| `surface_cut.py` | **Surface Cut** (Edit Mode, **K**; Blender's Knife is **Shift+K**). Walks the cut face to face over the surface; preview; real cut; one island per closed loop. |
| `poly_cut.py` | **PolyCut** (Sculpt tool, **Ctrl+Shift+K**): view-projected cut (Exact boolean through the mesh, or Knife Project front-only). Owns the shared toggles + "last cut" memory used by both tools. |
| `core.py`, `gn_remesh.py`, `cutter.py` | PolyCut's boolean cut (`MCP-PolyCut` GN group, `mcp_cut_edges` edge attribute). |
| `uv_mesh/operators.py` | Edit Mode right-click: Face select -> Clear Seam / Boundary as Seam; Vertex select -> Selected as Seam. |
| `ui.py`, `tool.py` | 03 Mesh Editing row and the Sculpt tool header. |

## Surface Cut design (agreed with the user)
- Straight **on the surface**, not on screen: plane through two points, normal from the two face
  normals (sum; difference when they face away - top/underside of a leaf). Several planes tried,
  shortest arriving path kept; detours (> 2x the edge route) rejected; then a steered walk
  (re-aimed per face); then along the open rim; last resort along existing edges.
- The first click **locks the piece** (connected part); later clicks always land on it.
- Snapping always on (vertex, else edge, else face). Shift = grey closing preview (only while
  held). Backspace / Ctrl+Z = drop last point. Red = hidden from this view (occluded or
  back-facing).
- Cut: crossed edges split (all crossings of an edge at once), faces split with
  `bmesh.utils.face_split` (a click inside a face becomes a vertex); tiny gaps bridged over
  existing edges; island = flood fill not crossing the cut, the side the cut runs all along,
  smaller by area; outline = exactly the island's border.

## Shared toggles (scene props `multicamproject_polycut_*`)
Row order: **Edges · Faces · Invert · Auto Seam · Close Border · [PolyCut button] · Cut Through ·
Clear Face Sets · Clean Floating** (Cut Through = PolyCut only). Edges / Faces are exclusive. Each also applies to the **last cut**
when switched afterwards. Last cut remembered as mesh attributes until the next cut:
`mcp_cut_edges` (outline), `mcp_cut_inside` (island faces), `mcp_cut_rim` (open-rim stretch),
`mcp_cut_piece` (the piece). Reset Add-on Data removes them. No toggle -> Set Seam / No Seam popup.

## Tested (on copies of the user's Plant scan, ENV_11_DG_LvRoom.02_Plant, Plant.blend)
- Surface Cut, realistic screen clicks: 20/20 closed loops -> one island, 0 gaps / loose ends /
  branches, mesh valid. Toggles after a cut (Faces, Invert, Auto Seam) verified.
- PolyCut closed through-cut: seam loop closed in 10/10 shapes.

## Open / to check
- **Close Border** never hit a real open rim in tests - try a cut across a leaf's open edge.
- PolyCut started in Edit Mode had known issues (that path was replaced by Surface Cut on K).
- Long segments (30+ faces apart) fall back to edges more often - click more points.
- `main` also has `44aabce` (Generate Normal: all faces smooth before the bake), committed by
  another session in parallel; merged together in `62832fc`.

## Workflow notes
- Install = copy `addons/MultiCamProject` to Blender 5.2's `scripts/addons`, then disable /
  purge `sys.modules` / enable (what `install.py` does; it can also be done from Blender's
  Python via the Blender MCP).
- User preferences: proposal / confirmation before a large build; a small sample first; straight
  apostrophes only. The user tests each step in Blender before the next.
