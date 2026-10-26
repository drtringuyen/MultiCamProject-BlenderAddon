"""The Bake | Load switch and 07's texture rows (drawn by baking/ui.py)."""
import bpy

from . import core


def is_on():
    from ... import module_manager
    return module_manager.is_loaded("tex_override")


def draw_switch(layout, obj, kind, label=True):
    """[ALB_] [Bake v]: Bake / Load - "Load (not found)" without its file in 02.Overrides
    (picking it stays on Bake)."""
    d = obj.multicamproject_bake
    row = layout.row(align=True)
    row.enabled = not d.locked
    if label:
        tag = row.row(align=True)
        tag.ui_units_x = 2.2
        tag.label(text="ALB_" if kind == 'ALB' else "NOR_")
    row.prop(d, "alb_pick" if kind == 'ALB' else "nor_pick", text="")
    return row


def draw_row(layout, obj, kind, text, icon, img):
    """07: [label] [Bake|Load] [file] [export to Overrides] [folder] [refresh] [delete
    override] - and under it an override found (Bake) or missing (Load)."""
    from ..baking import ui as bake_ui
    scene = bpy.context.scene
    col = layout.column(align=True)
    split = col.split(factor=0.3, align=True)
    split.label(text=text, icon=icon)
    right = split.row(align=True)
    sw = draw_switch(right, obj, kind, label=False)
    sw.ui_units_x = 5           # the file path gets the rest
    right.separator(factor=0.5)
    if img is not None:
        def export(row):
            op = row.operator("multicamproject.tex_export_override", text="", icon='EXPORT')
            op.kind, op.object_name = kind, obj.name
        bake_ui.draw_file(right, img, extra=export)
    else:
        right.label(text="(none yet)")
    op = right.operator("multicamproject.tex_refresh", text="", icon='FILE_REFRESH')
    op.kind, op.object_name = kind, obj.name
    if core.available(scene, obj, kind):
        b = right.row(align=True)
        b.enabled = not obj.multicamproject_bake.locked
        op = b.operator("multicamproject.tex_delete_override", text="", icon='TRASH')
        op.kind, op.object_name = kind, obj.name
    if core.is_load(obj, kind) and not core.available(scene, obj, kind):
        r = col.row()
        r.alert = True
        r.label(text="Override not found - the next bake goes back to Bake", icon='ERROR')
    elif not core.is_load(obj, kind) and core.available(scene, obj, kind):
        r = col.row()
        r.active = False
        r.label(text=f"Override found in 02.Overrides - Load to use it", icon='INFO')
