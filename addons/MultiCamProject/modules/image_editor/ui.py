import bpy

from ... import gate
from . import inpaint, lasso_ops, props, session, sources, tool


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
        gate.lock(self.layout, context)     # greyed out until Setup File IO
        layout = self.layout
        s = session.active()
        if not tool.tool_active(context):
            layout.operator("multicamproject.liquify_tool", icon='BRUSH_DATA',
                            text="Liquify Tool (Ctrl+Alt+L)")
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
        if s.inpaint:
            col = layout.column(align=True)
            col.active = False
            col.label(text="Bakes into the Inpaint copy (its Ctrl+Z, its Save)")
        elif s.out_path:
            col = layout.column(align=True)
            col.active = False
            col.label(text="Bakes to " + bpy.path.basename(s.out_path))

    @staticmethod
    def _draw_idle(context, layout):
        img = context.space_data.image
        row = layout.row()
        row.scale_y = 1.4
        row.operator("multicamproject.liquify_start", icon='MOD_WARP')
        row = layout.row(align=True)
        row.label(text="Preview")
        row.prop(props.settings(context), "preview_res", expand=True)
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


class MULTICAMPROJECT_PT_Lasso(bpy.types.Panel):
    bl_label = "Lasso"
    bl_idname = "MULTICAMPROJECT_PT_lasso"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"

    def draw_header(self, context):
        self.layout.label(icon='SELECT_SET')

    def draw(self, context):
        gate.lock(self.layout, context)     # greyed out until Setup File IO
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.4
        row.operator("multicamproject.lasso", icon='SELECT_SET')
        draw_lasso_settings(context, layout, context.space_data.image)
        img = context.space_data.image
        if img is not None and img.is_dirty:
            layout.label(text="Unsaved pixels - Image > Save", icon='ERROR')


def draw_lasso_settings(context, layout, img):
    """Hole fill + Lasso Undo + key hints - Image Editor and 3D Viewport panels."""
    st = props.lasso_settings(context)
    row = layout.row(align=True)
    row.prop(st, "fill", text="Hole")
    sub = row.row(align=True)
    sub.active = st.fill != 'TRANSPARENT'
    sub.prop(st, "color", text="")
    if st.fill == 'AUTO' and img is not None:
        col = layout.column(align=True)
        col.active = False
        col.label(text=f"{img.name}: " + ("transparent hole" if lasso_ops.has_alpha(img)
                                         else "no alpha - colour fill"))
    n = lasso_ops.undo_steps()
    layout.operator("multicamproject.lasso_undo", icon='LOOP_BACK',
                    text=f"Lasso Undo ({n})" if n else "Lasso Undo")
    col = layout.column(align=True)
    col.active = False
    col.label(text="Ctrl+Alt+K: lasso · click outside: apply, next")
    col.label(text="G R S · Shift+D stamp · X delete · Ctrl+C/V")
    col.label(text="Ctrl+Z undo · Enter apply · Esc revert all")


class MULTICAMPROJECT_PT_LassoView3D(bpy.types.Panel):
    """Lasso settings for the soloed camera's photo (Ctrl+Alt+K over the camera view)"""
    bl_label = "Lasso"
    bl_idname = "MULTICAMPROJECT_PT_lasso_view3d"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='SELECT_SET')

    def draw(self, context):
        gate.lock(self.layout, context)     # greyed out until Setup File IO
        cam = context.scene.camera
        img = None
        if cam is not None and cam.type == 'CAMERA' and len(cam.data.background_images):
            img = cam.data.background_images[0].image
        draw_lasso_settings(context, self.layout, img)


