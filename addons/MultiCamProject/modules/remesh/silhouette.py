"""Silhouette Decimate (03): simplify the inside of a mesh, keep its outline - on a preview copy
next to the object, so you choose before anything changes.

Preview: the object's mesh copied into PREVIEW_<name>_SilDec (a plain new object - not
obj.copy(), which would carry the add-on's settings and get its own MCP_/MAT_), beside the
object, showing its MAT_. Its Decimate (Collapse, triangulated) uses vg_Silhouette, inverted,
like 2B's vg_Protect. Blender's collapse never touches a weighted vertex (any weight, any
factor - measured), so weighting the whole outline would freeze it: a cut plant is mostly
outline. vg_Silhouette is pins instead, like UV pins: along
  - the outline (open edges: leaf rims, cut borders)   - Keep Outline
  - uv_normal's seams                                  - Keep UV Seams (textures stay put)
only the vertices Douglas-Peucker needs to hold the line within Tolerance (mm) are pinned;
the rest of the line and the whole inside are free. Plus the object's vg_Protect (>= 0.5).
Fewer tris than the pins allow are not reachable: the count shows it.

Both pin toggles are off by default: measured on the cut plant (2026-11-09, 17.7K tris from
92.8K), Blender's quadric alone - it weighs open edges itself - kept the outline as well
(0.33 mm median / 1.7 mm 95%) and the texture better (0.6 px median shift at 8K vs 1.5 px with
outline pins); the pins only cap how far it can go.

Use: the decimated mesh replaces the object's (the old one kept as <mesh>_before_SilDec, fake
user); Discard: the preview goes. Measure: how far the outline moved (median / 95% / max)."""
import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty

KEY = "multicamproject_sildec_of"       # on the preview: the name of the object it previews
VG = "vg_Silhouette"
MOD = "Silhouette Decimate"
MEASURE = "multicamproject_sildec_measure"  # on the preview: [ratio, factor, p50, p95, max]
PINS = "multicamproject_sildec_pins"        # on the preview: how many vertices are pinned
BACKUP_SUFFIX = "_before_SilDec"
GAP = 1.3           # the preview sits this many object widths to the right


def preview_of(obj):
    if obj is None:
        return None
    return next((o for o in bpy.data.objects if o.get(KEY) == obj.name), None)


def _modifier(prev):
    mod = prev.modifiers.get(MOD) if prev is not None else None
    return mod if mod is not None and mod.type == 'DECIMATE' else None


