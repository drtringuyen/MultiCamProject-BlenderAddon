"""Adaptive camera paint (4+ slots), switched by the camera painted with:
- cameras 4-6 (VCMix2): on - the stroke clears VCMix (cameras 1-3) under it, so they show;
- cameras 1-3 (VCMix): off - they are on top anyway; VCMix2 underneath is kept.

Blender paints one color attribute per stroke, so this runs after each stroke: a timer
waits until no paint stroke is running, compares the painted layer with its snapshot and
scales the other layer's R, G, B down by the amount claimed. Alpha is never cleared (VCMix
alpha is the scan mask of all cameras - Erase works there); a camera 4-6 stroke raises it,
as a camera 1-3 stroke does through the brush. Each clear is its own undo step (Ctrl+Z
once: the clear, twice: the stroke).

How much a camera 4-6 stroke claims: its largest R/G/B increase or its alpha increase.
VCMix2 alpha is otherwise unused and kept at 0 between strokes, and the brush raises it
(Affect Alpha) wherever it passes - so a stroke claims even where VCMix2 already had that
color. A black brush claims nothing.
Only faces turned toward the painted camera are claimed: a brush that reaches through the
mesh (or around its silhouette) must not hand the far side to a camera that cannot see it.
"""
import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import core, gn_builder

L1, L2 = gn_builder.LAYERS
STROKE_OPS = {"PAINT_OT_vertex_paint"}
CLAIM_MIN = 1e-4

_snap = {}          # mesh pointer -> {layer: color array}
_dirty = set()      # mesh pointers changed since the last sync


def _key(me):
    return str(me.as_pointer())


def _active_object():
    vl = getattr(bpy.context, "view_layer", None)
    return vl.objects.active if vl else None


def _qualifies(obj):
    """Vertex painting a set-up object with both layers (4+ slots)."""
    if obj is None or obj.type != 'MESH' or obj.mode != 'VERTEX_PAINT':
        return False
    d = core.data(obj)
    ca = obj.data.color_attributes
    return (d.is_setup and core.slot_count(d) > 3
            and ca.get(L1) is not None and ca.get(L2) is not None)


def _read(attr):
    buf = np.empty(len(attr.data) * 4, dtype=np.float32)
    attr.data.foreach_get("color", buf)
    return buf.reshape(-1, 4)


def _write(attr, arr):
    attr.data.foreach_set("color", arr.ravel())


def reset(obj):
    """Snapshot both layers: the next stroke is measured from here."""
    ca = obj.data.color_attributes
    _snap[_key(obj.data)] = {n: _read(ca[n]) for n in (L1, L2) if ca.get(n) is not None}
    _dirty.discard(_key(obj.data))


def clear_marker(obj):
    """VCMix2 alpha to 0, so the brush's alpha shows where the next stroke goes."""
    attr = obj.data.color_attributes.get(L2)
    if attr is not None:
        arr = _read(attr)
        arr[:, 3] = 0.0
        _write(attr, arr)


def _undo_push(message):
    """Own undo step - also from the timer, which has no window in its context."""
    wins = bpy.context.window_manager.windows
    if not wins:
        return
    with bpy.context.temp_override(window=wins[0]):
        if bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message=message)


def _brush_color():
    vp = bpy.context.scene.tool_settings.vertex_paint
    ups = vp.unified_paint_settings
    return tuple(ups.color if ups.use_unified_color else (vp.brush.color if vp.brush else (1, 1, 1)))


def _brush_is_black():
    return max(_brush_color()) < 1e-4


def _painted_camera(obj, layer):
    """The slot camera whose color the brush holds on `layer` (None if unclear)."""
    color = _brush_color()
    if max(color) < 1e-4:
        return None
    slot = gn_builder.LAYERS.index(layer) * 3 + int(np.argmax(color)) + 1
    slots = core.get_slots(core.data(obj))
    return slots[slot - 1] if slot <= len(slots) else None


