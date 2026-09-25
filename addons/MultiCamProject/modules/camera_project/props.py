"""Per-object data: the cameras that hit the object and the Camera 1-6 slots.

Images are NOT stored here - each image lives on its camera as
camera.data.background_images[0].image (single source of truth).
"""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def _is_camera(self, obj):
    return obj.type == 'CAMERA'


class MULTICAMPROJECT_CamItem(bpy.types.PropertyGroup):
    camera: PointerProperty(type=bpy.types.Object, poll=_is_camera)
    score: FloatProperty(name="Score", description="Coverage x facing of the object in this camera")
    coverage: FloatProperty(name="Coverage", subtype='FACTOR',
                            description="Share of the object inside this camera's frame")


class MULTICAMPROJECT_CamRef(bpy.types.PropertyGroup):
    camera: PointerProperty(type=bpy.types.Object, poll=_is_camera)


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


def _on_slot_count(self, context):
    from . import core
    core.change_slot_count(self.id_data, context.scene)


class MULTICAMPROJECT_ObjectData(bpy.types.PropertyGroup):
    is_setup: BoolProperty(default=False)
    cameras: CollectionProperty(type=MULTICAMPROJECT_CamItem)
    removed: CollectionProperty(
        type=MULTICAMPROJECT_CamRef,
        description="Cameras taken out of this object's list by hand - scoring skips them")
    coverage_filter: EnumProperty(
        name="Coverage", default='50',
        items=[('100', "100%", "Show and auto-pick cameras that see all of the object", 0),
               ('80', ">80%", "Show and auto-pick cameras that see more than 80% of the object", 1),
               ('50', ">50%", "Show and auto-pick cameras that see more than half of the object", 2),
               ('30', ">30%", "Show and auto-pick cameras that see more than 30% of the object", 3),
               ('ALL', "All", "Show and auto-pick every camera that sees the object", 4)],
        description="Show and auto-pick only cameras that see at least this much of the object")
    cam_index: IntProperty(default=-1, update=_on_cam_index,
                           description="Active row of the camera list (the soloed camera)")
    shifts: CollectionProperty(type=MULTICAMPROJECT_CamShift)
    shift_version: IntProperty(default=0, description="0 = shifts stored in pixels (old), 1 = UV range")
    slot_1: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 1")
    slot_2: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 2")
    slot_3: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 3")
    slot_4: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 4")
    slot_5: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 5")
    slot_6: PointerProperty(type=bpy.types.Object, poll=_is_camera, name="Camera 6")
    slot_count: EnumProperty(
        name="Cameras", default='3', update=_on_slot_count,
        items=[(str(n), str(n), f"Project {n} cameras at once"
                + ("" if n == 3 else " - cameras 4-6 paint into VCMix2, on top of 1-3"), n)
               for n in (3, 4, 5, 6)],
        description="How many cameras project at once. Cameras 4-6 paint into VCMix2, "
                    "which covers cameras 1-3 where it is painted")
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


def _on_global_shift(self, context):
    # fires on every step while the slider is dragged
    from . import core
    core.push_global_shift(self.id_data)


class MULTICAMPROJECT_CameraData(bpy.types.PropertyGroup):
    """Per-camera data (on the camera object, so it belongs to the file, not to a mesh).
    A global camera is shared by every object: one UV shift for all of them, and Reload
    All leaves its image and clipping alone."""
    is_global: BoolProperty(
        name="Global", default=False,
        description="Global camera: one shift for every object, image and clipping not "
                    "touched by Reload All. Off = attached to the objects (shift per object)")
    side: StringProperty(
        description="View this camera was created for by Project from Sides (Front, Left, ...). "
                    "Empty = not a sides camera")
    shift: FloatVectorProperty(
        name="Shift", size=2, default=(0.0, 0.0), subtype='XYZ',
        min=-1.0, max=1.0, step=0.1, precision=5, update=_on_global_shift,
        description="Shift of the photo in UV range, shared by every object (global camera): "
                    "-1 = one full photo to the left/down, 1 = to the right/up")


_classes = (MULTICAMPROJECT_CamItem, MULTICAMPROJECT_CamRef, MULTICAMPROJECT_CamShift, MULTICAMPROJECT_ObjectData,
            MULTICAMPROJECT_CameraData)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Object.multicamproject_cam = PointerProperty(type=MULTICAMPROJECT_ObjectData)
    bpy.types.Object.multicamproject_camera = PointerProperty(type=MULTICAMPROJECT_CameraData)


def unregister():
    del bpy.types.Object.multicamproject_camera
    del bpy.types.Object.multicamproject_cam
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
