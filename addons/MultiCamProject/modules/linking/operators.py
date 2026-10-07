"""Linking operators: Send Out / Receive / End Link / Open / Relink in the main file, Send Back
in a work window (core does the work)."""
import os
import subprocess

import bpy
from bpy_extras.io_utils import ImportHelper

from ... import folders, roles
from . import core


def _target(context, name):
    obj = bpy.data.objects.get(name) if name else context.active_object
    return obj if obj is not None and obj.type == 'MESH' and not obj.library else None


class MULTICAMPROJECT_OT_WorkSendOut(bpy.types.Operator):
    """Send Out: work on this object in a new, unsaved Blender - its materials, cameras and
    Bake Source come along (transforms applied there). Model, paint and bake, then Send Back
    and Receive here"""
    bl_idname = "multicamproject.work_send_out"
    bl_label = "Send Out"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and not core.is_work_window(context.scene)

    def execute(self, context):
        obj = _target(context, self.object_name)
        if obj is None:
            return {'CANCELLED'}
        if core.out_record(obj) is not None:
            self.report({'WARNING'}, f"'{obj.name}' is linked to a work window: end the link "
                                     "(X) first")
            return {'CANCELLED'}
        try:
            core.send_out(context, obj)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"'{obj.name}' sent out: a work window is opening (unsaved)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkReceive(bpy.types.Operator):
    """Receive what the work window sent back"""
    bl_idname = "multicamproject.work_receive"
    bl_label = "Receive"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action: bpy.props.EnumProperty(name="Receive as", default='REPLACE', items=(
        ('REPLACE', "Replace", "The mesh becomes the window's (with its VCMix painting); "
                               "modifiers applied there go, the camera list, slot picks, "
                               "shifts and the bakes made there come along, materials are "
                               "refreshed"),
        ('ADD', "Add", "Join the window's mesh into this object - its own geometry stays; "
                       "nothing else is taken")),
        options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        return ("Receive as Add: join the window's mesh into this object (its geometry stays)"
                if props.action == 'ADD' else
                "Receive: the mesh, painting, cameras and bakes from the work window "
                "(materials refreshed, still linked). Ctrl+Z undoes the mesh; replaced texture "
                "files are in the Recycle Bin")

    def invoke(self, context, event):
        obj = _target(context, self.object_name)
        why = core.receive_warnings(obj) if obj is not None else []
        if why:
            return context.window_manager.invoke_confirm(
                self, event, title=f"Receive into '{obj.name}'?",
                message="Since it was sent out or last received, " + "; ".join(why),
                confirm_text="Receive", icon='WARNING')
        return self.execute(context)

    def execute(self, context):
        obj = _target(context, self.object_name)
        if obj is None or core.new_send(obj) is None:
            self.report({'WARNING'}, "Nothing new sent back - press Send Back in the work window")
            return {'CANCELLED'}
        try:
            faces = core.receive(context, obj, self.action)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        verb = "added" if self.action == 'ADD' else "received"
        self.report({'INFO'}, f"'{obj.name}': {faces:,} faces {verb} (still linked: X ends it)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkOpen(bpy.types.Operator):
    """Open the saved work window of this object again (in a new Blender, still linked)"""
    bl_idname = "multicamproject.work_open"
    bl_label = "Open Work File"
    bl_options = {'REGISTER'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        obj = bpy.data.objects.get(props.object_name)
        path = core.saved_window(obj) if obj is not None else ""
        return f"Open {path} (still linked)" if path else cls.__doc__

    def execute(self, context):
        obj = _target(context, self.object_name)
        try:
            core.open_saved(obj)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkRelink(bpy.types.Operator, ImportHelper):
    """Link this object to a saved work window by hand - after the work file was moved or
    renamed, or the temp folder was cleaned. Its next Send Back can be received here"""
    bl_idname = "multicamproject.work_relink"
    bl_label = "Relink Work File"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".blend"
    filter_glob: bpy.props.StringProperty(default="*.blend", options={'HIDDEN'})
    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return not core.is_work_window(context.scene)

    def invoke(self, context, event):
        obj = _target(context, self.object_name)
        path = core.saved_window(obj) if obj is not None else ""
        if path:
            self.filepath = path
        elif bpy.data.filepath:
            self.filepath = os.path.dirname(bpy.data.filepath) + os.sep
        return super().invoke(context, event)

    def execute(self, context):
        obj = _target(context, self.object_name)
        if obj is None:
            self.report({'WARNING'}, "Pick a mesh object first")
            return {'CANCELLED'}
        try:
            name = core.relink(obj, self.filepath)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        note = "" if name == obj.name else f" (its object there: '{name}')"
        self.report({'INFO'}, f"'{obj.name}' linked to {os.path.basename(self.filepath)}{note}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkSelect(bpy.types.Operator):
    """Select this object (and make it active)"""
    bl_idname = "multicamproject.work_select"
    bl_label = "Select Linked Object"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None or context.view_layer.objects.get(obj.name) is None:
            self.report({'WARNING'}, "Not in this view layer")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkCancel(bpy.types.Operator):
    """End the link to the work window (this object stays as it is; the window's file, if
    saved, is not deleted)"""
    bl_idname = "multicamproject.work_cancel"
    bl_label = "End Link"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        obj = _target(context, self.object_name)
        if obj is not None:
            core.cancel(obj)
            try:        # a 0C window copy whose Decimate never came back: its GN show again
                from ..remesh import workflow as wf
                if wf.decimate_in_window(obj):
                    wf.set_gn(obj, True)
            except ImportError:
                pass
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkSendBack(bpy.types.Operator):
    """Send Back: this object's mesh (with VCMix painting), its cameras and the bakes made
    here, for Receive in the main file. No need to save this window"""
    bl_idname = "multicamproject.work_send_back"
    bl_label = "Send Back to Main File"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if not core.is_work_window(context.scene):
            return False
        obj = core.work_object(context.scene)
        if obj is None:
            return False
        from ... import gate
        if gate.decimate_pending(obj):  # it never comes back with a Decimate still on it
            cls.poll_message_set("Decide the Decimate first (Apply)")
            return False
        return True

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            faces = core.send_back(context)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"{faces:,} faces sent back: Receive in the main file")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ReloadFolder(bpy.types.Operator):
    """Reload this role's folder: every texture is pointed at the file of the same name in
    it (unless already there) and reloaded"""
    bl_idname = "multicamproject.reload_folder"
    bl_label = "Reload Folder"
    bl_options = {'REGISTER', 'UNDO'}

    role: bpy.props.EnumProperty(
        items=(('OBJECTS', "Scan Textures", ""), ('ORIGINALS', "Bake Folder", ""),
               ('EXPORT', "Export Folder", ""), ('CAMERAS', "Camera Photos", "")),
        options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        return {
            'OBJECTS': "Point every texture of the Objects' materials (not the bakes or camera "
                       "photos) at the file of the same name in Scan Textures, then reload them",
            'ORIGINALS': "Point every ALB_/NOR_ and work bake at the file of the same name in "
                         "the Bake Folder, then reload them",
            'EXPORT': "Reload the textures that come from the Export Folder",
            'CAMERAS': "Relink every camera's background photo from Camera Photos and reload "
                       "it; objects with Camera Projection use the folder and get a Reload All",
        }[props.role]

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        text, warnings = folders.reload(context.scene, context.view_layer, self.role)
        for w in warnings[:10]:
            self.report({'WARNING'}, w)
        self.report({'INFO'}, text)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_CreateRoleCollection(bpy.types.Operator):
    """Create this collection the way the add-on does (Original Mesh / EXPORT in the scene)"""
    bl_idname = "multicamproject.create_role_collection"
    bl_label = "Create Collection"
    bl_options = {'REGISTER', 'UNDO'}

    role: bpy.props.EnumProperty(
        items=(('ORIGINALS', "Original Mesh", ""), ('EXPORT', "Export", "")),
        options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        name = roles.ROLES[props.role][3][0]
        return f"Not in this scene yet: create the {name} collection (as Remesh / 07 would)"

    def execute(self, context):
        coll = roles.ensure(context.scene, self.role)
        self.report({'INFO'}, f"{coll.name} is in the scene")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_AutoFillFolders(bpy.types.Operator):
    """Fill the empty collection pickers (OBJECTS, Original Mesh, EXPORT, CAMERAS), create
    Original Mesh / EXPORT when they are not in the scene, and set
    every folder to the one holding most of its files - the default folder (or a folder in
    it), else where the files are now. Missing bake / export folders are made, and every
    image material of the file is wired image -> Principled BSDF -> Output (not Emission)"""
    bl_idname = "multicamproject.auto_fill_folders"
    bl_label = "Auto Detect and Fill Folders"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from ... import gate
        if not gate.prefix_ok(context.scene):
            cls.poll_message_set("Fill in the Name Prefix first")
            return False
        return context.mode == 'OBJECT'

    def execute(self, context):
        if not bpy.data.filepath:
            self.report({'WARNING'}, "Save the .blend first: the folders sit next to it")
            return {'CANCELLED'}
        for line in folders.auto_fill(context.scene, context.view_layer):
            self.report({'INFO'}, line)
        context.scene.multicamproject_props.file_io_done = True     # the add-on unlocks
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkRemeshOut(bpy.types.Operator):
    """One click: 0B Setup Camera Projection (when not done yet), 0C Remesh / 0D Retopo,
    the new low poly named ENV_<prefix>.<next ##>_<New Object Name> (the original keeps its
    name), then Send Out - a work window opens with it, its original (the Bake Source) and
    its cameras, in the PolyCut tool / on the retopo, saved as Bake Folder/<typed name>.blend.
    Receive it back here"""
    bl_idname = "multicamproject.work_remesh_out"
    bl_label = "Remesh + Send Out"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        items=(('REMESH', "Remesh", "0B + 0C Remesh, then Send Out"),
               ('RETOPO', "Retopo", "0B + 0D Retopo, then Send Out")),
        options={'HIDDEN', 'SKIP_SAVE'})
    existing: bpy.props.EnumProperty(
        items=(('ASK', "Ask", "Stop when the work file already exists"),
               ('OPEN', "Open Existing", "Link the new object to the existing work file and "
                                         "open it"),
               ('REPLACE', "Replace", "Send out a fresh work window over the existing file")),
        default='ASK', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        step = "0C Remesh" if props.mode == 'REMESH' else "0D Retopo"
        return (f"0B Setup Camera Projection (when not done), {step} - the new object is "
                "ENV_<prefix>.<next ##>_<name> - then open it in a work window saved in the "
                "Bake Folder (linked: Receive it back here)")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if core.is_work_window(context.scene):
            cls.poll_message_set("Already in a work window")
            return False
        if not bpy.data.filepath:
            cls.poll_message_set("Save the main file first")
            return False
        if obj is None or obj.type != 'MESH' or obj.library:
            return False
        try:
            from ..remesh import operators as rops
            from ..export import fixes  # noqa: F401 - the ENV_ rename
        except ImportError:
            cls.poll_message_set("Needs the Remesh and Export modules")
            return False
        if not context.scene.multicamproject_props.new_object_name.strip():
            cls.poll_message_set("Type the new object's name first")
            return False
        return rops._remesh_poll(cls, context)

    def _target(self, context):
        """(new name, work file path) or (None, why not)."""
        from ... import folders
        from ..baking import naming
        scene = context.scene
        sc = naming.scheme(scene)
        if sc is None:
            return None, "No Name Prefix"
        new_name = naming.full_name(sc, naming.next_index(sc),
                                    scene.multicamproject_props.new_object_name)
        if bpy.data.objects.get(new_name) is not None:
            return None, f"'{new_name}' already exists"
        folders.make_dir(scene, 'ORIGINALS')
        bake_dir = folders.folder(scene, 'ORIGINALS')
        if not bake_dir or not os.path.isdir(bake_dir):
            return None, f"Bake Folder not found: {bake_dir or '(empty)'}"
        # the file is named by the typed name only (Wall_Window.blend), not the ENV_ name
        short = naming.clean_name(scene.multicamproject_props.new_object_name)
        return new_name, os.path.join(bake_dir, short + ".blend")

    def invoke(self, context, event):
        new_name, path = self._target(context)
        if new_name is None or self.existing != 'ASK' or not os.path.exists(path):
            return self.execute(context)
        mode = self.mode

        def draw(menu, _context):
            col = menu.layout.column()
            col.label(text=f"{os.path.basename(path)} is already in the Bake Folder",
                      icon='ERROR')
            op = col.operator(self.bl_idname, text="Open Existing (link the new object to it)",
                              icon='FILE_BLEND')
            op.mode, op.existing = mode, 'OPEN'
            op = col.operator(self.bl_idname, text="Replace (fresh work window over it)",
                              icon='FILE_REFRESH')
            op.mode, op.existing = mode, 'REPLACE'
        context.window_manager.popup_menu(draw, title="Work file exists", icon='QUESTION')
        return {'CANCELLED'}

    def execute(self, context):
        from ..remesh import workflow as wf
        from ..export import fixes
        obj = context.active_object
        if core.out_record(obj) is not None:
            self.report({'WARNING'}, f"'{obj.name}' is linked to a work window: end the link "
                                     "(X) first")
            return {'CANCELLED'}
        scene = context.scene
        new_name, path = self._target(context)
        if new_name is None:
            self.report({'ERROR'}, path)
            return {'CANCELLED'}
        exists = os.path.exists(path)
        if exists and self.existing == 'ASK':
            self.report({'ERROR'}, f"{path} already exists - Open Existing or Replace")
            return {'CANCELLED'}
        if self.mode == 'RETOPO' and scene.multicamproject_retopo_plane                 and not wf.retopo_content(context, obj):
            self.report({'ERROR'}, wf.RETOPO_CONTENT_TEXT)
            return {'CANCELLED'}
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        name, mesh_name = obj.name, obj.data.name
        # 0B only on the new object (after the copy): on the scan it would be scored once
        # more and undone right away by the copy (~3 s on a scan with 500 cameras)
        cp = getattr(obj, "multicamproject_cam", None)
        need_setup = cp is not None and not cp.is_setup
        if need_setup and not any(o.type == 'CAMERA' for o in context.scene.objects):
            self.report({'WARNING'}, "No camera in the scene: 0B skipped")
            need_setup = False
        # no Decimate here: the window adds it, decides and applies it (it never comes back)
        copy, warnings = wf.make_copy(context, obj, retopo=self.mode == 'RETOPO',
                                      decimate=self.mode != 'REMESH')
        for w in warnings:
            self.report({'WARNING'}, w)
        # the new object's own name (MCP_ / MAT_ / textures follow), into EXPORT; the
        # original takes its name back (make_copy gave it _original)
        export = fixes.ensure_export_collection(scene)
        if export not in copy.users_collection:
            export.objects.link(copy)
        fixes.rename_object(copy, new_name)
        obj.name = name
        if obj.data.users == 1:
            obj.data.name = mesh_name
        if need_setup:          # 0B on the copy, called directly (bpy.ops would evaluate
            for w in wf.setup_projection(copy, scene):   # the whole scan first)
                self.report({'WARNING'}, w)
            if self.mode == 'REMESH':
                wf.set_gn(copy, False)      # its GN waits for the Decimate (in the window)
        if exists and self.existing == 'OPEN':
            return self._open_existing(copy, obj, path)
        try:
            core.send_out(context, copy, start=self.mode, save_as=path)
        except Exception as e:
            self.report({'ERROR'}, f"'{copy.name}' is made, but Send Out failed: {e}")
            return {'FINISHED'}
        self.report({'INFO'}, f"'{copy.name}' sent out: a work window is opening, saved as "
                              f"{os.path.basename(path)} ('{obj.name}' is its original)")
        return {'FINISHED'}

    def _open_existing(self, copy, obj, path):
        """The work file is there already: the new object is linked to it and its mesh sent
        along - the window keeps its version in "old version" and takes this one."""
        try:
            work_name = core.send_mesh(copy, path)
        except Exception as e:      # not a work window (or unreadable): open it anyway
            self.report({'WARNING'}, f"'{copy.name}' is made, but not linked to "
                                     f"{os.path.basename(path)}: {e}")
            subprocess.Popen([bpy.app.binary_path, path])
            return {'FINISHED'}
        self.report({'INFO'}, f"'{copy.name}' -> {os.path.basename(path)}: its '{work_name}' "
                              "takes this mesh (the old one goes to 'old version')")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkSave(bpy.types.Operator):
    """Save this work window as the file the 0C / 0D button named (Bake Folder/<name>.blend)
    - the main file can open it again from then on"""
    bl_idname = "multicamproject.work_save"
    bl_label = "Save Work Window"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(core.save_target(context.scene))    # else Ctrl+S is Blender's own

    def execute(self, context):
        path = core.save_target(context.scene)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        except Exception as e:
            self.report({'ERROR'}, f"Not saved: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved {os.path.basename(path)}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkReopen(bpy.types.Operator):
    """Open the work file this object was linked to before (X), with this object's current
    mesh: the window keeps its version in "old version" (hidden) and takes this one - the
    link is back on"""
    bl_idname = "multicamproject.work_reopen"
    bl_label = "Reopen Work File with this Mesh"
    bl_options = {'REGISTER'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and not core.is_work_window(context.scene)

    def execute(self, context):
        obj = _target(context, self.object_name)
        path = core.last_window(obj) if obj is not None else ""
        if not path:
            self.report({'WARNING'}, "No earlier work file for this object")
            return {'CANCELLED'}
        try:
            work_name = core.send_mesh(obj, path)
        except Exception as e:
            self.report({'ERROR'}, f"Not sent: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{os.path.basename(path)} opens: its '{work_name}' takes "
                              f"'{obj.name}''s mesh (the old one goes to 'old version')")
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_ReloadFolder, MULTICAMPROJECT_OT_WorkReopen, MULTICAMPROJECT_OT_WorkSave, MULTICAMPROJECT_OT_WorkRemeshOut, MULTICAMPROJECT_OT_AutoFillFolders, MULTICAMPROJECT_OT_CreateRoleCollection, MULTICAMPROJECT_OT_WorkSendOut, MULTICAMPROJECT_OT_WorkReceive,
            MULTICAMPROJECT_OT_WorkOpen, MULTICAMPROJECT_OT_WorkRelink,
            MULTICAMPROJECT_OT_WorkSelect, MULTICAMPROJECT_OT_WorkCancel,
            MULTICAMPROJECT_OT_WorkSendBack)


_keymaps = []


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:      # None in background mode
        # Ctrl+S in an unsaved work window saves it where the 0C / 0D button said; the poll
        # fails everywhere else, so Blender's own Save runs as usual
        km = kc.keymaps.new(name="Window")
        kmi = km.keymap_items.new(MULTICAMPROJECT_OT_WorkSave.bl_idname, 'S', 'PRESS', ctrl=True)
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
