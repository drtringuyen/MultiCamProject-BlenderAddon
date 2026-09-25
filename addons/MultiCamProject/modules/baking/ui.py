import bpy

from . import common, fingerprint, gn_final, normal


def _res(n):
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n}"


def draw_final_toggle(layout, data, prop):
    """Projection | Final as a two-state toggle: the current state stays highlighted."""
    layout.row(align=True).prop(data, prop, expand=True)


LABEL_UNITS = 5.2      # the label column of the Baking panel's settings rows


def indented(layout):
    """A column indented to the sub-panel arrows (as in Camera Project)."""
    row = layout.row()
    row.separator(factor=1.6)
    return row.column()


def labeled(context, layout, label, units=LABEL_UNITS):
    """A row with its label in a fixed column: every field starts at the same x. The split
    comes from the region's pixel width (a label's ui_units_x gets stretched by some fields)."""
    unit = 20 * context.preferences.system.ui_scale
    avail = max(1.0, context.region.width - 3.6 * unit)     # panel margins + indent
    split = layout.split(factor=min(0.45, units * unit / avail), align=True)
    split.label(text=label)
    return split.row(align=True)


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
            indented(layout).label(text="Select a mesh object", icon='INFO')
            return
        d = common.data(obj)
        top = indented(layout)

        draw_final_toggle(top, obj, "multicamproject_view")
        if not common.has_uv_normal(obj):
            box = top.box()
            box.alert = True
            box.label(text=f"No '{common.UV_NORMAL}' UV map", icon='ERROR')
            box.label(text="Make it by hand: non-overlapping, inside 0-1")
        elif d.fingerprint and fingerprint.is_outdated(obj):
            box = top.box()
            box.alert = True
            box.label(text="Bake is outdated - the projection changed", icon='ERROR')

        # what the Bake button at the end makes
        # what to bake + what was baked last, in one box
        box = top.box().column()
        box.row(align=True).prop(s, "bake_what", expand=True)
        info = box.column(align=True)
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
        self._draw_bake_button(indented(layout), s)
        self._draw_settings(layout, context, obj, s)

    @staticmethod
    def _draw_bake_button(layout, s):
        """One Bake for everything the options say."""
        parts = {'ALBEDO': "Albedo", 'NORMAL': "Normal", 'BOTH': "Albedo + Normal"}[s.bake_what]
        if s.bake_what != 'ALBEDO':
            parts += f" ({normal.LABELS.get(s.nor_source, s.nor_source)}"
            parts += ", 2K preview)" if s.nor_preview_2k else ")"
        row = layout.row()
        row.scale_y = 1.5
        row.operator("multicamproject.bake", text=f"Bake {parts}", icon='RENDER_STILL')

    @staticmethod
    def _draw_settings(layout, context, obj, s):
        header, body = layout.panel("multicamproject_bake_settings", default_closed=True)
        header.label(text="Settings", icon='PREFERENCES')
        if not body:
            return
        col = indented(body).column(align=True)
        labeled(context, col, "Folder").prop(s, "output_dir", text="")
        labeled(context, col, "Resolution").prop(s, "resolution", text="")
        labeled(context, col, "Margin").prop(s, "margin", text="")
        labeled(context, col, "Device").prop(s, "device", text="")
        labeled(context, col, "Samples").prop(s, "anti_alias", text="")
        labeled(context, col, "Roughness").prop(s, "roughness", text="")
        col.separator()
        labeled(context, col, "Vertex Color").prop(s, "color_source", text="")
        if s.color_source == 'SCAN_ATTRIBUTE':
            labeled(context, col, "Scan Attribute").prop(s, "scan_color_name", text="")
            if obj.data.color_attributes.get(s.scan_color_name) is None:
                labeled(context, col, "").label(
                    text=f"No '{s.scan_color_name}' - Color comes from ALB", icon='INFO')
        labeled(context, col, "PNG Compression").prop(s, "png_compression", text="")

    @staticmethod
    def _draw_normal(layout, context, obj, s):
        header, body = layout.panel("multicamproject_bake_normal", default_closed=False)
        header.label(text="Normal Map", icon='NORMALS_FACE')
        if not body:
            return
        col = indented(body)
        src = s.nor_source
        ai = src == 'AI'
        # [Lite / AI v] [High-pass v] - the engine, then Lite's method
        row = labeled(context, col, "Generate Engine")
        row.menu("MULTICAMPROJECT_MT_normal_engine", text="AI" if ai else "Lite",
                 icon='LIGHT_SUN' if ai else 'IMAGE_RGB')
        if not ai:
            row.menu("MULTICAMPROJECT_MT_normal_method", text=normal.LABELS.get(src, src))

        # the method's settings, full names: Strength | Radius, then Invert | Preview at 2K
        # the method's settings; Preview at 2K always ends the last row
        opts = col.column(align=True)
        mesh = src in {'MESH', 'BLEND'}
        if src in {'HIGHPASS', 'BLEND'}:
            row = opts.row(align=True)
            row.prop(s, "nor_strength", text="Strength")
            row.prop(s, "nor_radius", text="Radius")
            row = opts.row(align=True)
            row.prop(s, "nor_invert", text="Invert", toggle=True)
            if not mesh:
                row.prop(s, "nor_preview_2k", text="Preview at 2K", toggle=True)
        if mesh:        # High Poly | Cage | Smooth | Iterations | 2K - one row
            row = opts.row(align=True)
            row.prop(s, "hp_object", text="")
            row.prop(s, "cage_extrusion", text="Cage")
            row.prop(s, "smooth_source", text="Smooth", toggle=True)
            r = row.row(align=True)
            r.active = s.smooth_source
            r.prop(s, "smooth_iterations", text="")
            row.prop(s, "nor_preview_2k", text="2K", toggle=True)
        elif src == 'AI':
            opts.row(align=True).prop(s, "nor_preview_2k", text="Preview at 2K", toggle=True)
        why = normal.problem(obj, context.scene, src)
        if why and not (s.bake_what == 'BOTH' and why == "Bake the albedo first"):
            col.label(text=why, icon='ERROR')
        runs = normal.times(common.data(obj))
        if runs:        # the last time of each method and size, to compare them
            t = col.column(align=True)
            t.active = False
            for label, size, sec in runs:
                t.label(text=f"{label}  ·  {_res(size)}  ·  {sec:.1f} s", icon='TIME')


