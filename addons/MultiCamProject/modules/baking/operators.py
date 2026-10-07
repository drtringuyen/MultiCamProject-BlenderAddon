import os

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import common, engine, gn_final, handmade, jobs, normal, route

SCOPES = (('SELECTED', "Selected", "The selected meshes"),
          ('EXPORT', "EXPORT", "Every mesh in the EXPORT collection"))


def scope_objects(context, scope, bake=True):
    """The meshes of `scope`; to bake (`bake`) never handmade ones - baked by hand."""
    objs = (common.export_objects(context.scene) if scope == 'EXPORT'
            else common.selected_meshes(context))
    return [o for o in objs if not (bake and handmade.is_handmade(o))]


def _object_mode(context):
    return context.mode == 'OBJECT' and not jobs.busy()


def _object_or_edit(context):
    """Object Mode, or Edit Mode (the operator leaves it itself: after a UV edit the
    Rebake must not be a greyed-out button)."""
    return context.mode in {'OBJECT', 'EDIT_MESH'} and not jobs.busy()


def _to_object_mode(context):
    if context.mode != 'OBJECT' and context.active_object is not None:
        bpy.ops.object.mode_set(mode='OBJECT')


def _split_handmade(context):
    """(handmade meshes in a UV edit, the meshes 06 bakes). A handmade mesh without
    uv_old has nothing to bake (its textures are on uv_normal already): left out."""
    objs = common.selected_meshes(context)
    obj = context.active_object
    if obj is not None and obj.type == 'MESH' and obj not in objs:
        objs.append(obj)
    hand = [o for o in objs if handmade.is_handmade(o) and handmade.has_uv_old(o)]
    return hand, [o for o in objs if not handmade.is_handmade(o)]


def bake_objects(context, objs, albedo=True, nor_source=None, log=None):
    """bake_objects_steps, blocking."""
    return jobs.run_sync(bake_objects_steps(context, objs, albedo, nor_source, log))


def bake_objects_steps(context, objs, albedo=True, nor_source=None, log=None):
    """Albedo and/or normal bake for `objs`, one object after the other (each one an item of
    the job's progress list; a stop request ends the run between objects). An object with a
    Bake Source but no current BA_ bakes from its source first; a normal map at another size
    than the albedo remakes the albedo at that size too. A generator (jobs); returns
    (done, [(object name, error)])."""
    done, failed = [], []
    try:
        for obj in objs:
            if jobs.stop_requested():
                jobs.skip_rest()
                break
            name = obj.name
            jobs.item_start(name)
            try:
                if handmade.is_handmade(obj):
                    raise RuntimeError("handmade - baked by hand, not by the add-on")
                # an excluded / hidden collection bakes nothing: shown for the bake
                with common.shown(context, [obj]):
                    yield from _bake_one(context, obj, albedo, nor_source,
                                         common.resolution(obj, context.scene), log)
                done.append(obj)
                jobs.item_end(name)
            except Exception as e:      # one object failing must not stop the others
                failed.append((name, str(e)))
                jobs.item_end(name, str(e))
    finally:
        if done:
            from . import owned
            owned.record_used(context.scene)    # the new ALB_/NOR_ are this file's own
    return done, failed


def _bake_one(context, obj, albedo, nor_source, res, log):
    """bake_objects' work for one object (a generator)."""
    # one resolution: a normal map alone at a new size remakes the albedo too
    with_albedo = albedo or (nor_source is not None
                             and common.data(obj).alb_size not in (0, res))
    with_normal = nor_source is not None
    if with_albedo:
        if not common.has_uv_normal(obj):
            raise RuntimeError(f"no {common.UV_NORMAL}")
        why = common.uv_collapsed_text(obj)
        if why:
            raise RuntimeError(why)
        if route.uses_projection(obj) and route.has_projection(obj):
            from ..camera_project import core as cp
            if not cp.keep_mode(obj):
                # a blank Mode projects nothing - Cycles would only say "no UV map"
                raise RuntimeError("projection Mode is blank - pick Sharp / Smooth / Combined "
                                   "in 05 Projection Painting, then bake again")
        if engine.needs_source_bake(obj) and log:
            log(f"{obj.name}: baked from {common.data(obj).bake_source.name} first")
        yield jobs.Step("Albedo", 0.1)
        yield from engine.bake_albedo_steps(context, obj)
    if with_normal:
        why = normal.problem(obj, context.scene, nor_source)
        if why:
            raise RuntimeError(why)
        yield jobs.Step("Normal", 0.6 if with_albedo else 0.0)
        yield from normal.generate_steps(context, obj, nor_source)


