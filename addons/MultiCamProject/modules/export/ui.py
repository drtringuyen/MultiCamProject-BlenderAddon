"""Export panel. Every warning shows here - in the summary box and inline in the status
rows - never in a popup."""
import bpy

from ... import module_manager
from ..baking import common, naming
from ..baking.ui import draw_final_toggle
from . import checks, status

_ROW_STATUS = {}    # object name -> Status of the current draw (the list reads it)


def tri_text(n, unit=True):
    """Triangle count, short: 236.4k tris, 1.2M tris (unit=False: 236.4k)."""
    u = " tris" if unit else ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M{u}"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k{u}"
    return f"{n}{u}"


class MULTICAMPROJECT_UL_export(bpy.types.UIList):
    """EXPORT meshes, problems first. Clicking a row selects and frames the object."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        st = _ROW_STATUS.get(item.name)
        if st is None:
            return
        label, icon, _o = status.STAGES[st.stage]
        split = layout.split(factor=0.58, align=True)
        row = split.row(align=True)
        row.label(text="", icon=icon)
        sc = naming.scheme(context.scene)
        p = naming.parse(item.name, sc) if sc else None
        if p:
            # the part the add-on fills in, greyed; only <name> is typed (double-click)
            fixed = naming.fixed_part(sc, p[0])
            pre = row.row(align=True)
            pre.active = False
            pre.ui_units_x = 0.45 * len(fixed) + 0.3
            pre.label(text=fixed)
            row.prop(item, "multicamproject_short_name", text="", emboss=False)
        else:
            row.prop(item, "name", text="", emboss=False)
        problems = [i for i in st.issues if i.severity != checks.INFO]
        right = split.split(factor=0.42, align=True)
        tris = right.row()
        tris.active = False
        tris.alignment = 'RIGHT'
        tris.label(text=tri_text(checks.mesh_counts(item)[0], unit=False))
        sub = right.row()
        sub.alert = st.stage in {'OUTDATED', 'ISSUES', 'PROJECTION'}
        sub.label(text=problems[0].text if st.stage == 'ISSUES' and problems else label)

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
    bl_label = "Export"
    bl_idname = "MULTICAMPROJECT_PT_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 3

    def draw_header(self, context):
        self.layout.label(icon='EXPORT')

    def draw(self, context):
        layout = self.layout
        if not module_manager.is_loaded("baking"):
            layout.label(text="Needs the Baking module", icon='ERROR')
            return
        scene = context.scene
        es = scene.multicamproject_export

        self._draw_prefix(layout, scene, es)
        row = layout.row(align=True)
        row.label(text="EXPORT", icon='COLLECTION_COLOR_03')
        row.operator("multicamproject.export_add", text="Add", icon='ADD')
        row.operator("multicamproject.export_remove", text="Remove", icon='REMOVE')

        self._draw_scenes(layout, scene, es)
        unused = checks.unused_textures(scene)
        if unused:
            row = layout.row(align=True)
            sub = row.row()
            sub.alert = True
            sub.label(text=f"{len(unused)} unused ALB_/NOR_ PNG(s) in the Textures folder", icon='ERROR')
            row.operator("multicamproject.export_clean_textures", text="Clean", icon='TRASH')

        coll = common.export_collection(scene)
        if coll is None:
            layout.label(text="Select meshes and click Add", icon='INFO')
            return
        objs, per, grouped = status.scene_status(scene)
        _ROW_STATUS.clear()
        _ROW_STATUS.update(per)
        self._draw_summary(layout, objs, per, grouped)

        draw_final_toggle(layout, context.active_object if context.active_object in objs else None,
                          scope='EXPORT')
        row = layout.row()
        row.template_list("MULTICAMPROJECT_UL_export", "", coll, "all_objects", es,
                          "active_index", rows=6)
        side = row.column(align=True)
        op = side.operator("multicamproject.export_move", text="", icon='TRIA_UP')
        op.step = -1
        op = side.operator("multicamproject.export_move", text="", icon='TRIA_DOWN')
        op.step = 1
        side.separator()
        side.operator("multicamproject.export_renumber", text="", icon='SORTSIZE')
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
        to_fix = sum(st.stage != 'READY' for st in per.values())
        files = f"{naming.fbx_name(scene)}.fbx" if es.split == 'ONE' else f"{len(objs)} FBX files"
        row = layout.row()
        row.scale_y = 1.5
        row.operator("multicamproject.export_fbx", icon='EXPORT',
                     text=(f"Fix {to_fix} + Export" if to_fix else "Export") + f"  ({files})")

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
    def _draw_scenes(layout, scene, es):
        others = checks.other_scenes(scene)
        row = layout.row(align=True)
        if not others:
            row.label(text=f"Scene: {scene.name}", icon='SCENE_DATA')
            return
        cams = sum(o.cameras for o in others)
        imgs = sum(o.images for o in others)
        sub = row.row()
        sub.alert = True
        sub.label(text=f"Scene: {scene.name}  ·  {len(others)} other scene(s) "
                       f"({cams} cameras, {imgs} images)", icon='ERROR')
        row.prop(es, "show_scenes", text="", icon='DOWNARROW_HLT' if es.show_scenes else 'RIGHTARROW')
        if es.show_scenes:
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
    def _draw_summary(layout, objs, per, grouped):
        ready = sum(st.stage == 'READY' for st in per.values())
        bad = len(objs) - ready
        box = layout.box()
        row = box.row()
        total = sum(checks.mesh_counts(o)[0] for o in objs)
        row.label(text=f"{len(objs)} meshes  ·  {ready} ready"
                       + (f"  ·  {bad} not ready" if bad else "") + f"  ·  {tri_text(total)}",
                  icon='CHECKMARK' if not bad else 'INFO')
        col = box.column(align=True)
        for code, names in grouped.items():
            text, op, fix_label = checks.SUMMARY[code]
            severe = any(i.severity != checks.INFO for st in per.values()
                         for i in st.issues if i.code == code)
            r = col.row(align=True)
            r.alert = severe
            r.label(text=text.format(n=len(names)), icon='ERROR' if severe else 'INFO')
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
