import os

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import common, engine, gn_final, normal

SCOPES = (('SELECTED', "Selected", "The selected meshes"),
          ('EXPORT', "EXPORT", "Every mesh in the EXPORT collection"))


def scope_objects(context, scope):
    if scope == 'EXPORT':
        return common.export_objects(context.scene)
    return common.selected_meshes(context)


def _object_mode(context):
    return context.mode == 'OBJECT'


def bake_objects(context, objs, albedo=True, nor_source=None, preview=False):
    """Albedo and/or normal bake for `objs`, one object after the other, with a progress
    bar. Returns (done, [(object name, error)])."""
    wm = context.window_manager
    steps = len(objs) * (int(albedo) + int(nor_source is not None))
    wm.progress_begin(0, max(steps, 1))
    done, failed, step = [], [], 0
    try:
        for obj in objs:
            try:
                if albedo:
                    if not common.has_uv_normal(obj):
                        raise RuntimeError(f"no {common.UV_NORMAL}")
                    engine.bake_albedo(context, obj)
                    step += 1
                    wm.progress_update(step)
                if nor_source is not None:
                    why = normal.problem(obj, context.scene, nor_source)
                    if why:
                        raise RuntimeError(why)
                    normal.generate(context, obj, nor_source, preview)
                    step += 1
                    wm.progress_update(step)
                done.append(obj)
            except Exception as e:      # one object failing must not stop the others
                failed.append((obj.name, str(e)))
    finally:
        wm.progress_end()
    return done, failed


def _report(op, done, failed, what):
    for name, err in failed:
        op.report({'ERROR'}, f"{name}: {err}")
    if done:
        op.report({'INFO'}, f"{what}: {', '.join(o.name for o in done)}")


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
    """Bake the projection (with the scan) into ALB_<name> on uv_normal and build MAT_.
    Each object then switches to Final"""
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
        done, failed = bake_objects(context, objs, albedo=True, nor_source=src)
        _report(self, done, failed, "Baked")
        return {'FINISHED'} if done else {'CANCELLED'}


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
        done, failed = bake_objects(context, objs, albedo=False, nor_source=s.nor_source,
                                    preview=s.nor_preview_2k)
        _report(self, done, failed, "Normal map")
        return {'FINISHED'} if done else {'CANCELLED'}


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
            s.nor_source = 'AI'
        else:
            s.nor_source = 'HIGHPASS'
        return {'FINISHED'}


class MULTICAMPROJECT_OT_BakeSetupAI(bpy.types.Operator):
    """Make the AI normal source available: install onnxruntime (download from PyPI, ~15 MB,
    into Blender's user modules folder) and copy an .onnx color-to-normal model (e.g.
    DeepBump's deepbump256.onnx) into Blender's user data folder"""
    bl_idname = "multicamproject.bake_setup_ai"
    bl_label = "Set up AI"
    bl_options = {'REGISTER'}

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


_classes = (MULTICAMPROJECT_OT_BakeSetFinal, MULTICAMPROJECT_OT_BakeAlbedo,
            MULTICAMPROJECT_OT_BakeNormal, MULTICAMPROJECT_OT_BakeNormalMode,
            MULTICAMPROJECT_OT_BakeSetupAI)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
