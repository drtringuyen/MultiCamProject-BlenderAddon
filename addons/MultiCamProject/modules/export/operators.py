import os

import bpy
from bpy.props import StringProperty

from ..baking import cache, common, jobs
from . import autofix, fbx, fixes, status


def _poll(context):
    return context.mode == 'OBJECT' and not jobs.busy()


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


def _rebake_steps(context, objs):
    from ..baking import operators as bake_ops
    s = common.settings(context.scene)
    done, failed = [], []
    try:
        for obj in objs:
            src = common.data(obj).nor_source_used or s.nor_source
            d, f = yield from bake_ops.bake_objects_steps(context, [obj], albedo=True,
                                                          nor_source=src)
            done += d
            failed += f
    finally:
        cache.clear()
    return done, failed


def _rebake(op, context, objs, title):
    if not objs:
        op.report({'INFO'}, "Nothing to bake")
        return {'CANCELLED'}
    n = len(objs)

    def finish(result, error):
        if result is None:
            return []
        done, failed = result
        lines = [('ERROR', f"{name}: {err}") for name, err in failed]
        stopped = " (stopped)" if len(done) + len(failed) < n else ""
        return lines + [('INFO', f"Baked {len(done)} of {n}{stopped}")]
    return jobs.start(op, context, title, [o.name for o in objs], _rebake_steps(context, objs),
                      finish)


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
        return _rebake(self, context, status.objects_with(context.scene, {'OUTDATED'}),
                       "Update Outdated")


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
        return _rebake(self, context, objs, "Bake Missing")


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
    """Move this .blend's old ALB_/NOR_ PNGs in the bake folder (ones it used or baked
    before, no image uses now) to the Recycle Bin. Another file's bakes in a shared
    folder, and every other file, are never touched"""
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
            self, event, title=f"Move {n} old bake(s) of this file to the Recycle Bin?",
            message="This .blend used or baked them before; none of its images uses them now.",
            confirm_text="Move", icon='WARNING')

    def execute(self, context):
        from . import checks
        gone = fixes.to_recycle_bin(checks.unused_textures(context.scene))
        self.report({'INFO'}, f"Moved {len(gone)} PNG(s) to the Recycle Bin")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportCleanExportTextures(bpy.types.Operator):
    """Check the export's Textures folder: every ALB_/NOR_ PNG the export of EXPORT does not
    write (old names, removed objects, another .blend's exports) is listed and, after you
    confirm, moved to the Recycle Bin"""
    bl_idname = "multicamproject.export_clean_export_textures"
    bl_label = "Clean Export Textures"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        from . import checks
        self._unused = checks.unused_export_textures(context.scene)
        if not self._unused:
            n = len(checks.pngs(checks.export_textures_dir(context.scene)))
            self.report({'INFO'}, f"Export Textures/ is clean: {n} texture(s), all from this export")
            return {'FINISHED'}
        return context.window_manager.invoke_props_dialog(
            self, width=420, title=f"Move {len(self._unused)} unused texture(s) to the Recycle Bin?",
            confirm_text="Move")

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text="Not written by this export, not used by this .blend:")
        others = sum(not own for _p, own in self._unused)
        shown = self._unused[:20]
        for p, own in shown:
            col.label(text=os.path.basename(p) + ("" if own else "   (another .blend)"),
                      icon='FILE_IMAGE')
        if len(self._unused) > len(shown):
            col.label(text=f"... and {len(self._unused) - len(shown)} more")
        if others:
            col.separator()
            col.label(text=f"{others} came from another .blend - it may still use them",
                      icon='ERROR')

    def execute(self, context):
        from . import checks
        unused = getattr(self, "_unused", None)
        if unused is None:
            unused = checks.unused_export_textures(context.scene)
        gone = fixes.to_recycle_bin([p for p, _own in unused])
        cache.clear()
        self.report({'INFO'}, f"Moved {len(gone)} unused texture(s) from Export Textures/ "
                              "to the Recycle Bin")
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


