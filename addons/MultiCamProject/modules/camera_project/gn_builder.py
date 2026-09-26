"""Geometry-node groups for camera projection (from docs/prototype_gn_builder.py).

Groups are shared between objects: they are built only when missing or outdated,
and rebuilt in place (never removed) so existing modifiers keep their reference.
"""
import bpy

SINGLE = "GN-CamProject_Single"
MIX = "MCP-MixResult"
MATINDEX = "MCP-MatIndex_to_uv"   # legacy (Convert Material button) - removed by setup
MAIN = "GN-CameraProject"
VERSION = 12         # bump when the node layout changes
SLOT_COUNTS = (3, 4, 5, 6)
LAYERS = ("VCMix", "VCMix2")        # cameras 1-3 / 4-6 in R, G, B


def mix_name(n):
    """One group pair per slot count; 3 slots keep the original names."""
    return MIX if n == 3 else f"{MIX}_{n}"


def main_name(n):
    return MAIN if n == 3 else f"{MAIN}_{n}"


def is_main(ng):
    """`ng` is a main group, or a modifier's wrapper around one."""
    from . import wrapper
    ng = wrapper.shared(ng)
    return ng is not None and ng.name in {main_name(n) for n in SLOT_COUNTS}
_VERSION_KEY = "multicamproject_version"


def _sock(sockets, name, enabled_only=True):
    for s in sockets:
        if s.name == name and (s.enabled or not enabled_only):
            return s
    raise KeyError(name)


