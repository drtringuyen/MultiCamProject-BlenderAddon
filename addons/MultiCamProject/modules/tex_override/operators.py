import os

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, StringProperty

from . import core

KIND_ITEMS = (('ALB', "ALB_", "The albedo"), ('NOR', "NOR_", "The normal map"))


def _target(op, context):
    obj = bpy.data.objects.get(op.object_name) if op.object_name else context.active_object
    return obj if obj is not None and obj.type == 'MESH' else None


def _busy():
    try:
        from ..baking import jobs
        return jobs.busy()
    except ImportError:
        return False


class MULTICAMPROJECT_OT_TexMode(bpy.types.Operator):
    """Bake: the add-on makes this texture into 01.Bake. Load: the finished file in
    02.Overrides is used as it is"""
    bl_idname = "multicamproject.tex_mode"
    bl_label = "Bake / Load"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(items=KIND_ITEMS, options={'SKIP_SAVE'})
    mode: EnumProperty(items=(('BAKE', "Bake", ""), ('LOAD', "Load", "")), options={'SKIP_SAVE'})
    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        n = "ALB_" if props.kind == 'ALB' else "NOR_"
        if props.mode == 'LOAD':
            return (f"Load: {n} is the finished file in 02.Overrides - nothing is baked; "
                    "Lock Finish copies it into Export")
        step = "05 bakes" if props.kind == 'ALB' else "07 generates"
        return f"Bake: {step} {n} into 01.Bake (bake it again after switching)"

    @classmethod
    def poll(cls, context):
        return not _busy()

    def execute(self, context):
        obj = _target(self, context)
        if obj is None:
            return {'CANCELLED'}
        try:
            core.set_mode(context.scene, obj, self.kind, self.mode)
        except RuntimeError as e:
            self.report({'WARNING'}, f"{obj.name}: {e}")
            return {'CANCELLED'}
        n = "ALB_" if self.kind == 'ALB' else "NOR_"
        self.report({'INFO'}, f"{obj.name}: {n} " + (
            "loaded from 02.Overrides" if self.mode == 'LOAD' else "on Bake - bake it again"))
        return {'FINISHED'}


class MULTICAMPROJECT_OT_TexRefresh(bpy.types.Operator):
    """Read the texture's file again - after it was changed outside Blender (02.Overrides
    or 01.Bake). A texture with unsaved changes in Blender is kept as it is"""
    bl_idname = "multicamproject.tex_refresh"
    bl_label = "Refresh Texture"
    bl_options = {'REGISTER'}

    kind: EnumProperty(items=KIND_ITEMS, options={'SKIP_SAVE'})
    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return not _busy()

    def execute(self, context):
        obj = _target(self, context)
        if obj is None:
            return {'CANCELLED'}
        try:
            what = core.refresh(context.scene, obj, self.kind)
        except RuntimeError as e:
            self.report({'WARNING'}, f"{obj.name}: {e}")
            return {'CANCELLED'}
        n = core.name(obj, self.kind)
        if what == "unsaved":
            self.report({'WARNING'}, f"{n}: unsaved changes in Blender, not reloaded")
        else:
            self.report({'INFO'}, f"{n}: read from disk" if what
                        else f"{n}: not loaded yet - the next use reads the file")
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_TexDeleteOverride(bpy.types.Operator):
    """Move the override file to the Recycle Bin. The texture goes back to Bake: bake it
    again (05 / 07)"""
    bl_idname = "multicamproject.tex_delete_override"
    bl_label = "Delete Override"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(items=KIND_ITEMS, options={'SKIP_SAVE'})
    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return not _busy()

    def invoke(self, context, event):
        obj = _target(self, context)
        if obj is None:
            return {'CANCELLED'}
        path = core.override_path(context.scene, obj, self.kind)
        return context.window_manager.invoke_confirm(
            self, event, title=f"Delete {os.path.basename(path)}?",
            message="To the Recycle Bin - the texture goes back to Bake", confirm_text="Delete",
            icon='WARNING')

    def execute(self, context):
        obj = _target(self, context)
        if obj is None:
            return {'CANCELLED'}
        try:
            path = core.delete_override(context.scene, obj, self.kind)
        except RuntimeError as e:
            self.report({'WARNING'}, f"{obj.name}: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{os.path.basename(path)} moved to the Recycle Bin - "
                              f"{core.name(obj, self.kind)} is on Bake again")
        return {'FINISHED'}


@persistent
def _on_load(_):
    bpy.app.timers.register(_migrate_all, first_interval=0.5)


def _migrate_all():
    try:
        for sc in bpy.data.scenes:
            core.migrate(sc)
    except Exception as e:      # never stops a file from opening
        print(f"[MultiCamProject] TexOverride migrate: {e}")
    return None


_classes = (MULTICAMPROJECT_OT_TexMode, MULTICAMPROJECT_OT_TexRefresh,
            MULTICAMPROJECT_OT_TexDeleteOverride)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_migrate_all, first_interval=0.5)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_migrate_all):
        bpy.app.timers.unregister(_migrate_all)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
