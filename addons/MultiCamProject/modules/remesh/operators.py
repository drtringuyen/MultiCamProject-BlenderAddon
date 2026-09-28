import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import core, cutter, marks, tool, workflow as wf

_SAMPLES = 12           # interior rays per axis for the cut depth
_SAME_PX = 4            # a double click's first press lands on the last point: not a new one


def _inside(p, poly):
    x, y = p
    hit = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


class MULTICAMPROJECT_OT_RemeshPolyCut(bpy.types.Operator):
    """Draw a polygon: the mesh is cut exactly along its lines (not along the scan's triangles)
    and the inside becomes a new face set. Click to add points, Enter or double click to
    close it back to the first point, Esc to cancel. Then Set Faces opens for the new face set"""
    bl_idname = "multicamproject.remesh_poly_cut"
    bl_label = "PolyCut"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.area is not None
                and context.area.type == 'VIEW_3D')

    # ------------------------------------------------------------ drawing
    def _draw(self, context):
        if not self.points:
            return
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 2.0)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 1.0))
        batch_for_shader(shader, 'LINE_STRIP', {"pos": self.points + [self.mouse]}).draw(shader)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 0.35))
        batch_for_shader(shader, 'LINES', {"pos": [self.mouse, self.points[0]]}).draw(shader)
        pshader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.point_size_set(7.0)
        pshader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch_for_shader(pshader, 'POINTS', {"pos": self.points}).draw(pshader)
        gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')

    def _pos(self, event):
        return (event.mouse_x - self.win.x, event.mouse_y - self.win.y)

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.area.header_text_set(None)
        context.window.cursor_modal_restore()
        context.area.tag_redraw()

    # ------------------------------------------------------------ modal
    def invoke(self, context, event):
        # started from the sidebar: work in the viewport's own region
        self.win = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        self.rv3d = context.area.spaces.active.region_3d
        if self.win is None:
            return {'CANCELLED'}
        self.points = []
        self.mouse = self._pos(event)
        if event.type == 'LEFTMOUSE':   # Ctrl+click with the PolyCut tool: the first point
            self.points.append(self.mouse)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set('CROSSHAIR')
        context.area.header_text_set(
            "Poly Cut: click to add points - Enter / double click: close and cut - Esc: cancel")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE':
            self.mouse = self._pos(event)
            return {'RUNNING_MODAL'}
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}         # navigating is fine: the rays are taken at the end
        if event.type == 'ESC' and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._close(context)
        if event.type == 'LEFTMOUSE':
            p = self._pos(event)
            if event.value == 'DOUBLE_CLICK':
                if not self.points or (Vector(p) - Vector(self.points[-1])).length > _SAME_PX:
                    self.points.append(p)
                return self._close(context)
            if event.value == 'PRESS':
                self.points.append(p)
        return {'RUNNING_MODAL'}

    def _close(self, context):
        self._end(context)
        if len(self.points) < 3:
            self.report({'WARNING'}, "Poly Cut needs at least 3 points")
            return {'CANCELLED'}
        obj = context.active_object
        region, rv3d = self.win, self.rv3d
        forward = (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()

        def ray(p):
            return (view3d_utils.region_2d_to_origin_3d(region, rv3d, p),
                    view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized())

        rays = [ray(p) for p in self.points]
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        probe = [(o, d, forward) for o, d in rays]
        for i in range(_SAMPLES):
            for j in range(_SAMPLES):
                q = (min(xs) + (max(xs) - min(xs)) * (i + 0.5) / _SAMPLES,
                     min(ys) + (max(ys) - min(ys)) * (j + 0.5) / _SAMPLES)
                if _inside(q, self.points):
                    probe.append((*ray(q), forward))
        depth = cutter.auto_depth(obj, context.evaluated_depsgraph_get(), probe)
        if depth is None:
            self.report({'WARNING'}, "The polygon does not cover the object")
            return {'CANCELLED'}

        mode = obj.mode
        if mode != 'OBJECT':            # Sculpt Mode keeps its own copy of the mesh
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            new_id, count = core.poly_cut(obj, rays, forward, *depth)
        finally:
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)
        if not count:
            self.report({'WARNING'}, "Nothing inside the polygon was cut")
            return {'FINISHED'}
        marks.remember_face_set(obj, new_id)
        self.report({'INFO'}, f"Face set {new_id}: {count} faces")
        if obj.mode in {'SCULPT', 'EDIT'}:
            bpy.ops.multicamproject.remesh_set_faces('INVOKE_DEFAULT', face_set=new_id)
        return {'FINISHED'}


