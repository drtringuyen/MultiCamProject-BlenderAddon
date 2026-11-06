"""PolyCut (the Sculpt Mode tool, Ctrl+Shift+K): draw on the mesh, it is cut exactly along the line
and the new edges are offered as a seam. Nothing else.

    Ctrl+click   start; click: add a point (the points stay on the object while you navigate)
    close        click the first point (green under the mouse) or double click: a closed shape
    Enter        an open line
    Esc          drop it

Cut Through (the tool's header, 03): on - through the whole mesh (a closed shape with an Exact
boolean, MCP-PolyCut, nothing removed; an open line with Knife Project, Cut Through); off -
only the faces the view sees (Knife Project, both).

After the cut its outline shows in yellow (closed: the border of what is inside, one island;
open: the cut edges on the line - never an edge the cut added across a face) and a popup asks:
Set Seam or No Seam. The cut
stays either way (Ctrl+Z takes it back). Behaviour from Living Lacquer's Poly Cut; the cut
itself is MultiCamProject's. The writes happen in Object Mode (Sculpt's undo does not record
seams)."""
import time

import bmesh
import bpy
import gpu
import numpy as np
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import core, cutter, gn_remesh as gn, workflow as wf

THROUGH_PROP = "multicamproject_polycut_through"
CUT_EDGES = gn.CUT_EDGES            # edge attribute: the last cut's new edges, until the popup
POINT_PX = 12                       # a click this near an existing point is on it
DOUBLE_SECONDS, DOUBLE_PX = 0.45, 6
SAMPLES = 12                        # interior rays per axis: does the shape cover the object
COLOR = (1.0, 0.85, 0.2, 1.0)
CLOSE_COLOR = (0.3, 1.0, 0.4, 1.0)
NAVIGATE = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
            'MOUSEROTATE', 'MOUSESMARTZOOM'}
_preview = {"lines": None, "handle": None}


def inside(p, poly):
    """Even-odd test of the 2D point p in the polygon [(x, y)...]."""
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


# ---------------------------------------------------------------- the new edges

def cut_flags(obj):
    me = obj.data
    a = me.attributes.get(CUT_EDGES)
    flags = np.zeros(len(me.edges), bool)
    if a is not None and a.domain == 'EDGE':
        a.data.foreach_get("value", flags)
    return flags


def forget(obj):
    a = obj.data.attributes.get(CUT_EDGES)
    if a is not None:
        obj.data.attributes.remove(a)


def store_flags(obj, flags):
    me = obj.data
    forget(obj)
    me.attributes.new(CUT_EDGES, 'BOOLEAN', 'EDGE').data.foreach_set("value", flags)
    me.update()
    return int(flags.sum())


def set_seam(obj):
    """The last cut's edges become a seam. Returns the edge count."""
    me = obj.data
    flags = cut_flags(obj)
    seams = np.zeros(len(me.edges), bool)
    me.edges.foreach_get("use_seam", seams)
    me.edges.foreach_set("use_seam", seams | flags)
    me.update()
    return int(flags.sum())


def _draw_preview():
    if not _preview["lines"]:
        return
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    sh.uniform_float("lineWidth", 3.0)
    sh.uniform_float("color", COLOR)
    batch_for_shader(sh, 'LINES', {"pos": _preview["lines"]}).draw(sh)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def show_preview(obj):
    me = obj.data
    flags = cut_flags(obj)
    ev = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world, np.float32)
    co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    _preview["lines"] = [tuple(p) for p in co[ev.reshape(-1, 2)[flags].ravel()].tolist()]
    if _preview["handle"] is None:
        _preview["handle"] = bpy.types.SpaceView3D.draw_handler_add(
            _draw_preview, (), 'WINDOW', 'POST_VIEW')
    _redraw()


