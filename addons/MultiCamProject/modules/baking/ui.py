import bpy

from . import common, fingerprint, gn_final, matsync, normal, operators, route


def _res(n):
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n}"


def draw_resolution(layout, s):
    """[1K v]: the scene's resolution - every object set to A (Auto). Only in front of
    Export; 06 shows the object's own size (draw_object_size)."""
    sizes = layout.row(align=True)
    sizes.ui_units_x = 3
    sizes.prop(s, "resolution_menu", text="")


def draw_object_size(layout, obj):
    """[Auto Resolution v]: the object's own texture size - the same field as in the EXPORT
    list (Auto = the scene's resolution, set in front of Export)."""
    sizes = layout.row(align=True)
    sizes.ui_units_x = 6
    sizes.prop(common.data(obj), "tex_size", text="")


def draw_final_toggle(layout, data, prop):
    """Projection | Final as a two-state toggle: the current state stays highlighted."""
    layout.row(align=True).prop(data, prop, expand=True)


LABEL_UNITS = 5.2      # the label column of the Baking panel's settings rows


SUB_INDENT = 3.4       # a sub-panel's content: in to its header icon, below the arrow


def indented(layout, factor=1.6):
    """A column indented to the sub-panel arrows (as in Camera Project); with SUB_INDENT
    one level deeper, for the content of a sub-panel."""
    row = layout.row()
    row.separator(factor=factor)
    return row.column()


def labeled(context, layout, label, units=LABEL_UNITS):
    """A row with its label in a fixed column: every field starts at the same x. The split
    comes from the region's pixel width (a label's ui_units_x gets stretched by some fields)."""
    unit = 20 * context.preferences.system.ui_scale
    avail = max(1.0, context.region.width - 3.6 * unit)     # panel margins + indent
    split = layout.split(factor=min(0.45, units * unit / avail), align=True)
    split.label(text=label)
    return split.row(align=True)


def _draw_original(row, obj):
    """[original picker] [Cage]: as in Cutting & Modelling (the Cage is the object's own)."""
    d = common.data(obj)
    row.prop(d, "bake_source", text="", icon='OUTLINER_OB_MESH')
    cage = row.row(align=True)
    cage.ui_units_x = 4.5
    cage.prop(d, "cage", text="Cage")


