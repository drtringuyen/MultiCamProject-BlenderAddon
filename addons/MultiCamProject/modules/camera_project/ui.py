import bpy
from bl_ui.space_toolsystem_common import ToolSelectPanelHelper

from ... import gate
from . import core
from .operators import CAM_BRUSHES, cameras_hidden, flood_ready, is_solo


def _liquify_loaded():
    from ... import module_manager
    return bool(module_manager.is_loaded("image_editor"))


def _draw_liquify(layout, cam):
    """Nudge (Liquify) / Cut (Lasso) this camera's photo in solo view (image_editor module;
    hidden when it is off)."""
    if not _liquify_loaded():
        return
    from ..image_editor import camera_op
    op = layout.operator("multicamproject.liquify_camera", text="", icon='MOD_WARP',
                         depress=camera_op.running == cam.name)
    op.camera = cam.name
    from ..image_editor import lasso_ops
    op = layout.operator("multicamproject.lasso_camera", text="",
                         depress=lasso_ops.running_camera() == cam.name,
                         **_icon("file:lasso"))
    op.camera = cam.name


def _draw_bake_row(context, layout, obj):
    """Under the slots, in one box: Processing | Final (as in 06) and 06's bake row
    ([Auto Resolution v] [Bake ...] [mixture] [gear]) - bake right after picking cameras.
    Without the baking module: Bake Camera Mixture alone."""
    try:
        from ..baking import ui as bake_ui
    except ImportError:
        layout.operator("multicamproject.bake_view_mix", text="Bake Camera Mixture",
                        icon='RENDER_STILL')
        return
    bake_ui.draw_bake_box(context, layout, obj)


_previews = None        # the add-on's own icons (icons/*.png), loaded on first use


def _file_icon(name):
    """icon_value of <add-on>/icons/<name>.png. These are Blender's toolbar icons (bucket,
    eraser, lasso) drawn as plain square images: a toolbar icon itself sits off centre and
    clipped in a normal button."""
    global _previews
    import os
    import bpy.utils.previews
    if _previews is None:
        _previews = bpy.utils.previews.new()
    if name not in _previews:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                            "icons", name + ".png")
        if not os.path.isfile(path):
            return 0
        _previews.load(name, path, 'IMAGE')
    return _previews[name].icon_id


def _icon(icon):
    """Keyword for layout.operator: a UI icon name, "file:<name>" for one of the add-on's
    icons, or "tool:<handle>" for a toolbar icon."""
    if icon.startswith("file:"):
        value = _file_icon(icon[5:])
        return {"icon_value": value} if value else {"icon": 'QUESTION'}
    if icon.startswith("tool:"):
        value = ToolSelectPanelHelper._icon_value_from_icon_handle(icon[5:])
        if value:
            return {"icon_value": value}
        return {"icon": 'QUESTION'}
    return {"icon": icon}


TOOL_SCALE = 1.4     # Paint / Flood / Erase buttons and the shift fields next to them
TOOL_WIDTH = 1.0     # units per tool button: narrow, the X / Y shift fields need the room


