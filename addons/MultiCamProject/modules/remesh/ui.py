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


def _draw_decide(layout, context, obj):
    """0C's first step: the Decimate amount is decided and applied before anything else (the
    GN modifiers are muted meanwhile, 02 Protect still works)."""
    dec = wf.decimate_modifier(obj)
    box = layout.box().column(align=True)
    box.alert = True
    box.label(text="03. Decide the Decimate - the rest unlocks after Apply", icon='MOD_DECIM')
    box.alert = False
    _draw_amount_row(box.row(align=True), context, obj, dec, "", 1.4)
    hint = box.row()
    hint.active = False
    hint.label(text="02 Protect keeps parts · Ctrl+Z undoes 0C", icon='LOCKED')


class MULTICAMPROJECT_PT_Remesh(bpy.types.Panel):
    """Cutting & Modelling: shown for a low poly (0C Remesh, or a picked high poly)"""
    bl_label = "Cutting & Modelling"
    bl_idname = "MULTICAMPROJECT_PT_remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and wf.is_low_poly(obj)

    def draw_header(self, context):
        self.layout.label(icon='MOD_REMESH')

    def draw(self, context):
        self.layout.enabled = gate.ready(context.scene)     # greyed out until Setup File IO
        layout = self.layout
        obj = context.active_object
        pending = wf.decimate_pending(obj)
        d = obj.multicamproject_bake
        baking = module_manager.is_loaded("baking")
        s = context.scene.multicamproject_bake_settings if baking else None
        if module_manager.is_loaded("linking"):        # Send Out / Receive, Send Back
            from ..linking import core as link, ui as link_ui
            if link.is_work_window(context.scene):
                link_ui.draw_work(layout, context)
            else:
                link_ui.draw_main(layout, context, obj)
        if pending:
            _draw_decide(layout, context, obj)

        # the high poly this low poly bakes from (only 04 uses it)
        row = layout.row(align=True)
        row.prop(d, "bake_source", text="", icon='OUTLINER_OB_MESH')
        if s is not None:
            cage = row.row(align=True)
            cage.ui_units_x = 4.5
            cage.prop(d, "cage", text="Cage")

        retopo = wf.is_retopo(obj)
        rest = layout                       # 01 / Decimate Brush wait for the decision
        if pending:
            rest = layout.column()
            rest.enabled = False
        if retopo:
            # 0D: modelled by hand on the original - no PolyCut / Decimate
            col = layout.column(align=True)
            col.scale_y = 1.25
            col.operator("multicamproject.retopo_focus", text="Retopo (local view + Edit Mode)",
                         icon='EDITMODE_HLT')
        else:
            # 01 / 02: the PolyCut tool and Set Faces
            col = rest.column(align=True)
            col.scale_y = 1.25
            in_mesh_mode = context.mode in {'SCULPT', 'EDIT_MESH'}
            # PolyCut is a Sculpt Mode tool; Edit Mode keeps Blender's own L (Select Linked)
            active = (context.workspace.tools.from_space_view3d_mode('SCULPT', create=False)
                      if context.mode == 'SCULPT' else None)
            on_tool = active is not None and active.idname == tool.tool_id()
            row = col.row(align=True)
            if not on_tool:
                row.operator("multicamproject.remesh_enter_tool",
                             text="01. Poly Cut & Seams (Sculpt: Ctrl+Click, L)", icon='SCULPTMODE_HLT')
            else:
                row.label(text="01. PolyCut: Ctrl+Click cut · L pick", icon='CHECKMARK')
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
            row.operator("multicamproject.remesh_density_brush",
                         text="Decimate Brush" + ("  ·  active" if on_density and dyn else ""),
                         icon='MOD_DECIM', depress=on_density and dyn)
            if context.mode == 'SCULPT':
                tog = row.row(align=True)
                tog.alert = on_density and not dyn          # the brush does nothing without it
                tog.operator("sculpt.dynamic_topology_toggle", text="", icon='MESH_ICOSPHERE',
                             depress=dyn)
            col = layout.column(align=True)     # 02 (Protect) stays usable while deciding
            col.scale_y = 1.25
            row = col.row(align=True)
            sub = row.row(align=True)
            sub.enabled = in_mesh_mode
            sub.operator("multicamproject.remesh_set_faces",
                         text="02. Select & Set (L)" if context.mode != 'EDIT_MESH'
                         else "02. Set Selected Faces", icon='FACESEL')
            row.operator("multicamproject.remesh_set_faces", text="",
                         icon='FACE_MAPS').action = 'CLEAR_FACE_SETS'

        if pending:     # everything after 02 waits for the decision
            layout = layout.column()
            layout.enabled = False

        # 03: one Decimate (vg_Protect keeps parts), applied before the unwrap
        box = layout.box().column(align=True)
        dec = wf.decimate_modifier(obj)
        if pending:
            box.label(text="03. Decimate: decided above", icon='MOD_DECIM')
        elif retopo and dec is None:
            box.label(text=f"03. Retopo: {len(obj.data.polygons):,} faces (no Decimate)",
                      icon='MESH_PLANE')
        elif dec is not None:
            _draw_amount_row(box.row(align=True), context, obj, dec, "03.")
        elif obj.get(wf.APPLIED_KEY):
            box.label(text=f"03. Decimate applied: {len(obj.data.polygons):,} faces",
                      icon='CHECKMARK')
        else:
            box.label(text="03. No Decimate modifier", icon='MOD_DECIM')
        snap = wf.snap_modifier(obj)
        row = box.row(align=True)
        if snap is None:
            row.operator("multicamproject.remesh_snap", text="Snap vg_Snap to Source",
                         icon='SNAP_ON').on = True
        else:
            row.prop(snap, "show_viewport", text="", emboss=False)
            row.label(text="Snap to Source (vg_Snap)", icon='MOD_SHRINKWRAP')
            row.operator("multicamproject.remesh_snap", text="", icon='X').on = False

        # 04: BAo_ / BNo_ from the Bake Source (the baking module's settings)
        if not baking:
            return
        from ..baking import common, fingerprint, jobs, operators as bake_ops
        if jobs.busy():
            jobs.draw(layout)
            return
        box = layout.box().column(align=True)
        row = box.row(align=True)
        row.scale_y = 1.4
        why = bake_ops.source_poll_problem(obj)
        # the object's size: its own pick (06 / the EXPORT list), or the scene's in front of Export
        row.operator("multicamproject.bake_from_source",
                     text=f"04. Bake from Source ({_res(common.resolution(obj, context.scene))})",
                     icon='RENDER_STILL')
        if why:
            sub = box.row()
            sub.alert = True
            sub.label(text=why, icon='ERROR')
            if (not obj.data.uv_layers.get("uv_normal") and dec is None
                    and hasattr(bpy.types, "MULTICAMPROJECT_OT_export_make_uv")):
                box.operator("multicamproject.export_make_uv", text="Make uv_normal (Smart UV)",
                             icon='UV').scope = 'SELECTED'
        else:
            collapsed = common.uv_collapsed_text(obj)       # baking would give a black BA_
            if collapsed:
                sub = box.row()
                sub.alert = True
                sub.label(text=collapsed, icon='ERROR')
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
                warn.label(text=f"{why[:1].upper()}{why[1:]} - bake from source again", icon='ERROR')
            from ..baking import ui as bake_ui
            bake_ui.draw_fit_cage(box, obj)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Remesh)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Remesh)
