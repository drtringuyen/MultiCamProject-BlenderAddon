import bpy

from ... import gate, module_manager
from . import tool, workflow as wf


def _res(n):
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n}"


def _faces_text(context, obj):
    """'38,180 / 763,610 faces': after the Decimate / the mesh now (the viewport's
    evaluation, already there)."""
    before = len(obj.data.polygons)
    dec = wf.decimate_modifier(obj)
    if dec is not None and not dec.show_viewport:     # no preview: the ratio's estimate
        return f"~{int(before * dec.ratio):,} / {before:,} faces"
    try:
        after = len(obj.evaluated_get(context.evaluated_depsgraph_get()).data.polygons)
    except Exception:
        return f"{before:,} faces"
    return f"{after:,} / {before:,} faces"


def _draw_amount_row(row, context, obj, dec, label, scale=1.0):
    """[after / before faces] [eye] [amount] [Apply] on one row."""
    row.scale_y = scale
    split = row.split(factor=0.42, align=True)
    split.label(text=_faces_text(context, obj).replace(" faces", ""))
    rest = split.row(align=True)
    rest.prop(dec, "show_viewport", text="", emboss=False)
    split = rest.split(factor=0.55, align=True)
    split.prop(dec, "ratio", text=label)
    split.operator("multicamproject.remesh_apply_decimate", text="Apply", icon='CHECKMARK')


def draw_decide(layout, context, obj, text="Decide the Decimate - 03 unlocks after Apply"):
    """The end of 2B Remesh (02 Mesh Workflow; a work window: the top of 03): the Decimate
    amount is decided and applied before anything else (the GN modifiers are muted
    meanwhile, Protect still works)."""
    dec = wf.decimate_modifier(obj)
    box = layout.box().column(align=True)
    box.alert = True
    box.label(text=text, icon='MOD_DECIM')
    box.alert = False
    _draw_amount_row(box.row(align=True), context, obj, dec, "", 1.4)
    hint = box.row()
    hint.active = False
    hint.label(text="Protect keeps parts · Ctrl+Z undoes 2B", icon='LOCKED')


def _draw_handmade(layout, obj):
    """Handmade: new UVs through Edit UV, then Rebake from uv_old, Finish (or Cancel)."""
    from ..baking import handmade
    col = layout.column(align=True)
    col.scale_y = 1.3
    row = col.row(align=True)
    if not handmade.has_uv_old(obj):
        row.operator("multicamproject.handmade_edit_uv", text="Rebake: Edit UV", icon='UV')
        return
    row.operator("multicamproject.handmade_rebake", text="Rebake from uv_old",
                 icon='FILE_REFRESH')
    row.operator("multicamproject.handmade_edit_uv", text="", icon='UV')
    row.operator("multicamproject.handmade_finish_uv", text="", icon='CHECKMARK')
    row.operator("multicamproject.handmade_cancel_uv", text="", icon='X')
    warn = layout.row()
    if obj.mode == 'EDIT':          # the mesh holds the UVs only after Edit Mode
        warn.active = False
        warn.label(text="Editing uv_normal - then Rebake", icon='UV')
    elif handmade.needs_rebake(obj):
        warn.alert = True
        warn.label(text="uv_normal changed - Rebake (or Cancel X)", icon='ERROR')
    else:
        warn.active = False
        warn.label(text="Textures match uv_normal - Finish, or edit more", icon='CHECKMARK')


def _shown(obj):
    """03 is there for a low poly (2B / 2C), an Only Project object (2D) and a handmade one."""
    if obj is None or obj.type != 'MESH':
        return False
    if wf.is_low_poly(obj):
        return True
    d = getattr(obj, "multicamproject_bake", None)
    if d is not None and d.handmade:
        return True
    from ... import steps
    return steps.workflow(obj) == 'PROJECT'


