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


# ---------------------------------------------------------------- new UVs: rebake on itself
#
# Edit UV keeps the layout the textures were painted on as uv_old; the user unwraps
# uv_normal anew. Rebake carries ALB_ / NOR_ over from uv_old to uv_normal on the object
# itself (no Remesh copy): ALB_ by an EEVEE render (engine.uv_render), NOR_ by a Cycles
# Normal bake - it re-expresses the normals in the new tangents (rotated / mirrored islands).
# The first Rebake keeps the old files in <bake folder>/_previous/<name>_<date>.png and
# reads from them, so Rebake can run again on a changed uv_normal. Finish drops uv_old.
# GN-Final removes uv_old from the Final result, the exporter keeps uv_normal only.

UV_OLD = "uv_old"
PREVIOUS_DIR = "_previous"
_TMP_MAT = "MCP_REBAKE_TMP"


def has_uv_old(obj):
    return obj.type == 'MESH' and obj.data.uv_layers.get(UV_OLD) is not None


def start_uv_edit(obj, scene):
    """uv_old = a copy of uv_normal (only when there is none yet: it is what the textures
    were painted on). Returns True when it was made now."""
    uvs = obj.data.uv_layers
    if uvs.get(common.UV_NORMAL) is None:
        raise RuntimeError("No uv_normal")
    if uvs.get(UV_OLD) is not None:
        return False
    active = uvs.active
    uvs.active = uvs[common.UV_NORMAL]
    new = uvs.new(name=UV_OLD, do_init=True)        # copies the active UV map
    if new is None:
        raise RuntimeError("No room for another UV map (8 at most)")
    uvs.active = uvs[common.UV_NORMAL] if active is None else active
    uvs[common.UV_NORMAL].active_render = True
    from . import gn_final
    if gn_final.get_modifier(obj) is not None:
        gn_final.write_inputs(obj, scene)           # Final drops uv_old
    return True


def finish_uv_edit(obj, scene):
    """uv_old goes, the _previous textures are let go (their files stay)."""
    uv = obj.data.uv_layers.get(UV_OLD)
    if uv is not None:
        obj.data.uv_layers.remove(uv)
    d = common.data(obj)
    d.prev_alb = d.prev_nor = None
    from . import gn_final
    if gn_final.get_modifier(obj) is not None:
        gn_final.write_inputs(obj, scene)


def rebake_problem(obj):
    """Why Rebake cannot run for `obj` ('' = it can)."""
    if not is_handmade(obj):
        return "Not handmade"
    if not has_uv_old(obj):
        return "No uv_old - click Edit UV first"
    if not common.has_uv_normal(obj):
        return "No uv_normal"
    d = common.data(obj)
    if d.alb_image is None or not os.path.isfile(common.image_file(d.alb_image)):
        return "ALB_ has no file - tick Handmade again"
    if d.nor_image is not None and not os.path.isfile(common.image_file(d.nor_image)):
        return "NOR_ has no file - tick Handmade again"
    return common.uv_collapsed_text(obj)


def _keep_previous(img, scene, kind):
    """A copy of img's file in <bake folder>/_previous/<name>_<date>.png, loaded as an image
    the object points at (PREV_<name>, never in a material). Returns it."""
    import datetime
    import shutil
    src = common.image_file(img)
    folder = os.path.join(common.output_dir(scene), PREVIOUS_DIR)
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    base = f"{stem}_{datetime.date.today().isoformat()}"
    dst = os.path.join(folder, base + ".png")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(folder, f"{base}_{n}.png")
        n += 1
    shutil.copy2(src, dst)
    prev = bpy.data.images.load(dst, check_existing=False)
    prev.name = f"PREV_{stem}"
    prev.colorspace_settings.name = 'Non-Color' if kind == "NOR" else 'sRGB'
    engine.link_file(prev, dst)
    return prev