class MULTICAMPROJECT_PT_Baking(bpy.types.Panel):
    """06 Bake Final: ALB_ (the Processing material baked onto the object) and NOR_, the
    final 8K textures in MAT_"""
    bl_label = "06. Bake Final"
    bl_idname = "MULTICAMPROJECT_PT_baking"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return not context.scene.get("multicamproject_work_of")     # a Remesh work file

    def draw_header(self, context):
        self.layout.label(icon='RENDER_STILL')

    def draw(self, context):
        layout = self.layout
        from . import jobs
        if jobs.busy():
            jobs.draw(layout)
            return
        obj = context.active_object
        s = common.settings(context.scene)
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh object", icon='INFO')
            return
        d = common.data(obj)
        top = layout.column()      # no indent: the Baking panel reads better flush

        draw_final_toggle(top, obj, "multicamproject_view")
        self._draw_materials(top, obj)
        if d.handmade:
            self._draw_handmade(context, layout, top, obj)
            return
        if d.route_prompt:
            route.draw_prompt(top, obj)
        if not common.has_uv_normal(obj):
            box = top.box()
            box.alert = True
            box.label(text=f"No '{common.UV_NORMAL}' UV map", icon='ERROR')
            box.label(text="Unwrap it (non-overlapping, inside 0-1), or:")
            if hasattr(bpy.types, "MULTICAMPROJECT_OT_export_make_uv"):     # the export module
                box.alert = False
                box.operator("multicamproject.export_make_uv", text="Make uv_normal (Smart UV)",
                             icon='UV').scope = 'SELECTED'
        elif d.fingerprint and fingerprint.is_outdated(obj):
            box = top.box()
            box.alert = True
            box.label(text="Bake is outdated - the projection changed", icon='ERROR')

        # [route v] what to bake (+ the normal map's options behind the gear), what was
        # baked last
        box = top.box().column()
        split = box.split(factor=0.36, align=True)      # the route's full name always fits
        split.prop(d, "route", text="")
        row = split.row(align=True)
        row.prop_enum(s, "bake_what", 'ALBEDO')
        row.prop_enum(s, "bake_what", 'NORMAL')
        row.prop_enum(s, "bake_what", 'BOTH')
        self._draw_route(box, obj)
        info = box.column(align=True)
        if d.alb_size:
            info.label(text=f"ALB {_res(d.alb_size)}  ·  {d.last_bake_seconds:.1f} s",
                       icon='IMAGE_RGB')
        if d.nor_size:
            label = normal.LABELS.get(d.nor_source_used, d.nor_source_used)
            info.label(text=f"NOR {_res(d.nor_size)}  ·  {label}  ·  {d.last_nor_seconds:.1f} s",
                       icon='NORMALS_FACE')
            if d.alb_size and d.nor_size != d.alb_size:
                info.label(text="ALB and NOR sizes differ - bake again", icon='INFO')
        res = common.resolution(obj, context.scene)
        if d.tex_size == 'AUTO':
            info.label(text=f"Auto Resolution = {_res(res)} (the scene's, set in front of Export)",
                       icon='TEXTURE')
        if d.alb_size and d.alb_size != res:
            info.label(text=f"Baked at {_res(d.alb_size)} - bake again for {_res(res)}",
                       icon='INFO')
        # earlier normal map runs (other methods / sizes) to compare the times with
        current = (normal.LABELS.get(d.nor_source_used, d.nor_source_used), d.nor_size)
        others = [r for r in normal.times(d) if (r[0], r[1]) != current]
        if others:
            t = info.column(align=True)
            t.active = False
            for label, size, sec in others:
                t.label(text=f"NOR {_res(size)}  ·  {label}  ·  {sec:.1f} s", icon='TIME')

        self._draw_bake_button(layout, s, obj)
        why = normal.problem(obj, context.scene, s.nor_source)
        if (why and s.bake_what != 'ALBEDO'
                and not (s.bake_what == 'BOTH' and why == "Bake the albedo first")):
            layout.label(text=why, icon='ERROR')      # the popover is mostly closed

    @staticmethod
    def _draw_route(box, obj):
        """What the route bakes from, and what it bakes first (grey), or what it lacks (red)."""
        d = common.data(obj)
        col = box.column(align=True)
        why = route.problem(obj)
        if why:
            col.alert = True
            col.label(text=why, icon='ERROR')
            return
        col.active = False
        r = route.get(obj)
        if r in {route.ORIGINAL, route.MIXED}:
            if d.ba_image is None or not common.file_ok(d.ba_image):
                col.label(text=f"No BAo_ / BNo_ yet: bakes from {d.bake_source.name} first",
                          icon='INFO')
            elif fingerprint.ba_outdated(obj):
                col.label(text=f"BAo_ outdated ({fingerprint.ba_why(obj)}): bakes from the "
                               "source again first", icon='INFO')
            else:
                col.label(text=f"BAo_ / BNo_ {_res(d.ba_size)} from {d.bake_source.name}",
                          icon='MESH_DATA')
        if r in {route.PROJECTION, route.MIXED}:
            if d.bap_image is None or not common.file_ok(d.bap_image):
                col.label(text="No BAp_ yet: renders the projection first", icon='INFO')
            elif fingerprint.bp_outdated(obj):
                col.label(text="BAp_ outdated (the projection changed): renders it again first",
                          icon='INFO')
            else:
                bnp = " / BNp_" if d.bnp_image is not None else ""
                col.label(text=f"BAp_{bnp} {_res(d.bp_size)} from the projection",
                          icon='CAMERA_DATA')
        col.label(text={route.ORIGINAL: "ALB_ / NOR_ = copies of BAo_ / BNo_",
                        route.PROJECTION: "ALB_ / NOR_ = copies of BAp_ / BNp_",
                        route.MIXED: "VCMix alpha: BAo_ -> projection, BNo_ -> BNp_"}[r],
                  icon='FORWARD')

    @staticmethod
    def _draw_materials(layout, obj):
        """[MCP_ picker] [MAT_ picker] [Refresh]: which projection / final material the
        object is linked to; Refresh checks and fixes both. Problems and what the last
        Refresh fixed show below, never in a popup."""
        probs = matsync.problems(obj) if matsync.in_scope(obj) else []
        kinds = {k for k, _t in probs}
        if not matsync.is_projection(obj) and not common.data(obj).handmade:
            # no projection: the original it bakes from (Bake Source) + Cage on top
            _draw_original(layout.row(align=True), obj)
        row = layout.row(align=True)
        cam = getattr(obj, "multicamproject_cam", None)
        for kind, data, ok_icon, enabled in (
                ('MCP', cam, 'NODE_MATERIAL', matsync.is_projection(obj)),
                ('MAT', common.data(obj), 'SHADING_TEXTURE', True)):
            if kind == 'MCP' and not enabled:
                continue            # no projection material: MAT_ takes the whole row
            b = row.row(align=True)
            b.enabled = enabled and data is not None
            b.alert = kind in kinds
            if data is not None:
                b.prop(data, "material", text="", icon='ERROR' if kind in kinds else ok_icon)
        b = row.row(align=True)
        b.alert = 'SLOTS' in kinds
        b.operator("multicamproject.material_refresh", text="",
                   icon='FILE_REFRESH').scope = 'SELECTED'
        row.operator("multicamproject.check_textures", text="", icon='TRASH')
        if (matsync.is_projection(obj) and not common.data(obj).handmade
                and route.uses_original(obj)):
            # Mixed / From Original with a projection: the original it bakes from as well
            _draw_original(layout.row(align=True), obj)
        if probs:
            col = layout.column(align=True)
            col.alert = True
            for _k, t in probs:
                col.label(text=t, icon='ERROR')
        done = operators.last_refresh.get(obj.name)
        if done and done != ["materials OK"]:
            col = layout.column(align=True)
            col.active = False
            for t in done[:4]:
                col.label(text=t, icon='CHECKMARK')

    @staticmethod
    def _draw_handmade(context, layout, top, obj):
        """Handmade: nothing is baked from the projection; after 0E Edit UV, Bake carries
        ALB_ + NOR_ over from uv_old (always both: they share one layout)."""
        from . import handmade
        box = top.box().column(align=True)
        if not handmade.has_uv_old(obj):
            box.label(text="Handmade - baked by hand", icon='INFO')
            box.label(text="New UVs: 0E Rebake: Edit UV (Setup)")
            return
        if obj.mode == 'EDIT':
            box.label(text="Editing uv_normal - Bake rebakes from uv_old", icon='UV')
        elif handmade.needs_rebake(obj):
            box.alert = True
            box.label(text="uv_normal changed - rebake ALB_ + NOR_", icon='ERROR')
        else:
            box.label(text="ALB_ + NOR_ match uv_normal - Finish in Setup (0E)",
                      icon='CHECKMARK')
        row = layout.row(align=True)
        row.scale_y = 1.5
        draw_object_size(row, obj)
        row.separator(factor=0.5)
        row.operator("multicamproject.bake", text="Rebake from uv_old (Albedo + Normal)",
                     icon='FILE_REFRESH')
        why = handmade.rebake_problem(obj)
        if why and obj.mode != 'EDIT':
            layout.label(text=why, icon='ERROR')

    @staticmethod
    def _draw_bake_button(layout, s, obj):
        """[A v] (the active object's own size), one Bake for everything the options
        say, the bake settings' gear at its end."""
        parts = {'ALBEDO': "Albedo", 'NORMAL': "Normal", 'BOTH': "Albedo + Normal"}[s.bake_what]
        if s.bake_what != 'ALBEDO':
            d = common.data(obj)
            if route.get(obj) == route.ORIGINAL and d.original_normal == 'BAKED':
                parts += " (BNo_)"
            else:
                parts += f" ({normal.LABELS.get(s.nor_source, s.nor_source)})"
        row = layout.row(align=True)
        row.scale_y = 1.5
        draw_object_size(row, obj)
        row.separator(factor=0.5)
        row.operator("multicamproject.bake", text=f"Bake {parts}", icon='RENDER_STILL')
        row.popover(panel="MULTICAMPROJECT_PT_bake_settings", text="", icon='PREFERENCES')