class MULTICAMPROJECT_PT_Remesh(bpy.types.Panel):
    """03. Mesh Editing: PolyCut / Set Faces / Snap on the low poly, then BAo_ (its colors
    from the scan). Worked on in the main file or in its work window, never both"""
    bl_label = "03. Mesh Editing"
    bl_idname = "MULTICAMPROJECT_PT_remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return _shown(context.active_object)

    def draw_header(self, context):
        from ... import steps
        done = 3 in steps.state(context.active_object, context.scene).done
        self.layout.label(icon='CHECKMARK' if done else 'SCULPTMODE_HLT')

    def draw(self, context):
        from ... import steps
        obj = context.active_object
        if steps.sent_out(obj, context.scene):      # 03 happens in the work window
            from ..linking import ui as link_ui
            link_ui.draw_main(self.layout, context, obj)
            hint = self.layout.row()
            hint.active = False
            hint.label(text="In its work window: edit and bake BAo_ there, Receive it here",
                       icon='WINDOW')
            return
        layout, _open = gate.step_box(self.layout, context, 3)
        if obj.multicamproject_bake.handmade:
            _draw_handmade(layout, obj)
            return
        pending = wf.decimate_pending(obj)
        d = obj.multicamproject_bake
        baking = module_manager.is_loaded("baking")
        s = context.scene.multicamproject_bake_settings if baking else None
        if module_manager.is_loaded("linking"):        # Send Out / Receive, Send Back
            from ..linking import core as link, ui as link_ui
            if link.is_work_window(context.scene):
                if pending:     # a work window decides the Decimate here, first
                    draw_decide(layout, context, obj,
                                "Decide the Decimate first - the rest unlocks after Apply")
                    layout = layout.column()
                    layout.enabled = False          # the rest after Apply
                link_ui.draw_work(layout, context)
            else:
                link_ui.draw_main(layout, context, obj)
        if pending:         # decided at the end of 02 (a work window: just above)
            return
        if not wf.is_low_poly(obj):     # 2D Only Project: no low poly to edit
            hint = layout.row()
            hint.active = False
            hint.label(text="Only Project: BAo_ comes from the scan's own textures", icon='INFO')
            draw_bao(layout, context, obj)
            return

        # the high poly this low poly bakes from (only BAo_ uses it)
        row = layout.row(align=True)
        row.prop(d, "bake_source", text="", icon='OUTLINER_OB_MESH')
        if s is not None:
            cage = row.row(align=True)
            cage.ui_units_x = 4.5
            cage.prop(d, "cage", text="Cage")

        retopo = wf.is_retopo(obj)
        rest = layout
        if retopo:
            # 2C: modelled by hand on the original - no PolyCut / Decimate
            col = layout.column(align=True)
            col.scale_y = 1.25
            col.operator("multicamproject.retopo_focus", text="Retopo (local view + Edit Mode)",
                         icon='EDITMODE_HLT')
        else:
            # the PolyCut tool and Set Faces
            col = rest.column(align=True)
            col.scale_y = 1.25
            # PolyCut is a Sculpt Mode tool; Edit Mode keeps Blender's own L (Select Linked)
            active = (context.workspace.tools.from_space_view3d_mode('SCULPT', create=False)
                      if context.mode == 'SCULPT' else None)
            on_tool = active is not None and active.idname == tool.tool_id()
            row = col.row(align=True)
            if not on_tool:
                row.operator("multicamproject.remesh_enter_tool",
                             text="PolyCut & Seams (Ctrl+Shift+K)",
                             icon='SCULPTMODE_HLT')
            else:
                row.label(text="PolyCut: Ctrl+Click cut · L pick", icon='CHECKMARK')
            # 02 Set Faces has no button: L with the tool, after each PolyCut, or Edit Mode's
            # right-click / Face menu. Here: every face set cleared, Clean Floating
            row.operator("multicamproject.remesh_set_faces", text="",
                         icon='FILE_REFRESH').action = 'CLEAR_FACE_SETS'
            clean = row.row(align=True)         # Edit Mode: keep the selected mesh, rest goes
            clean.enabled = context.mode == 'EDIT_MESH'
            clean.operator("multicamproject.remesh_clean_floating", text="", icon='TRASH')
            # the decimate brush: Blender's Density brush, which only works with Dyntopo
            # [Dyntopo detail] [Decimate Brush] [Dyntopo toggle]: the detail field follows the
            # Detailing method (Relative: pixels, Constant / Manual: resolution, Brush: percent)
            row = col.row(align=True)
            ts = context.tool_settings.sculpt
            brush = ts.brush if context.mode == 'SCULPT' else None
            on_density = brush is not None and getattr(brush, "sculpt_brush_type", "") == 'SIMPLIFY'
            dyn = obj.use_dynamic_topology_sculpting
            det = row.row(align=True)
            det.ui_units_x = 4.5
            prop = {'CONSTANT': "constant_detail_resolution", 'MANUAL': "constant_detail_resolution",
                    'BRUSH': "detail_percent"}.get(ts.detail_type_method, "detail_size")
            det.prop(ts, prop, text="")
            from ... import addon_icons
            row.operator("multicamproject.remesh_density_brush",
                         text="Decimate · Ctrl+Shift+F detail" if on_density and dyn
                         else "Decimate Brush (Ctrl+Shift+D)",
                         depress=on_density and dyn, **addon_icons.kw("decimate", 'MOD_DECIM'))
            if context.mode == 'SCULPT':
                tog = row.row(align=True)
                tog.alert = on_density and not dyn          # the brush does nothing without it
                tog.operator("sculpt.dynamic_topology_toggle", text="", icon='MESH_ICOSPHERE',
                             depress=dyn)

        # the Decimate (vg_Protect keeps parts) was applied in 02, before the unwrap
        box = layout.box().column(align=True)
        dec = wf.decimate_modifier(obj)
        if retopo and dec is None:
            box.label(text=f"Retopo: {len(obj.data.polygons):,} faces (no Decimate)",
                      icon='MESH_PLANE')
        elif dec is not None:
            _draw_amount_row(box.row(align=True), context, obj, dec, "")
        elif obj.get(wf.APPLIED_KEY):
            box.label(text=f"Decimate applied: {len(obj.data.polygons):,} faces",
                      icon='CHECKMARK')
        else:
            box.label(text=f"{len(obj.data.polygons):,} faces (no Decimate)", icon='MOD_DECIM')
        snap = wf.snap_modifier(obj)
        row = box.row(align=True)
        if snap is None:
            row.operator("multicamproject.remesh_snap", text="Snap vg_Snap to Source",
                         icon='SNAP_ON').on = True
        else:
            row.prop(snap, "show_viewport", text="", emboss=False)
            row.label(text="Snap to Source (vg_Snap)", icon='MOD_SHRINKWRAP')
            row.operator("multicamproject.remesh_snap", text="", icon='X').on = False

        draw_bao(layout, context, obj)