def _finisher(what, n):
    """jobs.start's finish for a (done, failed) result."""
    def finish(result, error):
        if result is None:
            return []
        done, failed = result
        lines = [('ERROR', f"{name}: {err}") for name, err in failed]
        if len(done) + len(failed) < n:
            lines.append(('WARNING', f"Stopped after {len(done) + len(failed)} of {n}"))
        if done:
            lines.append(('INFO', f"{what}: {', '.join(o.name for o in done)}"))
        return lines
    return finish


def _start_bake(op, context, title, objs, albedo, nor_source, what):
    """bake_objects as a job (the progress in the status bar and the panel)."""
    names = [o.name for o in objs]
    log = []
    finish = _finisher(what, len(objs))
    return jobs.start(op, context, title, names,
                      bake_objects_steps(context, objs, albedo, nor_source,
                                         log=lambda t: log.append(t)),
                      lambda r, e: [('INFO', t) for t in log] + finish(r, e))


def _source_steps(context, objs):
    done, failed = [], []
    for obj in objs:
        if jobs.stop_requested():
            jobs.skip_rest()
            break
        jobs.item_start(obj.name)
        try:
            yield from engine.bake_from_source_steps(context, obj)
            done.append(obj)
            jobs.item_end(obj.name)
        except Exception as e:
            failed.append((obj.name, str(e)))
            jobs.item_end(obj.name, str(e))
    return done, failed


def source_poll_problem(obj):
    """Why 04 Bake from Source is off for `obj` ('' = it can run)."""
    why = engine.source_problem(obj)
    if why:
        return why
    try:
        from ..export import checks
    except ImportError:
        return ""
    outside, overlap = checks.uv_stats(obj)
    if outside > 0.001:
        return f"{common.UV_NORMAL}: {outside:.1%} outside 0-1 - fix the unwrap"
    if overlap > 0.01:
        return f"{common.UV_NORMAL}: {overlap:.1%} overlapping - fix the unwrap"
    return ""


class MULTICAMPROJECT_OT_FitCage(bpy.types.Operator):
    """Fit Cage: each selected low poly's own Cage to what reaches 99% of it, measured at its
    last Bake from Source. Then bake from source again"""
    bl_idname = "multicamproject.fit_cage"
    bl_label = "Fit Cage"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and common.data(obj).ba_fit_cage > 0

    def execute(self, context):
        objs = [o for o in common.selected_meshes(context) if common.data(o).ba_fit_cage > 0]
        if context.active_object not in objs:
            objs.append(context.active_object)
        for o in objs:      # the others only grow: a smaller Cage would outdate them for nothing
            d = common.data(o)
            if o == context.active_object or d.ba_fit_cage > d.cage:
                d.cage = d.ba_fit_cage
        d = common.data(context.active_object)
        self.report({'INFO'}, f"Cage {d.cage:.3f} m - bake from source again")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_BakeFromSource(bpy.types.Operator):
    """04 Bake from Source: the Bake Source's colors into BAo_ and (Original Normal = From
    Original's Surface only) its surface into BNo_, on
    uv_normal at the scene's resolution (Selected to Active, Cage). Files in the bake folder, under
    the projection - not the final textures (06 Bake Final makes those)"""
    bl_idname = "multicamproject.bake_from_source"
    bl_label = "Bake from Source"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            cls.poll_message_set("Object Mode only")
            return False
        objs = common.selected_meshes(context)
        if not objs:
            cls.poll_message_set("Select the low poly")
            return False
        for o in objs:
            why = source_poll_problem(o)
            if why:
                cls.poll_message_set(f"{o.name}: {why}")
                return False
        return True

    def execute(self, context):
        objs = common.selected_meshes(context)
        return jobs.start(self, context, "Bake from Source", [o.name for o in objs],
                          _source_steps(context, objs),
                          _finisher("Baked from source", len(objs)))


