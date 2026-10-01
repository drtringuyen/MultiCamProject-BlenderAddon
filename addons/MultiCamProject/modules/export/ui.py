"""Export panel. Every warning shows here - in the summary box and inline in the status
rows - never in a popup."""
import bpy

from ... import module_manager
from ..baking import common, naming
from ..baking.ui import draw_final_toggle, draw_resolution
from . import checks, status

_ROW_STATUS = {}    # object name -> Status of the current draw (the list reads it)


def _severe(per, code):
    return any(checks.blocking(i) for st in per.values() for i in st.issues if i.code == code)


def _scene_label(layout, scene, es):
    """Scene: <name> - red, with a details arrow, when the file has other scenes."""
    others = checks.other_scenes(scene)
    if not others:
        layout.label(text=scene.name, icon='SCENE_DATA')
        return
    sub = layout.row(align=True)
    sub.alert = True
    sub.label(text=f"{scene.name} +{len(others)}", icon='ERROR')     # details: the arrow
    layout.prop(es, "show_scenes", text="", icon='DOWNARROW_HLT' if es.show_scenes else 'RIGHTARROW')


def tri_text(n, unit=True):
    """Triangle count, short: 236.4k tris, 1.2M tris (unit=False: 236.4k)."""
    u = " tris" if unit else ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M{u}"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k{u}"
    return f"{n}{u}"


def stage_text(obj, st):
    """The status line of an EXPORT object: its stage, or its first problem / warning."""
    label = status.STAGES[st.stage][0]
    problems = [i for i in st.issues if checks.blocking(i)]
    warns = [i for i in st.issues if checks.soft(i)]
    if st.stage == 'READY' and obj.multicamproject_bake.handmade:
        label = "Ready (handmade)"
    elif st.stage == 'PROJECTION':      # one stage, two causes: name the real one
        label = "Decimate not applied (03)" if checks.live_decimate(obj) else "no uv_normal"
    if st.stage == 'ISSUES' and problems:
        label = problems[0].text
    elif st.stage == 'WARN' and warns:
        label = warns[0].text
    return label


