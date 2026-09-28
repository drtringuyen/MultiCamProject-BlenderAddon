"""GN-Remesh: the first modifier on a scan. For every cutter in the Cutters collection
(Repeat Zone), an Exact boolean splits the scan along the cutter's walls - nothing removed:
  inside  (Intersect)  -> remesh_region = the cutter's ID
  outside (Difference)
  both joined, the cut line merged, its edges OR-ed into remesh_seam.
Then, from the region table (one point per region ID, remesh_ratio / remesh_delete):
  deleted regions removed, and the MCP_Remesh vertex group written for the Decimate
  modifier after it (1 - ratio inside a region, 0 outside and on the seams).
Preview off passes the geometry through; only the chosen Switch branch is evaluated.

Output for other groups: remesh_region (face INT), remesh_seam (edge BOOL).
"""
import bpy

from ..camera_project import core as cp
from ..camera_project.gn_builder import B, _iface
from .cutter import CUTTER_ATTR

GROUP = "GN-Remesh"
MOD_NAME = "GN-Remesh"
DECIMATE_NAME = "MCP-Remesh Decimate"
VGROUP = "MCP_Remesh"
REGION = "remesh_region"
SEAM = "remesh_seam"
RATIO = "remesh_ratio"
DELETE = "remesh_delete"
VERSION = 2             # bump when the node layout changes
_VERSION_KEY = "multicamproject_remesh_version"


def _named(b, name, dtype, loc):
    n = b.n("GeometryNodeInputNamedAttribute", loc, data_type=dtype)
    n.inputs["Name"].default_value = name
    return n.outputs["Attribute"]


def _store(b, geo, name, dtype, domain, value, loc):
    s = b.n("GeometryNodeStoreNamedAttribute", loc, data_type=dtype, domain=domain)
    s.inputs["Name"].default_value = name
    b.link(geo, s.inputs["Geometry"])
    if isinstance(value, (int, float, bool)):
        s.inputs["Value"].default_value = value
    else:
        b.link(value, s.inputs["Value"])
    return s.outputs["Geometry"]


def _on_domain(b, value, domain, dtype, loc):
    e = b.n("GeometryNodeFieldOnDomain", loc, domain=domain, data_type=dtype)
    b.link(value, e.inputs[0])
    return e.outputs[0]


def _sample(b, geo, value, index, domain, dtype, loc):
    s = b.n("GeometryNodeSampleIndex", loc, domain=domain, data_type=dtype)
    b.link(geo, s.inputs["Geometry"])
    b.link(value, s.inputs["Value"])
    if index is not None:
        b.link(index, s.inputs["Index"])
    return s.outputs["Value"]


def _seam_or(b, geo, edges, loc):
    x, y = loc
    orr = b.n("FunctionNodeBooleanMath", (x, y - 160), operation='OR')
    b.link(_named(b, SEAM, 'BOOLEAN', (x - 200, y - 120)), orr.inputs[0])
    b.link(edges, orr.inputs[1])
    return _store(b, geo, SEAM, 'BOOLEAN', 'EDGE', orr.outputs[0], (x + 200, y))


def _drop_cutter(b, geo, loc):
    x, y = loc
    ne = b.n("FunctionNodeCompare", (x, y - 160), data_type='INT', operation='NOT_EQUAL')
    b.link(_named(b, CUTTER_ATTR, 'INT', (x - 200, y - 160)), ne.inputs["A"])
    rm = b.n("GeometryNodeDeleteGeometry", (x + 180, y), domain='FACE')
    b.link(geo, rm.inputs["Geometry"])
    b.link(ne.outputs["Result"], rm.inputs["Selection"])
    return rm.outputs["Geometry"]