def _auto_names_on(context):
    """Export with Auto Names off: on first (numbers 00 -> n, names in line). True if it was off."""
    es = context.scene.multicamproject_export
    if es.auto_names:
        return False
    es.auto_names = True
    return True


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
        _auto_names_on(context)
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
                (f"Move {len(p.unused)} old bake(s) of this file to the Recycle Bin",
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
            col.label(text="The progress shows in the status bar and above the list. "
                           "Esc: stop after the object", icon='TIME')
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
        if _auto_names_on(context):
            self._plan = None           # the names changed: plan again
        p = getattr(self, "_plan", None) or autofix.plan(context.scene)
        names = [o.name for o in p.bake] + [FBX_ITEM]
        return jobs.start(self, context, "Fix + Export", names, _export_steps(context, p),
                          _export_finish, light=(FBX_ITEM,))


FBX_ITEM = "Write FBX"


def _export_steps(context, p):
    """ExportFBX's work: the fixes and bakes of plan `p`, then the FBX. A generator (jobs);
    returns (log, files, missing) - files None when stopped or the export failed."""
    log = []
    ok = yield from autofix.run_steps(context, p, lambda sev, text: log.append((sev, text)))
    log += [('WARNING', w) for w in p.warnings]
    if not ok:
        jobs.item_end(FBX_ITEM, "stopped", skipped=True)
        return log, None, {}
    jobs.item_start(FBX_ITEM)
    try:
        files, missing, _report = yield from fbx.export_steps(context, log)
    except (RuntimeError, OSError) as e:
        jobs.item_end(FBX_ITEM, str(e))
        return log + [('ERROR', str(e))], None, {}
    jobs.item_end(FBX_ITEM)
    return log, files, missing


def _export_finish(result, error):
    if result is None:
        return []
    log, files, missing = result
    lines = [(sev, text) for sev, text in log if sev != 'INFO']
    lines += [('WARNING', f"{name}: {', '.join(miss)}") for name, miss in missing.items()]
    if files:
        lines.append(('INFO', f"Exported {', '.join(files)}"))
    return lines


def uv_candidates(objs):
    """Meshes without uv_normal that can get one now (not while the Decimate is live: the
    unwrap would be collapsed with it)."""
    from . import checks
    return [o for o in objs if not common.has_uv_normal(o) and not checks.live_decimate(o)]


class MULTICAMPROJECT_OT_ExportMakeUV(bpy.types.Operator):
    """Make uv_normal by Smart UV Project (non-overlapping, 0-1, margin from the bake
    settings) on the meshes that have none. The active and render UV maps stay as they are.
    A low poly with an unapplied Decimate is skipped (apply it first, 03)"""
    bl_idname = "multicamproject.export_make_uv"
    bl_label = "Make uv_normal"
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(items=(('EXPORT', "EXPORT", "Every mesh in EXPORT"),
                                         ('SELECTED', "Selected", "The selected meshes")),
                                  default='EXPORT')

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            cls.poll_message_set("Object Mode only")
            return False
        return True

    def execute(self, context):
        objs = (common.export_objects(context.scene) if self.scope == 'EXPORT'
                else common.selected_meshes(context))
        todo = uv_candidates(objs)
        if not todo:
            self.report({'INFO'}, "Every mesh has uv_normal (or its Decimate is not applied)")
            return {'CANCELLED'}
        for obj in todo:
            autofix.make_uv_normal(context, obj)
            self.report({'INFO'}, f"{obj.name}: uv_normal made by Smart UV Project")
        cache.clear()
        return {'FINISHED'}


def _steps_of(context, name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None, None
    _objs, per, _g = status.scene_status(context.scene)
    st = per.get(obj.name)
    return obj, (autofix.object_steps(obj, st.issues) if st else None)


class MULTICAMPROJECT_OT_ExportFixObject(bpy.types.Operator):
    """Make this object ready for the export"""
    bl_idname = "multicamproject.export_fix_object"
    bl_label = "Fix Object"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, props):
        obj, steps = _steps_of(context, props.object_name)
        todo = autofix.describe(steps) if steps else []
        if not todo:
            return f"{props.object_name}: nothing the add-on can fix - see its notes under the list"
        return f"{props.object_name}: " + ", then ".join(todo)

    def invoke(self, context, event):
        obj, steps = _steps_of(context, self.object_name)
        todo = autofix.describe(steps) if steps else []
        if not todo:
            self.report({'INFO'}, f"{self.object_name}: nothing the add-on can fix here")
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title=f"Fix {self.object_name}",
            message="; ".join(t.capitalize() for t in todo) + ".", confirm_text="Fix")

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}
        lines = []
        name = self.object_name

        def steps():
            try:
                yield from autofix.run_object_steps(context, obj, lambda s, t: lines.append((s, t)))
            except RuntimeError as e:
                lines.append(('ERROR', f"{name}: {e}"))

        def finish(result, error):
            return lines
        return jobs.start(self, context, f"Fix {name}", [name], steps(), finish)


# wm[_SOLO_KEY][<space pointer>] = {"obj": soloed object, "hidden": objects solo unhid}
_SOLO_KEY = "multicamproject_export_solo"


