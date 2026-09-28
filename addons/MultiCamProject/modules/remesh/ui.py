import bpy

from . import workflow as wf


class MULTICAMPROJECT_PT_Remesh(bpy.types.Panel):
    bl_label = "Remesh"
    bl_idname = "MULTICAMPROJECT_PT_remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 1

    def draw_header(self, context):
        self.layout.label(icon='MOD_REMESH')

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh", icon='INFO')
            return
        src = wf.source_of(obj)
        if src is None:
            row = layout.row()
            row.scale_y = 1.4
            row.operator("multicamproject.remesh", icon='MOD_REMESH')
            if wf.is_original(obj):
                layout.label(text="High poly of a Remesh copy", icon='INFO')
            return

        row = layout.row(align=True)
        row.label(text=f"High poly: {src.name}", icon='OUTLINER_OB_MESH')

        row = layout.row(align=True)
        row.scale_y = 1.3
        if context.mode not in {'SCULPT', 'EDIT_MESH'}:
            row.operator("multicamproject.remesh_enter_tool", text="PolyCut (Sculpt)",
                         icon='SCULPTMODE_HLT').mode = 'SCULPT'
            row.operator("multicamproject.remesh_enter_tool", text="Edit",
                         icon='EDITMODE_HLT').mode = 'EDIT'
        else:
            row.operator("multicamproject.remesh_set_faces", icon='FACESEL')
            layout.label(text="PolyCut tool: Ctrl+Click cut · L pick face set", icon='INFO')

        box = layout.box()
        mod = next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                    and m.node_group.name == wf.GN_REMESH), None)
        if mod is not None:
            row = box.row(align=True)
            row.prop(mod, "show_viewport", text="", emboss=False)
            row.label(text=wf.GN_REMESH, icon='GEOMETRY_NODES')
        for name in (wf.DEC_OVERALL, wf.DEC_SELECTIVE):
            dec = obj.modifiers.get(name)
            if dec is None or dec.type != 'DECIMATE':
                continue
            row = box.row(align=True)
            row.prop(dec, "show_viewport", text="", emboss=False)
            row.label(text=name, icon='MOD_DECIM')
            row.prop(dec, "ratio", text="")
            if dec.vertex_group:
                row.prop(dec, "invert_vertex_group", text="", icon='ARROW_LEFTRIGHT')
        col = box.column(align=True)
        col.active = False
        col.label(text="Decimate collapses across UV seams", icon='ERROR')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Remesh)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Remesh)
