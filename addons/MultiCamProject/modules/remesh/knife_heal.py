"""Heal Cut: Blender's own Knife (K and the toolbar Knife), followed by a check that closes
the gaps it leaves and marks the cut as a seam.

Why the Knife leaves gaps on a scan: it decides which face each piece of the stroke lies on by
casting rays from the screen. On a dense scan, where the stroke passes a hair's width from a
vertex, that piece is given to the neighbouring (tiny) face and the face the line actually
crosses keeps both cut points but no edge between them (an n-gon with a hole in the line).
It also selects only the edges it made, so where the line ran along an edge that was already
there, the selection looks broken although the mesh is joined.

The check, after the Knife confirms (its selection is exactly the new edges):
    ends      vertices with one selected edge; ends of different pieces closer than GAP x the
              longest edge at either end are paired, nearest first, until the pieces form a line
    join      an edge already there: selected; one face shared: split across it
              (connect_verts); else cut straight across the faces between (connect_vert_pair)
    seam      every edge of the cut
The header shows the result: "closed n gaps", and any it could not close.

Find Gaps (the magnifier beside it): every seam vertex with one seam edge that is not on the
mesh's border is an open end - the island leaks there. Each click selects and frames the next
gap (ends close together) or open end; stray pieces (a seam edge or two, open at both ends)
come last. Ctrl+click closes them all: ends joined to each other, or to the seam beside them.
Heal Cut also joins its cut to a seam already there, and closes a loop onto itself.

The switch is the scissors button beside Auto Merge (Edit Mode header, per scene). Off: K and
the toolbar Knife are Blender's own (this operator's poll fails, the key falls through to
Blender's keymap item).
"""
from collections import defaultdict

import bmesh
import bpy
from bpy.props import BoolProperty
from mathutils.kdtree import KDTree

PROP = "multicamproject_heal_cut"
GAP = 3.0       # pieces join when their ends are closer than GAP x the longest edge there
MIN_LOOP = 8    # a piece closes onto itself (its two ends joined) only with this many edges
STRAY = 2       # a seam piece this short, open at both ends, is a stray (not a gap)
FRAME = 0.6     # Find Gaps frames a gap from at least this far (m), to see where it is
PAIR = 0.5      # Find Gaps: ends closer than this (m) are shown as one gap
OVER = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE'}  # the Knife eats these


# ----------------------------------------------------------------------------------- healing

def _pieces(edges):
    """(ends, find, link): ends = vertices with one edge of `edges`; find(v) = its piece's
    root; link(edge) adds an edge to the pieces."""
    parent = {}

    def find(v):
        root = v
        while parent.get(root, root) is not root:
            root = parent[root]
        while v is not root:        # flatten
            parent[v], v = root, parent.get(v, v)
        return root

    def link(e):
        ra, rb = find(e.verts[0]), find(e.verts[1])
        if ra is not rb:
            parent[ra] = rb

    deg = defaultdict(int)
    for e in edges:
        a, b = e.verts
        deg[a] += 1
        deg[b] += 1
        link(e)
    return [v for v, n in deg.items() if n == 1], find, link


def _join(bm, a, b):
    """Edges that now join a and b (selected), else []."""
    e = bm.edges.get((a, b))
    if e is not None:
        return [e]
    shared = set(a.link_faces) & set(b.link_faces)
    try:
        if shared:
            out = bmesh.ops.connect_verts(bm, verts=[a, b])["edges"]
        else:
            out = bmesh.ops.connect_vert_pair(bm, verts=[a, b])["edges"]
    except (RuntimeError, ValueError):
        return []
    # connect_vert_pair returns the edges along the new path; keep only those really on it
    return [e for e in out if e.is_valid]


def _seam_degree(v):
    return sum(1 for e in v.link_edges if e.seam)


