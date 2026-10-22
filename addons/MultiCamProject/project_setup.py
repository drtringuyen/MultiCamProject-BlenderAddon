"""Project Setup: one popup listing everything Setup File IO does, each step with its own tick,
confirmed with OK. Opened by Setup File IO (the start gate) and by the Project Setup button
of Collections + Folders.

Collections + Folders: the four roles of IO Folders & Collections, each collection auto
detected (picked, else found by its usual name) or picked here, with a Rename to the add-on's
own name (OBJECTS, Original Mesh, EXPORT, CAMERAS), and its folder (detected, or typed here).
Applied on OK like the steps.

File settings (B1-B3): autosave, undo steps and undo memory are Blender preferences (one
value for every file). Here they belong to the file: the values are kept in the scene and
set when the file opens; a file without them gets the user's own values back. Those are
saved once, before the first change, in <config>/multicamproject/user_prefs.json - Blender
may auto-save the preferences with a file's values in them.
"""
import json
import os

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import folders, gate, roles

_PREFS = (("autosave_minutes", "filepaths", "auto_save_time"),
          ("undo_steps", "edit", "undo_steps"),
          ("undo_memory_mb", "edit", "undo_memory_limit"))


# ---------------------------------------------------------------- the file's preferences

def _snapshot_path():
    folder = bpy.utils.user_resource('CONFIG', path="multicamproject", create=True)
    return os.path.join(folder, "user_prefs.json")


