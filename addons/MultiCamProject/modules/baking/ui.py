import bpy

from ... import gate
from . import common, engine, fingerprint, gn_final, matsync, normal, operators, route


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
    """[Preview / Final] Processing | Baked: the file's quality (an icon), then the view as
    a two-state toggle - the current state stays highlighted."""
    from ... import quality
    row = layout.row(align=True)
    quality.draw_toggle(row, bpy.context)
    row.prop(data, prop, expand=True)


def draw_green(layout):
    """A green block: 05 Bake Albedo stands out (panels cannot colour a button)."""
    cell = layout.row(align=True)
    cell.ui_units_x = 0.7
    cell.prop(bpy.context.scene, "multicamproject_green", text="")


def draw_fit_cage(layout, obj):
    """[x% beyond the Cage (up to n m)] [Fit Cage n]: only when the last Bake from Source
    left a share of the surface outside the Cage (04, and the bake box of 05 / 06)."""
    d = common.data(obj)
    if not (d.ba_far_share > 0.02 and d.ba_fit_cage > d.cage):
        return
    row = layout.row(align=True)
    warn = row.row(align=True)
    warn.alert = True
    warn.label(text=f"{d.ba_far_share:.0%} beyond the Cage (up to {d.ba_far_max:.2f} m)",
               icon='ERROR')
    row.operator("multicamproject.fit_cage", text=f"Fit Cage {d.ba_fit_cage:.3f}")


