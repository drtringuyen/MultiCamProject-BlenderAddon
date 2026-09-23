import bpy
from bpy.props import BoolProperty, StringProperty, IntProperty


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


def register():
    bpy.utils.register_class(MULTICAMPROJECTProperties)
    bpy.types.Scene.multicamproject_props = bpy.props.PointerProperty(
        type=MULTICAMPROJECTProperties
    )


def unregister():
    del bpy.types.Scene.multicamproject_props
    bpy.utils.unregister_class(MULTICAMPROJECTProperties)
