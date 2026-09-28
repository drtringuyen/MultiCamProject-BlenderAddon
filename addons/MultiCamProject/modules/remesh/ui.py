import bpy

from ..camera_project import core as cp
from . import core, gn_remesh as gn


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
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.4
        row.operator("multicamproject.remesh_poly_cut", icon='GP_SELECT_STROKES')

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return
        mod = gn.get_modifier(obj)
        if mod is None:
            layout.operator("multicamproject.remesh_add_modifier", icon='GEOMETRY_NODES')
            return
        box = layout.box()
        row = box.row(align=True)
        row.prop(mod, "show_viewport", text="", emboss=False)
        row.label(text="GN-Remesh", icon='GEOMETRY_NODES')
        col = box.column(align=True)
        for name in ("Face Set", "Isolate", "Invert"):
            try:
                col.prop(cp.input_socket(mod, name), "value", text=name)
            except (KeyError, AttributeError):
                pass
        ids = core.face_set_ids(obj)
        if ids:
            flow = box.grid_flow(columns=3, even_columns=True, align=True)
            for fs, count in ids:
                flow.label(text=f"{fs}: {count:,}")


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Remesh)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Remesh)
