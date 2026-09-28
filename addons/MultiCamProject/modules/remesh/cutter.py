"""Cutter prisms: one closed mesh per Cut region, in world space, in a hidden collection
that GN-Remesh reads with Collection Info. Each cutter carries its region ID on its faces."""
import bmesh
import bpy
from mathutils import Vector

CUTTER_ATTR = "remesh_cutter_id"        # read by GN-Remesh, removed from its result


def data(obj):
    return obj.multicamproject_remesh


def ensure_collection(obj):
    """MCP_Remesh_<obj> (the region table) with its child MCP_RemeshCutters_<obj> (only the
    cutters: GN-Remesh loops over everything in it). Objects hidden; GN still reads them."""
    d = data(obj)
    coll = d.collection
    if coll is None:
        coll = bpy.data.collections.new(f"MCP_Remesh_{obj.name}")
        d.collection = coll
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    coll.hide_render = True
    return coll


def ensure_cutters(obj):
    d = data(obj)
    root = ensure_collection(obj)
    cut = d.cutters
    if cut is None:
        cut = bpy.data.collections.new(f"MCP_RemeshCutters_{obj.name}")
        d.cutters = cut
    if cut.name not in root.children:
        root.children.link(cut)
    cut.hide_render = True
    return cut


def _ray_points(region, depth):
    fwd = Vector(region.forward)
    pts = []
    for r in region.rays:
        o, dr = Vector(r.origin), Vector(r.direction)
        k = dr.dot(fwd)
        pts.append(o + dr * (depth / k if abs(k) > 1e-6 else depth))
    return pts


def _fill_prism(me, region):
    near, far = _ray_points(region, region.near), _ray_points(region, region.far)
    n = len(near)
    bm = bmesh.new()
    vn = [bm.verts.new(p) for p in near]
    vf = [bm.verts.new(p) for p in far]
    bm.faces.new(vn)
    bm.faces.new(vf)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((vn[i], vn[j], vf[j], vf[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)   # outward: the Exact solver needs it
    bm.to_mesh(me)
    bm.free()
    a = me.attributes.get(CUTTER_ATTR) or me.attributes.new(CUTTER_ATTR, 'INT', 'FACE')
    a.data.foreach_set("value", [region.region_id] * len(me.polygons))
    me.update()


def rebuild_prism(obj, region):
    if region.kind != 'CUT' or len(region.rays) < 3 or region.far <= region.near:
        return
    cut = region.cutter
    if cut is None:
        me = bpy.data.meshes.new(f"MCP_RemeshCut_{obj.name}_{region.region_id}")
        cut = bpy.data.objects.new(me.name, me)
        cut.display_type = 'WIRE'
        cut.hide_render = True
        ensure_cutters(obj).objects.link(cut)
        region.cutter = cut
    _fill_prism(cut.data, region)
    cut.hide_set(True)
    obj.update_tag()


def remove_prism(region):
    cut = region.cutter
    if cut is not None:
        me = cut.data
        bpy.data.objects.remove(cut)
        if me.users == 0:
            bpy.data.meshes.remove(me)
    region.cutter = None


def remove_collection(obj):
    d = data(obj)
    for coll in (d.cutters, d.collection):
        if coll is None:
            continue
        for o in list(coll.objects):
            me = o.data
            bpy.data.objects.remove(o)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        bpy.data.collections.remove(coll)
    d.cutters = None
    d.collection = None


def auto_depth(obj, depsgraph, rays, forward, samples):
    """Near/Far around what the drawn area hits on `obj`: `samples` are extra (origin,
    direction) rays inside the polygon. None when nothing is hit."""
    fwd = Vector(forward)
    inv = obj.matrix_world.inverted()
    ev = obj.evaluated_get(depsgraph)
    depths = []
    for o, dr in list(rays) + list(samples):
        o, dr = Vector(o), Vector(dr)
        lo = inv @ o
        ld = (inv.to_3x3() @ dr).normalized()
        hit, loc, _n, _i = ev.ray_cast(lo, ld)
        if hit:
            depths.append((obj.matrix_world @ loc - o).dot(fwd))
    if not depths:
        return None
    lo, hi = min(depths), max(depths)
    pad = max((hi - lo) * 0.1, max(obj.dimensions) * 0.01, 1e-3)
    return lo - pad, hi + pad
