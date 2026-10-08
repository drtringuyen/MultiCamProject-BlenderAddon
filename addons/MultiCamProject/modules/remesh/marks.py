"""02 Select & Set: what happens to a face region, and the overlay that previews it.

  Projected   -> VCMix and VCMix2 alpha = 1 on the region (the projection shows)
  Baked       -> both alphas = 0 (BA_, baked from the Bake Source, shows)
  Protect     -> vertex group vg_Protect = 1 (the Decimate leaves it as it is)
  Unprotect   -> out of vg_Protect
  Delete      -> the faces are deleted right away
  Close Hole  -> deleted, the hole filled with one cap, the cap triangulated
  Seam only   -> nothing but the seam option
  Clear all Face Sets -> the whole mesh back to one face set (the region does not matter)

Seam: the region's outline gets a UV seam (Mark), loses it (Clear) or is left alone.
The dialog only previews (overlay); the write happens on Set. Object Mode writes through
the mesh API (Sculpt Mode goes there for the write), Edit Mode through bmesh (its undo
records all of it).
"""
import bmesh
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils.bvhtree import BVHTree

from ..camera_project import gn_builder
from . import core, workflow as wf

ACTIONS = (('PROJECTED', "Projected", "VCMix + VCMix2 alpha = 1: the camera projection shows "
            "on these faces", 'CAMERA_DATA', 0),
           ('BAKED', "Baked", "VCMix + VCMix2 alpha = 0: BA_ (baked from the Bake Source) "
            "shows on these faces", 'TEXTURE', 1),
           ('PROTECT', "Protect from Decimate", "Vertex group vg_Protect = 1: the Decimate "
            "keeps these faces as they are (new, clean geometry)", 'LOCKED', 2),
           ('UNPROTECT', "Unprotect", "Out of vg_Protect: the Decimate reduces them again",
            'UNLOCKED', 3),
           ('DELETE', "Delete", "Delete these faces now", 'TRASH', 4),
           ('CLOSE_HOLE', "Close Hole", "Delete these faces, fill the hole they leave with one "
            "cap and triangulate it (the seam option goes on the cap's outline)", 'MOD_TRIANGULATE', 7),
           ('SEAM_ONLY', "Seam only", "Only the seam option below", 'MOD_UVPROJECT', 5),
           ('CLEAR_FACE_SETS', "Clear all Face Sets", "The whole mesh back to one face set "
            "(every PolyCut region and Sculpt face set goes; seams stay)", 'FACE_MAPS', 6))

SEAMS = (('MARK', "Mark Seam", "The outline becomes a UV seam"),
         ('CLEAR', "Clear Seam", "The outline's UV seam goes"),
         ('KEEP', "Leave", "Seams stay as they are"))

COLORS = {'PROJECTED': (0.2, 0.55, 1.0), 'BAKED': (0.9, 0.6, 0.2), 'PROTECT': (0.3, 0.9, 0.4),
          'UNPROTECT': (0.7, 0.7, 0.7), 'DELETE': (1.0, 0.2, 0.2), 'SEAM_ONLY': (1.0, 0.3, 0.9),
          'CLOSE_HOLE': (1.0, 0.55, 0.1),
          'CLEAR_FACE_SETS': (0.6, 0.6, 0.6)}

MASK_ACTIONS = {'PROJECTED': 1.0, 'BAKED': 0.0}

_last_face_set = {}     # mesh name -> the face set picked or cut last


def last_face_set(obj):
    return _last_face_set.get(obj.data.name, 0)


def remember_face_set(obj, fs):
    _last_face_set[obj.data.name] = fs


def needs_paint_layers(obj, action):
    """Projected / Baked write the mesh's own VCMix layers: they must exist (Object Mode
    makes them with camera_project.core.ensure_paint_layer)."""
    if action not in MASK_ACTIONS:
        return False
    ca = obj.data.color_attributes
    return ca.get(gn_builder.LAYERS[0]) is None


def mask_problem(obj, action):
    if action in MASK_ACTIONS and not (hasattr(obj, "multicamproject_cam")
                                       and obj.multicamproject_cam.is_setup):
        return "Projected / Baked need the camera projection (0B)"
    return ""


# ---------------------------------------------------------------- mesh topology (numpy)

def _loop_faces(me):
    totals = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", totals)
    return np.repeat(np.arange(len(me.polygons), dtype=np.int32), totals)


