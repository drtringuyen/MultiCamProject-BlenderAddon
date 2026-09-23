import os

import bpy
from bpy.props import IntProperty, StringProperty

from . import core


def _mesh_poll(context):
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and context.mode == 'OBJECT'


def _setup_poll(context):
    return _mesh_poll(context) and core.data(context.active_object).is_setup


def _report_warnings(op, warnings):
    for w in warnings:
        op.report({'WARNING'}, w)


class MULTICAMPROJECT_OT_Setup(bpy.types.Operator):
    """Build the projection node groups, material and modifier on the active mesh,
    then detect the cameras that see it"""
    bl_idname = "multicamproject.setup"
    bl_label = "Setup Camera Projection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        warnings = core.setup(obj, context.scene)
        _report_warnings(self, warnings)
        n = len(core.data(obj).cameras)
        if n == 0:
            self.report({'WARNING'}, "No camera sees this object")
        else:
            self.report({'INFO'}, f"{n} camera(s) see '{obj.name}'")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ReloadAll(bpy.types.Operator):
    """Reload all camera images from the folder, apply clipping, re-score the cameras,
    re-check Camera 1/2/3 and rewire the node setup and material"""
    bl_idname = "multicamproject.reload_all"
    bl_label = "Reload All"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _setup_poll(context)

    def execute(self, context):
        obj = context.active_object
        _report_warnings(self, core.refresh(obj, context.scene))
        self.report({'INFO'}, f"{len(core.data(obj).cameras)} camera(s) see '{obj.name}'")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_AssignSlot(bpy.types.Operator):
    """Use this camera for projection slot 1/2/3 (swaps if it is already in another slot)"""
    bl_idname = "multicamproject.assign_slot"
    bl_label = "Assign Camera Slot"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty()
    slot: IntProperty(min=1, max=3, default=1)

    @classmethod
    def poll(cls, context):
        return _setup_poll(context)

    @classmethod
    def description(cls, context, props):
        return f"Project this camera as Camera {props.slot}"

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        if not core.image_ok(core.cam_image(cam)):
            self.report({'WARNING'}, f"'{cam.name}' has no loaded image - it will project black")
        core.assign_slot(context.active_object, cam, self.slot, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SoloCamera(bpy.types.Operator):
    """Solo the active object in local view and look through this camera
    (click again to leave solo)"""
    bl_idname = "multicamproject.solo_camera"
    bl_label = "Solo Camera View"

    camera: StringProperty()

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context) and context.area and context.area.type == 'VIEW_3D'

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        obj = context.active_object
        space = context.space_data
        rv3d = space.region_3d
        scene = context.scene

        key = str(space.as_pointer())
        state = _load_state(context, key)
        if state and space.local_view is None:   # left local view by hand
            _restore_camera(state, space)
            state = None
            _save_state(context, key, None)

        if is_solo(context, cam):
            if state:
                _restore_camera(state, space)
            bpy.ops.view3d.localview(frame_selected=False)
            rv3d.view_perspective = 'PERSP'
            if state:
                space.overlay.show_overlays = state["overlays"]
            _save_state(context, key, None)
            return {'FINISHED'}

        if space.local_view is None:
            selected = list(context.selected_objects)
            for o in selected:
                o.select_set(False)
            obj.select_set(True)
            bpy.ops.view3d.localview(frame_selected=False)
            obj.select_set(False)
            for o in selected:
                o.select_set(True)
            state = {"overlays": space.overlay.show_overlays}
        elif state is None:
            state = {"overlays": space.overlay.show_overlays}
        else:
            _restore_camera(state, space)   # switching camera inside solo

        # Camera background images are drawn only when overlays are on and the
        # camera object itself is visible in this (local) view.
        bg = core.bg_entry(cam)
        state.update(cam=cam.name, hidden=cam.hide_get(),
                     depth=bg.display_depth if bg else "")
        _save_state(context, key, state)
        cam.hide_set(False)
        cam.local_view_set(space, True)
        cam.data.show_background_images = True
        if bg:
            bg.show_background_image = True
            bg.display_depth = 'FRONT'   # BACK is hidden behind Material Preview
        space.overlay.show_overlays = True

        scene.camera = cam
        space.camera = cam   # local view looks through the view's own camera
        rv3d.view_perspective = 'CAMERA'
        if not cam.visible_get():
            self.report({'WARNING'}, f"'{cam.name}' is in a hidden collection - "
                                     "its background image cannot be drawn")
        return {'FINISHED'}


# Solo state lives on the window manager (an ID), so it survives addon reloads:
# wm["multicamproject_solo"][<space pointer>] = {"overlays", "cam", "hidden", "depth"}
_WM_KEY = "multicamproject_solo"


def _load_state(context, key):
    states = context.window_manager.get(_WM_KEY)
    if states is None or key not in states:
        return None
    return states[key].to_dict()