def _draw_normal_settings(layout, context):
    """The normal map's options (first part of the bake settings' gear)."""
    s = common.settings(context.scene)
    obj = context.active_object
    col = layout.column()
    col.label(text="Normal Map", icon='NORMALS_FACE')
    src = s.nor_source
    ai = src == 'AI'
    d = common.data(obj) if obj is not None and obj.type == 'MESH' else None
    baked = False
    r = route.get(obj) if d is not None and not d.handmade else None
    if r in {route.ORIGINAL, route.MIXED}:
        # the original's side: BNo_ (its surface), or generated from BAo_ with the engine
        split = col.split(factor=0.34, align=True)
        split.label(text="Original")
        split.prop(d, "original_normal", text="")
        baked = d.original_normal == 'BAKED' and r == route.ORIGINAL
    if r in {route.PROJECTION, route.MIXED}:
        # the projection's side: always generated from BAp_
        split = col.split(factor=0.34, align=True)
        split.label(text="Projection")
        sub = split.row()
        sub.active = False
        sub.label(text="Generated from BAp_ High-pass", icon='IMAGE_RGB')
    engine_col = col.column()
    engine_col.active = not baked
    col = engine_col
    # [Lite / AI v] [High-pass v] - the engine, then Lite's method
    split = col.split(factor=0.34, align=True)
    split.label(text="Generate Engine")
    row = split.row(align=True)
    row.menu("MULTICAMPROJECT_MT_normal_engine", text="AI" if ai else "Lite",
             icon='LIGHT_SUN' if ai else 'IMAGE_RGB')
    if not ai:
        row.menu("MULTICAMPROJECT_MT_normal_method", text=normal.LABELS.get(src, src))

    # the detail's settings; BN_ (from the Bake Source) is the base where it exists
    opts = col.column(align=True)
    if src == 'HIGHPASS':
        row = opts.row(align=True)
        row.prop(s, "nor_strength", text="Strength")
        row.prop(s, "nor_radius", text="Radius")
        opts.row(align=True).prop(s, "nor_invert", text="Invert", toggle=True)
    note = col.row()
    note.active = False
    if d is not None:
        gen = d.original_normal == 'GENERATED'
        note.label(text={route.ORIGINAL: ("NOR_ generated from BAo_ with this" if gen else
                                          "NOR_ = BNo_ (the engine is not used)"),
                         route.PROJECTION: "BNp_ from BAp_ with this; NOR_ = BNp_",
                         route.MIXED: ("NOR_ = BAo_ generated -> BNp_ (both with this)" if gen
                                       else "NOR_ = BNo_ -> BNp_ (BNp_ with this)")}[
                             route.get(obj)], icon='INFO')
    if obj is not None and obj.type == 'MESH':
        why = normal.problem(obj, context.scene, src)
        if why and not (s.bake_what == 'BOTH' and why == "Bake the albedo first"):
            col.label(text=why, icon='ERROR')


