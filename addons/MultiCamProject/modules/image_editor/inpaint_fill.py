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


def rasterize_triangles(tris, x0: int, y0: int, w: int, h: int):
    """(h, w) bool: pixel centres of the crop at (x0, y0) inside any of `tris`
    ((n, 3, 2) image pixel coords) - the UV islands under the crop."""
    out = np.zeros((h, w), dtype=bool)
    if tris is None or not len(tris):
        return out
    tris = np.asarray(tris, dtype=np.float64)
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    keep = (hi[:, 0] >= x0) & (lo[:, 0] <= x0 + w) & (hi[:, 1] >= y0) & (lo[:, 1] <= y0 + h)
    for t, a, b in zip(tris[keep], lo[keep], hi[keep]):
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
        out[ty0 - y0:ty1 - y0, tx0 - x0:tx1 - x0] |= inside
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
    """Laplace smoothing of the hole pixels only (the rest stays as boundary)."""
    if steps <= 0 or not hole.any():
        return c
    c = c.copy()
    for _ in range(steps):
        p = np.pad(c, ((1, 1), (1, 1), (0, 0)), mode='edge')
        avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
        c[hole] = avg[hole]
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


def find_source_shift(hole, known, min_cover: float = 0.98):
    """(dx, dy) moving the hole onto an area of known pixels nearby, or None. Tries rings
    of the hole's own size in 8 directions, nearest first."""
    box = bbox(hole)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    h, w = hole.shape
    ys, xs = np.nonzero(hole)
    best = None
    for k in (1.0, 1.5, 2.0, 3.0):
        for ux, uy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
            dx, dy = int(round(ux * bw * k)), int(round(uy * bh * k))
            sx, sy = xs + dx, ys + dy
            ok = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
            if ok.mean() < 1.0:
                continue
            cover = known[sy, sx].mean()
            if cover >= min_cover:
                d = dx * dx + dy * dy
                if best is None or d < best[0]:
                    best = (d, dx, dy)
        if best is not None:
            return best[1], best[2]
    return None


def fill(crop, hole, valid=None, grow: int = 0, grain: float = 1.0, grain_radius: int = 3):
    """crop (h, w, 4) with a fill of the `hole` pixels (grown by `grow`), learnt from the
    pixels that are `valid` and outside the hole. Returns (out, hole_used)."""
    crop = np.asarray(crop, dtype=np.float32)
    hole = dilate(np.asarray(hole, dtype=bool), grow)
    if valid is None:
        valid = np.ones(hole.shape, dtype=bool)
    hole &= valid               # never paint outside the islands
    known = valid & ~hole
    if not hole.any() or not known.any():
        return crop.copy(), hole
    rgb = crop[..., :3].astype(np.float64)
    smooth = push_pull(rgb, known.astype(np.float64))
    bh = bbox(hole)
    smooth = relax(smooth, hole, steps=min(80, max(8, max(bh[2] - bh[0], bh[3] - bh[1]) // 2)))
    if grain > 0.0:
        shift = find_source_shift(hole, known)
        if shift is not None:
            detail = rgb - box_blur(rgb, known.astype(np.float64), grain_radius)
            ys, xs = np.nonzero(hole)
            dx, dy = shift
            smooth[ys, xs] += grain * detail[ys + dy, xs + dx]
    out = crop.copy()
    out[..., :3][hole] = np.clip(smooth[hole], 0.0, 1.0)
    return out, hole
