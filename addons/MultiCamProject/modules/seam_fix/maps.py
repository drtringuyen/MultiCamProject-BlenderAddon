"""The two images Seam Fix paints on uv_normal (Non-Color, packed in the .blend):

    SF_mask_<object>    how much each texel takes the source's continuation (Fix Seam):
                        1 on a seam's target side, fading over the blend width. Faded out
                        where the read would land on another island, outside the texture
                        or in black padding
    SF_level_<object>   0.5 + the colour offset that makes both sides of every seam meet
                        at their average (Seam-leveling), fading into each island

Both fade with a Gaussian of the seam lines: blur(seeds) / blur(seeds on a straight line),
so a seam's own value holds exactly on it and nothing has steps."""
import os

import bpy
import numpy as np

from . import geometry as geo

MASK = "SF_mask_"
LEVEL = "SF_level_"


def mask_name(obj):
    return MASK + obj.name


def level_name(obj):
    return LEVEL + obj.name


# ---------------------------------------------------------------- pixels in and out

def load_rgb(img, res):
    """(res, res, 3) of the image's stored values (sRGB 0-1 for an sRGB image), row 0 at
    v = 0, resized to res; plus alpha (res, res) or None."""
    path = bpy.path.abspath(img.filepath) if img.filepath else ""
    if path and os.path.isfile(path) and img.packed_file is None:
        import OpenImageIO as oiio
        src = oiio.ImageBuf(path)
        nch = src.spec().nchannels
        dst = oiio.ImageBufAlgo.resize(src, roi=oiio.ROI(0, res, 0, res, 0, 1, 0, nch))
        arr = np.asarray(dst.get_pixels(oiio.FLOAT), dtype=np.float32)[::-1]
    else:
        tmp = img.copy()
        tmp.scale(res, res)
        arr = np.empty(res * res * 4, dtype=np.float32)
        tmp.pixels.foreach_get(arr)
        bpy.data.images.remove(tmp)
        arr = arr.reshape(res, res, 4)
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    alpha = arr[..., 3].copy() if arr.shape[2] > 3 else None
    return np.ascontiguousarray(arr[..., :3]), alpha


def write_image(name, rgb, fill=0.0):
    """A packed Non-Color image `name` holding rgb ((res, res) or (res, res, 3), row 0 at
    v = 0). Replaced when its size changed."""
    res = rgb.shape[0]
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) != (res, res):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=False)
    img.colorspace_settings.name = 'Non-Color'
    rgba = np.ones((res, res, 4), dtype=np.float32)
    rgba[..., :3] = rgb[..., None] if rgb.ndim == 2 else rgb
    img.pixels.foreach_set(np.clip(rgba, 0, 1).ravel())
    img.pack()
    return img


def ensure_neutral(obj, res=64):
    """Both images exist (black mask, grey level) so the shader can be built before either
    step ran."""
    if bpy.data.images.get(mask_name(obj)) is None:
        write_image(mask_name(obj), np.zeros((res, res), dtype=np.float32))
    if bpy.data.images.get(level_name(obj)) is None:
        write_image(level_name(obj), np.full((res, res, 3), 0.5, dtype=np.float32))


# ---------------------------------------------------------------- blurs and seeds

def _box(a, r, axis):
    if r < 1:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad), axis=axis, dtype=np.float32)
    n = a.shape[axis]
    return (np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis)
            - np.take(c, np.arange(n), axis)) / (2 * r + 1)


def gauss(a, sigma):
    """Three box blurs per axis - close to a Gaussian of `sigma` pixels."""
    r = max(1, int(round((np.sqrt(4 * sigma * sigma + 1) - 1) / 2)))
    for axis in (0, 1):
        for _ in range(3):
            a = _box(a, r, axis)
    return a


def _splat(S, VS, p0, p1, v0, v1, offset):
    """Seed points every half pixel from p0 to p1 (pixel coords, minus `offset`), values
    from v0 to v1, into S (weights) and VS (weighted values)."""
    p0, p1 = np.asarray(p0, dtype=np.float64), np.asarray(p1, dtype=np.float64)
    n = max(2, int(np.ceil(np.linalg.norm(p1 - p0) / 0.5)) + 1)
    t = np.linspace(0.0, 1.0, n)
    pts = p0[None] + (p1 - p0)[None] * t[:, None] - offset
    ix, iy = np.floor(pts[:, 0]).astype(int), np.floor(pts[:, 1]).astype(int)
    ok = (ix >= 0) & (ix < S.shape[1]) & (iy >= 0) & (iy < S.shape[0])
    ix, iy, t = ix[ok], iy[ok], t[ok]
    np.add.at(S, (iy, ix), 0.5)
    if VS is not None:
        vals = np.asarray(v0)[None] * (1 - t[:, None]) + np.asarray(v1)[None] * t[:, None]
        np.add.at(VS, (iy, ix), 0.5 * vals)
    return iy, ix