def _loop_array(me, prop):
    a = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get(prop, a)
    return a


def boundary_edges(me, faces):
    """Edges between the region and the rest (not the mesh's own open border)."""
    loop_edge = _loop_array(me, "edge_index")
    inside = faces[_loop_faces(me)]
    n = len(me.edges)
    tot = np.bincount(loop_edge, minlength=n)
    ins = np.bincount(loop_edge[inside], minlength=n)
    return (ins > 0) & (ins < tot)


def face_mask(obj, face_set):
    """Faces of `face_set` in the mesh (Object or Sculpt Mode)."""
    return core._face_sets(obj.data) == face_set


def _set_alpha_object(me, faces, value):
    """VCMix / VCMix2 alpha (and paint_sync's stash) = value on the region."""
    lf = _loop_faces(me)
    corner = faces[lf]
    point = np.zeros(len(me.vertices), bool)
    point[np.unique(_loop_array(me, "vertex_index")[corner])] = True
    sel = {'CORNER': corner, 'POINT': point}
    for name in gn_builder.LAYERS:
        a = me.color_attributes.get(name)
        if a is None or a.domain not in sel:
            continue
        buf = np.empty(len(a.data) * 4, np.float32)
        a.data.foreach_get("color", buf)
        buf[3::4][sel[a.domain]] = value
        a.data.foreach_set("color", buf)
    st = me.attributes.get(gn_builder.MASK2_STASH)
    if st is not None and st.domain in sel and st.data_type == 'FLOAT':
        v = np.empty(len(st.data), np.float32)
        st.data.foreach_get("value", v)
        v[sel[st.domain]] = value
        st.data.foreach_set("value", v)


def apply_object_mode(obj, faces, action, seam):
    """Do `action` on the faces (bool array) in Object Mode. Returns the face count."""
    me = obj.data
    n = int(faces.sum())
    if not n:
        return 0
    if seam != 'KEEP':
        seams = np.empty(len(me.edges), bool)
        me.edges.foreach_get("use_seam", seams)
        seams[boundary_edges(me, faces)] = seam == 'MARK'
        me.edges.foreach_set("use_seam", seams)
    if action in MASK_ACTIONS:
        _set_alpha_object(me, faces, MASK_ACTIONS[action])
    elif action in {'PROTECT', 'UNPROTECT'}:
        vg = obj.vertex_groups.get(wf.VG_PROTECT) or obj.vertex_groups.new(name=wf.VG_PROTECT)
        verts = np.unique(_loop_array(me, "vertex_index")[faces[_loop_faces(me)]]).tolist()
        if action == 'PROTECT':
            vg.add(verts, 1.0, 'REPLACE')
        else:
            vg.remove(verts)
    elif action in {'DELETE', 'CLOSE_HOLE'}:
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        region = [bm.faces[i] for i in np.flatnonzero(faces).tolist()]
        if action == 'DELETE':
            bmesh.ops.delete(bm, geom=region, context='FACES')
        else:
            close_hole(bm, region)
        bm.to_mesh(me)
        bm.free()
    me.update()
    return n


def clear_face_sets(obj):
    """Object Mode: every face back in one face set (.sculpt_face_set and its GN copy
    face_set go). Returns the face count."""
    from . import gn_remesh
    me = obj.data
    for name in (core.SCULPT_FACE_SET, gn_remesh.FACE_SET):
        a = me.attributes.get(name)
        if a is not None:
            me.attributes.remove(a)
    _last_face_set.pop(me.name, None)
    core.sync_face_sets(obj)
    me.update()
    return len(me.polygons)


def clear_face_sets_edit(bm):
    """Edit Mode: the face set layer goes (Sculpt then shows one face set)."""
    lay = bm.faces.layers.int.get(core.SCULPT_FACE_SET)
    if lay is not None:
        bm.faces.layers.int.remove(lay)
    return len(bm.faces)


# ---------------------------------------------------------------- Edit Mode (bmesh)

def edit_region(bm):
    return [f for f in bm.faces if f.select]


def _edit_boundary(faces):
    region = set(faces)
    return [e for f in faces for e in f.edges
            if len(e.link_faces) > 1 and any(g not in region for g in e.link_faces)]


