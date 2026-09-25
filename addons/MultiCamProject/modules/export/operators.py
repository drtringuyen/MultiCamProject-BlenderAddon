import os

import bpy
from bpy.props import StringProperty

from ..baking import cache, common
from . import autofix, fbx, fixes, status


def _poll(context):
    return context.mode == 'OBJECT'


def select_and_frame_row(context, es):
    """The status list's clicked row: select that object only and frame it."""
    coll = common.export_collection(context.scene)
    if coll is None or not 0 <= es.active_index < len(coll.all_objects):
        return
    obj = coll.all_objects[es.active_index]
    vl = context.view_layer
    if vl.objects.get(obj.name) != obj:
        return
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    vl.objects.active = obj
    screen = context.window.screen if context.window else None
    for area in (screen.areas if screen else []):
        if area.type == 'VIEW_3D':
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is not None:
                with context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_selected()
            break


class MULTICAMPROJECT_OT_ExportAdd(bpy.types.Operator):
    """Link the selected meshes into the EXPORT collection (created in the active scene if
    missing). They stay in their collections too"""
    bl_idname = "multicamproject.export_add"
    bl_label = "Add to EXPORT"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(context.selected_objects)

    def execute(self, context):
        added, skipped = fixes.add_to_export(context.scene, context.selected_objects)
        if skipped:
            self.report({'INFO'}, f"Not meshes, skipped: {', '.join(skipped)}")
        self.report({'INFO'}, f"Added to EXPORT: {', '.join(added) or 'nothing new'}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportRemove(bpy.types.Operator):
    """Unlink the selected objects from EXPORT (an object only in EXPORT moves to the
    scene's root collection)"""
    bl_idname = "multicamproject.export_remove"
    bl_label = "Remove from EXPORT"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(context.selected_objects) and \
            common.export_collection(context.scene) is not None

    def execute(self, context):
        fixes.remove_from_export(context.scene, context.selected_objects)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportRename(bpy.types.Operator):
    """Give the EXPORT objects clean names (A-Z, 0-9, _): the object, its mesh, MCP_, MAT_,
    ALB_/NOR_ images and their files. Conflicts get _2"""
    bl_idname = "multicamproject.export_rename"
    bl_label = "Rename Objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        from ..baking import naming
        log = fixes.rename_all(common.export_objects(context.scene), naming.scheme(context.scene))
        cache.clear()
        for sev, text in log:
            self.report({sev}, text)
        if not log:
            self.report({'INFO'}, "All names are right")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportFixTransforms(bpy.types.Operator):
    """Apply location, rotation and scale into the meshes of EXPORT and put each origin at
    the bottom center (written into the .blend). The projection is unaffected"""
    bl_idname = "multicamproject.export_fix_transforms"
    bl_label = "Fix Transforms"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        n = 0
        for obj in status.objects_with(context.scene, {'TRANSFORM'}):
            why = fixes.fix_transform(obj)
            cache.clear(obj)
            if why:
                self.report({'WARNING'}, f"{obj.name}: skipped, {why}")
            else:
                n += 1
        self.report({'INFO'}, f"Fixed {n} transform(s)")
        return {'FINISHED'}


def _rebake(op, context, objs):
    from ..baking import operators as bake_ops
    s = common.settings(context.scene)
    ok = 0
    for obj in objs:
        src = common.data(obj).nor_source_used or s.nor_source
        done, failed = bake_ops.bake_objects(context, [obj], albedo=True, nor_source=src)
        ok += len(done)
        for name, err in failed:
            op.report({'ERROR'}, f"{name}: {err}")
    cache.clear()
    op.report({'INFO'}, f"Baked {ok} of {len(objs)}")
    return {'FINISHED'} if ok else {'CANCELLED'}


class MULTICAMPROJECT_OT_ExportUpdateOutdated(bpy.types.Operator):
    """Bake the outdated EXPORT objects again: albedo, then the normal map with the source
    it was made with"""
    bl_idname = "multicamproject.export_update_outdated"
    bl_label = "Update Outdated"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        return _rebake(self, context, status.objects_with(context.scene, {'OUTDATED'}))


class MULTICAMPROJECT_OT_ExportBakeMissing(bpy.types.Operator):
    """Bake the EXPORT objects that have uv_normal but no (or a broken) ALB/NOR/MAT_"""
    bl_idname = "multicamproject.export_bake_missing"
    bl_label = "Bake Missing"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        objs = [o for o in status.objects_with(context.scene, {'NOT_BAKED', 'FILE', 'TEXTURE'})
                if common.has_uv_normal(o)]
        return _rebake(self, context, objs)


class MULTICAMPROJECT_OT_ExportDeleteScene(bpy.types.Operator):
    """Delete this scene with the objects, cameras, images and collections only it uses.
    Objects that are also in the active scene stay"""
    bl_idname = "multicamproject.export_delete_scene"
    bl_label = "Delete Scene"
    bl_options = {'REGISTER', 'UNDO'}

    scene_name: StringProperty()

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title=f"Delete scene '{self.scene_name}'?",
            message="Its objects, cameras, images and collections that no other scene uses "
                    "are removed.", confirm_text="Delete", icon='WARNING')

    def execute(self, context):
        target = bpy.data.scenes.get(self.scene_name)
        if target is None or target == context.scene:
            self.report({'ERROR'}, "Scene not found, or it is the active scene")
            return {'CANCELLED'}
        objs, datas, imgs, colls = fixes.delete_scene(target, context.scene)
        cache.clear()
        self.report({'INFO'}, f"Deleted '{self.scene_name}': {objs} objects, {datas} data, "
                              f"{imgs} images, {colls} collections")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportCleanTextures(bpy.types.Operator):
    """Move the ALB_/NOR_ PNGs of the Textures folder that no image of this .blend uses
    to the Recycle Bin. Other files in the folder are never touched"""
    bl_idname = "multicamproject.export_clean_textures"
    bl_label = "Clean Textures Folder"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        from . import checks
        n = len(checks.unused_textures(context.scene))
        return context.window_manager.invoke_confirm(
            self, event, title=f"Move {n} unused PNG(s) to the Recycle Bin?",
            message="No image of this .blend uses them.", confirm_text="Move", icon='WARNING')

    def execute(self, context):
        from . import checks
        gone = fixes.to_recycle_bin(checks.unused_textures(context.scene))
        self.report({'INFO'}, f"Moved {len(gone)} PNG(s) to the Recycle Bin")
        return {'FINISHED'}


