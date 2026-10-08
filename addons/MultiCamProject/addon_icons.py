"""The add-on's own icons: icons/<name>.png, loaded on first use into one preview collection.

They are Blender's toolbar icons (bucket, eraser, lasso, the Density brush) drawn as plain
square images with even margins: a toolbar icon itself sits off centre and clipped in a
normal button, while a preview icon is centred like any other.

The PNGs are decoded here and their pixels handed to the preview (image AND icon) at once:
a preview left to load its file by itself stayed blank in a freshly started Blender (the
work window), its icon never made."""
import os
import struct
import zlib

import bpy

_previews = None
FOLDER = os.path.join(os.path.dirname(__file__), "icons")
ICON = 32       # the icon's own size (a button draws it scaled)


def _read_png(path):
    """(width, height, RGBA floats bottom row first) of an 8-bit RGBA / RGB PNG, else None."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, inter = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or inter:
                return None
            ch = 4 if ctype == 6 else 3
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * ch
    rows, prev = [], bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    px = []
    for line in reversed(rows):         # Blender: bottom row first
        for x in range(w):
            r, g, b = line[x * ch:x * ch + 3]
            px += (r / 255, g / 255, b / 255, line[x * ch + 3] / 255 if ch == 4 else 1.0)
    return w, h, px


def _shrink(w, h, px, size):
    """Box-filter the RGBA floats down to size x size."""
    sx, sy = w / size, h / size
    out = []
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            n = 0
            for yy in range(int(y * sy), max(int(y * sy) + 1, int((y + 1) * sy))):
                for xx in range(int(x * sx), max(int(x * sx) + 1, int((x + 1) * sx))):
                    i = (yy * w + xx) * 4
                    for k in range(4):
                        acc[k] += px[i + k]
                    n += 1
            out += [v / n for v in acc]
    return out


def icon_id(name):
    """icon_value of icons/<name>.png (0 when the file is missing or unreadable)."""
    global _previews
    import bpy.utils.previews
    if _previews is None:
        _previews = bpy.utils.previews.new()
    if name not in _previews:
        path = os.path.join(FOLDER, name + ".png")
        png = _read_png(path) if os.path.isfile(path) else None
        if png is None:
            return 0
        w, h, px = png
        pv = _previews.new(name)
        pv.image_size = (w, h)
        pv.image_pixels_float = px
        pv.icon_size = (ICON, ICON)
        pv.icon_pixels_float = _shrink(w, h, px, ICON)
    return _previews[name].icon_id


def kw(name, fallback='QUESTION'):
    """Keyword for layout.operator / prop: the add-on icon, else a stock one."""
    value = icon_id(name)
    return {"icon_value": value} if value else {"icon": fallback}


def free():
    global _previews
    if _previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews)
        _previews = None
