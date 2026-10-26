import json
import time

import bmesh
import bpy

from . import bake, files, maps, shader
from . import geometry as geo

MASK_RES = 4096         # SF_mask_ at most (the blend width is a few texels)
LEVEL_RES = 2048        # SF_level_ at most (its offsets are smooth)


def settings(context):
    return context.scene.multicamproject_seamfix


def _why_not(obj):
    """'' or why Seam Fix cannot work on obj."""
    if obj is None or obj.type != 'MESH':
        return "Select a mesh"
    me = obj.data
    if me.uv_layers.get(geo.UV_SRC) is None:
        return f"No {geo.UV_SRC} on {obj.name}"
    if me.users > 1:
        return f"{me.name} is shared by {me.users} objects - make it single user"
    img = shader.albedo(obj)
    if img is None:
        return f"No albedo image on {geo.UV_SRC} (an image into Base Color)"
    for mat in shader.materials(obj):
        for n, _kind in shader.image_nodes(mat):
            others = [o.name for o in bpy.data.objects if o is not obj and o.type == 'MESH'
                      and any(s.material and s.material.node_tree and any(
                          m.type == 'TEX_IMAGE' and m.image == n.image
                          for m in s.material.node_tree.nodes) for s in o.material_slots)]
            if others:
                return f"{n.image.name} is also used by {others[0]} - Apply would change it too"
    return ""


def _tex_res(img):
    return max(img.size) or 1024


def _report(obj, key, data):
    rep = json.loads(obj.get("seamfix_report", "{}"))
    rep[key] = data
    obj["seamfix_report"] = json.dumps(rep)


def _prepare(context, obj):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    me = obj.data
    src = me.uv_layers[geo.UV_SRC]
    src.active_render = True            # image nodes without a UV Map node read uv_normal
    for name in (geo.UV_NEW, geo.UV_FIX):
        if me.uv_layers.get(name) is None:
            geo.ensure_uv(me, name)
    me.uv_layers.active = src
    if obj.get("seamfix_state") in (None, "applied"):
        obj["seamfix_state"] = "prepared"
    if obj.get("seamfix_state") == "baked":
        obj["seamfix_stale"] = 1


def _select_faces(context, obj, faces):
    """Edit Mode with `faces` selected (3D view and UV editor)."""
    me = obj.data
    for p in me.polygons:
        p.select = p.index in faces
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(me)
    uv = bm.loops.layers.uv.get(geo.UV_SRC)
    for f in bm.faces:
        f.select = f.index in faces
        if uv is not None:
            for l in f.loops:
                try:
                    l[uv].select = f.select
                    l[uv].select_edge = f.select
                except AttributeError:
                    pass
    bm.select_flush_mode()
    bmesh.update_edit_mesh(me)


class _SeamFixOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        why = _why_not(context.active_object)
        if why:
            cls.poll_message_set(why)
        return not why


