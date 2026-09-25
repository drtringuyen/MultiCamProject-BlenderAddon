"""Export checks. Each returns a list of Issue; the panel shows them in the summary box
and inline in the status rows - never as popups. Nothing here loads image pixels or
reads the size of an unloaded image: sizes come from the bake or from PNG headers."""
import os
import struct
from collections import namedtuple

import bpy
import numpy as np

from ..baking import cache, common, fingerprint

Issue = namedtuple("Issue", "obj code text severity")

ERROR, WARNING, INFO = 'ERROR', 'WARNING', 'INFO'

# code -> (summary text with {n}, fix operator or None, fix label)
SUMMARY = {
    'NAME': ("{n} name(s) need renaming", "multicamproject.export_rename", "Rename objects"),
    'TRANSFORM': ("{n} transform(s) not applied", "multicamproject.export_fix_transforms",
                  "Fix transforms"),
    'OUTDATED': ("{n} outdated bake(s)", "multicamproject.export_update_outdated",
                 "Update outdated"),
    'UV_MISSING': ("{n} missing uv_normal", None, "create it by hand"),
    'UV_BAD': ("{n} uv_normal outside 0-1 / overlapping", None, "fix it by hand"),
    'UV_EXTRA': ("{n} with extra UV maps (dropped in the FBX)", None, ""),
    'NOT_BAKED': ("{n} not baked", "multicamproject.export_bake_missing", "Bake missing"),
    'FILE': ("{n} texture file(s) missing", "multicamproject.export_bake_missing", "Bake missing"),
    'TEXTURE': ("{n} texture size / format problem(s)", "multicamproject.export_bake_missing",
                "Bake again"),
    'SHARED_MESH': ("{n} mesh(es) shared by several objects", None, "make single user"),
    'MESH': ("{n} mesh warning(s)", None, ""),
    'COLOR': ("{n} without scan color (Color from ALB)", None, ""),
}

# ---------------------------------------------------------------- names

def check_names(obj, plan):
    """`plan`: fixes.planned_names of all EXPORT objects ({object: the name it should have})."""
    out = []
    if obj in plan:
        out.append(Issue(obj.name, 'NAME', f"should be '{plan[obj]}'", WARNING))
    d = common.data(obj)
    for item, want in ((d.material, common.mat_name(obj)), (d.alb_image, common.alb_name(obj)),
                       (d.nor_image, common.nor_name(obj))):
        if item is not None and item.name != want:
            out.append(Issue(obj.name, 'NAME', f"'{item.name}' should be '{want}'", WARNING))
    for img in (d.alb_image, d.nor_image):
        path = common.image_file(img)
        if path and os.path.splitext(os.path.basename(path))[0] != img.name:
            out.append(Issue(obj.name, 'NAME', f"file '{os.path.basename(path)}' should be "
                             f"'{img.name}.png'", WARNING))
    return out


# ---------------------------------------------------------------- textures folders

def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def used_files():
    """Every file an image of this .blend points at."""
    return {_norm(bpy.path.abspath(i.filepath, library=i.library))
            for i in bpy.data.images if i.source in {'FILE', 'SEQUENCE', 'TILED'} and i.filepath}


OWN = ("ALB_", "NOR_")     # the add-on's own textures - the only files a clean-up touches


def pngs(folder):
    """ALB_*.png / NOR_*.png in `folder`. Other files are never listed: the folder may be
    shared with other assets (e.g. a team's textures folder)."""
    try:
        return [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                if f.lower().endswith(".png") and f.startswith(OWN)
                and os.path.isfile(os.path.join(folder, f))]
    except OSError:
        return []


def unused_textures(scene):
    """ALB_/NOR_ PNGs in the bake folder that no image of this .blend uses."""
    used = used_files()
    return [p for p in pngs(common.output_dir(scene)) if _norm(p) not in used]


def export_textures_dir(scene):
    return os.path.join(bpy.path.abspath(scene.multicamproject_export.folder), "Textures")


def stale_export_textures(scene, keep_names):
    """ALB_/NOR_ PNGs in <export>/Textures/ that are not `keep_names` (texture names) and
    that no image of this .blend uses."""
    used = used_files()
    keep = {n.lower() + ".png" for n in keep_names}
    return [p for p in pngs(export_textures_dir(scene))
            if os.path.basename(p).lower() not in keep and _norm(p) not in used]


# ---------------------------------------------------------------- UV

