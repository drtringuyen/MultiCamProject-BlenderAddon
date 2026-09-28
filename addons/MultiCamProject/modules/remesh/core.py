"""Poly Cut: the mesh is split exactly along the drawn polygon and the inside gets a new
Sculpt face set. face_set (a copy of .sculpt_face_set, which GN cannot read) is refreshed
after every cut and whenever the object leaves Sculpt Mode."""
import bpy
import numpy as np
from bpy.app.handlers import persistent

from ..camera_project import core as cp
from . import cutter, gn_remesh as gn

SCULPT_FACE_SET = ".sculpt_face_set"
_TEMP_MOD = "_MCP_PolyCut"


def _face_sets(me):
    a = me.attributes.get(SCULPT_FACE_SET)
    if a is None:
        return np.ones(len(me.polygons), np.int32)
    v = np.empty(len(me.polygons), np.int32)
    a.data.foreach_get("value", v)
    return v


_ids = {}      # mesh name -> [(face set, face count)], refreshed by sync_face_sets


def face_set_ids(obj):
    """The face sets of the last sync (cheap: the panel calls it on every redraw)."""
    return _ids.get(obj.data.name, [])


def sync_face_sets(obj):
    """Copy the Sculpt face sets into face_set for GN (only writes when they differ)."""
    me = obj.data
    fs = _face_sets(me)
    ids, counts = np.unique(fs, return_counts=True)
    _ids[me.name] = list(zip(ids.tolist(), counts.tolist()))
    a = me.attributes.get(gn.FACE_SET)
    if a is not None and (a.domain != 'FACE' or a.data_type != 'INT'):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(gn.FACE_SET, 'INT', 'FACE')
    else:
        cur = np.empty(len(me.polygons), np.int32)
        a.data.foreach_get("value", cur)
        if np.array_equal(cur, fs):
            return
    a.data.foreach_set("value", fs)
    me.update()


def poly_cut(obj, rays, forward, near, far):
    """Object Mode only. Returns the new face set ID and its face count."""
    cut = cutter.build(rays, forward, near, far)
    mod = obj.modifiers.new(_TEMP_MOD, 'NODES')
    try:
        mod.node_group = gn.ensure_cut_group()
        cp.set_input(mod, "Cutter", cut)
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
            bpy.ops.object.modifier_apply(modifier=mod.name)
    finally:
        if obj.modifiers.get(_TEMP_MOD) is not None:
            obj.modifiers.remove(obj.modifiers[_TEMP_MOD])
        cutter.remove(cut)

    me = obj.data
    a = me.attributes.get(gn.INSIDE)
    if a is None:
        return None, 0
    inside = np.zeros(len(me.polygons), bool)
    a.data.foreach_get("value", inside)
    me.attributes.remove(me.attributes[gn.INSIDE])
    fs = _face_sets(me)
    new_id = int(fs.max()) + 1 if len(fs) else 1
    fs[inside] = new_id
    fsa = me.attributes.get(SCULPT_FACE_SET) or me.attributes.new(SCULPT_FACE_SET, 'INT', 'FACE')
    fsa.data.foreach_set("value", fs)
    me.update()
    sync_face_sets(obj)
    return new_id, int(inside.sum())


# ------------------------------------------------ keep face_set in sync after sculpting

_last_mode = {}


@persistent
def _on_depsgraph(scene, depsgraph):
    obj = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if obj is None or obj.type != 'MESH':
        return
    prev = _last_mode.get(obj.name)
    _last_mode[obj.name] = obj.mode
    if prev == 'SCULPT' and obj.mode != 'SCULPT' and gn.FACE_SET in obj.data.attributes:
        sync_face_sets(obj)
    elif obj.data.name not in _ids and gn.FACE_SET in obj.data.attributes:
        ids, counts = np.unique(_face_sets(obj.data), return_counts=True)   # e.g. after a reload
        _ids[obj.data.name] = list(zip(ids.tolist(), counts.tolist()))


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
