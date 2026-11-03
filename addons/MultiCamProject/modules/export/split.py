"""Split parts: an export mesh that goes to Unity as several objects.

A part is an INT face attribute SPLIT_<name> (1 = the face belongs to it) - faces, not
vertex groups: a scan's triangles at a border have all 3 corners on the border, so a
vertex rule puts them in both parts. Only the export splits: the object in Blender stays
one mesh (bake, paint, seams), the FBX gets an empty (or the mesh of the faces in no
part) with one child per part, named <name>, its origin at its bounding box center, all
on the same MAT_. A face in two parts goes to the first part by name (a warning)."""
import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

PREFIX = "SPLIT_"


def part_names(me):
    """The mesh's SPLIT_ attributes, sorted (the order overlaps are given away in)."""
    return sorted(a.name for a in me.attributes
                  if a.name.startswith(PREFIX) and a.domain == 'FACE' and a.data_type == 'INT')


def label(name):
    return name[len(PREFIX):]


def masks(me):
    """{part: bool array over the faces} - Object Mode (the mesh data)."""
    out = {}
    n = len(me.polygons)
    for name in part_names(me):
        a = np.zeros(n, dtype=np.int32)
        me.attributes[name].data.foreach_get("value", a)
        out[name] = a != 0
    return out


def owners(names, m, n):
    """Each face's part index (-1: none); a face in two parts goes to the first."""
    own = np.full(n, -1, dtype=np.int32)
    for i, name in enumerate(names):
        own[(own == -1) & m[name]] = i
    return own


def stats(me):
    """{part: faces}, overlapping faces, faces in no part."""
    m = masks(me)
    if not m:
        return {}, 0, len(me.polygons)
    total = np.sum([v.astype(np.int32) for v in m.values()], axis=0)
    return ({k: int(v.sum()) for k, v in m.items()}, int((total > 1).sum()),
            int((total == 0).sum()))


def bm_stats(obj, bm):
    """stats() of an Edit Mode bmesh; kept for the panel (the mesh is stale in Edit Mode)."""
    lays = [(n, bm.faces.layers.int.get(n)) for n in part_names(obj.data)]
    lays = [(n, l) for n, l in lays if l is not None]
    for lay in bm.faces.layers.int.values():      # parts added in this Edit Mode session
        if lay.name.startswith(PREFIX) and all(lay.name != n for n, _l in lays):
            lays.append((lay.name, lay))
    counts = {n: 0 for n, _l in lays}
    over = none = 0
    for f in bm.faces:
        k = 0
        for n, lay in lays:
            if f[lay]:
                counts[n] += 1
                k += 1
        over += k > 1
        none += k == 0
    _edit_stats[obj.name] = (counts, over, none)
    return _edit_stats[obj.name]


_edit_stats = {}


def shown_stats(obj):
    if obj.mode == 'EDIT':
        st = _edit_stats.get(obj.name)
        if st is None and part_names(obj.data):     # once per Edit Mode session
            st = bm_stats(obj, bmesh.from_edit_mesh(obj.data))
        return st
    _edit_stats.pop(obj.name, None)
    return stats(obj.data)


# ---------------------------------------------------------------- export

def _drop_parts(me):
    for name in part_names(me):
        me.attributes.remove(me.attributes[name])


def _bbox_center(me):
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return Vector(((co.min(axis=0) + co.max(axis=0)) / 2).tolist())


def _keep_faces(me, keep):
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[f for f, k in zip(bm.faces, keep) if not k], context='FACES')
    bm.to_mesh(me)
    bm.free()


def pieces(me):
    """Split an export copy's mesh. Returns ([(part label, mesh, center)], rest): the
    parts' meshes moved to their bounding box center; `me` keeps the faces in no part
    (rest True) or is unused (rest False). No parts: ([], True), `me` untouched."""
    names = part_names(me)
    if not names:
        return [], True
    n = len(me.polygons)
    own = owners(names, masks(me), n)
    out = []
    for i, name in enumerate(names):
        keep = own == i
        if not keep.any():
            continue
        pme = me.copy()
        _keep_faces(pme, keep)
        _drop_parts(pme)
        c = _bbox_center(pme)
        pme.transform(Matrix.Translation(-c))
        out.append((label(name), pme, c))
    rest = bool((own == -1).any())
    if rest and out:
        _keep_faces(me, own == -1)
    _drop_parts(me)
    return out, rest


