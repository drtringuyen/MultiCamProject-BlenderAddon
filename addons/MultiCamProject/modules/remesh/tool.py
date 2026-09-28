"""The PolyCut tool, in the Sculpt and Edit Mode toolbars. Its keys work only while it is
the active tool, so Blender's own L / Ctrl+LMB (Edit Mode: Select Linked Pick, extrude to
cursor) stay with the other tools:
  L         the face set under the mouse -> Set Faces
  Ctrl+LMB  starts a PolyCut (the click is the first point); Enter / double click cuts,
            then Set Faces on the new face set
"""
import bpy
from bpy.types import WorkSpaceTool

TOOL_SCULPT = "multicamproject.polycut_sculpt"
TOOL_EDIT = "multicamproject.polycut_edit"

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


class MULTICAMPROJECT_TL_PolyCutEdit(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = TOOL_EDIT
    bl_label = "PolyCut"
    bl_description = MULTICAMPROJECT_TL_PolyCutSculpt.bl_description
    bl_icon = "ops.sculpt.lasso_face_set"
    bl_widget = None
    bl_keymap = _KEYMAP
    draw_settings = staticmethod(_draw_settings)


_TOOLS = (MULTICAMPROJECT_TL_PolyCutSculpt, MULTICAMPROJECT_TL_PolyCutEdit)


def tool_id(mode):
    return TOOL_EDIT if mode == 'EDIT_MESH' else TOOL_SCULPT


def register():
    for t in _TOOLS:
        bpy.utils.register_tool(t, separator=True)


def unregister():
    for t in reversed(_TOOLS):
        try:
            bpy.utils.unregister_tool(t)
        except (ValueError, RuntimeError):
            pass
