import bpy
from bl_ui.space_toolsystem_common import ToolSelectPanelHelper

from . import core
from .operators import CAM_BRUSHES, flood_ready, is_solo


def _icon(icon):
    """Keyword for layout.operator: a UI icon name, or "tool:<handle>" for a toolbar icon."""
    if icon.startswith("tool:"):
        value = ToolSelectPanelHelper._icon_value_from_icon_handle(icon[5:])
        if value:
            return {"icon_value": value}
        return {"icon": 'QUESTION'}
    return {"icon": icon}


TOOL_SCALE = 1.4     # Paint / Flood / Erase buttons and the shift fields next to them


class MULTICAMPROJECT_PT_CameraProject(bpy.types.Panel):
    bl_label = "Camera Project"
    bl_idname = "MULTICAMPROJECT_PT_camera_project"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            layout.alert = True
            layout.label(text="Select a mesh object", icon='ERROR')
            return
        d = core.data(obj)
        if not d.is_setup:
            layout.operator("multicamproject.setup", icon='CAMERA_DATA')
            return

        mod = core.get_modifier(obj)
        if mod is None or mod.node_group is None:
            layout.alert = True
            layout.label(text="Projection modifier missing", icon='ERROR')
            layout.alert = False
            layout.operator("multicamproject.setup", text="Rebuild Setup", icon='FILE_REFRESH')
            return

        # straight in the panel: Folder, Clip + Mode, blend row, then the camera sections
        self._draw_box(context, layout, obj, d, mod)

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
        # indented to the Selected / All Cameras arrows below
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
        scan = d.material.node_tree.nodes.get(core.ORIGINAL_SCAN) if d.material else None
        if scan:
            # a Value node has no 0..1 range, so no slider (its bar would be wrong)
            blend.prop(scan.outputs[0], "default_value", text="Original Scan")
        split.prop(core.input_socket(mod, "Occlusion"), "value", text="Occlusion", toggle=True,
                   icon='MOD_MASK')

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
        header.label(text=f"Selected Cameras ({len(top)})", icon='RESTRICT_SELECT_OFF')
        # slot count dropdown + Auto (re-pick by axis) as an icon at the end, like All Cameras
        sub = header.row(align=True)
        cnt = sub.row(align=True)
        cnt.ui_units_x = 3.2
        cnt.prop(d, "slot_count", text="")
        sub.operator("multicamproject.auto_pick", text="", icon='FILE_REFRESH')
        if body:
            for item in top:
                self._draw_block(context, body, obj, item, debug, solo_cam, shift=True, flood=flood)
            body.operator("multicamproject.bake_view_mix", text="Bake Camera Mixture",
                          icon='RENDER_STILL')
        if any(it.camera and it.camera not in core.get_slots(d) for it in d.cameras):
            header, body = box.panel("multicamproject_all_cams", default_closed=False)
            slots = core.get_slots(d)
            total = sum(1 for it in d.cameras if it.camera and it.camera not in slots)
            removed = f" - {len(d.removed)} removed" if len(d.removed) else ""
            header.label(text=f"All Cameras ({len(rest)}/{total}{removed})", icon='OUTLINER_OB_CAMERA')
            # coverage filter (how much of the object a camera must see) + measure again
            flt = header.row(align=True)
            drop = flt.row(align=True)
            drop.ui_units_x = 3.2
            drop.prop(d, "coverage_filter", text="")
            flt.operator("multicamproject.restore_cameras", text="", icon='LOOP_BACK')
            flt.operator("multicamproject.measure_coverage", text="", icon='FILE_REFRESH')
            if body and not core.measured(d):
                r = body.row()
                r.alert = True
                r.label(text="Coverage not measured yet - press Auto or Reload All", icon='ERROR')
            elif body and not rest:
                body.label(text="No camera passes the coverage filter", icon='INFO')
            elif body:
                # a list widget: it scrolls itself to its active row = the soloed camera
                body.template_list("MULTICAMPROJECT_UL_cameras", "", d, "cameras", d, "cam_index",
                                   rows=12)

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
        image_x = m[2]
        split = col.row().split(factor=image_x, align=True)   # X starts under the image field
        tools = split.row(align=True)            # vertex paint VCMix with this camera's color
        tools.scale_x = tools.scale_y = TOOL_SCALE
        for mode, _name, icon in CAM_BRUSHES:
            sub = tools.row(align=True)
            sub.enabled = mode != 'FLOOD' or flood
            op = sub.operator("multicamproject.cam_paint", text="", **_icon(icon))
            op.camera, op.mode = cam.name, mode
        row = split.row(align=True)
        row.scale_y = TOOL_SCALE                 # same height: bottoms line up with the buttons
        row.prop(it, "shift", index=0, text="X", slider=True)
        row.prop(it, "shift", index=1, text="Y", slider=True)
        op = row.operator("multicamproject.load_shift", text="", icon='PASTEDOWN')
        op.camera = cam.name


class MULTICAMPROJECT_UL_cameras(bpy.types.UIList):
    """All Cameras: the cameras not in a slot that pass the coverage filter, most coverage
    first. Its active row is the soloed camera; clicking a name solos that camera."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.camera is None:
            return
        panel = MULTICAMPROJECT_PT_CameraProject
        debug = context.scene.multicamproject_props.debug_mode
        solo = is_solo(context, item.camera)
        col = layout.column()
        col.active = solo or not is_solo(context, context.scene.camera)
        panel._draw_row(col, context.active_object, item, debug,
                        panel._metrics(context, debug, core.slot_count(data), margin=4.2, extra=1),
                        solo, removable=True)   # margin: + list frame, scrollbar

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        slots = core.get_slots(data)
        pattern = self.filter_name.lower()
        flags = [self.bitflag_filter_item
                 if it.camera and it.camera not in slots and core.passes(data, it)
                 and pattern in it.camera.name.lower() else 0
                 for it in items]
        # most coverage first (the same order as the arrow keys step through)
        order = bpy.types.UI_UL_list.sort_items_helper(
            [(i, (-it.coverage, it.camera.name.lower() if it.camera else ""))
             for i, it in enumerate(items)], lambda e: e[1])
        return flags, order


_classes = (MULTICAMPROJECT_PT_CameraProject, MULTICAMPROJECT_UL_cameras)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