def _tris(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def _budget(context):
    """The work window's triangle budget (Polycount Estimation), None without one."""
    try:
        from ..estimation import core as est
        b = est.window_budget(context)
    except Exception:
        return None
    return b[0] if b else None


# ---------------------------------------------------------------- weights

def _outline_verts(me):
    ne = len(me.edges)
    ev = np.empty(ne * 2, np.int64)
    me.edges.foreach_get("vertices", ev)
    le = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("edge_index", le)
    open_e = np.bincount(le, minlength=ne) == 1
    return np.unique(ev.reshape(-1, 2)[open_e])


def _feature_edges(bm, outline, seams, uvl):
    """Edges whose shape matters: open edges (outline) and uv seams (the UV differs across
    the edge)."""
    out = []
    for e in bm.edges:
        ls = e.link_loops
        if len(ls) == 1:
            if outline:
                out.append(e)
        elif seams and uvl is not None and len(ls) == 2:
            a, b = ls
            if a.vert == b.vert:            # flipped neighbour: same winding
                pairs = ((a, b), (a.link_loop_next, b.link_loop_next))
            else:
                pairs = ((a, b.link_loop_next), (a.link_loop_next, b))
            if any((p[uvl].uv - q[uvl].uv).length_squared > 1e-12 for p, q in pairs):
                out.append(e)
    return out


def _chains(edges):
    """The feature edges as vertex chains: split at ends and junctions; closed loops whole."""
    adj = {}
    for e in edges:
        a, b = e.verts
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    used = set()
    chains = []

    def walk(start, nxt):
        chain = [start, nxt]
        used.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while len(adj[cur]) == 2 and cur is not start:
            n = adj[cur][0] if adj[cur][1] is prev else adj[cur][1]
            k = frozenset((cur, n))
            if k in used:
                break
            used.add(k)
            chain.append(n)
            prev, cur = cur, n
        return chain

    for v, ns in adj.items():
        if len(ns) != 2:
            for n in ns:
                if frozenset((v, n)) not in used:
                    chains.append(walk(v, n))
    for v, ns in adj.items():         # what is left: closed loops
        for n in ns:
            if frozenset((v, n)) not in used:
                chains.append(walk(v, n))
    return chains


def _douglas_peucker(pts, tol):
    """Indices of pts (n, 3) Douglas-Peucker keeps at tolerance tol (both ends always). A
    closed chain (first == last point) is split at its farthest point first."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    if np.allclose(pts[0], pts[-1]):
        far = int(np.argmax(np.linalg.norm(pts - pts[0], axis=1)))
        keep[far] = True
        stack = [(0, far), (far, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        a, b = pts[i], pts[j]
        seg = pts[i + 1:j]
        ab = b - a
        L = np.dot(ab, ab)
        if L < 1e-20:
            d = np.linalg.norm(seg - a, axis=1)
        else:
            t = np.clip((seg - a) @ ab / L, 0, 1)
            d = np.linalg.norm(seg - (a + t[:, None] * ab), axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return list(np.nonzero(keep)[0])


def build_weights(prev, scene):
    """vg_Silhouette on the preview = the vertices the Decimate must not touch (it treats
    any weight as a pin): along the outline / uv seams only the points Douglas-Peucker needs
    to keep their shape within the tolerance, like pins in a UV unwrap; plus vg_Protect."""
    import bmesh
    me = prev.data
    pinned = set()
    outline, seams = scene.multicamproject_sildec_outline, scene.multicamproject_sildec_seams
    if outline or seams:
        bm = bmesh.new()
        bm.from_mesh(me)
        uvl = bm.loops.layers.uv.get("uv_normal") or bm.loops.layers.uv.active
        scale = max(prev.matrix_world.to_scale()) or 1.0
        tol = scene.multicamproject_sildec_tolerance / 1000.0 / scale
        for chain in _chains(_feature_edges(bm, outline, seams, uvl)):
            pts = np.array([v.co[:] for v in chain])
            pinned.update(chain[i].index for i in _douglas_peucker(pts, tol))
        bm.free()
    from . import workflow as wf
    prot = prev.vertex_groups.get(wf.VG_PROTECT)
    if prot is not None:
        gi = prot.index
        pinned.update(v.index for v in me.vertices
                      if any(g.group == gi and g.weight >= 0.5 for g in v.groups))
    old = prev.vertex_groups.get(VG)
    if old is not None:
        prev.vertex_groups.remove(old)
    vg = prev.vertex_groups.new(name=VG)
    if pinned:
        vg.add(sorted(pinned), 1.0, 'REPLACE')
    prev[PINS] = len(pinned)
    _clear_measure(prev)
    return len(pinned)


def _clear_measure(prev):
    if prev is not None and MEASURE in prev:
        del prev[MEASURE]


def _on_toggle(self, context):
    obj = context.active_object
    prev = preview_of(obj)
    if prev is not None:
        build_weights(prev, context.scene)


# ---------------------------------------------------------------- operators

def _source_ok(cls, context):
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.library or KEY in obj:
        return False
    if context.mode != 'OBJECT':
        cls.poll_message_set("Object Mode")
        return False
    return True


class MULTICAMPROJECT_OT_SilDecPreview(bpy.types.Operator):
    """Silhouette Decimate: a decimated copy beside the object (its outline and UV seams kept,
    the inside simplified) - set the amount there, then Use or Discard. The object itself does
    not change until Use"""
    bl_idname = "multicamproject.sildec_preview"
    bl_label = "Silhouette Decimate Preview"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _source_ok(cls, context) and preview_of(context.active_object) is None

    def execute(self, context):
        from mathutils import Matrix
        obj = context.active_object
        me = obj.data.copy()
        me.name = f"PREVIEW_{obj.name}_SilDec"
        prev = bpy.data.objects.new(me.name, me)
        for c in obj.users_collection:
            c.objects.link(prev)
        prev.matrix_world = Matrix.Translation((obj.dimensions.x * GAP, 0, 0)) @ obj.matrix_world
        prev[KEY] = obj.name
        # the baked look: MAT_ (slot 2) on every slot, linked to the object - the mesh keeps
        # its material indices for Use
        mats = [s.material for s in obj.material_slots]
        show = mats[1] if len(mats) > 1 and mats[1] else (mats[0] if mats else None)
        for s in prev.material_slots:
            s.link = 'OBJECT'
            s.material = show
        kept = build_weights(prev, context.scene)
        mod = prev.modifiers.new(MOD, 'DECIMATE')
        mod.decimate_type = 'COLLAPSE'
        mod.use_collapse_triangulate = True
        mod.vertex_group = VG
        mod.invert_vertex_group = True          # weight 1 = kept, like vg_Protect
        mod.vertex_group_factor = 1.0
        tris = _tris(obj.data)
        budget = _budget(context)
        mod.ratio = min(1.0, budget / tris) if budget and tris else 0.5
        if budget and tris > budget:
            # the ratio counts faces, not triangles: one correction from what it gave
            got = len(prev.evaluated_get(context.evaluated_depsgraph_get()).data.polygons)
            if got:
                mod.ratio = max(0.01, min(1.0, mod.ratio * budget / got * 0.98))
        self.report({'INFO'}, f"Preview beside '{obj.name}': {kept:,} vertices pinned, "
                              f"ratio {mod.ratio:.2f} - Use or Discard")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SilDecMeasure(bpy.types.Operator):
    """How far the preview's outline moved from the object's: median / 95% / largest, in mm"""
    bl_idname = "multicamproject.sildec_measure"
    bl_label = "Measure Outline"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _modifier(preview_of(context.active_object)) is not None

    def execute(self, context):
        import bmesh
        from mathutils import Vector
        from mathutils.kdtree import KDTree
        obj = context.active_object
        prev = preview_of(obj)
        mod = _modifier(prev)
        me = obj.data
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        orig = co.reshape(-1, 3)[_outline_verts(me)]
        if not len(orig):
            self.report({'INFO'}, "No outline (the mesh is closed)")
            return {'CANCELLED'}
        ev = prev.evaluated_get(context.evaluated_depsgraph_get())
        bm = bmesh.new()
        bm.from_mesh(ev.data)
        step = max(obj.dimensions) / 4000 or 1e-4
        pts = []
        for e in bm.edges:
            if e.is_boundary:
                a, b = e.verts[0].co, e.verts[1].co
                n = max(1, int((a - b).length / step))
                pts.extend(a.lerp(b, i / n) for i in range(n + 1))
        bm.free()
        if not pts:
            self.report({'WARNING'}, "The preview has no outline left")
            return {'CANCELLED'}
        kd = KDTree(len(pts))
        for i, p in enumerate(pts):
            kd.insert(p, i)
        kd.balance()
        scale = max(obj.matrix_world.to_scale())
        d = np.array([kd.find(Vector(p))[2] for p in orig]) * scale * 1000
        prev[MEASURE] = [mod.ratio, mod.vertex_group_factor, float(np.percentile(d, 50)),
                         float(np.percentile(d, 95)), float(d.max())]
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SilDecUse(bpy.types.Operator):
    """Use the preview: its decimated mesh replaces the object's (UVs, materials and vertex
    groups carried over). The old mesh stays in the file as <mesh>_before_SilDec; Ctrl+Z
    undoes it"""
    bl_idname = "multicamproject.sildec_use"
    bl_label = "Use Silhouette Decimate"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (_source_ok(cls, context)
                and _modifier(preview_of(context.active_object)) is not None)

    def execute(self, context):
        from . import workflow as wf
        obj = context.active_object
        prev = preview_of(obj)
        dg = context.evaluated_depsgraph_get()
        new = bpy.data.meshes.new_from_object(prev.evaluated_get(dg),
                                              preserve_all_data_layers=True, depsgraph=dg)
        new.materials.clear()
        for m in obj.data.materials:
            new.materials.append(m)
        old = obj.data
        name = old.name
        before = _tris(old)
        old.name = name + BACKUP_SUFFIX
        old.use_fake_user = True
        obj.data = new
        new.name = name
        vg = obj.vertex_groups.get(VG)
        if vg is not None:
            obj.vertex_groups.remove(vg)
        pm = prev.data
        bpy.data.objects.remove(prev, do_unlink=True)
        if pm.users == 0:
            bpy.data.meshes.remove(pm)
        wf.clear_custom_normals(obj)
        self.report({'INFO'}, f"'{obj.name}': {before:,} -> {_tris(new):,} tris "
                              f"(old mesh kept as '{old.name}')")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SilDecDiscard(bpy.types.Operator):
    """Delete the Silhouette Decimate preview - the object stays as it is"""
    bl_idname = "multicamproject.sildec_discard"
    bl_label = "Discard Preview"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return preview_of(context.active_object) is not None

    def execute(self, context):
        prev = preview_of(context.active_object)
        pm = prev.data
        bpy.data.objects.remove(prev, do_unlink=True)
        if pm.users == 0:
            bpy.data.meshes.remove(pm)
        return {'FINISHED'}


# ---------------------------------------------------------------- panel

def draw(layout, context, obj):
    """03's Silhouette Decimate box (Object Mode)."""
    scene = context.scene
    box = layout.box().column(align=True)
    prev = preview_of(obj)
    mod = _modifier(prev)
    row = box.row(align=True)
    row.prop(scene, "multicamproject_sildec_outline", text="", icon='MOD_OUTLINE')
    row.prop(scene, "multicamproject_sildec_seams", text="", icon='UV_EDGESEL')
    if mod is None:
        row.operator(MULTICAMPROJECT_OT_SilDecPreview.bl_idname,
                     text="Silhouette Decimate: Preview", icon='MOD_DECIM')
        return
    row.label(text="Silhouette Decimate", icon='MOD_DECIM')
    row.operator(MULTICAMPROJECT_OT_SilDecDiscard.bl_idname, text="", icon='X')
    row = box.row(align=True)
    row.prop(mod, "ratio", text="Ratio")
    row.prop(scene, "multicamproject_sildec_tolerance", text="Tolerance")
    try:
        tris = len(prev.evaluated_get(context.evaluated_depsgraph_get()).data.polygons)
    except Exception:
        tris = 0
    budget = _budget(context)
    row = box.row(align=True)
    over = budget is not None and tris > budget
    lab = row.row()
    lab.alert = over
    lab.label(text=f"{_tris(obj.data):,} -> {tris:,} tris"
                   + (f" / {budget // 1000}K" if budget else ""))
    row.operator(MULTICAMPROJECT_OT_SilDecMeasure.bl_idname, text="", icon='DRIVER_DISTANCE')
    row.operator(MULTICAMPROJECT_OT_SilDecUse.bl_idname, text="Use", icon='CHECKMARK')
    m = prev.get(MEASURE)
    hint = box.row()
    hint.active = False
    if m and abs(m[0] - mod.ratio) < 1e-6 and abs(m[1] - mod.vertex_group_factor) < 1e-6:
        hint.label(text=f"Outline moved {m[2]:.2f} / {m[3]:.2f} / {m[4]:.1f} mm "
                        f"(median / 95% / max)", icon='DRIVER_DISTANCE')
    else:
        hint.label(text=f"{prev.get(PINS, 0):,} pinned · the preview is beside it", icon='PINNED')


