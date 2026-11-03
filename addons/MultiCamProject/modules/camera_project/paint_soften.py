"""Soft camera edges for the bakes: MCP_ renders BAp_ / Mixed ALB_ / the blend mask from
blurred copies of the paint textures, the painted ones stay as they are.

Where the camera mix was set per face (Set Faces, Fill, a per-corner VCMix converted),
CMX1_ / CMX2_ / MSK_ G switch from one camera to another along a triangle edge. The
viewport shows the photos capped at 2K and hides it; the bake reads them at full size,
and two photos that do not line up exactly tear there. A short cross-fade does not.

Blurred: the camera weights (CMX1_ / CMX2_ R, G, B) and MSK_ G (which group shows).
MSK_ R - the painted scan / projection mask - stays sharp. The blur never crosses a UV
island's border (two islands side by side on uv_normal are far apart on the mesh); the
free UV space around each island is grown again from the blurred pixels (paint_tex.grow).
"""
from contextlib import contextmanager

import bpy
import numpy as np

from . import paint_tex

RADIUS = 4              # px at 1K: three box passes of this radius, a cross-fade of ~16 px
TMP = "MCP_SOFT_TMP_"


def triangle_islands(me):
    """uv_normal island per loop triangle (0..K-1): triangles sharing an edge with the same
    two vertices and the same UVs there."""
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    loops = np.empty(n * 3, np.int32)
    me.loop_triangles.foreach_get("loops", loops)
    vi = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", vi)
    co = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[paint_tex.UV_NORMAL].data.foreach_get("uv", co)
    verts = vi[loops].reshape(n, 3).astype(np.int64)
    q = np.round(co.reshape(-1, 2)[loops].reshape(n, 3, 2) * 1e5).astype(np.int64)
    a, b = [0, 1, 2], [1, 2, 0]
    swap = verts[:, a] > verts[:, b]
    v0 = np.where(swap, verts[:, b], verts[:, a])
    v1 = np.where(swap, verts[:, a], verts[:, b])
    q0 = np.where(swap[..., None], q[:, b], q[:, a])
    q1 = np.where(swap[..., None], q[:, a], q[:, b])
    keys = np.concatenate([v0[..., None], v1[..., None], q0, q1], axis=-1).reshape(-1, 6)
    tri = np.repeat(np.arange(n), 3)
    _u, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind='stable')
    same = inv[order][1:] == inv[order][:-1]
    ea, eb = tri[order][:-1][same], tri[order][1:][same]
    lab = np.arange(n)
    while len(ea):
        m = np.minimum(lab[ea], lab[eb])
        new = lab.copy()
        np.minimum.at(new, ea, m)
        np.minimum.at(new, eb, m)
        for _ in range(4):
            new = new[new]              # pointer jumping: labels are triangle indices
        if np.array_equal(new, lab):
            break
        lab = new
    return np.unique(lab, return_inverse=True)[1].ravel()


def island_pixels(me, size=paint_tex.SIZE):
    """(size, size) island id per pixel on uv_normal (rows bottom-up), -1 where no face is."""
    tris = paint_tex.mesh_triangles(me)
    ids = triangle_islands(me).astype(np.float32)
    col = np.zeros((len(ids), 3, 4), np.float32)
    col[..., 0] = ids[:, None]
    col[..., 3] = 1.0
    px, cov = paint_tex.raster_colors(tris, col, size, clip=False)
    out = np.where(cov, np.rint(px[:, 0]), -1).astype(np.int32)
    return out.reshape(size, size)


def _box(a, r, axis):
    c = np.cumsum(a, axis=axis, dtype=np.float64)
    pad = [(0, 0)] * a.ndim
    pad[axis] = (1, 0)
    c = np.pad(c, pad)
    n = a.shape[axis]
    hi = np.minimum(np.arange(n) + r + 1, n)
    lo = np.maximum(np.arange(n) - r, 0)
    return (np.take(c, hi, axis=axis) - np.take(c, lo, axis=axis)).astype(np.float32)


def _blur(a, r):
    for _ in range(3):              # three box passes: close to a Gaussian
        a = _box(_box(a, r, 0), r, 1)
    return a


def blur_islands(stack, isl, r=RADIUS):
    """stack (size, size, c) blurred inside each island only (normalised: pixels outside
    the island neither count nor change). Pixels no face covers stay as they are."""
    out = stack.copy()
    size = isl.shape[0]
    pad = 3 * r + 2
    for iid in np.unique(isl[isl >= 0]):
        m = isl == iid
        ys, xs = np.nonzero(m)
        y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, size)
        x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, size)
        mm = m[y0:y1, x0:x1]
        w = mm.astype(np.float32)
        num = _blur(stack[y0:y1, x0:x1] * w[..., None], r)
        den = _blur(w[..., None], r)
        out[y0:y1, x0:x1][mm] = (num / np.maximum(den, 1e-6))[mm]
    return out


def softened_pixels(obj, r=RADIUS):
    """{kind: (n, 4) pixels} of obj's paint textures with soft camera edges, None when obj
    has none (or they are not 1K)."""
    imgs = paint_tex.images(obj)
    if "CMX1" not in imgs or "MSK" not in imgs:
        return None
    size = paint_tex.SIZE
    if any(tuple(img.size) != (size, size) for img in imgs.values()):
        return None
    px = {k: paint_tex.read(img).reshape(size, size, 4) for k, img in imgs.items()}
    if obj.mode == 'EDIT':
        obj.update_from_editmode()      # else the mesh's UV data reads empty
    isl = island_pixels(obj.data, size)
    cams = [k for k in paint_tex.CAM_KINDS if k in px]
    stack = np.concatenate([px[k][..., :3] for k in cams] + [px["MSK"][..., 1:2]], axis=-1)
    soft = blur_islands(stack, isl, r)
    cov = (isl >= 0).ravel()
    out = {}
    for i, k in enumerate(cams):
        p = px[k].copy()
        p[..., :3] = soft[..., i * 3:i * 3 + 3]
        out[k] = paint_tex.grow(p.reshape(-1, 4), cov)
    m = px["MSK"].copy()
    m[..., 1] = soft[..., -1]
    out["MSK"] = paint_tex.grow(m.reshape(-1, 4), cov)
    return out


@contextmanager
def softened(obj, r=RADIUS):
    """MCP_ reads blurred copies of obj's paint textures for the moment (a bake render);
    the painted images are not touched. Does nothing for vertex painting, or when the blur
    cannot be made (no GPU: headless) - then the bake reads them as painted."""
    mat = None
    if paint_tex.mode(obj) == 'TEXTURE' and hasattr(obj, "multicamproject_cam"):
        from . import core
        mat = core.data(obj).material
    swapped, tmps = [], []
    try:
        soft = None
        if mat is not None and mat.node_tree is not None:
            try:
                soft = softened_pixels(obj, r)
            except Exception as e:      # noqa: BLE001 - a bake without the blur still works
                print(f"[MultiCamProject] {obj.name}: soft camera edges skipped ({e})")
        for kind, p in (soft or {}).items():
            node = mat.node_tree.nodes.get(paint_tex.node_name(kind))
            if node is None or node.image is None:
                continue
            size = node.image.size[0]
            tmp = bpy.data.images.new(TMP + kind, size, size, alpha=True, float_buffer=True)
            tmps.append(tmp)
            paint_tex.setup_image(tmp)
            tmp.pixels.foreach_set(p.ravel())
            swapped.append((node, node.image))
            node.image = tmp
        yield
    finally:
        for node, img in swapped:
            node.image = img
        for tmp in tmps:
            bpy.data.images.remove(tmp)