class MULTICAMPROJECT_MT_normal_engine(bpy.types.Menu):
    """Lite (by default) or AI; AI is greyed out until it is set up"""
    bl_label = "Normal Map Engine"
    bl_idname = "MULTICAMPROJECT_MT_normal_engine"

    def draw(self, context):
        layout = self.layout
        op = layout.operator("multicamproject.bake_normal_mode", text="Lite", icon='IMAGE_RGB')
        op.mode = 'LITE'
        need = normal.ai.missing()
        row = layout.row()
        row.enabled = not need
        op = row.operator("multicamproject.bake_normal_mode", text="AI", icon='LIGHT_SUN')
        op.mode = 'AI'
        if need:
            layout.separator()
            layout.operator("multicamproject.bake_setup_ai", text="Set up AI...", icon='IMPORT')


class MULTICAMPROJECT_MT_normal_method(bpy.types.Menu):
    """Lite's methods: High-pass, Bake from mesh (+ Mesh + Albedo in the Full build)"""
    bl_label = "Normal Map Method"
    bl_idname = "MULTICAMPROJECT_MT_normal_method"

    def draw(self, context):
        s = common.settings(context.scene)
        for key, _label, _desc in normal.available_sources():
            if key != 'AI':
                self.layout.prop_enum(s, "nor_source", key)


_classes = (MULTICAMPROJECT_MT_normal_engine, MULTICAMPROJECT_MT_normal_method)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Baking)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Baking)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