def _save_state(context, key, state):
    wm = context.window_manager
    if _WM_KEY not in wm:
        wm[_WM_KEY] = {}
    states = wm[_WM_KEY]
    if state is None:
        if key in states:
            del states[key]
    else:
        states[key] = state


def _restore_camera(state, space):
    """Undo what solo changed on the previously soloed camera."""
    cam = bpy.data.objects.get(state.pop("cam", ""))
    if cam is None:
        return
    bg = core.bg_entry(cam)
    if bg and state.get("depth"):
        bg.display_depth = state["depth"]
    if space.local_view is not None:
        cam.local_view_set(space, False)
    cam.hide_set(state.get("hidden", False))


def is_solo(context, cam):
    space = context.space_data
    if space is None or space.type != 'VIEW_3D' or space.local_view is None:
        return False
    return context.scene.camera == cam and space.region_3d.view_perspective == 'CAMERA'


class MULTICAMPROJECT_OT_LoadCamImage(bpy.types.Operator):
    """Pick the photo for this camera (its background image)"""
    bl_idname = "multicamproject.load_cam_image"
    bl_label = "Load Camera Image"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty(options={'HIDDEN'})
    filepath: StringProperty(subtype='FILE_PATH')
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _setup_poll(context)

    def invoke(self, context, event):
        cam = bpy.data.objects.get(self.camera)
        folder = core.data(context.active_object).image_folder
        img = core.cam_image(cam) if cam else None
        if img and img.filepath:
            self.filepath = bpy.path.abspath(img.filepath)
        elif folder:
            self.filepath = os.path.join(bpy.path.abspath(folder), "")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, "No camera or file")
            return {'CANCELLED'}
        core.set_cam_image_path(cam, self.filepath)
        obj = context.active_object
        if cam in core.get_slots(core.data(obj)):
            core.apply_slots(obj, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LoadShift(bpy.types.Operator):
    """Load this object's pixel shift onto the camera's background image offset
    (use when the camera is shared with other objects)"""
    bl_idname = "multicamproject.load_shift"
    bl_label = "Load Shifting"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty()

    @classmethod
    def poll(cls, context):
        return _setup_poll(context)

    def execute(self, context):
        obj = context.active_object
        cam = bpy.data.objects.get(self.camera)
        it = core.shift_item(obj, cam) if cam else None
        if it is None:
            self.report({'ERROR'}, f"No shift stored for '{self.camera}'")
            return {'CANCELLED'}
        core.push_shift(obj, it, context.scene, to_camera=True)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_BakeViewMix(bpy.types.Operator):
    """Apply the projection modifier (bakes UV_cam1/2/3 + VCMix into the mesh) and
    re-add it with the same settings, so the baked VCMix becomes the base for the
    next blend (Original Blend)"""
    bl_idname = "multicamproject.bake_view_mix"
    bl_label = "Bake View Mix"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _setup_poll(context) and core.get_modifier(context.active_object) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Bake View Mix",
            message="Apply the projection into the mesh and re-add the modifier?",
            confirm_text="Bake")

    def execute(self, context):
        obj = context.active_object
        me = obj.data
        mod = core.get_modifier(obj)
        idx = list(obj.modifiers).index(mod)
        if idx != 0:
            self.report({'ERROR'}, f"'{mod.name}' must be first in the modifier stack "
                                   "(apply or move the modifiers above it)")
            return {'CANCELLED'}
        if me.shape_keys:
            self.report({'ERROR'}, "Mesh has shape keys - cannot apply a modifier")
            return {'CANCELLED'}
        if me.users > 1:
            self.report({'ERROR'}, "Mesh data is shared by several objects - make it single user first")
            return {'CANCELLED'}
        d = core.data(obj)
        slots = core.get_slots(d)
        if None in slots or not all(core.image_ok(core.cam_image(c)) for c in slots):
            self.report({'WARNING'}, "Some slots are empty or without image - baked areas may be black")

        keep = {k: core.get_input(mod, k) for k in ("Mode", "Original Blend", "Occlusion")}
        name = mod.name
        core.remove_drivers(obj, mod)
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=name)

        new = core.ensure_modifier(obj)
        new.name = name
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=new.name, index=idx)
        for k, v in keep.items():
            core.set_input(new, k, v)
        core.apply_slots(obj, context.scene)
        self.report({'INFO'}, "View mix baked - raise Original Blend to blend on top of it")
        return {'FINISHED'}


_classes = (
    MULTICAMPROJECT_OT_Setup,
    MULTICAMPROJECT_OT_ReloadAll,
    MULTICAMPROJECT_OT_AssignSlot,
    MULTICAMPROJECT_OT_SoloCamera,
    MULTICAMPROJECT_OT_LoadCamImage,
    MULTICAMPROJECT_OT_LoadShift,
    MULTICAMPROJECT_OT_BakeViewMix,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
