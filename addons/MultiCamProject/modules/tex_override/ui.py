"""The Bake | Load switch and 07's texture rows (drawn by baking/ui.py)."""
import bpy

from . import core


def is_on():
    from ... import module_manager
    return module_manager.is_loaded("tex_override")


def draw_switch(layout, obj, kind):
    """[ALB_Bake v]: ALB_Bake / ALB_Load (NOR_) - "ALB_Load (not found)" without its file
    in 02.Overrides (picking it stays on Bake)."""
    d = obj.multicamproject_bake
    row = layout.row(align=True)
    row.enabled = not d.locked
    row.prop(d, "alb_pick" if kind == 'ALB' else "nor_pick", text="")
    return row


def _draw_edit(row, obj, kind, img):
    """ALB_: [Inpaint in place] [Undo] - Save writes the file itself (Bake or Load), a copy
    in _previous first. NOR_: the same room left empty, so both switches line up."""
    from ... import module_manager
    from ..baking import alb_history
    if not module_manager.is_loaded("image_editor"):
        return
    sub = row.row(align=True)
    if kind != 'ALB' or img is None:
        for _i in range(3):
            sub.label(text="", icon='BLANK1')
        row.separator(factor=0.5)
        return
    sub.enabled = not obj.multicamproject_bake.locked
    op = sub.operator("multicamproject.inpaint_load", text="", icon='ANCHOR_CENTER')
    op.kind, op.owner, op.direct = 'ALB', obj.name, True
    u = sub.row(align=True)
    n = alb_history.count(obj)
    u.enabled = bool(n)
    u.operator("multicamproject.alb_undo", text="", icon='LOOP_BACK').object_name = obj.name
    from ..baking import common
    ip = obj.multicamproject_bake.alb_inpaint
    m = sub.row(align=True)
    m.enabled = ip is not None and ip != img and common.file_ok(ip)  # 06 saved an _inpaint
    m.operator("multicamproject.alb_merge_inpaint", text="",
               icon='IMPORT').object_name = obj.name
    row.separator(factor=0.5)


def draw_row(layout, obj, kind, text, icon, img):
    """07: [label] [Bake|Load] [file] [export to Overrides] [folder] [refresh] [delete
    override] - and under it an override found (Bake) or missing (Load)."""
    from ..baking import ui as bake_ui
    scene = bpy.context.scene
    col = layout.column(align=True)
    split = col.split(factor=0.3, align=True)
    if img is not None:         # a click shows it in the Image / UV Editor
        left = split.row(align=True)
        left.alignment = 'LEFT'         # reads like the label it was
        left.operator("multicamproject.show_image", text=text, icon=icon,
                      emboss=False).image_name = img.name
    else:
        split.label(text=text, icon=icon)
    right = split.row(align=True)
    _draw_edit(right, obj, kind, img)
    sw = draw_switch(right, obj, kind)
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
