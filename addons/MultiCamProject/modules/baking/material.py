"""MAT_<name>: the baked export material - ALB_ into Base Color, NOR_ through a tangent
Normal Map, both on uv_normal. Only nodes named mcp_bake_* are managed; nodes the user
adds survive a rebuild. The FBX exporter's Principled wrapper reads exactly this."""
import bpy

from ..camera_project.core import BAKED_TAG
from ..camera_project.gn_builder import B
from . import common

P = "mcp_bake_"


def find(obj):
    d = common.data(obj)
    if d.material is not None:
        return d.material
    return bpy.data.materials.get(common.mat_name(obj))


def build(obj, scene):
    d, s = common.data(obj), common.settings(scene)
    mat = find(obj)
    new = mat is None
    if new:
        mat = bpy.data.materials.new(common.mat_name(obj))
    elif mat.name != common.mat_name(obj) and not bpy.data.materials.get(common.mat_name(obj)):
        mat.name = common.mat_name(obj)
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
    bsdf.inputs["Roughness"].default_value = s.roughness
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
    return mat
