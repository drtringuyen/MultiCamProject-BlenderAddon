"""Set Faces: what a face region is marked as, and the overlay that previews it.

  High Density -> vertex group vg_HighRes = 1 (the Decimate Selective modifier reads it)
  Delete Geo   -> face BOOL remesh_delete   (for GN-Remesh)
  To Separate  -> face BOOL remesh_detach   (marked only, GN-Remesh decides)
  Clear        -> none of them

The options are exclusive: a region is cleared before it gets its new mark. The region's
boundary can become a UV seam. Object Mode writes through the mesh API (Sculpt Mode goes
there for the write), Edit Mode through bmesh (its undo records all of it).
"""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils.bvhtree import BVHTree

from . import core, workflow as wf

ACTIONS = (('HIGH_DENSITY', "High Density", "Keep more detail: vertex group vg_HighRes = 1 "
            "(the Decimate Selective modifier reads it)", 'MOD_DECIM', 0),
           ('DELETE', "Delete Geo", "Face attribute remesh_delete (GN-Remesh deletes it)",
            'TRASH', 1),
           ('SEPARATE', "To Separate", "Face attribute remesh_detach (marked only - GN-Remesh "
            "decides what happens)", 'MOD_EXPLODE', 2),
           ('CLEAR', "Clear", "Remove the marks from the faces, and their boundary seam when "
            "Mark boundary as seam is on", 'X', 3))

COLORS = {'HIGH_DENSITY': (0.2, 0.55, 1.0), 'DELETE': (1.0, 0.2, 0.2),
          'SEPARATE': (1.0, 0.7, 0.1), 'CLEAR': (0.7, 0.7, 0.7)}

_last_face_set = {}     # mesh name -> the face set picked or cut last


def last_face_set(obj):
    return _last_face_set.get(obj.data.name, 0)


def remember_face_set(obj, fs):
    _last_face_set[obj.data.name] = fs


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


def _bool_attr(me, name):
    a = me.attributes.get(name)
    if a is not None and (a.domain != 'FACE' or a.data_type != 'BOOLEAN'):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, 'BOOLEAN', 'FACE')
    return a


def _in_group(vg, index):
    try:
        return vg.weight(index) > 0.0
    except RuntimeError:            # not in the group
        return False


def apply_object_mode(obj, faces, action, mark_seam):
    """Write the marks for the faces (bool array) in Object Mode."""
    me = obj.data
    if not faces.any():
        return 0
    loop_vert = _loop_array(me, "vertex_index")
    lf = _loop_faces(me)
    verts = np.unique(loop_vert[faces[lf]])

    vg = obj.vertex_groups.get(wf.VG_HIGHRES) or obj.vertex_groups.new(name=wf.VG_HIGHRES)
    # clear: a vertex keeps its weight when a High Density face outside the region uses it
    # (only the faces touching the region are checked)
    in_region = np.zeros(len(me.vertices), bool)
    in_region[verts] = True
    ring = ~faces & (np.bincount(lf, weights=in_region[loop_vert], minlength=len(faces)) > 0)
    kept = np.zeros(len(me.vertices), bool)
    if ring.any():
        starts = np.empty(len(me.polygons), np.int32)
        totals = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get("loop_start", starts)
        me.polygons.foreach_get("loop_total", totals)
        weighted = {}
        for fi in np.flatnonzero(ring).tolist():
            fv = loop_vert[starts[fi]:starts[fi] + totals[fi]].tolist()
            for i in fv:
                if i not in weighted:
                    weighted[i] = _in_group(vg, i)
            if all(weighted[i] for i in fv):
                kept[fv] = True
    vg.remove([int(i) for i in verts if not kept[i]])
    target = {'DELETE': wf.ATTR_DELETE, 'SEPARATE': wf.ATTR_DETACH}.get(action)
    for name in (wf.ATTR_DELETE, wf.ATTR_DETACH):
        if name in me.attributes or name == target:
            a = _bool_attr(me, name)
            vals = np.zeros(len(me.polygons), bool)
            a.data.foreach_get("value", vals)
            vals[faces] = False
            a.data.foreach_set("value", vals)

    if action == 'HIGH_DENSITY':
        vg.add([int(i) for i in verts], 1.0, 'REPLACE')
    elif action in {'DELETE', 'SEPARATE'}:
        a = _bool_attr(me, wf.ATTR_DELETE if action == 'DELETE' else wf.ATTR_DETACH)
        vals = np.zeros(len(me.polygons), bool)
        a.data.foreach_get("value", vals)
        vals[faces] = True
        a.data.foreach_set("value", vals)

    if mark_seam:
        seams = np.empty(len(me.edges), bool)
        me.edges.foreach_get("use_seam", seams)
        seams[boundary_edges(me, faces)] = action != 'CLEAR'
        me.edges.foreach_set("use_seam", seams)
    me.update()
    return int(faces.sum())


