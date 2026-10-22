"""The Bake | Load switch and 07's texture rows (drawn by baking/ui.py)."""
import bpy

from . import core


def is_on():
    from ... import module_manager
    return module_manager.is_loaded("tex_override")


def draw_switch(layout, obj, kind, label=True):
    """[ALB_] [Bake|Load]: Load greyed while 02.Overrides has no file for it."""
    d = obj.multicamproject_bake
    cur = core.mode(obj, kind)
    row = layout.row(align=True)
    row.enabled = not d.locked
    if label:
        tag = row.row(align=True)
        tag.ui_units_x = 2.2
        tag.label(text="ALB_" if kind == 'ALB' else "NOR_")
    for m, text in ((core.BAKE, "Bake"), (core.LOAD, "Load")):
        cell = row.row(align=True)
        if m == core.LOAD and cur != core.LOAD:
            cell.enabled = core.available(bpy.context.scene, obj, kind)
        op = cell.operator("multicamproject.tex_mode", text=text, depress=cur == m)
        op.kind, op.mode, op.object_name = kind, m, obj.name
    return row


def draw_row(layout, obj, kind, text, icon, img):
    """07: [label] [Bake|Load] [file] [refresh] [folder] [delete override] - and under it
    an override found (Bake) or missing (Load)."""
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
        bake_ui.draw_file(right, img)
    else:
        right.label(text="(none yet)")
    op = right.operator("multicamproject.tex_refresh", text="", icon='FILE_REFRESH')
    op.kind, op.object_name = kind, obj.name
    if core.available(scene, obj, kind):
        b = right.row(align=True)
        b.enabled = not obj.multicamproject_bake.locked
        op = b.operator("multicamproject.tex_delete_override", text="", icon='TRASH')
        op.kind, op.object_name = kind, obj.name
    why = core.problem(scene, obj, kind)
    if why:
        r = col.row()
        r.alert = True
        r.label(text=why, icon='ERROR')
    elif not core.is_load(obj, kind) and core.available(scene, obj, kind):
        r = col.row()
        r.active = False
        r.label(text=f"Override found in 02.Overrides - Load to use it", icon='INFO')
