"""Normal map from the albedo: brightness -> high-pass -> Sobel slopes -> normals.

Blurs are running-sum box blurs (x3 ~ Gaussian), so the cost does not depend on the radius.
Seams: the blur is a normalized convolution over the UV islands only (pixels outside the
islands and the bake margin are black), so the background never bleeds into the relief;
outside the islands the map is flat.
"""
import numpy as np

CHUNK = 512         # rows/columns blurred at once in float64


def read_pixels(img):
    """RGB float32 (h, w, 3) of a Blender image, rows bottom-up."""
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)[:, :, :3]


def luminance_and_mask(rgb, size):
    """Brightness and island mask, box-downsampled to `size` when smaller."""
    h = rgb.shape[0]
    lum = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    mask = (rgb.sum(axis=2) > 0.0).astype(np.float32)
    f = h // size
    if f > 1:
        lum = lum[:size * f, :size * f].reshape(size, f, size, f).mean(axis=(1, 3))
        mask = (mask[:size * f, :size * f].reshape(size, f, size, f).mean(axis=(1, 3)) > 0.5)
        mask = mask.astype(np.float32)
    return lum.astype(np.float32), mask


def _box1d(a, r, axis):
    """In-place running-sum box blur of radius r along `axis`, edges clamped."""
    k = 2 * r + 1
    n = a.shape[axis]
    for s in range(0, a.shape[1 - axis], CHUNK):
        sl = (slice(None), slice(s, s + CHUNK)) if axis == 0 else (slice(s, s + CHUNK), slice(None))
        block = a[sl].astype(np.float64)
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(block, pad, mode="edge"), axis=axis)
        hi = np.take(c, np.arange(k, k + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        a[sl] = ((hi - lo) / k).astype(np.float32)


def blur(a, r):
    a = a.copy()
    for _ in range(3):
        _box1d(a, r, 1)
        _box1d(a, r, 0)
    return a


def high_pass(lum, mask, radius):
    """lum minus its island-only blur; 0 outside the islands."""
    low = blur(lum * mask, radius)
    cover = blur(mask, radius)
    low /= np.maximum(cover, 1e-6)
    del cover
    return (lum - low) * mask


def normals_from_height(hp, mask, strength, invert):
    """Sobel slopes (per UV unit, so a 2K preview matches the 8K result) -> RGB 0..1,
    OpenGL (Y+): the rows are bottom-up, so +row = +V."""
    size = hp.shape[0]
    p = np.pad(hp, 1, mode="edge")
    # Sobel: difference along one axis, [1 2 1]/4 smoothing along the other
    sx = p[:, 2:] - p[:, :-2]
    dx = (sx[:-2] + 2 * sx[1:-1] + sx[2:]) / 8.0
    del sx
    sy = p[2:] - p[:-2]
    dy = (sy[:, :-2] + 2 * sy[:, 1:-1] + sy[:, 2:]) / 8.0
    del sy, p
    k = strength * size / 1000.0 * (-1.0 if invert else 1.0)
    nx, ny = -dx * k, -dy * k
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + 1.0)
    out = np.empty(hp.shape + (3,), dtype=np.float32)
    out[..., 0] = nx * inv * 0.5 + 0.5
    out[..., 1] = ny * inv * 0.5 + 0.5
    out[..., 2] = inv * 0.5 + 0.5
    flat = mask < 0.5
    out[flat] = (0.5, 0.5, 1.0)
    return out


def generate(alb_img, size, settings):
    """Normal map RGB (size, size, 3) from the albedo image."""
    rgb = read_pixels(alb_img)
    lum, mask = luminance_and_mask(rgb, size)
    del rgb
    full = max(alb_img.size[0], 1)
    radius = max(1, round(settings.nor_radius * size / max(settings.resolution, 1)))
    hp = high_pass(lum, mask, radius)
    del lum
    if full < size:     # albedo smaller than asked (e.g. an old bake): upsample nearest
        f = size // full
        hp = np.repeat(np.repeat(hp, f, 0), f, 1)
        mask = np.repeat(np.repeat(mask, f, 0), f, 1)
    return normals_from_height(hp, mask, settings.nor_strength, settings.nor_invert)
