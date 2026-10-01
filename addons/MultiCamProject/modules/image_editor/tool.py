"""Liquify tool in the Image Editor toolbar (View and UV modes) and its brush circle.

Keys while the tool is active: LMB paint (Alt: Pucker <-> Bloat), F drag the size, [ ] size,
W R S P B pick the brush, Ctrl+Z / Ctrl+Shift+Z undo / redo the strokes of the session.
"""
import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import field, props, session

TOOL_IDS = ("multicamproject.liquify", "multicamproject.liquify_uv")

_hover = {}             # area pointer -> (x, y) mouse in region pixels
_draw_handle = None
_CIRCLE = 64


def tool_id(space):
    return TOOL_IDS[1] if space.mode == 'UV' else TOOL_IDS[0]


def tool_active(context):
    sp = context.space_data
    if sp is None or sp.type != 'IMAGE_EDITOR' or context.workspace is None:
        return False
    try:
        t = context.workspace.tools.from_space_image_mode(sp.mode, create=False)
    except (TypeError, ValueError):
        return False
    return t is not None and t.idname in TOOL_IDS


def set_hover(area, x, y):
    _hover[area.as_pointer()] = (x, y)


def preview_dims(space):
    """(w, h) of the preview the brush paints in: the session's, or the one the shown
    image would get (for the circle before Start)."""
    s = session.active()
    if s is not None:
        return s.lq.w, s.lq.h
    img = space.image
    if img is None or not img.has_data:
        return None
    return field.preview_size(*img.size)


def screen_scale(region, dims):
    """Region pixels per preview pixel at the current zoom."""
    v2d = region.view2d
    x0, _ = v2d.view_to_region(0.0, 0.0, clip=False)
    x1, _ = v2d.view_to_region(1.0, 0.0, clip=False)
    return abs(x1 - x0) / max(1, dims[0])


def draw_circle(center, radius):
    pts = [(center[0] + radius * math.cos(a), center[1] + radius * math.sin(a))
           for a in (2 * math.pi * i / _CIRCLE for i in range(_CIRCLE + 1))]
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'LINE_STRIP', {"pos": pts})
    gpu.state.blend_set('ALPHA')
    sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    for width, color in ((3.0, (0.0, 0.0, 0.0, 0.6)), (1.2, (1.0, 1.0, 1.0, 0.9))):
        sh.uniform_float("lineWidth", width)
        sh.uniform_float("color", color)
        batch.draw(sh)
    gpu.state.blend_set('NONE')


def _draw():
    context = bpy.context
    area, region = context.area, context.region
    if area is None or region is None or not tool_active(context):
        return
    pos = _hover.get(area.as_pointer())
    dims = preview_dims(context.space_data)
    if pos is None or dims is None:
        return
    st = props.settings(context)
    draw_circle(pos, st.size * 0.5 * screen_scale(region, dims))


def _draw_settings(context, layout, tool):
    from . import ui
    ui.draw_brush(context, layout, header=True)


def _brush_key(key, brush):
    return ("wm.context_set_enum", {"type": key, "value": 'PRESS'},
            {"properties": [("data_path", "scene.multicamproject_liquify.brush"),
                            ("value", brush)]})


KEYMAP = (
    ("multicamproject.liquify_stroke", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
    ("multicamproject.liquify_stroke", {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True},
     {"properties": [("invert", True)]}),
    ("multicamproject.liquify_hover", {"type": 'MOUSEMOVE', "value": 'ANY', "any": True}, None),
    ("multicamproject.liquify_resize", {"type": 'F', "value": 'PRESS'}, None),
    ("multicamproject.liquify_size", {"type": 'LEFT_BRACKET', "value": 'PRESS', "repeat": True},
     {"properties": [("factor", 1 / 1.15)]}),
    ("multicamproject.liquify_size", {"type": 'RIGHT_BRACKET', "value": 'PRESS', "repeat": True},
     {"properties": [("factor", 1.15)]}),
    ("multicamproject.liquify_undo", {"type": 'Z', "value": 'PRESS', "ctrl": True, "repeat": True},
     None),
    ("multicamproject.liquify_redo",
     {"type": 'Z', "value": 'PRESS', "ctrl": True, "shift": True, "repeat": True}, None),
    _brush_key('W', 'WARP'),
    _brush_key('R', 'RECONSTRUCT'),
    _brush_key('S', 'SMOOTH'),
    _brush_key('P', 'PUCKER'),
    _brush_key('B', 'BLOAT'),
)


class MULTICAMPROJECT_TL_Liquify(bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'VIEW'
    bl_idname = TOOL_IDS[0]
    bl_label = "Liquify"
    bl_description = ("Warp the photo like Photoshop's Liquify. The camera background and the "
                      "projected mesh follow live. F: size · Alt: invert Pucker/Bloat · "
                      "W R S P B: brushes")
    bl_icon = "brush.uv_sculpt.grab"
    bl_keymap = KEYMAP
    draw_settings = _draw_settings


class MULTICAMPROJECT_TL_LiquifyUV(MULTICAMPROJECT_TL_Liquify):
    bl_context_mode = 'UV'
    bl_idname = TOOL_IDS[1]


_tools = (MULTICAMPROJECT_TL_Liquify, MULTICAMPROJECT_TL_LiquifyUV)


def register():
    global _draw_handle
    for t in _tools:
        bpy.utils.register_tool(t, separator=True)
    _draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    for t in reversed(_tools):
        bpy.utils.unregister_tool(t)
    _hover.clear()
