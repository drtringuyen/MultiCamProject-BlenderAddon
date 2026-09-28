"""GN-Final: the last modifier on a baked object. Final off = the geometry passes through
(projection setup). Final on = what gets exported:
  1. Color (corner, byte) from the scan attribute - or sampled from ALB on uv_normal
     (chosen, or where the scan attribute is missing)
  2. UV_cam*, VCMix* removed (wildcard)
  3. uv_index, the scan color and the scan UV removed
  4. Set Material MAT_<name>
Only the chosen Switch branch is evaluated, so Final off costs nothing.

GN cannot drop the old material slots or "every UV except X": the exporter trims those
on temporary copies.
"""
import bpy

from ..camera_project import core as cp
from ..camera_project import wrapper
from ..camera_project.gn_builder import B, _iface
from . import common

GROUP = "GN-Final"
MOD_NAME = "GN-Final"
VERSION = 1             # bump when the node layout changes
_VERSION_KEY = "multicamproject_final_version"
COLOR_SOURCES = (('SCAN_ATTRIBUTE', "Scan Attribute"), ('FROM_ALB', "From ALB"))


def _remove(b, geo, name, loc, wildcard=False, name_socket=None):
    r = b.n("GeometryNodeRemoveAttribute", loc)
    if wildcard:
        r.inputs["Pattern Mode"].default_value = "Wildcard"
    if name_socket is not None:
        b.link(name_socket, r.inputs["Name"])
    else:
        r.inputs["Name"].default_value = name
    b.link(geo, r.inputs["Geometry"])
    return r.outputs["Geometry"]


def _unless(b, name, other, loc):
    """`name`, or '' (removes nothing) when it equals `other` - never remove what we keep."""
    eq = b.n("FunctionNodeCompare", loc, data_type='STRING', operation='EQUAL')
    b.link(name, eq.inputs["A"])
    b.link(other, eq.inputs["B"])
    sw = b.n("GeometryNodeSwitch", (loc[0] + 180, loc[1]), input_type='STRING')
    b.link(eq.outputs["Result"], sw.inputs["Switch"])
    b.link(name, sw.inputs["False"])
    return sw.outputs["Output"]


def build():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    ng.is_modifier = True
    ng[_VERSION_KEY] = VERSION
    ng.description = "Switch between the projection setup and the baked export result"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Final", "INPUT", "NodeSocketBool", False, single=True)
    _iface(ng, "Baked Material", "INPUT", "NodeSocketMaterial")
    _iface(ng, "Keep UV", "INPUT", "NodeSocketString", common.UV_NORMAL, single=True)
    _iface(ng, "Color Source", "INPUT", "NodeSocketMenu")
    _iface(ng, "Scan Color", "INPUT", "NodeSocketString", "Attribute", single=True)
    _iface(ng, "Albedo", "INPUT", "NodeSocketImage")
    scan_uv = _iface(ng, "Scan UV", "INPUT", "NodeSocketString", "", single=True)
    scan_uv.hide_in_modifier = True     # written by the Projection/Final toggle

    b = B(ng)
    gi = b.n("NodeGroupInput", (-1400, 0))
    go = b.n("NodeGroupOutput", (1500, 0))
    geo = gi.outputs["Geometry"]

    # ---- 1. Color: the scan attribute, or ALB sampled on uv_normal ----
    scan = b.n("GeometryNodeInputNamedAttribute", (-1100, -300), data_type='FLOAT_COLOR')
    b.link(gi.outputs["Scan Color"], scan.inputs["Name"])
    uv = b.n("GeometryNodeInputNamedAttribute", (-1100, -500), data_type='FLOAT_VECTOR')
    b.link(gi.outputs["Keep UV"], uv.inputs["Name"])
    tex = b.n("GeometryNodeImageTexture", (-850, -500), interpolation='Linear', extension='EXTEND')
    b.link(gi.outputs["Albedo"], tex.inputs["Image"])
    b.link(uv.outputs["Attribute"], tex.inputs["Vector"])
    ms = b.n("GeometryNodeMenuSwitch", (-850, -250), data_type='BOOLEAN')
    ms.label = "Use Scan Color"
    ms.enum_items.clear()
    for _k, label in COLOR_SOURCES:
        ms.enum_items.new(label)
    ms.inputs[1].default_value = True
    ms.inputs[2].default_value = False
    b.link(gi.outputs["Color Source"], ms.inputs["Menu"])
    use_scan = b.n("FunctionNodeBooleanMath", (-650, -300), operation='AND')
    b.link(ms.outputs["Output"], use_scan.inputs[0])
    b.link(scan.outputs["Exists"], use_scan.inputs[1])
    col = b.n("GeometryNodeSwitch", (-450, -350), input_type='RGBA')
    col.label = "Color"
    b.link(use_scan.outputs[0], col.inputs["Switch"])
    b.link(tex.outputs["Color"], col.inputs["False"])
    b.link(scan.outputs["Attribute"], col.inputs["True"])
    st = b.n("GeometryNodeStoreNamedAttribute", (-250, 0), data_type='BYTE_COLOR', domain='CORNER')
    st.inputs["Name"].default_value = common.COLOR
    b.link(geo, st.inputs["Geometry"])
    b.link(col.outputs["Output"], st.inputs["Value"])
    g = st.outputs["Geometry"]

    # ---- 2. + 3. the projection's attributes, the scan color and scan UV ----
    g = _remove(b, g, "UV_cam*", (0, 0), wildcard=True)
    g = _remove(b, g, "VCMix*", (200, 0), wildcard=True)
    g = _remove(b, g, cp.UV_INDEX, (400, 0))
    color_name = b.n("FunctionNodeInputString", (-100, -250), string=common.COLOR)
    g = _remove(b, g, None, (600, 0),
                name_socket=_unless(b, gi.outputs["Scan Color"], color_name.outputs[0], (200, -250)))
    g = _remove(b, g, None, (800, 0),
                name_socket=_unless(b, gi.outputs["Scan UV"], gi.outputs["Keep UV"], (400, -450)))

    # ---- 4. one material ----
    sm = b.n("GeometryNodeSetMaterial", (1000, 0))
    b.link(g, sm.inputs["Geometry"])
    b.link(gi.outputs["Baked Material"], sm.inputs["Material"])

    sw = b.n("GeometryNodeSwitch", (1250, 100), input_type='GEOMETRY')
    sw.label = "Final"
    b.link(gi.outputs["Final"], sw.inputs["Switch"])
    b.link(geo, sw.inputs["False"])
    b.link(sm.outputs["Geometry"], sw.inputs["True"])
    b.link(sw.outputs["Output"], go.inputs["Geometry"])
    return ng