def draw_bake_box(context, layout, obj, route_rows=False, bao=False):
    """One box, the same in 05 and 06: Processing | Final, Fit Cage (when needed), then the
    bake row ([Auto Resolution v] [Bake ...] [mixture] [gear]). `route_rows` (05): 06's
    route prompt under Processing | Final too, and a warning while From Original hides
    the projection - painting would show nothing. `bao` (04): 03's Rebake BAo_ and Inpaint
    BAo_ as icons in front of 05 - BAo_ shows where the mask leaves the projection out."""
    box = layout.box().column()
    draw_final_toggle(box, obj, "multicamproject_view")
    d = common.data(obj)
    if route_rows and not d.handmade:
        if d.route_prompt:
            route.draw_prompt(box, obj)
        elif d.route == route.ORIGINAL:
            route.draw_prompt(box, obj, "Bake Route From Original: the projection and its "
                                        "paint do not show")
    draw_fit_cage(box, obj)
    MULTICAMPROJECT_PT_Baking._draw_bake_button(box, common.settings(context.scene), obj, bao)


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
    """05 Bake Albedo: ALB_ (the Processing material baked onto the object) and NOR_, the
    final 8K textures in MAT_"""
    bl_label = "05. Bake Albedo"
    bl_idname = "MULTICAMPROJECT_PT_baking"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 4

    def draw_header(self, context):
        self.layout.label(icon='RENDER_STILL')

    def draw(self, context):
        layout, _open = gate.step_box(self.layout, context, 5)
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

        if d.handmade:      # no bake box: Processing | Final stays on top
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
            box.alert = False
            operators.draw_uv_fix(box, obj)
        elif d.fingerprint and fingerprint.is_outdated(obj):
            box = top.box()
            box.alert = True
            box.label(text="Bake is outdated - the projection changed", icon='ERROR')

        # [route v] what to bake (+ the normal map's options behind the gear), what was
        # baked last
        box = top.box().column()
        box.prop(d, "route", text="Route")
        self._draw_route(box, obj)
        info = box.column(align=True)
        if d.alb_size:
            info.label(text=f"ALB {_res(d.alb_size)}  ·  {d.last_bake_seconds:.1f} s",
                       icon='IMAGE_RGB')
        res = common.resolution(obj, context.scene)
        if d.tex_size == 'AUTO':
            info.label(text=f"Auto Resolution = {_res(res)} (the scene's, set in front of Export)",
                       icon='TEXTURE')
        if d.alb_size and d.alb_size != res:
            info.label(text=f"Baked at {_res(d.alb_size)} - bake again for {_res(res)}",
                       icon='INFO')
        draw_bake_box(context, layout, obj)

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
            parts = "BAo_ / BNo_" if d.original_normal == 'BAKED' else "BAo_"
            if d.ba_image is None or not common.file_ok(d.ba_image):
                col.label(text=f"No {parts} yet: bakes from {d.bake_source.name} first",
                          icon='INFO')
            elif fingerprint.ba_outdated(obj):
                col.label(text=f"BAo_ outdated ({fingerprint.ba_why(obj)}): bakes from the "
                               "source again first", icon='INFO')
            elif engine.needs_source_bake(obj):
                col.label(text="No current BNo_: bakes from the source again first", icon='INFO')
            else:
                col.label(text=f"{parts} {_res(d.ba_size)} from {d.bake_source.name}",
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
        from ... import steps       # every slot material's nodes per redraw: memoized
        probs = (steps.memo(("matsync", obj.name), lambda: matsync.problems(obj))
                 if matsync.in_scope(obj) else [])
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
            box.label(text="New UVs: Rebake: Edit UV (03)")
            return
        if obj.mode == 'EDIT':
            box.label(text="Editing uv_normal - Bake rebakes from uv_old", icon='UV')
        elif handmade.needs_rebake(obj):
            box.alert = True
            box.label(text="uv_normal changed - rebake ALB_ + NOR_", icon='ERROR')
        else:
            box.label(text="ALB_ + NOR_ match uv_normal - Finish in 03",
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
    def _draw_bake_button(layout, s, obj, bao=False):
        """[A v] (the active object's own size), the green "05. Bake Albedo" (ALB_ only:
        NOR_ is 07's), the bake settings' gear at its end. `bao`: [Rebake BAo_] [Inpaint
        BAo_] icons in front of 05 (04's box: 03 -> 04 -> 05 in one row)."""
        row = layout.row(align=True)
        row.scale_y = 1.5
        draw_object_size(row, obj)
        row.separator(factor=0.5)
        if bao:
            _draw_bao_icons(row, obj)
            row.separator(factor=0.5)
        draw_green(row)
        row.operator("multicamproject.bake_albedo", text="05. Bake Albedo",
                     icon='RENDER_STILL')
        draw_green(row)
        d = common.data(obj)
        from ... import module_manager
        if d.alb_image is not None and module_manager.is_loaded("image_editor"):
            from ..image_editor import sources
            if sources.draw_ready(obj):
                sources.draw_button(row, 'ALB', obj)   # 06: inpaint ALB_
        if (hasattr(bpy.types, "MULTICAMPROJECT_OT_bake_view_mix")      # camera_project on
                and getattr(obj, "multicamproject_cam", None) is not None
                and obj.multicamproject_cam.is_setup
                and obj.multicamproject_cam.paint_mode != 'TEXTURE'):     # vertex paint only
            row.operator("multicamproject.bake_view_mix", text="", icon='GROUP_VCOL')
        row.popover(panel="MULTICAMPROJECT_PT_bake_settings", text="", icon='PREFERENCES')


def _draw_bao_icons(row, obj):
    """03's Bake from Scan BAo_ (small: 05 is the row's main button) and BAo_'s Inpaint."""
    sub = row.row(align=True)
    sub.ui_units_x = 4.5
    sub.operator("multicamproject.bake_from_source", text="03. Rebake", icon='FILE_REFRESH')
    from ... import module_manager
    if common.data(obj).ba_image is not None and module_manager.is_loaded("image_editor"):
        from ..image_editor import sources
        if sources.draw_ready(obj):
            sources.draw_button(row, 'BAO', obj)


def draw_normal_settings(layout, context):
    """07's gear: High-pass of ALB_ (default) or Bake from Scan, the engine and its options."""
    s = common.settings(context.scene)
    obj = context.active_object
    d = common.data(obj) if obj is not None and obj.type == 'MESH' else None
    col = layout.column()
    col.label(text="Normal Map", icon='NORMALS_FACE')
    if d is not None and not d.handmade:
        col.row().prop(d, "original_normal", expand=True)
        if d.original_normal == 'BAKED':
            note = col.row()
            note.active = False
            note.label(text="BNo_ where the mask is 0, High-pass where it is 1"
                       if d.bake_source is not None else
                       "No Bake Source (2D): High-pass only", icon='INFO')
    ai_on = s.nor_source == 'AI'
    split = col.split(factor=0.34, align=True)
    split.label(text="Engine")
    row = split.row(align=True)
    row.menu("MULTICAMPROJECT_MT_normal_engine", text="AI" if ai_on else "Lite",
             icon='LIGHT_SUN' if ai_on else 'IMAGE_RGB')
    if not ai_on:
        row.menu("MULTICAMPROJECT_MT_normal_method", text=normal.LABELS.get(s.nor_source,
                                                                             s.nor_source))
    if s.nor_source == 'HIGHPASS':
        opts = col.column(align=True)
        row = opts.row(align=True)
        row.prop(s, "nor_strength", text="Strength")
        row.prop(s, "nor_radius", text="Radius")
        opts.row(align=True).prop(s, "nor_invert", text="Invert", toggle=True)
    if d is not None and d.original_normal == 'BAKED':
        row = col.row(align=True)
        row.prop(s, "smooth_source", text="Smooth the Scan")
        sub = row.row(align=True)
        sub.active = s.smooth_source
        sub.prop(s, "smooth_iterations", text="")


class MULTICAMPROJECT_PT_NormalSettings(bpy.types.Panel):
    """07's gear: how NOR_ is made"""
    bl_label = "Normal Settings"
    bl_idname = "MULTICAMPROJECT_PT_normal_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 16

    def draw(self, context):
        draw_normal_settings(self.layout, context)


class MULTICAMPROJECT_PT_GenerateNormal(bpy.types.Panel):
    """07. Generate Normal: NOR_ from ALB_ (its inpaint when there is one) - High-pass by
    default, or (gear) Bake from Scan: the scan's surface where the projection mask is 0.
    Unlocks with 06 once ALB_ is baked; 06 is optional"""
    bl_label = "07. Generate Normal"
    bl_idname = "MULTICAMPROJECT_PT_generate_normal"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 6

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def draw_header(self, context):
        from ... import steps
        done = 7 in steps.state(context.active_object, context.scene).done
        self.layout.label(icon='CHECKMARK' if done else 'NORMALS_FACE')

    def draw(self, context):
        layout, _open = gate.step_box(self.layout, context, 7)
        from ... import steps
        from . import jobs
        if jobs.busy():
            jobs.draw(layout)
            return
        obj = context.active_object
        d = common.data(obj)
        s = common.settings(context.scene)
        if d.handmade:
            layout.label(text="Handmade: NOR_ is made by hand", icon='INFO')
            return
        info = layout.column(align=True)
        alb = common.alb_source(obj)
        if alb is not None:
            sub = info.row()
            sub.active = False
            sub.label(text=f"From {alb.name}", icon='IMAGE_RGB')
        if not steps.alb_fresh(obj):
            note = info.row()
            note.alert = True
            note.label(text="ALB_ outdated or missing - 07 bakes it first (05)", icon='INFO')
        if d.nor_size and d.nor_image is not None:
            label = normal.LABELS.get(d.nor_source_used, d.nor_source_used)
            info.label(text=f"NOR {_res(d.nor_size)}  ·  {label}  ·  {d.last_nor_seconds:.1f} s",
                       icon='NORMALS_FACE')
            if normal.nor_outdated(obj, context.scene):
                warn = info.row()
                warn.alert = True
                warn.label(text="ALB_ or the settings changed - generate again", icon='ERROR')
        else:
            info.label(text="No NOR_ yet", icon='INFO')
        mode = ("Bake from Scan" if normal.from_scan(obj)
                else normal.LABELS.get(normal.usable(s.nor_source), "High-pass"))
        row = layout.row(align=True)
        row.scale_y = 1.5
        draw_object_size(row, obj)
        row.separator(factor=0.5)
        row.operator("multicamproject.bake_normal", text=f"07. Generate Normal ({mode})",
                     icon='NORMALS_FACE')
        row.popover(panel="MULTICAMPROJECT_PT_normal_settings", text="", icon='PREFERENCES')
        why = normal.problem(obj, context.scene, s.nor_source)
        if why:
            layout.label(text=why, icon='ERROR')


class MULTICAMPROJECT_PT_LockFinish(bpy.types.Panel):
    """08. Lock Finish: by hand only. A Locked object keeps its ALB_ / NOR_ (no clean-up
    removes them), every step is locked for it, and its files in the Export folder are
    checked - a missing one turns it red: Unlock & Rebake"""
    bl_label = "08. Lock Finish"
    bl_idname = "MULTICAMPROJECT_PT_lock_finish"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 7

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def draw_header(self, context):
        locked = common.data(context.active_object).locked
        self.layout.label(icon='LOCKED' if locked else 'UNLOCKED')

    def draw(self, context):
        from ... import steps
        obj = context.active_object
        d = common.data(obj)
        if not d.locked:
            layout, _open = gate.step_box(self.layout, context, 8)
            row = layout.row()
            row.scale_y = 1.5
            row.operator("multicamproject.lock_finish", text="08. Lock Finish",
                         icon='LOCKED').action = 'LOCK'
            hint = layout.row()
            hint.active = False
            hint.label(text="Copies ALB_ / NOR_ into Export, then nothing changes the object",
                       icon='INFO')
            return
        layout = self.layout
        missing = steps.locked_missing(obj, context.scene)
        if missing:
            box = layout.box().column(align=True)
            box.alert = True
            box.label(text=f"Missing: {', '.join(missing)}", icon='ERROR')
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator("multicamproject.lock_finish", text="Unlock & Rebake",
                         icon='FILE_REFRESH').action = 'REBAKE'
        else:
            layout.label(text="Locked Finish - ALB_ / NOR_ in the Export folder",
                         icon='CHECKMARK')
        layout.operator("multicamproject.lock_finish", text="Unlock",
                        icon='UNLOCKED').action = 'UNLOCK'


def _draw_normal_settings(layout, context):
    """(Before 07) the normal map's options by route - kept for reference."""
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
            MULTICAMPROJECT_PT_BakeSettings, MULTICAMPROJECT_PT_NormalSettings)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Baking)
    bpy.utils.register_class(MULTICAMPROJECT_PT_GenerateNormal)
    bpy.utils.register_class(MULTICAMPROJECT_PT_LockFinish)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_LockFinish)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_GenerateNormal)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Baking)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
