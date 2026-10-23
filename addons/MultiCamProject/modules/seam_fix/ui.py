import json

import bpy

from . import geometry as geo
from . import operators, shader


def draw(layout, context):
    """Fix Seam / Seam-leveling / Bake, the view toggle, Reset / Apply - same in the 3D
    view and the UV editor."""
    obj = context.active_object
    why = operators._why_not(obj)
    if why:
        col = layout.column()
        col.alert = True
        col.label(text=why, icon='ERROR')
        if obj is None or obj.type != 'MESH' or not shader.installed(obj):
            return
    st = operators.settings(context)
    state = obj.get("seamfix_state", "")
    report = json.loads(obj.get("seamfix_report", "{}"))

    col = layout.column(align=True)
    col.scale_y = 1.25
    row = col.row(align=True)
    row.operator("multicamproject.seamfix_fix", icon='UV_SYNC_SELECT',
                 depress=bool(obj.get("seamfix_fixed")))
    sub = row.row(align=True)
    sub.scale_x = 0.8
    sub.prop(st, "blend_px", text="")
    row.prop(st, "shrink", text="", icon='FULLSCREEN_EXIT')
    row = col.row(align=True)
    row.operator("multicamproject.seamfix_level", icon='MOD_SMOOTH',
                 depress=bool(obj.get("seamfix_leveled")))
    sub = row.row(align=True)
    sub.scale_x = 0.8
    sub.prop(st, "level_px", text="")
    sub.prop(st, "max_jump", text="")
    row = col.row(align=True)
    row.operator("multicamproject.seamfix_bake", icon='RENDER_STILL',
                 depress=state == "baked")
    sub = row.row(align=True)
    sub.scale_x = 0.8
    sub.prop(st, "bake_margin", text="")

    if shader.installed(obj):
        mode = shader.get_mode(obj)
        row = layout.row(align=True)
        for k, name in enumerate(shader.MODES):
            cell = row.row(align=True)
            cell.enabled = k != 3 or state == "baked"
            cell.operator("multicamproject.seamfix_mode", text=name,
                          depress=mode == k).mode = k
        if obj.get("seamfix_stale"):
            warn = layout.row()
            warn.alert = True
            warn.label(text="Changed after the bake - bake again", icon='ERROR')

    fix, level = report.get("fix"), report.get("level")
    if fix or level:
        info = layout.column(align=True)
        info.active = False
        if fix:
            info.label(text=f"{fix['seam_edges_used']}/{fix['seam_edges']} seam edges · "
                            f"{fix.get('shrunk', 0)} islands shrunk "
                            f"(min {fix.get('min_scale', 1):.2f})")
            info.label(text=f"Reads in margin {fix.get('reads_in_margin', 0):.0%} · "
                            f"faded out {fix.get('reads_suppressed', 0):.0%}")
        if level:
            info.label(text=f"Leveled {level['seam_sides']} seam edges · jump median "
                            f"{level['jump_median_255']:.0f}/255")
            if level.get("skipped_colour_edges"):
                info.label(text=f"{level['skipped_colour_edges']} edges on a colour edge "
                                "left alone")
        left = (fix or {}).get("left_out_islands") or (level or {}).get("left_out_islands", 0)
        if left:
            row = layout.row(align=True)
            row.alert = True
            row.label(text=f"{left} overlapping island(s) left out", icon='ERROR')
            row.operator("multicamproject.seamfix_select_overlaps", text="", icon='RESTRICT_SELECT_OFF')

    row = layout.row(align=True)
    row.operator("multicamproject.seamfix_reset", icon='LOOP_BACK')
    sub = row.row(align=True)
    sub.alert = True
    sub.operator("multicamproject.seamfix_apply", text="Apply to Files", icon='FILE_TICK')


class MULTICAMPROJECT_PT_SeamFix(bpy.types.Panel):
    """Hide UV seams: Fix Seam (the source's continuation across the seam), Seam-leveling
    (both sides meet at their average colour), Bake, Apply"""
    bl_label = "Seam Fix"
    bl_idname = "MULTICAMPROJECT_PT_seam_fix"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 11

    def draw_header(self, context):
        self.layout.label(icon='UV_SYNC_SELECT')

    def draw(self, context):
        draw(self.layout, context)


class MULTICAMPROJECT_PT_SeamFixUV(bpy.types.Panel):
    bl_label = "Seam Fix"
    bl_idname = "MULTICAMPROJECT_PT_seam_fix_uv"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"

    @classmethod
    def poll(cls, context):
        from ...panels import object_ready
        return object_ready(context)

    def draw_header(self, context):
        self.layout.label(icon='UV_SYNC_SELECT')

    def draw(self, context):
        draw(self.layout, context)


classes = (MULTICAMPROJECT_PT_SeamFix, MULTICAMPROJECT_PT_SeamFixUV)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