# ---------------------------------------------------------------- Edit Mode (bmesh)

def _bm_layers(bm):
    """Face BOOL layers (int on a bmesh without bool layers)."""
    layers = bm.faces.layers
    return layers.bool if hasattr(layers, "bool") else layers.int


def _bm_bool_layer(bm, name):
    layers = _bm_layers(bm)
    return layers.get(name) or layers.new(name)


def edit_region(bm):
    return [f for f in bm.faces if f.select]


def _edit_boundary(faces):
    region = set(faces)
    return [e for f in faces for e in f.edges
            if len(e.link_faces) > 1 and any(g not in region for g in e.link_faces)]


def edit_backup(obj, bm, faces):
    """What apply_edit_mode may change, to put back when the dialog is cancelled."""
    vg = obj.vertex_groups.get(wf.VG_HIGHRES)
    deform = bm.verts.layers.deform.active
    verts = {v for f in faces for v in f.verts}
    weights = {}
    if vg is not None and deform is not None:
        weights = {v.index: v[deform].get(vg.index) for v in verts}
    attrs = {}
    for name in (wf.ATTR_DELETE, wf.ATTR_DETACH):
        layers = _bm_layers(bm)
        lay = layers.get(name)
        attrs[name] = None if lay is None else {f.index: f[lay] for f in faces}
    seams = {e.index: e.seam for e in _edit_boundary(faces)}
    return {"weights": weights, "attrs": attrs, "seams": seams,
            "had_vg": vg is not None, "verts": [v.index for v in verts]}


def edit_restore(obj, bm, backup):
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    vg = obj.vertex_groups.get(wf.VG_HIGHRES)
    deform = bm.verts.layers.deform.active
    if vg is not None and deform is not None:
        for i in backup["verts"]:
            dv = bm.verts[i][deform]
            w = backup["weights"].get(i)
            if w is None:
                if vg.index in dv.keys():
                    del dv[vg.index]
            else:
                dv[vg.index] = w
    for name, vals in backup["attrs"].items():
        layers = _bm_layers(bm)
        lay = layers.get(name)
        if lay is None:
            continue
        if vals is None:
            layers.remove(lay)
            continue
        for i, v in vals.items():
            bm.faces[i][lay] = v
    for i, s in backup["seams"].items():
        bm.edges[i].seam = s


def apply_edit_mode(obj, bm, faces, action, mark_seam):
    if not faces:
        return 0
    vg = obj.vertex_groups.get(wf.VG_HIGHRES) or obj.vertex_groups.new(name=wf.VG_HIGHRES)
    # every layer first: adding one frees the BMFace references taken before it
    index = [f.index for f in faces]
    deform = bm.verts.layers.deform.verify()
    target = {'DELETE': wf.ATTR_DELETE, 'SEPARATE': wf.ATTR_DETACH}.get(action)
    if target is not None:
        _bm_bool_layer(bm, target)
    layers = _bm_layers(bm)
    attr_layers = [lay for lay in (layers.get(wf.ATTR_DELETE), layers.get(wf.ATTR_DETACH))
                   if lay is not None]
    bm.faces.ensure_lookup_table()
    faces = [bm.faces[i] for i in index]
    region = set(faces)
    verts = {v for f in faces for v in f.verts}

    def highres(f):
        return all(v[deform].get(vg.index, 0.0) > 0.0 for v in f.verts)

    for v in verts:
        if vg.index in v[deform].keys() and not any(g not in region and highres(g)
                                                    for g in v.link_faces):
            del v[deform][vg.index]
    for lay in attr_layers:
        for f in faces:
            f[lay] = False

    if action == 'HIGH_DENSITY':
        for v in verts:
            v[deform][vg.index] = 1.0
    elif target is not None:
        lay = layers.get(target)
        for f in faces:
            f[lay] = True
    if mark_seam:
        for e in _edit_boundary(faces):
            e.seam = action != 'CLEAR'
    return len(faces)


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

_overlay = {"handle": None, "tris": None, "lines": None, "action": 'HIGH_DENSITY'}


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
