import bpy
from bpy.props import BoolProperty

from . import core


class MULTICAMPROJECT_OT_ProjectSides(bpy.types.Operator):
    """Create the missing sides cameras and reset all of them to the panel: sheet image,
    camera type, Height, Distance, Focal Length / Ortho Scale, around the active object's
    origin. They are global cameras - Setup / Reload All then use them like any other"""
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
        return (core.camera_project_ready() and obj is not None and obj.type == 'MESH'
                and context.mode == 'OBJECT' and core.settings(context.scene).image is not None)

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
        return {'FINISHED'}


_classes = (MULTICAMPROJECT_OT_ProjectSides,)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
