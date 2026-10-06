import bpy
from bpy.props import BoolProperty, StringProperty, IntProperty, PointerProperty


class MULTICAMPROJECTProperties(bpy.types.PropertyGroup):
    """Global properties shared across all modules"""

    debug_mode: BoolProperty(
        name="Debug Mode",
        description="Show extra-info-label and debug information",
        default=False
    )

    last_build_time: StringProperty(
        name="Last Build Time",
        description="Timestamp of last install.py run",
        default="Never"
    )

    addon_version: StringProperty(
        name="Version",
        description="Current addon version",
        default="0.0.1"
    )

    # collection roles (roles.py): empty = found by the usual name
    objects_collection: PointerProperty(
        type=bpy.types.Collection, name="Objects",
        description="The collection of the objects being worked on. Empty = the one named "
                    "OBJECTS")
    originals_collection: PointerProperty(
        type=bpy.types.Collection, name="Original Mesh",
        description="Where Remesh puts the originals (high polys). Empty = the one named "
                    "Original Mesh")
    export_collection: PointerProperty(
        type=bpy.types.Collection, name="Export",
        description="The collection 07 exports and lists. Empty = the one named EXPORT")
    cameras_collection: PointerProperty(
        type=bpy.types.Collection, name="Cameras",
        description="The camera collection. Empty = every collection holding only cameras")


def register():
    bpy.utils.register_class(MULTICAMPROJECTProperties)
    bpy.types.Scene.multicamproject_props = bpy.props.PointerProperty(
        type=MULTICAMPROJECTProperties
    )


def unregister():
    del bpy.types.Scene.multicamproject_props
    bpy.utils.unregister_class(MULTICAMPROJECTProperties)
