"""uvTube: the selected edges become a tube with uv_normal, live until Enter / Esc.

Start (Edit Mode, edge select): the edges, split into chains at their ends and forks, become
a Poly curve object over the mesh (its matrix, its collection), with the MCP-uvTube Geometry
Nodes modifier: Catmull-Rom smoothing (Smooth), resampled to Segments per chain, swept by a
circle of Sides points and Radius, scaled by each point's own radius - Alt+S in the curve's
Edit Mode, as the Skin modifier's points do. The curve is what the user edits.

Enter (or Confirm): the evaluated tube becomes faces of the mesh, uv_normal laid out from the
attributes the nodes stored (u around, v along, caps planar), seams on the u = 0 line, the
v = 0 ring and around the caps; the source edges that had no faces go. Esc (or Cancel): the
curve goes, the mesh is as it was.

A file saved mid-session keeps the curve (TARGET_KEY on it): select it and Confirm / Cancel in
the uvTube panel."""
import math

import bmesh
import bpy

from ..camera_project.gn_builder import B, _iface

GROUP = "MCP-uvTube"
VERSION = 1
_VERSION_KEY = "multicamproject_uvtube_version"
TARGET_KEY = "multicamproject_uvtube_target"    # tube curve object: the mesh it goes into
MOD = "uvTube"
UV = "uv_normal"
INPUTS = ("Sides", "Segments", "Radius", "Smooth", "Caps")
_ATTR_U, _ATTR_V, _ATTR_LEN, _ATTR_ID = "uvt_u", "uvt_v", "uvt_len", "uvt_id"
_CURVE_ATTRS = ("radius", "tilt", "resolution", "cyclic", "curve_type", "normal_mode",
                "nurbs_weight", "nurbs_order", "knots_mode", "handle_left", "handle_right",
                "handle_type_left", "handle_type_right")


# ---------------------------------------------------------------- the node group

def _store(b, geo, name, value, loc):
    s = b.n("GeometryNodeStoreNamedAttribute", loc, data_type='FLOAT', domain='POINT')
    s.inputs["Name"].default_value = name
    b.link(geo, s.inputs["Geometry"])
    b.link(value, s.inputs["Value"])
    return s.outputs["Geometry"]


def ensure_group():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is not None and ng.get(_VERSION_KEY) == VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    else:                       # rebuilt in place: modifiers keep their reference
        ng.nodes.clear()
        ng.interface.clear()
    ng.is_modifier = True
    ng[_VERSION_KEY] = VERSION
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Sides", "INPUT", "NodeSocketInt", 8, 3, 64, single=True)
    _iface(ng, "Segments", "INPUT", "NodeSocketInt", 16, 1, 1000, single=True)
    _iface(ng, "Radius", "INPUT", "NodeSocketFloat", 0.02, 0.0, single=True)
    _iface(ng, "Smooth", "INPUT", "NodeSocketBool", True, single=True)
    _iface(ng, "Caps", "INPUT", "NodeSocketBool", False, single=True)
    b = B(ng)
    gi = b.n("NodeGroupInput", (-1000, 0))
    go = b.n("NodeGroupOutput", (900, 0))

    # the path: smoothed (Catmull-Rom through the points) or as drawn, then resampled
    st = b.n("GeometryNodeCurveSplineType", (-800, 120), spline_type='CATMULL_ROM')
    b.link(gi.outputs["Geometry"], st.inputs["Curve"])
    sw = b.n("GeometryNodeSwitch", (-600, 60), input_type='GEOMETRY')
    b.link(gi.outputs["Smooth"], sw.inputs["Switch"])
    b.link(gi.outputs["Geometry"], sw.inputs["False"])
    b.link(st.outputs["Curve"], sw.inputs["True"])
    count = b.n("GeometryNodeSwitch", (-600, -120), input_type='INT')
    b.link(b.n("GeometryNodeInputSplineCyclic", (-800, -120)).outputs["Cyclic"],
           count.inputs["Switch"])
    b.link(b.math('ADD', gi.outputs["Segments"], 1.0, (-800, -220)), count.inputs["False"])
    b.link(gi.outputs["Segments"], count.inputs["True"])
    rs = b.n("GeometryNodeResampleCurve", (-400, 60))
    rs.inputs["Mode"].default_value = 'Count'
    b.link(sw.outputs["Output"], rs.inputs["Curve"])
    b.link(count.outputs["Output"], rs.inputs["Count"])

    # what the uv_normal layout reads: v (length along), the chain's length and index
    par = b.n("GeometryNodeSplineParameter", (-200, -160))
    path = _store(b, rs.outputs["Curve"], _ATTR_V, par.outputs["Length"], (-200, 60))
    path = _store(b, path, _ATTR_LEN,
                  b.n("GeometryNodeSplineLength", (0, -160)).outputs["Length"], (0, 60))
    idx = b.n("GeometryNodeFieldOnDomain", (200, -160), domain='CURVE', data_type='FLOAT')
    b.link(b.n("GeometryNodeInputIndex", (0, -260)).outputs["Index"], idx.inputs[0])
    path = _store(b, path, _ATTR_ID, idx.outputs[0], (200, 60))

    # the profile: a circle, u = 0..1 around it
    circle = b.n("GeometryNodeCurvePrimitiveCircle", (-200, -400), mode='RADIUS')
    b.link(gi.outputs["Sides"], circle.inputs["Resolution"])
    b.link(gi.outputs["Radius"], circle.inputs["Radius"])
    prof = _store(b, circle.outputs["Curve"], _ATTR_U,
                  b.n("GeometryNodeSplineParameter", (0, -500)).outputs["Factor"], (200, -400))

    ctm = b.n("GeometryNodeCurveToMesh", (500, 0))
    b.link(path, ctm.inputs["Curve"])
    b.link(prof, ctm.inputs["Profile Curve"])
    b.link(b.n("GeometryNodeInputRadius", (300, -200)).outputs["Radius"], ctm.inputs["Scale"])
    b.link(gi.outputs["Caps"], ctm.inputs["Fill Caps"])
    b.link(ctm.outputs["Mesh"], go.inputs["Geometry"])
    return ng