def _set_alpha_edit(obj, bm, faces, value):
    for name in gn_builder.LAYERS:
        a = obj.data.color_attributes.get(name)
        if a is None:
            continue
        if a.domain == 'CORNER':
            lay = bm.loops.layers.float_color.get(name) or bm.loops.layers.color.get(name)
            items = [loop for f in faces for loop in f.loops]
        else:
            lay = bm.verts.layers.float_color.get(name) or bm.verts.layers.color.get(name)
            items = list({v for f in faces for v in f.verts})
        if lay is None:
            continue
        for it in items:
            c = it[lay]
            c[3] = value
            it[lay] = c
    lay = bm.loops.layers.float.get(gn_builder.MASK2_STASH)
    if lay is not None:
        for f in faces:
            for loop in f.loops:
                loop[lay] = value
    lay = bm.verts.layers.float.get(gn_builder.MASK2_STASH)
    if lay is not None:
        for v in {v for f in faces for v in f.verts}:
            v[lay] = value


def apply_edit_mode(obj, bm, faces, action, seam):
    """Do `action` on the selected faces of the edit bmesh. Returns the face count."""
    if not faces:
        return 0
    n = len(faces)
    if seam != 'KEEP':
        for e in _edit_boundary(faces):
            e.seam = seam == 'MARK'
    if action in MASK_ACTIONS:
        _set_alpha_edit(obj, bm, faces, MASK_ACTIONS[action])
    elif action in {'PROTECT', 'UNPROTECT'}:
        vg = obj.vertex_groups.get(wf.VG_PROTECT) or obj.vertex_groups.new(name=wf.VG_PROTECT)
        index = [f.index for f in faces]
        deform = bm.verts.layers.deform.verify()        # frees earlier BMFace references
        bm.faces.ensure_lookup_table()
        for v in {v for i in index for v in bm.faces[i].verts}:
            if action == 'PROTECT':
                v[deform][vg.index] = 1.0
            elif vg.index in v[deform].keys():
                del v[deform][vg.index]
    elif action == 'DELETE':
        bmesh.ops.delete(bm, geom=faces, context='FACES')
    elif action == 'CLOSE_HOLE':
        for f in close_hole(bm, faces):
            f.select_set(True)          # the new cap stays selected
    return n


def close_hole(bm, faces):
    """Delete the faces, fill the hole they leave (one cap per closed outline) and
    triangulate the cap. A part of the outline on the mesh's open border stays open.
    Returns the cap's triangles."""
    region = set(faces)
    outline = list({e for f in faces for e in f.edges
                    if any(g not in region for g in e.link_faces)})
    bmesh.ops.delete(bm, geom=list(faces), context='FACES')
    outline = [e for e in outline if e.is_valid]
    if not outline:
        return []
    caps = bmesh.ops.holes_fill(bm, edges=outline, sides=0)["faces"]
    if not caps:
        return []
    return bmesh.ops.triangulate(bm, faces=caps, quad_method='BEAUTY',
                                 ngon_method='BEAUTY')["faces"]


def edit_inside_loop(bm):
    """Edit Mode, no face selected but a closed ring of edges: the faces on its smaller side
    (flood fill that does not cross a selected edge) get selected. Returns them, [] when the
    edges do not close a region."""
    ring = {e for e in bm.edges if e.select}
    if not ring:
        return []
    # all regions grow one face per turn: the first one that runs out is the smallest, and
    # the rest of a big scan is never walked. Regions that meet are merged.
    owner, parts, todo = {}, {}, {}
    for i, f in enumerate({f for e in ring for f in e.link_faces}):
        owner[f], parts[i], todo[i] = i, [f], [f]
    inside = None
    while inside is None and len(parts) > 1:
        for i in list(parts):
            if i not in parts:
                continue                    # merged this turn
            if not todo[i]:
                inside = parts[i]
                break
            f = todo[i].pop()
            for e in f.edges:
                if e in ring:
                    continue
                for g in e.link_faces:
                    j = owner.get(g)
                    if j is None:
                        owner[g] = i
                        parts[i].append(g)
                        todo[i].append(g)
                    elif j != i:            # the same region: j joins i
                        for h in parts[j]:
                            owner[h] = i
                        parts[i] += parts.pop(j)
                        todo[i] += todo.pop(j)
    if inside is None:
        return []           # the ring is open: both sides are one region
    for f in inside:
        f.select_set(True)
    bm.select_flush(True)
    return inside


