import bpy

from . import core


class MULTICAMPROJECT_PT_ProjectSides(bpy.types.Panel):
    """0A's settings, opened from the gear next to it in Setup"""
    bl_label = "Project from Sides"
    bl_idname = "MULTICAMPROJECT_PT_project_sides"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'           # a popover only - not drawn in the sidebar
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        layout.label(text="0A. Project from Sides", icon='AXIS_SIDE')
        if not core.camera_project_ready():
            layout.alert = True
            layout.label(text="Needs the Camera Project module", icon='ERROR')
            return
        s = core.settings(context.scene)
        layout.template_ID(s, "image", open="image.open")
        layout.row().prop(s, "cam_type", expand=True)

        col = layout.column(align=True)
        col.prop(s, "height")
        col.prop(s, "distance")
        col.prop(s, "ortho_scale" if s.cam_type == 'ORTHO' else "lens")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("multicamproject.project_sides", text=f"Project from {len(core.SIDES)} Sides",
                     icon='OUTLINER_OB_CAMERA')
        op = row.operator("multicamproject.project_sides", text="", icon='FULLSCREEN_ENTER')
        op.reframe = True


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_ProjectSides)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_ProjectSides)
