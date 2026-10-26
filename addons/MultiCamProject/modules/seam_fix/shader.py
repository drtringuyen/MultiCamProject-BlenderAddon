"""The materials while Seam Fix works on an object. Every image texture reading uv_normal
gets a node group (SF_<material>_<node>) put after it: the image node stays where it was,
its links move to the group, and the old links are kept in the material's "seamfix"
property for Reset / Apply. Each group switches on the object's seamfix_mode:

    0 Original   the image on uv_normal
    1 Mask       albedo only: the blend weight (white), the leveling offset (blue) and
                 the overlapping faces left out (red)
    2 Preview    albedo: the image leveled, blended with the source's continuation read
                 through uv_fixseam - live, no bake. Other images: as Original
    3 Baked      the _seamfix bake on uv_new"""
import json
import os

import bpy

from . import geometry as geo
from . import maps

MODE_PROP = "seamfix_mode"
MODES = ("Original", "Mask", "Preview", "Baked")
STATE = "seamfix"           # material property: {"nodes": {...}, "links": [...]}


def stem(name):
    root, ext = os.path.splitext(name)
    return root if ext.lower() in {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff",
                                   ".exr", ".bmp", ".webp"} else name


def temp_name(img):
    return stem(img.name) + "_seamfix"


def temp_alpha_name(img):
    return stem(img.name) + "_seamfix_alpha"


# ---------------------------------------------------------------- which images

def _reads_uv_normal(node):
    vec = node.inputs.get("Vector")
    if vec is None or not vec.is_linked:
        return True                     # the render UV map - uv_normal (checked by the caller)
    src = vec.links[0].from_node
    return src.type == 'UVMAP' and src.uv_map in ("", geo.UV_SRC)


def _is_albedo(node):
    return any(l.to_node.type == 'BSDF_PRINCIPLED' and l.to_socket.identifier == "Base Color"
               for l in node.outputs["Color"].links)


def materials(obj):
    seen = []
    for slot in obj.material_slots:
        m = slot.material
        if m is not None and m.node_tree is not None and m not in seen:
            seen.append(m)
    return seen


def image_nodes(mat):
    """[(node, kind)] - kind 'ALB' (into Base Color) or 'OTHER' - for the material's image
    textures on uv_normal, or the ones Seam Fix already manages."""
    st = state(mat)
    if st:
        out = []
        for name, info in st["nodes"].items():
            n = mat.node_tree.nodes.get(name)
            if n is not None:
                out.append((n, info["kind"]))
        return out
    return [(n, 'ALB' if _is_albedo(n) else 'OTHER') for n in mat.node_tree.nodes
            if n.type == 'TEX_IMAGE' and n.image is not None and _reads_uv_normal(n)]


def albedo(obj):
    """The first albedo image on uv_normal, or None."""
    for m in materials(obj):
        for n, kind in image_nodes(m):
            if kind == 'ALB':
                return n.image
    return None


def state(mat):
    raw = mat.get(STATE)
    return json.loads(raw) if raw else None


def installed(obj):
    return any(state(m) for m in materials(obj))


# ---------------------------------------------------------------- the groups

