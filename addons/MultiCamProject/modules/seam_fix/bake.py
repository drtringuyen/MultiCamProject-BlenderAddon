"""Bake: every image Seam Fix manages, as Preview shows it, onto uv_new - into
<image>_seamfix (same size, colour space and float-ness as the original; packed). An
albedo with alpha gets its alpha in <image>_seamfix_alpha. Cycles Emission bakes, one
image per material per pass."""
import bpy

from . import geometry as geo
from . import shader

DUMMY = "SF_dummy_target"


def _temp(name, like, non_color=False):
    w, h = like.size
    img = bpy.data.images.get(name)
    float_buf = like.is_float and not non_color
    if img is not None and (tuple(img.size) != (w, h) or img.is_float != float_buf):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name, w, h, alpha=False, float_buffer=float_buf)
    img.colorspace_settings.name = 'Non-Color' if non_color else like.colorspace_settings.name
    return img


def _dummy():
    img = bpy.data.images.get(DUMMY)
    if img is None:
        img = bpy.data.images.new(DUMMY, 8, 8)
    return img


def _passes(obj):
    """[[(material, image node, group node, socket, target image)], ...] - at most one
    target per material in each pass."""
    per_mat = []
    for mat in shader.materials(obj):
        st = shader.state(mat)
        jobs = []
        if st:
            nt = mat.node_tree
            for name, info in st["nodes"].items():
                n, g = nt.nodes.get(name), nt.nodes.get(info["group_node"])
                if n is None or g is None:
                    continue
                img = n.image
                jobs.append((mat, n, g, "Color", _temp(shader.temp_name(img), img)))
                if info["kind"] == 'ALB' and img.channels == 4:
                    jobs.append((mat, n, g, "Alpha",
                                 _temp(shader.temp_alpha_name(img), img, non_color=True)))
        per_mat.append((mat, jobs))
    n_pass = max((len(j) for _m, j in per_mat), default=0)
    return [[j[k] if k < len(j) else (m, None, None, None, None) for m, j in per_mat]
            for k in range(n_pass)]


def _surface_link(nt):
    out = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output),
               None) or next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if out is None:
        return None, None
    link = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
    return out, link


def bake(obj, margin_px=16, report=print):
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    me = obj.data
    if me.uv_layers.get(geo.UV_NEW) is None:
        raise RuntimeError("No uv_new - run Fix Seam or Seam-leveling first")
    passes = _passes(obj)
    if not passes:
        raise RuntimeError("Nothing to bake - no image on uv_normal is managed")

    saved = {"engine": scene.render.engine, "samples": scene.cycles.samples,
             "mode": shader.get_mode(obj), "active": view_layer.objects.active,
             "selected": [o for o in view_layer.objects if o.select_get()]}
    shader.set_mode(obj, 2)                 # bake what Preview shows (never the old bake)
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    for o in saved["selected"]:
        o.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj

    done = {}
    try:
        for k, jobs in enumerate(passes):
            undo = []
            for mat, n, g, sock, target in jobs:
                nt = mat.node_tree
                out, prev = _surface_link(nt)
                emit = nt.nodes.new('ShaderNodeEmission')
                tnode = nt.nodes.new('ShaderNodeTexImage')
                tnode.image = target if target is not None else _dummy()
                if g is not None:
                    nt.links.new(g.outputs[sock], emit.inputs["Color"])
                else:
                    emit.inputs["Color"].default_value = (0, 0, 0, 1)
                if out is not None:
                    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
                nt.nodes.active = tnode
                undo.append((nt, out, prev, emit, tnode))
            report(f"Seam Fix bake pass {k + 1}/{len(passes)}")
            try:
                bpy.ops.object.bake(type='EMIT', uv_layer=geo.UV_NEW, margin=margin_px,
                                    margin_type='EXTEND', use_clear=True,
                                    target='IMAGE_TEXTURES')
            finally:
                for nt, out, prev, emit, tnode in undo:
                    nt.nodes.remove(emit)
                    nt.nodes.remove(tnode)
                    if out is not None and prev is not None:
                        nt.links.new(prev, out.inputs["Surface"])
            for mat, n, g, sock, target in jobs:
                if target is not None:
                    done.setdefault((mat.name, n.name), {})[sock] = target
    except Exception:
        shader.set_mode(obj, saved["mode"])
        raise
    finally:
        scene.render.engine = saved["engine"]
        scene.cycles.samples = saved["samples"]
        obj.select_set(False)
        for o in saved["selected"]:
            o.select_set(True)
        view_layer.objects.active = saved["active"]

    for (mat_name, node_name), imgs in done.items():
        for img in imgs.values():
            img.pack()
        shader.set_temp(bpy.data.materials[mat_name], node_name, imgs.get("Color"),
                        imgs.get("Alpha"))
    dummy = bpy.data.images.get(DUMMY)
    if dummy is not None:
        bpy.data.images.remove(dummy)
    shader.install(obj)                     # the groups now read the bakes in Baked mode
    shader.set_mode(obj, 3)
    return {"images": sorted(img.name for imgs in done.values() for img in imgs.values()),
            "passes": len(passes)}
