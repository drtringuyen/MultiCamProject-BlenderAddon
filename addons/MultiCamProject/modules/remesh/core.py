"""Remesh regions: the region table GN reads, adding/removing regions, Apply and Restore.

Apply makes it real: GN-Remesh applied, each region decimated to its exact ratio in Edit
Mode (the selection's border is never collapsed, so the straight cut stays straight),
seams on region borders, one Sculpt face set per region. remesh_region / remesh_seam stay
on the mesh for the other GN groups."""
import bmesh
import bpy
import numpy as np

from . import cutter, gn_remesh as gn

FACE_SET = ".sculpt_face_set"


def data(obj):
    return obj.multicamproject_remesh


def region_by_id(obj, rid):
    return next((r for r in data(obj).regions if r.region_id == rid), None)


# ---------------------------------------------------------------- region table

def _ensure_table(obj):
    """One point per region ID (point 0 = outside), carrying remesh_ratio / remesh_delete."""
    d = data(obj)
    t = d.table
    if t is None:
        me = bpy.data.meshes.new(f"MCP_RemeshTable_{obj.name}")
        t = bpy.data.objects.new(me.name, me)
        t.hide_render = True
        cutter.ensure_collection(obj).objects.link(t)   # not in the cutters child
        d.table = t
    return t


def update_table(obj):
    d = data(obj)
    t = _ensure_table(obj)
    size = max([r.region_id for r in d.regions] + [0]) + 1
    ratio = np.ones(size, np.float32)
    delete = np.zeros(size, bool)
    for r in d.regions:
        ratio[r.region_id] = r.ratio
        delete[r.region_id] = r.delete
    me = t.data
    me.clear_geometry()
    me.vertices.add(size)
    for name, typ, vals in ((gn.RATIO, 'FLOAT', ratio), (gn.DELETE, 'BOOLEAN', delete)):
        a = me.attributes.get(name) or me.attributes.new(name, typ, 'POINT')
        a.data.foreach_set("value", vals)
    me.update()
    t.hide_set(True)
    _update_decimate(obj)
    obj.update_tag()


def _update_decimate(obj):
    """One Decimate ratio for the whole mesh: the face count the region ratios aim for."""
    dec = gn.decimate_modifier(obj)
    if dec is None:
        return
    d = data(obj)
    total = max(len(obj.data.polygons), 1)
    removed = sum(r.face_count * (1.0 - r.ratio) for r in d.regions if not r.delete)
    dec.ratio = max(0.0, min(1.0, 1.0 - removed / total))
    dec.show_viewport = d.preview and dec.ratio < 1.0


def set_preview(obj, on):
    mod = gn.get_modifier(obj)
    if mod is not None:
        from ..camera_project import core as cp
        cp.set_input(mod, "Preview", on)
    _update_decimate(obj)
    obj.update_tag()


def ensure_setup(obj):
    coll = cutter.ensure_cutters(obj)
    t = _ensure_table(obj)
    gn.ensure_modifiers(obj, coll, t)


def count_faces(obj, depsgraph):
    """Faces per region in GN-Remesh's result (Decimate off) - for the Decimate preview."""
    dec = gn.decimate_modifier(obj)
    was = dec.show_viewport if dec else None
    if dec:
        dec.show_viewport = False
    depsgraph.update()
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    counts = {}
    a = me.attributes.get(gn.REGION)
    if a is not None and a.domain == 'FACE':
        v = np.empty(len(me.polygons), np.int32)
        a.data.foreach_get("value", v)
        ids, c = np.unique(v, return_counts=True)
        counts = dict(zip(ids.tolist(), c.tolist()))
    ev.to_mesh_clear()
    if dec:
        dec.show_viewport = was
    for r in data(obj).regions:
        r.face_count = counts.get(r.region_id, 0)
    _update_decimate(obj)
    return counts


# ---------------------------------------------------------------- regions

def add_cut(obj, rays, forward, near, far):
    d = data(obj)
    ensure_setup(obj)
    r = d.regions.add()
    r.region_id = d.next_id
    d.next_id += 1
    r.name = f"Cut {r.region_id}"
    r.kind = 'CUT'
    r.forward = forward
    for o, dr in rays:
        ray = r.rays.add()
        ray.origin, ray.direction = o, dr
    # set depth without triggering two rebuilds
    r["near"], r["far"] = near, far
    cutter.rebuild_prism(obj, r)
    d.active = len(d.regions) - 1
    update_table(obj)
    return r


def face_set_ids(obj):
    a = obj.data.attributes.get(FACE_SET)
    if a is None:
        return []
    v = np.empty(len(obj.data.polygons), np.int32)
    a.data.foreach_get("value", v)
    ids, c = np.unique(v, return_counts=True)
    return list(zip(ids.tolist(), c.tolist()))


def _region_attr(me):
    a = me.attributes.get(gn.REGION)
    if a is not None and (a.domain != 'FACE' or a.data_type != 'INT'):
        me.attributes.remove(a)
        a = None
    return a or me.attributes.new(gn.REGION, 'INT', 'FACE')


def add_face_set(obj, fs_id):
    """A region from a Sculpt face set: remesh_region written on the scan itself (no cut)."""
    me = obj.data
    fs = np.empty(len(me.polygons), np.int32)
    me.attributes[FACE_SET].data.foreach_get("value", fs)
    d = data(obj)
    ensure_setup(obj)
    a = _region_attr(me)
    v = np.empty(len(me.polygons), np.int32)
    a.data.foreach_get("value", v)
    v[fs == fs_id] = d.next_id
    a.data.foreach_set("value", v)
    me.update()
    r = d.regions.add()
    r.region_id = d.next_id
    d.next_id += 1
    r.name = f"Face Set {fs_id}"
    r.kind = 'FACESET'
    r.face_count = int((fs == fs_id).sum())
    d.active = len(d.regions) - 1
    update_table(obj)
    return r