# ---------------------------------------------------------------- modifier inputs

def modifier(obj):
    return obj.modifiers.get(MOD) if obj is not None else None


def _input(mod, name):
    item = mod.node_group.interface.items_tree.get(name) if mod.node_group else None
    return getattr(mod.properties.inputs, item.identifier) if item is not None else None


def get_input(mod, name):
    inp = _input(mod, name)
    return inp.value if inp is not None else None


def set_input(mod, name, value):
    inp = _input(mod, name)
    if inp is not None:
        inp.value = value


def draw_input(layout, mod, name):
    inp = _input(mod, name)
    if inp is not None:
        layout.prop(inp, "value", text=name)


# ---------------------------------------------------------------- the session

def session_object(context):
    """The active object when it is an open uvTube curve, else None."""
    obj = context.active_object
    return obj if obj is not None and obj.type == 'CURVE' and TARGET_KEY in obj else None


def _chains(edges):
    """`edges` as vertex chains [(verts, cyclic)]: split at their ends and forks."""
    link = {}
    for e in edges:
        for v in e.verts:
            link.setdefault(v, []).append(e)
    left, out = set(edges), []

    def walk(v, e):
        chain = [v]
        while True:
            left.discard(e)
            v = e.other_vert(v)
            chain.append(v)
            nxt = [g for g in link[v] if g in left]
            if len(link[v]) != 2 or not nxt:
                return chain
            e = nxt[0]

    for v, es in link.items():          # open chains: from every end / fork
        if len(es) != 2:
            for e in es:
                if e in left:
                    out.append((walk(v, e), False))
    while left:                         # what is left are loops
        e = next(iter(left))
        chain = walk(e.verts[0], e)
        out.append((chain[:-1], True))
    return out


def start(context, mesh_obj):
    """The selected edges of `mesh_obj` (Edit Mode) to a tube curve in Edit Mode. Returns the
    curve object, or raises ValueError."""
    bm = bmesh.from_edit_mesh(mesh_obj.data)
    edges = [e for e in bm.edges if e.select]
    if not edges:
        raise ValueError("Select edges first")
    chains = _chains(edges)
    mean = sum(e.calc_length() for e in edges) / len(edges)
    longest = max(len(c) - (0 if cyc else 1) for c, cyc in chains)

    cd = bpy.data.curves.new(f"uvTube_{mesh_obj.name}", 'CURVE')
    cd.dimensions = '3D'
    for verts, cyclic in chains:
        spl = cd.splines.new('POLY')
        spl.points.add(len(verts) - 1)
        for p, v in zip(spl.points, verts):
            p.co = (*v.co, 1.0)
            p.radius = 1.0
        spl.use_cyclic_u = cyclic
    tube = bpy.data.objects.new(cd.name, cd)
    tube.matrix_world = mesh_obj.matrix_world.copy()
    tube[TARGET_KEY] = mesh_obj.name
    (mesh_obj.users_collection or (context.scene.collection,))[0].objects.link(tube)
    mod = tube.modifiers.new(MOD, 'NODES')
    mod.node_group = ensure_group()
    set_input(mod, "Segments", max(4, longest * 2))
    set_input(mod, "Radius", max(0.001, mean * 0.15))

    bpy.ops.object.mode_set(mode='OBJECT')          # the mesh's edits written
    for o in context.view_layer.objects.selected:
        o.select_set(False)
    tube.select_set(True)
    context.view_layer.objects.active = tube
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.curve.select_all(action='SELECT')
    return tube


