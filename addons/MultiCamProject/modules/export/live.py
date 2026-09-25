"""Names follow the Name Prefix live: renaming an EXPORT object (F2, Outliner, the status
list) or changing the prefix puts '<scene>.<##>_' back in front and brings MCP_/MAT_/ALB_/
NOR_ and the texture files along. Runs from a timer, never inside Blender's rename itself."""
import bpy
from bpy.app.handlers import persistent

_owner = object()
_DELAY = 0.2


def _fix():
    try:
        from ..baking import cache, common, naming
        from . import fixes
        scene = bpy.context.scene
        sc = naming.scheme(scene)
        if sc is None or bpy.context.mode not in {'OBJECT', 'EDIT_MESH', 'PAINT_VERTEX'}:
            return None
        objs = common.export_objects(scene)
        if not objs:
            return None
        log = fixes.rename_all(objs, sc)
        if log:
            cache.clear()
            for area in bpy.context.screen.areas if bpy.context.screen else []:
                area.tag_redraw()
        for sev, text in log:
            print(f"[MultiCamProject] {text}")
    except Exception as e:      # a rename must never break Blender's own rename
        print(f"[MultiCamProject] live rename skipped: {e}")
    return None


def schedule():
    if bpy.app.timers.is_registered(_fix):
        bpy.app.timers.unregister(_fix)
    bpy.app.timers.register(_fix, first_interval=_DELAY)


def _subscribe():
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "name"), owner=_owner, args=(),
                             notify=schedule, options={'PERSISTENT'})


@persistent
def _on_load(_):
    _subscribe()       # a file load drops every subscription


def register():
    _subscribe()
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    bpy.msgbus.clear_by_owner(_owner)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_fix):
        bpy.app.timers.unregister(_fix)