def remove_region(obj, index):
    d = data(obj)
    r = d.regions[index]
    if r.kind == 'CUT':
        cutter.remove_prism(r)
    else:
        a = obj.data.attributes.get(gn.REGION)
        if a is not None:
            v = np.empty(len(obj.data.polygons), np.int32)
            a.data.foreach_get("value", v)
            v[v == r.region_id] = 0
            a.data.foreach_set("value", v)
            obj.data.update()
    d.regions.remove(index)
    d.active = min(d.active, len(d.regions) - 1)
    update_table(obj)


def clear(obj):
    """Regions, cutters, table and modifiers gone - the scan as it was (or as applied)."""
    d = data(obj)
    gn.remove_modifiers(obj)
    cutter.remove_collection(obj)
    d.regions.clear()
    d.table = None
    d.next_id = 1


# ---------------------------------------------------------------- apply / restore

def _with_mode(obj, mode):
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.mode_set(mode=mode)


def _decimate_regions(obj, regions):
    """Exact per-region ratio: Edit Mode decimate collapses only inside the selection."""
    todo = [(r.region_id, r.ratio) for r in regions if not r.delete and r.ratio < 1.0]
    if not todo:
        return
    _with_mode(obj, 'EDIT')
    try:
        me = obj.data
        for rid, ratio in todo:
            bm = bmesh.from_edit_mesh(me)
            layer = bm.faces.layers.int.get(gn.REGION)
            if layer is None:
                break
            for v in bm.verts:
                v.select = False
            for e in bm.edges:
                e.select = False
            for f in bm.faces:
                f.select = f[layer] == rid
            bm.select_flush_mode()
            bmesh.update_edit_mesh(me)
            with bpy.context.temp_override(object=obj, active_object=obj):
                bpy.ops.mesh.decimate(ratio=ratio)
        bm = bmesh.from_edit_mesh(me)
        for f in bm.faces:
            f.select = False
        bm.select_flush(False)
        bmesh.update_edit_mesh(me)
    finally:
        _with_mode(obj, 'OBJECT')


def _mark_borders(obj):
    """Seams on every edge between two regions (or a region and the rest), and one Sculpt
    face set per region. remesh_seam mirrors the seams for GN."""
    me = obj.data
    a = me.attributes.get(gn.REGION)
    if a is None:
        return
    reg = np.empty(len(me.polygons), np.int32)
    a.data.foreach_get("value", reg)
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.edges.ensure_lookup_table()
    seam = np.zeros(len(bm.edges), bool)
    for e in bm.edges:
        ids = {reg[f.index] for f in e.link_faces}
        seam[e.index] = len(ids) > 1
    bm.free()
    if "remesh_seam" in me.attributes:
        me.attributes.remove(me.attributes["remesh_seam"])
    s = me.attributes.new(gn.SEAM, 'BOOLEAN', 'EDGE')
    s.data.foreach_set("value", seam)
    old = np.zeros(len(me.edges), bool)
    me.edges.foreach_get("use_seam", old)
    me.edges.foreach_set("use_seam", old | seam)

    fs_attr = me.attributes.get(FACE_SET)
    if fs_attr is None:
        fs_attr = me.attributes.new(FACE_SET, 'INT', 'FACE')
        fs = np.ones(len(me.polygons), np.int32)
    else:
        fs = np.empty(len(me.polygons), np.int32)
        fs_attr.data.foreach_get("value", fs)
    base = int(fs.max()) if len(fs) else 0
    inside = reg > 0
    fs[inside] = base + reg[inside]
    fs_attr.data.foreach_set("value", fs)
    me.update()


def apply(obj):
    """Returns a list of warnings."""
    warnings = []
    d = data(obj)
    mod = gn.get_modifier(obj)
    if mod is None:
        return ["Nothing to apply"]
    old = d.backup
    if old is not None and old.users <= 1:
        bpy.data.meshes.remove(old)
    backup = obj.data.copy()
    backup.name = f"{obj.data.name}_preRemesh"
    backup.use_fake_user = True
    d.backup = backup
    d.source_mesh = obj.data.name
    regions = [(r.region_id, r.ratio, r.delete) for r in d.regions]

    from ..camera_project import core as cp
    cp.set_input(mod, "Preview", True)
    dec = gn.decimate_modifier(obj)
    if dec is not None:
        obj.modifiers.remove(dec)
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=mod.name)

    class _R:       # the region settings outlive the property group cleared below
        def __init__(self, t):
            self.region_id, self.ratio, self.delete = t
    _decimate_regions(obj, [_R(t) for t in regions])
    _mark_borders(obj)
    vg = obj.vertex_groups.get(gn.VGROUP)
    if vg is not None:
        obj.vertex_groups.remove(vg)
    clear(obj)
    if "uv_normal" in obj.data.uv_layers:
        warnings.append("The object was already baked: the topology changed, bake it again")
    return warnings


def restore(obj):
    d = data(obj)
    backup = d.backup
    if backup is None:
        return False
    cur = obj.data
    obj.data = backup
    backup.use_fake_user = False
    d.backup = None
    name = d.source_mesh
    if cur.users == 0:
        bpy.data.meshes.remove(cur)
    if name:
        backup.name = name
    return True
