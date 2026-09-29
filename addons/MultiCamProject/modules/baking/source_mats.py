"""The Bake Source's own materials (the scan's). Its slots hold only the scan materials
(no MCP_ / MAT_ - those belong to the low poly), and every material in its slots shows
its image through a Principled BSDF - image -> Base Color, BSDF -> Material Output Surface.
Scans often come unlit (image -> Emission -> Output, the BSDF left unconnected); Refresh
Materials wires them. An Emission node is left in the tree, only disconnected.

The materials may be shared with other scan objects: wiring one changes how all of them
render (lit instead of unlit)."""
from ..camera_project import core as cp
from . import common, engine


def _output(nt):
    """The Material Output Cycles renders (and bakes) with: the active one among those for
    All / Cycles, else the first of those, else any."""
    outs = [n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL']
    cyc = [n for n in outs if n.target in {'ALL', 'CYCLES'}]
    return (next((n for n in cyc if n.is_active_output), None) or (cyc[0] if cyc else None)
            or (outs[0] if outs else None))


def _image_color(nt):
    """The color the material means to show: a linked Base Color / Emission Color, else
    the first Image Texture's Color."""
    sock, _value = engine._color_socket(nt)
    if sock is not None:
        return sock
    tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image), None)
    return tex.outputs["Color"] if tex is not None else None


def scan_materials(src):
    """Every material in the source's slots, without the add-on's own."""
    return list(dict.fromkeys(s.material for s in src.material_slots
                              if s.material is not None and not cp._is_ours(s.material)))


def problem(mat):
    """Why `mat` is not wired image -> Principled BSDF -> Output ('' = it is)."""
    if not mat.use_nodes or mat.node_tree is None:
        return "does not use nodes"
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
    if not cp.roughness_ok(mat):
        return f"Roughness is not {cp.ROUGHNESS:g}"
    return ""


def wire(mat):
    """Image -> Principled BSDF Base Color -> Output Surface. Returns True when changed.
    A material without nodes or without an Output gets them (Principled + Output)."""
    if not problem(mat):
        return False
    mat.use_nodes = True
    nt = mat.node_tree
    out = _output(nt)
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
    out.is_active_output = True
    color = _image_color(nt)
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (out.location.x - 300, out.location.y)
    if color is not None and not bsdf.inputs["Base Color"].links:
        nt.links.new(color, bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    cp.set_roughness(mat)
    return True


def addon_slots(src):
    """The add-on's materials (MCP_, MAT_, MATMCP_) in the source's slots."""
    return [s.material for s in src.material_slots if s.material is not None and cp._is_ours(s.material)]


def strip_slots(src):
    """Only the scan materials in the source's slots (faces and uv_index follow their
    material; a face still on MCP_ goes back to its scan material first). Returns the
    names removed."""
    gone = [m.name for m in addon_slots(src)]
    if not gone and all(s.material is not None for s in src.material_slots):
        return []
    if src.library or src.mode != 'OBJECT' or cp.shared_mesh(src):
        return []
    try:
        from ..remesh import workflow
        workflow.repair_original(src)       # faces on MCP_ -> their scan material (uv_index)
    except ImportError:
        pass
    cp.arrange_slots(src, [])               # no head: the scan materials only
    return gone


def problems(obj):
    """[text] for obj's Bake Source materials. Reads only."""
    src = common.data(obj).bake_source
    if src is None or src.type != 'MESH':
        return []
    out = []
    ours = addon_slots(src)
    if ours:
        out.append(f"{src.name}: {', '.join(m.name for m in ours)} in its slots - only the "
                   "scan materials belong there")
    return out + [f"{src.name}: {m.name} - {why}" for m in scan_materials(src) if (why := problem(m))]


def fix(obj):
    """Wire obj's Bake Source materials. Returns what changed, as text."""
    src = common.data(obj).bake_source
    if src is None or src.type != 'MESH' or src.library:
        return []
    out = []
    gone = strip_slots(src)
    if gone:
        out.append(f"source slots: {', '.join(gone)} removed - scan materials only")
    for m in scan_materials(src):
        if wire(m):
            users = sum(1 for o in src.users_scene[0].objects if o.type == 'MESH'
                        and any(s.material == m for s in o.material_slots)) if src.users_scene else 1
            shared = f" (shared by {users} objects)" if users > 1 else ""
            out.append(f"source {m.name}: image -> Principled BSDF (Roughness "
                       f"{cp.ROUGHNESS:g}) -> Output{shared}")
    return out
