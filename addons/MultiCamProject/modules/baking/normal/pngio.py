"""16-bit RGB PNG writer (numpy + zlib). Blender's own image save goes through color
management for float images; a normal map must be written as raw values."""
import struct
import zlib

import numpy as np

STRIPE = 256        # rows compressed at once - keeps the memory flat at 8K


def _chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_rgb16(path, rgb, level=6):
    """`rgb`: float array (h, w, 3) in 0..1, rows bottom-up (Blender order)."""
    h, w, _ = rgb.shape
    comp = zlib.compressobj(level)
    parts = []
    prev = np.zeros(w * 6, dtype=np.uint8)
    for top in range(0, h, STRIPE):
        # PNG rows go top-down: stripe from the top of the image, flipped
        lo, hi = max(0, h - top - STRIPE), h - top
        block = rgb[lo:hi][::-1]
        v = np.clip(np.rint(block * 65535.0), 0, 65535).astype(">u2")
        rows = v.reshape(len(block), w * 3).view(np.uint8).reshape(len(block), w * 6)
        up = np.empty_like(rows)             # PNG filter 2 (Up): smooth maps compress well
        up[0] = rows[0] - prev
        up[1:] = rows[1:] - rows[:-1]
        prev = rows[-1].copy()
        out = np.empty((len(block), w * 6 + 1), dtype=np.uint8)
        out[:, 0] = 2
        out[:, 1:] = up
        parts.append(comp.compress(out.tobytes()))
    parts.append(comp.flush())
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0)))
        f.write(_chunk(b"IDAT", b"".join(parts)))
        f.write(_chunk(b"IEND", b""))


def _deflate_part(data, level, last):
    """One stripe as raw deflate, ending on a byte boundary (sync flush) unless it is the
    last - the parts join into one stream (pigz)."""
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)


def write_rgb8(path, px, level=1, threads=None):
    """8-bit RGB PNG of `px` (h, w, >=3) - floats in 0..1 (as stored: sRGB values) or uint8 -
    rows bottom-up (Blender order). Stripes are filtered and compressed on all cores at once
    (zlib leaves the GIL): an 8K texture in a fraction of Blender's own PNG save."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    h, w = px.shape[:2]
    threads = threads or max(1, min(16, os.cpu_count() or 4))

    def stripe(top):
        lo, hi = max(0, h - top - STRIPE), h - top
        block = px[lo:hi, :, :3][::-1]
        rows = (block if block.dtype == np.uint8 else
                np.clip(np.rint(block * 255.0), 0, 255).astype(np.uint8)).reshape(len(block), w * 3)
        prev = (px[hi:hi + 1, :, :3][::-1] if hi < h else None)    # the row above the stripe
        if prev is not None and prev.dtype != np.uint8:
            prev = np.clip(np.rint(prev * 255.0), 0, 255).astype(np.uint8)
        prev = prev.reshape(w * 3) if prev is not None else np.zeros(w * 3, np.uint8)
        out = np.empty((len(block), w * 3 + 1), dtype=np.uint8)
        out[:, 0] = 2                                   # filter Up
        out[0, 1:] = rows[0] - prev
        out[1:, 1:] = rows[1:] - rows[:-1]
        return out.tobytes()

    tops = list(range(0, h, STRIPE))
    with ThreadPoolExecutor(threads) as pool:
        raws = list(pool.map(stripe, tops))
        parts = list(pool.map(lambda i: _deflate_part(raws[i], level, i == len(raws) - 1),
                              range(len(raws))))
    adler = 1
    for r in raws:
        adler = zlib.adler32(r, adler)
    data = b"\x78\x01" + b"".join(parts) + struct.pack(">I", adler & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(_chunk(b"IDAT", data))
        f.write(_chunk(b"IEND", b""))
