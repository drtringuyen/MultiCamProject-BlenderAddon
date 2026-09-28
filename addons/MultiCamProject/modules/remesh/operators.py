import bpy
import gpu
from bpy.props import EnumProperty, IntProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import core, cutter

_CLOSE_PX = 12          # a click this close to the first point closes the polyline
_SAMPLES = 12           # interior rays per axis for the automatic Near/Far


def _mesh_poll(context):
    obj = context.active_object
    return obj is not None and obj.type == 'MESH'


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


class MULTICAMPROJECT_OT_RemeshPolylineCut(bpy.types.Operator):
    """Draw a polyline region: its border is cut exactly along the lines (not along the scan's
    triangles). Click to add points, click the first point or Enter to close, Backspace
    removes the last point, Esc / right click cancels"""
    bl_idname = "multicamproject.remesh_polyline_cut"
    bl_label = "Polyline Cut"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context) and context.area is not None and context.area.type == 'VIEW_3D'

    # ------------------------------------------------------------ drawing
    def _draw(self, context):
        if not self.points:
            return
        pts = self.points + [self.mouse]
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 2.0)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 1.0))
        batch_for_shader(shader, 'LINE_STRIP', {"pos": pts}).draw(shader)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 0.35))
        batch_for_shader(shader, 'LINES', {"pos": [self.mouse, self.points[0]]}).draw(shader)
        pshader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.point_size_set(7.0)
        pshader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch_for_shader(pshader, 'POINTS', {"pos": self.points}).draw(pshader)
        gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')

    def _finish(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.area.header_text_set(None)
        context.window.cursor_modal_restore()
        context.area.tag_redraw()

    # ------------------------------------------------------------ modal
    def _pos(self, event):
        return (event.mouse_x - self.win.x, event.mouse_y - self.win.y)

    def invoke(self, context, event):
        # started from the sidebar too: work in the viewport's own region
        self.win = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        self.rv3d = context.area.spaces.active.region_3d
        if self.win is None:
            return {'CANCELLED'}
        self.points = []
        self.mouse = self._pos(event)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set('CROSSHAIR')
        context.area.header_text_set(
            "Polyline Cut: click to add points - click the first point / Enter to close - "
            "Backspace: remove last - Esc / right click: cancel")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE':
            self.mouse = self._pos(event)
            return {'RUNNING_MODAL'}
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and not self.points:
            return {'PASS_THROUGH'}         # frame the view before the first click
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'CANCELLED'}
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self.points:
                self.points.pop()
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._close(context)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            p = self._pos(event)
            if len(self.points) >= 3 and (Vector(p) - Vector(self.points[0])).length < _CLOSE_PX:
                return self._close(context)
            self.points.append(p)
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    def _close(self, context):
        self._finish(context)
        if len(self.points) < 3:
            self.report({'WARNING'}, "A cut needs at least 3 points")
            return {'CANCELLED'}
        region, rv3d = self.win, self.rv3d
        obj = context.active_object

        def ray(p):
            return (view3d_utils.region_2d_to_origin_3d(region, rv3d, p),
                    view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized())

        rays = [ray(p) for p in self.points]
        forward = (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        samples = []
        for i in range(_SAMPLES):
            for j in range(_SAMPLES):
                q = (min(xs) + (max(xs) - min(xs)) * (i + 0.5) / _SAMPLES,
                     min(ys) + (max(ys) - min(ys)) * (j + 0.5) / _SAMPLES)
                if _inside(q, self.points):
                    samples.append(ray(q))
        depth = cutter.auto_depth(obj, context.evaluated_depsgraph_get(), rays, forward, samples)
        if depth is None:
            self.report({'WARNING'}, "The polyline does not cover the object")
            return {'CANCELLED'}
        r = core.add_cut(obj, rays, forward, *depth)
        core.count_faces(obj, context.evaluated_depsgraph_get())
        self.report({'INFO'}, f"{r.name}: {r.face_count} faces")
        return {'FINISHED'}


def _face_set_items(self, context):
    obj = context.active_object
    items = [(str(i), f"Face Set {i}", f"{c} faces") for i, c in core.face_set_ids(obj)] if obj else []
    _face_set_items.cache = items       # Blender needs the list kept alive
    return items or [('NONE', "No face sets", "")]


class MULTICAMPROJECT_OT_RemeshFromFaceSet(bpy.types.Operator):
    """Make a region from a Sculpt face set. Its border follows the scan's triangles (no cut)"""
    bl_idname = "multicamproject.remesh_from_face_set"
    bl_label = "Region from Face Set"
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = "face_set"

    face_set: EnumProperty(name="Face Set", items=_face_set_items)

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.face_set == 'NONE':
            return {'CANCELLED'}
        obj = context.active_object
        mode = obj.mode
        if mode == 'SCULPT':            # sculpt mode keeps its own copy of the face sets
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            core.add_face_set(obj, int(self.face_set))
        finally:
            if mode == 'SCULPT':
                bpy.ops.object.mode_set(mode='SCULPT')
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshRemoveRegion(bpy.types.Operator):
    """Remove the region (the scan gets its faces back)"""
    bl_idname = "multicamproject.remesh_remove_region"
    bl_label = "Remove Region"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context) and len(core.data(context.active_object).regions) > 0

    def execute(self, context):
        obj = context.active_object
        d = core.data(obj)
        i = self.index if self.index >= 0 else d.active
        if not 0 <= i < len(d.regions):
            return {'CANCELLED'}
        core.remove_region(obj, i)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshApply(bpy.types.Operator):
    """Make the regions real: cut, delete and decimate each region to its exact ratio, mark
    seams on the borders and one Sculpt face set per region. The mesh before is kept for
    Restore"""
    bl_idname = "multicamproject.remesh_apply"
    bl_label = "Apply Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context) and len(core.data(context.active_object).regions) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = context.active_object
        mode = obj.mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            warnings = core.apply(obj)
        finally:
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)
        for w in warnings:
            self.report({'WARNING'}, w)
        self.report({'INFO'}, f"Remesh applied: {len(obj.data.polygons)} faces")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshRestore(bpy.types.Operator):
    """Put back the mesh from before the last Apply"""
    bl_idname = "multicamproject.remesh_restore"
    bl_label = "Restore Original"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context) and core.data(context.active_object).backup is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = context.active_object
        mode = obj.mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            core.restore(obj)
        finally:
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_RemeshPolylineCut, MULTICAMPROJECT_OT_RemeshFromFaceSet,
            MULTICAMPROJECT_OT_RemeshRemoveRegion, MULTICAMPROJECT_OT_RemeshApply,
            MULTICAMPROJECT_OT_RemeshRestore)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
