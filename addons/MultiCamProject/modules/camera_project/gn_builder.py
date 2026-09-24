"""Geometry-node groups for camera projection (from docs/prototype_gn_builder.py).

Groups are shared between objects: they are built only when missing or outdated,
and rebuilt in place (never removed) so existing modifiers keep their reference.
"""
import bpy

SINGLE = "GN-CamProject_Single"
MIX = "MCP-MixResult"
MAIN = "GN-CameraProject"
VERSION = 4          # bump when the node layout changes
_VERSION_KEY = "multicamproject_version"


def _sock(sockets, name, enabled_only=True):
    for s in sockets:
        if s.name == name and (s.enabled or not enabled_only):
            return s
    raise KeyError(name)


class B:
    def __init__(self, tree):
        self.t = tree

    def n(self, typ, loc=(0, 0), **props):
        node = self.t.nodes.new(typ)
        for k, v in props.items():
            setattr(node, k, v)
        node.location = loc
        return node

    def link(self, a, b):
        self.t.links.new(a, b)

    def math(self, op, a, b=None, loc=(0, 0), label=None):
        m = self.n("ShaderNodeMath", loc, operation=op)
        if label:
            m.label = label
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                self.link(v, m.inputs[i])
        return m.outputs[0]

    def vmath(self, op, a, b=None, loc=(0, 0)):
        m = self.n("ShaderNodeVectorMath", loc, operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            self.link(v, m.inputs[i])
        return m

    def reroute(self, src, loc):
        r = self.n("NodeReroute", loc)
        self.link(src, r.inputs[0])
        return r.outputs[0]


def _iface(ng, name, io, typ, default=None, mn=None, mx=None, parent=None, dims=None):
    kw = {"name": name, "in_out": io, "socket_type": typ}
    if parent is not None:
        kw["parent"] = parent
    s = ng.interface.new_socket(**kw)
    if dims is not None:
        s.dimensions = dims
    if default is not None:
        s.default_value = default
    if mn is not None:
        s.min_value = mn
    if mx is not None:
        s.max_value = mx
    return s


def _fresh_group(name, is_modifier=False):
    """Return an empty group called `name`, reusing an existing datablock in place."""
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    ng.is_modifier = is_modifier
    ng[_VERSION_KEY] = VERSION
    return ng


def _up_to_date(name):
    ng = bpy.data.node_groups.get(name)
    return ng is not None and ng.get(_VERSION_KEY) == VERSION


def build_single():
    ng = _fresh_group(SINGLE)
    ng.description = "Project one camera: UV (corner field) + facing weight (face field)"
    _iface(ng, "UV", "OUTPUT", "NodeSocketVector")
    _iface(ng, "Weight", "OUTPUT", "NodeSocketFloat")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Occlusion", "INPUT", "NodeSocketBool", False)
    _iface(ng, "Camera", "INPUT", "NodeSocketObject")
    _iface(ng, "Focal Length", "INPUT", "NodeSocketFloat", 24.0, 0.001)
    _iface(ng, "Sensor Width", "INPUT", "NodeSocketFloat", 36.0, 0.001)
    _iface(ng, "Aspect", "INPUT", "NodeSocketFloat", 1.5, 0.001)

    b = B(ng)
    gi = b.n("NodeGroupInput", (-1400, 0))
    go = b.n("NodeGroupOutput", (1260, 45))

    oi = b.n("GeometryNodeObjectInfo", (-1150, 200), transform_space="RELATIVE")
    b.link(gi.outputs["Camera"], oi.inputs["Object"])
    inv = b.n("FunctionNodeInvertMatrix", (-950, 250))
    b.link(oi.outputs["Transform"], inv.inputs["Matrix"])
    pos = b.n("GeometryNodeInputPosition", (-1150, -50))
    tp = b.n("FunctionNodeTransformPoint", (-750, 250))
    b.link(pos.outputs[0], tp.inputs["Vector"])
    b.link(inv.outputs["Matrix"], tp.inputs["Transform"])
    sep = b.n("ShaderNodeSeparateXYZ", (-550, 250))
    b.link(tp.outputs[0], sep.inputs[0])

    # depth = -z ; scale = focal / sensor
    depth = b.math("MULTIPLY", sep.outputs["Z"], -1.0, (-350, 150), "Depth")
    scale = b.math("DIVIDE", gi.outputs["Focal Length"], gi.outputs["Sensor Width"], (-550, 50), "Focal/Sensor")
    ux = b.math("DIVIDE", sep.outputs["X"], depth, (-150, 350))
    u = b.math("MULTIPLY_ADD", ux, scale, (50, 350), "U")
    u.node.inputs[2].default_value = 0.5
    vy = b.math("DIVIDE", sep.outputs["Y"], depth, (-150, 200))
    vs = b.math("MULTIPLY", scale, gi.outputs["Aspect"], (-150, 50))
    v = b.math("MULTIPLY_ADD", vy, vs, (50, 200), "V")
    v.node.inputs[2].default_value = 0.5
    uv = b.n("ShaderNodeCombineXYZ", (250, 300))
    b.link(u, uv.inputs[0])
    b.link(v, uv.inputs[1])
    b.link(uv.outputs[0], go.inputs["UV"])

    # in-frame mask: 0<u<1, 0<v<1, depth>0
    m1 = b.math("GREATER_THAN", u, 0.0, (250, 150))
    m2 = b.math("LESS_THAN", u, 1.0, (250, 100))
    m3 = b.math("GREATER_THAN", v, 0.0, (250, 50))
    m4 = b.math("LESS_THAN", v, 1.0, (250, 0))
    m5 = b.math("GREATER_THAN", depth, 0.0, (250, -50))
    a = b.math("MULTIPLY", m1, m2, (420, 120))
    a = b.math("MULTIPLY", a, m3, (420, 70))
    a = b.math("MULTIPLY", a, m4, (420, 20))
    inframe = b.math("MULTIPLY", a, m5, (420, -30), "In Frame")

    # facing = max(0, dot(normal, normalize(camLoc - P)))
    nrm = b.n("GeometryNodeInputNormal", (-1150, -250))
    to_cam = b.vmath("SUBTRACT", oi.outputs["Location"], pos.outputs[0], (-750, -150))
    dist = b.vmath("LENGTH", to_cam.outputs[0], None, (-550, -300))
    dirn = b.vmath("NORMALIZE", to_cam.outputs[0], None, (-550, -150))
    dot = b.vmath("DOT_PRODUCT", nrm.outputs["Normal"], dirn.outputs[0], (-350, -200))
    facing = b.math("MAXIMUM", dot.outputs["Value"], 0.0, (-150, -200), "Facing")

    # occlusion: ray from face toward camera, offset along normal
    off = b.vmath("SCALE", nrm.outputs["Normal"], None, (-550, -450))
    off.inputs["Scale"].default_value = 0.001
    src = b.vmath("ADD", pos.outputs[0], off.outputs[0], (-350, -450))
    rc = b.n("GeometryNodeRaycast", (-150, -400))
    b.link(gi.outputs["Geometry"], rc.inputs["Target Geometry"])
    b.link(src.outputs[0], rc.inputs["Source Position"])
    b.link(dirn.outputs[0], rc.inputs["Ray Direction"])
    b.link(dist.outputs["Value"], rc.inputs["Ray Length"])
    hit = b.math("MULTIPLY", rc.outputs["Is Hit"], gi.outputs["Occlusion"], (50, -400))
    visible = b.math("SUBTRACT", 1.0, hit, (250, -400), "Visible")

    w = b.math("MULTIPLY", facing, inframe, (600, -150))
    w = b.math("MULTIPLY", w, visible, (600, -250))
    ev = b.n("GeometryNodeFieldOnDomain", (750, -200), domain="FACE", data_type="FLOAT")
    b.link(w, ev.inputs[0])
    b.link(ev.outputs[0], go.inputs["Weight"])
    return ng


def build_mix():
    """UV_cam1/2/3 (shifted) + VCMix (Sharp/Smooth/Combined, blended with the old
    VCMix) + material. Layout follows the hand-cleaned version of the group."""
    ng = _fresh_group(MIX)
    ng.description = "Store UV_cam1/2/3 and the VCMix weights, set the material"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Material", "INPUT", "NodeSocketMaterial")
    _iface(ng, "Mode", "INPUT", "NodeSocketMenu")
    _iface(ng, "Original Blend", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0).subtype = "FACTOR"
    for i in (1, 2, 3):
        _iface(ng, f"UV Shift {i}", "INPUT", "NodeSocketVector")
    for i in (1, 2, 3):
        _iface(ng, f"UV Cam{i}", "INPUT", "NodeSocketVector")
        _iface(ng, f"Weight Cam{i}", "INPUT", "NodeSocketFloat")

    b = B(ng)
    gi = b.n("NodeGroupInput", (-1908, 155))
    go = b.n("NodeGroupOutput", (1511, 0))

    # UV shift (photo moved by +shift -> sample at UV - shift)
    geo = gi.outputs["Geometry"]
    for i, (sub_loc, st_x) in enumerate((((-1299, 369), -1050), ((-1233, -58), -850),
                                         ((-1234, -211), -650)), 1):
        sh = b.vmath("SUBTRACT", gi.outputs[f"UV Cam{i}"], gi.outputs[f"UV Shift {i}"], sub_loc)
        sh.hide = True
        st = b.n("GeometryNodeStoreNamedAttribute", (st_x, 662), data_type="FLOAT2", domain="CORNER")
        st.inputs["Name"].default_value = f"UV_cam{i}"
        b.link(geo, st.inputs["Geometry"])
        b.link(sh.outputs[0], st.inputs["Value"])
        geo = st.outputs["Geometry"]

    R = b.reroute(gi.outputs["Weight Cam1"], (-969, 56))
    G = b.reroute(gi.outputs["Weight Cam2"], (-957, 1))
    Bw = b.reroute(gi.outputs["Weight Cam3"], (-949, -140))

    # ---- Sharp (argmax) ----
    def ge(a, c, loc):  # a >= c  ->  1 - (c > a)
        return b.math("SUBTRACT", 1.0, b.math("GREATER_THAN", c, a, loc), (loc[0] + 150, loc[1]))

    rs = b.math("MULTIPLY", ge(R, G, (-817, 359)), ge(R, Bw, (-817, 309)), (-467, 339))
    rs = b.math("MULTIPLY", rs, b.math("GREATER_THAN", R, 0.0, (-467, 279)), (-317, 319), "R sharp")
    gs = b.math("MULTIPLY", b.math("GREATER_THAN", G, R, (-817, 209)), ge(G, Bw, (-817, 159)), (-467, 189))
    gs = b.math("MULTIPLY", gs, b.math("GREATER_THAN", G, 0.0, (-467, 129)), (-317, 169), "G sharp")
    bs = b.math("MULTIPLY", b.math("GREATER_THAN", Bw, R, (-817, 59)),
                b.math("GREATER_THAN", Bw, G, (-817, 9)), (-467, 39))
    bs = b.math("MULTIPLY", bs, b.math("GREATER_THAN", Bw, 0.0, (-467, -21)), (-317, 19), "B sharp")
    sharp = b.n("FunctionNodeCombineColor", (-117, 259))
    sharp.label = "Sharp RGB"
    for s, sk in zip((rs, gs, bs), ("Red", "Green", "Blue")):
        b.link(s, sharp.inputs[sk])

    # ---- Smooth (normalised sum) ----
    tot = b.math("ADD", b.math("ADD", R, G, (-809, -245)), Bw, (-659, -245))
    inv = b.math("DIVIDE", 1.0, b.math("MAXIMUM", tot, 1e-6, (-509, -245)), (-359, -245))
    smooth = b.n("FunctionNodeCombineColor", (64, -43))
    smooth.label = "Smooth RGB"
    for s, sk, loc in zip((R, G, Bw), ("Red", "Green", "Blue"), ((-217, -91), (-209, -245), (-216, -404))):
        b.link(b.math("MULTIPLY", s, inv, loc), smooth.inputs[sk])

    # ---- Blend with original VCMix ----
    na = b.n("GeometryNodeInputNamedAttribute", (-641, -647), data_type="FLOAT_COLOR")
    na.inputs["Name"].default_value = "VCMix"
    fac = b.math("MULTIPLY", na.outputs["Exists"], gi.outputs["Original Blend"], (-450, -696), "Blend x Exists")
    fac = b.reroute(fac, (404, 219))
    old = b.reroute(na.outputs["Attribute"], (430, 136))
    geo = b.reroute(geo, (392, 390))

    def blended(col, loc):
        mx = b.n("ShaderNodeMix", loc, data_type="RGBA")
        b.link(fac, mx.inputs[0])
        b.link(col, _sock(mx.inputs, "A"))
        b.link(old, _sock(mx.inputs, "B"))
        return _sock(mx.outputs, "Result")

    ms = b.n("GeometryNodeMenuSwitch", (1064, 301), data_type="GEOMETRY")
    ms.enum_items.clear()
    for name in ("Sharp", "Smooth", "Combined"):
        ms.enum_items.new(name)
    cases = (("Sharp", sharp, "CORNER", 679), ("Smooth", smooth, "POINT", 429), ("Combined", sharp, "POINT", 179))
    for name, col, dom, y in cases:
        st = b.n("GeometryNodeStoreNamedAttribute", (780, y), data_type="FLOAT_COLOR", domain=dom)
        st.label = f"VCMix {name} ({dom.title()})"
        st.inputs["Name"].default_value = "VCMix"
        b.link(geo, st.inputs["Geometry"])
        b.link(blended(col.outputs[0], (530, y)), st.inputs["Value"])
        b.link(st.outputs["Geometry"], ms.inputs[name])
    b.link(gi.outputs["Mode"], ms.inputs["Menu"])

    sm = b.n("GeometryNodeSetMaterial", (1311, 11))
    b.link(ms.outputs["Output"], sm.inputs["Geometry"])
    b.link(gi.outputs["Material"], sm.inputs["Material"])
    b.link(sm.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def build_main(single, mix):
    ng = _fresh_group(MAIN, is_modifier=True)
    ng.description = "Project 3 cameras onto the mesh: UV_cam1/2/3 + VCMix weights + material"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    general = ng.interface.new_panel("General Settings")
    _iface(ng, "Material", "INPUT", "NodeSocketMaterial", parent=general)
    _iface(ng, "Mode", "INPUT", "NodeSocketMenu", parent=general)
    _iface(ng, "Original Blend", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0,
           parent=general).subtype = "FACTOR"
    _iface(ng, "Occlusion", "INPUT", "NodeSocketBool", False, parent=general)
    shift = ng.interface.new_panel("Camera Shift")
    for i in (1, 2, 3):
        _iface(ng, f"UV Shift Cam{i}", "INPUT", "NodeSocketVector", parent=shift, dims=2)
    cams = ng.interface.new_panel("Cameras")
    for i in (1, 2, 3):
        _iface(ng, f"Camera {i}", "INPUT", "NodeSocketObject", parent=cams)
    lens = ng.interface.new_panel("Lens (driven)", default_closed=True)
    for i in (1, 2, 3):
        _iface(ng, f"Focal {i}", "INPUT", "NodeSocketFloat", 24.0, 0.001, parent=lens)
        _iface(ng, f"Sensor {i}", "INPUT", "NodeSocketFloat", 36.0, 0.001, parent=lens)
        _iface(ng, f"Aspect {i}", "INPUT", "NodeSocketFloat", 1.5, 0.001, parent=lens)

    b = B(ng)
    gi = b.n("NodeGroupInput", (-440, -246))
    go = b.n("NodeGroupOutput", (2922, -21))
    geo = b.reroute(gi.outputs["Geometry"], (-257, -261))
    occ = b.reroute(gi.outputs["Occlusion"], (151, -541))

    mx = b.n("GeometryNodeGroup", (568, 94), node_tree=mix)
    b.link(geo, mx.inputs["Geometry"])
    for k in ("Material", "Mode", "Original Blend"):
        b.link(gi.outputs[k], mx.inputs[k])
    for i, loc in enumerate(((261, -200), (265, -444), (265, -676)), 1):
        g = b.n("GeometryNodeGroup", loc, node_tree=single)
        g.label = f"Cam {i}"
        b.link(geo, g.inputs["Geometry"])
        b.link(occ, g.inputs["Occlusion"])
        b.link(gi.outputs[f"Camera {i}"], g.inputs["Camera"])
        b.link(gi.outputs[f"Focal {i}"], g.inputs["Focal Length"])
        b.link(gi.outputs[f"Sensor {i}"], g.inputs["Sensor Width"])
        b.link(gi.outputs[f"Aspect {i}"], g.inputs["Aspect"])
        b.link(gi.outputs[f"UV Shift Cam{i}"], mx.inputs[f"UV Shift {i}"])
        b.link(g.outputs["UV"], mx.inputs[f"UV Cam{i}"])
        b.link(g.outputs["Weight"], mx.inputs[f"Weight Cam{i}"])
    b.link(mx.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def ensure_node_groups():
    """Build the groups if missing/outdated; return the modifier group."""
    single = bpy.data.node_groups.get(SINGLE)
    if not _up_to_date(SINGLE):
        single = build_single()
    mix = bpy.data.node_groups.get(MIX)
    if not _up_to_date(MIX):
        mix = build_mix()
    main = bpy.data.node_groups.get(MAIN)
    if not _up_to_date(MAIN):
        main = build_main(single, mix)
    return main
