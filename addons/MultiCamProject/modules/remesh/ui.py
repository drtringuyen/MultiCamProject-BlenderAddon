import bpy


class MULTICAMPROJECT_PT_Remesh(bpy.types.Panel):
    bl_label = "Remesh"
    bl_idname = "MULTICAMPROJECT_PT_remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 4

    def draw_header(self, context):
        self.layout.label(icon='MOD_REMESH')

    def draw(self, context):
        row = self.layout.row()
        row.scale_y = 1.4
        row.operator("multicamproject.remesh_poly_cut", icon='GP_SELECT_STROKES')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Remesh)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Remesh)