def clashes(scene, objs):
    """{object name: [part labels that come out as .001]} - a part named like another
    part of the export or like an object of the file."""
    seen = {}
    for o in objs:
        if o.type == 'MESH':
            for n in part_names(o.data):
                seen.setdefault(label(n), []).append(o.name)
    names = set(bpy.data.objects.keys())
    out = {}
    for lab, owners_ in seen.items():
        hit = owners_ if lab in names else owners_[1:]
        for o in hit:
            out.setdefault(o, []).append(lab)
    return out


# ---------------------------------------------------------------- receive

def carry_over(old, new):
    """Receive swaps the mesh: when the new one has no parts, each new face takes the
    parts of the nearest old face (face centers; a retopo or Decimate changed them).
    Both meshes in the object's space. Returns the parts carried."""
    names = part_names(old)
    if not names or part_names(new) or not len(new.polygons) or not len(old.polygons):
        return []
    from mathutils.kdtree import KDTree
    oc = np.empty(len(old.polygons) * 3, dtype=np.float32)
    old.polygons.foreach_get("center", oc)
    kd = KDTree(len(old.polygons))
    for i, co in enumerate(oc.reshape(-1, 3)):
        kd.insert(co, i)
    kd.balance()
    nc = np.empty(len(new.polygons) * 3, dtype=np.float32)
    new.polygons.foreach_get("center", nc)
    near = np.array([kd.find(co)[1] for co in nc.reshape(-1, 3)], dtype=np.int64)
    m = masks(old)
    for name in names:
        a = new.attributes.new(name, 'INT', 'FACE')
        a.data.foreach_set("value", m[name][near].astype(np.int32))
    return names


# ---------------------------------------------------------------- Edit Mode operators

def _poll_edit(context):
    obj = context.edit_object
    return obj is not None and obj.type == 'MESH'


def _edit(context):
    obj = context.edit_object
    return obj, bmesh.from_edit_mesh(obj.data)


def _done(obj, bm, select=False):
    if select:
        bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    bm_stats(obj, bm)


