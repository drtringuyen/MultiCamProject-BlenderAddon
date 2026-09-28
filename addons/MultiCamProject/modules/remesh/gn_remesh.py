"""MCP-PolyCut - applied once per Poly Cut (a temporary modifier): an Exact boolean splits
the scan along the cutter's walls, nothing removed. Inside faces get remesh_inside = True.
The scan is open, so the solver keeps bits of the cutter's walls: every cutter face is dropped.

(GN-Remesh, the modifier, is the user's own group - see workflow.py.)
"""
import bpy

from ..camera_project.gn_builder import B, _iface
from .cutter import CUTTER_ATTR

CUT_GROUP = "MCP-PolyCut"
INSIDE = "remesh_inside"
FACE_SET = "face_set"               # the GN-readable copy of .sculpt_face_set
_SEAM = "_remesh_seam"
VERSION = 4            # bump when a node layout changes
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


def _part(b, geo, edges, loc):
    """Cut edges remembered (for the merge), the cutter's faces dropped."""
    x, y = loc
    geo = _store(b, geo, _SEAM, 'BOOLEAN', 'EDGE', edges, (x, y))
    rm = b.n("GeometryNodeDeleteGeometry", (x + 200, y), domain='FACE')
    b.link(geo, rm.inputs["Geometry"])
    b.link(_named(b, CUTTER_ATTR, 'BOOLEAN', (x, y - 200)), rm.inputs["Selection"])
    return rm.outputs["Geometry"]


def _fresh(name, modifier):
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    ng.is_modifier = modifier
    ng[_VERSION_KEY] = VERSION
    return ng


def build_cut():
    ng = _fresh(CUT_GROUP, True)
    ng.description = "Split the mesh exactly along the cutter's walls (Poly Cut)"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Cutter", "INPUT", "NodeSocketObject")
    b = B(ng)
    gi = b.n("NodeGroupInput", (-600, 0))
    go = b.n("NodeGroupOutput", (1800, 0))
    info = b.n("GeometryNodeObjectInfo", (-400, -250), transform_space='RELATIVE')
    b.link(gi.outputs["Cutter"], info.inputs["Object"])
    cutter = info.outputs["Geometry"]

    inside = b.n("GeometryNodeMeshBoolean", (-150, 100), operation='INTERSECT', solver='EXACT')
    b.link(gi.outputs["Geometry"], inside.inputs["Mesh 2"])
    b.link(cutter, inside.inputs["Mesh 2"])
    outside = b.n("GeometryNodeMeshBoolean", (-150, -200), operation='DIFFERENCE', solver='EXACT')
    b.link(gi.outputs["Geometry"], outside.inputs["Mesh 1"])
    b.link(cutter, outside.inputs["Mesh 2"])

    g_in = _part(b, inside.outputs["Mesh"], inside.outputs["Intersecting Edges"], (100, 100))
    g_in = _store(b, g_in, INSIDE, 'BOOLEAN', 'FACE', True, (500, 100))
    g_out = _part(b, outside.outputs["Mesh"], outside.outputs["Intersecting Edges"], (100, -200))

    join = b.n("GeometryNodeJoinGeometry", (750, 0))
    b.link(g_in, join.inputs[0])
    b.link(g_out, join.inputs[0])
    merge = b.n("GeometryNodeMergeByDistance", (950, 0))
    merge.inputs["Distance"].default_value = 1e-5
    b.link(join.outputs[0], merge.inputs["Geometry"])
    seam = b.n("GeometryNodeFieldOnDomain", (750, -200), domain='POINT', data_type='BOOLEAN')
    b.link(_named(b, _SEAM, 'BOOLEAN', (550, -200)), seam.inputs[0])
    b.link(seam.outputs[0], merge.inputs["Selection"])
    geo = merge.outputs["Geometry"]
    for i, name in enumerate((CUTTER_ATTR, _SEAM)):
        clean = b.n("GeometryNodeRemoveAttribute", (1150 + i * 200, 0))
        clean.inputs["Name"].default_value = name
        b.link(geo, clean.inputs["Geometry"])
        geo = clean.outputs["Geometry"]
    b.link(geo, go.inputs["Geometry"])
    return ng


def _ensure(name, build):
    ng = bpy.data.node_groups.get(name)
    if ng is None or ng.get(_VERSION_KEY) != VERSION:
        ng = build()
    return ng


def ensure_cut_group():
    return _ensure(CUT_GROUP, build_cut)
