"""Selecting by seams when the seams have gaps.

L (Select Linked, Edit Mode) is Blender's own pick with one more option in its redo panel:
    Approximate Gaps   the island is picked; open seam ends inside what got selected (the
                       island and whatever it leaked into) are joined to each other or to the
                       seam beside them, closer than Max Gap; then the island is picked again.
                       The joins are real edges marked as seam (undoable), so the island
                       stays closed afterwards.
The pick is Blender's (select_linked_pick with the element under the mouse), so the redo
panel works: the element is found once with a click select (its index kept), then picked by
index. Off: exactly Blender's L.

Select Seam Network (Select menu / right-click): from the selected vertices or edges that sit
on a seam, every seam joined to them is selected. With Close Gaps, the open ends of that
network are joined (to each other or to any seam closer than Max Gap) and the spreading goes
on through what they now reach, until nothing more closes.
"""
from collections import deque

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty

from . import knife_heal as kh

MAX_GAP = 0.05      # m: default reach of Approximate Gaps / Close Gaps
ROUNDS = 20         # Seam Network: spread + close at most this often
DELIMIT = (('NORMAL', "Normal", "Delimit by face directions"),
           ('MATERIAL', "Material", "Delimit by face material"),
           ('SEAM', "Seam", "Delimit by edge seams"),
           ('SHARP', "Sharp", "Delimit by sharp edges"),
           ('UV', "UVs", "Delimit by UV coordinates"))


# ------------------------------------------------------------------------------------ helpers

def _seams(bm):
    """(seam edges, {vert: seam edge count})."""
    seams, deg = [], {}
    for e in bm.edges:
        if e.seam:
            seams.append(e)
            for v in e.verts:
                deg[v] = deg.get(v, 0) + 1
    return seams, deg


def close_ends(bm, ends, limit):
    """Join these open seam ends to each other or to any seam closer than `limit`; the new
    edges become seams. Returns gaps closed."""
    if not ends:
        return 0
    seams, deg = _seams(bm)
    _e, find, _l = kh._pieces(seams)
    size = kh._sizes(seams, find)
    ends = [v for v in ends if v.is_valid and deg.get(v) == 1 and not v.is_boundary]
    targets = set(deg).difference(ends)
    new, closed = kh._close(bm, ends, find, size, targets, limit=limit)
    for e in new:
        if e.is_valid:
            e.seam = True
    return closed


def _encode(bm, ele):
    """select_linked_pick's index of an element: verts, then edges, then faces."""
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    if isinstance(ele, bmesh.types.BMVert):
        return ele.index
    if isinstance(ele, bmesh.types.BMEdge):
        return len(bm.verts) + ele.index
    return len(bm.verts) + len(bm.edges) + ele.index


def _decode(bm, index):
    nv, ne = len(bm.verts), len(bm.edges)
    if index < nv:
        bm.verts.ensure_lookup_table()
        return bm.verts[index]
    if index < nv + ne:
        bm.edges.ensure_lookup_table()
        return bm.edges[index - nv]
    if index < nv + ne + len(bm.faces):
        bm.faces.ensure_lookup_table()
        return bm.faces[index - nv - ne]
    return None


def _ele_verts(ele):
    return [ele] if isinstance(ele, bmesh.types.BMVert) else list(ele.verts)


# ---------------------------------------------------------------------------------- Select Linked

class MULTICAMPROJECT_OT_SelectLinkedPick(bpy.types.Operator):
    """Select the island under the mouse (Blender's L). Approximate Gaps: seam gaps up to
Max Gap that let the island leak are closed first (new seam edges), then it is picked"""
    bl_idname = "multicamproject.select_linked_pick"
    bl_label = "Select Linked"
    bl_options = {'REGISTER', 'UNDO'}

    deselect: BoolProperty(name="Deselect", default=False)
    delimit: EnumProperty(name="Delimit", items=DELIMIT, options={'ENUM_FLAG'},
                          default={'SEAM'}, description="Delimit selected region")
    approximate: BoolProperty(
        name="Approximate Gaps", default=False,
        description="Close the seam gaps (up to Max Gap) the island leaks through, then "
                    "select it: the joins become real seam edges")
    max_gap: FloatProperty(name="Max Gap", default=MAX_GAP, min=0.001, soft_max=0.5,
                           subtype='DISTANCE', precision=3,
                           description="Seam ends closer than this are joined")
    object_index: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    index: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        lay.prop(self, "deselect")
        lay.prop(self, "delimit")
        lay.prop(self, "approximate")
        row = lay.row()
        row.active = self.approximate and 'SEAM' in self.delimit and not self.deselect
        row.prop(self, "max_gap")

    def invoke(self, context, event):
        # the element under the mouse, the way a click finds it (added to the selection: the
        # island picked next contains it anyway); its index makes the redo panel work
        before = {}
        for ob in context.objects_in_mode_unique_data:
            hist = bmesh.from_edit_mesh(ob.data).select_history
            before[ob.name] = (set(hist), hist.active)
        r = bpy.ops.view3d.select('INVOKE_DEFAULT', extend=True, deselect_all=False)
        if 'FINISHED' not in r:
            return {'CANCELLED'}
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        ele = bm.select_history.active
        if ele is None:
            return {'CANCELLED'}
        had, _active = before.get(obj.name, (set(), None))
        if ele not in had:
            bm.select_history.remove(ele)       # L does not change the active element
        self.index = _encode(bm, ele)
        objs = list(context.objects_in_mode)
        self.object_index = objs.index(obj) if obj in objs else 0
        return self.execute(context)

    def _pick(self, deselect):
        return bpy.ops.mesh.select_linked_pick('EXEC_DEFAULT', deselect=deselect,
                                               delimit=self.delimit,
                                               object_index=self.object_index, index=self.index)

    def execute(self, context):
        if self.index < 0:
            return {'CANCELLED'}
        r = self._pick(self.deselect)
        if 'FINISHED' not in r or not self.approximate or self.deselect \
                or 'SEAM' not in self.delimit:
            return r
        objs = list(context.objects_in_mode)
        if not 0 <= self.object_index < len(objs):
            return r
        obj = objs[self.object_index]
        bm = bmesh.from_edit_mesh(obj.data)
        ele = _decode(bm, self.index)
        _seams_, deg = _seams(bm)
        ends = [v for v, n in deg.items() if n == 1 and v.select and not v.is_boundary]
        if not ends:
            self.report({'INFO'}, "Select Linked: no open seam ends in the island")
            return r
        self._pick(True)                    # the leaked selection off again
        closed = close_ends(bm, ends, self.max_gap)
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        if ele is not None and ele.is_valid:
            self.index = _encode(bm, ele)   # new edges may have moved the indices
        self._pick(False)
        left = sum(1 for v in ends if v.is_valid and kh._seam_degree(v) == 1)
        msg = f"Select Linked: closed {closed} seam gap{'s' * (closed != 1)}"
        if left:
            self.report({'WARNING'}, f"{msg}; {left} open end{'s' * (left != 1)} too far "
                                     "(raise Max Gap, or Find Gaps)")
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


