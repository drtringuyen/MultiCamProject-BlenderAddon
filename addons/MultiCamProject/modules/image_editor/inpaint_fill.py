"""
inpaint_fill.py — pure numpy content fill for Inpaint (no bpy), like lasso_raster.py.

fill() rebuilds the pixels under a hole mask from the pixels around it:
  1. push-pull: average the known pixels down an image pyramid and pull them back up into
     the hole - a smooth colour field that follows gradients (the wall's light falloff)
  2. relax: a few Laplace steps inside the hole take the blockiness out of the pyramid
  3. grain: the fine detail (pixels minus their blur) of a nearby hole-shaped area of known
     pixels is copied on top, so the fill is not plastic-smooth next to the scan's noise
`valid` limits the pixels the fill may learn from (the UV islands: the black outside them
must never bleed in). Buffers are (h, w, 4) float32, rows bottom-up as Blender gives them.
Tested in tests/test_inpaint_fill.py.
"""

try:
    import numpy as np
except ImportError:
    np = None


# ── Masks ─────────────────────────────────────────────────────────────────────

def dilate(mask, px: int):
    """mask grown by `px` pixels (8-neighbourhood steps)."""
    out = mask.copy()
    for _ in range(max(0, int(px))):
        m = out.copy()
        m[1:, :] |= out[:-1, :]
        m[:-1, :] |= out[1:, :]
        m[:, 1:] |= out[:, :-1]
        m[:, :-1] |= out[:, 1:]
        m[1:, 1:] |= out[:-1, :-1]
        m[:-1, :-1] |= out[1:, 1:]
        m[1:, :-1] |= out[:-1, 1:]
        m[:-1, 1:] |= out[1:, :-1]
        out = m
    return out


