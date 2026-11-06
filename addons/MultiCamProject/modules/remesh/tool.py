"""The PolyCut tool, in the Sculpt Mode toolbar only (Edit Mode keeps Blender's own
Ctrl+LMB = extrude to cursor). Its key works only while it is the active tool:
  Ctrl+LMB  starts a PolyCut (the click is the first point): the first point / double
            click cuts a closed shape, Enter an open line; then Set Seam or No Seam
The header holds Cut Through (through the whole mesh, or only the faces the view sees).
"""
import bpy
from bpy.types import WorkSpaceTool

TOOL_SCULPT = "multicamproject.polycut_sculpt"

_KEYMAP = (
    ("multicamproject.remesh_poly_cut", {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True},
     None),
)


def _draw_settings(context, layout, tool):
    layout.label(text="Ctrl+Click: PolyCut  ·  first point / double click: closed  ·  Enter: open")
    layout.prop(context.scene, "multicamproject_polycut_through", text="Cut Through")


class MULTICAMPROJECT_TL_PolyCutSculpt(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'SCULPT'
    bl_idname = TOOL_SCULPT
    bl_label = "PolyCut"
    bl_description = ("Ctrl+Click starts a PolyCut: click points; the first point or a double "
                      "click cuts a closed shape, Enter an open line. Then Set Seam or No Seam "
                      "for the new edges")
    bl_icon = "ops.sculpt.lasso_face_set"
    bl_widget = None
    bl_keymap = _KEYMAP
    draw_settings = staticmethod(_draw_settings)


_TOOLS = (MULTICAMPROJECT_TL_PolyCutSculpt,)


def tool_id(mode=None):
    return TOOL_SCULPT


def register():
    for t in _TOOLS:
        bpy.utils.register_tool(t, separator=True)


def unregister():
    for t in reversed(_TOOLS):
        try:
            bpy.utils.unregister_tool(t)
        except (ValueError, RuntimeError):
            pass