def _facing(obj, cam):
    """Per corner: does the face point toward `cam`? (ortho: its view axis, perspective:
    its location - the same test the projection's weights use)."""
    me = obj.data
    n = len(me.loops)
    nr = np.empty(n * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", nr)
    mw = np.array(obj.matrix_world, dtype=np.float32)
    nmat = np.array(obj.matrix_world.to_3x3().inverted_safe().transposed(), dtype=np.float32)
    nr = nr.reshape(n, 3) @ nmat.T
    if cam.data.type == 'ORTHO':
        to_cam = np.array(cam.matrix_world.to_3x3().col[2], dtype=np.float32)
        return nr @ to_cam > 0.0
    vi = np.empty(n, dtype=np.int32)
    me.loops.foreach_get("vertex_index", vi)
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)[vi] @ mw[:3, :3].T + mw[:3, 3]
    to_cam = np.array(cam.matrix_world.translation, dtype=np.float32) - co
    return (nr * to_cam).sum(axis=1) > 0.0


def sync(obj, push_undo=True):
    """Clear VCMix where the last stroke(s) painted cameras 4-6 into VCMix2."""
    if not _qualifies(obj):
        return False
    me = obj.data
    ca = me.color_attributes
    snap = _snap.get(_key(me))
    active = ca.active_color.name if ca.active_color else None
    if snap is None or active not in (L1, L2):
        reset(obj)
        return False
    if active == L1:                    # cameras 1-3: adaptive off
        reset(obj)
        return False
    other = L1
    cur, old = _read(ca[active]), snap.get(active)
    if old is None or old.shape != cur.shape:
        reset(obj)
        return False

    claim = np.clip((cur[:, :3] - old[:, :3]).max(axis=1), 0.0, 1.0)
    claim = (np.zeros(len(cur), dtype=np.float32) if _brush_is_black()
             else np.maximum(claim, np.clip(cur[:, 3] - old[:, 3], 0.0, 1.0)))
    changed = False
    if claim.max(initial=0.0) > CLAIM_MIN:
        cam = _painted_camera(obj, active)
        if cam is not None and len(claim) == len(me.loops):
            claim = claim * _facing(obj, cam)       # faces the camera cannot see stay as they were
    if claim.max(initial=0.0) > CLAIM_MIN:
        oth = _read(ca[other])
        if len(oth) == len(claim):      # both layers on the same domain
            oth[:, :3] *= (1.0 - claim)[:, None]
            oth[:, 3] = np.maximum(oth[:, 3], claim)    # an erased area shows the new camera again
            _write(ca[other], oth)
            changed = True
    if cur[:, 3].max(initial=0.0) > 0.0:
        cur[:, 3] = 0.0                 # marker back to 0 for the next stroke
        _write(ca[active], cur)
    reset(obj)
    if changed:
        obj.update_tag()
        if push_undo:
            _undo_push("Camera Paint: clear other layer")
    return changed


# ---------------------------------------------------------------- after each stroke

def _stroke_running():
    for win in bpy.context.window_manager.windows:
        if any(op.bl_idname in STROKE_OPS for op in win.modal_operators):
            return True
    return False


def _tick():
    try:
        obj = _active_object()
        if not _qualifies(obj):
            # strokes made meanwhile (fewer than 4 cameras, other mode) are never claimed later
            _snap.clear()
            _dirty.clear()
            return 0.25
        if _stroke_running():
            return 0.05
        key = _key(obj.data)
        if key not in _snap:            # started watching (entered paint mode, after an undo)
            ca = obj.data.color_attributes
            if ca.active_color and ca.active_color.name == L2:
                clear_marker(obj)
            reset(obj)
        elif key in _dirty:
            _dirty.discard(key)
            sync(obj)
        return 0.1
    except Exception as e:      # never let the timer die
        print(f"[MultiCamProject] paint sync: {e}")
        return 0.5


@persistent
def _on_depsgraph(scene, depsgraph):
    obj = _active_object()
    if obj is None or obj.type != 'MESH' or obj.mode != 'VERTEX_PAINT':
        return
    me = obj.data
    for upd in depsgraph.updates:
        if getattr(upd.id, "original", None) in (me, obj):
            _dirty.add(_key(me))
            return


@persistent
def _on_undo(*_):
    # an undone/redone stroke or clear is the new baseline - never replay it
    _snap.clear()
    _dirty.clear()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.undo_post.append(_on_undo)
    bpy.app.handlers.redo_post.append(_on_undo)
    bpy.app.handlers.load_post.append(_on_undo)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                         (bpy.app.handlers.undo_post, _on_undo),
                         (bpy.app.handlers.redo_post, _on_undo),
                         (bpy.app.handlers.load_post, _on_undo)):
        if fn in handlers:
            handlers.remove(fn)
    _snap.clear()
    _dirty.clear()
