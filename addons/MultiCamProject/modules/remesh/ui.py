import bpy

from ... import module_manager
from . import tool, workflow as wf


def _res(n):
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n}"


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
        layout = self.layout
        obj = context.active_object
        d = obj.multicamproject_bake
        baking = module_manager.is_loaded("baking")
        s = context.scene.multicamproject_bake_settings if baking else None

        # the high poly this low poly bakes from (only 04 uses it)
        row = layout.row(align=True)
        row.prop(d, "bake_source", text="", icon='OUTLINER_OB_MESH')
        if s is not None:
            cage = row.row(align=True)
            cage.ui_units_x = 4.5
            cage.prop(s, "cage_extrusion", text="Cage")

        # 01 / 02: the PolyCut tool and Set Faces
        col = layout.column(align=True)
        col.scale_y = 1.25
        in_mesh_mode = context.mode in {'SCULPT', 'EDIT_MESH'}
        active = (context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
                  if in_mesh_mode else None)
        on_tool = active is not None and active.idname == tool.tool_id(context.mode)
        row = col.row(align=True)
        if not in_mesh_mode or not on_tool:
            row.operator("multicamproject.remesh_enter_tool",
                         text="01. Poly Cut & Seams (Ctrl+Click, L)",
                         icon='SCULPTMODE_HLT').mode = 'EDIT' if context.mode == 'EDIT_MESH' else 'SCULPT'
            if not in_mesh_mode:
                row.operator("multicamproject.remesh_enter_tool", text="",
                             icon='EDITMODE_HLT').mode = 'EDIT'
        else:
            row.label(text="01. PolyCut: Ctrl+Click cut · L pick", icon='CHECKMARK')
        row = col.row(align=True)
        row.enabled = in_mesh_mode
        row.operator("multicamproject.remesh_set_faces", text="02. Select & Set (L)",
                     icon='FACESEL')
        tip = layout.column(align=True)
        tip.active = False
        tip.label(text="Local clean-up: Density brush (Sculpt ▸ Other, Dyntopo on)", icon='INFO')

        # 03: one Decimate (vg_Protect keeps parts), applied before the unwrap
        box = layout.box().column(align=True)
        dec = wf.decimate_modifier(obj)
        if dec is not None:
            row = box.row(align=True)
            row.prop(dec, "show_viewport", text="", emboss=False)
            row.prop(dec, "ratio", text="03. Decimate amount")
            row.operator("multicamproject.remesh_apply_decimate", text="Apply", icon='CHECKMARK')
            box.label(text=f"{len(obj.data.polygons):,} faces · vg_Protect stays · "
                           "apply before the unwrap", icon='INFO')
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

        # 04: BA_ / BN_ from the Bake Source (the baking module's settings)
        if not baking:
            return
        from ..baking import fingerprint, operators as bake_ops
        box = layout.box().column(align=True)
        row = box.row(align=True)
        row.scale_y = 1.4
        why = bake_ops.source_poll_problem(obj)
        # the size comes from the 1K-8K toggles in 06 (one resolution for every texture)
        row.operator("multicamproject.bake_from_source",
                     text=f"04. Bake from Source ({_res(s.resolution)})", icon='RENDER_STILL')
        if why:
            sub = box.row()
            sub.alert = True
            sub.label(text=why, icon='ERROR')
            if (not obj.data.uv_layers.get("uv_normal") and dec is None
                    and hasattr(bpy.types, "MULTICAMPROJECT_OT_export_make_uv")):
                box.operator("multicamproject.export_make_uv", text="Make uv_normal (Smart UV)",
                             icon='UV').scope = 'SELECTED'
        if d.ba_image is not None:
            info = box.row()
            info.active = False
            info.label(text=f"BA_ + BN_ {_res(d.ba_size)} (packed, not final) · "
                            f"{d.last_ba_seconds:.1f} s", icon='IMAGE_RGB')
            if fingerprint.ba_outdated(obj):
                warn = box.row()
                warn.alert = True
                warn.label(text="Mesh or uv_normal changed - bake from source again", icon='ERROR')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Remesh)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Remesh)
