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
    written as a PNG into the export textures folder. Returns a note or ''."""
    path = common.image_file(img)
    if path and os.path.isfile(path) and path.lower().endswith(".png"):
        return ""
    name = common.alb_name(obj) if kind == "ALB" else common.nor_name(obj)
    dst = common.texture_path(scene, name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
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
# Edit UV copies the textures to <bake folder>/_previous/<name>_<date>.png; Rebake always
# reads from those, so it can run again on a changed uv_normal. Finish drops uv_old.
# GN-Final removes uv_old from the Final result, the exporter keeps uv_normal only.

UV_OLD = "uv_old"
PREVIOUS_DIR = "_previous"
_TMP_MAT = "MCP_REBAKE_TMP"


def has_uv_old(obj):
    return obj.type == 'MESH' and obj.data.uv_layers.get(UV_OLD) is not None


def uv_state(obj):
    """A checksum of uv_normal (the layout ALB_ / NOR_ have to be on)."""
    from .fingerprint import _vec_checksum
    attr = obj.data.attributes.get(common.UV_NORMAL)
    return f"{len(obj.data.loops)}/{_vec_checksum(attr, 2)}" if attr is not None else ""


def needs_rebake(obj):
    """uv_normal changed since ALB_ / NOR_ last matched it (only during a UV edit). Unknown in
    Edit Mode (the UVs reach the mesh when it is left): False, the operators check again."""
    return has_uv_old(obj) and obj.mode != 'EDIT' and common.data(obj).uv_rebaked != uv_state(obj)


def finish_problem(obj):
    """Why Finish would leave the textures on the wrong layout ('' = it can finish)."""
    if needs_rebake(obj):
        return "uv_normal changed since the textures were made - Rebake first (or Cancel)"
    return ""


def _same_file(a, b):
    pa, pb = common.image_file(a), common.image_file(b)
    return bool(pa and pb) and os.path.normcase(os.path.abspath(pa)) == os.path.normcase(
        os.path.abspath(pb))


def _same_content(a, b):
    """Two files with the same size and time: a copy (Export copies with copy2)."""
    pa, pb = common.image_file(a), common.image_file(b)
    try:
        sa, sb = os.stat(pa), os.stat(pb)
    except (OSError, TypeError):
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def _shown_pairs(obj):
    """[(kind, the image MAT_ shows, ALB_ / NOR_)] where they are different images."""
    d = common.data(obj)
    alb, nor, _uv = _images(d.material)
    return [(kind, shown, ours) for kind, shown, ours in
            (("ALB", alb, d.alb_image), ("NOR", nor, d.nor_image))
            if shown is not None and ours is not None and shown != ours]


def shown_mismatch(obj):
    """'' or why MAT_ does not show ALB_ / NOR_ (an image node picked by hand, e.g. an
    older export copy): Rebake writes ALB_ / NOR_, the viewport would not change. An
    identical copy (same size and time) is fine - show_ours puts ALB_ / NOR_ in."""
    for kind, shown, ours in _shown_pairs(obj):
        if not _same_file(shown, ours) and not _same_content(shown, ours):
            return (f"MAT_ shows {common.image_file(shown) or shown.name}, but {kind}_ is "
                    f"{common.image_file(ours) or ours.name} (another picture) - put "
                    f"{ours.name} into MAT_'s image node (or untick + tick Handmade)")
    return ""


def show_ours(obj):
    """MAT_'s image nodes on an identical copy of ALB_ / NOR_ get ALB_ / NOR_ themselves."""
    d = common.data(obj)
    nt = d.material.node_tree if d.material is not None else None
    if nt is None:
        return
    for _kind, shown, ours in _shown_pairs(obj):
        if _same_file(shown, ours) or _same_content(shown, ours):
            for n in nt.nodes:
                if n.type == 'TEX_IMAGE' and n.image == shown:
                    n.image = ours


def start_uv_edit(obj, scene):
    """uv_old = a copy of uv_normal (only when there is none yet: it is what the textures
    were painted on). The textures as they are now go to _previous right away and stay the
    Rebake source - an Undo can then never pair uv_old with an already rebaked file.
    Returns True when it was made now."""
    uvs = obj.data.uv_layers
    if uvs.get(common.UV_NORMAL) is None:
        raise RuntimeError("No uv_normal")
    if uvs.get(UV_OLD) is not None:
        return False
    why = shown_mismatch(obj)
    if why:
        raise RuntimeError(why)
    show_ours(obj)
    d = common.data(obj)
    if d.alb_image is None or not os.path.isfile(common.image_file(d.alb_image)):
        raise RuntimeError("ALB_ has no file - tick Handmade again")
    d.prev_alb = _keep_previous(d.alb_image, scene, "ALB")
    d.prev_nor = (_keep_previous(d.nor_image, scene, "NOR")
                  if d.nor_image is not None and os.path.isfile(common.image_file(d.nor_image))
                  else None)
    active = uvs.active
    uvs.active = uvs[common.UV_NORMAL]
    new = uvs.new(name=UV_OLD, do_init=True)        # copies the active UV map
    if new is None:
        raise RuntimeError("No room for another UV map (8 at most)")
    uvs.active = uvs[common.UV_NORMAL] if active is None else active
    uvs[common.UV_NORMAL].active_render = True
    d.uv_rebaked = uv_state(obj)        # the textures are on this layout
    d.rebaked = False
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
    d.uv_rebaked = ""
    d.rebaked = False
    from . import gn_final
    if gn_final.get_modifier(obj) is not None:
        gn_final.write_inputs(obj, scene)


