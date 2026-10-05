"""MAT_<name>: the baked export material - ALB_ into Base Color, NOR_ through a tangent
Normal Map, both on uv_normal. Only nodes named mcp_bake_* are managed; nodes the user
adds survive a rebuild. The FBX exporter's Principled wrapper reads exactly this."""
from contextlib import contextmanager

import bpy

from ..camera_project import core as cp
from ..camera_project.core import BAKED_TAG
from ..camera_project.gn_builder import B
from . import common

P = "mcp_bake_"


def find(obj):
    """obj's own MAT_ (see camera_project.core ownership): the one it points to, else the
    one named after it that no other object owns. None on a duplicate or before a bake."""
    own = cp.own_baked(obj)
    if own is not None:
        return own
    m = bpy.data.materials.get(common.mat_name(obj))
    if m is not None and m.get(BAKED_TAG) \
            and cp.owners(cp.mat_pointer, cp.baked_name).get(m) in (None, obj):
        return m
    return None


def place(obj):
    """MAT_ into slot 2 after MCP_ (slot 1 without a projection), the scan materials after."""
    if getattr(obj, "multicamproject_cam", None) is not None and obj.multicamproject_cam.is_setup:
        cp.place_material(obj)
    elif common.data(obj).material is not None:
        cp.arrange_slots(obj, [common.data(obj).material])


def release(obj):
    """A duplicate lets go of the original's bake (MAT_, ALB_, NOR_ and the work textures): it gets
    baked on its own."""
    d = common.data(obj)
    d.material = d.alb_image = d.nor_image = d.ba_image = d.bn_image = None
    d.bap_image = d.bnp_image = d.bng_image = None
    d.fingerprint = d.ba_fingerprint = d.bp_fingerprint = d.bnp_fingerprint = ""
    d.bng_fingerprint = ""
    d.alb_size = d.nor_size = d.ba_size = d.bp_size = 0


def _normal_maps(obj):
    """The Normal Map nodes of obj's MAT_ and Processing material (MCP_)."""
    mats = [common.data(obj).material]
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None:
        mats.append(cam.material)
    return [n for m in mats if m is not None and m.node_tree
            for n in m.node_tree.nodes if n.type == 'NORMAL_MAP']


def apply_normal_visibility(obj, show=None):
    """The normal maps of obj's materials at full strength, or flat (Strength 0) while its
    EXPORT row's normal toggle is off. `show` overrides the toggle (the export)."""
    show = common.data(obj).show_normal if show is None else show
    for n in _normal_maps(obj):
        v = 1.0 if show else 0.0
        if n.inputs["Strength"].default_value != v:
            n.inputs["Strength"].default_value = v


@contextmanager
def normals_on(objs):
    """Every normal map at full strength for the time of an export, then as toggled."""
    try:
        for o in objs:
            apply_normal_visibility(o, True)
        yield
    finally:
        for o in objs:
            try:
                apply_normal_visibility(o)
            except ReferenceError:
                pass


def build(obj, scene, place_slots=True):
    d, s = common.data(obj), common.settings(scene)
    mat = find(obj)
    new = mat is None
    if new:
        mat = bpy.data.materials.new(common.mat_name(obj))
    cp.claim_name(mat, common.mat_name(obj), bpy.data.materials)
    mat[BAKED_TAG] = True
    mat.use_nodes = True
    nt = mat.node_tree
    for n in [n for n in nt.nodes if n.name.startswith(P) or new]:
        nt.nodes.remove(n)      # a new material's default nodes too

    b = B(nt)

    def node(typ, name, loc, **kw):
        n = b.n(typ, loc, **kw)
        n.name = P + name
        return n

    out = node("ShaderNodeOutputMaterial", "out", (400, 0))
    bsdf = node("ShaderNodeBsdfPrincipled", "bsdf", (100, 0))
    bsdf.inputs["Roughness"].default_value = cp.ROUGHNESS
    bsdf.inputs["Metallic"].default_value = 0.0
    b.link(bsdf.outputs[0], out.inputs["Surface"])
    uv = node("ShaderNodeUVMap", "uv", (-700, -100), uv_map=common.UV_NORMAL)
    alb = node("ShaderNodeTexImage", "alb", (-400, 100), image=d.alb_image)
    alb.label = "ALB"
    b.link(uv.outputs["UV"], alb.inputs["Vector"])
    b.link(alb.outputs["Color"], bsdf.inputs["Base Color"])
    if d.nor_image is not None:
        nor = node("ShaderNodeTexImage", "nor", (-400, -250), image=d.nor_image)
        nor.label = "NOR"
        b.link(uv.outputs["UV"], nor.inputs["Vector"])
        nm = node("ShaderNodeNormalMap", "normalmap", (-120, -300), space='TANGENT',
                  uv_map=common.UV_NORMAL)
        b.link(nor.outputs["Color"], nm.inputs["Color"])
        b.link(nm.outputs["Normal"], bsdf.inputs["Normal"])
    d.material = mat
    apply_normal_visibility(obj)
    if place_slots:
        place(obj)
    return mat