class MULTICAMPROJECT_OT_SplitAdd(bpy.types.Operator):
    """New split part SPLIT_<name> from the selected faces (Edit Mode). Export makes it a
    child object <name> of this object in the FBX"""
    bl_idname = "multicamproject.split_add"
    bl_label = "Add Split Part"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", description="The child object's name in Unity")

    @classmethod
    def poll(cls, context):
        return _poll_edit(context)

    def invoke(self, context, event):
        self.name = ""
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        lab = self.name.strip()
        if lab.startswith(PREFIX):
            lab = lab[len(PREFIX):]
        if not lab:
            self.report({'WARNING'}, "Type a name")
            return {'CANCELLED'}
        full = PREFIX + lab
        obj, bm = _edit(context)
        if obj.vertex_groups.get(full) is not None:
            self.report({'ERROR'}, f"A vertex group '{full}' exists - From Vertex Groups, or "
                                   "delete that group first")
            return {'CANCELLED'}
        lay = bm.faces.layers.int.get(full)
        had = lay is not None
        if lay is None:
            lay = bm.faces.layers.int.new(full)
        n = 0
        for f in bm.faces:
            if f.select:
                f[lay] = 1
                n += 1
        _done(obj, bm)
        self.report({'INFO'}, f"{full}: {n} selected faces added"
                    + (" (it existed)" if had else ""))
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SplitAssign(bpy.types.Operator):
    """Add the selected faces to this part (Remove: take them out)"""
    bl_idname = "multicamproject.split_assign"
    bl_label = "Assign to Split Part"
    bl_options = {'REGISTER', 'UNDO'}

    part: bpy.props.StringProperty()
    remove: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        return _poll_edit(context)

    @classmethod
    def description(cls, context, props):
        return ("Take the selected faces out of this part" if props.remove
                else "Add the selected faces to this part")

    def execute(self, context):
        obj, bm = _edit(context)
        lay = bm.faces.layers.int.get(self.part)
        if lay is None:
            return {'CANCELLED'}
        v = 0 if self.remove else 1
        for f in bm.faces:
            if f.select:
                f[lay] = v
        _done(obj, bm)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SplitSelect(bpy.types.Operator):
    """Select this part's faces (Deselect: deselect them)"""
    bl_idname = "multicamproject.split_select"
    bl_label = "Select Split Part"
    bl_options = {'REGISTER', 'UNDO'}

    part: bpy.props.StringProperty()
    deselect: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        return _poll_edit(context)

    @classmethod
    def description(cls, context, props):
        return "Deselect this part's faces" if props.deselect else "Select this part's faces"

    def execute(self, context):
        obj, bm = _edit(context)
        lay = bm.faces.layers.int.get(self.part)
        if lay is None:
            return {'CANCELLED'}
        for f in bm.faces:
            if f[lay]:
                f.select_set(not self.deselect)
        _done(obj, bm, select=True)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SplitDelete(bpy.types.Operator):
    """Delete this split part (the faces stay, they belong to no part then)"""
    bl_idname = "multicamproject.split_delete"
    bl_label = "Delete Split Part"
    bl_options = {'REGISTER', 'UNDO'}

    part: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return _poll_edit(context)

    def execute(self, context):
        obj, bm = _edit(context)
        lay = bm.faces.layers.int.get(self.part)
        if lay is not None:
            bm.faces.layers.int.remove(lay)
        _done(obj, bm)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SplitSelectOverlaps(bpy.types.Operator):
    """Select the faces in two or more split parts (export gives them to the first part
    by name)"""
    bl_idname = "multicamproject.split_select_overlaps"
    bl_label = "Select Overlaps"
    bl_options = {'REGISTER', 'UNDO'}

    unassigned: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        return _poll_edit(context)

    @classmethod
    def description(cls, context, props):
        return ("Select the faces in no split part (they stay on this object in the FBX)"
                if props.unassigned else cls.__doc__)

    def execute(self, context):
        obj, bm = _edit(context)
        lays = [l for l in bm.faces.layers.int.values() if l.name.startswith(PREFIX)]
        pairs = {}
        n = 0
        for f in bm.faces:
            ins = [l.name for l in lays if f[l]]
            hit = not ins if self.unassigned else len(ins) > 1
            f.select_set(hit)
            if hit:
                n += 1
                if ins:
                    key = " + ".join(label(s) for s in ins)
                    pairs[key] = pairs.get(key, 0) + 1
        _done(obj, bm, select=True)
        if self.unassigned:
            self.report({'INFO'}, f"{n} faces in no part")
        elif n:
            self.report({'WARNING'}, f"{n} overlapping faces: " + ", ".join(
                f"{k} ({v})" for k, v in sorted(pairs.items())))
        else:
            self.report({'INFO'}, "No overlapping faces")
        return {'FINISHED'}


def _resolve(me, inside):
    """Faces in two parts by the vertex rule: the part most of their edge neighbors are
    in alone; a few passes, so a strip of border triangles is decided from both sides.
    Ties stay in both (Select Overlaps)."""
    names = list(inside)
    k = np.sum([inside[n].astype(np.int32) for n in names], axis=0)
    amb = np.nonzero(k > 1)[0]
    if not len(amb):
        return 0
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    own = np.full(len(me.polygons), -1, dtype=np.int32)
    for i, n in enumerate(names):
        own[(k == 1) & inside[n]] = i
    for _pass in range(4):
        changed = False
        for fi in amb:
            if own[fi] != -1:
                continue
            votes = {}
            for e in bm.faces[fi].edges:
                for g in e.link_faces:
                    o = own[g.index]
                    if g.index != fi and o != -1 and inside[names[o]][fi]:
                        votes[o] = votes.get(o, 0) + 1
            if votes:
                best = sorted(votes.items(), key=lambda kv: -kv[1])
                if len(best) == 1 or best[0][1] > best[1][1]:
                    own[fi] = best[0][0]
                    changed = True
        if not changed:
            break
    bm.free()
    left = 0
    for fi in amb:
        if own[fi] == -1:
            left += 1
            continue
        for i, n in enumerate(names):
            inside[n][fi] = i == own[fi]
    return left


