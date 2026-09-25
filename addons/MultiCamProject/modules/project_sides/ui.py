import bpy

from . import core


class MULTICAMPROJECT_PT_ProjectSides(bpy.types.Panel):
    bl_label = "Project from Sides"
    bl_idname = "MULTICAMPROJECT_PT_project_sides"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(icon='AXIS_SIDE')

    def draw(self, context):
        layout = self.layout
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