class MULTICAMPROJECT_OT_BakeSetFinal(bpy.types.Operator):
    """Switch between the projection setup and the baked result (GN-Final)"""
    bl_idname = "multicamproject.bake_set_final"
    bl_label = "Projection / Final"
    bl_options = {'REGISTER', 'UNDO'}

    state: BoolProperty(name="Final", default=True)
    scope: EnumProperty(items=SCOPES, default='SELECTED')

    @classmethod
    def description(cls, context, props):
        who = "every mesh in EXPORT" if props.scope == 'EXPORT' else "the selected meshes"
        if props.state:
            return (f"Final for {who}: the baked MAT_, Color and uv_normal only - the projection "
                    "is switched off")
        return f"Projection for {who}: the camera projection setup, editable again"

    @classmethod
    def poll(cls, context):
        return _object_mode(context)

    def execute(self, context):
        objs = scope_objects(context, self.scope)
        if not objs:
            self.report({'WARNING'}, "No meshes")
            return {'CANCELLED'}
        for obj in objs:
            gn_final.set_final(obj, context.scene, self.state)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_BakeAlbedo(bpy.types.Operator):
    """Bake the Processing material (BAo_ + projection, by the Bake Route) into ALB_<name> on uv_normal and
    build MAT_. Each object then switches to Final"""
    bl_idname = "multicamproject.bake_albedo"
    bl_label = "Bake Albedo"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(items=SCOPES, default='SELECTED')
    with_normal: BoolProperty(name="With Normal", default=False,
                              description="Make NOR_ right after, with the Normal Source")

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            return False
        objs = common.selected_meshes(context)
        if not objs:
            cls.poll_message_set("Select meshes")
            return False
        if not all(common.has_uv_normal(o) for o in objs):
            cls.poll_message_set(f"Every selected mesh needs a '{common.UV_NORMAL}' UV map")
            return False
        return True

    def execute(self, context):
        objs = scope_objects(context, self.scope)
        src = common.settings(context.scene).nor_source if self.with_normal else None
        return _start_bake(self, context, "Bake Albedo", objs, True, src, "Baked")


class MULTICAMPROJECT_OT_BakeNormal(bpy.types.Operator):
    """Make NOR_<name> with the Normal Source (16-bit PNG) and put it into MAT_"""
    bl_idname = "multicamproject.bake_normal"
    bl_label = "Make Normal"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(items=SCOPES, default='SELECTED')

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            return False
        objs = common.selected_meshes(context)
        if not objs:
            cls.poll_message_set("Select meshes")
            return False
        src = common.settings(context.scene).nor_source
        for o in objs:
            why = normal.problem(o, context.scene, src)
            if why:
                cls.poll_message_set(f"{o.name}: {why}")
                return False
        return True

    def execute(self, context):
        s = common.settings(context.scene)
        objs = scope_objects(context, self.scope)
        return _start_bake(self, context, "Make Normal", objs, False, s.nor_source, "Normal map")


class MULTICAMPROJECT_OT_BakeNormalMode(bpy.types.Operator):
    """Lite: normal map from the albedo's high-pass (seconds).
AI: predicted from the albedo by an AI model (minutes at 8K on the CPU)"""
    bl_idname = "multicamproject.bake_normal_mode"
    bl_label = "Normal Mode"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(items=(('LITE', "Lite", "High-pass from the albedo"),
                              ('AI', "AI", "AI model from the albedo")))

    @classmethod
    def description(cls, context, props):
        if props.mode == 'AI':
            need = normal.ai.missing()
            return ("Normal map predicted from the albedo by the AI model - minutes at 8K on the "
                    "CPU. Compare with Lite using the times below"
                    + (f". Needs {need}: Set up AI first" if need else ""))
        return "Normal map from the albedo's high-pass - seconds"

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        s = common.settings(context.scene)
        if self.mode == 'AI':
            need = normal.ai.missing()
            if need:
                self.report({'ERROR'}, f"AI needs {need} - click Set up AI")
                return {'CANCELLED'}
            if s.nor_source != 'AI':
                s.nor_lite_source = s.nor_source        # Lite comes back to this method
            s.nor_source = 'AI'
        elif s.nor_source == 'AI':
            lite = {k for k, _l, _d in normal.available_sources()} - {'AI'}
            s.nor_source = s.nor_lite_source if s.nor_lite_source in lite else 'HIGHPASS'
        return {'FINISHED'}