def _fade(GS, seed_pix):
    """GS normalised to 1 on the seed lines (their median), clipped to 0..1."""
    on = GS[seed_pix]
    L = float(np.median(on[on > 0])) if (on > 0).any() else 1.0
    return np.clip(GS / max(L, 1e-8), 0.0, 1.0)


def _dilate_into(dst, src_mask, free, px):
    """Copies dst values `px` pixels outward from src_mask into free pixels (so bilinear
    sampling at an island's edge sees its own values)."""
    have = src_mask.copy()
    for _ in range(px):
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh_have = np.roll(have, (dy, dx), (0, 1))
            take = free & ~have & sh_have
            if take.any():
                dst[take] = np.roll(dst, (dy, dx), (0, 1))[take]
                have |= take


# ---------------------------------------------------------------- Fix Seam: the mask

def build_mask(obj, seams, strips, albedo, blend_px, tex_res, res):
    """SF_mask_ for the strip faces from geometry.build_fixseam. Returns stats."""
    me = obj.data
    owner, _face, _ = geo.raster_islands(me, geo.UV_SRC, seams.island, res)
    rgb, alpha = load_rgb(albedo, res)
    sigma = max(0.75, blend_px * res / tex_res / 2)

    S = np.zeros((res, res), dtype=np.float32)
    seed_y, seed_x = [], []
    for (_ia, _ib, _a1, _a2, b1, b2, *_k) in seams.sides:
        iy, ix = _splat(S, None, np.array(b1) * res, np.array(b2) * res, 0, 0, 0)
        seed_y.append(iy)
        seed_x.append(ix)
    if not seed_y:
        write_image(mask_name(obj), np.zeros((res, res), dtype=np.float32))
        return {"strip_px": 0}
    GS = gauss(S, sigma)
    weight = _fade(GS, (np.concatenate(seed_y), np.concatenate(seed_x)))

    uv = me.uv_layers[geo.UV_SRC].data
    fix = me.uv_layers[geo.UV_FIX].data
    src_of = dict(strips)
    strip = np.zeros((res, res), dtype=bool)
    bad = np.zeros((res, res), dtype=np.float32)
    n_read = n_bad = n_pad = 0
    for tri in me.loop_triangles:
        src = src_of.get(tri.polygon_index)
        if src is None:
            continue
        xs, ys, w0, w1, w2 = geo.raster_tri(*(uv[i].uv for i in tri.loops), res, bary=True)
        ok = (xs >= 0) & (xs < res) & (ys >= 0) & (ys < res)
        xs, ys, w0, w1, w2 = xs[ok], ys[ok], w0[ok], w1[ok], w2[ok]
        if not len(xs):
            continue
        f0, f1, f2 = (np.array(fix[i].uv) for i in tri.loops)
        q = (w0[:, None] * f0 + w1[:, None] * f1 + w2[:, None] * f2) * res
        qx, qy = np.floor(q[:, 0]).astype(int), np.floor(q[:, 1]).astype(int)
        inside = (qx >= 0) & (qx < res) & (qy >= 0) & (qy < res)
        qo = np.full(len(xs), -2)
        qo[inside] = owner[qy[inside], qx[inside]]
        black = np.zeros(len(xs), dtype=bool)
        if inside.any():
            c = rgb[qy[inside], qx[inside]]
            dark = c.max(axis=1) < 2 / 255
            if alpha is not None:
                dark |= alpha[qy[inside], qx[inside]] < 0.5
            black[inside] = dark & (qo[inside] == -1)
        wrong = ~inside | ((qo != -1) & (qo != src)) | black
        reads = weight[ys, xs] > 0.05
        n_read += int(reads.sum())
        n_bad += int((wrong & reads).sum())
        n_pad += int((reads & inside & (qo == -1)).sum())
        strip[ys, xs] = True
        bad[ys[wrong], xs[wrong]] = 1.0

    keep = np.clip(1.0 - 2.0 * gauss(bad, sigma), 0.0, 1.0)
    mask = np.where(strip, weight * keep, 0.0).astype(np.float32)
    _dilate_into(mask, strip, owner == -1, 2)
    write_image(mask_name(obj), mask)
    return {"blend_px": blend_px, "reads": n_read,
            "reads_suppressed": round(n_bad / max(n_read, 1), 3),
            "reads_in_margin": round(n_pad / max(n_read, 1), 3)}


# ---------------------------------------------------------------- Seam-leveling

