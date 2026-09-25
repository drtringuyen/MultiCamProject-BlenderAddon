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
