"""Two node groups:

MCP-PolyCut - applied once per Poly Cut (a temporary modifier): an Exact boolean splits the
scan along the cutter's walls, nothing removed. Inside faces get remesh_inside = True.
The scan is open, so the solver keeps bits of the cutter's walls: every cutter face is dropped.

GN-Remesh - a modifier: Face Set number in, only those faces out (Isolate / Invert). It
reads face_set, the copy of the Sculpt face sets (GN cannot read .sculpt_face_set).
"""
import bpy

from ..camera_project.gn_builder import B, _iface
from .cutter import CUTTER_ATTR

CUT_GROUP = "MCP-PolyCut"
FACESET_GROUP = "GN-Remesh"
INSIDE = "remesh_inside"
FACE_SET = "face_set"               # the GN-readable copy of .sculpt_face_set
_SEAM = "_remesh_seam"
SELECTION = "remesh_selection"      # GN-Remesh's chosen faces, for modifiers after it
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


def build_face_set():
    """GN-Remesh, a modifier: pick a face set by number, see only it (or all but it).
    Stores remesh_selection (face BOOL) for whatever comes after it in the stack."""
    ng = _fresh(FACESET_GROUP, True)
    ng.description = "Read the Sculpt face sets: show one face set, or everything but it"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    s = _iface(ng, "Face Set", "INPUT", "NodeSocketInt", default=1, mn=0, single=True)
    s.description = "Face set number (as in Sculpt Mode; Poly Cut reports the new one)"
    s = _iface(ng, "Isolate", "INPUT", "NodeSocketBool", default=True, single=True)
    s.description = "Show only the chosen face set. Off: the whole mesh, selection stored only"
    s = _iface(ng, "Invert", "INPUT", "NodeSocketBool", default=False, single=True)
    s.description = "Everything except the chosen face set"
    b = B(ng)
    gi = b.n("NodeGroupInput", (-700, 0))
    go = b.n("NodeGroupOutput", (1000, 0))

    read = b.frame("1. Read the face sets (face_set = copy of the Sculpt face sets)", (-450, -200))
    fs = b.n("GeometryNodeInputNamedAttribute", (0, 0), read, data_type='INT')
    fs.inputs["Name"].default_value = FACE_SET
    eq = b.n("FunctionNodeCompare", (200, 0), read, data_type='INT', operation='EQUAL')
    b.link(fs.outputs["Attribute"], eq.inputs["A"])
    b.link(gi.outputs["Face Set"], eq.inputs["B"])
    inv = b.n("FunctionNodeBooleanMath", (400, 0), read, operation='XOR')
    b.link(eq.outputs["Result"], inv.inputs[0])
    b.link(gi.outputs["Invert"], inv.inputs[1])
    sel = inv.outputs[0]

    use = b.frame("2. Use the selection", (200, 200))
    store = b.n("GeometryNodeStoreNamedAttribute", (0, 0), use, data_type='BOOLEAN', domain='FACE')
    store.inputs["Name"].default_value = SELECTION
    b.link(gi.outputs["Geometry"], store.inputs["Geometry"])
    b.link(sel, store.inputs["Value"])
    other = b.n("FunctionNodeBooleanMath", (0, -220), use, operation='NOT')
    b.link(sel, other.inputs[0])
    drop = b.n("GeometryNodeDeleteGeometry", (220, -60), use, domain='FACE')
    b.link(store.outputs["Geometry"], drop.inputs["Geometry"])
    b.link(other.outputs[0], drop.inputs["Selection"])
    sw = b.n("GeometryNodeSwitch", (440, 0), use, input_type='GEOMETRY')
    b.link(gi.outputs["Isolate"], sw.inputs["Switch"])
    b.link(store.outputs["Geometry"], sw.inputs["False"])
    b.link(drop.outputs["Geometry"], sw.inputs["True"])
    b.link(sw.outputs["Output"], go.inputs["Geometry"])
    return ng


def _ensure(name, build):
    ng = bpy.data.node_groups.get(name)
    if ng is None or ng.get(_VERSION_KEY) != VERSION:
        ng = build()
    return ng


def ensure_cut_group():
    return _ensure(CUT_GROUP, build_cut)


def ensure_face_set_group():
    return _ensure(FACESET_GROUP, build_face_set)


def get_modifier(obj):
    return next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                 and m.node_group.name == FACESET_GROUP), None)


def add_modifier(obj):
    """GN-Remesh first in the stack (before the projection)."""
    ng = ensure_face_set_group()
    mod = get_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(FACESET_GROUP, 'NODES')
        mod.node_group = ng
    if list(obj.modifiers).index(mod) != 0:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
    return mod
