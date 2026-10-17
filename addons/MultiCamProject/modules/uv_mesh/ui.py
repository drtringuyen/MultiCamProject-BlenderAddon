import bpy

from . import tube


def draw_row(layout, context):
    """03's uvMesh row: [uvTube] [uvExtrude] [Close Hole] - each greyed out until Edit Mode
    has the right selection (edges / faces)."""
    row = layout.row(align=True)
    row.scale_y = 1.25
    row.operator("multicamproject.uv_tube", text="uvTube", icon='MOD_SCREW')
    row.operator("multicamproject.uv_extrude", text="uvExtrude", icon='FACESEL')
    row.operator("multicamproject.close_hole", text="Close Hole", icon='MOD_TRIANGULATE')


class MULTICAMPROJECT_PT_uv_tube(bpy.types.Panel):
    """While a uvTube is open: its sides, segments, radius, and Confirm / Cancel."""
    bl_label = "uvTube"
    bl_idname = "MULTICAMPROJECT_PT_uv_tube"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return tube.session_object(context) is not None

    def draw(self, context):
        obj = tube.session_object(context)
        mod = tube.modifier(obj)
        col = self.layout.column(align=True)
        if mod is None:
            col.label(text="The tube's modifier is gone", icon='ERROR')
        else:
            for name in tube.INPUTS:
                tube.draw_input(col, mod, name)
        col.separator()
        hint = col.column(align=True)
        hint.active = False
        hint.label(text="Edit the points · Alt+S radius per point")
        hint.label(text="Ctrl+Wheel sides · Shift+Wheel segments")
        row = col.row(align=True)
        row.scale_y = 1.4
        row.operator("multicamproject.uv_tube_finish", text="Confirm (Enter)",
                     icon='CHECKMARK').action = 'CONFIRM'
        row.operator("multicamproject.uv_tube_finish", text="Cancel (Esc)",
                     icon='X').action = 'CANCEL'


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_uv_tube)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_uv_tube)