def _close(bm, ends, find, size, targets=(), gap=GAP):
    """Join open ends: to each other, or to a vertex in `targets` (a seam they should end on).
    Nearest first, each end once. An end joins its own piece's other end only when that piece
    is a real line (MIN_LOOP edges), so a short piece is never folded into a triangle.
    Returns (new edges, gaps closed)."""
    reach = {v: max(e.calc_length() for e in v.link_edges) for v in ends}
    targets = list(targets)
    tree = None
    if targets:
        tree = KDTree(len(targets))
        for k, w in enumerate(targets):
            tree.insert(w.co, k)
        tree.balance()
    cands = []
    for i, a in enumerate(ends):
        for b in ends[i + 1:]:
            d = (a.co - b.co).length
            if d > gap * max(reach[a], reach[b]):
                continue
            if find(a) is find(b) and size[find(a)] < MIN_LOOP:
                continue
            e = bm.edges.get((a, b))
            if e is not None and (e.select or e.seam):
                continue
            cands.append((d, 0, a, b))
        if tree is not None:
            for _co, k, d in tree.find_range(a.co, gap * reach[a]):
                w = targets[k]
                if w is not a and find(w) is not find(a):
                    cands.append((d, 1, a, w))
    cands.sort(key=lambda c: (c[0], c[1]))
    used, new, closed = set(), [], 0
    for _d, kind, a, b in cands:
        if a in used or (kind == 0 and b in used):
            continue
        if not (a.is_valid and b.is_valid):
            continue
        joined = _join(bm, a, b)
        if not joined:
            continue
        new += joined
        used.add(a)
        if kind == 0:
            used.add(b)
        closed += 1
    return new, closed


def _sizes(edges, find):
    size = defaultdict(int)
    for e in edges:
        size[find(e.verts[0])] += 1
    return size


def heal(bm, gap=GAP):
    """Close the gaps of the selected cut, between its own pieces and to the seams already
    there (a cut ending a little short of an older seam), and mark it as a seam.
    Returns (closed, left, edges): gaps closed, open ends left, seam edges of the cut."""
    cut, old = [], set()
    for e in bm.edges:
        if e.select:
            cut.append(e)
        elif e.seam:
            old.update(e.verts)
    if not cut:
        return 0, 0, 0
    ends, find, link = _pieces(cut)
    ends = [v for v in ends if not v.is_boundary and v not in old]   # on a seam: closed
    old.difference_update(v for e in cut for v in e.verts)
    new, closed = _close(bm, ends, find, _sizes(cut, find), old, gap)
    for e in new:
        if e.is_valid:
            e.select_set(True)
            cut.append(e)
    cut = [e for e in cut if e.is_valid]
    for e in cut:
        e.seam = True
    verts = {v for e in cut for v in e.verts}
    left = sum(1 for v in verts if not v.is_boundary and _seam_degree(v) == 1)
    return closed, left, len(cut)


# ------------------------------------------------------------------------------------- gaps

def seam_gaps(bm):
    """Where the seams are open: [(verts, size in m, stray)], gaps first, nearest first.
    An open end = a seam vertex with one seam edge, not on the mesh's border (a seam ending
    on a border or a hole is closed). Ends close to each other make one gap; a short seam
    piece open at both ends (STRAY edges or fewer) is listed as stray, after the gaps."""
    seams, deg = [], defaultdict(int)
    for e in bm.edges:
        if e.seam:
            seams.append(e)
            deg[e.verts[0]] += 1
            deg[e.verts[1]] += 1
    _ends, find, _link = _pieces(seams)
    size = _sizes(seams, find)
    ends = [v for v, n in deg.items() if n == 1 and not v.is_boundary]
    stray = defaultdict(list)
    real = []
    for v in ends:
        (stray[find(v)] if size[find(v)] <= STRAY else real).append(v)
    pairs = sorted(((a.co - b.co).length, i, j)
                   for i, a in enumerate(real) for j, b in enumerate(real[i + 1:], i + 1))
    used, out = set(), []
    for d, i, j in pairs:
        if d > PAIR or i in used or j in used:
            continue
        used.update((i, j))
        out.append(([real[i], real[j]], d, False))
    out += [([v], 0.0, False) for k, v in enumerate(real) if k not in used]
    out.sort(key=lambda g: (g[1] == 0.0, g[1]))
    for vs in stray.values():
        out.append((vs, max((a.co - b.co).length for a in vs for b in vs), True))
    return out


