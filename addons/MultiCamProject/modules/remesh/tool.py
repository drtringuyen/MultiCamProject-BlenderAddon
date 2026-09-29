"""The PolyCut tool, in the Sculpt Mode toolbar only (Edit Mode keeps Blender's own L =
Select Linked Pick and Ctrl+LMB = extrude to cursor). Its keys work only while it is the
active tool:
  L         the face set under the mouse -> Set Faces
  Ctrl+LMB  starts a PolyCut (the click is the first point); Enter / double click cuts,
            then Set Faces on the new face set
"""
import bpy
from bpy.types import WorkSpaceTool

TOOL_SCULPT = "multicamproject.polycut_sculpt"

_KEYMAP = (
    ("multicamproject.remesh_pick", {"type": 'L', "value": 'PRESS'}, None),
    ("multicamproject.remesh_poly_cut", {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True},
     None),
)


def _draw_settings(context, layout, tool):
    layout.label(text="Ctrl+Click: PolyCut   L: face set under the mouse -> Set Faces")


class MULTICAMPROJECT_TL_PolyCutSculpt(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'SCULPT'
    bl_idname = TOOL_SCULPT
    bl_label = "PolyCut"
    bl_description = ("Ctrl+Click starts a PolyCut: click points, Enter or double click cuts "
                      "and opens Set Faces for the new face set.\n"
                      "L: Set Faces for the face set under the mouse")
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
