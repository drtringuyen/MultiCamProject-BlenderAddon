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

The switch is the scissors button beside Auto Merge (Edit Mode header, per scene). Off: K and
the toolbar Knife are Blender's own (this operator's poll fails, the key falls through to
Blender's keymap item).
"""
from collections import defaultdict

import bmesh
import bpy
from bpy.props import BoolProperty

PROP = "multicamproject_heal_cut"
GAP = 3.0       # pieces join when their ends are closer than GAP x the longest edge there
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


def heal(bm, gap=GAP):
    """Close the gaps between the pieces of the selected cut and mark it as a seam.
    Returns (closed, left, edges): gaps closed, gaps still open (pieces - 1), seam edges."""
    cut = [e for e in bm.edges if e.select]
    if not cut:
        return 0, 0, 0
    ends, find, link = _pieces(cut)
    reach = {v: max(e.calc_length() for e in v.link_edges) for v in ends}
    pairs = []
    for i, a in enumerate(ends):
        for b in ends[i + 1:]:
            d = (a.co - b.co).length
            if d <= gap * max(reach[a], reach[b]) and find(a) is not find(b):
                pairs.append((d, i, a, b))
    pairs.sort(key=lambda p: (p[0], p[1]))
    used, closed = set(), 0
    for _d, _i, a, b in pairs:
        if a in used or b in used:
            continue
        if find(a) is find(b):
            continue
        new = _join(bm, a, b)
        if not new:
            continue
        for e in new:
            e.select_set(True)
            cut.append(e)
            link(e)
        used.update((a, b))
        closed += 1
    cut = [e for e in cut if e.is_valid]
    for e in cut:
        e.seam = True
    roots = {find(v) for e in cut for v in e.verts}
    return closed, len(roots) - 1, len(cut)


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
                self.report({'WARNING'}, f"{msg}, {left} still open (selected pieces)")
            else:
                self.report({'INFO'}, msg)
            return {'FINISHED', 'PASS_THROUGH'}
        if event.type in OVER:              # reached us: the Knife is gone, nothing cut
            self._stop(context)
            return {'CANCELLED', 'PASS_THROUGH'}
        return {'PASS_THROUGH'}


# ------------------------------------------------------------------------------------ header

def _draw_button(layout, context):
    from ... import addon_icons
    layout.prop(context.scene, PROP, text="", **addon_icons.kw("heal_cut", 'SCULPTMODE_HLT'))


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
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_HealKnife)
    del bpy.types.Scene.multicamproject_heal_cut
