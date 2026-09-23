import bpy

from . import core
from .operators import is_solo


class MULTICAMPROJECT_PT_CameraProject(bpy.types.Panel):
    bl_label = "Camera Project"
    bl_idname = "MULTICAMPROJECT_PT_camera_project"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 0

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
        box.prop(d, "image_folder")
        row = box.row(align=True)
        row.label(text="Clip")
        row.prop(d, "clip_start")
        row.prop(d, "clip_end")
        box.operator("multicamproject.reload_all", icon='FILE_REFRESH')

        col = box.column(align=True)
        col.prop(core.input_socket(mod, "Mode"), "value", text="Mode")
        col.prop(core.input_socket(mod, "Original Blend"), "value", text="Original Blend")
        col.prop(core.input_socket(mod, "Occlusion"), "value", text="Occlusion")

        box.separator(type='LINE')
        if not d.cameras:
            box.label(text="No camera sees this object - Reload All", icon='INFO')
            return

        debug = context.scene.multicamproject_props.debug_mode
        top, rest = core.display_order(obj)
        for item in top:
            self._draw_row(context, box, obj, item, debug)
            self._draw_shift(box, obj, item.camera)
        if top and rest:
            box.separator(type='LINE')
        for item in rest:
            self._draw_row(context, box, obj, item, debug)

    def _draw_row(self, context, box, obj, item, debug):
        # Blender scales ui_units_x down proportionally in narrow panels, so fixed
        # widths are made with splits computed from the region's pixel width:
        # name and slot buttons keep their size, the image field absorbs the rest.
        cam = item.camera
        unit = 20 * context.preferences.system.ui_scale
        avail = max(1.0, context.region.width - 2.2 * unit)   # box + panel margins
        slots_f = min(0.6, 4.8 * unit / avail)
        split = box.row().split(factor=1.0 - slots_f, align=True)
        left, right = split.row(align=True), split.row(align=True)

        solo = is_solo(context, cam)
        op = left.operator("multicamproject.solo_camera", text="",
                           icon='HIDE_OFF' if solo else 'HIDE_ON', depress=solo)
        op.camera = cam.name

        left_w = max(1.0, avail * (1.0 - slots_f) - unit)       # minus eye button
        name_f = min(0.6, (5.5 if debug else 4.5) * unit / left_w)
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

        slots = right.row(align=True)
        current = core.slot_of(obj, cam)
        for n in (1, 2, 3):
            op = slots.operator("multicamproject.assign_slot", text=str(n), depress=current == n)
            op.camera = cam.name
            op.slot = n

    def _draw_shift(self, box, obj, cam):
        it = core.shift_item(obj, cam)
        if it is None:
            return
        row = box.row(align=True)
        row.separator(factor=2.0)
        lab = row.row(align=True)
        lab.ui_units_x = 2.5
        lab.label(text="Shift")
        row.prop(it, "shift", index=0, text="X", slider=True)
        row.prop(it, "shift", index=1, text="Y", slider=True)
        op = row.operator("multicamproject.load_shift", text="", icon='PASTEDOWN')
        op.camera = cam.name


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_CameraProject)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_CameraProject)