class MULTICAMPROJECT_OT_SplitFromVertexGroups(bpy.types.Operator):
    """Turn the SPLIT_ vertex groups into split parts: a face whose corners are all in a
    group; border faces in two groups go to the part around them. The vertex groups are
    deleted (an attribute and a vertex group cannot share a name)"""
    bl_idname = "multicamproject.split_from_vgroups"
    bl_label = "From Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.mode in {'OBJECT', 'EDIT_MESH'}
                and any(g.name.startswith(PREFIX) for g in obj.vertex_groups))

    def execute(self, context):
        obj = context.active_object
        was_edit = context.mode == 'EDIT_MESH'
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
        me = obj.data
        groups = [g for g in obj.vertex_groups if g.name.startswith(PREFIX)]
        nv = len(me.vertices)
        W = np.zeros((len(obj.vertex_groups), nv), dtype=np.float32)
        for v in me.vertices:
            for g in v.groups:
                W[g.group, v.index] = g.weight
        lv = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", lv)
        ls = np.empty(len(me.polygons), dtype=np.int32)
        me.polygons.foreach_get("loop_start", ls)
        inside = {}
        for g in groups:
            if g.name in me.attributes:
                continue
            inside[g.name] = np.minimum.reduceat(W[g.index][lv], ls) > 0.5
        left = _resolve(me, inside)
        for g in groups:
            if g.name in inside:
                obj.vertex_groups.remove(g)
        for name, m in inside.items():
            a = me.attributes.new(name, 'INT', 'FACE')
            a.data.foreach_set("value", m.astype(np.int32))
        me.update()
        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')
            bm_stats(obj, bmesh.from_edit_mesh(me))
        msg = ", ".join(f"{label(n)} {int(m.sum())}" for n, m in inside.items())
        if left:
            self.report({'WARNING'}, f"Parts: {msg} faces - {left} border faces still in two "
                                     "parts (Select Overlaps)")
        else:
            self.report({'INFO'}, f"Parts: {msg} faces")
        return {'FINISHED'}


# ---------------------------------------------------------------- panel

class MULTICAMPROJECT_PT_SplitParts(bpy.types.Panel):
    """Object Data, under Vertex Groups"""
    bl_label = "Split Parts (Export)"
    bl_idname = "MULTICAMPROJECT_PT_split_parts"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        obj = context.object
        edit = obj.mode == 'EDIT' and context.edit_object == obj
        st = shown_stats(obj)
        names = part_names(obj.data)
        if edit and st is not None:      # parts added in this Edit Mode session
            names = sorted(st[0])
        col = layout.column(align=True)
        if not names:
            col.label(text="No parts - the FBX gets this object whole")
        for name in names:
            row = col.row(align=True)
            n = st[0].get(name) if st is not None else None
            row.label(text=label(name), icon='MESH_DATA')
            row.label(text="" if n is None else f"{n} faces")
            sub = row.row(align=True)
            sub.enabled = edit
            op = sub.operator("multicamproject.split_assign", text="", icon='ADD')
            op.part, op.remove = name, False
            op = sub.operator("multicamproject.split_assign", text="", icon='REMOVE')
            op.part, op.remove = name, True
            op = sub.operator("multicamproject.split_select", text="", icon='RESTRICT_SELECT_OFF')
            op.part, op.deselect = name, False
            op = sub.operator("multicamproject.split_select", text="", icon='RESTRICT_SELECT_ON')
            op.part, op.deselect = name, True
            sub.operator("multicamproject.split_delete", text="", icon='X').part = name
        if st is not None and names:
            _counts, over, none = st
            box = layout.column(align=True)
            if over:
                box.label(text=f"{over} faces in two parts - export gives them to the first",
                          icon='ERROR')
            box.label(text=(f"{none} faces stay on this object" if none
                            else "Every face in a part - this object becomes an empty"),
                      icon='INFO')
        row = layout.row(align=True)
        row.enabled = edit
        row.operator("multicamproject.split_add", icon='ADD')
        row = layout.row(align=True)
        row.enabled = edit
        row.operator("multicamproject.split_select_overlaps", icon='ERROR').unassigned = False
        row.operator("multicamproject.split_select_overlaps", text="Select Unassigned",
                     icon='SELECT_SET').unassigned = True
        if any(g.name.startswith(PREFIX) for g in obj.vertex_groups):
            layout.operator("multicamproject.split_from_vgroups", icon='GROUP_VERTEX')
        if not edit:
            layout.label(text="Edit Mode: select faces, then Add / + / -")


_classes = (MULTICAMPROJECT_OT_SplitAdd, MULTICAMPROJECT_OT_SplitAssign,
            MULTICAMPROJECT_OT_SplitSelect, MULTICAMPROJECT_OT_SplitDelete,
            MULTICAMPROJECT_OT_SplitSelectOverlaps, MULTICAMPROJECT_OT_SplitFromVertexGroups,
            MULTICAMPROJECT_PT_SplitParts)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
