"""PolyCut (the Sculpt Mode tool, Ctrl+Shift+K): draw on the mesh, it is cut exactly along the line
and the new edges are offered as a seam. Nothing else.

    Ctrl+click   start; click: add a point (the points stay on the object while you navigate)
    close        click the first point (green under the mouse) or double click: a closed shape
    Enter        an open line
    Esc          drop it

Cut Through (the tool's header, 03): on - through the whole mesh (a closed shape with an Exact
boolean, MCP-PolyCut, nothing removed; an open line with Knife Project, Cut Through); off -
only the faces the view sees (Knife Project, both).

While drawing, the cut is previewed on the mesh: yellow on the surface the view sees, and with
Cut Through fainter on every layer behind it.

Edit Mode: Ctrl+Shift+K brings you to the tool (K is Surface Cut, Blender's Knife is Shift+K).

The last cut is remembered on the mesh (its outline edges, mcp_cut_edges, and the faces inside a
closed one, mcp_cut_inside) until the next cut: switching Select Edges / Select Faces / Auto Seam
on afterwards applies it to that cut too.

Auto Seam (the tool's header, 03): the outline becomes a seam right away, no popup.

Select Faces / Select Edges (the tool's header, 03; one at a time): no popup, Edit Mode with
the cut selected, to set seams yourself. Faces: the faces inside a closed shape, Face select
(right-click: Boundary as Seam); an open line has no inside, so its cut edges. Edges: the cut
edges, Edge select (right-click: Mark Seam).

Otherwise, after the cut its outline shows in yellow (closed: the border of what is inside, one island;
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
CUT_FACES = "mcp_cut_inside"        # face attribute: the faces inside the last closed cut
SELECT_PROP = "multicamproject_polycut_select"              # Select Faces after the cut
SELECT_EDGES_PROP = "multicamproject_polycut_select_edges"  # Select Edges after the cut
AUTO_SEAM_PROP = "multicamproject_polycut_auto_seam"        # the outline a seam, no popup
INVERT_PROP = "multicamproject_polycut_invert"              # Select Faces: the other side
CLOSE_BORDER_PROP = "multicamproject_polycut_close_border"  # the open rim part of the outline
CUT_RIM = "mcp_cut_rim"             # edge attribute: where the last Surface Cut ran along an open rim
CUT_PIECE = "mcp_cut_piece"         # face attribute: the piece the last Surface Cut split
CUT_EDGES = gn.CUT_EDGES            # edge attribute: the last cut's new edges, until the popup
POINT_PX = 12                       # a click this near an existing point is on it (x UI scale)
DOUBLE_SECONDS, DOUBLE_PX = 0.45, 6
DOT_PX, LAST_PX, HOVER_PX = 4.0, 6.0, 9.0   # disc radii (x UI scale)
SAMPLES = 12                        # interior rays per axis: does the shape cover the object
COLOR = (1.0, 0.85, 0.2, 1.0)
CLOSE_COLOR = (0.3, 1.0, 0.4, 1.0)
NAVIGATE = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
            'MOUSEROTATE', 'MOUSESMARTZOOM'}
_preview = {"lines": None, "handle": None}
TRACE_PX = 6.0          # the preview samples the drawn line this often (x UI scale)
TRACE_LAYERS = 12       # Cut Through: at most this many surfaces behind each other


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


def _drop(obj, name):
    a = obj.data.attributes.get(name)
    if a is not None:
        obj.data.attributes.remove(a)


def forget(obj):
    """The last cut is no longer remembered (a new cut starts)."""
    for name in (CUT_EDGES, CUT_FACES, CUT_RIM, CUT_PIECE):
        _drop(obj, name)


def remembers(obj):
    return obj is not None and obj.type == 'MESH' and CUT_EDGES in obj.data.attributes


def store_flags(obj, flags):
    me = obj.data
    _drop(obj, CUT_EDGES)
    me.attributes.new(CUT_EDGES, 'BOOLEAN', 'EDGE').data.foreach_set("value", flags)
    me.update()
    return int(flags.sum())


def store_faces(obj, faces):
    me = obj.data
    _drop(obj, CUT_FACES)
    if faces is not None:
        me.attributes.new(CUT_FACES, 'BOOLEAN', 'FACE').data.foreach_set("value", faces.astype(bool))
        me.update()


def store_bools(obj, name, domain, flags):
    """A remembered bool layer of the last cut (None: dropped)."""
    me = obj.data
    _drop(obj, name)
    if flags is not None:
        me.attributes.new(name, 'BOOLEAN', domain).data.foreach_set("value", flags.astype(bool))
        me.update()


def read_bools(obj, name, domain):
    me = obj.data
    a = me.attributes.get(name)
    if a is None or a.domain != domain:
        return None
    out = np.zeros(len(me.polygons) if domain == 'FACE' else len(me.edges), bool)
    a.data.foreach_get("value", out)
    return out


def selection_faces(context, obj):
    """The faces Select Faces picks for the last cut: inside, or with Invert the rest of the
    piece it split (the whole mesh for a PolyCut). None for an open line."""
    inside = cut_faces(obj)
    if inside is None or not inside.any():
        return None
    if not getattr(context.scene, INVERT_PROP, False):
        return inside
    piece = read_bools(obj, CUT_PIECE, 'FACE')
    if piece is None:
        piece = np.ones(len(inside), bool)
    return piece & ~inside


def cut_faces(obj):
    me = obj.data
    a = me.attributes.get(CUT_FACES)
    if a is None or a.domain != 'FACE':
        return None
    faces = np.zeros(len(me.polygons), bool)
    a.data.foreach_get("value", faces)
    return faces


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
    full, width, height = view[:3]
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


def outline_only(obj, flags, points3d, closed, view, through=True):
    """The cut's new edges reduced to the drawn outline: no edge that the cut added across a face
    (a diagonal of a split face, a sliver between two cuts). Closed: the border of everything
    inside the shape (one loop, the inside is one island); open: the cut edges on the line.
    Returns (edges, the faces inside - closed only, else None)."""
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
        return flags, None
    segs = list(zip(line, line[1:] + (line[:1] if closed else [])))
    v2 = _project(co, view)
    near_v = _dist_to_line(v2, segs) <= ON_LINE_PX
    mid = _project((co[ev[:, 0]] + co[ev[:, 1]]) / 2.0, view)
    on = near_v[ev[:, 0]] & near_v[ev[:, 1]] & (_dist_to_line(mid, segs) <= ON_LINE_PX)
    if not closed:
        return flags & on, None
    # every face is now wholly in or out of the shape: its centre says which
    totals = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_total", totals)
    centres = np.empty(len(me.polygons) * 3, np.float64)
    me.polygons.foreach_get("center", centres)
    centres = centres.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    in_shape = _inside_polygon(_project(centres, view), line)
    loop_edge = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("edge_index", loop_edge)
    loop_face = np.repeat(np.arange(len(me.polygons)), totals)
    n = len(me.edges)
    if not through:
        # front only: the outline has gaps where nearer faces hide farther ones, so it does not
        # split the mesh into pieces - the cut edges on the line are the seam, and the selection
        # is every face the view sees inside the shape
        border = flags & on
        return border, _visible(obj, in_shape, centres, view)
    # through: the line splits the mesh into pieces. A piece is inside when most of its area is
    # (one long or bent face's centre can sit across the line: a whole piece cannot). The seam is
    # every edge on the line between an inside and an outside piece - also an edge the mesh
    # already had there - so the loop closes and the inside is one island.
    areas = np.empty(len(me.polygons), np.float64)
    me.polygons.foreach_get("area", areas)
    piece = _pieces(on, loop_edge, loop_face, len(me.polygons))
    count = int(piece.max()) + 1 if len(piece) else 0
    share = np.bincount(piece, weights=areas * in_shape, minlength=count)
    total = np.bincount(piece, weights=areas, minlength=count)
    inside = (share > 0.5 * np.maximum(total, 1e-30))[piece]
    tot = np.bincount(loop_edge, minlength=n)
    ins = np.bincount(loop_edge[inside[loop_face]], minlength=n)
    border = (ins > 0) & (ins < tot) & on
    return border, inside


def _pieces(cuts, loop_edge, loop_face, n_faces):
    """A piece number per face: faces joined through edges that are not in `cuts`."""
    parent = np.arange(n_faces)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    first = {}
    for e, f in zip(loop_edge.tolist(), loop_face.tolist()):
        if cuts[e]:
            continue
        g = first.setdefault(e, f)
        if g != f:
            ra, rb = find(f), find(g)
            if ra != rb:
                parent[ra] = rb
    roots = np.array([find(f) for f in range(n_faces)], np.int64)
    _u, piece = np.unique(roots, return_inverse=True)
    return piece


def select_cut(obj, faces=None, edges=None):
    """Object Mode: exactly these faces (or edges) selected, for Edit Mode."""
    me = obj.data
    vs = np.zeros(len(me.vertices), bool)
    es = np.zeros(len(me.edges), bool)
    fs = np.zeros(len(me.polygons), bool)
    if faces is not None:
        totals = np.empty(len(me.polygons), np.int64)
        me.polygons.foreach_get("loop_total", totals)
        loop_face = np.repeat(np.arange(len(me.polygons)), totals)
        lv = np.empty(len(me.loops), np.int64)
        me.loops.foreach_get("vertex_index", lv)
        le = np.empty(len(me.loops), np.int64)
        me.loops.foreach_get("edge_index", le)
        fs = faces.astype(bool)
        sel = fs[loop_face]
        vs[lv[sel]] = True
        es[le[sel]] = True
    else:
        ev = np.empty(len(me.edges) * 2, np.int64)
        me.edges.foreach_get("vertices", ev)
        es = edges.astype(bool)
        vs[ev.reshape(-1, 2)[es].ravel()] = True
    me.vertices.foreach_set("select", vs)
    me.edges.foreach_set("select", es)
    me.polygons.foreach_set("select", fs)
    me.update()
    return int(fs.sum()) if faces is not None else int(es.sum())


def auto_seam_on(context):
    return getattr(context.scene, AUTO_SEAM_PROP, False)


def select_mode(context):
    """'FACE', 'EDGE' or None (the seam popup)."""
    if getattr(context.scene, SELECT_PROP, False):
        return 'FACE'
    if getattr(context.scene, SELECT_EDGES_PROP, False):
        return 'EDGE'
    return None


def _ui():
    return bpy.context.preferences.system.ui_scale


def _discs(centres, radius, color):
    """Filled circles in region pixels (point sizes are not honoured on every GPU backend)."""
    if not centres:
        return
    import math
    ring = [(math.cos(a * math.tau / 16), math.sin(a * math.tau / 16)) for a in range(16)]
    tris = []
    for cx, cy in centres:
        for i in range(16):
            (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % 16]
            tris += [(cx, cy), (cx + x0 * radius, cy + y0 * radius), (cx + x1 * radius, cy + y1 * radius)]
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    sh.uniform_float("color", color)
    batch_for_shader(sh, 'TRIS', {"pos": tris}).draw(sh)


def view_of(region, rv3d):
    return (np.array(rv3d.perspective_matrix, np.float64), region.width, region.height,
            rv3d.view_matrix.inverted(), rv3d.is_perspective)


def _visible(obj, faces, centres, view):
    """Of the faces, those the view sees (the first face a ray from the eye meets)."""
    from mathutils.bvhtree import BVHTree
    _full, _w, _h, view_inv, persp = view
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    tree = BVHTree.FromPolygons(co.reshape(-1, 3).tolist(), [list(p.vertices) for p in me.polygons],
                                all_triangles=False)
    inv = obj.matrix_world.inverted()
    eye = inv @ view_inv.translation
    look = (inv.to_3x3() @ (view_inv.to_3x3() @ Vector((0.0, 0.0, -1.0)))).normalized()
    size = max(obj.dimensions.length, 1e-6)
    out = np.zeros(len(faces), bool)
    for i in np.nonzero(faces)[0].tolist():
        c = inv @ Vector(centres[i])
        if persp:
            start, d = eye, (c - eye).normalized()
        else:
            d = look
            start = c - d * (10.0 * max(size, 1.0))
        loc, _n, hit, _dist = tree.ray_cast(start, d)
        # the face itself, or a hit right at its centre (a neighbour sharing the spot)
        out[i] = hit is None or hit == i or (loc - c).length <= 1e-3 * size
    return out


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
        if not (obj is not None and obj.type == 'MESH' and context.mode in {'SCULPT', 'EDIT_MESH'}
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
        best, found = POINT_PX * _ui(), None
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
        k = _ui()
        under = self._under_mouse()
        if under is not None and self._points()[under] is not None:
            closing = under == 0 and len(self._points3d) >= 3
            _discs([self._points()[under]], HOVER_PX * k, CLOSE_COLOR if closing else (1.0, 1.0, 1.0, 0.9))
        _discs(points, DOT_PX * k, (1.0, 1.0, 1.0, 1.0))
        _discs([points[-1]], LAST_PX * k, COLOR)        # the last point clicked
        if len(self._points3d) >= 3 and self._points()[0] is not None:
            _discs([self._points()[0]], DOT_PX * k, CLOSE_COLOR)   # the first point closes the shape
        gpu.state.blend_set('NONE')

    # ------------------------------------------------------------ the cut's preview on the mesh
    def _build_tree(self, context, obj):
        from mathutils.bvhtree import BVHTree
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            self._tree = BVHTree.FromBMesh(bm)
        else:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            self._tree = BVHTree.FromBMesh(bm)
            bm.free()
        self._mw = obj.matrix_world.copy()
        self._inv = self._mw.inverted()
        self._eps = max(obj.dimensions.length, 1e-6) * 1e-5
        self._through = through_on(context)
        self._traces = {}
        self._view_key = None

    def _hits(self, p):
        """World points where the ray under the region pixel p meets the mesh, nearest first
        (only the first without Cut Through)."""
        o = view3d_utils.region_2d_to_origin_3d(self._win, self._rv3d, p)
        d = view3d_utils.region_2d_to_vector_3d(self._win, self._rv3d, p).normalized()
        lo = self._inv @ o
        ld = (self._inv.to_3x3() @ d).normalized()
        out = []
        start = lo
        for _ in range(TRACE_LAYERS if self._through else 1):
            loc, _n, _i, _dist = self._tree.ray_cast(start, ld)
            if loc is None:
                break
            out.append(self._mw @ loc)
            start = loc + ld * self._eps
        return o, d, out

    def _trace(self, a, b):
        """The drawn segment a -> b (region pixels) on the mesh: [(layer, p, q)...] in world."""
        a, b = Vector(a), Vector(b)
        steps = max(1, int((b - a).length / (TRACE_PX * _ui())))
        rays = [self._hits(a.lerp(b, i / steps)) for i in range(steps + 1)]
        out = []
        for (o1, _d1, h1), (o2, d2, h2) in zip(rays, rays[1:]):
            for k in range(min(len(h1), len(h2))):
                # same surface: no bigger depth jump than a few times the gap between the rays
                lateral = (h1[k] - (o2 + d2 * (h1[k] - o1).length)).length
                if (h1[k] - h2[k]).length <= 4.0 * lateral + 10 * self._eps:
                    out.append((k, h1[k], h2[k]))
        return out

    def _segment(self, i, a, b):
        key = (i, a, b)
        hit = self._traces.get(key)
        if hit is None:
            hit = self._traces[key] = self._trace(a, b)
        return hit

    def _draw_3d(self, _context):
        if bpy.context.region != self._win:
            return
        pts = self._points()
        if not pts or None in pts:
            return
        view_key = (tuple(tuple(r) for r in self._rv3d.perspective_matrix), self._win.width, self._win.height)
        if view_key != self._view_key:     # navigated: the drawn line covers other faces now
            self._view_key = view_key
            self._traces = {}
        segs = []
        for i in range(len(pts) - 1):
            segs += self._segment(i, pts[i], pts[i + 1])
        live = self._trace(pts[-1], self._mouse)
        closing = self._trace(self._mouse, pts[0]) if len(pts) >= 2 else []
        front = [q for k, a, b in segs + live if k == 0 for q in (a, b)]
        back = [q for k, a, b in segs + live if k > 0 for q in (a, b)]
        hint = [q for k, a, b in closing if k == 0 for q in (a, b)]
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        for lines, width, alpha in ((back, 1.5, 0.45), (hint, 1.5, 0.3), (front, 3.0, 1.0)):
            if lines:
                sh.uniform_float("lineWidth", width)
                sh.uniform_float("color", (COLOR[0], COLOR[1], COLOR[2], alpha))
                batch_for_shader(sh, 'LINES', {"pos": lines}).draw(sh)
        gpu.state.blend_set('NONE')

    def _pos(self, event):
        return (event.mouse_x - self._win.x, event.mouse_y - self._win.y)

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        bpy.types.SpaceView3D.draw_handler_remove(self._handle_3d, 'WINDOW')
        context.workspace.status_text_set(None)
        context.window.cursor_modal_restore()
        self._area.tag_redraw()

    # ------------------------------------------------------------ modal
    def invoke(self, context, event):
        obj = context.active_object
        hide_preview()
        if remembers(obj) or CUT_FACES in obj.data.attributes:     # the last cut: forgotten
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
        self._selected = None
        self._seamed = 0
        self._mouse = self._pos(event)
        self._last_pos = self._mouse
        if event.type == 'LEFTMOUSE':   # Ctrl+click with the tool: the first point
            self._add(context, self._mouse)
        self._last_click = time.monotonic()
        self._build_tree(context, obj)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_3d, (context,), 'WINDOW', 'POST_VIEW')
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
                     and (Vector(p) - Vector(self._last_pos)).length < DOUBLE_PX * _ui())
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
        seam = f"seam on {self._seamed:,} edges" if self._seamed else ""
        if self._selected:
            what = "faces inside the cut" if self._selected[0] == 'FACE' else "cut edges"
            self.report({'INFO'}, f"PolyCut: {self._selected[1]:,} {what} selected"
                                  + (f", {seam}" if seam else ""))
            return {'FINISHED'}
        if self._seamed:
            self.report({'INFO'}, f"PolyCut: {seam}")
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
                edges, faces = outline_only(obj, cut_flags(obj), pts3d, True,
                                            view_of(region, rv3d), through)
                n = store_flags(obj, edges)
                store_faces(obj, faces if n else None)
                if n and auto_seam_on(context):
                    self._seamed = set_seam(obj)
                want = select_mode(context) if n else None
                if want:
                    sel = selection_faces(context, obj) if want == 'FACE' else None
                    self._selected = (('FACE', select_cut(obj, faces=sel)) if sel is not None
                                      else ('EDGE', select_cut(obj, edges=edges)))
        except RuntimeError as e:
            self.report({'ERROR'}, f"PolyCut failed: {e}")
            n = 0
        finally:
            self._back(context, obj, mode)
        return self._done(context, obj, n)

    def _back(self, context, obj, mode):
        """Select after Cut: Edit Mode, Face / Edge select; else the mode it was in."""
        if self._selected:
            bpy.ops.object.mode_set(mode='EDIT')
            context.tool_settings.mesh_select_mode = (False, self._selected[0] == 'EDGE',
                                                      self._selected[0] == 'FACE')
        elif obj.mode != mode:
            bpy.ops.object.mode_set(mode=mode)

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
                edges, _faces = outline_only(obj, cut_flags(obj), pts3d, False,
                                             view_of(self._win, self._rv3d))
                n = store_flags(obj, edges)
                if n and auto_seam_on(context):
                    self._seamed = set_seam(obj)
                if n and select_mode(context):     # an open line has no inside: its edges
                    self._selected = ('EDGE', select_cut(obj, edges=edges))
        except RuntimeError as e:
            self.report({'ERROR'}, f"Knife Project failed: {e}")
            n = 0
        finally:
            self._back(context, obj, mode)
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
            n = set_seam(obj) if self.action == 'SEAM' else 0     # the cut stays remembered
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


def apply_last(context, what):
    """A toggle switched after a cut: the remembered cut gets it too. 'SEAM': its outline a
    seam; 'FACE' / 'EDGE': Edit Mode with its inside faces (Invert: the rest of the piece) / its
    edges selected; 'BORDER': Close Border switched - the open rim joins the outline or leaves
    it (and its seam follows the outline's)."""
    obj = getattr(context, "active_object", None)
    if not remembers(obj) or obj.library:
        return
    mode = obj.mode
    end = mode
    try:
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if what == 'BORDER':
            _close_border(context, obj)
            what = 'EDGE' if getattr(context.scene, SELECT_EDGES_PROP, False) else None
            if what is not None:
                select_cut(obj, edges=cut_flags(obj))
                end = 'EDIT'
        elif what == 'SEAM':
            set_seam(obj)
        else:
            faces = selection_faces(context, obj) if what == 'FACE' else None
            if faces is not None and faces.any():
                select_cut(obj, faces=faces)
            else:                   # an open line has no inside: its edges
                what = 'EDGE'
                select_cut(obj, edges=cut_flags(obj))
            end = 'EDIT'
        hide_preview()
    except RuntimeError as e:
        print(f"[MultiCamProject] PolyCut: the last cut could not be applied: {e}")
    finally:
        if obj.mode != end:
            bpy.ops.object.mode_set(mode=end)
        if end == 'EDIT' and what in {'FACE', 'EDGE'}:
            context.tool_settings.mesh_select_mode = (False, what == 'EDGE', what == 'FACE')


def _close_border(context, obj):
    """The open rim stretch in or out of the remembered outline; if the outline is a seam
    already, the rim's seam follows."""
    rim = read_bools(obj, CUT_RIM, 'EDGE')
    if rim is None or not rim.any():
        return
    me = obj.data
    edges = cut_flags(obj)
    on = getattr(context.scene, CLOSE_BORDER_PROP, False)
    store_flags(obj, (edges | rim) if on else (edges & ~rim))
    seams = np.zeros(len(me.edges), bool)
    me.edges.foreach_get("use_seam", seams)
    path = edges & ~rim
    if path.any() and seams[path].all():        # the outline was seamed: the rim follows
        seams[rim] = on
        me.edges.foreach_set("use_seam", seams)
        me.update()


def _invert(scene, context):
    if getattr(scene, SELECT_PROP):
        apply_last(context, 'FACE')


def _border(_scene, context):
    apply_last(context, 'BORDER')


def _only_faces(scene, context):
    if getattr(scene, SELECT_PROP):
        if getattr(scene, SELECT_EDGES_PROP):
            setattr(scene, SELECT_EDGES_PROP, False)
        apply_last(context, 'FACE')


def _only_edges(scene, context):
    if getattr(scene, SELECT_EDGES_PROP):
        if getattr(scene, SELECT_PROP):
            setattr(scene, SELECT_PROP, False)
        apply_last(context, 'EDGE')


def _auto_seam(scene, context):
    if getattr(scene, AUTO_SEAM_PROP):
        apply_last(context, 'SEAM')


_CLASSES = (MULTICAMPROJECT_OT_RemeshPolyCut, MULTICAMPROJECT_OT_PolyCutSeam)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    setattr(bpy.types.Scene, THROUGH_PROP, bpy.props.BoolProperty(
        name="Cut Through", default=True,
        description="PolyCut: on - cut through the whole mesh (the back too); off - only the "
                    "faces the view sees"))
    setattr(bpy.types.Scene, AUTO_SEAM_PROP, bpy.props.BoolProperty(
        name="Auto Seam", default=False, update=_auto_seam,
        description="PolyCut: the cut's outline becomes a seam right away, no popup (with Select "
                    "Faces / Edges: the seam, then the selection)"))
    setattr(bpy.types.Scene, SELECT_PROP, bpy.props.BoolProperty(
        name="Select Faces", default=False, update=_only_faces,
        description="PolyCut: no seam popup - Edit Mode, Face select, the faces inside the cut "
                    "selected (an open line: its edges), to set seams yourself"))
    setattr(bpy.types.Scene, SELECT_EDGES_PROP, bpy.props.BoolProperty(
        name="Select Edges", default=False, update=_only_edges,
        description="PolyCut: no seam popup - Edit Mode, Edge select, the cut edges selected, "
                    "to set seams yourself"))
    setattr(bpy.types.Scene, INVERT_PROP, bpy.props.BoolProperty(
        name="Invert", default=False, update=_invert,
        description="Select Faces picks the other side of the cut (the rest of the piece)"))
    setattr(bpy.types.Scene, CLOSE_BORDER_PROP, bpy.props.BoolProperty(
        name="Close Border", default=True, update=_border,
        description="Surface Cut: where the cut runs along the mesh's open rim, that rim stretch "
                    "is part of the outline (its selection and seam); off: left open"))


def unregister():
    hide_preview()
    for prop in (THROUGH_PROP, SELECT_PROP, SELECT_EDGES_PROP, AUTO_SEAM_PROP, INVERT_PROP,
                 CLOSE_BORDER_PROP):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
