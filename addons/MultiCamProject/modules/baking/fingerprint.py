"""State hash of everything the albedo bake depends on. Stored at the bake; a different
current value means the bake is outdated. Cheap: no image is ever loaded (paths only)."""
import hashlib

import bpy
import numpy as np

from ..camera_project import core as cp
from ..camera_project import gn_builder
from . import cache, common

_WEIGHTS = {}


def _checksum(attr, field):
    """Sum and position-weighted sum of an attribute's data - catches edits and moves."""
    n = len(attr.data)
    width = {"color": 4, "vector": 2}[field]
    buf = np.empty(n * width, dtype=np.float32)
    attr.data.foreach_get(field, buf)
    w = _WEIGHTS.get(len(buf))
    if w is None:
        w = _WEIGHTS[len(buf)] = (np.arange(len(buf)) % 97).astype(np.float32)
    return f"{float(buf.sum(dtype=np.float64)):.4f}/{float(np.dot(buf, w)):.1f}"


def _material_images(mat):
    if mat is None or not mat.node_tree:
        return []
    out = []
    for n in mat.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image:
            out.append((n.name, n.image.name, n.image.filepath))
        elif n.type == 'VALUE':
            out.append((n.name, round(n.outputs[0].default_value, 4)))
    return sorted(out, key=repr)


def _checksums(obj):
    """The mesh data part (uv_normal, the mesh's own VCMix layers) - the only costly part.
    Mesh edits trigger a depsgraph update, so this is safe to cache."""
    me = obj.data
    uv = me.attributes.get(common.UV_NORMAL)     # the FLOAT2 attribute behind the UV map
    out = [_checksum(uv, "vector") if uv else "no uv_normal"]
    for name in gn_builder.LAYERS:
        a = me.color_attributes.get(name)
        out.append(_checksum(a, "color") if a is not None else "")
    return out


def compute(obj, cached=False):
    """`cached`: reuse the mesh checksums (panel redraws). Everything else - modifier
    inputs, cameras, images - is read live: changing an input of a disabled modifier
    (the projection while Final is on) triggers no depsgraph update."""
    me = obj.data
    parts = [obj.name, me.name]
    parts += cache.get(obj, "checksums", _checksums) if cached else _checksums(obj)
    mod = common.cp_modifier(obj)
    if (mod is not None and mod.node_group is not None
            and hasattr(bpy.types.Object, "multicamproject_cam")):     # camera_project on
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
        parts.append(_material_images(d.material))     # photos, scan images, Original Scan
    else:
        parts += [_material_images(s.material) for s in obj.material_slots]
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def current(obj):
    """compute() with the mesh checksums cached (for panel redraws)."""
    return compute(obj, cached=True)


def is_outdated(obj):
    d = common.data(obj)
    return bool(d.fingerprint) and d.fingerprint != current(obj)
