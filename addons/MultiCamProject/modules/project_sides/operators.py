import bpy
from bpy.props import BoolProperty

from . import core


class MULTICAMPROJECT_OT_ProjectSides(bpy.types.Operator):
    """0A: create the missing sides cameras and reset all of them to the settings (gear):
    sheet image, camera type, Height, Distance, Focal Length / Ortho Scale, around the active
    object's origin. Then 0B runs on the object (Setup, or Reload All when set up already)"""
    bl_idname = "multicamproject.project_sides"
    bl_label = "Project from Sides"
    bl_options = {'REGISTER', 'UNDO'}

    reframe: BoolProperty(
        name="Reframe", options={'SKIP_SAVE'},
        description="Reset Height, Distance, Focal Length and Ortho Scale to the defaults "
                    "from the active object, then project")

    @classmethod
    def description(cls, context, props):
        if props.reframe:
            return ("Defaults from the active object: Height = half the Z of its highest point, "
                    "Distance = Height, Focal Length / Ortho Scale fit ground to top into the "
                    "sheet's height")
        return cls.__doc__

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not core.camera_project_ready():
            cls.poll_message_set("Needs the Camera Project module")
            return False
        if obj is None or obj.type != 'MESH' or context.mode != 'OBJECT':
            cls.poll_message_set("Object Mode, a mesh active")
            return False
        if core.settings(context.scene).image is None:
            cls.poll_message_set("Pick the sides sheet image first (gear)")
            return False
        return True

    def execute(self, context):
        s = core.settings(context.scene)
        from ..camera_project import core as cp
        if not cp.image_ok(s.image):
            self.report({'ERROR'}, f"Image '{s.image.name}' has no pixels or its file is missing")
            return {'CANCELLED'}
        cams, res_changed = core.project(context.scene, context.active_object, self.reframe)
        r = context.scene.render
        if res_changed:
            self.report({'WARNING'}, f"Scene resolution set to {r.resolution_x} x {r.resolution_y} "
                                     "(the sheet's aspect) - it applies to every camera")
        else:
            self.report({'INFO'}, f"{len(cams)} sides cameras reset in '{core.COLLECTION}'")
        obj = context.active_object             # then 0B on the object
        warnings = (cp.refresh(obj, context.scene) if cp.data(obj).is_setup
                    else cp.setup(obj, context.scene))
        for w in warnings:
            self.report({'WARNING'}, w)
        return {'FINISHED'}


_classes = (MULTICAMPROJECT_OT_ProjectSides,)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