class B:
    def __init__(self, tree):
        self.t = tree

    def n(self, typ, loc=(0, 0), parent=None, **props):
        """`loc` is in frame space when `parent` (a frame) is given."""
        node = self.t.nodes.new(typ)
        for k, v in props.items():
            setattr(node, k, v)
        if parent is not None:
            node.parent = parent
        node.location = loc
        return node

    def frame(self, label, loc):
        return self.n("NodeFrame", loc, label=label, label_size=20, shrink=True)

    def link(self, a, b):
        self.t.links.new(a, b)

    def math(self, op, a, b=None, loc=(0, 0), label=None, parent=None):
        m = self.n("ShaderNodeMath", loc, parent, operation=op)
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

    def vmath(self, op, a, b=None, loc=(0, 0), parent=None):
        m = self.n("ShaderNodeVectorMath", loc, parent, operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            self.link(v, m.inputs[i])
        return m

    def reroute(self, src, loc):
        r = self.n("NodeReroute", loc)
        self.link(src, r.inputs[0])
        return r.outputs[0]


def _iface(ng, name, io, typ, default=None, mn=None, mx=None, parent=None, dims=None,
           single=False):
    kw = {"name": name, "in_out": io, "socket_type": typ}
    if parent is not None:
        kw["parent"] = parent
    s = ng.interface.new_socket(**kw)
    if dims is not None:
        s.dimensions = dims
    if single:                  # a plain value, never a field/grid
        s.structure_type = 'SINGLE'
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
    _iface(ng, "Ortho", "INPUT", "NodeSocketFloat", 0.0)
    _iface(ng, "Ortho Scale", "INPUT", "NodeSocketFloat", 6.0, 0.001)

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

    # width of the frame at the point: perspective depth * sensor / focal, orthographic
    # Ortho Scale (sensor fit horizontal). u = x / width + 0.5, v = y / width * aspect + 0.5
    depth = b.math("MULTIPLY", sep.outputs["Z"], -1.0, (-350, 150), "Depth")
    pw = b.math("MULTIPLY", depth, gi.outputs["Sensor Width"], (-550, 50))
    pw = b.math("DIVIDE", pw, gi.outputs["Focal Length"], (-350, 50), "Persp Width")
    ortho = b.math("COMPARE", gi.outputs["Ortho"], 1.0, (-550, -50), "Is Ortho")
    ortho.node.inputs[2].default_value = 0.1        # camera type: 0 persp, 1 ortho, 2 pano
    width = b.n("ShaderNodeMix", (-150, 50), data_type="FLOAT", clamp_factor=True)
    width.label = "Frame Width"
    b.link(ortho, _sock(width.inputs, "Factor"))
    b.link(pw, _sock(width.inputs, "A"))
    b.link(gi.outputs["Ortho Scale"], _sock(width.inputs, "B"))
    width = _sock(width.outputs, "Result")
    ux = b.math("DIVIDE", sep.outputs["X"], width, (50, 400))
    u = b.math("ADD", ux, 0.5, (200, 400), "U")
    vy = b.math("DIVIDE", sep.outputs["Y"], width, (50, 250))
    v = b.math("MULTIPLY_ADD", vy, gi.outputs["Aspect"], (200, 250), "V")
    v.node.inputs[2].default_value = 0.5
    uv = b.n("ShaderNodeCombineXYZ", (400, 350))
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

    # facing = max(0, dot(normal, direction to the camera)): perspective toward the camera
    # location, orthographic the camera's back axis (the same for every point)
    nrm = b.n("GeometryNodeInputNormal", (-1150, -250))
    to_cam = b.vmath("SUBTRACT", oi.outputs["Location"], pos.outputs[0], (-750, -150))
    dist = b.vmath("LENGTH", to_cam.outputs[0], None, (-550, -300))
    persp_dir = b.vmath("NORMALIZE", to_cam.outputs[0], None, (-550, -150))
    back = b.n("FunctionNodeTransformDirection", (-750, -50))
    back.inputs["Direction"].default_value = (0.0, 0.0, 1.0)
    b.link(oi.outputs["Transform"], back.inputs["Transform"])
    ortho_dir = b.vmath("NORMALIZE", back.outputs[0], None, (-550, -50))
    dirn = b.n("ShaderNodeMix", (-350, -100), data_type="VECTOR", clamp_factor=True)
    dirn.label = "To Camera"
    b.link(ortho, _sock(dirn.inputs, "Factor"))
    b.link(persp_dir.outputs[0], _sock(dirn.inputs, "A"))
    b.link(ortho_dir.outputs[0], _sock(dirn.inputs, "B"))
    dirn = _sock(dirn.outputs, "Result")
    dot = b.vmath("DOT_PRODUCT", nrm.outputs["Normal"], dirn, (-150, -200))
    facing = b.math("MAXIMUM", dot.outputs["Value"], 0.0, (-150, -200), "Facing")

    # occlusion: ray from face toward camera, offset along normal
    off = b.vmath("SCALE", nrm.outputs["Normal"], None, (-550, -450))
    off.inputs["Scale"].default_value = 0.001
    src = b.vmath("ADD", pos.outputs[0], off.outputs[0], (-350, -450))
    rc = b.n("GeometryNodeRaycast", (-150, -400))
    b.link(gi.outputs["Geometry"], rc.inputs["Target Geometry"])
    b.link(src.outputs[0], rc.inputs["Source Position"])
    b.link(dirn, rc.inputs["Ray Direction"])
    b.link(dist.outputs["Value"], rc.inputs["Ray Length"])
    hit = b.math("MULTIPLY", rc.outputs["Is Hit"], gi.outputs["Occlusion"], (50, -400))
    visible = b.math("SUBTRACT", 1.0, hit, (250, -400), "Visible")

    w = b.math("MULTIPLY", facing, inframe, (600, -150))
    w = b.math("MULTIPLY", w, visible, (600, -250))
    ev = b.n("GeometryNodeFieldOnDomain", (750, -200), domain="FACE", data_type="FLOAT")
    b.link(w, ev.inputs[0])
    b.link(ev.outputs[0], go.inputs["Weight"])
    return ng


def build_mix(n):
    """UV_cam1..n (shifted) + VCMix / VCMix2 (Sharp/Smooth/Combined, blended with the
    mesh's own layers by Previous Bake) + material.
    VCMix:  R, G, B = cameras 1-3, A = blend mask (cameras see the face; 0 = scan).
            Cameras 1-3 win: where their sum is 1, cameras 4-6 do not show.
    VCMix2: R, G, B = cameras 4-6 - which of them shows where 1-3 leave room (A unused, 1).
    VCMix2 is stored first and VCMix last - VCMix is the top layer."""
    ng = _fresh_group(mix_name(n))
    ng.description = f"Store UV_cam1-{n} and the VCMix weights, set the material"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Material", "INPUT", "NodeSocketMaterial")
    _iface(ng, "Mode", "INPUT", "NodeSocketMenu")
    _iface(ng, "Previous Bake", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0).subtype = "FACTOR"
    for i in range(1, n + 1):
        _iface(ng, f"UV Shift {i}", "INPUT", "NodeSocketVector")
    for i in range(1, n + 1):
        _iface(ng, f"UV Cam{i}", "INPUT", "NodeSocketVector")
        _iface(ng, f"Weight Cam{i}", "INPUT", "NodeSocketFloat")

    b = B(ng)
    gi = b.n("NodeGroupInput", (-1908, 155))
    go = b.n("NodeGroupOutput", (1511, 0))

    # UV shift (photo moved by +shift -> sample at UV - shift)
    geo = gi.outputs["Geometry"]
    for i in range(1, n + 1):
        sh = b.vmath("SUBTRACT", gi.outputs[f"UV Cam{i}"], gi.outputs[f"UV Shift {i}"],
                     (-1300, 400 - i * 60))
        sh.hide = True
        st = b.n("GeometryNodeStoreNamedAttribute", (-1250 + i * 200, 662),
                 data_type="FLOAT2", domain="CORNER")
        st.inputs["Name"].default_value = f"UV_cam{i}"
        b.link(geo, st.inputs["Geometry"])
        b.link(sh.outputs[0], st.inputs["Value"])
        geo = st.outputs["Geometry"]

    W = [b.reroute(gi.outputs[f"Weight Cam{i}"], (-969, 56 - i * 50)) for i in range(1, n + 1)]

    def argmax(ws, x, y0, label):
        """One-hot of the largest weight (> 0); the first camera wins a tie."""
        top = ws[0]
        for i, w in enumerate(ws[1:], 1):
            top = b.math("MAXIMUM", top, w, (x, y0 + 100 - i * 40))
        out, taken = [], None
        for i, w in enumerate(ws):
            y = y0 - i * 90
            win = b.math("SUBTRACT", 1.0, b.math("LESS_THAN", w, top, (x + 200, y)), (x + 350, y))
            win = b.math("MULTIPLY", win, b.math("GREATER_THAN", w, 0.0, (x + 350, y - 40)), (x + 500, y))
            if taken is None:
                taken = win
            else:
                win = b.math("MULTIPLY", win, b.math("SUBTRACT", 1.0, taken, (x + 500, y - 40)), (x + 650, y))
                taken = b.math("ADD", taken, win, (x + 800, y - 40))
            win.node.label = f"{label} {i + 1}"
            out.append(win)
        return out

    def total(ws, x, y):
        t = ws[0]
        for i, w in enumerate(ws[1:], 1):
            t = b.math("ADD", t, w, (x, y - i * 40))
        return t

    def normalised(ws, t, x, y):
        inv = b.math("DIVIDE", 1.0, b.math("MAXIMUM", t, 1e-6, (x, y)), (x + 150, y))
        return [b.math("MULTIPLY", w, inv, (x + 300, y - 60 - i * 60)) for i, w in enumerate(ws)]

    # Cameras 1-3 (VCMix) win over 4-6 (VCMix2): VCMix holds cameras 1-3's share of ALL
    # cameras, VCMix2 which of 4-6 shows where 1-3 do not - so clearing VCMix reveals the
    # best of 4-6, and the material's priority blend gives back the plain 6-way result.
    W2 = W[3:]
    # ---- Sharp: argmax over all cameras (1-3 part) / over cameras 4-6 ----
    sharp = argmax(W, -817, 300, "Sharp")[:3]
    sharp2 = argmax(W2, -817, -100, "Sharp 4-6") if W2 else []
    # ---- Smooth: normalised over all cameras (1-3 part) / over cameras 4-6 ----
    tot = total(W, -809, -500)
    smooth = normalised(W, tot, -600, -500)[:3]
    smooth2 = normalised(W2, total(W2, -809, -800), -600, -800) if W2 else []

    # blend mask in VCMix alpha: how much the cameras see this face (0 = none -> original scan)
    cov = b.math("MINIMUM", tot, 1.0, (-509, -150), "Blend Mask")

    def colors(ws1, ws2, loc, label):
        """[VCMix color, VCMix2 color (4+ slots)]."""
        out = []
        for layer, chunk in enumerate((ws1, ws2)):
            if layer and not chunk:
                break
            cc = b.n("FunctionNodeCombineColor", (loc[0], loc[1] - layer * 160))
            cc.label = f"{label} {LAYERS[layer]}"
            for w, sk in zip(chunk, ("Red", "Green", "Blue")):
                b.link(w, cc.inputs[sk])
            if layer:
                cc.inputs["Alpha"].default_value = 1.0
            else:
                b.link(cov, cc.inputs["Alpha"])
            out.append(cc.outputs[0])
        return out

    sharp_c = colors(sharp, sharp2, (64, 300), "Sharp")
    smooth_c = colors(smooth, smooth2, (64, -100), "Smooth")

    # ---- Blend with the mesh's own layers (baked / painted) ----
    geo = b.reroute(geo, (392, 390))
    olds = []
    for layer in range(len(sharp_c)):
        na = b.n("GeometryNodeInputNamedAttribute", (-641, -647 - layer * 200), data_type="FLOAT_COLOR")
        na.inputs["Name"].default_value = LAYERS[layer]
        fac = b.math("MULTIPLY", na.outputs["Exists"], gi.outputs["Previous Bake"],
                     (-450, -696 - layer * 200), f"Previous Bake x {LAYERS[layer]} Exists")
        olds.append((b.reroute(fac, (404, 219 - layer * 40)),
                     b.reroute(na.outputs["Attribute"], (430, 136 - layer * 40))))

    def blended(col, layer, loc):
        fac, old = olds[layer]
        mx = b.n("ShaderNodeMix", loc, data_type="RGBA")
        b.link(fac, mx.inputs[0])
        b.link(col, _sock(mx.inputs, "A"))
        b.link(old, _sock(mx.inputs, "B"))
        return _sock(mx.outputs, "Result")

    ms = b.n("GeometryNodeMenuSwitch", (1064, 301), data_type="GEOMETRY")
    ms.enum_items.clear()
    for name in ("Sharp", "Smooth", "Combined"):
        ms.enum_items.new(name)
    cases = (("Sharp", sharp_c, "CORNER", 679), ("Smooth", smooth_c, "POINT", 329),
             ("Combined", sharp_c, "POINT", -21))
    for name, cols, dom, y in cases:
        g = geo
        for layer in reversed(range(len(cols))):       # VCMix2 first, VCMix last (on top)
            col = cols[layer]
            st = b.n("GeometryNodeStoreNamedAttribute", (780 + layer * 170, y - layer * 40),
                     data_type="FLOAT_COLOR", domain=dom)
            st.label = f"{LAYERS[layer]} {name} ({dom.title()})"
            st.inputs["Name"].default_value = LAYERS[layer]
            b.link(g, st.inputs["Geometry"])
            b.link(blended(col, layer, (530, y - layer * 120)), st.inputs["Value"])
            g = st.outputs["Geometry"]
        b.link(g, ms.inputs[name])
    b.link(gi.outputs["Mode"], ms.inputs["Menu"])

    sm = b.n("GeometryNodeSetMaterial", (1311, 11))
    b.link(ms.outputs["Output"], sm.inputs["Geometry"])
    b.link(gi.outputs["Material"], sm.inputs["Material"])
    b.link(sm.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def build_main(single, mix, n):
    ng = _fresh_group(main_name(n), is_modifier=True)
    ng.description = f"Project {n} cameras onto the mesh: UV_cam1-{n} + VCMix weights + material"
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    general = ng.interface.new_panel("General Settings")
    _iface(ng, "Material", "INPUT", "NodeSocketMaterial", parent=general)
    _iface(ng, "Mode", "INPUT", "NodeSocketMenu", parent=general)
    _iface(ng, "Previous Bake", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0,
           parent=general).subtype = "FACTOR"
    _iface(ng, "Occlusion", "INPUT", "NodeSocketBool", False, parent=general, single=True)
    shift = ng.interface.new_panel("Camera Shift")
    for i in range(1, n + 1):
        _iface(ng, f"UV Shift Cam{i}", "INPUT", "NodeSocketVector", parent=shift, dims=2, single=True)
    cams = ng.interface.new_panel("Cameras")
    for i in range(1, n + 1):
        _iface(ng, f"Camera {i}", "INPUT", "NodeSocketObject", parent=cams)
    lens = ng.interface.new_panel("Lens (driven)", default_closed=True)
    for i in range(1, n + 1):
        _iface(ng, f"Focal {i}", "INPUT", "NodeSocketFloat", 24.0, 0.001, parent=lens, single=True)
        _iface(ng, f"Sensor {i}", "INPUT", "NodeSocketFloat", 36.0, 0.001, parent=lens, single=True)
        _iface(ng, f"Aspect {i}", "INPUT", "NodeSocketFloat", 1.5, 0.001, parent=lens, single=True)
        _iface(ng, f"Ortho {i}", "INPUT", "NodeSocketFloat", 0.0, parent=lens, single=True)
        _iface(ng, f"Ortho Scale {i}", "INPUT", "NodeSocketFloat", 6.0, 0.001, parent=lens, single=True)

    b = B(ng)
    gi = b.n("NodeGroupInput", (-440, -246))
    go = b.n("NodeGroupOutput", (2922, -21))
    geo = b.reroute(gi.outputs["Geometry"], (-257, -261))
    occ = b.reroute(gi.outputs["Occlusion"], (151, -541))

    mx = b.n("GeometryNodeGroup", (568, 94), node_tree=mix)
    b.link(geo, mx.inputs["Geometry"])
    for k in ("Material", "Mode", "Previous Bake"):
        b.link(gi.outputs[k], mx.inputs[k])
    for i in range(1, n + 1):
        g = b.n("GeometryNodeGroup", (265, -200 - (i - 1) * 235), node_tree=single)
        g.label = f"Cam {i}"
        b.link(geo, g.inputs["Geometry"])
        b.link(occ, g.inputs["Occlusion"])
        b.link(gi.outputs[f"Camera {i}"], g.inputs["Camera"])
        b.link(gi.outputs[f"Focal {i}"], g.inputs["Focal Length"])
        b.link(gi.outputs[f"Sensor {i}"], g.inputs["Sensor Width"])
        b.link(gi.outputs[f"Aspect {i}"], g.inputs["Aspect"])
        b.link(gi.outputs[f"Ortho {i}"], g.inputs["Ortho"])
        b.link(gi.outputs[f"Ortho Scale {i}"], g.inputs["Ortho Scale"])
        b.link(gi.outputs[f"UV Shift Cam{i}"], mx.inputs[f"UV Shift {i}"])
        b.link(g.outputs["UV"], mx.inputs[f"UV Cam{i}"])
        b.link(g.outputs["Weight"], mx.inputs[f"Weight Cam{i}"])
    b.link(mx.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def up_to_date(n):
    return all(_up_to_date(name) for name in (SINGLE, mix_name(n), main_name(n)))


def ensure_node_groups(n):
    """Build the groups for `n` slots if missing/outdated; return the modifier group."""
    single = bpy.data.node_groups.get(SINGLE)
    if not _up_to_date(SINGLE):
        single = build_single()
    mix = bpy.data.node_groups.get(mix_name(n))
    if not _up_to_date(mix_name(n)):
        mix = build_mix(n)
    main = bpy.data.node_groups.get(main_name(n))
    if not _up_to_date(main_name(n)):
        main = build_main(single, mix, n)
    return main
