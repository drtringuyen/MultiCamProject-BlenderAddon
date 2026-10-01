import bpy

from . import props, session, tool


def draw_brush(context, layout, header=False):
    """Brush settings - in the tool header (header=True) and the sidebar panel."""
    st = props.settings(context)
    if header:
        layout.prop(st, "brush", text="", expand=True, icon_only=True)
        layout.prop(st, "size")
        row = layout.row(align=True)
        row.prop(st, "strength", slider=True)
        row.prop(st, "use_pressure", text="", icon='STYLUS_PRESSURE')
        return
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(st, "brush", expand=True, icon_only=True)
    col.label(text=props.BRUSHES[[b[0] for b in props.BRUSHES].index(st.brush)][1])
    col = layout.column(align=True)
    col.prop(st, "size")
    row = col.row(align=True)
    row.prop(st, "strength", slider=True)
    row.prop(st, "use_pressure", text="", icon='STYLUS_PRESSURE')


class MULTICAMPROJECT_PT_Liquify(bpy.types.Panel):
    bl_label = "Liquify"
    bl_idname = "MULTICAMPROJECT_PT_liquify"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"

    def draw_header(self, context):
        self.layout.label(icon='MOD_WARP')

    def draw(self, context):
        layout = self.layout
        s = session.active()
        if not tool.tool_active(context):
            layout.operator("multicamproject.liquify_tool", icon='BRUSH_DATA')
        if s is None:
            self._draw_idle(context, layout)
            return

        box = layout.box()
        box.label(text=s.photo_name, icon='IMAGE_DATA')
        col = box.column(align=True)
        col.active = False
        if s.reedit:
            col.label(text="Re-editing the saved warp")
        col.label(text=f"Preview {s.lq.w}x{s.lq.h} · bake {s.size[0]}x{s.size[1]}")
        col.label(text=f"{len(s.users['cameras'])} camera(s), "
                       f"{len(s.users['materials'])} material(s)")
        if context.space_data.image != s.preview:
            layout.label(text="This editor shows another image", icon='ERROR')

        draw_brush(context, layout)

        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = s.lq.can_undo
        sub.operator("multicamproject.liquify_undo", text="", icon='LOOP_BACK')
        sub = row.row(align=True)
        sub.enabled = s.lq.can_redo
        sub.operator("multicamproject.liquify_redo", text="", icon='LOOP_FORWARDS')
        row.operator("multicamproject.liquify_reset", icon='X')

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("multicamproject.liquify_bake", icon='CHECKMARK')
        row.operator("multicamproject.liquify_cancel", icon='CANCEL')
        if s.out_path:
            col = layout.column(align=True)
            col.active = False
            col.label(text="Bakes to " + bpy.path.basename(s.out_path))

    @staticmethod
    def _draw_idle(context, layout):
        img = context.space_data.image
        row = layout.row()
        row.scale_y = 1.4
        row.operator("multicamproject.liquify_start", icon='MOD_WARP')
        cams = session.cameras_showing(img)
        if cams:
            col = layout.column(align=True)
            col.label(text=f"Background of {len(cams)} camera(s):", icon='CAMERA_DATA')
            for cam in cams[:5]:
                col.label(text="   " + cam.name)
            if len(cams) > 5:
                col.label(text=f"   ... and {len(cams) - 5} more")
        elif img is not None:
            layout.label(text="No camera uses this image", icon='INFO')
        draw_brush(context, layout)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Liquify)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Liquify)
