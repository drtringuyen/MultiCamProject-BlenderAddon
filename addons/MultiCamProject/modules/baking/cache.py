"""Per-object cache for values the panels draw on every redraw (fingerprints, checks).
An entry is dropped when its object or mesh changes; a change to anything else the
results may depend on (materials, cameras, images, node groups) drops everything."""
import os
import stat
import time

import bpy
from bpy.app.handlers import persistent

_cache = {}         # object name -> {key: value}
# files on a network drive (Google Drive: ~1 ms a stat) - panels redraw on every mouse
# move, so a file's state is read at most every FILE_TTL seconds; bakes and exports
# clear() it right after writing
FILE_TTL = 2.0
_files = {}         # path -> (read at, (is file, mtime))
_dirs = {}          # folder -> (read at, [names])
_SHARED = (bpy.types.Material, bpy.types.Image, bpy.types.Camera, bpy.types.NodeTree,
           bpy.types.Collection)


def get(obj, key, fn):
    entry = _cache.setdefault(obj.name, {})
    if key not in entry:
        entry[key] = fn(obj)
    return entry[key]


def clear(obj=None):
    if obj is None:
        _cache.clear()
        _files.clear()
        _dirs.clear()
    else:
        _cache.pop(obj.name, None)


def file_state(path):
    """(is a file, mtime) of `path`, read again after FILE_TTL seconds."""
    now = time.monotonic()
    hit = _files.get(path)
    if hit is not None and now - hit[0] < FILE_TTL:
        return hit[1]
    try:
        st = os.stat(path)
        val = (stat.S_ISREG(st.st_mode), st.st_mtime)
    except OSError:
        val = (False, 0.0)
    _files[path] = (now, val)
    return val


def isfile(path):
    return bool(path) and file_state(path)[0]


def listdir(folder):
    """The names in `folder` ([] when it cannot be read), read again after FILE_TTL."""
    now = time.monotonic()
    hit = _dirs.get(folder)
    if hit is not None and now - hit[0] < FILE_TTL:
        return hit[1]
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    _dirs[folder] = (now, names)
    return names


@persistent
def _on_depsgraph(scene, depsgraph):
    for u in depsgraph.updates:
        idb = u.id.original if u.id else None
        if isinstance(idb, bpy.types.Object):
            if u.is_updated_geometry or u.is_updated_transform:
                _cache.pop(idb.name, None)
                if idb.type == 'CAMERA' and u.is_updated_transform:
                    _cache.clear()
        elif isinstance(idb, bpy.types.Mesh):
            for name in [n for n in _cache if (o := bpy.data.objects.get(n)) is None or o.data == idb]:
                _cache.pop(name, None)
        elif isinstance(idb, _SHARED):
            _cache.clear()
            return


@persistent
def _on_load(_):
    clear()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
    _cache.clear()
