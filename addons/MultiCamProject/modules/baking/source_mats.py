"""The Bake Source's own materials (the scan's): every material its faces use shows its
image through a Principled BSDF - image -> Base Color, BSDF -> Material Output Surface.
Scans often come unlit (image -> Emission -> Output, the BSDF left unconnected); Refresh
Materials wires them. An Emission node is left in the tree, only disconnected.

The materials may be shared with other scan objects: wiring one changes how all of them
render (lit instead of unlit)."""
from ..camera_project import core as cp
from . import common, engine


def _output(nt):
    return (next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
            or next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None))


def _image_color(nt):
    """The color the material means to show: a linked Base Color / Emission Color, else
    the first Image Texture's Color."""
    sock, _value = engine._color_socket(nt)
    if sock is not None:
        return sock
    tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image), None)
    return tex.outputs["Color"] if tex is not None else None


def scan_materials(src):
    """The materials the source's faces use, without the add-on's own."""
    return [m for m in engine._used_materials(src) if not cp._is_ours(m)]


def problem(mat):
    """Why `mat` is not wired image -> Principled BSDF -> Output ('' = it is)."""
    if not mat.use_nodes or mat.node_tree is None:
        return "no nodes"
    nt = mat.node_tree
    out = _output(nt)
    if out is None:
        return "no Material Output"
    surf = out.inputs["Surface"]
    if not surf.links or surf.links[0].from_node.type != 'BSDF_PRINCIPLED':
        what = surf.links[0].from_node.bl_label if surf.links else "nothing"
        return f"Surface comes from {what}, not the Principled BSDF"
    bsdf = surf.links[0].from_node
    color = _image_color(nt)
    if color is not None and not bsdf.inputs["Base Color"].links:
        return "the image is not in Base Color"
    return ""


def wire(mat):
    """Image -> Principled BSDF Base Color -> Output Surface. Returns True when changed."""
    if problem(mat) in ("", "no nodes", "no Material Output"):
        return False
    nt = mat.node_tree
    out = _output(nt)
    color = _image_color(nt)
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (out.location.x - 300, out.location.y)
    if color is not None and not bsdf.inputs["Base Color"].links:
        nt.links.new(color, bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return True


def problems(obj):
    """[text] for obj's Bake Source materials. Reads only."""
    src = common.data(obj).bake_source
    if src is None or src.type != 'MESH':
        return []
    return [f"{src.name}: {m.name} - {why}" for m in scan_materials(src) if (why := problem(m))]


def fix(obj):
    """Wire obj's Bake Source materials. Returns what changed, as text."""
    src = common.data(obj).bake_source
    if src is None or src.type != 'MESH' or src.library:
        return []
    out = []
    for m in scan_materials(src):
        if wire(m):
            users = sum(1 for o in src.users_scene[0].objects if o.type == 'MESH'
                        and any(s.material == m for s in o.material_slots)) if src.users_scene else 1
            shared = f" (shared by {users} objects)" if users > 1 else ""
            out.append(f"source {m.name}: image -> Principled BSDF -> Output{shared}")
    return out
