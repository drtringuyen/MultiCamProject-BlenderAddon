"""Adaptive camera paint (4+ slots), switched by the camera painted with:
- cameras 4-6 (VCMix2): on - the stroke clears VCMix (cameras 1-3) under it, so they show;
- cameras 1-3 (VCMix): off - they are on top anyway; VCMix2 underneath is kept.

Blender paints one color attribute per stroke, so this runs after each stroke: a timer
waits until no paint stroke is running, compares the painted layer with its snapshot and
scales the other layer's R, G, B down by the amount claimed. Each clear is its own undo
step (Ctrl+Z once: the clear, twice: the stroke).

Blend mask = VCMix alpha x VCMix2 alpha (0 = baked, 1 = projected), so both alphas move
together: a camera 4-6 stroke raises VCMix alpha where it claims, and a VCMix stroke
(cameras 1-3, Erase, Shift+Erase) moves VCMix2 alpha by the same amount.

How much a camera 4-6 stroke claims: its largest R/G/B increase or its alpha increase.
During a camera 4-6 session VCMix2 alpha is the marker, kept at 0 between strokes, and
the brush raises it (Affect Alpha) wherever it passes - so a stroke claims even where
VCMix2 already had that color. Its real alpha waits in _mcp_mask2 meanwhile (the GN shows
the larger of the two, so nothing flickers), and each stroke's marker is folded into it.
The session ends when another layer is painted or Vertex Paint is left: _mcp_mask2 goes
back into VCMix2 alpha. A black brush claims nothing.
Only faces turned toward the painted camera are claimed: a brush that reaches through the
mesh (or around its silhouette) must not hand the far side to a camera that cannot see it.
"""
import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import core, gn_builder

L1, L2 = gn_builder.LAYERS
STASH = gn_builder.MASK2_STASH
STROKE_OPS = {"PAINT_OT_vertex_paint"}
CLAIM_MIN = 1e-4

_snap = {}          # mesh pointer -> {layer: color array}
_dirty = set()      # mesh pointers changed since the last sync
_sessions = set()   # names of objects with VCMix2 alpha in _mcp_mask2 (camera 4-6 session)


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


def _stash(me, create=False):
    """The _mcp_mask2 float attribute on VCMix2's domain (None when missing)."""
    l2 = me.color_attributes.get(L2)
    a = me.attributes.get(STASH)
    if a is not None and (l2 is None or a.domain != l2.domain or a.data_type != 'FLOAT'):
        me.attributes.remove(a)
        a = None
    if a is None and create and l2 is not None:
        a = me.attributes.new(STASH, 'FLOAT', l2.domain)
        a.data.foreach_set("value", np.zeros(len(a.data), dtype=np.float32))
    return a


def _read_stash(a):
    buf = np.empty(len(a.data), dtype=np.float32)
    a.data.foreach_get("value", buf)
    return buf


def begin_session(obj):
    """Camera 4-6 painting starts: VCMix2's real alpha into _mcp_mask2 (merged with one
    left from before), VCMix2 alpha to 0 so the brush's alpha shows where a stroke goes."""
    me = obj.data
    attr = me.color_attributes.get(L2)
    if attr is None:
        return
    arr = _read(attr)
    fresh = me.attributes.get(STASH) is None
    st = _stash(me, create=True)
    real = arr[:, 3].copy() if fresh else np.maximum(_read_stash(st), arr[:, 3])
    st.data.foreach_set("value", real)
    arr[:, 3] = 0.0
    _write(attr, arr)
    _sessions.add(obj.name)


def end_session(obj):
    """VCMix2 alpha back from _mcp_mask2 (the larger of it and a marker not folded yet).
    Returns True when there was a stash to put back."""
    _sessions.discard(obj.name)
    if obj.type != 'MESH' or obj.mode == 'EDIT':
        return False
    me = obj.data
    st = _stash(me)
    if st is None:
        return False
    attr = me.color_attributes.get(L2)
    if attr is not None:
        arr = _read(attr)
        arr[:, 3] = np.maximum(arr[:, 3], _read_stash(st))
        _write(attr, arr)
    me.attributes.remove(me.attributes[STASH])
    obj.update_tag()
    return True


def _mirror_alpha(obj, cur, old):
    """A VCMix stroke moved VCMix alpha: VCMix2 alpha (or its stash) moves by the same."""
    d = cur[:, 3] - old[:, 3]
    if np.abs(d).max(initial=0.0) <= CLAIM_MIN:
        return False
    me = obj.data
    st = _stash(me)
    if st is not None and len(st.data) == len(d):
        st.data.foreach_set("value", np.clip(_read_stash(st) + d, 0.0, 1.0))
        return True
    attr = me.color_attributes.get(L2)
    if attr is None:
        return False
    arr = _read(attr)
    if len(arr) != len(d):
        return False
    arr[:, 3] = np.clip(arr[:, 3] + d, 0.0, 1.0)
    _write(attr, arr)
    return True


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
    cur, old = _read(ca[active]), snap.get(active)
    if old is None or old.shape != cur.shape:
        reset(obj)
        return False
    if active == L1:                    # cameras 1-3 / Erase: no claim, the alphas move together
        changed = _mirror_alpha(obj, cur, old)
        reset(obj)
        if changed:
            obj.update_tag()
            if push_undo:
                _undo_push("Camera Paint: VCMix2 alpha")
        return changed
    other = L1

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
        st = _stash(me, create=True)    # the stroke's alpha is real mask: into the stash
        if len(st.data) == len(cur):
            st.data.foreach_set("value", np.maximum(_read_stash(st), cur[:, 3]))
            changed = True
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
            for name in list(_sessions):        # Vertex Paint left: VCMix2 alpha back
                o = bpy.data.objects.get(name)
                if o is None or o.mode != 'VERTEX_PAINT':
                    if o is None or not end_session(o):
                        _sessions.discard(name)
            return 0.25
        if _stroke_running():
            return 0.05
        key = _key(obj.data)
        if key not in _snap:            # started watching (entered paint mode, after an undo)
            ca = obj.data.color_attributes
            if ca.active_color and ca.active_color.name == L2:
                begin_session(obj)
            elif obj.name in _sessions:
                end_session(obj)
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
    _sessions.clear()       # the stash is undone with the mesh; begin_session merges a leftover


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