class MULTICAMPROJECT_PT_CameraProject(bpy.types.Panel):
    """05 Projection Painting: go through the cameras and paint VCMix / VCMix2 - which
    camera shows where, and (alpha) projection or BAo_. Shown once 0B is done"""
    bl_label = "05. Projection Painting"
    bl_idname = "MULTICAMPROJECT_PT_camera_project"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and core.data(obj).is_setup

    def draw_header(self, context):
        self.layout.label(icon='BRUSH_DATA')

    def draw(self, context):
        gate.lock(self.layout, context)     # greyed out until Setup File IO
        layout = self.layout
        obj = context.active_object
        d = core.data(obj)

        mod = core.get_modifier(obj)
        if mod is None or mod.node_group is None:
            layout.alert = True
            layout.label(text="Projection modifier missing", icon='ERROR')
            layout.alert = False
            layout.operator("multicamproject.setup", text="Rebuild Setup", icon='FILE_REFRESH')
            return

        self._draw_mode(layout, obj)
        # straight in the panel: Folder, Clip + Mode, blend row, then the camera sections
        self._draw_box(context, layout, obj, d, mod)

    @staticmethod
    def _draw_mode(layout, obj):
        """What the Processing material blends (the VCMix layers decide where)."""
        ba, _bn = core.baked_images(obj)
        row = layout.row(align=True)
        row.active = False
        if ba is not None:
            row.label(text="Mix: projection over BAo_ (by the mask, Bake Route Mixed)", icon='NODE_MATERIAL')
        else:
            row.label(text="Projection only (grey where erased)", icon='NODE_MATERIAL')

    LABEL_UNITS = 2.6      # width of the Folder / Clip / Blend label column
    MODE_SPLIT = 0.66      # Clip | Mode and Blend | Occlusion share this split

    @classmethod
    def _labeled(cls, context, layout, label):
        """A row with its label in a narrow fixed column: every field starts right after it,
        at the same x. The split comes from the region's pixel width (ui_units_x on a label
        gets stretched when the row holds a file path field)."""
        unit = 20 * context.preferences.system.ui_scale
        avail = max(1.0, context.region.width - 3.6 * unit)     # panel margins + indent
        split = layout.split(factor=min(0.4, cls.LABEL_UNITS * unit / avail), align=True)
        split.label(text=label)
        return split.row(align=True)

    def _draw_box(self, context, box, obj, d, mod):
        # indented to the Selected / Other Cameras arrows below
        outer = box.row()
        outer.separator(factor=1.6)
        col = outer.column()

        row = self._labeled(context, col, "Folder")
        row.prop(d, "image_folder", text="")
        row.operator("multicamproject.reload_all", text="", icon='FILE_REFRESH')
        # clipping of every camera + the projection mode
        row = self._labeled(context, col, "Clip")
        split = row.split(factor=self.MODE_SPLIT, align=True)
        clip = split.row(align=True)
        clip.prop(d, "clip_start")
        clip.prop(d, "clip_end")
        split.prop(core.input_socket(mod, "Mode"), "value", text="")

        # blend controls
        row = self._labeled(context, col, "Blend")
        split = row.split(factor=self.MODE_SPLIT, align=True)     # Occlusion under Mode
        blend = split.row(align=True)
        blend.prop(core.input_socket(mod, "Previous Bake"), "value", text="Previous Bake")
        split.prop(d, "project_through", text="Project through", toggle=True,
                   icon='XRAY')

        box.separator(type='LINE')
        if not d.cameras:
            box.label(text="No camera sees this object - Reload All", icon='INFO')
            return

        debug = context.scene.multicamproject_props.debug_mode
        cam = context.scene.camera
        solo_cam = cam if is_solo(context, cam) else None
        flood = flood_ready(context, obj)
        top, rest = core.display_order(obj)
        # always drawn: its header holds the slot count
        header, body = box.panel("multicamproject_selected_cams", default_closed=False)
        header.label(text=f"Selected Cameras ({len(top)})", icon='VIEW_CAMERA')
        # Resort (score all cameras, pick the best), slot count dropdown, Refresh (check the
        # material's Cam textures against the slots) at the end, like Other Cameras
        sub = header.row(align=True)
        sub.operator("multicamproject.auto_pick", text="", icon='SORTSIZE')
        cnt = sub.row(align=True)
        cnt.ui_units_x = 3.2
        cnt.prop(d, "slot_count", text="")
        sub.operator("multicamproject.check_slots", text="", icon='FILE_REFRESH')
        if body:
            for item in top:
                self._draw_block(context, body, obj, item, debug, solo_cam, shift=True, flood=flood)
            _draw_bake_row(context, body, obj)
        # always drawn, also when every camera is in a slot: its header holds the coverage
        # filter, Restore Removed and Measure
        header, body = box.panel("multicamproject_all_cams", default_closed=False)
        slots = core.get_slots(d)
        total = sum(1 for it in d.cameras if it.camera and it.camera not in slots)
        removed = f" - {len(d.removed)} removed" if len(d.removed) else ""
        # the cameras not in a slot; "shown/total" only while the coverage filter hides some
        count = str(total) if len(rest) == total else f"{len(rest)}/{total}"
        header.label(text=f"Other Cameras ({count}{removed})", icon='VIEW_CAMERA_UNSELECTED')
        # camera collection eye, coverage filter (how much of the object a camera must see),
        # restore removed
        flt = header.row(align=True)
        hidden = cameras_hidden(context, obj)
        flt.operator("multicamproject.toggle_camera_folder", text="", depress=not hidden,
                     icon='HIDE_ON' if hidden else 'HIDE_OFF')
        drop = flt.row(align=True)
        drop.ui_units_x = 3.2
        drop.prop(d, "coverage_filter", text="")
        flt.operator("multicamproject.restore_cameras", text="", icon='LOOP_BACK')
        if body and len(d.cameras) and not core.measured(d):
            r = body.row()      # every camera shown until then: the filter needs the measure
            r.active = False
            r.label(text="Coverage not measured - Resort scores the cameras and picks",
                    icon='INFO')
        if body and not total:
            body.label(text="No other camera - every camera seeing the object is in a slot",
                       icon='INFO')
        elif body:
            # a list widget: it scrolls itself to its active row = the soloed camera;
            # up / down / resort next to it, the search under it (as in EXPORT)
            row = body.row()
            lst = row.column(align=True)
            if rest or d.cam_search:        # kept while searching: the search is in its bar
                lst.template_list("MULTICAMPROJECT_UL_cameras", "", d, "cameras", d, "cam_index",
                                  rows=12)
            else:
                lst.label(text="No camera passes the coverage filter", icon='INFO')
            side = row.column(align=True)
            side.operator("multicamproject.cam_step", text="", icon='TRIA_UP').step = -1
            side.operator("multicamproject.cam_step", text="", icon='TRIA_DOWN').step = 1
            side.separator()
            side.operator("multicamproject.measure_coverage", text="", icon='SORTSIZE')

    @staticmethod
    def _metrics(context, debug, n, margin=2.8, extra=0):
        """Split factors for a camera block. Blender scales ui_units_x down in narrow
        panels, so fixed widths are made with splits from the region's pixel width:
        name and slot buttons keep their size, the image field absorbs the rest."""
        unit = 20 * context.preferences.system.ui_scale
        avail = max(1.0, context.region.width - margin * unit)   # boxes, panel, list frame
        per = 1.4 if n == 3 else 1.15                             # width of one slot button
        slots_f = min(0.6, (n + 1 + extra) * per * unit / avail)  # 1..n + global (+ remove)
        left_w = max(1.0, avail * (1.0 - slots_f) - unit)       # minus eye button
        name_f = min(0.6, (5.5 if debug else 4.5) * unit / left_w)
        image_x = (unit + name_f * left_w) / avail               # where the image field starts
        return slots_f, name_f, image_x

    def _draw_block(self, context, layout, obj, item, debug, solo_cam, shift, flood):
        """One camera = one box. While a camera is soloed every other box is dimmed, so
        the soloed one stands out (Blender can only tint a whole block red)."""
        col = layout.box().column()
        col.scale_y = 1.25
        solo = item.camera == solo_cam
        col.active = solo_cam is None or solo
        m = self._metrics(context, debug, core.slot_count(core.data(obj)))
        self._draw_row(col, obj, item, debug, m, solo)
        if shift:
            self._draw_shift(col, obj, item.camera, m, flood)

    @staticmethod
    def _draw_row(col, obj, item, debug, m, solo, removable=False):
        slots_f, name_f = m[:2]
        cam = item.camera
        split = col.row().split(factor=1.0 - slots_f, align=True)
        left, right = split.row(align=True), split.row(align=True)

        eye = left.row(align=True)
        eye.active = True       # never dimmed: clicking another eye switches the solo camera
        op = eye.operator("multicamproject.solo_camera", text="",
                          icon='HIDE_OFF' if solo else 'HIDE_ON', depress=solo)
        op.camera = cam.name

        nsplit = left.split(factor=name_f, align=True)
        nsplit.label(text=f"{cam.name} {item.score:.2f} {item.coverage:.0%}" if debug else cam.name)
        mid = nsplit.row(align=True)

        # image field takes all remaining width
        bg = core.bg_entry(cam)
        if bg:
            mid.prop(bg, "image", text="")
        else:
            mid.label(text="(no background)")

        sub = mid.row(align=True)
        sub.alert = not core.image_ok(core.cam_image(cam))
        op = sub.operator("multicamproject.load_cam_image", text="", icon='FILE_FOLDER')
        op.camera = cam.name

        count = core.slot_count(core.data(obj))
        # equal columns: the global toggle gets the same width as a slot button
        slots = right.grid_flow(row_major=True, columns=count + 1 + removable, even_columns=True,
                                align=True)
        current = core.slot_of(obj, cam)
        for n in range(1, count + 1):
            op = slots.operator("multicamproject.assign_slot", text=str(n), depress=current == n)
            op.camera = cam.name
            op.slot = n
        glob = core.is_global(cam)
        op = slots.operator("multicamproject.toggle_global", text="",
                            icon='WORLD' if glob else 'OBJECT_DATA', depress=glob)
        op.camera = cam.name
        if removable:
            op = slots.operator("multicamproject.remove_camera", text="", icon='X')
            op.camera = cam.name

    def _draw_shift(self, col, obj, cam, m, flood):
        it = core.shift_holder(obj, cam)      # a global camera's shift is shared by every object
        if it is None:
            return
        # the tools as narrow icon buttons (their icons are the add-on's own, centred); the
        # X / Y shift fields take the rest of the line
        line = col.row(align=True)
        tools = line.row(align=True)            # vertex paint VCMix with this camera's color
        tools.scale_y = TOOL_SCALE
        tools.scale_x = TOOL_WIDTH
        for mode, _name, icon in CAM_BRUSHES:
            sub = tools.row(align=True)
            sub.enabled = mode != 'FLOOD' or flood
            op = sub.operator("multicamproject.cam_paint", text="", **_icon(icon))
            op.camera, op.mode = cam.name, mode
        _draw_liquify(tools, cam)
        # next to Erase: the whole object's alpha (VCMix + VCMix2) to 0 at once
        tools.operator("multicamproject.mask_fill", text="", icon='X').value = 0.0
        row = line.row(align=True)
        row.scale_y = TOOL_SCALE                 # same height: bottoms line up with the buttons
        row.prop(it, "shift", index=0, text="X", slider=True)
        row.prop(it, "shift", index=1, text="Y", slider=True)
        op = row.operator("multicamproject.load_shift", text="", icon='PASTEDOWN')
        op.camera = cam.name