def _transfer_material(alb, nor):
    """uv_old -> the old ALB_ as Emission (EEVEE) and the old NOR_ as the surface's normal
    (Cycles Normal bake): the textures alone, none of the user's node tweaks."""
    mat = bpy.data.materials.new(_TMP_MAT)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = UV_OLD
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = alb
    tex.interpolation = 'Linear'
    nt.links.new(uv.outputs["UV"], tex.inputs["Vector"])
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
    nt.links.new(emit.outputs[0], out.inputs["Surface"])
    if nor is not None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        ntex = nt.nodes.new("ShaderNodeTexImage")
        ntex.image = nor
        ntex.interpolation = 'Linear'
        nt.links.new(uv.outputs["UV"], ntex.inputs["Vector"])
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.space = 'TANGENT'
        nmap.uv_map = UV_OLD
        nt.links.new(ntex.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        mat["_mcp_bsdf"] = bsdf.name
        mat["_mcp_emit"] = emit.name
    return mat


def _surface(mat, which):
    """Send the emission ('EMIT') or the BSDF with the normal map ('NORMAL') to the output."""
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL')
    src = nt.nodes[mat["_mcp_bsdf"] if which == 'NORMAL' else mat["_mcp_emit"]]
    nt.links.new(src.outputs[0], out.inputs["Surface"])


class _Swapped:
    """Every slot of obj on `mat` for the time of the transfer, put back after."""

    def __init__(self, obj, mat):
        self.obj, self.mat, self.saved = obj, mat, []

    def __enter__(self):
        for s in self.obj.material_slots:
            self.saved.append((s, s.material))
            s.material = self.mat
        if not self.saved:
            self.obj.data.materials.append(self.mat)
            self.saved = None
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            self.obj.data.materials.pop()
        else:
            for s, m in self.saved:
                s.material = m


def rebake_steps(context, obj):
    """ALB_ / NOR_ from uv_old onto uv_normal at the object's size, written over the same
    files (the old ones kept in _previous). A generator (jobs); returns the seconds."""
    import time

    import numpy as np

    from . import cache, gn_final, jobs, owned
    from .normal import pngio
    why = rebake_problem(obj)
    if why:
        raise RuntimeError(why)
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    size = common.resolution(obj, scene)
    t0 = time.perf_counter()
    if d.prev_alb is None:
        d.prev_alb = _keep_previous(d.alb_image, scene, "ALB")
    if d.nor_image is not None and d.prev_nor is None:
        d.prev_nor = _keep_previous(d.nor_image, scene, "NOR")
    nor_src = d.prev_nor if d.nor_image is not None else None
    mat = _transfer_material(d.prev_alb, nor_src)
    was_final = gn_final.is_final(obj)
    bn = None
    try:
        if gn_final.get_modifier(obj) is not None:
            gn_final.set_final(obj, scene, False)   # Final drops uv_old
        with common.shown(context, [obj]), _Swapped(obj, mat):
            context.view_layer.update()
            yield jobs.Step("Rebake: albedo (EEVEE)")
            px = engine.uv_render(context, obj, size, emit=False)
            covered = px[..., 3] > 0.0
            px[..., 3] = 1.0
            engine.to_srgb(px[..., :3])
            engine.extend_margin(px, covered, common.margin_px(s, size))
            del covered
            if nor_src is not None:
                _surface(mat, 'NORMAL')
                bn = bpy.data.images.new("MCP_REBAKE_NOR_TMP", size, size, alpha=False,
                                         float_buffer=True)
                bn.colorspace_settings.name = 'Non-Color'
                prev_uv = engine._uv_normal_active(obj)
                try:
                    with engine.render_state(scene), engine.selection(context, obj, [obj]), \
                            engine.target_nodes(obj, bn):
                        engine.configure(scene, 'NORMAL')
                        b = scene.render.bake
                        b.normal_space = 'TANGENT'
                        b.normal_r, b.normal_g, b.normal_b = 'POS_X', 'POS_Y', 'POS_Z'
                        yield jobs.Step("Rebake: normal (Cycles)")
                        yield from engine._bake(context, obj, [obj], 'NORMAL', size, bn,
                                                normal_space='TANGENT')
                finally:
                    engine._uv_restore(obj, prev_uv)
        # all baked: now the files are overwritten
        alb_path = common.image_file(d.alb_image)
        yield jobs.Step(f"Rebake: writing {os.path.basename(alb_path)}")
        tmp = bpy.data.images.new(engine.TMP_IMAGE, size, size, alpha=False, float_buffer=False)
        try:
            tmp.colorspace_settings.name = 'sRGB'
            tmp.pixels.foreach_set(px.ravel())
            del px
            tmp.filepath_raw = alb_path
            tmp.file_format = 'PNG'
            tmp.save(filepath=alb_path)
        finally:
            bpy.data.images.remove(tmp)
        d.alb_image.reload()
        d.alb_size = size
        if bn is not None:
            nor_path = common.image_file(d.nor_image)
            yield jobs.Step(f"Rebake: writing {os.path.basename(nor_path)} (16-bit)")
            buf = np.empty(size * size * 4, np.float32)
            bn.pixels.foreach_get(buf)
            pngio.write_rgb16(nor_path, buf.reshape(size, size, 4)[..., :3],
                              s.png_compression)
            del buf
            d.nor_image.reload()
            d.nor_size = size
    finally:
        if bn is not None:
            bpy.data.images.remove(bn)
        bpy.data.materials.remove(mat)
        if gn_final.get_modifier(obj) is not None and gn_final.is_final(obj) != was_final:
            gn_final.set_final(obj, scene, was_final)
    owned.record_used(scene)
    cache.clear(obj)
    d.last_bake_seconds = time.perf_counter() - t0
    return d.last_bake_seconds