def cancel_uv_edit(obj, scene):
    """Back to before Edit UV: uv_old goes back into uv_normal and, when a Rebake already
    wrote ALB_ / NOR_, the _previous copies are put back into their files. Then as Finish.
    Returns notes."""
    import shutil
    notes = []
    uvs = obj.data.uv_layers
    old, new = uvs.get(UV_OLD), uvs.get(common.UV_NORMAL)
    if old is None:
        return notes
    if new is not None:
        import numpy as np
        buf = np.empty(len(obj.data.loops) * 2, np.float32)
        old.uv.foreach_get("vector", buf)
        new.uv.foreach_set("vector", buf)
        notes.append("uv_normal back to uv_old")
    d = common.data(obj)
    if d.rebaked:
        for img, prev in ((d.alb_image, d.prev_alb), (d.nor_image, d.prev_nor)):
            dst, src = common.image_file(img), common.image_file(prev)
            if img is None or not (src and dst and os.path.isfile(src)):
                continue
            shutil.copy2(src, dst)
            _reload_file(dst)
            notes.append(f"{os.path.basename(dst)} back from _previous")
        from . import cache
        cache.clear(obj)
    obj.data.update()
    finish_uv_edit(obj, scene)
    return notes


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
    if d.prev_alb is None or not os.path.isfile(common.image_file(d.prev_alb)):
        return "The old textures are gone from _previous - Cancel the UV edit, then Edit UV again"
    return shown_mismatch(obj) or common.uv_collapsed_text(obj)


def _target(img, name, scene):
    """Where a rebaked texture goes: <export folder>/Textures/<name>.png, next to every other
    ALB_ / NOR_. A file already there that the image does not use yet is copied to the bake
    folder's _previous first (never into the export); the image's old file (e.g. textures/)
    stays where it is - another .blend may use it."""
    import shutil
    path = common.texture_path(scene, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cur = common.image_file(img)
    if os.path.isfile(path) and not (cur and os.path.normcase(os.path.abspath(cur))
                                     == os.path.normcase(os.path.abspath(path))):
        folder = os.path.join(common.output_dir(scene), PREVIOUS_DIR)
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, f"{name}_replaced.png")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(folder, f"{name}_replaced_{n}.png")
            n += 1
        shutil.copy2(path, dst)
    return path


def _point_at(img, path, name):
    """img on its (new) file and named after it; every image on that file reloads."""
    cur = common.image_file(img)
    if not cur or os.path.normcase(os.path.abspath(cur)) != os.path.normcase(os.path.abspath(path)):
        engine.link_file(img, path)
    if img.name != name and bpy.data.images.get(name) is None:
        img.name = name
    _reload_file(path)


def _reload_file(path):
    """Every image on `path` shows the new pixels (not only ALB_ / NOR_ themselves)."""
    for img in bpy.data.images:
        p = common.image_file(img)
        if p and os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(path)):
            img.reload()


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
    """ALB_ / NOR_ from uv_old onto uv_normal at the object's size, written into the bake
    folder (the old ones kept in _previous). A generator (jobs); returns the seconds."""
    import time

    import numpy as np

    from . import cache, gn_final, jobs, owned
    from .normal import pngio
    why = rebake_problem(obj)
    if why:
        raise RuntimeError(why)
    show_ours(obj)
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    size = common.resolution(obj, scene)
    t0 = time.perf_counter()
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
        # all baked: now the files are written - into <export>/Textures with every other ALB_/NOR_
        alb_path = _target(d.alb_image, common.alb_name(obj), scene)
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
        _point_at(d.alb_image, alb_path, common.alb_name(obj))
        d.alb_size = size
        if bn is not None:
            nor_path = _target(d.nor_image, common.nor_name(obj), scene)
            yield jobs.Step(f"Rebake: writing {os.path.basename(nor_path)} (16-bit)")
            buf = np.empty(size * size * 4, np.float32)
            bn.pixels.foreach_get(buf)
            pngio.write_rgb16(nor_path, buf.reshape(size, size, 4)[..., :3],
                              s.png_compression)
            del buf
            d.nor_image.colorspace_settings.name = 'Non-Color'
            _point_at(d.nor_image, nor_path, common.nor_name(obj))
            d.nor_size = size
    finally:
        if bn is not None:
            bpy.data.images.remove(bn)
        bpy.data.materials.remove(mat)
        if gn_final.get_modifier(obj) is not None and gn_final.is_final(obj) != was_final:
            gn_final.set_final(obj, scene, was_final)
    d.uv_rebaked = uv_state(obj)        # Finish may go now
    d.rebaked = True
    owned.record_used(scene)
    copied = _to_export_textures(obj, scene)
    if copied:
        yield jobs.Step(f"Rebake: {len(copied)} texture(s) copied to Export Textures/")
    cache.clear(obj)
    d.last_bake_seconds = time.perf_counter() - t0
    return d.last_bake_seconds


def _to_export_textures(obj, scene):
    """The rebaked ALB_ / NOR_ into <export folder>/Textures/ right away (as Export copies
    them), so that folder always holds the latest. [] without an export folder."""
    from . import owned
    es = getattr(scene, "multicamproject_export", None)
    if es is None or not es.folder:
        return []
    try:
        from ..export import fbx
    except ImportError:
        return []
    copies = list(fbx.copy_textures([obj], bpy.path.abspath(es.folder)).values())
    owned.add(scene, copies)
    return copies