def _enter_tool(context, obj, mode='SCULPT'):
    """The object in Sculpt (or Edit) Mode with the PolyCut tool active."""
    if obj.mode != mode:
        bpy.ops.object.mode_set(mode=mode)
    try:
        bpy.ops.wm.tool_set_by_id(name=tool.tool_id(context.mode))
    except (RuntimeError, TypeError):
        pass        # no 3D view in this context: the mode is set, the tool is one click away


class MULTICAMPROJECT_OT_Remesh(bpy.types.Operator):
    """Make the remesh copy: the copy takes the name, EXPORT and the baked textures, gets
    GN-Remesh, two Decimate modifiers and the camera projection. The original becomes
    <name>_original in "Original Mesh" (no GN modifiers), the high poly for Bake from mesh.
    Then Sculpt Mode with the PolyCut tool"""
    bl_idname = "multicamproject.remesh"
    bl_label = "Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or obj.library:
            return False
        if wf.is_copy(obj):
            cls.poll_message_set("Already a Remesh copy")
            return False
        if obj.name.endswith(wf.ORIGINAL_SUFFIX) or wf.is_original(obj):
            cls.poll_message_set("This is a Remesh original")
            return False
        return True

    def execute(self, context):
        obj = context.active_object
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        copy, warnings = wf.make_copy(context, obj)
        for w in warnings:
            self.report({'WARNING'}, w)
        _enter_tool(context, copy)
        self.report({'INFO'}, f"'{copy.name}' is the Remesh copy, '{obj.name}' the high poly")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshEnterTool(bpy.types.Operator):
    """Sculpt Mode with the PolyCut tool (Ctrl+Click: PolyCut, L: Set Faces for the face set
    under the mouse)"""
    bl_idname = "multicamproject.remesh_enter_tool"
    bl_label = "PolyCut Tool"
    bl_options = {'REGISTER'}

    mode: EnumProperty(items=(('SCULPT', "Sculpt", ""), ('EDIT', "Edit", "")), default='SCULPT',
                       options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and not obj.library

    def execute(self, context):
        _enter_tool(context, context.active_object, self.mode)
        return {'FINISHED'}


def _mouse_ray(context, event):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or region.type != 'WINDOW':
        return None
    p = (event.mouse_region_x, event.mouse_region_y)
    return (view3d_utils.region_2d_to_origin_3d(region, rv3d, p),
            view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized())


class MULTICAMPROJECT_OT_RemeshPick(bpy.types.Operator):
    """The face set under the mouse -> Set Faces"""
    bl_idname = "multicamproject.remesh_pick"
    bl_label = "Pick Face Set"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and context.mode in {'SCULPT', 'EDIT_MESH'})

    def invoke(self, context, event):
        obj = context.active_object
        ray = _mouse_ray(context, event)
        if ray is None:
            return {'PASS_THROUGH'}
        if context.mode == 'EDIT_MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            index = marks.pick_face(obj, *ray, bm=bm)
            if index is None:
                return {'CANCELLED'}
            lay = bm.faces.layers.int.get(core.SCULPT_FACE_SET)
            fs = bm.faces[index][lay] if lay is not None else 1
        else:
            index = marks.pick_face(obj, *ray)
            if index is None:
                return {'CANCELLED'}
            fs = int(core._face_sets(obj.data)[index])
        marks.remember_face_set(obj, fs)
        bpy.ops.multicamproject.remesh_set_faces('INVOKE_DEFAULT', face_set=fs)
        return {'FINISHED'}


_state = {"action": 'HIGH_DENSITY', "mark_seam": True, "backup": None}


