"""Texture Tiles in the Bake view (Final): each tile shows its own ALB_ / NOR_.

GN-Final is the user's group and stays as it is. A small per-object modifier GN-Tiles
runs right after it, only while Final is on:
  1. uv_normal = uv_final - (tile, 0) on every corner (MCP_TILE: the tile of each face,
     written by export/tiles.write_tile_attr) - so MAT_ and everything else that reads uv_normal
     (the exporter too) sees the tile's own 0-1 layout
  2. Set Material MAT_<obj>_<tile> on each tile's faces
It is on in "Baked Tiles" (and always during an export); "Baked" shows the object's own
MAT_ / ALB_ / NOR_ on uv_normal with GN-Tiles off.
The tile materials carry their own tag (not MAT_'s): matsync never takes them for
orphans, so it never recycles their files; the tile owns them (tile.material)."""
import bpy

from ..camera_project.gn_builder import B
from . import common

MOD_NAME = "GN-Tiles"
GROUP_PREFIX = "GN-Tiles | "
TILE_TAG = "multicamproject_tile_mat"
TILE_ATTR = "MCP_TILE"
P = "mcp_bake_"


def _tiles():
    from ..export import tiles
    return tiles


def is_on(obj):
    try:
        return _tiles().is_on(obj)
    except ImportError:
        return False


def tile_mat_name(obj, x):
    return common.mat_name(obj) + "_" + x.name


# ---------------------------------------------------------------- materials

def _fill(mat, alb, nor):
    """MAT_'s layout (material.build): ALB_ -> Base Color, NOR_ -> tangent Normal Map, both
    on uv_normal (GN-Tiles puts the tile's layout there)."""
    from ..camera_project import core as cp
    mat.use_nodes = True
    nt = mat.node_tree
    for n in [n for n in nt.nodes
              if n.name.startswith(P) or n.type in {'BSDF_PRINCIPLED', 'OUTPUT_MATERIAL'}]:
        nt.nodes.remove(n)          # ours and a new material's defaults; Preview re-adds its own
    b = B(nt)

    def node(typ, name, loc, **kw):
        n = b.n(typ, loc, **kw)
        n.name = P + name
        return n

    out = node("ShaderNodeOutputMaterial", "out", (400, 0))
    bsdf = node("ShaderNodeBsdfPrincipled", "bsdf", (100, 0))
    bsdf.inputs["Roughness"].default_value = cp.ROUGHNESS
    bsdf.inputs["Metallic"].default_value = 0.0
    b.link(bsdf.outputs[0], out.inputs["Surface"])
    uv = node("ShaderNodeUVMap", "uv", (-700, -100), uv_map=common.UV_NORMAL)
    a = node("ShaderNodeTexImage", "alb", (-400, 100), image=alb)
    a.label = "ALB"
    b.link(uv.outputs["UV"], a.inputs["Vector"])
    b.link(a.outputs["Color"], bsdf.inputs["Base Color"])
    if nor is not None:
        n = node("ShaderNodeTexImage", "nor", (-400, -250), image=nor)
        n.label = "NOR"
        b.link(uv.outputs["UV"], n.inputs["Vector"])
        nm = node("ShaderNodeNormalMap", "normalmap", (-120, -300), space='TANGENT',
                  uv_map=common.UV_NORMAL)
        b.link(n.outputs["Color"], nm.inputs["Color"])
        b.link(nm.outputs["Normal"], bsdf.inputs["Normal"])


def build_materials(obj):
    """MAT_<obj>_<tile> for every tile, on the tile's ALB_ / NOR_."""
    from ... import quality
    t = _tiles().tiles_of(obj)
    out = []
    for x in t.tiles:
        name = tile_mat_name(obj, x)
        mat = x.material
        if mat is None:
            mat = bpy.data.materials.get(name)
            if mat is not None and not mat.get(TILE_TAG):
                mat = None
        if mat is None:
            mat = bpy.data.materials.new(name)
        elif mat.name != name and not bpy.data.materials.get(name):
            mat.name = name
        mat[TILE_TAG] = True
        _fill(mat, x.alb_image, x.nor_image)
        x.material = mat
        quality.sync_material(mat)
        out.append(mat)
    return out


def tile_images(obj):
    """[(ALB_, NOR_)] of every tile."""
    if not is_on(obj):
        return []
    return [(x.alb_image, x.nor_image) for x in _tiles().tiles_of(obj).tiles]


def materials(obj):
    """The tile materials of obj (for the normal toggle, the export)."""
    if not is_on(obj):
        return []
    return [x.material for x in _tiles().tiles_of(obj).tiles if x.material is not None]


# ---------------------------------------------------------------- GN-Tiles