def hide_preview():
    _preview["lines"] = None
    if _preview["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_preview["handle"], 'WINDOW')
        _preview["handle"] = None
    _redraw()


def _redraw():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ---------------------------------------------------------------- only the outline as the seam

ON_LINE_PX = 1.0        # an edge is on the drawn line when both ends and its middle are this near


def _project(co, view):
    """World points (n, 3) -> region pixels (n, 2); NaN behind the eye."""
    full, width, height = view
    h = co @ full[:3, :3].T + full[:3, 3]
    w = co @ full[3, :3] + full[3, 3]
    out = np.full((len(co), 2), np.nan)
    ok = w > 1e-9
    out[ok, 0] = (h[ok, 0] / w[ok] + 1.0) * width / 2.0
    out[ok, 1] = (h[ok, 1] / w[ok] + 1.0) * height / 2.0
    return out


def _dist_to_line(q, segs):
    """Pixel distance of the points q (n, 2) to the polyline's segments [(a, b)...]."""
    best = np.full(len(q), np.inf)
    for a, b in segs:
        ab = b - a
        L = float(ab @ ab)
        t = np.zeros(len(q)) if L < 1e-12 else np.clip(((q - a) @ ab) / L, 0.0, 1.0)
        d = np.linalg.norm(a + t[:, None] * ab - q, axis=1)
        best = np.minimum(best, np.nan_to_num(d, nan=np.inf))
    return best


def _inside_polygon(q, poly):
    px, py = q[:, 0], q[:, 1]
    inside = np.zeros(len(q), bool)
    x0, y0 = poly[-1]
    for x1, y1 in poly:
        with np.errstate(invalid='ignore', divide='ignore'):
            cross = ((y1 > py) != (y0 > py)) & (px < (x0 - x1) * (py - y1) / ((y0 - y1) or 1e-12) + x1)
        inside ^= np.nan_to_num(cross, nan=0).astype(bool)
        x0, y0 = x1, y1
    return inside


def outline_only(obj, flags, points3d, closed, view):
    """The cut's new edges reduced to the drawn outline: no edge that the cut added across a face
    (a diagonal of a split face, a sliver between two cuts). Closed: the border of everything
    inside the shape (one loop, the inside is one island); open: the cut edges on the line."""
    me = obj.data
    mw = np.array(obj.matrix_world, np.float64)
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    ev = np.empty(len(me.edges) * 2, np.int64)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    line = _project(np.array([tuple(p) for p in points3d], np.float64), view)
    line = [p for p in line if not np.isnan(p).any()]
    if len(line) < 2:
        return flags
    segs = list(zip(line, line[1:] + (line[:1] if closed else [])))
    v2 = _project(co, view)
    near_v = _dist_to_line(v2, segs) <= ON_LINE_PX
    mid = _project((co[ev[:, 0]] + co[ev[:, 1]]) / 2.0, view)
    on = near_v[ev[:, 0]] & near_v[ev[:, 1]] & (_dist_to_line(mid, segs) <= ON_LINE_PX)
    if not closed:
        return flags & on
    # every face is now wholly in or out of the shape: its centre says which
    totals = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_total", totals)
    centres = np.empty(len(me.polygons) * 3, np.float64)
    me.polygons.foreach_get("center", centres)
    centres = centres.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    inside = _inside_polygon(_project(centres, view), line)
    loop_edge = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("edge_index", loop_edge)
    loop_face = np.repeat(np.arange(len(me.polygons)), totals)
    n = len(me.edges)
    tot = np.bincount(loop_edge, minlength=n)
    ins = np.bincount(loop_edge[inside[loop_face]], minlength=n)
    border = (ins > 0) & (ins < tot)
    return border & on


def view_of(region, rv3d):
    return (np.array(rv3d.perspective_matrix, np.float64), region.width, region.height)


# ---------------------------------------------------------------- the cuts (Object Mode)

def through_depth(obj, rays, perspective):
    """Near / Far along the view that hold the whole object: the cut goes through it."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    ds = [(c - o).dot(fwd) for o, _d, fwd in rays for c in corners]
    lo, hi = min(ds), max(ds)
    pad = max((hi - lo) * 0.05, max(obj.dimensions) * 0.01, 1e-3)
    near = lo - pad
    if perspective:
        near = max(near, 1e-4)      # never behind the eye: the rays meet there
    return near, hi + pad


def cut_closed(obj, rays, forward, near, far):
    """Exact boolean along the closed shape. Returns the new edges' count."""
    return core.poly_cut(obj, rays, forward, near, far)


def through_on(context):
    return getattr(context.scene, THROUGH_PROP, True)


def cut_knife(context, obj, points3d, area, region, closed, through):
    """Knife Project along the line (closed: back to the first point), through the mesh or
    only the faces the view sees. Runs from Object Mode, ends there. Returns the new edges'
    count."""
    n = len(points3d)
    edges = [(i, i + 1) for i in range(n - 1)] + ([(n - 1, 0)] if closed else [])
    me = bpy.data.meshes.new("_MCP_PolyCutLine")
    me.from_pydata([tuple(p) for p in points3d], edges, [])
    line = bpy.data.objects.new(me.name, me)
    context.scene.collection.objects.link(line)
    space = area.spaces.active
    vl = context.view_layer
    selected = [o for o in context.selected_objects if o != obj]
    ts = context.tool_settings
    select_mode = tuple(ts.mesh_select_mode)
    try:
        if getattr(space, "local_view", None) is not None:
            line.local_view_set(space, True)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        vl.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')        # obj alone: the line is not a cutter in Edit Mode
        bpy.ops.mesh.select_all(action='DESELECT')
        ts.mesh_select_mode = (False, True, False)
        line.select_set(True)
        old_verts = len(bmesh.from_edit_mesh(obj.data).verts)
        with context.temp_override(window=context.window, area=area, region=region,
                                   space_data=space):
            bpy.ops.mesh.knife_project(cut_through=through)
        bm = bmesh.from_edit_mesh(obj.data)
        flags = _knife_edges(bm, old_verts, obj, points3d, closed, region, space.region_3d)
        bpy.ops.mesh.select_all(action='DESELECT')
        ts.mesh_select_mode = select_mode
        bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(line)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        for o in selected:
            if o.name in vl.objects:
                o.select_set(True)
        obj.select_set(True)
        vl.objects.active = obj
    return store_flags(obj, flags)


def _knife_edges(bm, old_verts, obj, points3d, closed, region, rv3d, px=0.75):
    """The cut's new edges (Knife Project's own selection does not say: it selects the faces
    inside a closed line and nothing for an open one). Knife Project adds its vertices at the
    end: an edge between two of them is on the cut; an edge from one of them to an old vertex
    is on it when that vertex lies on the drawn line in the view (the cut ran through it)."""
    bm.verts.index_update()
    bm.edges.index_update()
    line = [view3d_utils.location_3d_to_region_2d(region, rv3d, p) for p in points3d]
    line = [p for p in line if p is not None]
    segs = list(zip(line, line[1:] + (line[:1] if closed else [])))
    mw = obj.matrix_world

    def on_line(v):
        q = view3d_utils.location_3d_to_region_2d(region, rv3d, mw @ v.co)
        if q is None:
            return False
        for a, b in segs:
            ab = b - a
            t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (q - a).dot(ab) / ab.length_squared))
            if (a + ab * t - q).length <= px:
                return True
        return False

    flags = np.zeros(len(bm.edges), bool)
    near = {}
    for e in bm.edges:
        a, b = e.verts
        new_a, new_b = a.index >= old_verts, b.index >= old_verts
        if new_a and new_b:
            flags[e.index] = True
        elif new_a or new_b:
            old = b if new_a else a
            if old.index not in near:
                near[old.index] = on_line(old)
            flags[e.index] = near[old.index]
    return flags