def draw_bao(layout, context, obj):
    """03's BAo_ box: Bake BAo_ (from the Bake Source, or 2D: the scan's own textures),
    why it can't run, the last bake and why it is outdated."""
    if not module_manager.is_loaded("baking"):
        return
    d = obj.multicamproject_bake
    dec = wf.decimate_modifier(obj)
    from ..baking import common, fingerprint, jobs, operators as bake_ops
    if jobs.busy():
        jobs.draw(layout)
        return
    box = layout.box().column(align=True)
    row = box.row(align=True)
    row.scale_y = 1.4
    why = bake_ops.source_poll_problem(obj)
    # the object's size: its own pick (05 / the EXPORT list), or the scene's in front of Export -
    # its picker here too, so it can change while 04-09 are still locked
    from ..baking import ui as bake_ui
    bake_ui.draw_object_size(row, obj)
    bake_ui.draw_lock(row, obj)         # 08's lock: unlock here to bake BAo_ again
    row.operator("multicamproject.bake_from_source",
                 text=f"03. Bake from Scan BAo_ ({_res(common.resolution(obj, context.scene))})",
                 icon='RENDER_STILL')
    if d.ba_image is not None and module_manager.is_loaded("image_editor"):
        from ..image_editor import sources
        if sources.draw_ready(obj):
            sources.draw_button(row, 'BAO', obj)       # 06: inpaint BAo_
    if why:
        sub = box.row()
        sub.alert = True
        sub.label(text=why, icon='ERROR')
        if not obj.data.uv_layers.get("uv_normal") and dec is None:
            from ..baking import operators as bops
            bops.draw_uv_fix(box, obj)
    else:
        collapsed = common.uv_collapsed_text(obj)       # baking would give a black BA_
        if collapsed:
            sub = box.row()
            sub.alert = True
            sub.label(text=collapsed, icon='ERROR')
        warn = bake_ops.source_uv_warning(obj)          # a warning: the bake still runs
        if warn:
            sub = box.row()
            sub.alert = True
            sub.label(text=warn + " (bakes anyway)", icon='ERROR')
    if d.ba_image is not None:
        info = box.row()
        info.active = False
        what = "BAo_" if d.bn_stale or d.bn_image is None else "BAo_ + BNo_"
        info.label(text=f"{what} {_res(d.ba_size)} (files, not final) · "
                        f"{d.last_ba_seconds:.1f} s", icon='IMAGE_RGB')
        why = fingerprint.ba_why(obj)
        if why:
            warn = box.row()
            warn.alert = True
            warn.label(text=f"{why[:1].upper()}{why[1:]} - bake BAo_ again", icon='ERROR')
        from ..baking import ui as bake_ui
        bake_ui.draw_fit_cage(box, obj)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Remesh)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Remesh)
