"""Prototype builder for GN-CameraProject, verified live in Blender 5.2 on MESH_Trashbin.
Reference for the MultiCamProject module - not yet wired into the addon."""
import bpy

SINGLE = "GN-CamProject_Single"
MAIN = "GN-CameraProject"


def _sock(sockets, name, enabled_only=True):
    for s in sockets:
        if s.name == name and (s.enabled or not enabled_only):
            return s
    raise KeyError(name)


class B:
    def __init__(self, tree):
        self.t = tree
        self.x = 0

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


def _iface(ng, name, io, typ, default=None, mn=None, mx=None, parent=None):
    kw = {"name": name, "in_out": io, "socket_type": typ}
    if parent is not None:
        kw["parent"] = parent
    s = ng.interface.new_socket(**kw)
    if default is not None:
        s.default_value = default
    if mn is not None:
        s.min_value = mn
    if mx is not None:
        s.max_value = mx
    return s


def build_single():
    old = bpy.data.node_groups.get(SINGLE)
    if old:
        bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(SINGLE, "GeometryNodeTree")
    ng.description = "Project one camera: UV (corner field) + facing weight (face field)"
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    _iface(ng, "Camera", "INPUT", "NodeSocketObject")
    _iface(ng, "Focal Length", "INPUT", "NodeSocketFloat", 24.0, 0.001)
    _iface(ng, "Sensor Width", "INPUT", "NodeSocketFloat", 36.0, 0.001)
    _iface(ng, "Aspect", "INPUT", "NodeSocketFloat", 1.5, 0.001)
    _iface(ng, "Occlusion", "INPUT", "NodeSocketBool", False)
    _iface(ng, "UV", "OUTPUT", "NodeSocketVector")
    _iface(ng, "Weight", "OUTPUT", "NodeSocketFloat")

    b = B(ng)
    gi = b.n("NodeGroupInput", (-1400, 0))
    go = b.n("NodeGroupOutput", (900, 0))

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


