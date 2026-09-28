import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..camera_project import core as cp
from . import core, cutter, gn_remesh as gn

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
    close it back to the first point, Esc to cancel"""
    bl_idname = "multicamproject.remesh_poly_cut"
    bl_label = "Poly Cut"
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
        mod = gn.get_modifier(obj)
        if mod is not None:             # GN-Remesh follows the newest face set
            cp.set_input(mod, "Face Set", new_id)
        self.report({'INFO'}, f"Face set {new_id}: {count} faces")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshAddModifier(bpy.types.Operator):
    """Add GN-Remesh at the top of the stack: pick a face set by number and see only it.
    The face sets are copied to face_set so GN can read them"""
    bl_idname = "multicamproject.remesh_add_modifier"
    bl_label = "Add GN-Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        mode = obj.mode
        if mode == 'SCULPT':            # Sculpt Mode keeps its own copy of the face sets
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            core.sync_face_sets(obj)
            mod = gn.add_modifier(obj)
            ids = [i for i, _c in core.face_set_ids(obj)]
            if ids:                     # start on the newest face set (the last Poly Cut)
                cp.set_input(mod, "Face Set", max(ids))
        finally:
            if mode == 'SCULPT':
                bpy.ops.object.mode_set(mode='SCULPT')
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_RemeshPolyCut, MULTICAMPROJECT_OT_RemeshAddModifier)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    core.register()


def unregister():
    core.unregister()
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