_CLASSES = (MULTICAMPROJECT_OT_SilDecPreview, MULTICAMPROJECT_OT_SilDecMeasure,
            MULTICAMPROJECT_OT_SilDecUse, MULTICAMPROJECT_OT_SilDecDiscard)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.multicamproject_sildec_outline = BoolProperty(
        name="Pin Outline", default=False, update=_on_toggle,
        description="Silhouette Decimate: pin the open edges (leaf rims, cut borders) within "
                    "the Tolerance - the silhouette stays, the inside is simplified")
    bpy.types.Scene.multicamproject_sildec_seams = BoolProperty(
        name="Pin UV Seams", default=False, update=_on_toggle,
        description="Silhouette Decimate: pin uv_normal's seams too (within the Tolerance) - "
                    "the baked textures stay in place along them")
    bpy.types.Scene.multicamproject_sildec_tolerance = FloatProperty(
        name="Outline Tolerance", default=1.0, min=0.01, soft_max=10.0, precision=2,
        update=_on_toggle,
        description="Silhouette Decimate: how far (mm) the outline and seams may move. "
                    "Smaller pins more points: closer outline, more tris")


def unregister():
    del bpy.types.Scene.multicamproject_sildec_tolerance
    del bpy.types.Scene.multicamproject_sildec_seams
    del bpy.types.Scene.multicamproject_sildec_outline
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
