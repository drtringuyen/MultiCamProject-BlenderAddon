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


TARGET_ITEMS = (('ASK', "Ask", "ALB_: a popup with the choices, NOR_: a confirm"),
                ('OVERRIDE', "Override", "Into 02.Overrides as the override"),
                ('BAO', "Overwrite BAo_", "Over this object's BAo_ in the bake folder"),
                ('CANCEL', "Cancel", "Nothing happens"))


class MULTICAMPROJECT_OT_TexExportOverride(bpy.types.Operator):
    """ALB_: overwrite this object's BAo_ with it, or copy it into 02.Overrides as its
    override (a popup asks). NOR_: into 02.Overrides. Nothing switches: pick Load to use an
    override"""
    bl_idname = "multicamproject.tex_export_override"
    bl_label = "Export to Overrides"
    bl_options = {'REGISTER'}

    kind: EnumProperty(items=KIND_ITEMS, options={'SKIP_SAVE'})
    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    target: EnumProperty(items=TARGET_ITEMS, default='ASK', options={'SKIP_SAVE'})

    @staticmethod
    def _why_not(context, obj, kind, target='OVERRIDE'):
        """'' or why the texture can't go to `target` (OVERRIDE / BAO)."""
        from ..baking import cache, common
        if obj is None:
            return "No mesh"
        src = common.image_file(core.image(obj, kind))
        if not src or not cache.isfile(src):
            return f"{core.name(obj, kind)} has no file on disk yet"
        if target == 'BAO':
            return "" if kind == 'ALB' else "Only ALB_ can overwrite BAo_"
        if not core.folder(context.scene):
            return "No Overrides folder set (Project Setup)"
        if os.path.normcase(src) == os.path.normcase(
                os.path.normpath(core.override_path(context.scene, obj, kind))):
            return "On Load: the texture already is the file in 02.Overrides"
        return ""

    @staticmethod
    def _bao_path(scene, obj):
        """The object's BAo_ file - BAo_<name>.png in the bake folder when it has none yet."""
        from ..baking import common
        return (common.image_file(common.data(obj).ba_image)
                or common.texture_path(scene, common.ba_name(obj)))

    @classmethod
    def description(cls, context, props):
        obj = _target(props, context)
        if props.target == 'CANCEL':
            return "Nothing happens"
        if props.target == 'BAO':
            return cls._why_not(context, obj, props.kind, 'BAO') or (
                "Replace this object's BAo_ with this ALB_ (the old file to the Recycle Bin). "
                "ALB_ / NOR_ are outdated against it then")
        if props.target == 'OVERRIDE':
            return cls._why_not(context, obj, props.kind) or (
                "Copy it into 02.Overrides as its override (the old one to the Recycle Bin). "
                "Pick Load to use it")
        if props.kind == 'ALB':
            return "Overwrite BAo_ with this ALB_, or copy it into 02.Overrides (a popup asks)"
        return cls._why_not(context, obj, props.kind) or cls.__doc__

    @classmethod
    def poll(cls, context):
        return not _busy()

    def invoke(self, context, event):
        if self.target == 'CANCEL':
            return {'CANCELLED'}
        if self.target != 'ASK':            # a choice of the ALB_ popup
            return self.execute(context)
        obj = _target(self, context)
        if self.kind == 'ALB':
            return self._ask(context, obj)
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

    def _ask(self, context, obj):
        """ALB_: [Overwrite BAo_] [Override ALB_] [Cancel] - a choice that can't be done is
        greyed, with why."""
        from ..baking import cache
        if obj is None:
            self.report({'WARNING'}, "No mesh")
            return {'CANCELLED'}
        scene, name, kind = context.scene, obj.name, self.kind
        img = core.image(obj, kind)
        dirty = img is not None and img.is_dirty
        bao_path = self._bao_path(scene, obj)
        ovr_path = core.override_path(scene, obj, kind)
        choices = (('BAO', f"{'Overwrite' if cache.isfile(bao_path) else 'Write'} "
                           f"{os.path.basename(bao_path)}", 'IMAGE_DATA',
                    self._why_not(context, obj, kind, 'BAO')),
                   ('OVERRIDE', f"{'Override' if cache.isfile(ovr_path) else 'Export'} "
                                f"{os.path.basename(ovr_path)} (02.Overrides)", 'EXPORT',
                    self._why_not(context, obj, kind)))
        idname = MULTICAMPROJECT_OT_TexExportOverride.bl_idname

        def draw(menu, _context):
            col = menu.layout.column()
            for target, text, icon, why in choices:
                r = col.row()
                r.enabled = not why
                op = r.operator(idname, text=text + (f"  ({why})" if why else ""), icon=icon)
                op.kind, op.object_name, op.target = kind, name, target
            if dirty:
                r = col.row()
                r.alert = True
                r.label(text="Unsaved changes in Blender are not in the file - save first",
                        icon='ERROR')
            col.separator()
            op = col.operator(idname, text="Cancel", icon='CANCEL')
            op.kind, op.object_name, op.target = kind, name, 'CANCEL'

        context.window_manager.popup_menu(draw, title=f"{core.name(obj, kind)}: where to?",
                                          icon='QUESTION')
        return {'CANCELLED'}

    @staticmethod
    def _replace(src, dst):
        """Copy src over dst - the old dst to the Recycle Bin (Export module on)."""
        import shutil
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(dst):
            try:
                from ..export import fixes
                fixes.to_recycle_bin([dst])
            except ImportError:
                pass
        shutil.copy2(src, dst)

    def execute(self, context):
        from ..baking import cache, common
        if self.target == 'CANCEL':
            return {'CANCELLED'}
        target = 'BAO' if self.target == 'BAO' else 'OVERRIDE'
        obj = _target(self, context)
        why = self._why_not(context, obj, self.kind, target)
        if why:
            self.report({'WARNING'}, why)
            return {'CANCELLED'}
        src = common.image_file(core.image(obj, self.kind))
        dst = (self._bao_path(context.scene, obj) if target == 'BAO'
               else core.override_path(context.scene, obj, self.kind))
        if os.path.normcase(os.path.normpath(src)) == os.path.normcase(os.path.normpath(dst)):
            self.report({'WARNING'}, "Source and target are the same file")
            return {'CANCELLED'}
        try:
            self._replace(src, dst)
        except (OSError, RuntimeError) as e:
            self.report({'ERROR'}, f"{os.path.basename(dst)}: {e}")
            return {'CANCELLED'}
        if target == 'BAO':
            self._take_bao(context.scene, obj, dst)
        cache.clear()
        where = "the bake folder" if target == 'BAO' else "02.Overrides"
        self.report({'INFO'}, f"{os.path.basename(dst)} written to {where}")
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        return {'FINISHED'}

    @staticmethod
    def _take_bao(scene, obj, path):
        """The object's BAo_ is the new file now: its image reloaded (or made), stamped as a
        fresh BAo_ (ALB_ / NOR_ are outdated against it) and shown in MCP_ - an inpainted
        BAo_ copy (06) is switched off, so this one shows."""
        from ..baking import common, engine, fingerprint
        d = common.data(obj)
        img = d.ba_image
        if img is None:
            name = common.ba_name(obj)
            img = bpy.data.images.get(name) or bpy.data.images.load(path, check_existing=False)
            img.name = name
            d.ba_image = img
        img.colorspace_settings.name = 'sRGB'
        engine.link_file(img, path)
        try:
            from ..baking import owned
            owned.add(scene, [path])
        except ImportError:
            pass
        d.ba_size = img.size[0] or common.resolution(obj)
        d.ba_fingerprint = fingerprint.stamp_source(obj)
        if d.ba_inpaint is not None:
            d.ba_use_inpaint = False
        try:
            from ..image_editor import sources
            sources.relink(sources.Source('BAO', obj), scene)
        except ImportError:
            cam = getattr(obj, "multicamproject_cam", None)
            if cam is not None and cam.is_setup:
                from ..camera_project import core as cp
                cp.ensure_material(obj)


@persistent
def _on_load(_):
    bpy.app.timers.register(_migrate_all, first_interval=0.5)


def _migrate_all():
    try:
        for sc in bpy.data.scenes:
            core.migrate(sc)
            core.relink_loads(sc)   # Load at the room's 02.Overrides, whatever relinked it
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
