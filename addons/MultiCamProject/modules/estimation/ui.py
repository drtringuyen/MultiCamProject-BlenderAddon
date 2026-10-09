import bpy

from . import core


def _line(col, block=None):
    """(name cell, number cells): the name gets a third of the width, the 4 numbers share
    the rest equally. `block`: the row whose colour block goes in front (a gap otherwise,
    so the columns stay in line)."""
    row = col.row(align=True)
    cell = row.row(align=True)
    cell.ui_units_x = 0.7
    if block is not None:
        cell.prop(block, "color", text="")
    else:
        cell.label(text="")
    split = row.split(factor=0.34, align=True)
    return split.row(align=True), split.row(align=True)


class MULTICAMPROJECT_PT_Estimation(bpy.types.Panel):
    """Polycount Estimation (part of Setup): the triangle budget of the OBJECTS collection,
    split by area. In a work window: the object's budget, which its Decimate was set to"""
    bl_label = "Polycount Estimation"
    bl_idname = "MULTICAMPROJECT_PT_estimation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_setup"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='MOD_DECIM')

    def draw(self, context):
        layout = self.layout
        if context.scene.get(core.TARGET_KEY) is not None or _work_window(context.scene):
            _draw_window(layout, context)
            return
        s = context.scene.multicamproject_estimation
        row = layout.row(align=True)
        row.prop(s, "budget")
        row.prop(s, "minimum")
        row = layout.row()
        row.scale_y = 1.3
        coll = core.objects_collection(context)
        row.enabled = coll is not None
        row.operator("multicamproject.estimation_calculate",
                     text=f"Calculate {coll.name}" if coll else "No OBJECTS collection",
                     icon='FILE_REFRESH')
        if not s.rows:
            return
        if s.calculated_budget != s.budget:
            layout.label(text="Budget changed: Calculate again", icon='ERROR')

        total_area = sum(r.area for r in s.rows)
        from ... import roles
        export = roles.find(context.scene, 'EXPORT')
        states = {r.name: core.row_state(context, r, export) for r in s.rows}
        core.STATE.clear()
        core.STATE.update({n: st for n, (st, _now) in states.items()})
        names = core.display_names(context.scene, [r.name for r in s.rows])
        box = layout.box()
        col = box.column(align=True)
        name, nums = _line(col)
        name.label(text="Object")
        for text in ("Area", "Budget", "Now", "Remove"):
            nums.label(text=text)
        for r in s.rows:
            state, now = states[r.name]
            name, nums = _line(col, r)
            obj = context.scene.objects.get(r.name)
            name.alignment = 'LEFT'
            name.operator("multicamproject.estimation_select", text=names[r.name],
                          emboss=False, icon=core.ICONS[state],
                          depress=obj is not None and obj == context.active_object
                          ).object_name = r.name
            nums.label(text=f"{r.area:.2f} m²")
            nums.label(text=core.short(r.budget))
            nums.label(text=core.short(now))
            nums.label(text=f"{core.removable(r.budget, now) * 100:.0f}%")

        tris = sum(now for _st, now in states.values())
        budget = sum(r.budget for r in s.rows)
        col.separator()
        name, nums = _line(col)
        name.label(text=f"{len(s.rows)} objects")
        nums.label(text=f"{total_area:.2f} m²")
        nums.label(text=core.short(budget))
        nums.label(text=core.short(tris))
        nums.label(text=f"{core.removable(budget, tris) * 100:.0f}%")
        count = [st for st, _now in states.values()]
        legend = box.row(align=True)
        legend.active = False
        legend.label(text=f"{count.count(core.DONE)} on budget", icon=core.ICONS[core.DONE])
        legend.label(text=f"{count.count(core.OVER)} over", icon=core.ICONS[core.OVER])
        legend.label(text=f"{count.count(core.NEW)} not worked on", icon=core.ICONS[core.NEW])
        if budget > s.calculated_budget:
            box.label(text=f"Minimums alone exceed the budget ({core.short(budget)})",
                      icon='ERROR')


def _work_window(scene):
    try:
        from ..linking import core as lk
    except ImportError:
        return False
    return lk.is_work_window(scene)


def _draw_window(layout, context):
    """A work window: the budget it was opened with, against the object now."""
    from ..linking import core as lk
    target = context.scene.get(core.TARGET_KEY, 0)
    obj = lk.work_object(context.scene)
    if not target or obj is None:
        layout.label(text="No budget: not in the main file's OBJECTS, or sent out before "
                          "the Estimation", icon='INFO')
        return
    now = core.tris_now(context, obj)
    row = layout.row()
    row.alert = now > target
    row.label(text=f"{core.short(now)} / {core.short(target)} tris "
                   f"({now / target * 100:.0f}% of the budget)", icon='MOD_DECIM')
    hint = layout.row()
    hint.active = False
    hint.label(text="Budget from the main file's Estimation · the Decimate opened at it",
               icon='INFO')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Estimation)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Estimation)
