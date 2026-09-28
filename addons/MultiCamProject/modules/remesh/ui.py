import bpy

from . import core


class MULTICAMPROJECT_UL_RemeshRegions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index=0):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False,
                 icon='MOD_BOOLEAN' if item.kind == 'CUT' else 'FACE_MAPS')
        sub = row.row(align=True)
        sub.active = not item.delete
        sub.prop(item, "ratio", text="")
        row.prop(item, "delete", text="", icon='TRASH' if item.delete else 'CHECKBOX_DEHLT',
                 emboss=False)


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
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh", icon='INFO')
            return
        d = core.data(obj)

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("multicamproject.remesh_polyline_cut", icon='GP_SELECT_STROKES')
        row.operator("multicamproject.remesh_from_face_set", text="", icon='FACE_MAPS')

        if d.regions:
            row = layout.row()
            row.template_list("MULTICAMPROJECT_UL_RemeshRegions", "", d, "regions", d, "active",
                              rows=3)
            col = row.column(align=True)
            col.operator("multicamproject.remesh_remove_region", text="", icon='REMOVE')
            if 0 <= d.active < len(d.regions):
                r = d.regions[d.active]
                if r.kind == 'CUT':
                    col = layout.column(align=True)
                    col.prop(r, "near")
                    col.prop(r, "far")
            row = layout.row(align=True)
            row.prop(d, "preview", toggle=True, icon='HIDE_OFF' if d.preview else 'HIDE_ON')
            row.label(text=f"{len(obj.data.polygons):,} faces")

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("multicamproject.remesh_apply", icon='CHECKMARK')
        row.operator("multicamproject.remesh_restore", text="", icon='LOOP_BACK')


_CLASSES = (MULTICAMPROJECT_UL_RemeshRegions, MULTICAMPROJECT_PT_Remesh)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
