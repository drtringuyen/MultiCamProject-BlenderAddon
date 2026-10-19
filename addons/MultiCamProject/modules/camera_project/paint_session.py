"""The camera brush in use: which object, camera and tool (Paint / Erase) the last camera
paint button set up. Its button stays depressed while that brush is really what the
viewport paints with. The 3D View's Overlays (and with them the camera photo) are left
as they are: the camera row's toggle switches them.

It stays on while: the object is active and in Texture Paint (Vertex Paint) on the canvas
(color layer) the button chose, with the button's blend (Erase: Erase / Add Alpha) and,
for Paint, the camera's color. Anything else - another mode, object, canvas, brush or
color - ends it: paint_sync's timer checks every 0.1-0.25 s.
"""
import bpy
from bpy.app.handlers import persistent

_state = {}     # obj, cam, mode ('PAINT' / 'ERASE'), target (canvas / layer name), color


def _brush_color(ps):
    ups = ps.unified_paint_settings
    if ups.use_unified_color:
        return tuple(ups.color)
    return tuple(ps.brush.color) if ps.brush else None


def _valid():
    s = _state
    if not s:
        return False
    obj = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if obj is None or obj.name != s["obj"]:
        return False
    ts = bpy.context.scene.tool_settings
    if s["texture"]:
        if obj.mode != 'TEXTURE_PAINT':
            return False
        ps = ts.image_paint
        if ps.canvas is None or ps.canvas.name != s["target"]:
            return False
    else:
        if obj.mode != 'VERTEX_PAINT':
            return False
        ps = ts.vertex_paint
        ca = obj.data.color_attributes.active_color
        if ca is None or ca.name != s["target"]:
            return False
    br = ps.brush
    if br is None:
        return False
    # texture Erase: Add / Subtract on MSK_ R; vertex Erase: Erase / Add Alpha
    erasing = br.blend in ({'ADD', 'SUB'} if s["texture"] else {'ERASE_ALPHA', 'ADD_ALPHA'})
    if erasing != (s["mode"] == 'ERASE'):
        return False
    if s["mode"] == 'PAINT':
        col = _brush_color(ps)
        if col is None or max(abs(a - b) for a, b in zip(col, s["color"])) > 1e-3:
            return False
    return True


def color():
    return _state.get("color")


def current(obj):
    """(camera name, 'PAINT' / 'ERASE') of the brush `obj` is painted with, or None."""
    if not _state or obj is None or obj.name != _state["obj"] or not _valid():
        return None
    return _state["cam"], _state["mode"]


def begin(context, obj, cam, mode, texture, target, color):
    """The button just set the brush up: remember it."""
    _state.clear()
    _state.update(obj=obj.name, cam=cam.name, mode=mode, texture=texture, target=target,
                  color=tuple(color or (0.0, 0.0, 0.0)))


def end():
    """The brush is no longer the button's: its button pops up."""
    if not _state:
        return
    _state.clear()
    _redraw_sidebars()


def tick():
    """paint_sync's timer: the brush changed away from the button's -> end."""
    if _state and not _valid():
        end()


def _redraw_sidebars():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


@persistent
def _on_load(*_):
    _state.clear()


def register():
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    end()
