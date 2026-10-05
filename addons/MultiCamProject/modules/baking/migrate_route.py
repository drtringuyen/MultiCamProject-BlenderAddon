"""(2026-10-05) Bake Route: once per file (flag on the scenes) -

  * the packed BA_ / BN_ (baked from the Bake Source = the original) become the files
    BAo_ / BNo_ in the bake folder (nothing stays packed), stale copies go;
  * each object gets the route it baked with so far: projection + Bake Source -> Mixed,
    projection only -> From Projection, Bake Source only -> From Original;
  * MCP_ is rebuilt with the ROUTE switch;
  * an ALB_ that was up to date stays up to date (its state is stored again in the new form;
    the old form is kept below only for this comparison)."""
import hashlib
import os
import shutil

import bpy

from ..camera_project import core as cp
from . import cache, common, fingerprint, owned, route

KEY = "route_migrated"
_NEW_NODES = {cp.USE_ORIGINAL, cp.USE_PROJECTION, cp.GENERATED_NORMAL}


def _legacy_images(mat):
    if mat is None or not mat.node_tree:
        return []
    out = []
    for n in mat.node_tree.nodes:
        if n.name in _NEW_NODES:
            continue
        if n.type == 'TEX_IMAGE' and n.image:
            out.append((n.name, n.image.name, n.image.filepath))
        elif n.type == 'VALUE':
            out.append((n.name, round(n.outputs[0].default_value, 4)))
    return sorted(out, key=repr)


def _legacy_state(obj):
    """fingerprint.compute as it was before the Bake Route."""
    me = obj.data
    parts = [obj.name, me.name, common.data(obj).ba_fingerprint]
    parts += fingerprint._checksums(obj)
    mod = common.cp_modifier(obj)
    if mod is not None and mod.node_group is not None:
        d = cp.data(obj)
        n = cp.slot_count(d)
        parts.append(n)
        for i, cam in enumerate(cp.get_slots(d), 1):
            img = cp.cam_image(cam) if cam else None
            parts.append((cam.name if cam else "", img.filepath if img else ""))
        for k in ("Mode", "Previous Bake") + tuple(f"UV Shift Cam{i}" for i in range(1, n + 1)):
            try:
                v = cp.get_input(mod, k)
            except (KeyError, AttributeError):
                v = None
            parts.append((k, tuple(round(x, 5) for x in v) if hasattr(v, "__len__")
                           and not isinstance(v, str) else v))
        parts.append(_legacy_images(d.material))
    else:
        parts += [_legacy_images(s.material) for s in obj.material_slots
                  if not (s.material and s.material.get(cp.BAKED_TAG))]
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def _to_file(scene, img, name, normal):
    """A packed (or file) BA_/BN_ image -> <bake folder>/<name>.png, the image renamed and
    pointed at it. Returns the path ('' when there was nothing to write)."""
    from .engine import link_file
    path = common.texture_path(scene, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if img.packed_file is not None:
        with open(path, "wb") as f:
            f.write(img.packed_file.data)
    else:
        src = common.image_file(img)
        if not src or not os.path.isfile(src):
            return ""
        if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(path)):
            shutil.copyfile(src, path)
    other = bpy.data.images.get(name)
    if other is not None and other != img:
        other.name = name + "_stale"
    img.name = name
    img.colorspace_settings.name = 'Non-Color' if normal else 'sRGB'
    link_file(img, path)
    return path


def _route_for(obj):
    proj, orig = route.has_projection(obj), route.has_original(obj)
    if proj and orig:
        return route.MIXED
    if orig:
        return route.ORIGINAL
    return route.PROJECTION


def migrate(log=print):
    if not bpy.data.scenes or any(sc.multicamproject_bake_settings.get(KEY)
                                  for sc in bpy.data.scenes):
        return
    scene = bpy.context.scene or bpy.data.scenes[0]
    written = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.library:
            continue
        d = common.data(obj)
        try:
            was_ok = bool(d.fingerprint) and d.fingerprint == _legacy_state(obj)
        except Exception:
            was_ok = False
        if not d.handmade:
            with route.automatic():
                d.route = _route_for(obj)
        for attr, name, normal in (("ba_image", common.ba_name(obj), False),
                                   ("bn_image", common.bn_name(obj), True)):
            img = getattr(d, attr)
            if img is None or (img.name == name and img.packed_file is None):
                continue
            path = _to_file(obj.users_scene[0] if obj.users_scene else scene, img, name, normal)
            if path:
                written.append(path)
                log(f"[MultiCamProject] {img.name}: written to {path}")
        if route.has_projection(obj):
            cp.ensure_material(obj)
            cp.set_route(obj)
        cache.clear()
        if was_ok:
            d.fingerprint = fingerprint.compute(obj)
    for img in [i for i in bpy.data.images if i.name.startswith(common.LEGACY_PREFIXES)
                and (i.name.endswith("_stale") or i.users == 0)]:
        log(f"[MultiCamProject] removed the old {img.name}")
        bpy.data.images.remove(img)
    if written:
        owned.add(scene, written)
    for sc in bpy.data.scenes:
        sc.multicamproject_bake_settings[KEY] = 1