def _group(obj):
    """The object's GN-Tiles group, rebuilt (cheap) for its current tiles / materials."""
    name = GROUP_PREFIX + obj.name
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    else:
        ng.nodes.clear()
        ng.interface.clear()
    ng.is_modifier = True
    ng.description = ("Texture Tiles in Final: uv_normal = uv_final of the face's tile, "
                      "and each tile's MAT_")
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    b = B(ng)
    gi = b.n("NodeGroupInput", (-900, 0))
    go = b.n("NodeGroupOutput", (300 + 220 * len(_tiles().tiles_of(obj).tiles), 0))
    tile = b.n("GeometryNodeInputNamedAttribute", (-900, -300), data_type='INT')
    tile.inputs["Name"].default_value = TILE_ATTR
    tile.label = "Tile"
    uvf = b.n("GeometryNodeInputNamedAttribute", (-900, -450), data_type='FLOAT_VECTOR')
    uvf.inputs["Name"].default_value = _tiles().UV_FINAL
    uvf.label = "uv_final"
    off = b.n("ShaderNodeCombineXYZ", (-650, -300))
    b.link(tile.outputs["Attribute"], off.inputs["X"])
    sub = b.n("ShaderNodeVectorMath", (-450, -400), operation='SUBTRACT')
    b.link(uvf.outputs["Attribute"], sub.inputs[0])
    b.link(off.outputs["Vector"], sub.inputs[1])
    st = b.n("GeometryNodeStoreNamedAttribute", (-200, 0), data_type='FLOAT2', domain='CORNER')
    st.inputs["Name"].default_value = common.UV_NORMAL
    st.label = "uv_normal = tile layout"
    b.link(gi.outputs[0], st.inputs["Geometry"])
    b.link(sub.outputs["Vector"], st.inputs["Value"])
    g = st.outputs["Geometry"]
    g = _color_from_tiles(obj, b, g, tile, sub)
    for k, x in enumerate(_tiles().tiles_of(obj).tiles):
        if x.material is None:
            continue
        eq = b.n("FunctionNodeCompare", (220 * k - 200, -300), data_type='INT',
                 operation='EQUAL')
        b.link(tile.outputs["Attribute"], eq.inputs["A"])
        eq.inputs["B"].default_value = k
        sm = b.n("GeometryNodeSetMaterial", (220 * k, 0))
        sm.label = x.name
        sm.inputs["Material"].default_value = x.material      # a node default: no leak
        b.link(g, sm.inputs["Geometry"])
        b.link(eq.outputs["Result"], sm.inputs["Selection"])
        g = sm.outputs["Geometry"]
    b.link(g, go.inputs[0])
    return ng


def _color_from_tiles(obj, b, g, tile, uv):
    """During an export (gn_final.albedo_on) that takes Color from ALB: Color sampled from
    each face's own tile ALB_ (GN-Final sampled the object's own ALB_ through uv_normal). Outside an export no image is held: GN would make each 8K a 1 GB float."""
    from . import gn_final
    scene = bpy.context.scene
    if not gn_final._albedo_on or not gn_final.uses_alb(obj, scene):
        return g
    t = _tiles().tiles_of(obj)
    col = None
    for k, x in enumerate(t.tiles):
        img = x.alb_image
        if img is None:
            continue
        tex = b.n("GeometryNodeImageTexture", (220 * k - 200, -700), interpolation='Linear',
                  extension='EXTEND')
        tex.inputs["Image"].default_value = img
        b.link(uv.outputs["Vector"], tex.inputs["Vector"])
        if col is None:
            col = tex.outputs["Color"]
            continue
        eq = b.n("FunctionNodeCompare", (220 * k - 200, -900), data_type='INT',
                 operation='EQUAL')
        b.link(tile.outputs["Attribute"], eq.inputs["A"])
        eq.inputs["B"].default_value = k
        sw = b.n("GeometryNodeSwitch", (220 * k, -800), input_type='RGBA')
        b.link(eq.outputs["Result"], sw.inputs["Switch"])
        b.link(col, sw.inputs["False"])
        b.link(tex.outputs["Color"], sw.inputs["True"])
        col = sw.outputs["Output"]
    if col is None:
        return g
    st = b.n("GeometryNodeStoreNamedAttribute", (0, -600), data_type='BYTE_COLOR',
             domain='CORNER')
    st.inputs["Name"].default_value = common.COLOR
    st.label = "Color from the tile's ALB_"
    b.link(g, st.inputs["Geometry"])
    b.link(col, st.inputs["Value"])
    return st.outputs["Geometry"]


def _modifier(obj):
    m = obj.modifiers.get(MOD_NAME)
    return m if m is not None and m.type == 'NODES' else None


def remove(obj):
    m = _modifier(obj)
    if m is not None:
        ng = m.node_group
        obj.modifiers.remove(m)
        if ng is not None and ng.users == 0:
            bpy.data.node_groups.remove(ng)


def sync(obj, final=None):
    """GN-Tiles on (Baked Tiles, and in Baked during an export) / off (Processing, Baked on
    uv_normal), right after GN-Final; removed when the object is not in Tiles mode."""
    from . import gn_final
    if not is_on(obj):
        remove(obj)
        return
    if final is None:
        final = gn_final.is_final(obj)
    final = bool(final) and (_tiles().tiles_of(obj).final_tiles or gn_final._albedo_on)
    m = _modifier(obj)
    if m is None:
        if not final:
            return
        m = obj.modifiers.new(MOD_NAME, 'NODES')
    m.node_group = _group(obj)
    m.show_viewport = m.show_render = bool(final)
    last = len(obj.modifiers) - 1
    if list(obj.modifiers).index(m) != last:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=m.name, index=last)
    obj.update_tag()


def refresh(obj, scene):
    """After a tile bake: the tile materials on their images, GN-Tiles rebuilt."""
    if not is_on(obj):
        return
    build_materials(obj)
    sync(obj)