# --------------------------------------------------------------------------------- Seam Network

def seam_network(bm, seeds):
    """Seam edges reached from the seed vertices along seams."""
    seen, out = set(seeds), set()
    q = deque(seeds)
    while q:
        v = q.popleft()
        for e in v.link_edges:
            if e.seam and e not in out:
                out.add(e)
                w = e.other_vert(v)
                if w not in seen:
                    seen.add(w)
                    q.append(w)
    return out


class MULTICAMPROJECT_OT_SelectSeamNetwork(bpy.types.Operator):
    """From the selected vertices/edges on a seam: select every seam joined to them.
Close Gaps: open ends of those seams are joined to each other or to any seam closer than
Max Gap, and the spreading goes on through what they reach"""
    bl_idname = "multicamproject.select_seam_network"
    bl_label = "Select Seam Network"
    bl_options = {'REGISTER', 'UNDO'}

    close_gaps: BoolProperty(name="Close Gaps", default=True,
                             description="Join open ends of the network (new seam edges)")
    max_gap: FloatProperty(name="Max Gap", default=MAX_GAP, min=0.001, soft_max=0.5,
                           subtype='DISTANCE', precision=3,
                           description="Seam ends closer than this are joined")

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        total = closed = left = 0
        found = []
        for obj in context.objects_in_mode_unique_data:
            bm = bmesh.from_edit_mesh(obj.data)
            seeds = [v for v in bm.verts
                     if v.select and any(e.seam for e in v.link_edges)]
            if seeds:
                found.append((obj, bm, seeds))
        if not found:
            self.report({'WARNING'}, "Select a vertex or edge on a seam first")
            return {'CANCELLED'}
        bpy.ops.mesh.select_all(action='DESELECT')     # all objects in Edit Mode, in C
        for obj, bm, seeds in found:
            net = seam_network(bm, seeds)
            for _round in range(ROUNDS if self.close_gaps else 0):
                verts = {v for e in net for v in e.verts}
                ends = [v for v in verts if not v.is_boundary and kh._seam_degree(v) == 1]
                n = close_ends(bm, ends, self.max_gap)
                if not n:
                    break
                closed += n
                net = seam_network(bm, seeds)
            verts = {v for e in net for v in e.verts}
            left += sum(1 for v in verts if not v.is_boundary and kh._seam_degree(v) == 1)
            for e in net:
                e.select_set(True)
            bmesh.update_edit_mesh(obj.data, loop_triangles=bool(closed), destructive=bool(closed))
            total += len(net)
        msg = f"Seam Network: {total} seam edges"
        if self.close_gaps:
            msg += f", closed {closed} gap{'s' * (closed != 1)}"
        if left:
            self.report({'WARNING'}, f"{msg}; {left} open end{'s' * (left != 1)} left "
                                     "(raise Max Gap)")
        else:
            self.report({'INFO'}, msg + ", no open ends")
        return {'FINISHED'}


# --------------------------------------------------------------------------------- registration

def _menu(self, context):
    self.layout.separator()
    self.layout.operator(MULTICAMPROJECT_OT_SelectSeamNetwork.bl_idname, icon='EDGESEL')


_CLASSES = (MULTICAMPROJECT_OT_SelectLinkedPick, MULTICAMPROJECT_OT_SelectSeamNetwork)
_keymaps = []


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_select_edit_mesh.append(_menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new(MULTICAMPROJECT_OT_SelectLinkedPick.bl_idname,
                                                 'L', 'PRESS', head=True)))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(_menu)
    bpy.types.VIEW3D_MT_select_edit_mesh.remove(_menu)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