def build_main(single):
    old = bpy.data.node_groups.get(MAIN)
    if old:
        bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(MAIN, "GeometryNodeTree")
    ng.is_modifier = True
    ng.description = "Project 3 cameras onto the mesh: UV_cam1/2/3 + VCMix weights + material"
    _iface(ng, "Geometry", "INPUT", "NodeSocketGeometry")
    for i in (1, 2, 3):
        _iface(ng, f"Camera {i}", "INPUT", "NodeSocketObject")
    _iface(ng, "Mode", "INPUT", "NodeSocketMenu")
    _iface(ng, "Original Blend", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0).subtype = "FACTOR"
    _iface(ng, "Occlusion", "INPUT", "NodeSocketBool", False)
    _iface(ng, "Material", "INPUT", "NodeSocketMaterial")
    lens = ng.interface.new_panel("Lens (driven)", default_closed=True)
    for i in (1, 2, 3):
        _iface(ng, f"Focal {i}", "INPUT", "NodeSocketFloat", 24.0, 0.001, parent=lens)
        _iface(ng, f"Sensor {i}", "INPUT", "NodeSocketFloat", 36.0, 0.001, parent=lens)
    _iface(ng, "Aspect", "INPUT", "NodeSocketFloat", 1.5, 0.001, parent=lens)
    _iface(ng, "Geometry", "OUTPUT", "NodeSocketGeometry")

    b = B(ng)
    gi = b.n("NodeGroupInput", (-1400, 0))
    go = b.n("NodeGroupOutput", (1800, 0))

    geo = gi.outputs["Geometry"]
    weights = []
    for i in (1, 2, 3):
        y = 500 - (i - 1) * 300
        g = b.n("GeometryNodeGroup", (-1000, y), node_tree=single)
        g.label = f"Cam {i}"
        b.link(gi.outputs["Geometry"], g.inputs["Geometry"])
        b.link(gi.outputs[f"Camera {i}"], g.inputs["Camera"])
        b.link(gi.outputs[f"Focal {i}"], g.inputs["Focal Length"])
        b.link(gi.outputs[f"Sensor {i}"], g.inputs["Sensor Width"])
        b.link(gi.outputs["Aspect"], g.inputs["Aspect"])
        b.link(gi.outputs["Occlusion"], g.inputs["Occlusion"])
        st = b.n("GeometryNodeStoreNamedAttribute", (-700 + (i - 1) * 200, 700),
                 data_type="FLOAT2", domain="CORNER")
        st.inputs["Name"].default_value = f"UV_cam{i}"
        b.link(geo, st.inputs["Geometry"])
        b.link(g.outputs["UV"], st.inputs["Value"])
        geo = st.outputs["Geometry"]
        weights.append(g.outputs["Weight"])
    R, G, Bw = weights

    # ---- Sharp (argmax) ----
    def ge(a, c, loc):  # a >= c  ->  1 - (c > a)
        return b.math("SUBTRACT", 1.0, b.math("GREATER_THAN", c, a, loc), (loc[0] + 150, loc[1]))

    x0 = -500
    rs = b.math("MULTIPLY", ge(R, G, (x0, 100)), ge(R, Bw, (x0, 50)), (x0 + 350, 80))
    rs = b.math("MULTIPLY", rs, b.math("GREATER_THAN", R, 0.0, (x0 + 350, 20)), (x0 + 500, 60), "R sharp")
    gs = b.math("MULTIPLY", b.math("GREATER_THAN", G, R, (x0, -50)), ge(G, Bw, (x0, -100)), (x0 + 350, -70))
    gs = b.math("MULTIPLY", gs, b.math("GREATER_THAN", G, 0.0, (x0 + 350, -130)), (x0 + 500, -90), "G sharp")
    bs = b.math("MULTIPLY", b.math("GREATER_THAN", Bw, R, (x0, -200)), b.math("GREATER_THAN", Bw, G, (x0, -250)), (x0 + 350, -220))
    bs = b.math("MULTIPLY", bs, b.math("GREATER_THAN", Bw, 0.0, (x0 + 350, -280)), (x0 + 500, -240), "B sharp")
    sharp = b.n("FunctionNodeCombineColor", (200, 0))
    sharp.label = "Sharp RGB"
    for s, sk in zip((rs, gs, bs), ("Red", "Green", "Blue")):
        b.link(s, sharp.inputs[sk])

    # ---- Smooth (normalised sum) ----
    tot = b.math("ADD", b.math("ADD", R, G, (x0, -400)), Bw, (x0 + 150, -400))
    inv = b.math("DIVIDE", 1.0, b.math("MAXIMUM", tot, 1e-6, (x0 + 300, -400)), (x0 + 450, -400))
    smooth = b.n("FunctionNodeCombineColor", (200, -350))
    smooth.label = "Smooth RGB"
    for i, (s, sk) in enumerate(zip((R, G, Bw), ("Red", "Green", "Blue"))):
        b.link(b.math("MULTIPLY", s, inv, (x0 + 600, -350 - i * 50)), smooth.inputs[sk])

    # ---- Blend with original VCMix ----
    na = b.n("GeometryNodeInputNamedAttribute", (200, -600), data_type="FLOAT_COLOR")
    na.inputs["Name"].default_value = "VCMix"
    fac = b.math("MULTIPLY", gi.outputs["Original Blend"], na.outputs["Exists"], (400, -600), "Blend x Exists")

    def blended(col, loc):
        mx = b.n("ShaderNodeMix", loc, data_type="RGBA")
        b.link(fac, mx.inputs[0])
        b.link(col, _sock(mx.inputs, "A"))
        b.link(na.outputs["Attribute"], _sock(mx.inputs, "B"))
        return _sock(mx.outputs, "Result")

    ms = b.n("GeometryNodeMenuSwitch", (1200, 0), data_type="GEOMETRY")
    ms.enum_items.clear()
    for name in ("Sharp", "Smooth", "Combined"):
        ms.enum_items.new(name)
    cases = (("Sharp", sharp, "CORNER", 250), ("Smooth", smooth, "POINT", 0), ("Combined", sharp, "POINT", -250))
    for name, col, dom, y in cases:
        st = b.n("GeometryNodeStoreNamedAttribute", (950, y), data_type="FLOAT_COLOR", domain=dom)
        st.label = f"VCMix {name} ({dom.title()})"
        st.inputs["Name"].default_value = "VCMix"
        b.link(geo, st.inputs["Geometry"])
        b.link(blended(col.outputs[0], (700, y)), st.inputs["Value"])
        b.link(st.outputs["Geometry"], ms.inputs[name])
    b.link(gi.outputs["Mode"], ms.inputs["Menu"])

    sm = b.n("GeometryNodeSetMaterial", (1500, 0))
    b.link(ms.outputs["Output"], sm.inputs["Geometry"])
    b.link(gi.outputs["Material"], sm.inputs["Material"])
    b.link(sm.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def build_material(obj, cams):
    name = f"MAT_{obj.name}"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    b = B(nt)
    out = b.n("ShaderNodeOutputMaterial", (1200, 0))
    bsdf = b.n("ShaderNodeBsdfPrincipled", (900, 0))
    b.link(bsdf.outputs[0], out.inputs["Surface"])
    vc = b.n("ShaderNodeVertexColor", (-600, -500), layer_name="VCMix")
    sepc = b.n("ShaderNodeSeparateColor", (-400, -500))
    b.link(vc.outputs["Color"], sepc.inputs[0])
    ws = [sepc.outputs["Red"], sepc.outputs["Green"], sepc.outputs["Blue"]]
    acc = None
    for i, cam in enumerate(cams):
        uvn = b.n("ShaderNodeUVMap", (-900, 400 - i * 300), uv_map=f"UV_cam{i + 1}")
        tex = b.n("ShaderNodeTexImage", (-650, 400 - i * 300), extension="CLIP")
        tex.label = f"Cam {i + 1}: {cam.name}"
        bgs = cam.data.background_images
        if len(bgs) and bgs[0].image:
            tex.image = bgs[0].image
        b.link(uvn.outputs["UV"], tex.inputs["Vector"])
        sc = b.vmath("SCALE", tex.outputs["Color"], None, (-250, 400 - i * 300))
        b.link(ws[i], sc.inputs["Scale"])
        acc = sc.outputs[0] if acc is None else b.vmath("ADD", acc, sc.outputs[0], (0, 300 - i * 200)).outputs[0]
    tot = b.math("ADD", b.math("ADD", ws[0], ws[1], (-150, -500)), ws[2], (0, -500))
    inv = b.math("DIVIDE", 1.0, b.math("MAXIMUM", tot, 1e-6, (150, -500)), (300, -500))
    norm = b.vmath("SCALE", acc, None, (500, 0))
    b.link(inv, norm.inputs["Scale"])
    b.link(norm.outputs[0], bsdf.inputs["Base Color"])
    return mat


def _ident(ng, name):
    for it in ng.interface.items_tree:
        if getattr(it, "in_out", None) == "INPUT" and it.name == name:
            return it.identifier
    raise KeyError(name)


def _set_input(mod, ng, name, value):
    # Blender 5.2: modifier inputs live on mod.properties.inputs.<Socket_N>.value
    # (the old mod["Socket_N"] style silently does nothing)
    getattr(mod.properties.inputs, _ident(ng, name)).value = value


def setup(obj, cams, mode="Sharp"):
    single = build_single()
    main = build_main(single)
    mat = build_material(obj, cams)
    mod = obj.modifiers.get("GN-CameraProject") or obj.modifiers.new("GN-CameraProject", "NODES")
    mod.node_group = main
    img = cams[0].data.background_images[0].image
    aspect = img.size[0] / img.size[1] if img and img.size[1] else 1.5
    _set_input(mod, main, "Aspect", aspect)
    _set_input(mod, main, "Material", mat)
    _set_input(mod, main, "Mode", mode)
    for i, cam in enumerate(cams, 1):
        _set_input(mod, main, f"Camera {i}", cam)
        for sock, prop in ((f"Focal {i}", "lens"), (f"Sensor {i}", "sensor_width")):
            path = f'modifiers["{mod.name}"].properties.inputs.{_ident(main, sock)}.value'
            fc = obj.driver_add(path)
            d = fc.driver
            d.type = "AVERAGE"
            while d.variables:
                d.variables.remove(d.variables[0])
            var = d.variables.new()
            var.name = "v"
            var.targets[0].id_type = "CAMERA"
            var.targets[0].id = cam.data
            var.targets[0].data_path = prop
    obj.update_tag()
    return mod, mat