class MULTICAMPROJECT_PT_BakeSettings(bpy.types.Panel):
    """Bake settings, opened from the gear at the end of the Bake button: the normal map's
    options first, then the bake's"""
    bl_label = "Bake Settings"
    bl_idname = "MULTICAMPROJECT_PT_bake_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'           # a popover only - not drawn in the sidebar
    bl_ui_units_x = 17

    def draw(self, context):
        s = common.settings(context.scene)
        obj = context.active_object
        _draw_normal_settings(self.layout, context)
        self.layout.separator()
        self.layout.label(text="Bake", icon='RENDER_STILL')
        col = self.layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(s, "output_dir", text="Folder")
        if obj is not None and obj.type == 'MESH':
            col.prop(common.data(obj), "cage", text=f"Cage ({obj.name})")
        row = col.row(align=True)
        row.prop(s, "smooth_source", text="Smooth BN_ Source")
        sub = row.row(align=True)
        sub.active = s.smooth_source
        sub.prop(s, "smooth_iterations", text="")
        col.prop(s, "margin")
        col.label(text=f"= {common.margin_px(s)} px at {_res(s.resolution)}")
        col.prop(s, "device")
        col.prop(s, "anti_alias")
        col.separator()
        col.prop(s, "color_source")
        if s.color_source == 'SCAN_ATTRIBUTE':
            col.prop(s, "scan_color_name")
            if obj is not None and obj.type == 'MESH' \
                    and obj.data.color_attributes.get(s.scan_color_name) is None:
                col.label(text=f"No '{s.scan_color_name}' - Color comes from ALB", icon='INFO')
        col.prop(s, "png_compression")


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
    """Lite's detail method: High-pass"""
    bl_label = "Normal Map Method"
    bl_idname = "MULTICAMPROJECT_MT_normal_method"

    def draw(self, context):
        s = common.settings(context.scene)
        for key, _label, _desc in normal.available_sources():
            if key != 'AI':
                self.layout.prop_enum(s, "nor_source", key)


_classes = (MULTICAMPROJECT_MT_normal_engine, MULTICAMPROJECT_MT_normal_method,
            MULTICAMPROJECT_PT_BakeSettings)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Baking)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Baking)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