def _back_to(context, mesh_obj):
    """`mesh_obj` active and in Edit Mode again."""
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in context.view_layer.objects.selected:
        o.select_set(False)
    mesh_obj.select_set(True)
    context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode='EDIT')


def _remove(tube):
    cd = tube.data
    bpy.data.objects.remove(tube)
    if cd.users == 0:
        bpy.data.curves.remove(cd)


def cancel(context, tube):
    mesh_obj = bpy.data.objects.get(tube.get(TARGET_KEY, ""))
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    _remove(tube)
    if mesh_obj is not None:
        _back_to(context, mesh_obj)


def _signed_area(pts):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])) / 2


def _layout_uvs(bm, radius, cyclic_ids):
    """uv_normal from the stored attributes; seams marked. Returns the uv layer."""
    lu = bm.verts.layers.float.get(_ATTR_U)
    lv = bm.verts.layers.float.get(_ATTR_V)
    ll = bm.verts.layers.float.get(_ATTR_LEN)
    li = bm.verts.layers.float.get(_ATTR_ID)
    uv = bm.loops.layers.uv.get(UV) or bm.loops.layers.uv.new(UV)
    circ = 2 * math.pi * max(radius, 1e-6)
    gap = circ * 0.1

    def is_cap(f):
        vs = [v[lv] for v in f.verts]
        return max(vs) - min(vs) < 1e-7

    # per chain: [u, v] of every corner of its sleeve faces, cap faces apart
    sleeves, caps = {}, {}
    for f in bm.faces:
        k = int(round(f.verts[0][li]))
        if is_cap(f):
            caps.setdefault(k, []).append(f)
            continue
        us = [lp.vert[lu] for lp in f.loops]
        vs = [lp.vert[lv] for lp in f.loops]
        if max(us) - min(us) > 0.5:             # across the u = 0 line
            us = [u + 1 if u < 0.5 else u for u in us]
        length = f.verts[0][ll]
        if k in cyclic_ids and max(vs) - min(vs) > length / 2:     # across a loop's start
            vs = [v + length if v < length / 2 else v for v in vs]
        sleeves.setdefault(k, []).append((f, [[u * circ, v] for u, v in zip(us, vs)]))

    x = 0.0
    height = 0.0
    for k in sorted(set(sleeves) | set(caps)):
        faces = sleeves.get(k, [])
        if faces:       # mirrored against the faces' winding: u runs the other way
            f0, pts = faces[0]
            if _signed_area([tuple(p) for p in pts]) < 0:
                for _f, pts in faces:
                    for p in pts:
                        p[0] = circ - p[0]
            for f, pts in faces:
                for lp, (u, v) in zip(f.loops, pts):
                    lp[uv].uv = (x + u, v)
                height = max(height, max(p[1] for p in pts))
        x += circ + gap
        y = cap_w = 0.0
        for f in caps.get(k, []):       # planar, beside the sleeve
            f.normal_update()
            n = f.normal
            t = (f.verts[1].co - f.verts[0].co).normalized()
            bt = n.cross(t)
            c = f.calc_center_median()
            r = max(((v.co - c).length for v in f.verts), default=0.0)
            for lp in f.loops:
                d = lp.vert.co - c
                lp[uv].uv = (x + r + d.dot(t), y + r + d.dot(bt))
            y += 2 * r + gap
            height = max(height, y)
            cap_w = max(cap_w, 2 * r + gap)
        x += cap_w
    scale = 1.0 / max(x - gap, height, 1e-9)     # into 0..1, square texels kept
    for f in bm.faces:
        for lp in f.loops:
            lp[uv].uv = lp[uv].uv * scale

    for e in bm.edges:
        a, b = e.verts
        cap_side = sum(is_cap(f) for f in e.link_faces) == 1
        on_u0 = a[lu] == 0.0 and b[lu] == 0.0 and a[lv] != b[lv]
        on_v0 = a[lv] == 0.0 and b[lv] == 0.0
        e.seam = cap_side or on_u0 or on_v0
    for name in (_ATTR_U, _ATTR_V, _ATTR_LEN, _ATTR_ID):    # by name: a removal moves the rest
        bm.verts.layers.float.remove(bm.verts.layers.float[name])
    return uv