class MULTICAMPROJECT_OT_BakeSetupAI(bpy.types.Operator):
    """Make the AI normal source available: install onnxruntime (download from PyPI, ~15 MB,
    into Blender's user modules folder) and copy an .onnx color-to-normal model (e.g.
    DeepBump's deepbump256.onnx) into Blender's user data folder"""
    bl_idname = "multicamproject.bake_setup_ai"
    bl_label = "Set up AI"
    bl_options = {'REGISTER'}

    @classmethod
    def description(cls, context, props):
        need = normal.ai.missing()
        return (f"AI needs {need}.\n" if need else "") + cls.__doc__

    filepath: bpy.props.StringProperty(name="Model", subtype='FILE_PATH',
                                       description="The .onnx model file (skip when already set up)")
    filter_glob: bpy.props.StringProperty(default="*.onnx", options={'HIDDEN'})

    def invoke(self, context, event):
        if not normal.ai.has_runtime() or not os.path.isfile(normal.ai.model_path()):
            if not os.path.isfile(normal.ai.model_path()):
                context.window_manager.fileselect_add(self)     # pick the model first
                return {'RUNNING_MODAL'}
        return context.window_manager.invoke_confirm(
            self, event, title="Install onnxruntime?",
            message="Downloads onnxruntime (~15 MB) from PyPI into Blender's user modules folder. "
                    "Blender waits until it is done.", confirm_text="Install")

    def execute(self, context):
        ai = normal.ai
        if self.filepath:
            if not self.filepath.lower().endswith(".onnx") or not os.path.isfile(self.filepath):
                self.report({'ERROR'}, "Pick an .onnx model file")
                return {'CANCELLED'}
            self.report({'INFO'}, f"Model copied to {ai.install_model(self.filepath)}")
        if not ai.has_runtime():
            context.window_manager.progress_begin(0, 1)
            try:
                ok, msg = ai.install_runtime()
            finally:
                context.window_manager.progress_end()
            self.report({'INFO'} if ok else {'ERROR'}, msg)
            if not ok:
                return {'CANCELLED'}
        need = ai.missing()
        if need:
            self.report({'WARNING'}, f"Still missing: {need}")
            return {'CANCELLED'}
        common.settings(context.scene).nor_source = 'AI'
        self.report({'INFO'}, "AI normal maps ready")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_Bake(bpy.types.Operator):
    """Bake the selected meshes with the options above: Albedo, Normal or Both, the normal
    map with the chosen method, each at its own texture size (A = the scene's resolution, set in
    front of Export). Each object ends in Final"""
    bl_idname = "multicamproject.bake"
    bl_label = "Bake"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_or_edit(context):
            return False
        hand, objs = _split_handmade(context)
        if not objs and not hand:
            cls.poll_message_set("Select meshes (a handmade one: 0E Edit UV first)")
            return False
        for o in hand:
            why = handmade.rebake_problem(o)
            if why:
                cls.poll_message_set(f"{o.name}: {why}")
                return False
        for o in objs:
            why = route.problem(o)
            if why:
                cls.poll_message_set(f"{o.name}: {why}")
                return False
        s = common.settings(context.scene)
        if s.bake_what in {'ALBEDO', 'BOTH'}:
            missing =[o.name for o in objs if not common.has_uv_normal(o)]
            if missing:
                cls.poll_message_set(f"No '{common.UV_NORMAL}' UV map on {', '.join(missing[:3])}")
                return False
        if s.bake_what in {'NORMAL', 'BOTH'}:
            for o in objs:
                why = normal.problem(o, context.scene, s.nor_source)
                if why and not (s.bake_what == 'BOTH' and why == "Bake the albedo first"):
                    cls.poll_message_set(f"{o.name}: {why}")
                    return False
        return True

    def execute(self, context):
        _to_object_mode(context)
        s = common.settings(context.scene)
        hand, objs = _split_handmade(context)
        albedo = s.bake_what in {'ALBEDO', 'BOTH'}
        src = s.nor_source if s.bake_what in {'NORMAL', 'BOTH'} else None
        if not hand:
            return _start_bake(self, context, "Bake", objs, albedo, src, "Baked")
        # handmade: ALB_ + NOR_ carried over from uv_old (always both: one layout)
        log = []

        def steps():
            done, failed = yield from _rebake_steps(context, hand)
            if objs:
                d2, f2 = yield from bake_objects_steps(context, objs, albedo, src,
                                                       log=lambda t: log.append(t))
                done, failed = done + d2, failed + f2
            return done, failed
        finish = _finisher("Baked / rebaked", len(hand) + len(objs))
        return jobs.start(self, context, "Bake", [o.name for o in hand + objs], steps(),
                          lambda r, e: [('INFO', t) for t in log] + finish(r, e))