def _cut_loop(b, geo_in, cutters, loc):
    x, y = loc
    count = b.n("GeometryNodeAttributeDomainSize", (x, y - 220), component='INSTANCES')
    b.link(cutters, count.inputs["Geometry"])
    ri = b.n("GeometryNodeRepeatInput", (x + 200, y))
    ro = b.n("GeometryNodeRepeatOutput", (x + 2600, y))
    ri.pair_with_output(ro)
    b.link(count.outputs["Instance Count"], ri.inputs["Iterations"])
    b.link(geo_in, ri.inputs["Geometry"])
    geo = ri.outputs["Geometry"]

    # cutter i, realized
    idx = b.n("GeometryNodeInputIndex", (x + 200, y - 420))
    ne = b.n("FunctionNodeCompare", (x + 400, y - 380), data_type='INT', operation='NOT_EQUAL')
    b.link(idx.outputs[0], ne.inputs["A"])
    b.link(ri.outputs["Iteration"], ne.inputs["B"])
    one = b.n("GeometryNodeDeleteGeometry", (x + 600, y - 300), domain='INSTANCE')
    b.link(cutters, one.inputs["Geometry"])
    b.link(ne.outputs["Result"], one.inputs["Selection"])
    real = b.n("GeometryNodeRealizeInstances", (x + 800, y - 300))
    b.link(one.outputs["Geometry"], real.inputs["Geometry"])
    cutter = real.outputs["Geometry"]
    rid = _sample(b, cutter, _named(b, CUTTER_ATTR, 'INT', (x + 800, y - 520)), None,
                  'FACE', 'INT', (x + 1000, y - 460))

    inside = b.n("GeometryNodeMeshBoolean", (x + 1100, y - 40), operation='INTERSECT', solver='EXACT')
    b.link(geo, inside.inputs["Mesh 2"])
    b.link(cutter, inside.inputs["Mesh 2"])
    outside = b.n("GeometryNodeMeshBoolean", (x + 1100, y + 260), operation='DIFFERENCE', solver='EXACT')
    b.link(geo, outside.inputs["Mesh 1"])
    b.link(cutter, outside.inputs["Mesh 2"])

    # the scan is open: the solver keeps bits of the cutter's walls - drop every cutter face
    g_in = _seam_or(b, inside.outputs["Mesh"], inside.outputs["Intersecting Edges"], (x + 1350, y - 40))
    g_in = _store(b, _drop_cutter(b, g_in, (x + 1550, y - 40)), REGION, 'INT', 'FACE', rid,
                  (x + 1750, y - 40))
    g_out = _seam_or(b, outside.outputs["Mesh"], outside.outputs["Intersecting Edges"], (x + 1350, y + 260))
    g_out = _drop_cutter(b, g_out, (x + 1550, y + 260))

    join = b.n("GeometryNodeJoinGeometry", (x + 1950, y + 100))
    b.link(g_in, join.inputs[0])
    b.link(g_out, join.inputs[0])
    merge = b.n("GeometryNodeMergeByDistance", (x + 2150, y + 100))
    merge.inputs["Distance"].default_value = 1e-5
    b.link(join.outputs[0], merge.inputs["Geometry"])
    b.link(_on_domain(b, _named(b, SEAM, 'BOOLEAN', (x + 1750, y - 240)), 'POINT', 'BOOLEAN',
                      (x + 1950, y - 200)), merge.inputs["Selection"])
    b.link(merge.outputs["Geometry"], ro.inputs["Geometry"])
    return ro.outputs["Geometry"]