def confirm(context, tube):
    """The tube into its mesh as faces (uv_normal, seams), selected; the curve goes. Returns
    the number of faces added."""
    mesh_obj = bpy.data.objects.get(tube.get(TARGET_KEY, ""))
    if mesh_obj is None or mesh_obj.type != 'MESH':
        raise ValueError("The mesh this tube goes into is gone - Cancel it")
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    mod = modifier(tube)
    radius = get_input(mod, "Radius") if mod is not None else 0.02
    cyclic = {i for i, s in enumerate(tube.data.splines) if s.use_cyclic_u}
    dg = context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tube.evaluated_get(dg))
    try:
        bm = bmesh.new()
        bm.from_mesh(me)
        added = len(bm.faces)
        if added:
            _layout_uvs(bm, radius, cyclic)
            for f in bm.faces:
                f.select = True
                f.smooth = True
            bm.to_mesh(me)
        bm.free()
        for name in _CURVE_ATTRS:       # the curve's own attributes, carried over
            if name in me.attributes and name not in mesh_obj.data.attributes:
                me.attributes.remove(me.attributes[name])

        bm = bmesh.new()
        bm.from_mesh(mesh_obj.data)
        guides = [e for e in bm.edges if e.select and not e.link_faces]
        bmesh.ops.delete(bm, geom=guides, context='EDGES')     # wire source edges go
        for seq in (bm.verts, bm.edges, bm.faces):
            for el in seq:
                el.select = False
        if added:
            bm.from_mesh(me)        # appended (layers matched by name)
        bm.to_mesh(mesh_obj.data)
        bm.free()
    finally:
        bpy.data.meshes.remove(me)
    mesh_obj.data.update()
    _remove(tube)
    _back_to(context, mesh_obj)
    return added


# ---------------------------------------------------------------- operators

def _status(context, tube):
    mod = modifier(tube)
    if mod is None:
        return
    context.workspace.status_text_set(
        f"uvTube · Sides {get_input(mod, 'Sides')} (Ctrl+Wheel) · Segments "
        f"{get_input(mod, 'Segments')} (Shift+Wheel) · Alt+S radius per point · "
        "Enter confirm · Esc cancel")


class MULTICAMPROJECT_OT_uv_tube(bpy.types.Operator):
    """Turn the selected edges into a tube with uv_normal: edit its curve (Alt+S scales a
    point's radius), Ctrl+Wheel sides, Shift+Wheel segments, Enter confirms, Esc cancels"""
    bl_idname = "multicamproject.uv_tube"
    bl_label = "uvTube"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH' or not context.tool_settings.mesh_select_mode[1]:
            cls.poll_message_set("Edit Mode, edge select")
            return False
        if context.edit_object.data.total_edge_sel == 0:
            cls.poll_message_set("Select edges first")
            return False
        return True

    def invoke(self, context, event):
        try:
            tube = start(context, context.edit_object)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        bpy.ops.ed.undo_push(message="uvTube start")
        self._tube = tube.name
        _status(context, tube)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        tube = bpy.data.objects.get(self._tube)
        if tube is None or TARGET_KEY not in tube:      # confirmed / cancelled elsewhere
            self._end(context)
            return {'FINISHED'}
        if context.active_object is not tube or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and not (event.ctrl or event.alt):
            self._end(context)
            try:
                n = confirm(context, tube)
            except ValueError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
            bpy.ops.ed.undo_push(message="uvTube")
            self.report({'INFO'}, f"uvTube: {n} faces added")
            return {'FINISHED'}
        if event.type == 'ESC':
            self._end(context)
            cancel(context, tube)
            bpy.ops.ed.undo_push(message="uvTube cancel")
            self.report({'INFO'}, "uvTube cancelled")
            return {'CANCELLED'}
        step = {'WHEELUPMOUSE': 1, 'WHEELDOWNMOUSE': -1}.get(event.type)
        name = "Sides" if event.ctrl else "Segments" if event.shift else None
        mod = modifier(tube)
        if step and name and mod is not None:
            set_input(mod, name, get_input(mod, name) + step)
            tube.update_tag()
            _status(context, tube)
            for area in context.screen.areas:
                area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


class MULTICAMPROJECT_OT_uv_tube_finish(bpy.types.Operator):
    """Confirm the uvTube (its faces into the mesh) or cancel it (the curve goes)"""
    bl_idname = "multicamproject.uv_tube_finish"
    bl_label = "uvTube"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=(('CONFIRM', "Confirm", ""), ('CANCEL', "Cancel", "")),
                                   options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return session_object(context) is not None

    def execute(self, context):
        tube = session_object(context)
        if self.action == 'CANCEL':
            cancel(context, tube)
            return {'FINISHED'}
        try:
            n = confirm(context, tube)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"uvTube: {n} faces added")
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_uv_tube, MULTICAMPROJECT_OT_uv_tube_finish)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