class _Builder:
    def __init__(self, ng):
        self.ng = ng
        self.x = 0

    def node(self, kind, **props):
        n = self.ng.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        n.location = (self.x, -len(self.ng.nodes) * 40)
        self.x += 30
        return n

    def link(self, a, b):
        self.ng.links.new(a, b)

    def tex(self, image, uv_name, like=None):
        uvn = self.node('ShaderNodeUVMap', uv_map=uv_name)
        t = self.node('ShaderNodeTexImage', image=image)
        if like is not None:
            t.interpolation, t.extension = like.interpolation, like.extension
        self.link(uvn.outputs["UV"], t.inputs["Vector"])
        return t

    def mix(self, fac, a, b, blend='MIX', data='RGBA', clamp=False):
        m = self.node('ShaderNodeMix', data_type=data, blend_type=blend, clamp_result=clamp)
        ins = (6, 7, 2) if data == 'RGBA' else (2, 3, 0)
        if isinstance(fac, float):
            m.inputs[0].default_value = fac
        else:
            self.link(fac, m.inputs[0])
        for sock, val in ((m.inputs[ins[0]], a), (m.inputs[ins[1]], b)):
            if isinstance(val, tuple):
                sock.default_value = val
            else:
                self.link(val, sock)
        return m.outputs[ins[2]]

    def math(self, op, a, b=None, c=None):
        m = self.node('ShaderNodeMath', operation=op)
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                self.link(v, m.inputs[i])
        return m.outputs[0]

    def gamma(self, col, g):
        n = self.node('ShaderNodeGamma')
        self.link(col, n.inputs["Color"])
        n.inputs["Gamma"].default_value = g
        return n.outputs["Color"]

    def level(self, col, lev, srgb):
        """col + (lev - 0.5), in the image's own (sRGB) values."""
        c = self.gamma(col, 1 / 2.2) if srgb else col
        c = self.mix(1.0, c, lev, blend='ADD')
        c = self.mix(1.0, c, (0.5, 0.5, 0.5, 1.0), blend='SUBTRACT', clamp=True)
        return self.gamma(c, 2.2) if srgb else c


def build_group(name, obj, src_node, kind, temp=None, temp_alpha=None):
    ng = bpy.data.node_groups.get(name) or bpy.data.node_groups.new(name, 'ShaderNodeTree')
    ng.nodes.clear()
    ng.interface.clear()
    ng.interface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Alpha", in_out='OUTPUT', socket_type='NodeSocketFloat')
    b = _Builder(ng)
    img = src_node.image
    srgb = img.colorspace_settings.name.lower().startswith("srgb")

    mode = b.node('ShaderNodeAttribute', attribute_type='OBJECT',
                  attribute_name=f'["{MODE_PROP}"]').outputs["Fac"]
    eq = [b.math('COMPARE', mode, float(k), 0.1) for k in range(4)]

    t_src = b.tex(img, geo.UV_SRC, src_node)
    color, alpha = t_src.outputs["Color"], t_src.outputs["Alpha"]

    if kind == 'ALB':
        t_fix = b.tex(img, geo.UV_FIX, src_node)
        t_mask = b.tex(bpy.data.images[maps.mask_name(obj)], geo.UV_SRC)
        l_src = b.tex(bpy.data.images[maps.level_name(obj)], geo.UV_SRC)
        l_fix = b.tex(bpy.data.images[maps.level_name(obj)], geo.UV_FIX)
        base = b.level(t_src.outputs["Color"], l_src.outputs["Color"], srgb)
        borrowed = b.level(t_fix.outputs["Color"], l_fix.outputs["Color"], srgb)
        preview = b.mix(t_mask.outputs["Color"], base, borrowed)

        dev = b.node('ShaderNodeVectorMath', operation='SUBTRACT')
        b.link(l_src.outputs["Color"], dev.inputs[0])
        dev.inputs[1].default_value = (0.5, 0.5, 0.5)
        size = b.node('ShaderNodeVectorMath', operation='LENGTH')
        b.link(dev.outputs[0], size.inputs[0])
        tint = b.math('MINIMUM', b.math('MULTIPLY', size.outputs["Value"], 4.0), 1.0)
        view = b.mix(tint, t_mask.outputs["Color"], (0.1, 0.35, 1.0, 1.0))
        over = b.node('ShaderNodeAttribute', attribute_type='GEOMETRY',
                      attribute_name=geo.OVERLAP).outputs["Fac"]
        view = b.mix(over, view, (1.0, 0.05, 0.05, 1.0))

        color = b.mix(eq[1], color, view)
        color = b.mix(eq[2], color, preview)

    if temp is not None:
        t_new = b.tex(temp, geo.UV_NEW, src_node)
        color = b.mix(eq[3], color, t_new.outputs["Color"])
        if temp_alpha is not None:
            t_a = b.tex(temp_alpha, geo.UV_NEW, src_node)
            alpha = b.mix(eq[3], alpha, t_a.outputs["Color"], data='FLOAT')
        else:
            alpha = b.mix(eq[3], alpha, t_new.outputs["Alpha"], data='FLOAT')

    out = b.node('NodeGroupOutput')
    b.link(color, out.inputs["Color"])
    b.link(alpha, out.inputs["Alpha"])
    return ng


