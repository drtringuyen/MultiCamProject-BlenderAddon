import bpy

from . import core

# the columns, as fractions of the list's width: colour block, name, Area, Budget, Now,
# To Go, colour block, Stage, the EXPORT fix, the Linking buttons (original Load / Offload +
# Send Out / Receive). Splits, never ui units: header, rows and the total line up exactly
WIDTHS = (0.02, 0.18, 0.095, 0.075, 0.075, 0.1, 0.02, 0.15, 0.045, 0.24)
L, NAME, AREA, BUDGET, NOW, TOGO, R, STAGE, FIX, LINKS = range(len(WIDTHS))
# the last two columns hold buttons of a fixed size: their width in ui units, the rest of
# the list stretches - no empty space after the buttons in a wide panel
FIXED_UNITS = {FIX: 1.3, LINKS: 5.7}
CURRENT = [WIDTHS]      # the widths of this draw (_widths)


def _widths(context):
    """WIDTHS for the region's width now: FIX / LINKS as wide as their buttons (never more
    than WIDTHS gives them), the other columns share what is left as WIDTHS splits it."""
    region = context.region
    if region is None:
        return WIDTHS
    scale = context.preferences.system.ui_scale
    avail = max(1.0, region.width - 60 * scale)
    fixed = {i: min(WIDTHS[i], u * 20 * scale / avail) for i, u in FIXED_UNITS.items()}
    rest = sum(w for i, w in enumerate(WIDTHS) if i not in fixed)
    left = 1.0 - sum(fixed.values())
    return tuple(fixed[i] if i in fixed else w * left / rest for i, w in enumerate(WIDTHS))


def _line(col, widths=None):
    """One line of the table: its cells, in WIDTHS' order (this draw's widths)."""
    widths = widths or CURRENT[0]
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
    if obj.type == 'MESH':       # a Raw scan's cell: Automate's popup
        from . import automate
        automate.draw_stage(cells[STAGE], context, obj, units=3.4)
    if module_manager.is_loaded("export"):
        from ..export import ui as export_ui
        export_ui.draw_fix(cells[FIX], obj.name, st)
    if module_manager.is_loaded("linking"):
        from ..linking import originals, ui as link_ui
        originals.draw_toggle(cells[LINKS], obj)
        link_ui.draw_row_button(cells[LINKS], obj)
        from . import automate      # in a work file: load it back (Keep Local)
        home = cells[LINKS].row(align=True)
        home.ui_units_x = 1.1
        from ..linking import core as lk     # (no file check per redraw: the record only)
        if automate.ready() or lk.out_record(obj) is None:
            home.label(text="", icon='BLANK1')
        else:
            home.operator(automate.MULTICAMPROJECT_OT_EstimationKeepLocal.bl_idname, text="",
                          icon='HOME').object_name = obj.name


def _fit(context, text):
    """`text` cut ('...') to fit the name column with its icon, measured in the UI font."""
    import blf
    prefs = context.preferences
    scale = prefs.system.ui_scale
    region = context.region
    if region is None:
        return text
    avail = (region.width - 60 * scale) * CURRENT[0][NAME] - 34 * scale
    blf.size(0, prefs.ui_styles[0].widget.points * scale)
    if blf.dimensions(0, text)[0] <= avail:
        return text
    while len(text) > 4 and blf.dimensions(0, text + "...")[0] > avail:
        text = text[:-1]
    return text + "..."


def _sort_button(layout, s, column, text):
    """A header cell that sorts the list by its column (again: reversed). The sorted column
    shows its arrow; the colour column has the arrow only."""
    sub = layout.row(align=True)
    if column == 'NAME':
        sub.alignment = 'LEFT'          # over the names
    on = s.sort_by == column
    icon = ('SORT_DESC' if s.sort_reverse else 'SORT_ASC') if on else 'NONE'
    if not text and not on:
        icon = 'SORTSIZE'
    sub.operator("multicamproject.estimation_sort", text=text, icon=icon,
                 emboss=False, depress=on).column = column


def _draw_row(col, context, s, index, ln, st):
    item = s.rows[index]
    cells = _line(col)
    _blocks(cells, item)
    active = index == s.active_index
    # the name focuses the object (selected, active, framed) - the active row pressed in.
    # Left-aligned and cut to fit here (a button Blender has to squeeze starts further left)
    name = cells[NAME].row(align=True)
    name.alignment = 'LEFT'
    op = name.operator("multicamproject.estimation_select", text=_fit(context, ln.label),
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
        from ... import module_manager
        if module_manager.is_loaded("baking"):
            from ..baking import jobs
            if jobs.busy():         # Automate (or a bake) runs: its progress
                jobs.draw(layout)
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

        CURRENT[0] = _widths(context)
        table = _table(context)
        per = _export_status(context)
        index = {r.name: i for i, r in enumerate(s.rows)}
        box = layout.box()
        col = box.column(align=True)
        cells = _line(col)
        cells[R].label(text="")
        for i, column, text in ((L, 'STATE', ""), (NAME, 'NAME', "Object"),
                                (AREA, 'AREA', "Area"), (BUDGET, 'BUDGET', "Budget"),
                                (NOW, 'NOW', "Now"), (TOGO, 'TOGO', "To Go"),
                                (STAGE, 'STAGE', "Stage")):
            _sort_button(cells[i], s, column, text)
        # fix every red one (as each row's button does)
        fix = cells[FIX].row(align=True)
        fix.alert = any(st.stage not in {'READY', 'WARN'} for st in per.values())
        fix.enabled = hasattr(bpy.types, "MULTICAMPROJECT_OT_export_fix_all")
        if fix.enabled:
            fix.operator("multicamproject.export_fix_all", text="", icon='FILE_REFRESH')
        else:
            fix.label(text="", icon='FILE_REFRESH')
        cells[LINKS].label(text="")
        col = box.column(align=True)
        for ln in table:
            _draw_row(col, context, s, index[ln.name], ln, per.get(ln.name))

        # the total, one line: its colour block = the budget's state, the counts at the end
        lines = [ln for ln in table if not ln.room]     # the budget's objects
        tris = sum(ln.now for ln in lines)
        budget = sum(ln.budget for ln in lines)
        core.TOTAL_STATE[0] = core.DONE if tris <= budget else core.OVER
        w = CURRENT[0]
        cells = _line(box.column(align=True), w[:STAGE] + (sum(w[STAGE:]),))
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
        from . import automate
        if module_manager.is_loaded("linking"):
            from ..linking import originals
            automate.draw_button(originals.draw_all(box, context), context)
        else:
            automate.draw_button(box.row(align=True), context)
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
