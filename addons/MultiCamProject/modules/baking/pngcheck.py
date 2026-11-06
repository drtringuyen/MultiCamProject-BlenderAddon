"""Is a PNG file nothing but black? Called from panel redraws, so it has to stay cheap: only a
file far smaller than its pixels is opened at all, and reading stops at the first colored
pixel. No Blender needed.

PNG filters only subtract neighboring bytes of the same channel, so an image is all black
exactly when its filtered color bytes are all zero: nothing has to be unfiltered."""
import os
import struct
import zlib

import numpy as np

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: (1, 1), 2: (3, 3), 4: (2, 1), 6: (4, 3)}    # color type: (channels, color ones)
MIN_RATIO = 50          # a real bake packs ~8:1, a black one 228:1; flat colors stop at once
_READ = 1 << 20         # compressed bytes at a time
_ROWS = 256             # rows checked at a time


def _chunks(f):
    """The compressed pixel data of the open PNG `f`, right after its IHDR chunk."""
    while True:
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        if kind == b"IEND":
            return
        if kind != b"IDAT":
            f.seek(size + 4, os.SEEK_CUR)
            continue
        left = size
        while left:
            data = f.read(min(left, _READ))
            if not data:
                return
            left -= len(data)
            yield data
        f.seek(4, os.SEEK_CUR)


def _colored(rows, stride, pixel, color):
    """Any color byte set in whole `rows` (bytes): each row is a filter byte, then pixels."""
    a = np.frombuffer(rows, dtype=np.uint8).reshape(-1, stride)[:, 1:]
    if pixel == color:
        return bool(a.any())
    return bool(a.reshape(a.shape[0], -1, pixel)[:, :, :color].any())


def is_black(path):
    """True when every pixel of the PNG at `path` is black. False for anything else, a file
    that can't be read or a format this doesn't check (palette, interlaced, under 8 bit)."""
    try:
        with open(path, "rb") as f:
            head = f.read(33)
            if len(head) < 33 or head[:8] != _SIGNATURE or head[12:16] != b"IHDR":
                return False
            w, h, depth, ctype, _comp, _flt, interlace = struct.unpack(">IIBBBBB", head[16:29])
            if ctype not in _CHANNELS or depth not in (8, 16) or interlace or not w or not h:
                return False
            channels, color = _CHANNELS[ctype]
            size = depth // 8
            pixel, color = channels * size, color * size
            stride = w * pixel + 1
            if os.fstat(f.fileno()).st_size * MIN_RATIO > stride * h:
                return False

            z = zlib.decompressobj()
            pending = b""
            total = 0
            for data in _chunks(f):
                while data:
                    pending += z.decompress(data, stride * _ROWS)
                    data = z.unconsumed_tail
                    whole = len(pending) - len(pending) % stride
                    if whole:
                        if _colored(pending[:whole], stride, pixel, color):
                            return False
                        total += whole
                        pending = pending[whole:]
            pending += z.flush()
            whole = len(pending) - len(pending) % stride
            if whole and _colored(pending[:whole], stride, pixel, color):
                return False
            return whole == len(pending) and total + whole == stride * h
    except (OSError, zlib.error, ValueError, struct.error):
        return False
