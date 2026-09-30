"""Per-object cache for values the panels draw on every redraw: mesh checksums, UV stats,
triangle counts, the pivot, PNG headers. Only values that depend on the object's own
mesh, its transform or a file (keyed by its path and date) belong here - an entry is
dropped when that mesh is edited, the object moves or gets another mesh. Materials,
images, cameras and node groups change all the time (Material Preview, the node editor)
and never clear it: that used to rebuild everything, ~1 s a redraw with 20 objects."""
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


def get(obj, key, fn):
    data = obj.data.as_pointer() if obj.data is not None else 0
    entry = _cache.get(obj.name)
    if entry is None or entry.get("__data") != data:     # new, renamed onto, other mesh
        entry = _cache[obj.name] = {"__data": data}
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
            # the pivot uses the transform; a modifier re-run (geometry) changes nothing here
            if u.is_updated_transform:
                _cache.pop(idb.name, None)
        elif isinstance(idb, bpy.types.Mesh):       # edit mode, apply, data API
            for name in [n for n in _cache if (o := bpy.data.objects.get(n)) is None or o.data == idb]:
                _cache.pop(name, None)


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
