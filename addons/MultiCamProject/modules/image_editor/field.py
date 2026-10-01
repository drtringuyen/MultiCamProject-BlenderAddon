"""Liquify warp field - pure numpy, no bpy (tested outside Blender: tests/test_liquify_field.py).

The photo is never edited. A warp field D (h, w, 2) at preview resolution holds, per pixel,
the offset in preview pixels to the photo pixel shown there (backward map):
    out[i, j] = src sampled at (j + D[i, j, 0], i + D[i, j, 1])
Pixel (i, j) - row i, column j - has its centre at x = j, y = i; rows run bottom-up like
Blender image pixels. The field is saved in UV units (field_uv), so it drives any resolution:
the preview while painting, the original size on bake (warp_image).

Every brush only touches the pixels under it and returns that box, so a dab costs the same
on any image size. Keep everything float32: float32 - int32 silently gives float64 in numpy
and doubles the cost.
"""
import numpy as np

PREVIEW_SIZE = 1024     # long side of the preview
PUCKER_RATE = 0.03      # share of the way to the brush centre a full-strength dab moves
                        # (dabs repeat at 30 Hz while the pen rests: ~1 s to pull in strongly)
UNDO_STEPS = 50


def preview_size(w, h, long_side=PREVIEW_SIZE):
    """(w, h) of the preview: long side PREVIEW_SIZE (never upscaled), same aspect."""
    s = min(1.0, long_side / max(w, h))
    return max(2, round(w * s)), max(2, round(h * s))


# ---------------------------------------------------------------- sampling

def bilinear(img, sx, sy):
    """img (h, w, c) sampled at pixel coords sx, sy (same shape), edges clamped."""
    h, w = img.shape[:2]
    sx = np.clip(sx, 0.0, w - 1.0)
    sy = np.clip(sy, 0.0, h - 1.0)
    x0f = np.minimum(np.floor(sx), w - 2)
    y0f = np.minimum(np.floor(sy), h - 2)
    fx = (sx - x0f)[..., None]
    fy = (sy - y0f)[..., None]
    x0 = x0f.astype(np.int32)
    y0 = y0f.astype(np.int32)
    a = img[y0, x0]
    b = img[y0, x0 + 1]
    c = img[y0 + 1, x0]
    d = img[y0 + 1, x0 + 1]
    top = a + (b - a) * fx
    bot = c + (d - c) * fx
    return top + (bot - top) * fy


def remap(src, D, y0, y1, x0, x1):
    """Warped pixels of the box rows y0:y1, columns x0:x1 (src and D share the size)."""
    ys = np.arange(y0, y1, dtype=np.float32)[:, None]
    xs = np.arange(x0, x1, dtype=np.float32)[None, :]
    d = D[y0:y1, x0:x1]
    return bilinear(src, xs + d[..., 0], ys + d[..., 1])


def resize(a, H, W):
    """Bilinear resize of a (h, w, c) array to (H, W, c), pixel centres aligned."""
    h, w = a.shape[:2]
    if (h, w) == (H, W):
        return a.astype(np.float32, copy=True)
    ys = ((np.arange(H, dtype=np.float32) + 0.5) * (h / H) - 0.5)[:, None]
    xs = ((np.arange(W, dtype=np.float32) + 0.5) * (w / W) - 0.5)[None, :]
    return bilinear(a.astype(np.float32, copy=False),
                    np.broadcast_to(xs, (H, W)), np.broadcast_to(ys, (H, W)))