_filter_opened = set()      # list ids whose filter bar was opened once this session


class MULTICAMPROJECT_UL_cameras(bpy.types.UIList):
    """Other Cameras: the cameras not in a slot that pass the coverage filter and the search,
    most coverage first. Its active row is the soloed camera; clicking a name solos that camera."""

    def draw_filter(self, context, layout):
        """The list's own filter bar (as in EXPORT), holding the camera search - stored on
        the object, so the up / down buttons and arrow keys step through what it shows."""
        obj = context.active_object
        if obj is not None:
            layout.row().prop(core.data(obj), "cam_search", text="", icon='VIEWZOOM')

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.camera is None:
            return
        panel = MULTICAMPROJECT_PT_CameraProject
        debug = context.scene.multicamproject_props.debug_mode
        solo = is_solo(context, item.camera)
        col = layout.column()
        col.active = solo or not is_solo(context, context.scene.camera)
        panel._draw_row(col, context.active_object, item, debug,
                        panel._metrics(context, debug, core.slot_count(data), margin=6.0, extra=1),
                        solo, removable=True)   # margin: + list frame, scrollbar, side buttons

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        slots = core.get_slots(data)
        if self.list_id not in _filter_opened:      # the search bar starts open
            _filter_opened.add(self.list_id)
            self.use_filter_show = True
        pattern = self.filter_name.lower()
        flags = [self.bitflag_filter_item
                 if it.camera and it.camera not in slots and core.passes(data, it)
                 and core.searched(data, it) and pattern in it.camera.name.lower() else 0
                 for it in items]
        # most coverage first, by name before a measure (the order the arrow keys step through)
        order = bpy.types.UI_UL_list.sort_items_helper(
            [(i, core.sort_key(it)) for i, it in enumerate(items)], lambda e: e[1])
        return flags, order


_classes = (MULTICAMPROJECT_PT_CameraProject, MULTICAMPROJECT_UL_cameras)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    global _previews
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    if _previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews)
        _previews = None
