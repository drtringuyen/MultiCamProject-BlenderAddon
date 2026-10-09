import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)

from .quality import ITEMS as QUALITY_ITEMS


def _on_quality(self, context):
    from . import quality
    quality.apply(context.scene)


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

    new_object_name: StringProperty(
        name="New Object Name", default="",
        description="The window buttons of 0C / 0D: the new object becomes ENV_<prefix>."
                    "<next ##>_<this name>, its work window saved as <this name>.blend in the "
                    "Bake Folder")
    quality: EnumProperty(
        name="Quality", default='FINAL',
        items=QUALITY_ITEMS,
        update=lambda self, context: _on_quality(self, context),
        description="Preview: the add-on's materials as flat color, no lighting or normal "
                    "maps, textures capped at 2K. Final: full quality, up to 8K")
    file_io_done: BoolProperty(
        name="File IO Set Up", default=False,
        description="Setup File IO ran in this file (gate.py: the add-on unlocks)")

    original_normal_strength: FloatProperty(
        name="Original Normal", default=1.0, min=0.0, soft_max=5.0,
        update=lambda self, context: _on_original_normal(self, context),
        description="Normal strength of the originals' own materials (Normal Map / Bump of "
                    "the scan materials in Objects and Original Mesh). The add-on's MCP_ / "
                    "MAT_ are not touched")

    # role folders (folders.py; the bake and export folders live in their modules)
    scan_textures_folder: StringProperty(
        name="Scan Textures", default="//00.Scan/01.FBX/", subtype='DIR_PATH',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
        update=lambda self, context: _on_scan(self, context),
        description="Folder holding the original scan textures of the objects. Missing "
                    "textures are relinked from it on their own. Reload points "
                    "every texture of the objects' materials at the file of the same name here")
    photos_folder: StringProperty(
        name="Camera Photos", default="//00.Scan/00.Photos/", subtype='DIR_PATH',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
        update=lambda self, context: _on_photos(self, context),
        description="Folder holding all camera photos. Reload relinks every camera's "
                    "background photo from it; every object with Camera Projection uses it "
                    "(new setups too) and gets a Reload All")


def _on_original_normal(props, context):
    from . import folders
    folders.set_original_normal(props.id_data, context.view_layer, props.original_normal_strength)


def _on_scan(props, context):
    from . import folders
    folders.relink_missing(props.id_data)


def _on_photos(props, context):
    from . import folders
    folders.push_photos(props.id_data)
    folders.relink_missing(props.id_data)


def register():
    bpy.utils.register_class(MULTICAMPROJECTProperties)
    bpy.types.Scene.multicamproject_props = bpy.props.PointerProperty(
        type=MULTICAMPROJECTProperties
    )


def unregister():
    del bpy.types.Scene.multicamproject_props
    bpy.utils.unregister_class(MULTICAMPROJECTProperties)