def stamp_disc(mask, cx: float, cy: float, radius: float, value: bool = True):
    """Set (or clear) a disc of `mask` around image pixel (cx, cy), in place.
    Returns the touched rect (x0, y0, x1, y1) or None."""
    h, w = mask.shape
    r = max(0.5, float(radius))
    x0, x1 = max(0, int(cx - r)), min(w, int(cx + r) + 1)
    y0, y1 = max(0, int(cy - r)), min(h, int(cy + r) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    disc = (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= r * r
    sub = mask[y0:y1, x0:x1]
    if value:
        sub |= disc
    else:
        sub &= ~disc
    return x0, y0, x1, y1


def bbox(mask):
    """(x0, y0, x1, y1) of the True pixels, or None."""
    rows = np.flatnonzero(mask.any(axis=1))
    if not len(rows):
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def crop_rect(rect, w: int, h: int, margin: int):
    """rect grown by `margin` and clamped to the image."""
    x0, y0, x1, y1 = rect
    return (max(0, x0 - margin), max(0, y0 - margin),
            min(w, x1 + margin), min(h, y1 + margin))


def rasterize_triangles(tris, x0: int, y0: int, w: int, h: int, labels=None):
    """(h, w) bool: pixel centres of the crop at (x0, y0) inside any of `tris`
    ((n, 3, 2) image pixel coords) - the UV islands under the crop. With `labels` (one int
    per triangle, > 0): (h, w) int32, each pixel the label of its triangle, 0 outside."""
    out = np.zeros((h, w), dtype=bool if labels is None else np.int32)
    if tris is None or not len(tris):
        return out
    tris = np.asarray(tris, dtype=np.float64)
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    keep = (hi[:, 0] >= x0) & (lo[:, 0] <= x0 + w) & (hi[:, 1] >= y0) & (lo[:, 1] <= y0 + h)
    labs = np.asarray(labels)[keep] if labels is not None else np.ones(int(keep.sum()), np.int32)
    for t, a, b, lab in zip(tris[keep], lo[keep], hi[keep], labs):
        tx0, tx1 = max(x0, int(a[0])), min(x0 + w, int(np.ceil(b[0])) + 1)
        ty0, ty1 = max(y0, int(a[1])), min(y0 + h, int(np.ceil(b[1])) + 1)
        if tx1 <= tx0 or ty1 <= ty0:
            continue
        yy, xx = np.mgrid[ty0:ty1, tx0:tx1]
        px, py = xx + 0.5, yy + 0.5
        (ax, ay), (bx, by), (cx, cy) = t
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        eps = -1e-6
        inside = (l1 >= eps) & (l2 >= eps) & (1.0 - l1 - l2 >= eps)
        if labels is None:
            out[ty0 - y0:ty1 - y0, tx0 - x0:tx1 - x0] |= inside
        else:
            out[ty0 - y0:ty1 - y0, tx0 - x0:tx1 - x0][inside] = lab
    return out


# ── Fill ──────────────────────────────────────────────────────────────────────

def _down(c, w):
    """2x2 weighted average of c (h, w, C) by weights w (h, w) -> half size."""
    h, wd = w.shape
    if h % 2 or wd % 2:
        c = np.pad(c, ((0, h % 2), (0, wd % 2), (0, 0)))
        w = np.pad(w, ((0, h % 2), (0, wd % 2)))
        h, wd = w.shape
    cw = (c * w[..., None]).reshape(h // 2, 2, wd // 2, 2, -1).sum(axis=(1, 3))
    ws = w.reshape(h // 2, 2, wd // 2, 2).sum(axis=(1, 3))
    out = cw / np.maximum(ws, 1e-8)[..., None]
    return out, np.minimum(ws, 1.0)


def _box3(c):
    """3x3 box blur, edges clamped."""
    p = np.pad(c, ((1, 1), (1, 1), (0, 0)), mode='edge')
    h, w = c.shape[:2]
    acc = np.zeros_like(c)
    for dy in range(3):
        for dx in range(3):
            acc += p[dy:dy + h, dx:dx + w]
    return acc / 9.0


def push_pull(c, w):
    """c (h, w, C) with known-ness w (h, w) in 0..1 -> every pixel filled: known pixels
    kept, the rest pulled from coarser levels of the weighted pyramid."""
    h, wd = w.shape
    if w.min() >= 1.0:
        return c
    if h <= 1 or wd <= 1 or w.max() <= 0.0:
        tot = w.sum()
        mean = (c * w[..., None]).sum(axis=(0, 1)) / tot if tot > 0 else c.mean(axis=(0, 1))
        return w[..., None] * c + (1.0 - w[..., None]) * mean
    cd, wd2 = _down(c, w)
    coarse = push_pull(cd, wd2)
    up = np.repeat(np.repeat(coarse, 2, axis=0), 2, axis=1)[:h, :wd]
    up = _box3(up)
    return w[..., None] * c + (1.0 - w[..., None]) * up


def relax(c, hole, steps: int):
    """Laplace smoothing of the hole pixels only (the rest stays as boundary). Works on the
    hole's bounds plus a 1 px ring, not the whole crop."""
    box = bbox(hole) if steps > 0 else None
    if box is None:
        return c
    h, w = hole.shape
    x0, y0, x1, y1 = max(0, box[0] - 1), max(0, box[1] - 1), min(w, box[2] + 1), min(h, box[3] + 1)
    sub = c[y0:y1, x0:x1].copy()
    hm = hole[y0:y1, x0:x1]
    p = np.pad(sub, ((1, 1), (1, 1), (0, 0)), mode='edge')
    inner = p[1:-1, 1:-1]                       # a view: sub lives inside p
    hm3 = np.broadcast_to(hm[..., None], inner.shape)
    avg = np.empty_like(inner)
    for _ in range(steps):
        np.add(p[:-2, 1:-1], p[2:, 1:-1], out=avg)
        avg += p[1:-1, :-2]
        avg += p[1:-1, 2:]
        avg *= 0.25
        np.copyto(inner, avg, where=hm3)
        p[0, 1:-1], p[-1, 1:-1] = p[1, 1:-1], p[-2, 1:-1]     # keep the edge padding
        p[:, 0], p[:, -1] = p[:, 1], p[:, -2]
    sub = inner
    c = c.copy()
    c[y0:y1, x0:x1] = sub
    return c


def box_blur(c, w, r: int):
    """Weighted box blur of c by w with radius r (separable, via cumulative sums)."""
    def blur1(a, axis):
        a = np.moveaxis(a, axis, 0)
        n = a.shape[0]
        cs = np.cumsum(np.pad(a, [(1, 0)] + [(0, 0)] * (a.ndim - 1)), axis=0)
        idx = np.arange(n)
        hi = np.minimum(idx + r + 1, n)
        lo = np.maximum(idx - r, 0)
        return np.moveaxis(cs[hi] - cs[lo], 0, axis)
    num = blur1(blur1(c * w[..., None], 0), 1)
    den = blur1(blur1(w[..., None].astype(c.dtype), 0), 1)
    return num / np.maximum(den, 1e-8)


_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1))
GRAIN_TILE = 64


def _shift_for(ys, xs, size, known, min_cover=0.95, rings=(1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)):
    """(dx, dy) moving the pixels (ys, xs) onto known pixels, rings of `size` in 8
    directions, nearest first; or None."""
    h, w = known.shape
    for k in rings:
        best = None
        for ux, uy in _DIRS:
            dx, dy = int(round(ux * size * k)), int(round(uy * size * k))
            if (xs.min() + dx < 0 or xs.max() + dx >= w
                    or ys.min() + dy < 0 or ys.max() + dy >= h):
                continue
            if known[ys + dy, xs + dx].mean() >= min_cover:
                d = dx * dx + dy * dy
                if best is None or d < best[0]:
                    best = (d, dx, dy)
        if best is not None:
            return best[1], best[2]
    return None


def find_source_shift(hole, known, min_cover: float = 0.98):
    """(dx, dy) moving the whole hole onto known pixels nearby, or None."""
    box = bbox(hole)
    if box is None:
        return None
    ys, xs = np.nonzero(hole)
    return _shift_for(ys, xs, max(box[2] - box[0], box[3] - box[1]), known, min_cover,
                      rings=(1.0, 1.5, 2.0, 3.0))


def add_grain(smooth, rgb, hole, known, amount, radius=3, tile=GRAIN_TILE):
    """The fine detail (pixels minus their blur) of known pixels copied onto the hole, tile
    by tile, each tile from its own nearest fully known spot - a big hole gets grain too
    (one shift for the whole hole finds no known area of its size)."""
    ys, xs = np.nonzero(hole)
    if not len(ys):
        return smooth
    detail = rgb - box_blur(rgb, known.astype(np.float32), radius)
    # grain is noise: clip it to the known pixels' noise level, so an edge or a spot inside a
    # source tile is never stamped into the fill
    sigma = float(detail[known].std()) if known.any() else 0.0
    np.clip(detail, -2.5 * sigma, 2.5 * sigma, out=detail)
    key =(ys // tile) * (hole.shape[1] // tile + 1) + xs // tile
    order = np.argsort(key, kind='stable')
    ys, xs, key = ys[order], xs[order], key[order]
    cuts = np.flatnonzero(np.diff(key)) + 1
    for gy, gx in zip(np.split(ys, cuts), np.split(xs, cuts)):
        size = max(gx.max() - gx.min(), gy.max() - gy.min()) + 1
        shift = _shift_for(gy, gx, size, known)
        if shift is not None:
            dx, dy = shift
            smooth[gy, gx] += amount * detail[gy + dy, gx + dx]
    return smooth


def _fill_one(out, rgb, hole, known, grain, grain_radius):
    """Fill `hole` of out (in place) from `known`."""
    if not hole.any() or not known.any():
        return False
    smooth = push_pull(rgb, known.astype(np.float32))
    bh = bbox(hole)
    smooth = relax(smooth, hole, steps=min(80, max(8, max(bh[2] - bh[0], bh[3] - bh[1]) // 2)))
    if grain > 0.0:
        smooth = add_grain(smooth, rgb, hole, known, grain, grain_radius)
    out[..., :3][hole] = np.clip(smooth[hole], 0.0, 1.0)
    return True


def fill(crop, hole, valid=None, grow: int = 0, grain: float = 1.0, grain_radius: int = 3,
         labels=None):
    """crop (h, w, 4) with a fill of the `hole` pixels (grown by `grow`). Returns
    (out, hole_used).
    labels (h, w) int: UV island per pixel (0 = none). Each island's part of the hole is
    filled from that island only - a hole across a seam never pulls the other side's
    colours in, and pixels on no island stay. Without labels: learnt from `valid` pixels."""
    crop = np.asarray(crop, dtype=np.float32)
    hole = dilate(np.asarray(hole, dtype=bool), grow)
    out = crop.copy()
    rgb = crop[..., :3].copy()                  # float32: half the memory traffic
    if labels is not None:
        used = np.zeros_like(hole)
        for k in np.unique(labels[hole]):
            if k == 0:
                continue
            isl = labels == k
            hk = hole & isl
            if _fill_one(out, rgb, hk, isl & ~hole, grain, grain_radius):
                used |= hk
        return out, used
    if valid is None:
        valid = np.ones(hole.shape, dtype=bool)
    hole &= valid               # never paint outside the islands
    if not _fill_one(out, rgb, hole, valid & ~hole, grain, grain_radius):
        return out, np.zeros_like(hole)
    return out, hole


# ── UV islands ────────────────────────────────────────────────────────────────

def triangle_islands(verts, uvs):
    """UV island per triangle, 1..K: triangles are joined where they share an edge with the
    same two vertices AND the same UVs there (a seam splits). verts (n, 3) int vertex
    indices, uvs (n, 3, 2) float."""
    verts = np.asarray(verts, dtype=np.int64)
    n = len(verts)
    if not n:
        return np.zeros(0, dtype=np.int32)
    q = np.round(np.asarray(uvs, dtype=np.float64) * 1e5).astype(np.int64)
    a, b = [0, 1, 2], [1, 2, 0]
    va, vb, qa, qb = verts[:, a], verts[:, b], q[:, a], q[:, b]
    swap = va > vb
    v0, v1 = np.where(swap, vb, va), np.where(swap, va, vb)
    q0 = np.where(swap[..., None], qb, qa)
    q1 = np.where(swap[..., None], qa, qb)
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
    _u, dense = np.unique(lab, return_inverse=True)
    return (dense.ravel() + 1).astype(np.int32)


def dilate_labels(labels, px: int):
    """Island labels grown by `px` pixels into unlabelled pixels (the bake margin belongs to
    the island it was bled from)."""
    out = labels.copy()
    for _ in range(max(0, int(px))):
        empty = out == 0
        if not empty.any():
            break
        grown = out.copy()
        for src, dst in (((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
                         ((slice(1, None), slice(None)), (slice(None, -1), slice(None))),
                         ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                         ((slice(None), slice(1, None)), (slice(None), slice(None, -1)))):
            take = empty[dst] & (grown[dst] == 0)
            grown[dst][take] = out[src][take]
        out = grown
    return out
