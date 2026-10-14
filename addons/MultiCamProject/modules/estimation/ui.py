import bpy

from . import core

# the columns, as fractions of the list's width: colour block, name, Area, Budget, Now,
# To Go, colour block, Stage, the EXPORT fix, the Linking buttons (original Load / Offload +
# Send Out / Receive). Splits, never ui units: header, rows and the total line up exactly
WIDTHS = (0.02, 0.2, 0.095, 0.075, 0.075, 0.12, 0.02, 0.15, 0.045, 0.2)
L, NAME, AREA, BUDGET, NOW, TOGO, R, STAGE, FIX, LINKS = range(len(WIDTHS))


def _line(col, widths=WIDTHS):
    """One line of the table: its cells, in WIDTHS' order."""
    cells, rest, left = [], col.row(align=True), 1.0
    for w in widths[:-1]:
        split = rest.split(factor=w / left, align=True)
        cells.append(split.row(align=True))
        rest, left = split.row(align=True), left - w
    cells.append(rest)
    return cells


def _blocks(cells, item):
    """Both colour blocks: `item`'s colour (None: the header's gaps)."""
    for i in (L, R):
        if item is not None:
            cells[i].prop(item, "color", text="")
        else:
            cells[i].label(text="")


def _cell(layout, text, icon='NONE'):
    """A centred cell."""
    cell = layout.row(align=True)
    cell.alignment = 'CENTER'
    cell.label(text=text, icon=icon)


def _delta(layout, d):
    """The To Go cell: '+23K' (green square) or '-5.3K' in red."""
    cell = layout.row(align=True)
    cell.alert = d < 0
    _cell(cell, core.delta_text(d), 'STRIP_COLOR_04' if d >= 0 else 'STRIP_COLOR_01')


def _table(context):
    """core.table, memoized for the redraws (every row's triangles, as shown)."""
    from ... import steps
    return steps.memo(("estimation_table", context.scene.name), lambda: core.table(context))


def _export_status(context):
    """{name: export Status} of the EXPORT meshes (the fix column), {} without Export."""
    from ... import module_manager
    if not module_manager.is_loaded("export"):
        return {}
    from ..export import status
    try:
        return status.scene_status(context.scene, cached=True)[1]
    except Exception:
        return {}


def _draw_tail(cells, context, obj, st):
    """Stage, the EXPORT fix, the Linking buttons."""
    from ... import module_manager, steps
    if obj is None:
        return
    if obj.type == 'MESH':
        steps.draw_stage(cells[STAGE], context, obj, units=3.4)
    if module_manager.is_loaded("export"):
        from ..export import ui as export_ui
        export_ui.draw_fix(cells[FIX], obj.name, st)
    if module_manager.is_loaded("linking"):
        from ..linking import originals, ui as link_ui
        originals.draw_toggle(cells[LINKS], obj)
        link_ui.draw_row_button(cells[LINKS], obj)


def _draw_row(col, context, s, index, ln, st):
    item = s.rows[index]
    cells = _line(col)
    _blocks(cells, item)
    active = index == s.active_index
    # the name focuses the object (selected, active, framed) - the active row pressed in
    op = cells[NAME].operator("multicamproject.estimation_select", text=ln.label,
                              emboss=active, depress=active,
                              icon='HOME' if ln.room else core.ICONS[ln.state])
    op.object_name = ln.name
    if ln.room:         # ROOM: no budget, no To Go
        texts = (f"{ln.area:.2f} m²", "-", core.short(ln.now), "")
    else:
        texts = (f"{ln.area:.2f} m²", core.short(ln.budget), core.short(ln.now))
        _delta(cells[TOGO], ln.delta)
    for i, text in zip((AREA, BUDGET, NOW, TOGO), texts):
        _cell(cells[i], text)
    _draw_tail(cells, context, bpy.data.objects.get(ln.name), st)


class MULTICAMPROJECT_PT_Estimation(bpy.types.Panel):
    """Polycount Estimation (part of Setup): the triangle budget of the OBJECTS collection,
    split by area; ROOM listed with its stage, outside the budget. In a work window: the object's budget, which its Decimate was set to"""
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
        row = layout.row(align=True)
        row.scale_y = 1.3
        coll = core.objects_collection(context)
        sub = row.row(align=True)
        sub.enabled = coll is not None
        sub.operator("multicamproject.estimation_calculate",
                     text=f"Calculate {coll.name}" if coll else "No OBJECTS collection",
                     icon='FILE_REFRESH')
        from ... import roles       # hide / show OBJECTS, ROOM, EXPORT... (as in 09 Export)
        roles.draw_toggles(row.row(align=True), context.scene, context.view_layer)
        if not s.rows:
            return
        if s.calculated_budget != s.budget:
            layout.label(text="Budget changed: Calculate again", icon='ERROR')

        table = _table(context)
        per = _export_status(context)
        index = {r.name: i for i, r in enumerate(s.rows)}
        box = layout.box()
        col = box.column(align=True)
        cells = _line(col)
        _blocks(cells, None)
        for i, text in ((NAME, "Object"), (AREA, "Area"), (BUDGET, "Budget"), (NOW, "Now"),
                        (TOGO, "To Go"), (STAGE, "Stage")):
            _cell(cells[i], text)       # centred, as the names (buttons) are
        _cell(cells[FIX], "", 'FILE_REFRESH')
        cells[LINKS].label(text="")
        col = box.column(align=True)
        for ln in table:
            _draw_row(col, context, s, index[ln.name], ln, per.get(ln.name))

        # the total, one line: its colour block = the budget's state, the counts at the end
        lines = [ln for ln in table if not ln.room]     # the budget's objects
        tris = sum(ln.now for ln in lines)
        budget = sum(ln.budget for ln in lines)
        core.TOTAL_STATE[0] = core.DONE if tris <= budget else core.OVER
        cells = _line(box.column(align=True), WIDTHS[:STAGE] + (sum(WIDTHS[STAGE:]),))
        _blocks(cells, s)
        for i, text in ((NAME, f"{len(lines)} objects"),
                        (AREA, f"{sum(ln.area for ln in lines):.2f} m²"),
                        (BUDGET, core.short(budget)), (NOW, core.short(tris))):
            _cell(cells[i], text)
        _delta(cells[TOGO], budget - tris)
        count = [ln.state for ln in lines]
        legend = cells[STAGE].row(align=True)       # Stage to the end: the counts
        legend.alignment = 'CENTER'
        for state in (core.DONE, core.OVER, core.NEW):     # on budget, over, not worked on
            legend.label(text=str(count.count(state)), icon=core.ICONS[state])
        from ... import module_manager
        if module_manager.is_loaded("linking"):
            from ..linking import originals
            originals.draw_all(box, context)
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


CLASSES = (MULTICAMPROJECT_PT_Estimation,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