def ensure_group():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None or ng.get(_VERSION_KEY) != VERSION:
        ng = build()
        bpy.context.view_layer.update()     # the menu input has no items until an update
    return ng


def get_modifier(obj):
    mod = obj.modifiers.get(MOD_NAME)
    if mod and mod.type == 'NODES':
        return mod
    return next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                 and wrapper.shared(m.node_group).name == GROUP), None)


def ensure_modifier(obj):
    """GN-Final on the object, always last in the stack. It runs the object's wrapper
    around GN-Final: Baked Material and Albedo never sit on the modifier (wrapper.py)."""
    ng = ensure_group()
    mod = get_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(MOD_NAME, 'NODES')
    wrapper.ensure(obj, mod, ng)
    last = len(obj.modifiers) - 1
    if list(obj.modifiers).index(mod) != last:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=last)
    return mod


def migrate_wrappers():
    """(2026-09-26) GN-Final modifiers from before wrapper.py (or shared with a duplicated
    object) get their own wrapper. A no-op once done."""
    for obj in bpy.data.objects:
        mod = None if obj.library else get_modifier(obj)
        if mod is None or mod.node_group is None:
            continue
        if not wrapper.is_wrapper(mod.node_group) or mod.node_group.users > 1:
            ensure_modifier(obj)


def is_final(obj):
    mod = get_modifier(obj)
    if mod is None or mod.node_group is None:
        return False
    try:
        return bool(cp.get_input(mod, "Final"))
    except (KeyError, AttributeError):
        return False


def scan_uv(obj):
    """The scan's UV map name ('' if none) - removed from the Final result."""
    warnings = []
    return cp._scan_uv(obj, warnings) or ""


def write_inputs(obj, scene):
    """Push the object's bake results and the scene settings into GN-Final."""
    mod = ensure_modifier(obj)
    d, s = common.data(obj), common.settings(scene)
    cp.set_input(mod, "Baked Material", d.material)
    # GN samples the image as floats (1 GB at 8K): only hand it over when it is used
    scan_missing = obj.data.color_attributes.get(s.scan_color_name) is None
    uses_alb = s.color_source == 'FROM_ALB' or scan_missing
    cp.set_input(mod, "Albedo", d.alb_image if uses_alb else None)
    cp.set_input(mod, "Keep UV", common.UV_NORMAL)
    cp.set_input(mod, "Scan Color", s.scan_color_name)
    cp.set_input(mod, "Scan UV", scan_uv(obj))
    label = dict(COLOR_SOURCES)[s.color_source]
    if cp.get_input(mod, "Color Source") != label:
        cp.set_input(mod, "Color Source", label)
    return mod


def set_final(obj, scene, on):
    """Flip one object between the projection setup and the baked result. With Final on,
    GN-CameraProject is off (no projection cost) and uv_normal is the active and render UV;
    Projection puts the previous UVs back."""
    mod = write_inputs(obj, scene)
    was = bool(cp.get_input(mod, "Final"))
    cp.set_input(mod, "Final", on)
    cpm = common.cp_modifier(obj)
    if cpm is not None:
        cpm.show_viewport = cpm.show_render = not on
    d = common.data(obj)
    uvs = obj.data.uv_layers
    if on and not was:
        target = uvs.get(common.UV_NORMAL)
        if target is not None:
            d.prev_uv_active = uvs.active.name if uvs.active else ""
            d.prev_uv_render = next((u.name for u in uvs if u.active_render), "")
            uvs.active = target
            target.active_render = True
    elif not on and was:
        if uvs.get(d.prev_uv_active):
            uvs.active = uvs[d.prev_uv_active]
        if uvs.get(d.prev_uv_render):
            uvs[d.prev_uv_render].active_render = True
        d.prev_uv_active = d.prev_uv_render = ""
    obj.update_tag()
