"""Per-object data: the cameras that hit the object and the Camera 1/2/3 slots.

Images are NOT stored here - each image lives on its camera as
camera.data.background_images[0].image (single source of truth).
"""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def _is_camera(self, obj):
    return obj.type == 'CAMERA'


class MULTICAMPROJECT_CamItem(bpy.types.PropertyGroup):
    camera: PointerProperty(type=bpy.types.Object, poll=_is_camera)
    score: FloatProperty(name="Score", description="Coverage x facing of the object in this camera")


def _on_shift(self, context):
    # fires on every step while the slider is dragged
    from . import core
    core.push_shift(self.id_data, self, context.scene, to_camera=True)


class MULTICAMPROJECT_CamShift(bpy.types.PropertyGroup):
    """Per-object UV shift of one camera's photo. Never cleared by Reload All."""
    camera: PointerProperty(type=bpy.types.Object, poll=_is_camera)
    shift: FloatVectorProperty(
        name="Shift", size=2, default=(0.0, 0.0), subtype='XYZ',
        min=-1.0, max=1.0, step=0.1, precision=5, update=_on_shift,
        description="Shift of the photo in UV range: -1 = one full photo to the left/down, "
                    "1 = to the right/up (moves UV_camN and the camera background offset "
                    "together). Shift+drag for finer steps")


def _on_cam_index(self, context):
    """Clicking a row of the All Cameras list solos that camera (the soloed camera
    is the list's active row, so the list scrolls to it)."""
    from . import operators
    if not 0 <= self.cam_index < len(self.cameras):
        return
    cam = self.cameras[self.cam_index].camera
    if cam is None or operators.is_solo(context, cam):
        return      # also stops the solo operator's own sync from re-entering
    if operators.MULTICAMPROJECT_OT_SoloCamera.poll(context):
        bpy.ops.multicamproject.solo_camera(camera=cam.name)


class MULTICAMPROJECT_ObjectData(bpy.types.PropertyGroup):
    is_setup: BoolProperty(default=False)
    cameras: CollectionProperty(type=MULTICAMPROJECT_CamItem)
    cam_index: IntProperty(default=-1, update=_on_cam_index,
                           description="Active row of the camera list (the soloed camera)")
    shifts: CollectionProperty(type=MULTICAMPROJECT_CamShift)
    shift_version: IntProperty(default=0, description="0 = shifts stored in pixels (old), 1 = UV range")
    slot_1: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 1")
    slot_2: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 2")
    slot_3: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 3")
    user_picked: BoolProperty(
        default=False,
        description="True once the user chose a slot - auto-fill then keeps their choice")
    material: PointerProperty(type=bpy.types.Material)
    image_folder: StringProperty(
        name="Folder", subtype='DIR_PATH',
        description="Folder holding all camera photos, matched by camera name")
    clip_start: FloatProperty(name="Start", default=0.01, min=0.0001, unit='LENGTH',
                              description="Clip start applied to every camera on Reload All")
    clip_end: FloatProperty(name="End", default=100.0, min=0.001, unit='LENGTH',
                            description="Clip end applied to every camera on Reload All")


_classes = (MULTICAMPROJECT_CamItem, MULTICAMPROJECT_CamShift, MULTICAMPROJECT_ObjectData)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Object.multicamproject_cam = PointerProperty(type=MULTICAMPROJECT_ObjectData)


def unregister():
    del bpy.types.Object.multicamproject_cam
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
