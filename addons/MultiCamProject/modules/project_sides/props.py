"""Project from Sides settings - on the Scene, so they belong to the file (the sides
cameras are global, not tied to one object)."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       PointerProperty)


def _on_change(self, context):
    # fires on every step while a field is dragged: the cameras follow live
    from . import core
    core.place(self)


class MULTICAMPROJECT_SidesSettings(bpy.types.PropertyGroup):
    image: PointerProperty(
        type=bpy.types.Image, name="Sheet",
        description="Reference sheet with the views side by side (Left, Right, Front, Back)")
    cam_type: EnumProperty(
        name="Camera Type", default='ORTHO', update=_on_change,
        items=(('ORTHO', "Orthographic", "Orthographic cameras: size set by Ortho Scale", 'VIEW_ORTHO', 0),
               ('PERSP', "Perspective", "Perspective cameras: size set by Focal Length and Distance",
                'VIEW_PERSPECTIVE', 1)))
    height: FloatProperty(
        name="Height", default=1.0, unit='LENGTH', update=_on_change,
        description="Height of all sides cameras above the ground (world Z). "
                    "Default: half the Z of the object's highest point")
    distance: FloatProperty(
        name="Distance", default=1.0, min=0.001, unit='LENGTH', update=_on_change,
        description="Horizontal distance of all sides cameras from the center. Default: Height")
    lens: FloatProperty(
        name="Focal Length", default=50.0, min=1.0, unit='CAMERA', update=_on_change,
        description="Focal length of all perspective sides cameras. "
                    "Default: fits ground to top of the object into the sheet's height")
    ortho_scale: FloatProperty(
        name="Ortho Scale", default=6.0, min=0.001, unit='LENGTH', update=_on_change,
        description="Orthographic scale (frame width) of all orthographic sides cameras. "
                    "Default: fits ground to top of the object into the sheet's height")
    center: FloatVectorProperty(
        name="Center", size=2, subtype='XYZ', unit='LENGTH',
        description="X/Y the cameras look at: the active object's origin at the last Project")
    placed: BoolProperty(
        default=False, description="False until the first Project fills in the defaults")


def register():
    bpy.utils.register_class(MULTICAMPROJECT_SidesSettings)
    bpy.types.Scene.multicamproject_sides = PointerProperty(type=MULTICAMPROJECT_SidesSettings)


def unregister():
    del bpy.types.Scene.multicamproject_sides
    bpy.utils.unregister_class(MULTICAMPROJECT_SidesSettings)