def _sample_side(rgb, owner, isl, p0, p1, res, inset=2.0):
    """Mean colour just inside island `isl` along the seam p0-p1 (UV), or None."""
    p0, p1 = np.array(p0) * res, np.array(p1) * res
    d = p1 - p0
    length = np.linalg.norm(d)
    if length < 1e-6:
        return None
    n = np.array([-d[1], d[0]]) / length
    vals = []
    for t in (0.15, 0.35, 0.5, 0.65, 0.85):
        base = p0 + d * t
        for sign in (1, -1):
            p = base + n * inset * sign
            x, y = int(p[0]), int(p[1])
            if 1 <= x < res - 1 and 1 <= y < res - 1 and owner[y, x] == isl:
                win = owner[y - 1:y + 2, x - 1:x + 2] == isl
                vals.append(rgb[y - 1:y + 2, x - 1:x + 2][win].mean(axis=0))
                break
    return np.mean(vals, axis=0) if vals else None


def build_level(obj, seams, albedo, falloff_px, tex_res, res, max_jump=64 / 255):
    """SF_level_: per seam corner, the offset that brings its side to the seam's average
    colour (averaged over the corner's seam edges); spread along each seam and faded into
    each island separately. A seam whose sides differ by more than max_jump runs along a
    real colour edge (pillow / sheet) and is left alone. Returns stats."""
    me = obj.data
    owner, _face, _ = geo.raster_islands(me, geo.UV_SRC, seams.island, res)
    rgb, _alpha = load_rgb(albedo, res)
    sigma = max(1.0, falloff_px * res / tex_res / 2)

    corner = {}      # key -> [sum offset (3), count]
    edges = []       # (island, uv1, uv2, key1, key2) per seam side
    jumps = []
    skipped = 0
    for (ia, ib, a1, a2, b1, b2, ka1, ka2, kb1, kb2) in seams.sides:
        ca = _sample_side(rgb, owner, ia, a1, a2, res)
        cb = _sample_side(rgb, owner, ib, b1, b2, res)
        if ca is None or cb is None:
            continue
        mean = (ca + cb) / 2
        jump = float(np.abs(ca - cb).max())
        jumps.append(jump)
        if jump > max_jump:
            skipped += 1
            continue
        for key, off in ((ka1, mean - ca), (ka2, mean - ca), (kb1, mean - cb), (kb2, mean - cb)):
            acc = corner.setdefault(key, [np.zeros(3), 0])
            acc[0] += off
            acc[1] += 1
        edges.append((ia, a1, a2, ka1, ka2))
        edges.append((ib, b1, b2, kb1, kb2))

    out = np.full((res, res, 3), 0.5, dtype=np.float32)
    by_island = {}
    for e in edges:
        by_island.setdefault(e[0], []).append(e)
    pad = int(np.ceil(4 * sigma)) + 2
    for isl, es in by_island.items():
        ys, xs = np.nonzero(owner == isl)
        if not len(ys):
            continue
        y0, y1 = max(0, ys.min() - pad), min(res, ys.max() + pad + 1)
        x0, x1 = max(0, xs.min() - pad), min(res, xs.max() + pad + 1)
        h, w = y1 - y0, x1 - x0
        S = np.zeros((h, w), dtype=np.float32)
        VS = np.zeros((h, w, 3), dtype=np.float32)
        sy, sx = [], []
        for (_i, p1, p2, k1, k2) in es:
            v1 = corner[k1][0] / corner[k1][1]
            v2 = corner[k2][0] / corner[k2][1]
            iy, ix = _splat(S, VS, np.array(p1) * res, np.array(p2) * res, v1, v2,
                            np.array([x0, y0]))
            sy.append(iy)
            sx.append(ix)
        GS = gauss(S, sigma)
        GVS = np.stack([gauss(VS[..., c], sigma) for c in range(3)], axis=-1)
        field = GVS / np.maximum(GS, 1e-8)[..., None] * _fade(GS, (np.concatenate(sy),
                                                                   np.concatenate(sx)))[..., None]
        crop_owner = owner[y0:y1, x0:x1]
        mine = crop_owner == isl
        region = out[y0:y1, x0:x1]
        region[mine] = 0.5 + field[mine]
        sub = region.copy()
        _dilate_into(sub, mine, crop_owner == -1, 2)
        grow = (crop_owner == -1) & np.any(sub != region, axis=-1)
        region[grow] = sub[grow]
    write_image(level_name(obj), out)
    return {"falloff_px": falloff_px, "seam_sides": len(edges) // 2,
            "skipped_colour_edges": skipped,
            "jump_median_255": round(float(np.median(jumps)) * 255, 1) if jumps else 0.0,
            "jump_max_255": round(float(np.max(jumps)) * 255, 1) if jumps else 0.0}