def heal_seams(bm, gap=GAP):
    """Close every seam gap: open ends joined to each other or to a nearby seam (Ctrl+click
    on Find Gaps). Stray pieces are left as they are. Returns (closed, new edges)."""
    seams, verts, deg = [], set(), defaultdict(int)
    for e in bm.edges:
        if e.seam:
            seams.append(e)
            verts.update(e.verts)
            deg[e.verts[0]] += 1
            deg[e.verts[1]] += 1
    _ends, find, _link = _pieces(seams)
    size = _sizes(seams, find)
    ends = [v for v, n in deg.items()
            if n == 1 and not v.is_boundary and size[find(v)] > STRAY]
    new, closed = _close(bm, ends, find, size, verts.difference(ends), gap)
    new = [e for e in new if e.is_valid]
    for e in new:
        e.seam = True
    return closed, new


# ---------------------------------------------------------------------------------- operator

def _counts(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    return len(bm.verts), len(bm.edges)


class MULTICAMPROJECT_OT_HealKnife(bpy.types.Operator):
    """Blender's Knife, then the cut is checked: gaps closed, marked as a seam (Heal Cut on)"""
    bl_idname = "multicamproject.heal_knife"
    bl_label = "Knife (Heal Cut)"
    bl_options = {'REGISTER', 'UNDO'}

    use_occlude_geometry: BoolProperty(name="Occlude Geometry", default=True,
                                       options={'SKIP_SAVE'})
    only_selected: BoolProperty(name="Only Selected", default=False, options={'SKIP_SAVE'})
    wait_for_input: BoolProperty(name="Wait for Input", default=True, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (context.mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH'
                and getattr(context.scene, PROP, False))

    def invoke(self, context, event):
        self._obj = context.edit_object
        self._base = _counts(self._obj)
        wm = context.window_manager
        wm.modal_handler_add(self)      # first: the Knife's handler goes above ours
        bpy.ops.mesh.knife_tool('INVOKE_DEFAULT',
                                use_occlude_geometry=self.use_occlude_geometry,
                                only_selected=self.only_selected,
                                wait_for_input=self.wait_for_input)
        self._timer = wm.event_timer_add(0.1, window=context.window)
        return {'RUNNING_MODAL'}

    def _stop(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def modal(self, context, event):
        obj = self._obj
        try:
            alive = context.mode == 'EDIT_MESH' and obj.mode == 'EDIT'
        except ReferenceError:
            alive = False
        if not alive:
            self._stop(context)
            return {'CANCELLED', 'PASS_THROUGH'}
        if _counts(obj) != self._base:      # the Knife confirmed: its cut is selected
            self._stop(context)
            bm = bmesh.from_edit_mesh(obj.data)
            closed, left, n = heal(bm)
            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
            msg = f"Heal Cut: {n} edges as seam, closed {closed} gap{'s' * (closed != 1)}"
            if left:
                self.report({'WARNING'}, f"{msg}; {left} open end{'s' * (left != 1)} left "
                                         "(Find Gaps shows them)")
            else:
                self.report({'INFO'}, msg)
            return {'FINISHED', 'PASS_THROUGH'}
        if event.type in OVER:              # reached us: the Knife is gone, nothing cut
            self._stop(context)
            return {'CANCELLED', 'PASS_THROUGH'}
        return {'PASS_THROUGH'}


class MULTICAMPROJECT_OT_FindSeamGaps(bpy.types.Operator):
    """Show where the seams are open, one gap per click (selected and framed).
Ctrl+click: close every gap (open ends joined to each other or to the seam beside them)"""
    bl_idname = "multicamproject.find_seam_gaps"
    bl_label = "Find Seam Gaps"
    bl_options = {'REGISTER', 'UNDO'}

    heal: BoolProperty(name="Close Gaps", default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return context.mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH'

    def invoke(self, context, event):
        self.heal = self.heal or event.ctrl
        return self.execute(context)

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        closed = 0
        if self.heal:
            closed, _new = heal_seams(bm)
            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        groups = seam_gaps(bm)
        gaps = [g for g in groups if not g[2]]
        done = f"Closed {closed} gap{'s' * (closed != 1)}. " if self.heal else ""
        if not groups:
            self.report({'INFO'}, done + "No seam gaps: every seam ends on a seam or the mesh's "
                                         "border")
            return {'FINISHED'}
        key = (obj.name, len(groups))
        i = (_cycle[1] + 1) % len(groups) if _cycle[0] == key and not self.heal else 0
        _cycle[:] = [key, i]
        verts, size, stray = groups[i]
        bpy.ops.mesh.select_all(action='DESELECT')
        for v in verts:
            v.select_set(True)
            for e in v.link_edges:
                if e.seam:
                    e.select_set(True)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _frame(context)
        what = ("stray seam piece" if stray else
                f"{size * 100:.1f} cm gap" if size else "open end, nothing near")
        tail = "" if self.heal else " - Ctrl+click closes them"
        self.report({'WARNING'}, f"{done}{i + 1} of {len(groups)}: {what} "
                                 f"({len(gaps)} gap{'s' * (len(gaps) != 1)}){tail}")
        return {'FINISHED'}


_cycle = [None, -1]     # (object, how many) and the one shown last: the next click shows the next


def _frame(context):
    """Frame the selection in the 3D View (from a header button too), not closer than FRAME."""
    area = context.area if context.area and context.area.type == 'VIEW_3D' else None
    if area is None:
        return
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return
    with context.temp_override(area=area, region=region):
        bpy.ops.view3d.view_selected()
    rv3d = area.spaces.active.region_3d
    rv3d.view_distance = max(rv3d.view_distance, FRAME)


# ------------------------------------------------------------------------------------ header

def _draw_button(layout, context):
    from ... import addon_icons
    row = layout.row(align=True)
    row.prop(context.scene, PROP, text="", **addon_icons.kw("heal_cut", 'SCULPTMODE_HLT'))
    row.operator(MULTICAMPROJECT_OT_FindSeamGaps.bl_idname, text="", icon='VIEWZOOM')


class _Layout:
    """The header's layout, drawing the button just before the Options popover."""

    def __init__(self, layout, context):
        self._l, self._c, self.drawn = layout, context, False

    def __getattr__(self, name):
        return getattr(self._l, name)

    def popover_group(self, *args, **kw):
        if not self.drawn and (kw.get("context") == ".mesh_edit"
                               or (args and args[0] == ".mesh_edit")):
            _draw_button(self._l, self._c)
            self.drawn = True
        return self._l.popover_group(*args, **kw)


class _Header:
    def __init__(self, real, layout):
        self._r, self.layout = real, layout

    def __getattr__(self, name):
        return getattr(self._r, name)


_ORIG = "_multicamproject_draw_mode_settings"


def _draw_mode_settings(self, context):
    orig = getattr(bpy.types.VIEW3D_HT_tool_header, _ORIG)
    if context.mode != 'EDIT_MESH':
        return orig(self, context)
    lay = _Layout(self.layout, context)
    orig(_Header(self, lay), context)
    if not lay.drawn:       # Blender's header changed: at the end then
        _draw_button(self.layout, context)


def _patch(on):
    cls = bpy.types.VIEW3D_HT_tool_header
    if on:
        if not hasattr(cls, _ORIG):
            setattr(cls, _ORIG, cls.draw_mode_settings)
        cls.draw_mode_settings = _draw_mode_settings
    elif hasattr(cls, _ORIG):
        cls.draw_mode_settings = getattr(cls, _ORIG)
        delattr(cls, _ORIG)


# ------------------------------------------------------------------------------ registration

_keymaps = []


def register():
    bpy.types.Scene.multicamproject_heal_cut = BoolProperty(
        name="Heal Cut", default=True,
        description="K and the Knife tool check their cut: gaps closed, the cut marked as a "
                    "seam. Off: Blender's own Knife")
    bpy.utils.register_class(MULTICAMPROJECT_OT_HealKnife)
    bpy.utils.register_class(MULTICAMPROJECT_OT_FindSeamGaps)
    _patch(True)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:      # background mode
        return
    idname = MULTICAMPROJECT_OT_HealKnife.bl_idname
    km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
    kmi = km.keymap_items.new(idname, 'K', 'PRESS', head=True)
    kmi.properties.use_occlude_geometry = True
    kmi.properties.only_selected = False
    _keymaps.append((km, kmi))
    km = kc.keymaps.new(name="3D View Tool: Edit Mesh, Knife", space_type='VIEW_3D',
                        region_type='WINDOW', tool=True)
    kmi = km.keymap_items.new(idname, 'LEFTMOUSE', 'PRESS', head=True)
    kmi.properties.wait_for_input = False
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    _patch(False)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_FindSeamGaps)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_HealKnife)
    del bpy.types.Scene.multicamproject_heal_cut
