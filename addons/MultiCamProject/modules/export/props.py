"""Export settings, on the Scene."""
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty


def _on_row(self, context):
    """Clicking a row of the status list selects the object and frames it."""
    from . import operators
    operators.select_and_frame_row(context, self)


def _on_prefix(self, context):
    from . import live
    live.schedule()


def _get_short(self):
    from ..baking import naming
    sc = naming.scheme()
    p = naming.parse(self.name, sc) if sc else None
    return p[1] if p else self.name


def _set_short(self, value):
    """Typing only <name>: the object keeps its <scene>.<##>_ part."""
    from ..baking import naming
    from . import fixes, live
    sc = naming.scheme()
    p = naming.parse(self.name, sc) if sc else None
    new = naming.full_name(sc, p[0], value) if p else naming.clean_name(value)
    if new != self.name:
        fixes.rename_object(self, new)
    live.schedule()


class MULTICAMPROJECT_ExportSettings(bpy.types.PropertyGroup):
    name_prefix: StringProperty(
        name="Name Prefix", default="", update=_on_prefix,
        description="e.g. 00_30stBR. Objects become ENV_00_30stBR.<##>_<name>, materials "
                    "MAT_00_30stBR.<##>_<name>, textures ALB_/NOR_00_30stBR.<##>_<name>, the FBX "
                    "ENV_00_30stBR.fbx. You name only <name>; ## is numbered for you. Saved in the "
                    ".blend. Empty = clean names only")
    folder: StringProperty(name="Folder", default="//Export/", subtype='DIR_PATH',
                           description="Folder for the FBX, Textures/, Editor/ and the report")
    split: EnumProperty(
        name="Files", default='ONE',
        items=(('ONE', "One FBX", "All objects in one FBX"),
               ('PER_OBJECT', "One per Object", "One FBX per object, named after it")))
    write_cs: BoolProperty(name="Unity Script", default=True,
                           description="Write Editor/MCPTexturePostprocessor.cs: first import sets "
                                       "ALB/NOR to max 8192 and NOR to Normal map")
    write_report: BoolProperty(name="Report", default=True,
                               description="Write export_report.txt (objects, stages, issues, timings)")
    active_index: IntProperty(default=-1, update=_on_row)
    show_scenes: BoolProperty(name="Details", default=False)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_ExportSettings)
    bpy.types.Scene.multicamproject_export = PointerProperty(type=MULTICAMPROJECT_ExportSettings)
    bpy.types.Object.multicamproject_short_name = StringProperty(
        name="Name", get=_get_short, set=_set_short, options={'SKIP_SAVE'},
        description="The object's own name - the add-on keeps the <scene>.<##>_ in front")


def unregister():
    del bpy.types.Object.multicamproject_short_name
    del bpy.types.Scene.multicamproject_export
    bpy.utils.unregister_class(MULTICAMPROJECT_ExportSettings)
