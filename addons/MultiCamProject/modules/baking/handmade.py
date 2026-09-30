"""Handmade EXPORT objects: meshes someone baked by hand (their own material with an
albedo and a normal texture). Ticking Handmade makes that material the object's MAT_ and
its two textures ALB_ / NOR_, so the exporter treats it like a baked object: names are
checked and fixed, and GN-Final writes one material, one UV (uv_normal) and one color.
Nothing is baked, rebuilt, unwrapped or transformed for them.

Untick: the material and textures are let go again (never removed)."""
import os

import bpy

from ..camera_project.core import BAKED_TAG
from . import common, engine


def is_handmade(obj):
    d = getattr(obj, "multicamproject_bake", None)
    return bool(d is not None and d.handmade)


def _upstream(node, seen=None):
    """Every node feeding `node`, nearest first."""
    seen = set() if seen is None else seen
    out = []
    for inp in node.inputs:
        for link in inp.links:
            n = link.from_node
            if n not in seen:
                seen.add(n)
                out.append(n)
                out += _upstream(n, seen)
    return out


def _images(mat):
    """(albedo image, normal image, UV map name or '') the material's Principled BSDF uses."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None, None, ""
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    alb = nor = None
    uv = ""
    if bsdf is not None:
        for key, is_nor in (("Base Color", False), ("Normal", True)):
            sock = bsdf.inputs.get(key)
            if sock is None or not sock.links:
                continue
            up = [sock.links[0].from_node] + _upstream(sock.links[0].from_node)
            tex = next((n for n in up if n.type == 'TEX_IMAGE' and n.image), None)
            if tex is None:
                continue
            if is_nor:
                nor = tex.image
            else:
                alb = tex.image
            uvn = next((n for n in _upstream(tex) if n.type == 'UVMAP' and n.uv_map), None)
            uv = uv or (uvn.uv_map if uvn else "")
    if alb is None or nor is None:      # not wired to a Principled: go by the names
        for n in nodes:
            if n.type != 'TEX_IMAGE' or n.image is None:
                continue
            name = n.image.name.lower()
            if nor is None and ("_n" in name or "normal" in name or "nor_" in name):
                nor = n.image
            elif alb is None and n.image != nor:
                alb = n.image
    return alb, nor, uv


def _material(obj):
    """The material holding the textures: the one with an albedo, first slot first."""
    for s in obj.material_slots:
        if s.material is not None and _images(s.material)[0] is not None:
            return s.material
    return None


def _uv_normal(obj, uv_name):
    """Rename the textures' UV map to uv_normal (the one GN-Final keeps). Image nodes
    that name it follow. Returns a note, or '' when nothing was done."""
    uvs = obj.data.uv_layers
    if uvs.get(common.UV_NORMAL) is not None:
        return ""
    uv = uvs.get(uv_name) if uv_name else None
    if uv is None:
        cands = [u for u in uvs if not u.name.startswith("UV_cam")]
        if len(cands) != 1:
            uv = next((u for u in cands if u.active_render), None)
        else:
            uv = cands[0]
    if uv is None:
        return "no UV map to use as uv_normal"
    old = uv.name
    uv.name = common.UV_NORMAL
    for s in obj.material_slots:
        m = s.material
        if m is not None and m.node_tree is not None:
            for n in m.node_tree.nodes:
                if n.type == 'UVMAP' and n.uv_map == old:
                    n.uv_map = common.UV_NORMAL
                elif n.type == 'NORMAL_MAP' and n.uv_map == old:
                    n.uv_map = common.UV_NORMAL
    return f"UV map '{old}' renamed to uv_normal"


def _png(img, scene, kind, obj):
    """The exporter copies <texture>.png files: a packed-only or non-PNG texture is
    written as a PNG into the bake folder. Returns a note or ''."""
    path = common.image_file(img)
    if path and os.path.isfile(path) and path.lower().endswith(".png"):
        return ""
    folder = common.output_dir(scene)
    os.makedirs(folder, exist_ok=True)
    name = common.alb_name(obj) if kind == "ALB" else common.nor_name(obj)
    dst = os.path.join(folder, name + ".png")
    if os.path.isfile(dst):
        raise RuntimeError(f"{os.path.basename(dst)} already exists - not overwritten")
    was_packed = img.packed_file is not None
    img.filepath_raw = dst
    img.file_format = 'PNG'
    img.save()
    if was_packed:
        img.unpack(method='REMOVE')
    engine.link_file(img, dst)
    return f"{kind} written as {os.path.basename(dst)}"


def adopt(obj, scene):
    """Handmade on: its material -> MAT_, its textures -> ALB_ / NOR_. Returns notes."""
    d = common.data(obj)
    mat = _material(obj)
    if mat is None:
        return ["no material with an image texture - pick one, then tick Handmade again"]
    alb, nor, uv = _images(mat)
    notes = []
    mat[BAKED_TAG] = True
    d.material = mat
    d.alb_image, d.nor_image = alb, nor
    if nor is not None and nor.colorspace_settings.name != 'Non-Color':
        nor.colorspace_settings.name = 'Non-Color'
        notes.append("NOR set to Non-Color")
    for img, kind in ((alb, "ALB"), (nor, "NOR")):
        if img is not None:
            try:
                n = _png(img, scene, kind, obj)
            except (RuntimeError, OSError) as e:
                n = f"{kind}: {e}"
            if n:
                notes.append(n)
    n = _uv_normal(obj, uv)
    if n:
        notes.append(n)
    if nor is None:
        notes.append("no normal texture found")
    return notes


def release(obj):
    """Handmade off: let go of the material and textures (they stay in the file)."""
    d = common.data(obj)
    if d.material is not None and BAKED_TAG in d.material:
        del d.material[BAKED_TAG]
    d.material = d.alb_image = d.nor_image = None


def on_toggle(data):
    obj = data.id_data
    if data.handmade:
        for n in adopt(obj, bpy.context.scene):
            print(f"[MultiCamProject] {obj.name}: {n}")
    else:
        release(obj)
    from . import cache
    cache.clear(obj)
