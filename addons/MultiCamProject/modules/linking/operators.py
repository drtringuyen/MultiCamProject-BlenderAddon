"""Linking operators: Send Out / Receive / End Link / Open / Relink in the main file, Send Back
in a work window (core does the work)."""
import os

import bpy
from bpy_extras.io_utils import ImportHelper

from ... import folders
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
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkSendBack(bpy.types.Operator):
    """Send Back: this object's mesh (with VCMix painting), its cameras and the bakes made
    here, for Receive in the main file. No need to save this window"""
    bl_idname = "multicamproject.work_send_back"
    bl_label = "Send Back to Main File"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return core.is_work_window(context.scene) and core.work_object(context.scene) is not None

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


_CLASSES = (MULTICAMPROJECT_OT_ReloadFolder, MULTICAMPROJECT_OT_WorkSendOut, MULTICAMPROJECT_OT_WorkReceive,
            MULTICAMPROJECT_OT_WorkOpen, MULTICAMPROJECT_OT_WorkRelink,
            MULTICAMPROJECT_OT_WorkSelect, MULTICAMPROJECT_OT_WorkCancel,
            MULTICAMPROJECT_OT_WorkSendBack)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