class MULTICAMPROJECT_OT_BakeResolution(bpy.types.Operator):
    """The resolution of every baked texture (ALB_, NOR_ and the work textures BAo_/BNo_/BAp_/BNp_) of the objects set to A"""
    bl_idname = "multicamproject.bake_resolution"
    bl_label = "Bake Resolution"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    size: bpy.props.IntProperty(default=1024)

    @classmethod
    def description(cls, context, props):
        return (f"Bake ALB_, NOR_ and the work textures at {props.size} x {props.size} px (the whole "
                "scene). Textures baked at another size count as outdated")

    def execute(self, context):
        common.settings(context.scene).resolution = self.size
        return {'FINISHED'}


def _handmade_selected(context, need_old=False):
    objs = [o for o in common.selected_meshes(context) if handmade.is_handmade(o)]
    obj = context.active_object
    if obj is not None and obj not in objs and obj.type == 'MESH' and handmade.is_handmade(obj):
        objs.append(obj)
    return [o for o in objs if handmade.has_uv_old(o)] if need_old else objs


class MULTICAMPROJECT_OT_HandmadeEditUV(bpy.types.Operator):
    """Handmade: keep the UV layout the textures were painted on as uv_old (and the
    textures in <bake folder>/_previous), then edit uv_normal in Edit Mode. Rebake carries
    the textures over"""
    bl_idname = "multicamproject.handmade_edit_uv"
    bl_label = "Edit UV"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            cls.poll_message_set("Object Mode only")
            return False
        if not _handmade_selected(context):
            cls.poll_message_set("Select a handmade mesh")
            return False
        return True

    def execute(self, context):
        objs = _handmade_selected(context)
        for o in objs:
            try:
                made = handmade.start_uv_edit(o, context.scene)
            except RuntimeError as e:
                self.report({'ERROR'}, f"{o.name}: {e}")
                return {'CANCELLED'}
            if not made:
                self.report({'INFO'}, f"{o.name}: uv_old kept (the layout the textures are on)")
        for o in context.selected_objects:
            o.select_set(o in objs)
        if context.view_layer.objects.active not in objs:
            context.view_layer.objects.active = objs[0]
        bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, "Edit uv_normal, then Rebake (Object Mode)")
        return {'FINISHED'}


def _rebake_steps(context, objs):
    done, failed = [], []
    for obj in objs:
        if jobs.stop_requested():
            jobs.skip_rest()
            break
        jobs.item_start(obj.name)
        try:
            yield from handmade.rebake_steps(context, obj)
            done.append(obj)
            jobs.item_end(obj.name)
        except Exception as e:
            failed.append((obj.name, str(e)))
            jobs.item_end(obj.name, str(e))
    return done, failed


class MULTICAMPROJECT_OT_HandmadeRebake(bpy.types.Operator):
    """Handmade: carry ALB_ and NOR_ over from uv_old onto the new uv_normal, at the
    object's texture size, into the same files. It reads the copies Edit UV put into
    <bake folder>/_previous, so Rebake can run again after more UV changes"""
    bl_idname = "multicamproject.handmade_rebake"
    bl_label = "Rebake from uv_old"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_or_edit(context):
            return False
        objs = _handmade_selected(context, need_old=True)
        if not objs:
            cls.poll_message_set("Select a handmade mesh with uv_old (Edit UV first)")
            return False
        for o in objs:
            why = handmade.rebake_problem(o)
            if why:
                cls.poll_message_set(f"{o.name}: {why}")
                return False
        return True

    def execute(self, context):
        _to_object_mode(context)        # writes the UV edit into the mesh
        objs = _handmade_selected(context, need_old=True)
        for o in objs:
            why = handmade.rebake_problem(o)
            if why:
                self.report({'ERROR'}, f"{o.name}: {why}")
                return {'CANCELLED'}
        return jobs.start(self, context, "Rebake", [o.name for o in objs],
                          _rebake_steps(context, objs), _finisher("Rebaked", len(objs)))


