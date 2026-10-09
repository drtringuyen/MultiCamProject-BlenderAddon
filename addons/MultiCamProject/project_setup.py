"""Project Setup: one popup listing everything Setup File IO does, each step with its own tick,
confirmed with OK. Opened by Setup File IO (the start gate) and by the Project Setup button
of Collections + Folders.

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
from bpy.props import BoolProperty, IntProperty

from . import gate

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


# ---------------------------------------------------------------- the steps

def _loaded(name):
    from . import module_manager
    return module_manager.is_loaded(name)


def empty_libraries():
    """Linked .blend files nothing comes from any more (e.g. an old work window's mesh.blend)."""
    return [lib for lib in bpy.data.libraries if not lib.users_id]


STEPS = (
    # key, group, label
    ("pickers", 'A', "Fill the empty collection pickers (OBJECTS, Original Mesh, EXPORT, CAMERAS)"),
    ("create", 'A', "Create Original Mesh / EXPORT / ROOM_Template when missing"),
    ("dirs", 'A', "Set every role folder, make missing bake / export folders"),
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


class MULTICAMPROJECT_OT_ProjectSetup(bpy.types.Operator):
    """Project Setup: collections, folders, viewport speed and file settings - each step listed
    with a tick, nothing changes until OK. The first run unlocks the add-on"""
    bl_idname = "multicamproject.project_setup"
    bl_label = "Project Setup"
    bl_options = {'REGISTER', 'UNDO'}

    pickers: BoolProperty(name="Collection pickers", default=True)
    create: BoolProperty(name="Create collections", default=True)
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
        s = context.scene.multicamproject_setup
        if s.use_file_prefs:            # what the file has, else the defaults above
            self.autosave_minutes = s.autosave_minutes
            self.undo_steps = s.undo_steps
            self.undo_memory_mb = s.undo_memory_mb
        return context.window_manager.invoke_props_dialog(self, width=560, confirm_text="OK")

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

    def draw(self, context):
        layout = self.layout
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

    def execute(self, context):
        scene = context.scene
        lines = []
        if any((self.pickers, self.create, self.dirs, self.wire, self.relink)):
            from . import folders
            lines += folders.auto_fill(scene, context.view_layer, self.pickers, self.create,
                                       self.dirs, self.wire, self.relink)
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
