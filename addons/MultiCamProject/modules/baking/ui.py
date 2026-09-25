import bpy

from . import common, fingerprint, gn_final, normal


def _res(n):
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n}"


def draw_final_toggle(layout, obj, scope='SELECTED'):
    final = gn_final.is_final(obj) if obj else False
    row = layout.row(align=True)
    op = row.operator("multicamproject.bake_set_final", text="Projection", icon='CAMERA_DATA',
                      depress=obj is not None and not final)
    op.state, op.scope = False, scope
    op = row.operator("multicamproject.bake_set_final", text="Final", icon='SHADING_TEXTURE',
                      depress=final)
    op.state, op.scope = True, scope


class MULTICAMPROJECT_PT_Baking(bpy.types.Panel):
    bl_label = "Baking"
    bl_idname = "MULTICAMPROJECT_PT_baking"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 2

    def draw_header(self, context):
        self.layout.label(icon='RENDER_STILL')

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        s = common.settings(context.scene)
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh object", icon='INFO')
            return
        d = common.data(obj)

        draw_final_toggle(layout, obj)
        if not common.has_uv_normal(obj):
            box = layout.box()
            box.alert = True
            box.label(text=f"No '{common.UV_NORMAL}' UV map", icon='ERROR')
            box.label(text="Make it by hand: non-overlapping, inside 0-1")
        elif d.fingerprint and fingerprint.is_outdated(obj):
            box = layout.box()
            box.alert = True
            box.label(text="Bake is outdated - the projection changed", icon='ERROR')

        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("multicamproject.bake_albedo", icon='RENDER_STILL')
        row = col.row(align=True)
        row.operator("multicamproject.bake_normal", icon='NORMALS_FACE')
        op = row.operator("multicamproject.bake_albedo", text="Both", icon='RENDER_RESULT')
        op.with_normal = True

        info = layout.column(align=True)
        if d.alb_size:
            info.label(text=f"ALB {_res(d.alb_size)}  ·  {d.last_bake_seconds:.1f} s",
                       icon='IMAGE_RGB')
        if d.nor_size:
            label = normal.LABELS.get(d.nor_source_used, d.nor_source_used)
            info.label(text=f"NOR {_res(d.nor_size)}  ·  {label}  ·  {d.last_nor_seconds:.1f} s",
                       icon='NORMALS_FACE')
            if d.alb_size and d.nor_size != d.alb_size:
                info.label(text="Normal map is a preview - make it again at full size",
                           icon='INFO')

        self._draw_normal(layout, context, obj, s)

        header, body = layout.panel("multicamproject_bake_settings", default_closed=True)
        header.label(text="Settings", icon='PREFERENCES')
        if body:
            col = body.column()
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(s, "output_dir")
            col.prop(s, "resolution")
            col.prop(s, "margin")
            col.prop(s, "device")
            col.prop(s, "anti_alias")
            col.prop(s, "roughness")
            col.separator()
            col.prop(s, "color_source")
            if s.color_source == 'SCAN_ATTRIBUTE':
                col.prop(s, "scan_color_name")
                if obj.data.color_attributes.get(s.scan_color_name) is None:
                    col.label(text=f"No '{s.scan_color_name}' - Color comes from ALB", icon='INFO')
            col.prop(s, "png_compression")

    @staticmethod
    def _draw_normal(layout, context, obj, s):
        header, body = layout.panel("multicamproject_bake_normal", default_closed=False)
        header.label(text="Normal Map", icon='NORMALS_FACE')
        if not body:
            return
        col = body.column()
        # Lite / AI switch - the times below compare the two
        row = col.row(align=True)
        row.scale_y = 1.2
        lite = s.nor_source == 'HIGHPASS'
        op = row.operator("multicamproject.bake_normal_mode", text="Lite", icon='IMAGE_RGB', depress=lite)
        op.mode = 'LITE'
        sub = row.row(align=True)
        need = normal.ai.missing()
        sub.enabled = not need
        op = sub.operator("multicamproject.bake_normal_mode", text="AI", icon='LIGHT_SUN',
                          depress=s.nor_source == 'AI')
        op.mode = 'AI'
        if need:
            r = col.row(align=True)
            r.label(text=f"AI needs {need}", icon='INFO')
            r.operator("multicamproject.bake_setup_ai", text="Set up AI", icon='IMPORT')
        runs = normal.times(common.data(obj))
        if runs:
            t = col.column(align=True)
            t.active = False
            for label, size, sec in runs:
                t.label(text=f"{label}  ·  {_res(size)}  ·  {sec:.1f} s", icon='TIME')
        col.prop(s, "nor_source", text="Source")
        src = s.nor_source
        sub = col.column(align=True)
        if src in {'HIGHPASS', 'BLEND'}:
            sub.prop(s, "nor_strength")
            sub.prop(s, "nor_radius")
            sub.prop(s, "nor_invert")
        if src in {'MESH', 'BLEND'}:
            sub.prop(s, "hp_object")
            sub.prop(s, "cage_extrusion")
            row = sub.row(align=True)
            row.prop(s, "smooth_source")
            r = row.row(align=True)
            r.active = s.smooth_source
            r.prop(s, "smooth_iterations", text="")
        col.prop(s, "nor_preview_2k")
        why = normal.problem(obj, context.scene, src)
        if why:
            col.label(text=why, icon='ERROR')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Baking)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Baking)