def _numbering_poll(cls, context):
    from ..baking import naming
    if not _poll(context):
        return False
    if naming.scheme(context.scene) is None:
        cls.poll_message_set("Set a Name Prefix first")
        return False
    return bool(common.export_objects(context.scene))


def _list_object(context):
    coll = common.export_collection(context.scene)
    es = context.scene.multicamproject_export
    if coll is None or not 0 <= es.active_index < len(coll.all_objects):
        return None
    return coll.all_objects[es.active_index]


class MULTICAMPROJECT_OT_ExportMove(bpy.types.Operator):
    """Move the selected row up / down: its ## swaps with the neighbour's, and all objects
    are numbered 00 -> n (MAT_, ALB_/NOR_ and the files follow)"""
    bl_idname = "multicamproject.export_move"
    bl_label = "Move"
    bl_options = {'REGISTER', 'UNDO'}

    step: bpy.props.IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _numbering_poll(cls, context) and _list_object(context) is not None

    def execute(self, context):
        from ..baking import naming
        obj = _list_object(context)
        objs = common.export_objects(context.scene)
        if obj not in objs:
            return {'CANCELLED'}
        for sev, text in fixes.move(objs, naming.scheme(context.scene), obj, self.step):
            if sev != 'INFO':
                self.report({sev}, text)
        cache.clear()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportRenumber(bpy.types.Operator):
    """Number the EXPORT objects 00 -> n in their current order, without gaps"""
    bl_idname = "multicamproject.export_renumber"
    bl_label = "Renumber 00 -> n"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _numbering_poll(cls, context)

    def execute(self, context):
        from ..baking import naming
        log = fixes.renumber(common.export_objects(context.scene), naming.scheme(context.scene))
        cache.clear()
        self.report({'INFO'}, f"Renumbered {sum(t.startswith('Renamed') for _s, t in log)} object(s)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportFBX(bpy.types.Operator):
    """Fix what can be fixed automatically (names, transforms, a Smart UV uv_normal where
    there is none, rebake outdated / not baked), then export the EXPORT meshes as FBX for
    Unity. A dialog lists everything first. The objects stay in Final afterwards"""
    bl_idname = "multicamproject.export_fbx"
    bl_label = "Export FBX"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(common.export_objects(context.scene))

    def invoke(self, context, event):
        self._plan = autofix.plan(context.scene)
        return context.window_manager.invoke_props_dialog(
            self, width=460, title="Fix and Export", confirm_text="Fix + Export")

    def draw(self, context):
        p = getattr(self, "_plan", None) or autofix.plan(context.scene)
        col = self.layout.column(align=True)
        rows = [(f"Rename {len(p.rename)}", [f"{o.name} -> {n}" for o, n in p.rename.items()]),
                (f"Fix transforms of {len(p.transforms)}", [o.name for o in p.transforms]),
                (f"Make uv_normal (Smart UV) on {len(p.make_uv)}", [o.name for o in p.make_uv]),
                (f"Bake {len(p.bake)} (outdated / not baked)", [o.name for o in p.bake]),
                (f"Move {len(p.unused)} unused ALB_/NOR_ PNG(s) to the Recycle Bin",
                 [os.path.basename(x) for x in p.unused])]
        if not any(names for _t, names in rows):
            col.label(text="Nothing to fix", icon='CHECKMARK')
        for title, names in rows:
            if names:
                col.label(text=title, icon='MODIFIER')
                for n in names[:8]:
                    col.label(text=f"      {n}")
                if len(names) > 8:
                    col.label(text=f"      ... and {len(names) - 8} more")
        if p.bake:
            col.label(text="Baking freezes Blender until it is done", icon='TIME')
        if p.warnings:
            col.separator()
            col.label(text="Not fixed automatically:", icon='ERROR')
            for w in p.warnings[:10]:
                r = col.row()
                r.alert = True
                r.label(text=f"      {w}")
            if len(p.warnings) > 10:
                col.label(text=f"      ... and {len(p.warnings) - 10} more (see the report)")

    def execute(self, context):
        p = getattr(self, "_plan", None) or autofix.plan(context.scene)
        log = []
        autofix.run(context, p, lambda sev, text: log.append((sev, text)))
        log += [('WARNING', w) for w in p.warnings]
        for sev, text in log:
            if sev != 'INFO':
                self.report({sev}, text)
        try:
            files, missing, report = fbx.export(context, log)
        except (RuntimeError, OSError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for name, miss in missing.items():
            self.report({'WARNING'}, f"{name}: {', '.join(miss)}")
        self.report({'INFO'}, f"Exported {', '.join(files)}")
        return {'FINISHED'}


_classes = (MULTICAMPROJECT_OT_ExportAdd, MULTICAMPROJECT_OT_ExportRemove,
            MULTICAMPROJECT_OT_ExportRename, MULTICAMPROJECT_OT_ExportFixTransforms,
            MULTICAMPROJECT_OT_ExportUpdateOutdated, MULTICAMPROJECT_OT_ExportBakeMissing,
            MULTICAMPROJECT_OT_ExportDeleteScene, MULTICAMPROJECT_OT_ExportCleanTextures,
            MULTICAMPROJECT_OT_ExportMove, MULTICAMPROJECT_OT_ExportRenumber,
            MULTICAMPROJECT_OT_ExportFBX)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