# ---------------------------------------------------------------- operators

class MULTICAMPROJECT_OT_RemeshPolyCut(bpy.types.Operator):
    """PolyCut: click points on the mesh. The first point or a double click closes the shape,
    Enter keeps it an open line, Esc cancels. The mesh is cut exactly along it (through the
    whole mesh or only the front, per Cut Through); then Set Seam or No Seam for the new
    edges"""
    bl_idname = "multicamproject.remesh_poly_cut"
    bl_label = "PolyCut"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not (obj is not None and obj.type == 'MESH' and context.mode == 'SCULPT'
                and context.area is not None and context.area.type == 'VIEW_3D'):
            return False
        if wf.decimate_pending(obj):
            cls.poll_message_set("Decide the Decimate first (Apply)")
            return False
        return True

    # ------------------------------------------------------------ points (kept in 3D)
    def _add(self, context, p):
        region, rv3d = self._win, self._rv3d
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, p)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized()
        hit, loc, *_rest = context.scene.ray_cast(context.evaluated_depsgraph_get(),
                                                  origin, direction)
        if not hit:
            depth = self._points3d[-1] if self._points3d else rv3d.view_location
            loc = view3d_utils.region_2d_to_location_3d(region, rv3d, p, depth)
        self._points3d.append(loc.copy())

    def _points(self):
        out = []
        for co in self._points3d:
            p = view3d_utils.location_3d_to_region_2d(self._win, self._rv3d, co)
            out.append(None if p is None else (p.x, p.y))
        return out

    def _under_mouse(self):
        best, found = POINT_PX, None
        for i, p in enumerate(self._points()):
            if p is not None:
                d = (Vector(p) - Vector(self._mouse)).length
                if d < best:
                    best, found = d, i
        return found

    def _draw(self, _context):
        points = [p for p in self._points() if p is not None]
        if not points or bpy.context.region != self._win:
            return
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        sh.uniform_float("lineWidth", 2.0)
        sh.uniform_float("color", COLOR)
        batch_for_shader(sh, 'LINE_STRIP', {"pos": points + [self._mouse]}).draw(sh)
        sh.uniform_float("color", (COLOR[0], COLOR[1], COLOR[2], 0.35))
        batch_for_shader(sh, 'LINES', {"pos": [self._mouse, points[0]]}).draw(sh)
        dots = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.point_size_set(7.0)
        dots.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch_for_shader(dots, 'POINTS', {"pos": points}).draw(dots)
        gpu.state.point_size_set(11.0)
        dots.uniform_float("color", COLOR)          # the last point clicked
        batch_for_shader(dots, 'POINTS', {"pos": [points[-1]]}).draw(dots)
        under = self._under_mouse()
        if under is not None and self._points()[under] is not None:
            closing = under == 0 and len(self._points3d) >= 3
            gpu.state.point_size_set(15.0)
            dots.uniform_float("color", CLOSE_COLOR if closing else (1.0, 1.0, 1.0, 1.0))
            batch_for_shader(dots, 'POINTS', {"pos": [self._points()[under]]}).draw(dots)
        gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')

    def _pos(self, event):
        return (event.mouse_x - self._win.x, event.mouse_y - self._win.y)

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.workspace.status_text_set(None)
        context.window.cursor_modal_restore()
        self._area.tag_redraw()

    # ------------------------------------------------------------ modal
    def invoke(self, context, event):
        obj = context.active_object
        hide_preview()
        if cut_flags(obj).any():        # a popup left unanswered: its edges are dropped
            mode = obj.mode
            bpy.ops.object.mode_set(mode='OBJECT')
            forget(obj)
            bpy.ops.object.mode_set(mode=mode)
        self._area = context.area
        self._win = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        self._rv3d = context.area.spaces.active.region_3d
        if self._win is None:
            return {'CANCELLED'}
        self._points3d = []
        self._mouse = self._pos(event)
        self._last_pos = self._mouse
        if event.type == 'LEFTMOUSE':   # Ctrl+click with the tool: the first point
            self._add(context, self._mouse)
        self._last_click = time.monotonic()
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set('CROSSHAIR')
        context.workspace.status_text_set(
            "PolyCut: click: point  ·  click the first point or double click: closed cut  ·  "
            "Enter: open cut  ·  Esc: cancel")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        self._area.tag_redraw()
        if event.type == 'MOUSEMOVE':
            self._mouse = self._pos(event)
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._cut_open(context)
        if event.type in NAVIGATE or event.type.startswith(('NUMPAD_', 'NDOF_')):
            return {'PASS_THROUGH'}
        if event.type == 'ESC' and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value in {'PRESS', 'DOUBLE_CLICK'}:
            p = self._pos(event)
            self._mouse = p
            # the clock runs from when the last click was done: adding a point on a dense mesh
            # takes a moment, which Blender's own double click counts against you
            again = (time.monotonic() - self._last_click < DOUBLE_SECONDS
                     and (Vector(p) - Vector(self._last_pos)).length < DOUBLE_PX)
            if (event.value == 'DOUBLE_CLICK' or again or self._under_mouse() == 0) \
                    and len(self._points3d) >= 3:
                return self._cut_closed(context)
            if not again and event.value == 'PRESS':
                self._add(context, p)
            self._last_pos = p
            self._last_click = time.monotonic()
        return {'RUNNING_MODAL'}

    def _done(self, context, obj, n):
        if not n:
            self.report({'WARNING'}, "The line does not cross the mesh: nothing cut")
            return {'FINISHED'}
        show_preview(obj)
        _popup(context, f"PolyCut: {n:,} new edges")
        return {'FINISHED'}

    def _cut_closed(self, context):
        pts = self._points()
        pts3d = list(self._points3d)
        self._end(context)
        if len(pts) < 3 or None in pts:
            self.report({'WARNING'}, "A closed cut needs 3 points in front of the view")
            return {'CANCELLED'}
        obj = context.active_object
        region, rv3d = self._win, self._rv3d
        forward = (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()

        def ray(p):
            return (view3d_utils.region_2d_to_origin_3d(region, rv3d, p),
                    view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized())

        rays = [ray(p) for p in pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        probe = [(o, d, forward) for o, d in rays]
        for i in range(SAMPLES):
            for j in range(SAMPLES):
                q = (min(xs) + (max(xs) - min(xs)) * (i + 0.5) / SAMPLES,
                     min(ys) + (max(ys) - min(ys)) * (j + 0.5) / SAMPLES)
                if inside(q, pts):
                    probe.append((*ray(q), forward))
        if cutter.auto_depth(obj, context.evaluated_depsgraph_get(), probe) is None:
            self.report({'WARNING'}, "The shape does not cover the object")
            return {'CANCELLED'}
        through = through_on(context)
        near, far = through_depth(obj, probe, rv3d.is_perspective)
        mode = obj.mode
        bpy.ops.object.mode_set(mode='OBJECT')      # Sculpt Mode keeps its own copy of the mesh
        try:
            if through:
                n = cut_closed(obj, rays, forward, near, far)
            else:
                n = cut_knife(context, obj, pts3d, self._area, self._win, True, False)
            if n:
                n = store_flags(obj, outline_only(obj, cut_flags(obj), pts3d, True,
                                                  view_of(region, rv3d)))
            if n:
                show_preview(obj)
        except RuntimeError as e:
            self.report({'ERROR'}, f"PolyCut failed: {e}")
            n = 0
        finally:
            if obj.mode != mode:
                bpy.ops.object.mode_set(mode=mode)
        return self._done(context, obj, n)

    def _cut_open(self, context):
        pts3d = list(self._points3d)
        self._end(context)
        if len(pts3d) < 2:
            self.report({'WARNING'}, "An open cut needs 2 points")
            return {'CANCELLED'}
        obj = context.active_object
        mode = obj.mode
        bpy.ops.object.mode_set(mode='OBJECT')
        try:
            n = cut_knife(context, obj, pts3d, self._area, self._win, False, through_on(context))
            if n:
                n = store_flags(obj, outline_only(obj, cut_flags(obj), pts3d, False,
                                                  view_of(self._win, self._rv3d)))
            if n:
                show_preview(obj)
        except RuntimeError as e:
            self.report({'ERROR'}, f"Knife Project failed: {e}")
            n = 0
        finally:
            if obj.mode != mode:
                bpy.ops.object.mode_set(mode=mode)
        return self._done(context, obj, n)


class MULTICAMPROJECT_OT_PolyCutSeam(bpy.types.Operator):
    """The new edges of the last PolyCut: a seam, or not"""
    bl_idname = "multicamproject.poly_cut_seam"
    bl_label = "PolyCut Seam"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[('SEAM', "Set Seam", ""), ('NONE', "No Seam", "")],
                                   options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and not obj.library

    def execute(self, context):
        obj = context.active_object
        hide_preview()
        mode = obj.mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            n = set_seam(obj) if self.action == 'SEAM' else 0
            forget(obj)
        finally:
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)
        self.report({'INFO'}, f"Seam on {n:,} edges" if n else "PolyCut: no seam")
        return {'FINISHED'}


def _popup(context, title):
    def draw(menu, _context):
        col = menu.layout.column()
        idname = MULTICAMPROJECT_OT_PolyCutSeam.bl_idname
        col.operator(idname, text="Set Seam", icon='UV_EDGESEL').action = 'SEAM'
        col.operator(idname, text="No Seam", icon='CANCEL').action = 'NONE'

    context.window_manager.popup_menu(draw, title=title, icon='QUESTION')


_CLASSES = (MULTICAMPROJECT_OT_RemeshPolyCut, MULTICAMPROJECT_OT_PolyCutSeam)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    setattr(bpy.types.Scene, THROUGH_PROP, bpy.props.BoolProperty(
        name="Cut Through", default=True,
        description="PolyCut: on - cut through the whole mesh (the back too); off - only the "
                    "faces the view sees"))


def unregister():
    hide_preview()
    if hasattr(bpy.types.Scene, THROUGH_PROP):
        delattr(bpy.types.Scene, THROUGH_PROP)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
