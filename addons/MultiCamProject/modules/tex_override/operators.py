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
        if what == "bake":
            self.report({'WARNING'}, f"{n}: not found in 02.Overrides - back to Bake")
        elif what == "unsaved":
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


class MULTICAMPROJECT_OT_TexExportOverride(bpy.types.Operator):
    """Copy this texture's file into 02.Overrides as its override (overwrites the one there).
    Nothing switches: pick Load to use it"""
    bl_idname = "multicamproject.tex_export_override"
    bl_label = "Export to Overrides"
    bl_options = {'REGISTER'}

    kind: EnumProperty(items=KIND_ITEMS, options={'SKIP_SAVE'})
    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @staticmethod
    def _why_not(context, obj, kind):
        """'' or why the texture can't be exported."""
        from ..baking import cache, common
        if obj is None:
            return "No mesh"
        if not core.folder(context.scene):
            return "No Overrides folder set (Project Setup)"
        src = common.image_file(core.image(obj, kind))
        if not src or not cache.isfile(src):
            return f"{core.name(obj, kind)} has no file on disk yet"
        if os.path.normcase(src) == os.path.normcase(
                os.path.normpath(core.override_path(context.scene, obj, kind))):
            return "On Load: the texture already is the file in 02.Overrides"
        return ""

    @classmethod
    def description(cls, context, props):
        why = cls._why_not(context, _target(props, context), props.kind)
        return why or cls.__doc__

    @classmethod
    def poll(cls, context):
        return not _busy()

    def invoke(self, context, event):
        obj = _target(self, context)
        why = self._why_not(context, obj, self.kind)
        if why:
            self.report({'WARNING'}, why)
            return {'CANCELLED'}
        from ..baking import cache
        dst = core.override_path(context.scene, obj, self.kind)
        there = cache.isfile(dst)
        img = core.image(obj, self.kind)
        msg = ("The file in 02.Overrides is replaced (to the Recycle Bin)" if there
               else "Into 02.Overrides - pick Load to use it")
        if img is not None and img.is_dirty:
            msg += ". Unsaved changes in Blender are not in the file - save them first"
        return context.window_manager.invoke_confirm(
            self, event, title=(f"Overwrite {os.path.basename(dst)}?" if there
                                else f"Export {os.path.basename(dst)}?"),
            message=msg, confirm_text="Overwrite" if there else "Export",
            icon='WARNING' if there else 'QUESTION')

    def execute(self, context):
        import shutil
        from ..baking import cache, common
        obj = _target(self, context)
        why = self._why_not(context, obj, self.kind)
        if why:
            self.report({'WARNING'}, why)
            return {'CANCELLED'}
        src = common.image_file(core.image(obj, self.kind))
        dst = core.override_path(context.scene, obj, self.kind)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isfile(dst):         # the old override: recoverable (Export module on)
                try:
                    from ..export import fixes
                    fixes.to_recycle_bin([dst])
                except ImportError:
                    pass
            shutil.copy2(src, dst)
        except (OSError, RuntimeError) as e:
            self.report({'ERROR'}, f"{os.path.basename(dst)}: {e}")
            return {'CANCELLED'}
        cache.clear()
        self.report({'INFO'}, f"{os.path.basename(dst)} written to 02.Overrides")
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
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
            MULTICAMPROJECT_OT_TexDeleteOverride, MULTICAMPROJECT_OT_TexExportOverride)


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
