"""Small thumbnails for the images the panels show (camera photos, ALB_ / BAo_ / NOR_ ...).

An image with a file on disk gets its thumbnail the way the File Browser does: read from
the file (a JPEG decoded at a fraction of its size), kept in the OS thumbnail cache, never
the image itself loaded - an image's own preview would load the 45 MP photo into memory.
A packed / generated image uses its own preview, only once its pixels are in memory.

    icon(img)               the icon id, 0 when there is none (yet)
    kw(img, 'IMAGE_DATA')   {'icon_value': id} or {'icon': fallback} for prop() / label()"""
import os
import time

import bpy

_previews = None
_stat = {}          # path -> (checked at, mtime): a changed file (a saved _inpaint) gets a new one
RECHECK = 2.0       # seconds between looks at one file's time


def _collection():
    global _previews
    if _previews is None:
        import bpy.utils.previews
        _previews = bpy.utils.previews.new()
    return _previews


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
            pc = _collection()
            p = pc.get(key)
            if p is None:
                p = pc.load(key, path, 'IMAGE')
            return p.icon_id
        if img.preview is not None:
            return img.preview.icon_id
        if img.has_data:            # in memory already: its preview costs no load
            return img.preview_ensure().icon_id
    except (ReferenceError, RuntimeError, KeyError, ValueError):
        pass
    return 0


def kw(img, fallback='IMAGE_DATA'):
    """prop() / label() icon arguments: the thumbnail, else the fallback icon."""
    i = icon(img)
    return {"icon_value": i} if i else {"icon": fallback}


def register():
    pass


def unregister():
    global _previews
    if _previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews)
        _previews = None
    _stat.clear()