def solo_original(obj):
    """The original `obj` was made from: its Bake Source, else its Remesh source."""
    d = common.data(obj)
    o = d.bake_source or d.source
    return o if o is not None and o != obj else None


def _solo_states(context):
    wm = context.window_manager
    if _SOLO_KEY not in wm:
        wm[_SOLO_KEY] = {}
    return wm[_SOLO_KEY]


def is_soloed(context, obj):
    """True while `obj` is soloed (local view) from its EXPORT row in this 3D view."""
    space = context.space_data
    if space is None or space.type != 'VIEW_3D' or space.local_view is None:
        return False
    st = context.window_manager.get(_SOLO_KEY, {}).get(str(space.as_pointer()))
    return st is not None and st.get("obj") == obj.name


class MULTICAMPROJECT_OT_ExportSolo(bpy.types.Operator):
    """Solo this object and its original (Bake Source / Remesh source) in local view.
    Click again to leave solo"""
    bl_idname = "multicamproject.export_solo"
    bl_label = "Solo Object + Original"

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context) and context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}
        space = context.space_data
        states = _solo_states(context)
        key = str(space.as_pointer())
        again = is_soloed(context, obj)
        if space.local_view is not None:        # leave the current solo first
            bpy.ops.view3d.localview(frame_selected=False)
            st = states.get(key)
            for name in (st.to_dict().get("hidden", []) if st else []):
                o = bpy.data.objects.get(name)
                if o is not None and context.view_layer.objects.get(name) == o:
                    o.hide_set(True)
            if key in states:
                del states[key]
            if again:
                return {'FINISHED'}
        vl = context.view_layer
        orig = solo_original(obj)
        keep, hidden = [], []
        for o in (obj, orig):
            if o is None:
                continue
            if vl.objects.get(o.name) != o:
                self.report({'WARNING'}, f"'{o.name}' is in an excluded collection - not soloed")
                continue
            if o.hide_get():
                o.hide_set(False)
                hidden.append(o.name)
            if not o.visible_get():
                self.report({'WARNING'}, f"'{o.name}' is in a hidden collection - not soloed")
                continue
            keep.append(o)
        if obj not in keep:
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        for o in keep:
            o.select_set(True)
        vl.objects.active = obj
        bpy.ops.view3d.localview(frame_selected=True)
        states[key] = {"obj": obj.name, "hidden": hidden}
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ExportApplyDecimate(bpy.types.Operator):
    """Apply the Decimate (03) of the EXPORT objects that still have it live, and unwrap
    uv_normal again (Smart UV) - the old one was made on the dense mesh. Bake them next.
    Ctrl+Z brings the dense meshes back"""
    bl_idname = "multicamproject.export_apply_decimate"
    bl_label = "Apply Decimate + Unwrap"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        objs = status.objects_with(context.scene, {'DECIMATE'})
        if not objs:
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title=f"Apply the Decimate of {len(objs)} object(s)?",
            message=", ".join(o.name for o in objs) + " - then unwrap uv_normal again.",
            confirm_text="Apply")

    def execute(self, context):
        n = 0
        for obj in status.objects_with(context.scene, {'DECIMATE'}):
            try:
                before, after = autofix.apply_decimate_unwrap(context, obj)
            except RuntimeError as e:
                self.report({'ERROR'}, f"{obj.name}: {e}")
                continue
            n += 1
            self.report({'INFO'}, f"{obj.name}: {before:,} -> {after:,} faces, uv_normal unwrapped")
        cache.clear()
        return {'FINISHED'} if n else {'CANCELLED'}


_classes = (MULTICAMPROJECT_OT_ExportFixObject, MULTICAMPROJECT_OT_ExportApplyDecimate,
            MULTICAMPROJECT_OT_ExportMakeUV, MULTICAMPROJECT_OT_ExportAdd, MULTICAMPROJECT_OT_ExportRemove,
            MULTICAMPROJECT_OT_ExportRename, MULTICAMPROJECT_OT_ExportFixTransforms,
            MULTICAMPROJECT_OT_ExportUpdateOutdated, MULTICAMPROJECT_OT_ExportBakeMissing,
            MULTICAMPROJECT_OT_ExportDeleteScene, MULTICAMPROJECT_OT_ExportCleanTextures,
            MULTICAMPROJECT_OT_ExportCleanExportTextures, MULTICAMPROJECT_OT_ExportMove, MULTICAMPROJECT_OT_ExportRenumber,
            MULTICAMPROJECT_OT_ExportFBX, MULTICAMPROJECT_OT_ExportSolo)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