def _uv_stats(obj):
    """(outside 0-1, overlap share) of uv_normal, from a coarse rasterization."""
    me = obj.data
    attr = me.attributes.get(common.UV_NORMAL)
    if attr is None:
        return 0.0, 0.0
    uv = np.empty(len(attr.data) * 2, dtype=np.float32)
    attr.data.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2)
    outside = float(((uv < -1e-4) | (uv > 1 + 1e-4)).any(axis=1).mean()) if len(uv) else 0.0
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    if nt == 0:
        return outside, 0.0
    loops = np.empty(nt * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("loops", loops)
    tri = uv[loops].reshape(nt, 3, 2).astype(np.float64)
    grid = 512
    lo = np.floor(tri.min(axis=1) * grid).astype(np.int64)
    hi = np.ceil(tri.max(axis=1) * grid).astype(np.int64)
    lo, hi = np.clip(lo, 0, grid), np.clip(hi, 0, grid)
    w, h = np.maximum(hi[:, 0] - lo[:, 0], 0), np.maximum(hi[:, 1] - lo[:, 1], 0)
    cnt = w * h
    total = int(cnt.sum())
    if total == 0 or total > 30_000_000:
        return outside, 0.0
    idx = np.repeat(np.arange(nt), cnt)
    k = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    px = lo[idx, 0] + k % w[idx]
    py = lo[idx, 1] + k // w[idx]
    p = (np.stack([px, py], axis=1) + 0.5) / grid
    a, b, c = tri[idx, 0], tri[idx, 1], tri[idx, 2]

    def cross(o, u, v):
        return (u[:, 0] - o[:, 0]) * (v[:, 1] - o[:, 1]) - (u[:, 1] - o[:, 1]) * (v[:, 0] - o[:, 0])

    area = cross(a, b, c)
    s = np.sign(area)
    inside = ((cross(a, b, p) * s > 1e-12) & (cross(b, c, p) * s > 1e-12)
              & (cross(c, a, p) * s > 1e-12) & (np.abs(area) > 1e-14))
    hits = np.bincount(px[inside] * grid + py[inside], minlength=grid * grid)
    covered = int((hits > 0).sum())
    return outside, (float((hits > 1).sum()) / covered if covered else 0.0)


def uv_stats(obj):
    return cache.get(obj, "uv_stats", _uv_stats)


def check_uv(obj):
    me = obj.data
    if me.uv_layers.get(common.UV_NORMAL) is None:
        return [Issue(obj.name, 'UV_MISSING', "no uv_normal (make it by hand)", ERROR)]
    out = []
    outside, overlap = uv_stats(obj)
    if outside > 0:
        out.append(Issue(obj.name, 'UV_BAD', f"uv_normal: {outside:.1%} of corners outside 0-1",
                         WARNING))
    if overlap > 0.005:
        out.append(Issue(obj.name, 'UV_BAD', f"uv_normal: {overlap:.1%} overlapping", WARNING))
    from ..camera_project import core as cp
    known = {common.UV_NORMAL, cp._scan_uv(obj, [])}
    extra = [u.name for u in me.uv_layers if u.name not in known and not u.name.startswith("UV_cam")]
    if extra:
        out.append(Issue(obj.name, 'UV_EXTRA', f"extra UV maps {', '.join(extra)} "
                         "(only uv_normal goes into the FBX)", INFO))
    return out


def check_color(obj, scene):
    s = common.settings(scene)
    if s.color_source == 'SCAN_ATTRIBUTE' and obj.data.color_attributes.get(s.scan_color_name) is None:
        return [Issue(obj.name, 'COLOR', f"no '{s.scan_color_name}' - Color comes from ALB", INFO)]
    return []


# ---------------------------------------------------------------- transform + mesh

def _bbox_pivot(obj):
    """Bottom center of the mesh in world orientation, relative to the origin."""
    me = obj.data
    n = len(me.vertices)
    if n == 0:
        return (0.0, 0.0, 0.0)
    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world.to_3x3(), dtype=np.float32)
    co = co.reshape(n, 3) @ m.T
    lo, hi = co.min(axis=0), co.max(axis=0)
    return (float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2, float(lo[2]))


def users_of_mesh(me):
    return [o for o in bpy.data.objects if o.data == me]


