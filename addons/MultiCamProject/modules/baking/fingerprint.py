"""State hashes, stored at each bake; a different current value means that bake is outdated.
Two levels:
  BA_ (Bake from Source): the low poly's shape, uv_normal and the Bake Source.
  ALB_ (Bake Final): the projection, the paint and which BA_ it was made from - a new
      BA_ outdates ALB_ too.
Cheap: no image is ever loaded (paths only)."""
import hashlib
import json
import time

import bpy
import numpy as np

from ..camera_project import core as cp
from ..camera_project import gn_builder
from . import cache, common

_WEIGHTS = {}


def _vec_checksum(attr, width):
    """_checksum of a vector attribute with `width` floats per element."""
    buf = np.empty(len(attr.data) * width, dtype=np.float32)
    attr.data.foreach_get("vector", buf)
    w = _WEIGHTS.get(len(buf))
    if w is None:
        w = _WEIGHTS[len(buf)] = (np.arange(len(buf)) % 97).astype(np.float32)
    return f"{float(buf.sum(dtype=np.float64)):.4f}/{float(np.dot(buf, w)):.1f}"


def _source_parts(obj):
    me = obj.data
    pos = me.attributes.get("position")
    uv = me.attributes.get(common.UV_NORMAL)
    return [len(me.vertices), len(me.polygons),
            _vec_checksum(pos, 3) if pos is not None else "",
            _vec_checksum(uv, 2) if uv is not None else "no uv_normal"]


def _source_list(obj, cached=False, cage=None):
    d = common.data(obj)
    src = d.bake_source
    parts = list(cache.get(obj, "source_parts", _source_parts) if cached else _source_parts(obj))
    if src is not None:
        parts += [src.name, src.data.name if src.data else "", len(src.data.vertices)
                  if src.type == 'MESH' else 0]
    parts.append(round(common.cage(obj) if cage is None else cage, 5))
    return parts


def source_state(obj, cached=False, cage=None):
    """Hash of what Bake from Source depends on: the low poly's shape and uv_normal, the
    Bake Source (by name and size) and its own Cage (`cage`: try another value)."""
    return hashlib.sha1(repr(_source_list(obj, cached, cage)).encode()).hexdigest()


def _named(parts):
    """The parts by what they are - stored readable, so the panel can say what changed."""
    return {"mesh": repr(parts[:2]), "shape": repr(parts[2]), "uv_normal": repr(parts[3]),
            "Bake Source": repr(parts[4:-1]), "Cage": repr(parts[-1])}


def stamp_source(obj):
    """What a Bake from Source stores: the state + when (so ALB_ sees every new BA_). Also
    keeps the parts readable in ba_parts (ba_why)."""
    parts = _source_list(obj)
    common.data(obj).ba_parts = json.dumps(_named(parts))
    return f"{hashlib.sha1(repr(parts).encode()).hexdigest()}:{time.time():.0f}"


def ba_outdated(obj):
    """BA_ exists but the mesh, uv_normal or the source changed since, or it was baked at
    another resolution than the object's (one resolution for all its textures)."""
    d = common.data(obj)
    if not d.ba_fingerprint:
        return False
    if d.ba_size and d.ba_size != common.resolution(obj):
        return True
    return d.ba_fingerprint.split(":")[0] != source_state(obj, cached=True)


_WHY = {"mesh": "vertex / face count", "shape": "mesh shape", "uv_normal": "uv_normal",
        "Bake Source": "Bake Source", "Cage": "Cage"}


def ba_why(obj):
    """Why BA_ is outdated, in words ('' = it is not)."""
    if not ba_outdated(obj):
        return ""
    d = common.data(obj)
    if d.ba_size and d.ba_size != common.resolution(obj):
        return f"baked at {d.ba_size // 1024}K, set to {common.resolution(obj) // 1024}K"
    try:
        old = json.loads(d.ba_parts) if d.ba_parts else None
    except ValueError:
        old = None
    if not old:
        return "mesh, uv_normal or Bake Source changed (not recorded before 2026-10-05)"
    now = _named(_source_list(obj, cached=True))
    changed = [_WHY[k] for k in _WHY if old.get(k) != now[k]]
    return f"{', '.join(changed) or 'something'} changed since Bake from Source"