def _group_name(mat, node):
    return f"SF_{mat.name}_{node.name}"[:63]


def install(obj):
    """Puts (or rebuilds) a group after every image on uv_normal in obj's materials."""
    maps.ensure_neutral(obj)
    for mat in materials(obj):
        st = state(mat) or {"nodes": {}, "links": []}
        nt = mat.node_tree
        for n, kind in image_nodes(mat):
            info = st["nodes"].get(n.name)
            temp = bpy.data.images.get(info["temp"]) if info and info.get("temp") else None
            temp_a = bpy.data.images.get(info["temp_alpha"]) if info and info.get("temp_alpha") else None
            ng = build_group(_group_name(mat, n), obj, n, kind, temp, temp_a)
            gnode = nt.nodes.get(info["group_node"]) if info else None
            if gnode is None:
                gnode = nt.nodes.new('ShaderNodeGroup')
                gnode.location = (n.location.x + 60, n.location.y - 60)
                gnode.label = "Seam Fix"
                for out in ("Color", "Alpha"):
                    for l in list(n.outputs[out].links):
                        st["links"].append([n.name, out, l.to_node.name, l.to_socket.identifier])
                        nt.links.remove(l)
                info ={"kind": kind, "group": ng.name, "image": n.image.name,
                        "temp": info.get("temp") if info else None,
                        "temp_alpha": info.get("temp_alpha") if info else None}
                info["group_node"] = gnode.name
                st["nodes"][n.name] = info
            gnode.node_tree = ng
            for node_name, out, to_name, to_sock in st["links"]:
                if node_name != n.name:
                    continue
                to_node = nt.nodes.get(to_name)
                if to_node is None:
                    continue
                to = next((s for s in to_node.inputs if s.identifier == to_sock), None)
                if to is not None and not any(l.from_node == gnode for l in to.links):
                    nt.links.new(gnode.outputs[out], to)
        mat[STATE] = json.dumps(st)


def set_temp(mat, node_name, temp, temp_alpha):
    st = state(mat)
    st["nodes"][node_name]["temp"] = temp.name if temp else None
    st["nodes"][node_name]["temp_alpha"] = temp_alpha.name if temp_alpha else None
    mat[STATE] = json.dumps(st)


def uninstall(obj):
    """Original links back, groups gone. Returns the images Seam Fix made for them."""
    made = []
    for mat in materials(obj):
        st = state(mat)
        if not st:
            continue
        nt = mat.node_tree
        for name, info in st["nodes"].items():
            gnode = nt.nodes.get(info.get("group_node", ""))
            if gnode is not None:
                nt.nodes.remove(gnode)
            ng = bpy.data.node_groups.get(info.get("group", ""))
            if ng is not None and ng.users == 0:
                bpy.data.node_groups.remove(ng)
            made += [info.get("temp"), info.get("temp_alpha")]
        for node_name, out, to_name, to_sock in st["links"]:
            n, to_node = nt.nodes.get(node_name), nt.nodes.get(to_name)
            if n is None or to_node is None:
                continue
            to = next((s for s in to_node.inputs if s.identifier == to_sock), None)
            if to is not None:
                nt.links.new(n.outputs[out], to)
        del mat[STATE]
    return [m for m in made if m]


def set_mode(obj, mode):
    obj[MODE_PROP] = int(mode)
    obj.update_tag()
    for mat in materials(obj):
        mat.node_tree.update_tag()


def get_mode(obj):
    return int(obj.get(MODE_PROP, 0))