class MULTICAMPROJECT_OT_RemeshSetFaces(bpy.types.Operator):
    """Mark faces for the remesh: High Density (vg_HighRes), Delete Geo (remesh_delete),
    To Separate (remesh_detach) or Clear. Edit Mode: the selected faces. Sculpt Mode: the
    face set picked with L or made by the last PolyCut"""
    bl_idname = "multicamproject.remesh_set_faces"
    bl_label = "Set Faces"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(name="Set", items=marks.ACTIONS, default='HIGH_DENSITY')
    mark_seam: BoolProperty(name="Mark boundary as seam", default=True,
                            description="The region's outline becomes a UV seam")
    face_set: IntProperty(name="Face Set", default=0, min=0, options={'SKIP_SAVE'},
                          description="Face set to mark (0: Edit Mode selection, or the face "
                                      "set picked / cut last in Sculpt Mode)")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and not obj.library
                and context.mode in {'SCULPT', 'EDIT_MESH'})

    # ------------------------------------------------------------ the region
    def _sculpt_face_set(self, obj):
        return self.face_set or marks.last_face_set(obj)

    def _edit(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        return obj, bm

    # ------------------------------------------------------------ dialog
    def invoke(self, context, event):
        obj = context.active_object
        self.action, self.mark_seam = _state["action"], _state["mark_seam"]
        _state["backup"] = None
        if context.mode == 'EDIT_MESH':
            obj, bm = self._edit(context)
            if self.face_set:
                marks.edit_select_face_set(bm, self.face_set)
            faces = marks.edit_region(bm)
            if not faces:
                self.report({'WARNING'}, "Select faces first")
                return {'CANCELLED'}
            # Edit Mode previews on the real data (its undo records it); Esc puts it back
            _state["backup"] = (obj.name, marks.edit_backup(obj, bm, faces))
            marks.apply_edit_mode(obj, bm, faces, self.action, self.mark_seam)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            # a new layer frees the face references: take the selection again
            marks.overlay_show(obj, marks.edit_region(bm), self.action, bm=bm)
        else:
            fs = self._sculpt_face_set(obj)
            if not fs:
                self.report({'WARNING'}, "Pick a face set with L or make one with a PolyCut")
                return {'CANCELLED'}
            self.face_set = fs
            faces = marks.face_mask(obj, fs)
            if not faces.any():
                self.report({'WARNING'}, f"Face set {fs} has no faces")
                return {'CANCELLED'}
            marks.overlay_show(obj, faces, self.action)
        return context.window_manager.invoke_props_dialog(
            self, title=f"Set Faces ({'selection' if context.mode == 'EDIT_MESH' else f'face set {self.face_set}'})",
            confirm_text="Set")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "action", expand=True)
        col.prop(self, "mark_seam")

    def check(self, context):
        marks.overlay_action(self.action)
        if context.mode == 'EDIT_MESH' and _state["backup"] is not None:
            obj, bm = self._edit(context)
            marks.edit_restore(obj, bm, _state["backup"][1])
            marks.apply_edit_mode(obj, bm, marks.edit_region(bm), self.action, self.mark_seam)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        return True

    def cancel(self, context):
        marks.overlay_hide()
        backup, _state["backup"] = _state["backup"], None
        obj = context.active_object
        if backup is not None and context.mode == 'EDIT_MESH' and obj and obj.name == backup[0]:
            _obj, bm = self._edit(context)
            marks.edit_restore(obj, bm, backup[1])
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    def execute(self, context):
        marks.overlay_hide()
        _state["action"], _state["mark_seam"] = self.action, self.mark_seam
        obj = context.active_object
        label = dict((k, l) for k, l, *_r in marks.ACTIONS)[self.action]
        if context.mode == 'EDIT_MESH':
            obj, bm = self._edit(context)
            backup, _state["backup"] = _state["backup"], None
            if backup is not None and backup[0] == obj.name:
                marks.edit_restore(obj, bm, backup[1])
            elif self.face_set:             # redo / run without the dialog
                marks.edit_select_face_set(bm, self.face_set)
            n = marks.apply_edit_mode(obj, bm, marks.edit_region(bm), self.action,
                                      self.mark_seam)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        else:
            fs = self._sculpt_face_set(obj)
            if not fs:
                self.report({'WARNING'}, "Pick a face set with L or make one with a PolyCut")
                return {'CANCELLED'}
            # Sculpt undo does not record vertex groups or seams: write in Object Mode
            bpy.ops.object.mode_set(mode='OBJECT')
            try:
                n = marks.apply_object_mode(obj, marks.face_mask(obj, fs), self.action,
                                            self.mark_seam)
            finally:
                bpy.ops.object.mode_set(mode='SCULPT')
        if not n:
            self.report({'WARNING'}, "No faces to mark")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{label}: {n:,} faces")
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_RemeshPolyCut, MULTICAMPROJECT_OT_Remesh,
            MULTICAMPROJECT_OT_RemeshEnterTool, MULTICAMPROJECT_OT_RemeshPick,
            MULTICAMPROJECT_OT_RemeshSetFaces)


def _face_menu(self, context):
    self.layout.separator()
    self.layout.operator(MULTICAMPROJECT_OT_RemeshSetFaces.bl_idname, icon='MOD_REMESH')


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(_face_menu)
    bpy.types.VIEW3D_MT_edit_mesh_faces.append(_face_menu)
    core.register()


def unregister():
    core.unregister()
    bpy.types.VIEW3D_MT_edit_mesh_faces.remove(_face_menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(_face_menu)
    marks.unregister()
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