def _checksum(attr, field, alpha=None):
    """Sum and position-weighted sum of an attribute's data - catches edits and moves.
    `alpha`: a float attribute whose larger values replace the colors' alpha."""
    n = len(attr.data)
    width = {"color": 4, "vector": 2}[field]
    buf = np.empty(n * width, dtype=np.float32)
    attr.data.foreach_get(field, buf)
    if alpha is not None and len(alpha.data) == n:
        a = np.empty(n, dtype=np.float32)
        alpha.data.foreach_get("value", a)
        buf[3::4] = np.maximum(buf[3::4], a)
    w = _WEIGHTS.get(len(buf))
    if w is None:
        w = _WEIGHTS[len(buf)] = (np.arange(len(buf)) % 97).astype(np.float32)
    return f"{float(buf.sum(dtype=np.float64)):.4f}/{float(np.dot(buf, w)):.1f}"


# MCP_ nodes that are not inputs of ALB_: the route's work textures (covered by their own
# stamps) and the route switch (the route is a part of its own)
_NOT_INPUTS = {cp.BAKED_ALBEDO, cp.BAKED_NORMAL, cp.GENERATED_NORMAL, cp.USE_ORIGINAL,
               cp.USE_PROJECTION}


def _material_images(mat):
    if mat is None or not mat.node_tree:
        return []
    out = []
    for n in mat.node_tree.nodes:
        if n.name in _NOT_INPUTS:
            continue
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
        # VCMix2 alpha as the GN shows it: paint_sync may hold it in the stash
        stash = me.attributes.get(gn_builder.MASK2_STASH) if name == gn_builder.LAYERS[1] else None
        if stash is not None and (stash.data_type != 'FLOAT' or a is None or stash.domain != a.domain):
            stash = None
        out.append(_checksum(a, "color", stash) if a is not None else "")
    return out


def _projection_parts(obj, cached=False):
    """What the projection shows: uv_normal, the VCMix layers (RGB and alpha), the slot
    cameras and their photos, the modifier's inputs and MCP_'s images."""
    parts = list(cache.get(obj, "checksums", _checksums) if cached else _checksums(obj))
    mod = common.cp_modifier(obj)
    if (mod is None or mod.node_group is None
            or not hasattr(bpy.types.Object, "multicamproject_cam")):     # camera_project off
        return parts + ["no projection"]
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
    parts.append(_material_images(d.material))     # the photos
    return parts


def projection_state(obj, cached=False):
    """Hash of what the projection shows (BAp_ and Mixed's ALB_ depend on it)."""
    return hashlib.sha1(repr(_projection_parts(obj, cached)).encode()).hexdigest()


def stamp_projection(obj):
    """What a BAp_ render stores: the state + when (so ALB_ sees every new BAp_)."""
    return f"{projection_state(obj)}:{time.time():.0f}"


def bp_outdated(obj):
    """BAp_ exists but the projection changed since, or it is at another resolution."""
    d = common.data(obj)
    if not d.bp_fingerprint:
        return False
    if d.bp_size and d.bp_size != common.resolution(obj):
        return True
    return d.bp_fingerprint.split(":")[0] != projection_state(obj, cached=True)


def compute(obj, cached=False):
    """The state ALB_ / NOR_ were baked from, by the object's Bake Route: the route itself,
    From Original BAo_'s stamp, From Projection what the projection shows, Mixed both.
    `cached`: reuse the mesh checksums (panel redraws). Everything else - modifier inputs,
    cameras, images - is read live: changing an input of a disabled modifier (the
    projection while Final is on) triggers no depsgraph update."""
    from . import route
    d = common.data(obj)
    r = route.get(obj)
    parts = [obj.name, obj.data.name, r]
    if r in {route.ORIGINAL, route.MIXED}:
        parts.append(d.ba_fingerprint)
    if r in {route.PROJECTION, route.MIXED}:
        parts += _projection_parts(obj, cached)
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def current(obj):
    """compute() with the mesh checksums cached (for panel redraws)."""
    return compute(obj, cached=True)


def is_outdated(obj):
    d = common.data(obj)
    return bool(d.fingerprint) and d.fingerprint != current(obj)


def original_outdated(obj):
    """The route uses the Original and BAo_ is missing or outdated."""
    from . import route
    d = common.data(obj)
    return (route.uses_original(obj) and d.bake_source is not None
            and (d.ba_image is None or ba_outdated(obj)))


def projection_outdated(obj):
    """The route uses the Projection and BAp_ is outdated (a missing BAp_ is just made)."""
    from . import route
    return route.uses_projection(obj) and bp_outdated(obj)
