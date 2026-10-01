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


def auto_names(scene=None):
    """Auto Names on: names are checked, kept in line live and the list is sorted by ##."""
    return (scene or bpy.context.scene).multicamproject_export.auto_names


_typed = {}        # Auto Names off: object session_uid -> when its ## was typed


def _on_auto_names(self, context):
    """Back on: number 00 -> n in the order of the ## typed meanwhile and bring MCP_/MAT_/
    ALB_/NOR_ and the files along. Two objects on one ##: the one typed last goes first
    (it was put there), the other moves down."""
    from ..baking import cache, common, naming
    from . import fixes
    if self.auto_names:
        objs = common.export_objects(self.id_data)
        sc = naming.scheme(self.id_data)
        if objs and sc is None:
            log = fixes.rename_all(objs, sc)
        elif objs:
            def key(o):
                p = naming.parse(o.name, sc)
                return (p[0] if p else 10 ** 6, -_typed.get(o.session_uid, -1), o.name)
            log = fixes.renumber(objs, sc, sorted(objs, key=key))
        else:
            log = []
        for sev, text in log:
            if sev != 'INFO':
                print(f"[MultiCamProject] {text}")
    _typed.clear()
    cache.clear()


def _quick_rename(obj, new, typed=False):
    """Auto Names off: the object only - MCP_/MAT_/textures follow when it is on again."""
    if typed:
        _typed[obj.session_uid] = len(_typed)
    if new != obj.name:
        obj.name = new


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
    if not auto_names():
        _quick_rename(self, new)
        return
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
    if sc is None or self not in objs:
        return
    if not auto_names(scene):           # only this object's ##: no one moves
        p = naming.parse(self.name, sc)
        _quick_rename(self, naming.full_name(sc, value, p[1] if p else naming.short_name(self.name)),
                      typed=True)       # the same ## again still counts: it goes first there
        cache.clear()
        return
    if value == _get_index(self):
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
    auto_names: BoolProperty(
        name="Auto Names", default=True, update=_on_auto_names,
        description="On: names are checked and kept in line live, typing a ## moves the others "
                    "down, the list is sorted by ##. Off: type ## and names freely (only the "
                    "object is renamed, nothing moves). Turning it on again numbers 00 -> n in "
                    "the typed order and renames MCP_/MAT_/ALB_/NOR_ and their files")
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