def _read_snapshot():
    try:
        with open(_snapshot_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_snapshot():
    """The user's own values, once: before a file first changes them."""
    if _read_snapshot() is not None:
        return
    p = bpy.context.preferences
    data = {attr: getattr(getattr(p, sect), attr) for _k, sect, attr in _PREFS}
    data["use_auto_save_temporary_files"] = p.filepaths.use_auto_save_temporary_files
    try:
        with open(_snapshot_path(), "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"[MultiCamProject] Project Setup: user preferences not saved: {e}")


def _set(sect, attr, value):
    holder = getattr(bpy.context.preferences, sect)
    if getattr(holder, attr) != value:
        setattr(holder, attr, value)


def apply_file_prefs(scene):
    """The file's autosave / undo values, or the user's own when the file has none."""
    s = getattr(scene, "multicamproject_setup", None) if scene else None
    if s is not None and s.use_file_prefs:
        _save_snapshot()
        for key, sect, attr in _PREFS:
            _set(sect, attr, getattr(s, key))
        _set("filepaths", "use_auto_save_temporary_files", True)
        return
    snap = _read_snapshot()
    if snap is None:
        return
    for _k, sect, attr in _PREFS:
        if attr in snap:
            _set(sect, attr, snap[attr])
    if "use_auto_save_temporary_files" in snap:
        _set("filepaths", "use_auto_save_temporary_files", snap["use_auto_save_temporary_files"])


@persistent
def _on_load(_):
    try:
        apply_file_prefs(bpy.context.scene)
    except Exception as e:      # never block loading
        print(f"[MultiCamProject] Project Setup preferences skipped: {e}")


class MULTICAMPROJECT_PG_ProjectSetup(bpy.types.PropertyGroup):
    use_file_prefs: BoolProperty(
        name="File Preferences", default=False,
        description="This file sets autosave and undo when it opens (Project Setup)")
    autosave_minutes: IntProperty(name="Autosave (min)", default=5, min=1, max=60)
    undo_steps: IntProperty(name="Undo Steps", default=64, min=1, max=256)
    undo_memory_mb: IntProperty(name="Undo Memory (MB)", default=8192, min=0, max=65536,
                                description="0 = no limit")
    template_meshes: BoolProperty(
        name="Template Meshes", default=True,
        description="TEMPLATE gets the room parts (ceiling, floor, walls A-D). Off: an empty "
                    "TEMPLATE collection")


# ---------------------------------------------------------------- the steps

def _loaded(name):
    from . import module_manager
    return module_manager.is_loaded(name)


def empty_libraries():
    """Linked .blend files nothing comes from any more (e.g. an old work window's mesh.blend)."""
    return [lib for lib in bpy.data.libraries if not lib.users_id]


STEPS = (
    # key, group (R: the Collections + Folders box), label
    ("create", 'R', "Create Original Mesh / EXPORT / TEMPLATE when missing"),
    ("dirs", 'A', "Make missing bake / export folders"),
    ("wire", 'A', "Scan materials: image -> Principled BSDF (not Emission)"),
    ("relink", 'A', "Relink missing textures and photos"),
    ("preview", 'A', "Viewport quality Preview (flat color, no normal maps, textures max 2K)"),
    ("samples", 'A', "EEVEE viewport samples max 8"),
    ("shadows", 'A', "EEVEE shadows off"),
    ("estimate", 'A', "Calculate the Polycount Estimation"),
    ("file_prefs", 'B', "Autosave / undo for this file only:"),
    ("libraries", 'B', "Remove empty library links"),
    ("slim", 'B', "Scan meshes: normals per vertex, no UV selection data (same shading)"),
    ("albedo", 'B', "GN-Final lets go of ALB_ (the 8K float copy in RAM)"),
)


_KEYS = {'OBJECTS': "objects", 'ORIGINALS': "originals", 'EXPORT': "export",
         'CAMERAS': "cameras"}
_COLL_TIP = ("The collection of this role: auto detected (picked, else found by its usual "
             "name), or pick another. Empty = found by its usual name later")
_RENAME_TIP = "On OK, rename the collection to the add-on's own name for this role"
_REL = {'PATH_SUPPORTS_BLEND_RELATIVE'}     # the folders are // paths
_FOUND = {}     # role: "n/total files" of the folder detected when the popup opened


def _role_collection(scene, view_layer, role):
    """The collection the role has now: picked, else the one its usual name finds."""
    return roles.picked(scene, role) or roles._found(scene, view_layer, role)


class MULTICAMPROJECT_OT_ProjectSetup(bpy.types.Operator):
    """Project Setup: collections, folders, viewport speed and file settings - each step listed
    with a tick, nothing changes until OK. The first run unlocks the add-on"""
    bl_idname = "multicamproject.project_setup"
    bl_label = "Project Setup"
    bl_options = {'REGISTER', 'UNDO'}

    create: BoolProperty(name="Create collections", default=True)
    template_meshes: BoolProperty(
        name="Template Meshes", default=True,
        description="TEMPLATE gets the room parts (ceiling, floor, walls A-D). Off: an empty "
                    "TEMPLATE collection")
    dirs: BoolProperty(name="Folders", default=True)
    wire: BoolProperty(name="Wire scan materials", default=True)
    relink: BoolProperty(name="Relink missing", default=True)
    preview: BoolProperty(name="Preview quality", default=True)
    samples: BoolProperty(name="EEVEE samples", default=True)
    shadows: BoolProperty(name="EEVEE shadows off", default=True)
    estimate: BoolProperty(name="Polycount Estimation", default=True)
    file_prefs: BoolProperty(name="File autosave / undo", default=True)
    libraries: BoolProperty(name="Empty library links", default=True)
    slim: BoolProperty(name="Slim scan meshes", default=True)
    albedo: BoolProperty(name="GN-Final ALB", default=True)
    autosave_minutes: IntProperty(name="Autosave (min)", default=5, min=1, max=60)
    undo_steps: IntProperty(name="Undo Steps", default=64, min=1, max=256)
    undo_memory_mb: IntProperty(name="Undo Memory (MB)", default=8192, min=0, max=65536,
                                description="0 = no limit")
    # the Collections + Folders section, one of each per role (_KEYS)
    coll_objects: StringProperty(name="Objects", description=_COLL_TIP)
    coll_originals: StringProperty(name="Original Mesh", description=_COLL_TIP)
    coll_export: StringProperty(name="Export", description=_COLL_TIP)
    coll_cameras: StringProperty(name="Cameras", description=_COLL_TIP)
    rename_objects: BoolProperty(name="Rename", description=_RENAME_TIP)
    rename_originals: BoolProperty(name="Rename", description=_RENAME_TIP)
    rename_export: BoolProperty(name="Rename", description=_RENAME_TIP)
    rename_cameras: BoolProperty(name="Rename", description=_RENAME_TIP)
    dir_objects: StringProperty(name="Scan Textures", subtype='DIR_PATH', options=_REL)
    dir_originals: StringProperty(name="Bake Folder", subtype='DIR_PATH', options=_REL)
    dir_export: StringProperty(name="Export Folder", subtype='DIR_PATH', options=_REL)
    dir_cameras: StringProperty(name="Camera Photos", subtype='DIR_PATH', options=_REL)
    resolution: EnumProperty(
        name="Bake Resolution", default='8192',
        items=tuple((str(n), f"{n // 1024}K", f"Bake at {n} x {n} px (every object set to A)")
                    for n in (1024, 2048, 4096, 8192)),
        description="The baking resolution of every object set to A (Auto) - the dropdown in "
                    "front of Export")
    dir_override: StringProperty(
        name="Overrides", subtype='DIR_PATH', options=_REL,
        description="02.Overrides: finished ALB_<name>.png / NOR_<name>.png. A texture set to "
                    "Load (07) uses its file here instead of a bake")

    @classmethod
    def poll(cls, context):
        if not gate.prefix_ok(context.scene):
            cls.poll_message_set("Fill in the Name Prefix first")
            return False
        return context.mode == 'OBJECT'

    def invoke(self, context, event):
        if not bpy.data.filepath:
            self.report({'WARNING'}, "Save the .blend first: the folders sit next to it")
            return {'CANCELLED'}
        scene, vl = context.scene, context.view_layer
        _FOUND.clear()
        for role, key in _KEYS.items():
            coll = _role_collection(scene, vl, role)
            setattr(self, f"coll_{key}", coll.name if coll else "")
            setattr(self, f"rename_{key}", False)
            if folders.holder(scene, role)[0] is not None:
                value, n, total = folders.detect(scene, vl, role)
                setattr(self, f"dir_{key}", value)
                _FOUND[role] = f"{n}/{total} files" if total else ""
        bake = getattr(scene, "multicamproject_bake_settings", None)
        self.resolution = '8192'        # a file that never set its resolution: 8K
        if bake is not None and bake.is_property_set("resolution") and \
                str(bake.resolution) in {"1024", "2048", "4096", "8192"}:
            self.resolution = str(bake.resolution)
        self.dir_override = scene.multicamproject_props.texture_override_folder
        s = scene.multicamproject_setup
        self.template_meshes = s.template_meshes
        if s.use_file_prefs:            # what the file has, else the defaults above
            self.autosave_minutes = s.autosave_minutes
            self.undo_steps = s.undo_steps
            self.undo_memory_mb = s.undo_memory_mb
        return context.window_manager.invoke_props_dialog(self, width=640, confirm_text="OK")

    def _now(self, context, key):
        """The current value, shown after a step's label."""
        p, scene = context.preferences, context.scene
        if key == "preview":
            return scene.multicamproject_props.quality.title()
        if key == "samples":
            return str(scene.eevee.taa_samples)
        if key == "shadows":
            return "on" if scene.eevee.use_shadows else "off"
        if key == "libraries":
            return str(len(empty_libraries()))
        if key == "file_prefs":
            mem = p.edit.undo_memory_limit
            return (f"{p.filepaths.auto_save_time} min, {p.edit.undo_steps} steps, "
                    f"{f'{mem} MB' if mem else 'no limit'}")
        return ""

    def _draw_roles(self, layout, scene):
        """Collections + Folders: label | collection [rename] | folder, one row per role."""
        box = layout.box().column(align=True)
        box.label(text="Collections + Folders (auto detected - pick or type to change)")
        for role, key in _KEYS.items():
            label, icon = roles.ROLES[role][1:3]
            row = box.row(align=True)
            split = row.split(factor=0.17, align=True)
            split.label(text=label, icon=icon)
            split = split.split(factor=0.4, align=True)
            cell = split.row(align=True)
            cell.prop_search(self, f"coll_{key}", bpy.data, "collections", text="")
            name = getattr(self, f"coll_{key}")
            default = roles.default_name(role)
            sub = cell.row(align=True)
            sub.enabled = bool(name) and name != default
            sub.prop(self, f"rename_{key}", text="", icon='SORTALPHA')
            right = split.row(align=True)
            pg, why = folders.holder(scene, role)
            if pg is None:
                right.active = False
                right.label(text=f"{folders.LABELS[role]}: {why}", icon='INFO')
            else:
                path = getattr(self, f"dir_{key}")
                right.alert = role not in folders.MADE and not folders.exists(
                    os.path.normpath(bpy.path.abspath(path)) if path else "")
                right.prop(self, f"dir_{key}", text="")
            hint = box.row()
            hint.active = False
            sub = hint.split(factor=0.17)
            sub.label(text="")
            picked = roles.picked(scene, role)
            if getattr(self, f"rename_{key}") and name and name != default:
                text = f"renamed {name} -> {default} on OK"
            elif not name:
                text = ("none found - made on OK (Create step)" if role in roles.CREATABLE
                        else "none found")
            elif bpy.data.collections.get(name) is None:
                text = f"no collection named {name}"
            else:
                text = "picked" if picked is not None and picked.name == name else "auto detected"
            found = _FOUND.get(role)
            sub.label(text=text + (f"  ·  folder: {found}" if found else ""))
        row = box.row(align=True)       # the baking resolution + the Texture Override folder
        split = row.split(factor=0.17, align=True)
        split.label(text="Resolution", icon='TEXTURE')
        split = split.split(factor=0.4, align=True)
        mid = split.split(factor=0.45, align=True)
        cell = mid.row(align=True)
        cell.enabled = hasattr(scene, "multicamproject_bake_settings")
        cell.prop(self, "resolution", text="")
        mid.label(text="Overrides", icon='FILE_FOLDER')
        cell = split.row(align=True)        # lines up with the folders above
        cell.alert = bool(self.dir_override) and not folders.exists(
            os.path.normpath(bpy.path.abspath(self.dir_override)))
        cell.prop(self, "dir_override", text="")
        hint = box.row()
        hint.active = False
        sub = hint.split(factor=0.17)
        sub.label(text="")
        sub.label(text="objects set to A bake at this size  ·  ALB_ / NOR_ set to Load (07) "
                       "use their file in Overrides")
        box.separator()
        box.prop(self, "create", text=next(t for k, _g, t in STEPS if k == "create"))
        sub = box.row(align=True)
        sub.enabled = self.create
        sub.separator(factor=3)
        sub.prop(self, "template_meshes",
                 text="TEMPLATE with the room meshes (ceiling, floor, walls A-D), off: empty")

    def draw(self, context):
        layout = self.layout
        self._draw_roles(layout, context.scene)
        missing = {"estimate": not _loaded("estimation"), "slim": not _loaded("remesh"),
                   "albedo": not _loaded("baking")}
        for group, title in (('A', "Collections, folders, viewport"), ('B', "Performance")):
            box = layout.box().column(align=True)
            box.label(text=title)
            for key, g, label in STEPS:
                if g != group:
                    continue
                row = box.row(align=True)
                row.enabled = not missing.get(key, False)
                row.prop(self, key, text=label)
                now = self._now(context, key)
                if now:
                    sub = row.row()
                    sub.alignment = 'RIGHT'
                    sub.active = False
                    sub.label(text=f"now: {now}")
                if key == "file_prefs":
                    sub = box.row(align=True)
                    sub.enabled = self.file_prefs
                    sub.separator(factor=3)
                    sub.prop(self, "autosave_minutes")
                    sub.prop(self, "undo_steps")
                    sub.prop(self, "undo_memory_mb")
        p = context.preferences.filepaths
        row = layout.row()
        row.active = False
        row.label(text=f"Save compression: {'on' if p.use_file_compression else 'off'} "
                       "(Blender preference, not changed)", icon='INFO')

    def _apply_roles(self, scene):
        """The popup's collections picked for their roles (empty: found by name later),
        renamed to the add-on's names where asked. Returns report lines."""
        lines = []
        props = scene.multicamproject_props
        for role, key in _KEYS.items():
            attr = roles.ROLES[role][0]
            name = getattr(self, f"coll_{key}")
            coll = bpy.data.collections.get(name) if name else None
            if name and coll is None:
                self.report({'WARNING'}, f"{roles.ROLES[role][1]}: no collection named {name}")
            if getattr(props, attr) != coll:
                setattr(props, attr, coll)
            if coll is not None and getattr(self, f"rename_{key}"):
                old = coll.name
                why = roles.rename_to_default(role, coll)
                if why:
                    self.report({'WARNING'}, f"{old} not renamed: {why}")
                elif coll.name != old:
                    lines.append(f"Renamed {old} -> {coll.name}")
        return lines

    def execute(self, context):
        scene = context.scene
        lines = self._apply_roles(scene)
        scene.multicamproject_setup.template_meshes = self.template_meshes
        bake = getattr(scene, "multicamproject_bake_settings", None)
        if bake is not None and bake.resolution != int(self.resolution):
            bake.resolution = int(self.resolution)
            lines.append(f"Bake resolution: {int(self.resolution) // 1024}K")
        props = scene.multicamproject_props
        if props.texture_override_folder != self.dir_override:
            props.texture_override_folder = self.dir_override
            lines.append(f"Texture Override: {self.dir_override or 'off'}")
        given = {role: getattr(self, f"dir_{key}") for role, key in _KEYS.items()}
        lines += folders.auto_fill(scene, context.view_layer, True, self.create, True,
                                   self.wire, self.relink, self.template_meshes, given,
                                   make=self.dirs)
        scene.multicamproject_props.file_io_done = True     # the add-on unlocks
        e = scene.eevee
        if self.samples:
            from .quality import VIEWPORT_SAMPLES
            e.taa_samples = min(e.taa_samples, VIEWPORT_SAMPLES) or VIEWPORT_SAMPLES
            lines.append(f"EEVEE viewport samples: {e.taa_samples}")
        if self.shadows:
            e.use_shadows = False
            lines.append("EEVEE shadows off")
        if self.preview:
            scene.multicamproject_props.quality = 'PREVIEW'     # -> quality.apply
            lines.append("Viewport: Preview")
        s = scene.multicamproject_setup
        if self.file_prefs:
            s.autosave_minutes = self.autosave_minutes
            s.undo_steps = self.undo_steps
            s.undo_memory_mb = self.undo_memory_mb
            s.use_file_prefs = True
            apply_file_prefs(scene)
            lines.append(f"This file: autosave {s.autosave_minutes} min, {s.undo_steps} undo "
                         f"steps, undo memory {s.undo_memory_mb or 'no limit'} MB")
        if self.libraries:
            libs = empty_libraries()
            names = [lib.filepath for lib in libs]
            for lib in libs:
                bpy.data.libraries.remove(lib)
            if names:
                lines.append(f"{len(names)} empty library link(s) removed")
        if self.slim and _loaded("remesh"):
            from .modules.remesh import workflow
            n = sum(workflow.slim_scan(o) for o in bpy.data.objects)
            lines.append(f"Scan meshes slimmed: {n}")
        if self.albedo and _loaded("baking"):
            from .modules.baking import gn_final
            gn_final.drop_albedo()
            lines.append("GN-Final: ALB_ let go")
        if self.estimate and _loaded("estimation"):
            from .modules.estimation import core as est
            if est.calculate(context) is not None:
                lines.append(f"Polycount Estimation: {len(scene.multicamproject_estimation.rows)} "
                             "objects")
        for line in lines:
            self.report({'INFO'}, line)
        self.report({'INFO'}, f"Project Setup: {len(lines)} step(s) done")
        return {'FINISHED'}


_classes = (MULTICAMPROJECT_PG_ProjectSetup, MULTICAMPROJECT_OT_ProjectSetup)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.multicamproject_setup = bpy.props.PointerProperty(
        type=MULTICAMPROJECT_PG_ProjectSetup)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    del bpy.types.Scene.multicamproject_setup
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