def build():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    ng.is_modifier = True
    ng[_VERSION_KEY] = VERSION
    ng.description = "Straight polyline regions: cut, delete, decimate weights"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Cutters", "INPUT", "NodeSocketCollection")
    _iface(ng, "Region Table", "INPUT", "NodeSocketObject")
    _iface(ng, "Preview", "INPUT", "NodeSocketBool", default=True)
    b = B(ng)
    gi = b.n("NodeGroupInput", (-800, 0))
    go = b.n("NodeGroupOutput", (4200, 0))

    coll = b.n("GeometryNodeCollectionInfo", (-500, -300), transform_space='RELATIVE')
    b.link(gi.outputs["Cutters"], coll.inputs["Collection"])
    coll.inputs["Separate Children"].default_value = True
    geo = _cut_loop(b, gi.outputs["Geometry"], coll.outputs["Instances"], (-300, 0))

    # region table lookups (index = region ID; point 0 = outside every region)
    x = 2500
    table = b.n("GeometryNodeObjectInfo", (x, -500))
    b.link(gi.outputs["Region Table"], table.inputs["Object"])
    reg = _named(b, REGION, 'INT', (x, -700))
    dele = _sample(b, table.outputs["Geometry"], _named(b, DELETE, 'BOOLEAN', (x + 200, -560)), reg,
                   'POINT', 'BOOLEAN', (x + 400, -500))
    rm = b.n("GeometryNodeDeleteGeometry", (x + 600, 0), domain='FACE')
    b.link(geo, rm.inputs["Geometry"])
    b.link(dele, rm.inputs["Selection"])

    ratio = _sample(b, table.outputs["Geometry"], _named(b, RATIO, 'FLOAT', (x + 200, -820)), reg,
                    'POINT', 'FLOAT', (x + 400, -780))
    inside = b.n("FunctionNodeCompare", (x + 400, -1000), data_type='INT', operation='GREATER_THAN')
    b.link(reg, inside.inputs["A"])
    w = b.math('SUBTRACT', 1.0, ratio, (x + 600, -780))
    w = b.math('MULTIPLY', w, inside.outputs["Result"], (x + 800, -780))
    w = _on_domain(b, w, 'FACE', 'FLOAT', (x + 1000, -780))          # averaged onto the points
    seam = _on_domain(b, _named(b, SEAM, 'BOOLEAN', (x + 800, -1000)), 'POINT', 'BOOLEAN',
                      (x + 1000, -1000))
    keep = b.n("FunctionNodeBooleanMath", (x + 1200, -1000), operation='NOT')
    b.link(seam, keep.inputs[0])
    w = b.math('MULTIPLY', w, keep.outputs[0], (x + 1200, -780))
    geo = _store(b, rm.outputs["Geometry"], VGROUP, 'FLOAT', 'POINT', w, (x + 1400, 0))
    clean = b.n("GeometryNodeRemoveAttribute", (x + 1600, 0))
    clean.inputs["Name"].default_value = CUTTER_ATTR
    b.link(geo, clean.inputs["Geometry"])

    sw = b.n("GeometryNodeSwitch", (x + 1800, 0), input_type='GEOMETRY')
    b.link(gi.outputs["Preview"], sw.inputs["Switch"])
    b.link(gi.outputs["Geometry"], sw.inputs["False"])
    b.link(clean.outputs["Geometry"], sw.inputs["True"])
    b.link(sw.outputs["Output"], go.inputs["Geometry"])
    return ng


def ensure_group():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None or ng.get(_VERSION_KEY) != VERSION:
        ng = build()
    return ng


def get_modifier(obj):
    mod = obj.modifiers.get(MOD_NAME)
    if mod and mod.type == 'NODES':
        return mod
    return next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                 and m.node_group.name == GROUP), None)


def _move(obj, mod, index):
    if list(obj.modifiers).index(mod) != index:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=index)


def ensure_modifiers(obj, collection, table):
    """GN-Remesh first in the stack, its Decimate right after (before the projection)."""
    ng = ensure_group()
    mod = get_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(MOD_NAME, 'NODES')
    if mod.node_group != ng:
        mod.node_group = ng
    cp.set_input(mod, "Cutters", collection)
    cp.set_input(mod, "Region Table", table)
    _move(obj, mod, 0)
    if obj.vertex_groups.get(VGROUP) is None:
        obj.vertex_groups.new(name=VGROUP)      # GN writes into it only when it exists
    dec = obj.modifiers.get(DECIMATE_NAME)
    if dec is None:
        dec = obj.modifiers.new(DECIMATE_NAME, 'DECIMATE')
        dec.decimate_type = 'COLLAPSE'
        dec.vertex_group = VGROUP
        dec.vertex_group_factor = 10.0
        dec.use_collapse_triangulate = False
    _move(obj, dec, 1)
    return mod, dec


def decimate_modifier(obj):
    return obj.modifiers.get(DECIMATE_NAME)


def remove_modifiers(obj):
    for m in (get_modifier(obj), decimate_modifier(obj)):
        if m is not None:
            obj.modifiers.remove(m)
    vg = obj.vertex_groups.get(VGROUP)
    if vg is not None:
        obj.vertex_groups.remove(vg)
