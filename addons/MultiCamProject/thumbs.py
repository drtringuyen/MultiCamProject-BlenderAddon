"""Small thumbnails for the images the panels show (camera photos, ALB_ / BAo_ / NOR_ ...).

An image with a file on disk gets its thumbnail the way the File Browser does: read from
the file (a JPEG decoded at a fraction of its size), kept in the OS thumbnail cache, never
the image itself loaded - an image's own preview would load the 45 MP photo into memory.
A packed / generated image uses its own preview, only once its pixels are in memory.

The file's preview is only read here: a preview left to make its icon by itself often
stayed blank (see addon_icons), so each image gets a second preview with pixels set from
it - the icon the whole picture (fitted), the big image a centred square that fills a
template_icon to its edges.

    icon(img)               the icon id, 0 when there is none (yet)
    kw(img, 'IMAGE_DATA')   {'icon_value': id} or {'icon': fallback} for prop() / label()"""
import os
import time

import bpy

_files = None       # the files' thumbnails, as Blender reads them
_made = None        # the drawn ones: pixels from _files' (fitted icon, square image)
_stat = {}          # path -> (checked at, mtime): a changed file (a saved _inpaint) gets a new one
RECHECK = 2.0       # seconds between looks at one file's time
ICON = 32


def _collections():
    global _files, _made
    if _files is None:
        import bpy.utils.previews
        _files, _made = bpy.utils.previews.new(), bpy.utils.previews.new()
    return _files, _made


def _mtime(path):
    now = time.monotonic()
    hit = _stat.get(path)
    if hit is not None and now - hit[0] < RECHECK:
        return hit[1]
    try:
        m = os.path.getmtime(path)
    except OSError:
        m = None
    _stat[path] = (now, m)
    return m


def _resample(px, w, h, ow, oh):
    """Nearest-sample an (h, w, 4) array to (oh, ow, 4)."""
    import numpy as np
    ys = np.minimum((np.arange(oh) + 0.5) * h / oh, h - 1).astype(int)
    xs = np.minimum((np.arange(ow) + 0.5) * w / ow, w - 1).astype(int)
    return px[ys][:, xs]


def _make(key, src):
    """The drawn preview for key from the file's preview src, None until src has pixels."""
    import numpy as np
    w, h = src.image_size          # reading it loads the file's thumbnail
    if not w or not h:
        return None
    px = np.array(src.image_pixels_float[:], dtype=np.float32).reshape(h, w, 4)
    # icon: the whole picture fitted into the square, transparent around it
    s = ICON / max(w, h)
    fw, fh = max(1, round(w * s)), max(1, round(h * s))
    icon = np.zeros((ICON, ICON, 4), np.float32)
    y0, x0 = (ICON - fh) // 2, (ICON - fw) // 2
    icon[y0:y0 + fh, x0:x0 + fw] = _resample(px, w, h, fw, fh)
    # image: the centred square, so a big template_icon is filled to its edges
    n = min(w, h)
    sq = px[(h - n) // 2:(h - n) // 2 + n, (w - n) // 2:(w - n) // 2 + n]
    p = _collections()[1].new(key)
    p.icon_size = (ICON, ICON)
    p.icon_pixels_float = icon.ravel()
    p.image_size = (n, n)
    p.image_pixels_float = sq.ravel()
    return p


def icon(img):
    """The image's thumbnail icon id, 0 when it has none (no file, not loaded)."""
    if img is None:
        return 0
    try:
        if img.source == 'FILE' and img.filepath and not img.packed_file:
            path = os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))
            m = _mtime(path)
            if m is None:
                return 0
            key = f"{os.path.normcase(path)}|{m}"
            files, made = _collections()
            p = made.get(key)
            if p is None:
                src = files.get(key) or files.load(key, path, 'IMAGE')
                p = _make(key, src)
                if p is None:
                    return 0
            return p.icon_id
        if img.preview is not None:
            return img.preview.icon_id
        if img.has_data:            # in memory already: its preview costs no load
            return img.preview_ensure().icon_id
    except (ReferenceError, RuntimeError, KeyError, ValueError, AttributeError):
        pass
    return 0


def kw(img, fallback='IMAGE_DATA'):
    """prop() / label() icon arguments: the thumbnail, else the fallback icon."""
    i = icon(img)
    return {"icon_value": i} if i else {"icon": fallback}


def register():
    pass


def unregister():
    global _files, _made
    if _files is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_files)
        bpy.utils.previews.remove(_made)
        _files = _made = None
    _stat.clear()