class MULTICAMPROJECT_PT_Inpaint(bpy.types.Panel):
    bl_label = "Inpaint"
    bl_idname = "MULTICAMPROJECT_PT_inpaint"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"

    def draw_header(self, context):
        self.layout.label(icon='ANCHOR_CENTER')

    def draw(self, context):
        gate.lock(self.layout, context)     # greyed out until Setup File IO
        layout = self.layout
        s = inpaint.active()
        st = props.inpaint_settings(context)
        if s is None:
            img, src = inpaint.target(context)
            reason = inpaint.start_reason(context)
            if img is not None:
                col = layout.column(align=True)
                col.label(text=img.name, icon='IMAGE_DATA')
                sub = col.column(align=True)
                sub.active = False
                sub.label(text=sources.label(src)
                          + ("  ·  inpainted" if sources.has_inpaint(src) else ""))
            # [Inpaint] [Reload] [Remove]: the last two only once there is an _inpaint
            row = layout.row(align=True)
            row.scale_y = 1.4
            row.operator("multicamproject.inpaint", icon='ANCHOR_CENTER', text="Inpaint")
            if src is not None and sources.inpaint_image(src) is not None:
                for op_id, icon in (("multicamproject.inpaint_reload", 'FILE_REFRESH'),
                                    ("multicamproject.inpaint_remove", 'TRASH')):
                    op = row.operator(op_id, text="", icon=icon)
                    op.kind, op.owner = src.kind, src.owner.name
            if reason:
                layout.label(text=reason, icon='INFO')
            self._draw_settings(layout, st)
            return

        box = layout.box()
        box.label(text=s.alb_name, icon='IMAGE_DATA')
        col = box.column(align=True)
        col.active = False
        col.label(text=f"Copy {s.w}x{s.h} · {len(s.undo)} fill(s)")
        if not inpaint.running():
            box.label(text="Paused - Resume, Save or Discard", icon='PAUSE')
            row = box.row()
            row.operator("multicamproject.inpaint", icon='PLAY', text="Resume")

        self._draw_settings(layout, st)

        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = bool(s.undo)
        sub.operator("multicamproject.inpaint_step", text="", icon='LOOP_BACK').redo = False
        sub = row.row(align=True)
        sub.enabled = bool(s.redo)
        sub.operator("multicamproject.inpaint_step", text="", icon='LOOP_FORWARDS').redo = True
        row.operator("multicamproject.inpaint_fill", icon='ANCHOR_CENTER')

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("multicamproject.inpaint_save", icon='CHECKMARK')
        row.operator("multicamproject.inpaint_discard", icon='CANCEL')
        col = layout.column(align=True)
        col.active = False
        col.label(text="Saves <name>_inpaint, the original stays")

    @staticmethod
    def _draw_settings(layout, st):
        row = layout.row(align=True)
        row.prop(st, "mode", expand=True)
        col = layout.column(align=True)
        if st.mode == 'BRUSH':
            col.prop(st, "size")
        col.prop(st, "grow")
        col.prop(st, "grain", slider=True)
        layout.prop(st, "auto_fill")
        row = layout.row(align=True)        # the UV overlay while Inpaint runs (off by default)
        row.prop(st, "show_uv", toggle=True, icon='UV')
        sub = row.row(align=True)
        sub.active = st.show_uv
        sub.prop(st, "uv_opacity", text="Opacity", slider=True)


class MULTICAMPROJECT_PT_Inpainting(bpy.types.Panel):
    """06. Inpainting: the active object's images to inpaint - ALB_, BAo_ and its cameras'
    photos. Each opens in the Image Editor with Inpaint on; a saved one is <name>_inpaint
    (the original stays), which the materials and 05 / 07 / Export use. Optional: 07 does not
    wait for it"""
    bl_label = "06. Inpainting"
    bl_idname = "MULTICAMPROJECT_PT_inpainting"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 5

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and hasattr(obj, "multicamproject_bake")

    def draw_header(self, context):
        self.layout.label(icon='ANCHOR_CENTER')

    def draw(self, context):
        layout, _open = gate.step_box(self.layout, context, 6)
        obj = context.active_object
        if not sources.draw_ready(obj):
            hint = layout.row()
            hint.active = False
            hint.label(text="Bake BAo_ first (03)", icon='INFO')
            return
        if inpaint.active() is not None:
            layout.label(text=f"Inpaint open on {inpaint.active().alb_name}", icon='PAUSE')
        col = layout.column(align=True)
        for src in sources.object_sources(obj):
            row = col.row(align=True)
            done = sources.has_inpaint(src)
            name = (f"{sources.KINDS[src.kind]}" if src.kind != 'BG'
                    else f"BG {src.owner.name}")
            row.label(text=name + ("  ·  inpainted" if done else ""),
                      icon='CHECKMARK' if done else 'IMAGE_DATA')
            sources.draw_button(row, src.kind, src.owner)
            if sources.inpaint_image(src) is not None:
                for op_id, icon in (("multicamproject.inpaint_reload", 'FILE_REFRESH'),
                                    ("multicamproject.inpaint_remove", 'TRASH')):
                    op = row.operator(op_id, text="", icon=icon)
                    op.kind, op.owner = src.kind, src.owner.name
        hint = layout.column(align=True)
        hint.active = False
        hint.label(text="BG / BAo_ inpainted: 05 bakes again · ALB_: 07 again", icon='INFO')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Inpainting)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Liquify)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Lasso)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Inpaint)
    bpy.utils.register_class(MULTICAMPROJECT_PT_LassoView3D)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_LassoView3D)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Inpaint)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Lasso)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Liquify)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Inpainting)