class MULTICAMPROJECT_UL_export(bpy.types.UIList):
    """EXPORT meshes in ## order. Clicking a row selects and frames the object.
    [solo] [status] ENV_<prefix>. [##] <name> [hand] tris [size] [fix]"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        st = _ROW_STATUS.get(item.name)
        if st is None:
            return
        from .operators import is_soloed
        bake = item.multicamproject_bake
        row = layout.row(align=True)
        solo = is_soloed(context, item)
        op = row.operator("multicamproject.export_solo", text="", depress=solo,
                          icon='HIDE_OFF' if solo else 'HIDE_ON')
        op.object_name = item.name
        row.label(text="", icon=status.STAGES[st.stage][1])
        sc = naming.scheme(context.scene)
        p = naming.parse(item.name, sc) if sc else None
        if p:
            # the part the add-on fills in, greyed; ## and <name> are typed (double-click)
            fixed = f"{naming.ENV}_{sc.core}."
            pre = row.row(align=True)
            pre.active = False
            pre.ui_units_x = min(3.0, 0.45 * len(fixed) + 0.3)
            pre.label(text=fixed)
            num = row.row(align=True)
            num.ui_units_x = 2.0
            num.prop(item, "multicamproject_export_index", text="")
            row.prop(item, "multicamproject_short_name", text="", emboss=False)
        else:
            row.prop(item, "name", text="", emboss=False)
        right = layout.row(align=True)
        hand = right.row(align=True)
        hand.ui_units_x = 1.1
        hand.prop(bake, "handmade", text="", icon='VIEW_PAN', toggle=True)
        tris = right.row()
        tris.ui_units_x = 2.2
        tris.active = False
        tris.alignment = 'RIGHT'
        tris.label(text=tri_text(checks.mesh_counts(item)[0], unit=False))
        size = right.row(align=True)
        size.ui_units_x = 2.2
        size.prop(bake, "tex_size", text="")
        # the fix column: one button per object that is not ready
        fix = right.row(align=True)
        fix.ui_units_x = 1.1
        if st.stage == 'READY':
            fix.label(text="", icon='CHECKMARK')
        elif st.stage == 'WARN':       # nothing to fix automatically: orange, no button
            fix.label(text="", icon='ERROR')
        else:
            sub = fix.row(align=True)
            sub.alert = st.stage in {'OUTDATED', 'ISSUES', 'PROJECTION'}
            op = sub.operator("multicamproject.export_fix_object", text="", icon='FILE_REFRESH')
            op.object_name = item.name

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if it.name in _ROW_STATUS else 0 for it in items]
        sc = naming.scheme(context.scene)

        def key(it):
            p = naming.parse(it.name, sc) if sc else None
            if p:                   # with a prefix: the ## order the arrows change
                return (0, p[0], it.name.lower())
            stage = status.STAGES[_ROW_STATUS[it.name].stage][2] if it.name in _ROW_STATUS else 99
            return (1, stage, it.name.lower())
        order = bpy.types.UI_UL_list.sort_items_helper(
            [(i, key(it)) for i, it in enumerate(items)], lambda e: e[1])
        return flags, order


class MULTICAMPROJECT_PT_Export(bpy.types.Panel):
    bl_label = "07. Final Export"
    bl_idname = "MULTICAMPROJECT_PT_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 4

    def draw_header(self, context):
        self.layout.label(icon='EXPORT')

    def draw(self, context):
        layout = self.layout
        if not module_manager.is_loaded("baking"):
            layout.label(text="Needs the Baking module", icon='ERROR')
            return
        from ..baking import jobs
        if jobs.busy():                 # the list would show half-done states: progress only
            jobs.draw(layout)
            return
        scene = context.scene
        es = scene.multicamproject_export

        self._draw_prefix(layout, scene, es)

        coll = common.export_collection(scene)
        if coll is None:
            self._draw_export_row(layout)
            self._draw_info(layout, scene, es)
            layout.label(text="Select meshes and click Add", icon='INFO')
            return
        objs, per, grouped = status.scene_status(scene)
        _ROW_STATUS.clear()
        _ROW_STATUS.update(per)

        row = layout.row()
        lst = row.column(align=True)
        self._draw_list_header(lst, scene, es, objs, per, grouped)
        lst.template_list("MULTICAMPROJECT_UL_export", "", coll, "all_objects", es,
                          "active_index", rows=6)
        side = row.column(align=True)
        side.separator(factor=2.4)          # lines the buttons up with the list, below its header
        side.operator("multicamproject.export_add", text="", icon='ADD')
        side.operator("multicamproject.export_remove", text="", icon='REMOVE')
        side.separator()
        op = side.operator("multicamproject.export_move", text="", icon='TRIA_UP')
        op.step = -1
        op = side.operator("multicamproject.export_move", text="", icon='TRIA_DOWN')
        op.step = 1
        side.separator()
        side.operator("multicamproject.export_renumber", text="", icon='SORTSIZE')
        # scene details, textures folder, problems with a fix: under the list
        self._draw_info(layout, scene, es, scene_row=False)
        self._draw_summary(layout, per, grouped)
        self._draw_active(layout, context, per)

        # [One FBX v] Folder [path.........][dir]
        row = layout.row(align=True)
        mode = row.row(align=True)
        mode.ui_units_x = 5.5
        mode.prop(es, "split", text="")
        lbl = row.row(align=True)
        lbl.ui_units_x = 2.4
        lbl.alignment = 'RIGHT'
        lbl.label(text="Folder")
        row.prop(es, "folder", text="")
        draw_final_toggle(layout, es, "view")
        to_fix = sum(st.stage not in {'READY', 'WARN'} for st in per.values())
        files = f"{naming.fbx_name(scene)}.fbx" if es.split == 'ONE' else f"{len(objs)} FBX files"
        row = layout.row(align=True)
        row.scale_y = 1.5
        draw_resolution(row, common.settings(scene))
        row.separator(factor=0.5)
        row.operator("multicamproject.export_fbx", icon='EXPORT',
                     text=(f"Fix {to_fix} + Export" if to_fix else "Export") + f"  ({files})")
        row.operator("multicamproject.export_clean_export_textures", text="", icon='TRASH')

        header, body = layout.panel("multicamproject_export_advanced", default_closed=True)
        header.label(text="Advanced")
        if body:
            s = common.settings(scene)
            col = body.column()
            col.prop(s, "color_source")
            col.prop(s, "png_compression")
            col.prop(es, "write_cs")
            col.prop(es, "write_report")

    @staticmethod
    def _draw_prefix(layout, scene, es):
        """The Name Prefix and what it makes of the names."""
        box = layout.box()
        col = box.column(align=True)
        col.prop(es, "name_prefix", icon='SORTALPHA', placeholder="00_30stBR")
        sc = naming.scheme(scene)
        hint = col.column(align=True)
        hint.active = False
        if sc is None:
            hint.label(text="e.g. 00_30stBR (ENV_ is added) - empty: clean names only")
            return
        core = f"{sc.core}.##_Name"
        hint.label(text=f"Object  ENV_{core}   ·   FBX  {naming.fbx_name(scene)}.fbx")
        hint.label(text=f"MAT_{core}   ·   ALB_/NOR_{core}")

    @staticmethod
    def _draw_export_row(layout):
        row = layout.row(align=True)
        row.label(text="EXPORT", icon='COLLECTION_COLOR_03')
        row.operator("multicamproject.export_add", text="Add", icon='ADD')
        row.operator("multicamproject.export_remove", text="Remove", icon='REMOVE')

    @staticmethod
    def _draw_list_header(layout, scene, es, objs, per, grouped):
        """EXPORT (meshes · ready · tris · warnings) ........ Scene: <name>"""
        ready = sum(st.stage in {'READY', 'WARN'} for st in per.values())
        tris = sum(checks.mesh_counts(o)[0] for o in objs)
        notes = sum(1 for code in grouped if not _severe(per, code))
        # notes (e.g. n-gons) are counted here; their texts show under the list for the
        # active object
        warn = f" · {notes} warn" if notes else ""
        split = layout.split(factor=0.72, align=True)       # the counts get most of the row
        split.label(text=f"EXPORT ({ready}/{len(objs)} ready · {tri_text(tris)}{warn})",
                    icon='COLLECTION_COLOR_03')
        right = split.row(align=True)
        right.alignment = 'RIGHT'
        _scene_label(right, scene, es)

    def _draw_info(self, layout, scene, es, scene_row=True):
        if scene_row:
            _scene_label(layout.row(align=True), scene, es)
        self._draw_scenes(layout, scene, es)
        unused = checks.unused_textures(scene, fresh=False)
        if unused:
            row = layout.row(align=True)
            sub = row.row()
            sub.alert = True
            sub.label(text=f"{len(unused)} old bake(s) of this file in the bake folder", icon='ERROR')
            row.operator("multicamproject.export_clean_textures", text="Clean", icon='TRASH')

    @staticmethod
    def _draw_scenes(layout, scene, es):
        """The other scenes' details, when opened from the scene label."""
        others = checks.other_scenes(scene)
        if others and es.show_scenes:
            box = layout.box().column(align=True)
            for o in others:
                r = box.row(align=True)
                text = f"{o.name}  ·  {o.cameras} cams"
                if o.shared:
                    text += f"  ·  shares: {', '.join(o.shared[:3])}" + ("..." if len(o.shared) > 3 else "")
                r.label(text=text)
                op = r.operator("multicamproject.export_delete_scene", text="Delete", icon='TRASH')
                op.scene_name = o.name

    @staticmethod
    def _draw_summary(layout, per, grouped):
        """The problems (red) and the soft warnings (orange, e.g. overlapping uv_normal);
        notes are counted in the list header."""
        codes = [code for code in grouped if _severe(per, code)]
        soft = [code for code in grouped if code not in codes and any(
            checks.soft(i) for st in per.values() for i in st.issues if i.code == code)]
        if not codes and not soft:
            return
        col = layout.box().column(align=True)
        for code in codes + soft:
            names = grouped[code]
            text, op, fix_label = checks.SUMMARY[code]
            r = col.row(align=True)
            r.alert = code in codes     # a soft warning keeps the orange triangle
            r.label(text=text.format(n=len(names)), icon='ERROR')
            if op:
                r.operator(op, text=fix_label)
            elif fix_label:
                r.label(text=f"({fix_label})")

    @staticmethod
    def _draw_active(layout, context, per):
        """The issues of the active object, inline under the list."""
        obj = context.active_object
        st = per.get(obj.name) if obj else None
        if st is None:
            return
        col = layout.column(align=True)
        tris, _ng = checks.mesh_counts(obj)
        col.label(text=f"{obj.name}  ·  {tris:,} triangles", icon='MESH_DATA')
        if st.stage not in {'ISSUES', 'WARN'}:      # those name their issue: listed below
            r = col.row()
            r.alert = st.stage in {'OUTDATED', 'PROJECTION'}
            r.label(text=stage_text(obj, st), icon=status.STAGES[st.stage][1])
        for i in st.issues:
            r = col.row()
            r.alert = i.severity == checks.ERROR
            r.label(text=i.text, icon='ERROR' if i.severity != checks.INFO else 'INFO')


_classes = (MULTICAMPROJECT_UL_export, MULTICAMPROJECT_PT_Export)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
