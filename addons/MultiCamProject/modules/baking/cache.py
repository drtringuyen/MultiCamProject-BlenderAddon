"""Per-object cache for values the panels draw on every redraw (fingerprints, checks).
An entry is dropped when its object or mesh changes; a change to anything else the
results may depend on (materials, cameras, images, node groups) drops everything."""
import bpy
from bpy.app.handlers import persistent

_cache = {}         # object name -> {key: value}
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
    else:
        _cache.pop(obj.name, None)


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
    _cache.clear()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
    _cache.clear()