def edit_select_face_set(bm, face_set):
    """Exactly the faces of `face_set` selected. Deselect everything first: deselecting a
    face after would also drop the border vertices it shares with the set."""
    lay = bm.faces.layers.int.get(core.SCULPT_FACE_SET)
    for f in bm.faces:
        f.select_set(False)
    for f in bm.faces:
        if (f[lay] if lay is not None else 1) == face_set:
            f.select_set(True)
    bm.select_flush(True)


# ---------------------------------------------------------------- picking (BVH)

_bvh = {}       # mesh name -> (key, BVHTree)


def _mesh_key(me):
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return (len(me.vertices), len(me.polygons), float(co.sum()))


def mesh_bvh(me):
    """A BVH of the base mesh (not the evaluated one: its face indices are the mesh's).
    Rebuilt when the mesh changes (a cut, sculpting)."""
    key = _mesh_key(me)
    hit = _bvh.get(me.name)
    if hit is not None and hit[0] == key:
        return hit[1]
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    verts = co.reshape(-1, 3).tolist()
    starts = np.empty(len(me.polygons), np.int32)
    totals = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", totals)
    lv = _loop_array(me, "vertex_index").tolist()
    polys = [lv[s:s + t] for s, t in zip(starts.tolist(), totals.tolist())]
    tree = BVHTree.FromPolygons(verts, polys, all_triangles=False)
    _bvh[me.name] = (key, tree)
    return tree


def pick_face(obj, origin, direction, bm=None):
    """The face index under a world-space ray, or None."""
    inv = obj.matrix_world.inverted()
    o = inv @ origin
    d = (inv.to_3x3() @ direction).normalized()
    tree = BVHTree.FromBMesh(bm) if bm is not None else mesh_bvh(obj.data)
    _loc, _nor, index, _dist = tree.ray_cast(o, d)
    return index


# ---------------------------------------------------------------- overlay

_overlay = {"handle": None, "tris": None, "lines": None, "action": 'PROJECTED'}


def _overlay_mesh(obj, faces):
    me = obj.data
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    tv = np.empty(n * 3, np.int32)
    tp = np.empty(n, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    me.loop_triangles.foreach_get("polygon_index", tp)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = _world(obj, co.reshape(-1, 3))
    tris = co[tv.reshape(-1, 3)[faces[tp]].ravel()]
    ev = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    lines = co[ev.reshape(-1, 2)[boundary_edges(me, faces)].ravel()]
    return tris, lines


def _overlay_bmesh(obj, bm, faces):
    region = set(faces)
    mw = obj.matrix_world
    tris = [mw @ loop.vert.co for tri in bm.calc_loop_triangles() if tri[0].face in region
            for loop in tri]
    lines = [mw @ v.co for e in _edit_boundary(faces) for v in e.verts]
    return np.array(tris, np.float32).reshape(-1, 3), np.array(lines, np.float32).reshape(-1, 3)


def _world(obj, co):
    m = np.array(obj.matrix_world, np.float32)
    return co @ m[:3, :3].T + m[:3, 3]


def _draw():
    tris, lines = _overlay["tris"], _overlay["lines"]
    if tris is None:
        return
    r, g, b = COLORS.get(_overlay["action"], (1, 1, 1))
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(False)
    if len(tris):
        sh = gpu.shader.from_builtin('UNIFORM_COLOR')
        sh.uniform_float("color", (r, g, b, 0.35))
        batch_for_shader(sh, 'TRIS', {"pos": tris}).draw(sh)
    if len(lines):
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        sh.uniform_float("lineWidth", 3.0)
        sh.uniform_float("color", (r, g, b, 1.0))
        batch_for_shader(sh, 'LINES', {"pos": lines}).draw(sh)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def overlay_show(obj, faces, action, bm=None):
    overlay_hide()
    if bm is not None:
        _overlay["tris"], _overlay["lines"] = _overlay_bmesh(obj, bm, faces)
    else:
        _overlay["tris"], _overlay["lines"] = _overlay_mesh(obj, faces)
    _overlay["action"] = action
    _overlay["handle"] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    _redraw()


def overlay_action(action):
    _overlay["action"] = action
    _redraw()


def overlay_hide():
    if _overlay["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_overlay["handle"], 'WINDOW')
    _overlay.update(handle=None, tris=None, lines=None)
    _redraw()


def _redraw():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def unregister():
    overlay_hide()
    _bvh.clear()