class MULTICAMPROJECT_OT_HandmadeFinishUV(bpy.types.Operator):
    """Handmade: done with the new UVs - uv_old goes; the old textures stay in _previous.
    The next Edit UV starts from the textures as they are now"""
    bl_idname = "multicamproject.handmade_finish_uv"
    bl_label = "Finish"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_or_edit(context):
            return False
        objs = _handmade_selected(context, need_old=True)
        if not objs:
            return False
        for o in objs:
            why = handmade.finish_problem(o)
            if why:
                cls.poll_message_set(f"{o.name}: {why}")
                return False
        return True

    def execute(self, context):
        _to_object_mode(context)
        objs = _handmade_selected(context, need_old=True)
        for o in objs:
            why = handmade.finish_problem(o)       # a UV change made in Edit Mode just now
            if why:
                self.report({'ERROR'}, f"{o.name}: {why}")
                return {'CANCELLED'}
        for o in objs:
            handmade.finish_uv_edit(o, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_HandmadeCancelUV(bpy.types.Operator):
    """Handmade: give up the UV edit - uv_normal goes back to uv_old and, if a Rebake
    already wrote ALB_ / NOR_, the copies in _previous are put back into those files"""
    bl_idname = "multicamproject.handmade_cancel_uv"
    bl_label = "Cancel UV Edit"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _object_or_edit(context) and bool(_handmade_selected(context, need_old=True))

    def invoke(self, context, event):
        objs = _handmade_selected(context, need_old=True)
        files = any(common.data(o).rebaked for o in objs)
        return context.window_manager.invoke_confirm(
            self, event, title="Cancel the UV edit?",
            message="uv_normal goes back to uv_old"
                    + (" and ALB_ / NOR_ get their _previous files back (Ctrl+Z does not "
                       "bring the files back)." if files else "."),
            confirm_text="Cancel UV Edit", icon='WARNING')

    def execute(self, context):
        _to_object_mode(context)
        for o in _handmade_selected(context, need_old=True):
            for n in handmade.cancel_uv_edit(o, context.scene):
                self.report({'INFO'}, f"{o.name}: {n}")
        return {'FINISHED'}


last_refresh = {}       # object name -> what the last Refresh fixed (shown in the panel)


class MULTICAMPROJECT_OT_MaterialRefresh(bpy.types.Operator):
    """Check and fix the materials: every object its own MCP_ (projection) and MAT_ (final),
    named after it, in slots 1 and 2, wired into GN-CameraProject and GN-Final. A copy
    (Shift+D) gets its own; materials of deleted objects are removed. The Bake Source's
    scan materials get image -> Principled BSDF -> Material Output (unlit scans bake black)"""
    bl_idname = "multicamproject.material_refresh"
    bl_label = "Refresh Materials"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(items=SCOPES, default='EXPORT')

    @classmethod
    def poll(cls, context):
        return _object_or_edit(context)

    def execute(self, context):
        # Edit Mode: out for the refresh (the slots / GN change), back in afterwards
        edit = context.mode == 'EDIT_MESH'
        _to_object_mode(context)
        try:
            return self._refresh(context)
        finally:
            if edit and context.active_object is not None:
                bpy.ops.object.mode_set(mode='EDIT')

    def _refresh(self, context):
        from . import cache, matsync
        scene = context.scene
        objs = [o for o in scope_objects(context, self.scope, bake=False) if matsync.in_scope(o)]
        if self.scope == 'SELECTED' and context.active_object is not None \
                and context.active_object not in objs and matsync.in_scope(context.active_object):
            objs.append(context.active_object)
        last_refresh.clear()
        for o in matsync._duplicates(objs):     # copies first: the owners keep theirs
            last_refresh.setdefault(o.name, []).extend(matsync.sync(o, scene))
        for t in matsync.sync_names():
            self.report({'INFO'}, f"Renamed {t}")
        for o in objs:
            lines = last_refresh.setdefault(o.name, [])
            lines.extend(matsync.sync(o, scene, full=True))
            left = matsync.problems(o)
            lines.extend(f"still: {t}" for _k, t in left)
            if not lines:
                lines.append("materials OK")
        for n in matsync.remove_orphans(everything=True):
            self.report({'INFO'}, f"Removed {n} (its object is gone)")
        cache.clear()
        self.report({'INFO'}, f"Materials refreshed on {len(objs)} object(s)")
        return {'FINISHED'}


_classes = (MULTICAMPROJECT_OT_Bake, MULTICAMPROJECT_OT_BakeFromSource, MULTICAMPROJECT_OT_FitCage,
            MULTICAMPROJECT_OT_BakeSetFinal, MULTICAMPROJECT_OT_BakeAlbedo,
            MULTICAMPROJECT_OT_BakeNormal, MULTICAMPROJECT_OT_BakeNormalMode,
            MULTICAMPROJECT_OT_BakeSetupAI, MULTICAMPROJECT_OT_BakeResolution,
            MULTICAMPROJECT_OT_MaterialRefresh, MULTICAMPROJECT_OT_HandmadeEditUV,
            MULTICAMPROJECT_OT_HandmadeRebake, MULTICAMPROJECT_OT_HandmadeFinishUV,
            MULTICAMPROJECT_OT_HandmadeCancelUV)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