def downscale(pixels, W, H):
    """Photo (h, w, 4) -> preview (H, W, 4): box average by the whole factor, then bilinear."""
    h, w = pixels.shape[:2]
    f = max(1, min(h // H, w // W))
    if f > 1:
        hh, ww = h // f * f, w // f * f
        pixels = pixels[:hh, :ww].reshape(hh // f, f, ww // f, f, -1).mean(axis=(1, 3))
    return resize(pixels.astype(np.float32, copy=False), H, W)


def _box_blur(a, r):
    """Separable box blur of radius r, edges clamped (small regions only)."""
    k = 2 * r + 1
    for axis in (0, 1):
        n = a.shape[axis]
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
        hi = np.take(c, np.arange(k, k + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        a = ((hi - lo) / k).astype(np.float32)
    return a


# ---------------------------------------------------------------- the session's field

class Liquify:
    """Warp field + warped preview of one photo. src: float32 (h, w, 4) preview pixels."""

    def __init__(self, src):
        self.src = np.ascontiguousarray(src, dtype=np.float32)
        self.h, self.w = self.src.shape[:2]
        if self.h < 2 or self.w < 2:
            raise ValueError("preview must be at least 2x2")
        self.D = np.zeros((self.h, self.w, 2), np.float32)
        self.out = self.src.copy()
        self._undo = []
        self._redo = []
        self._before = None      # D at stroke start
        self._box = None         # union of the stroke's dab boxes

    # ------------------------------------------------ coordinates

    def uv_to_px(self, u, v):
        return u * self.w - 0.5, v * self.h - 0.5

    def _brush_box(self, cx, cy, radius):
        y0 = max(0, int(np.floor(cy - radius)))
        y1 = min(self.h, int(np.ceil(cy + radius)) + 1)
        x0 = max(0, int(np.floor(cx - radius)))
        x1 = min(self.w, int(np.ceil(cx + radius)) + 1)
        if y0 >= y1 or x0 >= x1:
            return None
        return y0, y1, x0, x1

    @staticmethod
    def _falloff(box, cx, cy, radius):
        """Smooth weight 1 at the centre -> 0 at the radius, plus the box's offsets to the
        centre (dx, dy) for the brushes that need a direction."""
        y0, y1, x0, x1 = box
        dx = cx - np.arange(x0, x1, dtype=np.float32)[None, :]
        dy = cy - np.arange(y0, y1, dtype=np.float32)[:, None]
        q = np.clip(1.0 - (dx * dx + dy * dy) / np.float32(radius * radius), 0.0, 1.0)
        return (q * q).astype(np.float32), dx, dy

    # ------------------------------------------------ strokes / undo

    def stroke_begin(self):
        self._before = self.D.copy()
        self._box = None

    def stroke_end(self):
        """Close the stroke as one undo step. Returns its box (or None if nothing changed)."""
        box, before = self._box, self._before
        self._before = self._box = None
        if box is None or before is None:
            return None
        y0, y1, x0, x1 = box
        self._undo.append((box, before[y0:y1, x0:x1].copy(), self.D[y0:y1, x0:x1].copy()))
        del self._undo[:-UNDO_STEPS]
        self._redo.clear()
        return box

    def stroke_cancel(self):
        """Drop the running stroke (Esc). Returns the box that changed back, or None."""
        box, before = self._box, self._before
        self._before = self._box = None
        if box is None or before is None:
            return None
        y0, y1, x0, x1 = box
        self.D[y0:y1, x0:x1] = before[y0:y1, x0:x1]
        self._refresh(box)
        return box

    @property
    def in_stroke(self):
        return self._before is not None

    def _grow(self, box):
        if self._box is None:
            self._box = box
        else:
            a, b = self._box, box
            self._box = (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]))

    def _apply_step(self, src_stack, dst_stack, idx):
        if not src_stack:
            return None
        step = src_stack.pop()
        box = step[0]
        y0, y1, x0, x1 = box
        self.D[y0:y1, x0:x1] = step[idx]
        dst_stack.append(step)
        self._refresh(box)
        return box

    def undo(self):
        return self._apply_step(self._undo, self._redo, 1)

    def redo(self):
        return self._apply_step(self._redo, self._undo, 2)

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    # ------------------------------------------------ output

    def _refresh(self, box):
        y0, y1, x0, x1 = box
        self.out[y0:y1, x0:x1] = remap(self.src, self.D, y0, y1, x0, x1)

    def _changed(self, box):
        self._refresh(box)
        if self._before is not None:
            self._grow(box)
        return box

    def reset(self):
        """Back to the unwarped photo, as one undo step."""
        self.stroke_begin()
        self.D[:] = 0.0
        self._changed((0, self.h, 0, self.w))
        return self.stroke_end()

    # ------------------------------------------------ brushes
    # All take the brush centre (cx, cy) and radius in preview pixels and return the
    # changed box (y0, y1, x0, x1) or None.

    def _advect(self, box, t):
        """Move the shown content by t (bh, bw, 2) pixels: D'(p) = D(p - t) - t."""
        y0, y1, x0, x1 = box
        ys = np.arange(y0, y1, dtype=np.float32)[:, None]
        xs = np.arange(x0, x1, dtype=np.float32)[None, :]
        moved = bilinear(self.D, xs - t[..., 0], ys - t[..., 1])
        self.D[y0:y1, x0:x1] = moved - t
        return self._changed(box)

    def warp(self, cx, cy, radius, delta, strength=1.0):
        """Forward Warp: push the content under the brush along delta (mouse move, px)."""
        box = self._brush_box(cx, cy, radius)
        if box is None:
            return None
        wgt, _, _ = self._falloff(box, cx, cy, radius)
        wgt *= np.float32(min(max(strength, 0.0), 1.0))
        t = np.stack([wgt * np.float32(delta[0]), wgt * np.float32(delta[1])], axis=-1)
        return self._advect(box, t)

    def pucker(self, cx, cy, radius, amount):
        """Pucker (amount > 0) pulls the content toward the centre, Bloat (< 0) pushes it out."""
        box = self._brush_box(cx, cy, radius)
        if box is None or amount == 0:
            return None
        wgt, dx, dy = self._falloff(box, cx, cy, radius)
        k = wgt * np.float32(PUCKER_RATE * max(-1.0, min(1.0, amount)))
        t = np.stack([k * dx, k * dy], axis=-1)
        return self._advect(box, t)

    def reconstruct(self, cx, cy, radius, amount):
        """Fade the warp back toward the original photo (amount 1: fully at the centre)."""
        box = self._brush_box(cx, cy, radius)
        if box is None or amount <= 0:
            return None
        y0, y1, x0, x1 = box
        wgt, _, _ = self._falloff(box, cx, cy, radius)
        keep = 1.0 - wgt * np.float32(min(amount, 1.0))
        self.D[y0:y1, x0:x1] *= keep[..., None]
        return self._changed(box)

    def smooth(self, cx, cy, radius, amount):
        """Even out the warp under the brush (blur of the field, not of the pixels)."""
        box = self._brush_box(cx, cy, radius)
        if box is None or amount <= 0:
            return None
        y0, y1, x0, x1 = box
        r = max(1, int(radius / 4))
        ey0, ey1 = max(0, y0 - r), min(self.h, y1 + r)
        ex0, ex1 = max(0, x0 - r), min(self.w, x1 + r)
        blurred = _box_blur(self.D[ey0:ey1, ex0:ex1], r)[y0 - ey0:y1 - ey0, x0 - ex0:x1 - ex0]
        wgt, _, _ = self._falloff(box, cx, cy, radius)
        wgt = (wgt * np.float32(min(amount, 1.0)))[..., None]
        cur = self.D[y0:y1, x0:x1]
        self.D[y0:y1, x0:x1] = cur + (blurred - cur) * wgt
        return self._changed(box)

    # ------------------------------------------------ save / load

    def field_uv(self):
        """The field in UV units (offset / preview size), for the sidecar file."""
        return self.D / np.array([self.w, self.h], np.float32)

    def load_field_uv(self, f):
        """Load a saved UV field (any size) and refresh the whole preview. Clears undo."""
        f = resize(np.asarray(f, np.float32), self.h, self.w)
        self.D[:] = f * np.array([self.w, self.h], np.float32)
        self._undo.clear()
        self._redo.clear()
        self._refresh((0, self.h, 0, self.w))


# ---------------------------------------------------------------- bake

def warp_image(src, field_uv, strip=256):
    """Apply a UV field to a full-size photo src (H, W, 4). Works in strips of rows so the
    upsampled field and the sampling never exist at full size at once."""
    src = np.ascontiguousarray(src, dtype=np.float32)
    H, W = src.shape[:2]
    f = np.asarray(field_uv, np.float32)
    h, w = f.shape[:2]
    scale = np.array([W, H], np.float32)
    out = np.empty_like(src)
    fx = np.broadcast_to(((np.arange(W, dtype=np.float32) + 0.5) * (w / W) - 0.5)[None, :], (1, W))
    xs = np.arange(W, dtype=np.float32)[None, :]
    for s in range(0, H, strip):
        e = min(H, s + strip)
        rows = np.arange(s, e, dtype=np.float32)[:, None]
        fy = (rows + 0.5) * (h / H) - 0.5
        n = e - s
        d = bilinear(f, np.broadcast_to(fx, (n, W)), np.broadcast_to(fy, (n, W))) * scale
        out[s:e] = bilinear(src, xs + d[..., 0], rows + d[..., 1])
    return out