class MULTICAMPROJECT_OT_seamfix_fix(_SeamFixOp, bpy.types.Operator):
    """Shrink the UV islands into uv_new (room for each seam's continuation), unfold the
    seams' target sides into uv_fixseam and paint the blend mask. Overlapping islands are
    left out and selected at the end. Always starts again from uv_normal"""
    bl_idname = "multicamproject.seamfix_fix"
    bl_label = "Fix Seam"

    def execute(self, context):
        t0 = time.time()
        obj = context.active_object
        st = settings(context)
        _prepare(context, obj)
        me = obj.data
        img = shader.albedo(obj)
        tex = _tex_res(img)
        seams = geo.Seams(me)
        geo.write_overlap_attr(me, seams.overlap_faces)
        shrink = geo.shrink_new(me, seams, st.blend_px + 4, tex) if st.shrink else {}
        if not st.shrink:
            geo.ensure_uv(me, geo.UV_NEW)
        strips = geo.build_fixseam(me, seams)
        mask = maps.build_mask(obj, seams, strips, img, st.blend_px, tex, min(tex, MASK_RES))
        shader.install(obj)
        shader.set_mode(obj, 2)
        obj["seamfix_fixed"] = 1
        data = seams.stats() | shrink | mask | {"seconds": round(time.time() - t0, 1)}
        _report(obj, "fix", data)
        msg = (f"Fix Seam: {data['seam_edges_used']} seam edges, "
               f"{data.get('shrunk', 0)} islands shrunk, "
               f"{data.get('reads_suppressed', 0):.0%} of reads faded out")
        if seams.overlap_faces:
            _select_faces(context, obj, seams.overlap_faces)
            self.report({'WARNING'}, msg + f" - {len(seams.left_out)} overlapping islands "
                        "left out (selected)")
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_seamfix_level(_SeamFixOp, bpy.types.Operator):
    """Even out the colour across every seam: both sides meet at their average, the
    correction fading into each island (no margin needed, uv_normal untouched)"""
    bl_idname = "multicamproject.seamfix_level"
    bl_label = "Seam-leveling"

    def execute(self, context):
        t0 = time.time()
        obj = context.active_object
        st = settings(context)
        _prepare(context, obj)
        img = shader.albedo(obj)
        tex = _tex_res(img)
        seams = geo.Seams(obj.data)
        geo.write_overlap_attr(obj.data, seams.overlap_faces)
        maps.ensure_neutral(obj)
        data = seams.stats() | maps.build_level(obj, seams, img, st.level_px, tex,
                                                min(tex, LEVEL_RES), st.max_jump / 255)
        shader.install(obj)
        shader.set_mode(obj, 2)
        obj["seamfix_leveled"] = 1
        data["seconds"] = round(time.time() - t0, 1)
        _report(obj, "level", data)
        self.report({'INFO'}, f"Seam-leveling: {data['seam_sides']} seam edges, median jump "
                              f"{data['jump_median_255']:.0f}/255 evened out")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_seamfix_bake(_SeamFixOp, bpy.types.Operator):
    """Bake every image on uv_normal, as Preview shows it, onto uv_new into
    <image>_seamfix (packed). Nothing is renamed or overwritten until Apply"""
    bl_idname = "multicamproject.seamfix_bake"
    bl_label = "Bake"

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        if not shader.installed(context.active_object):
            cls.poll_message_set("Run Fix Seam or Seam-leveling first")
            return False
        return True

    def execute(self, context):
        t0 = time.time()
        obj = context.active_object
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            data = bake.bake(obj, settings(context).bake_margin)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        obj["seamfix_state"] = "baked"
        obj["seamfix_stale"] = 0
        data["seconds"] = round(time.time() - t0, 1)
        _report(obj, "bake", data)
        self.report({'INFO'}, f"Baked {len(data['images'])} image(s) in {data['seconds']}s")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_seamfix_mode(bpy.types.Operator):
    """Show the object as Original / Mask / Preview / Baked"""
    bl_idname = "multicamproject.seamfix_mode"
    bl_label = "Seam Fix View"
    bl_options = {'INTERNAL'}

    mode: bpy.props.IntProperty(min=0, max=3)

    @classmethod
    def description(cls, context, props):
        return ("The original image on uv_normal",
                "Blend weight (white), leveling (blue), overlaps left out (red)",
                "The fix, live - no bake needed",
                "The _seamfix bake on uv_new")[props.mode]

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and shader.installed(obj)

    def execute(self, context):
        obj = context.active_object
        if self.mode == 3 and obj.get("seamfix_state") != "baked":
            self.report({'WARNING'}, "Nothing baked yet")
            return {'CANCELLED'}
        shader.set_mode(obj, self.mode)
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_seamfix_select_overlaps(bpy.types.Operator):
    """Select the faces of the overlapping islands Seam Fix left out (Edit Mode)"""
    bl_idname = "multicamproject.seamfix_select_overlaps"
    bl_label = "Select Overlaps"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' \
            and obj.data.attributes.get(geo.OVERLAP) is not None

    def execute(self, context):
        obj = context.active_object
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        attr = obj.data.attributes[geo.OVERLAP]
        faces = {i for i, d in enumerate(attr.data) if d.value > 0.5}
        if not faces:
            self.report({'INFO'}, "No overlapping islands")
            return {'CANCELLED'}
        _select_faces(context, obj, faces)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_seamfix_reset(bpy.types.Operator):
    """Undo everything Seam Fix made on this object: uv_new, uv_fixseam, the masks, the
    bakes and the node groups go; uv_normal and the original images stay as they were"""
    bl_idname = "multicamproject.seamfix_reset"
    bl_label = "Reset"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and (
            shader.installed(obj) or obj.data.uv_layers.get(geo.UV_NEW) is not None
            or obj.data.uv_layers.get(geo.UV_FIX) is not None)

    def execute(self, context):
        obj = context.active_object
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        files.reset(obj)
        self.report({'INFO'}, f"{obj.name}: back to {geo.UV_SRC} and the original images")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_seamfix_apply(bpy.types.Operator):
    """DESTRUCTIVE: overwrite the original image files with the bakes (backed up to
    _seamfix_backup first), make uv_new the only UV map, named uv_normal, and give the
    materials back their plain image nodes"""
    bl_idname = "multicamproject.seamfix_apply"
    bl_label = "Apply to Files (Destructive)"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or obj.get("seamfix_state") != "baked":
            cls.poll_message_set("Bake first")
            return False
        if obj.get("seamfix_stale"):
            cls.poll_message_set("Fix Seam / Seam-leveling ran after the bake - bake again")
            return False
        return True

    def invoke(self, context, event):
        try:
            self._jobs = files.files_to_write(context.active_object)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=520,
                                                          confirm_text="Overwrite files")

    def draw(self, context):
        col = self.layout.column(align=True)
        col.alert = True
        col.label(text="These files are overwritten (copies go to _seamfix_backup):",
                  icon='ERROR')
        for _img, _t, _a, path in getattr(self, "_jobs", []):
            col.label(text=path)
        col.label(text="Every UV map but uv_new (renamed uv_normal) is deleted.")

    def execute(self, context):
        obj = context.active_object
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            data = files.apply(obj)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Applied: {len(data['files'])} file(s) overwritten, "
                              f"backups in _seamfix_backup")
        return {'FINISHED'}


classes = (MULTICAMPROJECT_OT_seamfix_fix, MULTICAMPROJECT_OT_seamfix_level,
           MULTICAMPROJECT_OT_seamfix_bake, MULTICAMPROJECT_OT_seamfix_mode,
           MULTICAMPROJECT_OT_seamfix_select_overlaps, MULTICAMPROJECT_OT_seamfix_reset,
           MULTICAMPROJECT_OT_seamfix_apply)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
