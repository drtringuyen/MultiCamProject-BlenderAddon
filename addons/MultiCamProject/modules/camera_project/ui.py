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

        props = context.scene.multicamproject_props
        for item in d.cameras:
            cam = item.camera
            if cam is None:
                continue
            row = box.row(align=True)
            solo = is_solo(context, cam)
            op = row.operator("multicamproject.solo_camera", text="",
                              icon='HIDE_OFF' if solo else 'HIDE_ON', depress=solo)
            op.camera = cam.name

            split = row.split(factor=0.3, align=True)
            label = f"{cam.name}  {item.score:.2f}" if props.debug_mode else cam.name
            split.label(text=label)
            rest = split.row(align=True)

            bg = core.bg_entry(cam)
            if bg:
                rest.prop(bg, "image", text="")
            else:
                rest.label(text="(no background)")

            ok = core.image_ok(core.cam_image(cam))
            sub = rest.row(align=True)
            sub.alert = not ok
            op = sub.operator("multicamproject.load_cam_image", text="", icon='FILE_FOLDER')
            op.camera = cam.name

            slots = rest.row(align=True)
            current = core.slot_of(obj, cam)
            for n in (1, 2, 3):
                op = slots.operator("multicamproject.assign_slot", text=str(n), depress=current == n)
                op.camera = cam.name
                op.slot = n


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_CameraProject)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_CameraProject)
