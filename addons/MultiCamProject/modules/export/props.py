"""Export settings, on the Scene."""
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty


def _on_row(self, context):
    """Clicking a row of the status list selects the object and frames it."""
    from . import operators
    operators.select_and_frame_row(context, self)


def _get_export_view(self):
    from ..baking import common, gn_final
    objs = common.export_objects(self.id_data)
    return 1 if objs and all(gn_final.is_final(o) for o in objs) else 0


def _set_export_view(self, value):
    from ..baking import common, gn_final
    scene = self.id_data
    for o in common.export_objects(scene):
        gn_final.set_final(o, scene, bool(value))


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


def _get_index(self):
    from ..baking import naming
    sc = naming.scheme()
    p = naming.parse(self.name, sc) if sc else None
    return p[0] if p else 0


def _get_order(self):
    return str(_get_index(self))


def _set_order(self, value):
    """The typed ##: anything that is not a number 0-99 is ignored."""
    try:
        n = int(value.strip())
    except ValueError:
        return
    if 0 <= n <= 99:
        _set_index(self, n)


def _set_index(self, value):
    """Typing a ## moves the object there; the ones from there on move one down."""
    from ..baking import cache, common, naming
    from . import fixes
    scene = bpy.context.scene
    sc = naming.scheme(scene)
    objs = common.export_objects(scene)
    if sc is None or self not in objs or value == _get_index(self):
        return
    for sev, text in fixes.move_to(objs, sc, self, value):
        if sev != 'INFO':
            print(f"[MultiCamProject] {text}")
    cache.clear()


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
    view: EnumProperty(
        name="View", get=_get_export_view, set=_set_export_view, options={'SKIP_SAVE'},
        items=(('PROJECTION', "Processing", "Every EXPORT object shows its Processing material "
                                            "(MCP_): the camera projection setup",
                'CAMERA_DATA', 0),
               ('FINAL', "Final", "Every EXPORT object shows the baked result (what the FBX gets). "
                                  "Export switches to Final by itself", 'SHADING_TEXTURE', 1)),
        description="Processing material or Final baked result for every EXPORT object")
    show_scenes: BoolProperty(name="Details", default=False)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_ExportSettings)
    bpy.types.Scene.multicamproject_export = PointerProperty(type=MULTICAMPROJECT_ExportSettings)
    bpy.types.Object.multicamproject_short_name = StringProperty(
        name="Name", get=_get_short, set=_set_short, options={'SKIP_SAVE'},
        description="The object's own name - the add-on keeps the <scene>.<##>_ in front")
    # text, not an IntProperty: typed only - no arrows, no dragging it to another number
    bpy.types.Object.multicamproject_export_index = StringProperty(
        name="Order", get=_get_order, set=_set_order, options={'SKIP_SAVE'},
        description="The object's ## in the EXPORT order. Type a number to move it there - "
                    "the objects from there on move one down, then all are numbered 00 -> n")


def unregister():
    del bpy.types.Object.multicamproject_export_index
    del bpy.types.Object.multicamproject_short_name
    del bpy.types.Scene.multicamproject_export
    bpy.utils.unregister_class(MULTICAMPROJECT_ExportSettings)
