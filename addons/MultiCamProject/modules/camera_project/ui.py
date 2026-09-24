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

        header, body = layout.panel("multicamproject_cameras", default_closed=False)
        header.label(text=f"Cameras ({len(d.cameras)})", icon='OUTLINER_OB_CAMERA')
        if body:
            self._draw_box(context, body.box(), obj, d, mod)

        layout.operator("multicamproject.bake_view_mix", icon='RENDER_STILL')

    def _draw_box(self, context, box, obj, d, mod):
        row = box.row(align=True)
        row.prop(d, "image_folder")
        row.operator("multicamproject.reload_all", text="", icon='FILE_REFRESH')
        row = box.row(align=True)
        row.label(text="Clip")
        row.prop(d, "clip_start")
        row.prop(d, "clip_end")

        col = box.column(align=True)
        col.prop(core.input_socket(mod, "Mode"), "value", text="Mode")
        col.prop(core.input_socket(mod, "Previous Bake"), "value", text="Previous Bake")
        scan = d.material.node_tree.nodes.get(core.ORIGINAL_SCAN) if d.material else None
        if scan:
            # a Value node has no 0..1 range, so no slider (its bar would be wrong)
            col.prop(scan.outputs[0], "default_value", text="Original Scan")
        col.prop(core.input_socket(mod, "Occlusion"), "value", text="Occlusion")

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
        sub = header.row()
        sub.ui_units_x = 3
        sub.prop(d, "slot_count", text="")
        if body:
            for item in top:
                self._draw_block(context, body, obj, item, debug, solo_cam, shift=True, flood=flood)
        if rest:
            header, body = box.panel("multicamproject_all_cams", default_closed=False)
            header.label(text=f"All Cameras ({len(rest)})", icon='OUTLINER_OB_CAMERA')
            if body:
                # a list widget: it scrolls itself to its active row = the soloed camera
                body.template_list("MULTICAMPROJECT_UL_cameras", "", d, "cameras", d, "cam_index",
                                   rows=12)

    @staticmethod
    def _metrics(context, debug, n, margin=2.8):
        """Split factors for a camera block. Blender scales ui_units_x down in narrow
        panels, so fixed widths are made with splits from the region's pixel width:
        name and slot buttons keep their size, the image field absorbs the rest."""
        unit = 20 * context.preferences.system.ui_scale
        avail = max(1.0, context.region.width - margin * unit)   # boxes, panel, list frame
        per = 1.4 if n == 3 else 1.15                             # width of one slot button
        slots_f = min(0.6, (n + 1) * per * unit / avail)         # 1..n + global toggle, same width
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
    def _draw_row(col, obj, item, debug, m, solo):
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
        nsplit.label(text=f"{cam.name} {item.score:.2f}" if debug else cam.name)
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
        slots = right.grid_flow(row_major=True, columns=count + 1, even_columns=True, align=True)
        current = core.slot_of(obj, cam)
        for n in range(1, count + 1):
            op = slots.operator("multicamproject.assign_slot", text=str(n), depress=current == n)
            op.camera = cam.name
            op.slot = n
        glob = core.is_global(cam)
        op = slots.operator("multicamproject.toggle_global", text="",
                            icon='WORLD' if glob else 'OBJECT_DATA', depress=glob)
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
    """All Cameras: the cameras not in a slot, alphabetical. Its active row is the
    soloed camera; clicking a name solos that camera."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.camera is None:
            return
        panel = MULTICAMPROJECT_PT_CameraProject
        debug = context.scene.multicamproject_props.debug_mode
        solo = is_solo(context, item.camera)
        col = layout.column()
        col.active = solo or not is_solo(context, context.scene.camera)
        panel._draw_row(col, context.active_object, item, debug,
                        panel._metrics(context, debug, core.slot_count(data), margin=4.2),
                        solo)   # + list frame, scrollbar

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        slots = core.get_slots(data)
        pattern = self.filter_name.lower()
        flags = [self.bitflag_filter_item
                 if it.camera and it.camera not in slots and pattern in it.camera.name.lower() else 0
                 for it in items]
        order = bpy.types.UI_UL_list.sort_items_helper(
            [(i, it.camera.name.lower() if it.camera else "") for i, it in enumerate(items)],
            lambda e: e[1])
        return flags, order


_classes = (MULTICAMPROJECT_PT_CameraProject, MULTICAMPROJECT_UL_cameras)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
