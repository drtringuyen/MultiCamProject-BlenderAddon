"""The cutter prism of a Poly Cut: the drawn polygon pushed along the view, from just in
front of what it covers to just behind it. A temporary closed mesh in world space."""
import bmesh
import bpy
from mathutils import Vector

CUTTER_ATTR = "remesh_cutter"       # on the cutter's faces: MCP-PolyCut drops them after the cut


def _ray_points(rays, forward, depth):
    fwd = Vector(forward)
    pts = []
    for o, dr in rays:
        k = dr.dot(fwd)
        pts.append(o + dr * (depth / k if abs(k) > 1e-6 else depth))
    return pts


def build(rays, forward, near, far):
    """A temporary object (not linked to any scene) holding the prism."""
    near_pts, far_pts = _ray_points(rays, forward, near), _ray_points(rays, forward, far)
    n = len(near_pts)
    bm = bmesh.new()
    vn = [bm.verts.new(p) for p in near_pts]
    vf = [bm.verts.new(p) for p in far_pts]
    bm.faces.new(vn)
    bm.faces.new(vf)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((vn[i], vn[j], vf[j], vf[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)   # outward: the Exact solver needs it
    me = bpy.data.meshes.new("_MCP_PolyCut")
    bm.to_mesh(me)
    bm.free()
    me.attributes.new(CUTTER_ATTR, 'BOOLEAN', 'FACE').data.foreach_set(
        "value", [True] * len(me.polygons))
    return bpy.data.objects.new(me.name, me)


def remove(cut):
    me = cut.data
    bpy.data.objects.remove(cut)
    if me.users == 0:
        bpy.data.meshes.remove(me)


def auto_depth(obj, depsgraph, rays):
    """Near/Far around what the rays hit on `obj`, as depth along each ray's view. None when
    nothing is hit."""
    inv = obj.matrix_world.inverted()
    ev = obj.evaluated_get(depsgraph)
    depths = []
    for o, dr, fwd in rays:
        hit, loc, _n, _i = ev.ray_cast(inv @ o, (inv.to_3x3() @ dr).normalized())
        if hit:
            depths.append((obj.matrix_world @ loc - o).dot(fwd))
    if not depths:
        return None
    lo, hi = min(depths), max(depths)
    pad = max((hi - lo) * 0.1, max(obj.dimensions) * 0.01, 1e-3)
    return lo - pad, hi + pad