def check_transform(obj):
    out = []
    if len(users_of_mesh(obj.data)) > 1:
        return [Issue(obj.name, 'SHARED_MESH', "mesh shared by several objects - transforms "
                      "cannot be applied", WARNING)]
    loc, rot, sca = obj.matrix_world.decompose()
    if obj.matrix_world.to_3x3().determinant() < 0:
        out.append(Issue(obj.name, 'TRANSFORM', "negative scale", WARNING))
    elif rot.angle > 1e-4 or any(abs(v - 1.0) > 1e-4 for v in sca):
        out.append(Issue(obj.name, 'TRANSFORM', "rotation/scale not applied", WARNING))
    pivot = cache.get(obj, "pivot", _bbox_pivot)
    if max(abs(v) for v in pivot) > 0.001:
        out.append(Issue(obj.name, 'TRANSFORM', "origin not at the bottom center", WARNING))
    return out


def _mesh_counts(obj):
    me = obj.data
    lt = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum()), int((lt > 4).sum())


def mesh_counts(obj):
    """(triangles, n-gons)."""
    return cache.get(obj, "mesh_counts", _mesh_counts)


def check_mesh(obj):
    _tris, ngons = mesh_counts(obj)
    if ngons:
        return [Issue(obj.name, 'MESH', f"{ngons} n-gons (tangents may differ in Unity)", INFO)]
    return []


# ---------------------------------------------------------------- textures + state

def png_header(path):
    """(width, height, bit depth, color type) of a PNG file, or None."""
    try:
        with open(path, "rb") as f:
            h = f.read(26)
        if h[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, hh, depth, ctype = struct.unpack(">IIBB", h[16:26])
        return w, hh, depth, ctype
    except OSError:
        return None


def check_textures(obj, scene):
    d = common.data(obj)
    s = common.settings(scene)
    out = []
    if d.alb_image is None:
        return [Issue(obj.name, 'NOT_BAKED', "not baked", WARNING)] if common.has_uv_normal(obj) else []
    for img, label, depth in ((d.alb_image, "ALB", 8), (d.nor_image, "NOR", 16)):
        if img is None:
            if label == "NOR":
                out.append(Issue(obj.name, 'NOT_BAKED', "no normal map", WARNING))
            continue
        path = common.image_file(img)
        if not path or not os.path.isfile(path):
            out.append(Issue(obj.name, 'FILE', f"{label} file missing: {path or img.name}", ERROR))
            continue
        hdr = cache.get(obj, f"png_{label}_{os.path.getmtime(path)}", lambda _o: png_header(path))
        if hdr is None:
            out.append(Issue(obj.name, 'TEXTURE', f"{label} is not a PNG", WARNING))
            continue
        w, h, dep, _ct = hdr
        if (w, h) != (s.resolution, s.resolution):
            out.append(Issue(obj.name, 'TEXTURE', f"{label} is {w}x{h}, not {s.resolution}", WARNING))
        if dep != depth:
            out.append(Issue(obj.name, 'TEXTURE', f"{label} is {dep}-bit, not {depth}-bit", WARNING))
    if d.nor_image is not None and d.nor_image.colorspace_settings.name != 'Non-Color':
        out.append(Issue(obj.name, 'TEXTURE', "NOR is not Non-Color", WARNING))
    if d.material is None:
        out.append(Issue(obj.name, 'NOT_BAKED', "no MAT_ material", WARNING))
    return out


def check_state(obj):
    if fingerprint.is_outdated(obj):
        return [Issue(obj.name, 'OUTDATED', "outdated - the projection changed since the bake",
                      WARNING)]
    return []


def object_issues(obj, scene, plan):
    return (check_names(obj, plan) + check_uv(obj) + check_color(obj, scene)
            + check_transform(obj) + check_mesh(obj) + check_textures(obj, scene)
            + check_state(obj))


# ---------------------------------------------------------------- scenes

SceneInfo = namedtuple("SceneInfo", "name cameras images shared")


def other_scenes(scene):
    """The file's other scenes: their cameras, the images those cameras show, and the
    objects they share with `scene`."""
    mine = set(scene.objects)
    out = []
    for sc in bpy.data.scenes:
        if sc == scene:
            continue
        cams = [o for o in sc.objects if o.type == 'CAMERA']
        imgs = {bg.image for c in cams for bg in c.data.background_images if bg.image}
        shared = sorted(o.name for o in sc.objects if o in mine and o.type == 'MESH')
        out.append(SceneInfo(sc.name, len(cams), len(imgs), shared))
    return out
