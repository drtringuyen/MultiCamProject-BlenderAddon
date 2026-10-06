"""Width / height of an image without decoding it. img.size makes Blender load the whole
picture - an 8K photo is ~0.3 s and hundreds of MB, far too much for 490 cameras - so the
size is read from the file header (PNG IHDR, JPEG SOF), from the packed bytes for a packed
image. Cached per file (path, size, date).

The photos sit on a network drive where every stat costs ~1 ms: a file's existence, size and
date come from one listing of its folder (scandir carries them), kept DIR_TTL seconds."""
import io
import os
import struct
import time

import bpy

_cache = {}         # key -> (w, h) or None
_dirs = {}          # normcase folder -> (time read, {normcase name: (size, mtime)})
DIR_TTL = 2.0


def forget():
    """Read the folders again on the next call (a Reload All starts with this)."""
    _dirs.clear()


def entry(path):
    """(size, mtime) of the file at `path`, or None when it is not there - from its
    folder's listing."""
    # the key is case-folded, the folder is read as written: Google Drive's
    # shortcut-targets-by-id paths are case-sensitive
    real, name = os.path.split(os.path.abspath(path))
    folder, name = os.path.normcase(real), os.path.normcase(name)
    now = time.monotonic()
    hit = _dirs.get(folder)
    if hit is None or now - hit[0] > DIR_TTL:
        files = {}
        try:
            with os.scandir(real) as it:
                for e in it:
                    if e.is_file():
                        st = e.stat()       # Windows: from the listing, no extra call
                        files[os.path.normcase(e.name)] = (st.st_size, st.st_mtime)
        except OSError:
            pass
        hit = _dirs[folder] = (now, files)
    return hit[1].get(name)
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _png(head):
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    return None


def _jpeg(f):
    """Walk the JPEG segments up to the first SOF (APP / EXIF blocks are skipped)."""
    if f.read(2) != b"\xff\xd8":
        return None
    while True:
        b = f.read(1)
        while b and b != b"\xff":
            b = f.read(1)
        while b == b"\xff":             # fill bytes
            b = f.read(1)
        if not b:
            return None
        m = b[0]
        if m == 0x01 or 0xD0 <= m <= 0xD8:      # markers without a length
            continue
        if m == 0xD9:                   # end of image
            return None
        raw = f.read(2)
        if len(raw) < 2:
            return None
        length = struct.unpack(">H", raw)[0]
        if m in _SOF:
            seg = f.read(5)
            if len(seg) < 5:
                return None
            h, w = struct.unpack(">xHH", seg)
            return (w, h) if w and h else None
        f.seek(length - 2, 1)


def read(f):
    """(w, h) from an open binary file, or None when it is neither PNG nor JPEG."""
    head = f.read(24)
    dims = _png(head)
    if dims is not None:
        return dims
    f.seek(0)
    try:
        return _jpeg(f)
    except (OSError, struct.error):
        return None


def _header(img):
    if img.packed_file is not None:
        key = ("packed", img.name, img.packed_file.size)
        if key not in _cache:
            _cache[key] = read(io.BytesIO(img.packed_file.data))
        return _cache[key]
    if img.source != 'FILE' or not img.filepath:
        return None
    path = bpy.path.abspath(img.filepath, library=img.library)
    st = entry(path)
    if st is None:
        return None
    key = (os.path.normcase(path), st[0], st[1])
    if key not in _cache:
        try:
            with open(path, "rb") as f:
                _cache[key] = read(f)
        except OSError:
            _cache[key] = None
    return _cache[key]


def size(img, load=False):
    """(w, h) of `img` or None. An image already in memory answers directly; otherwise
    the file header. `load`: decode as a last resort (formats without a known header,
    e.g. EXR / TIFF)."""
    if img is None:
        return None
    if img.has_data:
        w, h = img.size
        return (w, h) if w and h else None
    dims = _header(img)
    if dims is None and load:
        w, h = img.size
        dims = (w, h) if w and h else None
    return dims
