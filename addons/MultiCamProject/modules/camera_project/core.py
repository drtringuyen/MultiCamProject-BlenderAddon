"""Camera projection logic: material, modifier wiring, camera scoring, images, slots."""
import os

import bpy
import numpy as np

from . import gn_builder
from .gn_builder import B

MOD_NAME = "GN-CameraProject"
MODES = ("Sharp", "Smooth", "Combined")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".exr", ".webp", ".bmp")
SCORE_MIN = 0.01        # cameras below this do not "hit" the object
# coverage filter of the camera list and the auto pick: (key, label, minimum coverage)
COVERAGE_FILTERS = (('100', "100%", 0.999), ('80', ">80%", 0.8), ('50', ">50%", 0.5),
                    ('30', ">30%", 0.3), ('ALL', "All", 0.0))
AXES = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))    # Camera 1 ~ +-Y, 2 ~ +-X, 3 ~ +-Z
MAX_SAMPLES = 4000      # vertices sampled for scoring


def data(obj):
    return obj.multicamproject_cam


def slot_count(d):
    return int(d.slot_count)


def get_slots(d):
    """The active slots (Camera 1..slot_count). Slots above the count keep their camera
    but are not projected."""
    return [getattr(d, f"slot_{i}") for i in range(1, slot_count(d) + 1)]


def set_slots(d, cams):
    for i, cam in enumerate(cams, 1):
        setattr(d, f"slot_{i}", cam)


# ---------------------------------------------------------------- images

def bg_entry(cam, create=False):
    bgs = cam.data.background_images
    if len(bgs):
        return bgs[0]
    return bgs.new() if create else None


def cam_image(cam):
    bg = bg_entry(cam)
    return bg.image if bg else None


def image_ok(img):
    """True when the image file exists (or is packed/generated).
    Deliberately does not touch img.size: that decodes the photo, far too slow
    for hundreds of cameras. An image already in memory must have pixels."""
    if img is None:
        return False
    if img.source == 'FILE' and not img.packed_file:
        if not os.path.isfile(bpy.path.abspath(img.filepath, library=img.library)):
            return False
    if img.has_data:
        return img.size[0] > 0 and img.size[1] > 0
    return True


def _render_aspect(scene):
    r = scene.render
    return (r.resolution_x * r.pixel_aspect_x) / max(1e-6, r.resolution_y * r.pixel_aspect_y)


def image_aspect(img, scene, load=True):
    """Width/height of the photo. With load=False, unloaded images fall back to
    the render aspect instead of being decoded."""
    if image_ok(img) and (load or img.has_data):
        w, h = img.size
        if w and h:
            return w / h
    return _render_aspect(scene)


def _folder_listing(folder):
    folder = bpy.path.abspath(folder) if folder else ""
    if not folder or not os.path.isdir(folder):
        return folder, {}
    return folder, {f.lower(): f for f in os.listdir(folder)}


def _find_in_folder(folder, listing, cam):
    """Look for the camera's photo: first the current image's filename,
    then <camera name>.<ext>."""
    names = []
    img = cam_image(cam)
    if img and img.filepath:
        names.append(os.path.basename(bpy.path.abspath(img.filepath)))
    names += [cam.name + ext for ext in IMAGE_EXTS]
    for n in names:
        real = listing.get(n.lower())
        if real:
            return os.path.join(folder, real)
    return None


def _reload(img):
    # only images already in memory need a reload; others load fresh when used
    if img and img.source == 'FILE' and img.has_data:
        img.reload()


def set_cam_image_path(cam, path):
    """Use `path` as the camera's background image (reuses an already loaded one)."""
    img = bpy.data.images.load(path, check_existing=True)
    _reload(img)
    bg = bg_entry(cam, create=True)
    bg.image = img
    cam.data.show_background_images = True
    return img


def resolve_cam_image(cam, folder, listing):
    """Fetch/reload the camera's photo. Returns the image (may be None)."""
    path = _find_in_folder(folder, listing, cam) if listing else None
    img = cam_image(cam)
    if path:
        same = img and os.path.normcase(bpy.path.abspath(img.filepath)) == os.path.normcase(path)
        if not same:
            return set_cam_image_path(cam, path)
    _reload(img)
    return img


# ---------------------------------------------------------------- scoring

def _object_samples(obj):
    me = obj.data
    n = len(me.vertices)
    if n == 0:
        return None, None
    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    nr = np.empty(n * 3, dtype=np.float32)
    me.vertex_normals.foreach_get("vector", nr)
    co, nr = co.reshape(n, 3), nr.reshape(n, 3)
    step = max(1, n // MAX_SAMPLES)
    co, nr = co[::step], nr[::step]
    mw = np.array(obj.matrix_world, dtype=np.float32)
    co_w = co @ mw[:3, :3].T + mw[:3, 3]
    nmat = np.array(obj.matrix_world.to_3x3().inverted_safe().transposed(), dtype=np.float32)
    nr_w = nr @ nmat.T
    nr_w /= np.maximum(np.linalg.norm(nr_w, axis=1, keepdims=True), 1e-9)
    return co_w, nr_w


def score_camera(cam, co_w, nr_w, scene):
    """Same projection as the GN group. Returns (score, coverage): coverage = share of the
    object's (sampled) vertices in the frame, score = coverage x mean facing of those."""
    cd = cam.data
    if cd.type not in {'PERSP', 'ORTHO'}:
        return 0.0, 0.0
    ortho = cd.type == 'ORTHO'
    cmi = np.array(cam.matrix_world.inverted_safe(), dtype=np.float32)
    p = co_w @ cmi[:3, :3].T + cmi[:3, 3]
    depth = -p[:, 2]
    front = depth > 1e-6
    if not front.any():
        return 0.0, 0.0
    # frame width at each point (sensor fit horizontal)
    width = cd.ortho_scale if ortho else np.where(front, depth, 1.0) * cd.sensor_width / cd.lens
    aspect = image_aspect(cam_image(cam), scene, load=False)
    u = p[:, 0] / width + 0.5
    v = p[:, 1] / width * aspect + 0.5
    inside = front & (u > 0) & (u < 1) & (v > 0) & (v < 1)
    if not inside.any():
        return 0.0, 0.0
    if ortho:       # every point looks along the camera's back axis
        back = np.array(cam.matrix_world.to_3x3().col[2], dtype=np.float32)
        to_cam = np.broadcast_to(back / max(np.linalg.norm(back), 1e-9), (int(inside.sum()), 3))
    else:
        to_cam = np.array(cam.matrix_world.translation, dtype=np.float32) - co_w[inside]
        to_cam /= np.maximum(np.linalg.norm(to_cam, axis=1, keepdims=True), 1e-9)
    facing = np.clip((nr_w[inside] * to_cam).sum(axis=1), 0.0, None).mean()
    coverage = float(inside.mean())
    return coverage * float(facing), coverage


def scene_cameras(scene):
    return [o for o in scene.objects if o.type == 'CAMERA']


# ---------------------------------------------------------------- material
# One projection material per object, MCP_<name>, in material slot 1 (MAT_<name> is the
# baked export material of the baking module). Three frames:
#   ORIGINAL MATERIALS  the scan's Base Color images, picked per face by uv_index
#   PROJECTION          Cam 1..N on UV_cam1..N, weighted by VCMix (1-3) and VCMix2 (4-6)
#   BLEND               Original Scan slider (0 = projection, 1 = scan), masked by VCMix alpha

MAT_TAG = "multicamproject_material"
MAT_VERSION_KEY = "multicamproject_mat_version"
MAT_VERSION = 5         # bump when the material's node setup changes
PREFIX = "MCP_"
OLD_PREFIX = "MAT_"                 # projection material before the 2026-09-25 rename
BAKED_TAG = "multicamproject_baked"     # MAT_<name> built by the baking module
LEGACY_PREFIX = "MATMCP_"           # material of the removed Convert Material button
ORIGINAL_SCAN = "Original Scan"     # name of the slider's Value node
UV_INDEX = "uv_index"               # face attribute: the face's material slot
UV_NORMAL = "uv_normal"             # the user's non-overlapping bake UV (never touched here)


def material_name(obj):
    return f"{PREFIX}{obj.name}"


def _is_ours(mat):
    return bool(mat.get(MAT_TAG) or mat.get(BAKED_TAG)) or mat.name.startswith(LEGACY_PREFIX)


def _base_color_image(mat):
    """The Image Texture feeding the Principled BSDF's Base Color (through
    reroutes/mix nodes), or None."""
    if mat is None or not mat.use_nodes:
        return None
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        return None
    todo, seen = [bsdf.inputs["Base Color"]], set()
    while todo:
        sock = todo.pop()
        for link in sock.links:
            node = link.from_node
            if node.type == 'TEX_IMAGE' and node.image:
                return node
            if node.name not in seen:
                seen.add(node.name)
                todo.extend(node.inputs)
    return None


def original_textures(obj):
    """[(slot index, Image Texture node)] of the scan materials. Materials built by the
    addon (also another object's, e.g. on a duplicate) are never originals."""
    return [(i, t) for i, s in enumerate(obj.material_slots)
            if s.material and not _is_ours(s.material) and (t := _base_color_image(s.material))]


def _scan_uv(obj, warnings):
    """The scan's UV map - the mesh's only one besides the addon's UV_camN and the
    bake target uv_normal."""
    uvs = [u for u in obj.data.uv_layers if not u.name.startswith("UV_cam") and u.name != UV_NORMAL]
    if not uvs:
        warnings.append("Mesh has no UV map - original scan textures skipped")
        return None
    if len(uvs) > 1:
        uv = next((u for u in uvs if u.active_render), uvs[0])
        warnings.append(f"Mesh has {len(uvs)} UV maps - the original scan uses '{uv.name}'")
        return uv.name
    return uvs[0].name


def _build_original(b, uv_name, textures):
    """ORIGINAL MATERIALS frame (hand-arranged layout). Returns its color output."""
    f = b.frame("ORIGINAL MATERIALS", (-1730, 411))
    if not textures or uv_name is None:
        rgb = b.n("ShaderNodeRGB", (851, -36), f, label="No scan texture")
        rgb.outputs[0].default_value = (0.8, 0.8, 0.8, 1.0)
        return rgb.outputs[0]
    uv = b.n("ShaderNodeUVMap", (29, -572), f, uv_map=uv_name)
    attr = b.n("ShaderNodeAttribute", (36, -428), f, attribute_type='GEOMETRY', attribute_name=UV_INDEX)
    uv_out = b.n("NodeReroute", (576, -600), f)
    b.link(uv.outputs["UV"], uv_out.inputs[0])
    uv_out = uv_out.outputs[0]
    slot_out = b.n("NodeReroute", (711, -449), f)
    b.link(attr.outputs["Fac"], slot_out.inputs[0])
    slot_out = slot_out.outputs[0]
    col = None
    for row, (slot, src) in enumerate(textures):
        y = -36 - row * 300
        tex = b.n("ShaderNodeTexImage", (851, y), f, image=src.image,
                  interpolation=src.interpolation, extension=src.extension)
        tex.name = tex.label = f"Slot {slot}"
        b.link(uv_out, tex.inputs["Vector"])
        if col is None:
            col = tex.outputs["Color"]
            continue
        hit = b.math("COMPARE", slot_out, float(slot), (1151, y + 100), f"uv_index = {slot}", f)
        hit.node.inputs[2].default_value = 0.5
        mix = b.n("ShaderNodeMix", (1351, y), f, data_type="RGBA")
        b.link(hit, mix.inputs[0])
        b.link(col, gn_builder._sock(mix.inputs, "A"))
        b.link(tex.outputs["Color"], gn_builder._sock(mix.inputs, "B"))
        col = gn_builder._sock(mix.outputs, "Result")
    return col


def _weighted(b, f, ws, cams, x, label):
    """sum(w_i * photo_i) / sum(w_i) over one layer's cameras; returns (color, sum)."""
    acc, tot = None, None
    for w, (i, tex) in zip(ws, cams):
        y = -50 - (i - 1) * 300
        sc = b.vmath("SCALE", tex.outputs["Color"], None, (x, y), f)
        b.link(w, sc.inputs["Scale"])
        acc = sc.outputs[0] if acc is None else b.vmath("ADD", acc, sc.outputs[0], (x + 250, y), f).outputs[0]
        tot = w if tot is None else b.math("ADD", tot, w, (x + 250, y - 150), parent=f)
    inv = b.math("DIVIDE", 1.0, b.math("MAXIMUM", tot, 1e-6, (x + 450, -950), parent=f),
                 (x + 600, -950), parent=f)
    norm = b.vmath("SCALE", acc, None, (x + 750, -450), f)
    norm.label = label
    b.link(inv, norm.inputs["Scale"])
    return norm.outputs[0], tot


def _build_projection(b, n):
    """PROJECTION frame: cameras 1-3 weighted by VCMix, 4-6 by VCMix2 (each normalised).
    Cameras 1-3 win (VCMix on top): s1 = sum(VCMix), w2 = sum(VCMix2) x (1 - s1), color =
    (s1 x cams 1-3 + w2 x cams 4-6) / (s1 + w2) - no dark seams at soft edges. Blend
    mask = VCMix alpha x (s1 + w2): where no camera covers the face, the scan shows.
    Returns (color, blend mask)."""
    f = b.frame("PROJECTION", (-1730, 1500))
    texs = []
    for i in range(1, n + 1):
        y = -50 - (i - 1) * 300
        uvn = b.n("ShaderNodeUVMap", (50, y), f, uv_map=f"UV_cam{i}")
        uvn.name = f"CamUV_{i}"
        tex = b.n("ShaderNodeTexImage", (300, y), f, extension="CLIP")
        tex.name = f"CamTex_{i}"
        tex.label = f"Cam {i}"
        b.link(uvn.outputs["UV"], tex.inputs["Vector"])
        texs.append((i, tex))
    layers = []
    for layer, name in enumerate(gn_builder.LAYERS):
        cams = texs[layer * 3:layer * 3 + 3]
        if not cams:
            break
        vc = b.n("ShaderNodeVertexColor", (350, -950 - layer * 250), f, layer_name=name)
        sepc = b.n("ShaderNodeSeparateColor", (550, -950 - layer * 250), f)
        b.link(vc.outputs["Color"], sepc.inputs[0])
        ws = [sepc.outputs[k] for k in ("Red", "Green", "Blue")]
        col, _tot = _weighted(b, f, ws, cams, 700 + layer * 1000, f"{name} cameras")
        layers.append((col, vc, ws))
    color, vc1, ws1 = layers[0]
    mask = vc1.outputs["Alpha"]
    if len(layers) > 1:
        col2, _vc2, ws2 = layers[1]

        def layer_sum(ws, y, label):
            t = b.math("ADD", b.math("ADD", ws[0], ws[1], (2700, y), parent=f), ws[2], (2850, y), label, f)
            t.node.use_clamp = True
            return t

        s1 = layer_sum(ws1, -1200, "1-3 Cover")
        s2 = layer_sum(ws2, -1400, "4-6 Weight")
        w2 = b.math("MULTIPLY", s2, b.math("SUBTRACT", 1.0, s1, (3050, -1300), parent=f),
                    (3200, -1400), "4-6 Share (1-3 on top)", f)
        both = b.math("ADD", s1, w2, (3350, -1300), "Cameras Cover", f)
        both.node.use_clamp = True
        share = b.math("DIVIDE", w2, b.math("MAXIMUM", both, 1e-6, (3500, -1300), parent=f),
                       (3650, -1300), "4-6 Fraction", f)
        mx = b.n("ShaderNodeMix", (3800, -450), f, data_type="RGBA")
        mx.label = "VCMix on top"
        b.link(share, mx.inputs[0])
        b.link(color, gn_builder._sock(mx.inputs, "A"))
        b.link(col2, gn_builder._sock(mx.inputs, "B"))
        color = gn_builder._sock(mx.outputs, "Result")
        # nothing covers the face (1-3 cleared, 4-6 do not see it): the scan shows
        mask = b.math("MULTIPLY", mask, both, (3800, -1200), "Blend Mask", f)
    return color, mask


def build_material(obj, warnings=None):
    """(Re)build MCP_<name> in place. Keeps the Original Scan value."""
    warnings = [] if warnings is None else warnings
    name = material_name(obj)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat[MAT_TAG] = True
    mat[MAT_VERSION_KEY] = MAT_VERSION
    mat.use_nodes = True
    nt = mat.node_tree
    old = nt.nodes.get(ORIGINAL_SCAN)
    scan = old.outputs[0].default_value if old else 0.0
    nt.nodes.clear()
    b = B(nt)
    out = b.n("ShaderNodeOutputMaterial", (1200, 0))
    bsdf = b.n("ShaderNodeBsdfPrincipled", (900, 0))
    b.link(bsdf.outputs[0], out.inputs["Surface"])

    original = _build_original(b, _scan_uv(obj, warnings), original_textures(obj))
    projected, mask = _build_projection(b, slot_count(data(obj)))

    # projection weight = mask x (1 - Original Scan)
    f = b.frame("BLEND", (-150, 250))
    val = b.n("ShaderNodeValue", (20, -40), f, label=ORIGINAL_SCAN)
    val.name = ORIGINAL_SCAN
    val.outputs[0].default_value = scan
    inv = b.math("SUBTRACT", 1.0, val.outputs[0], (220, -40), "Projection", f)
    inv.node.use_clamp = True
    w = b.math("MULTIPLY", mask, inv, (420, -40), "x Blend Mask", f)
    mix = b.n("ShaderNodeMix", (620, -40), f, data_type="RGBA")
    b.link(w, mix.inputs[0])
    b.link(original, gn_builder._sock(mix.inputs, "A"))
    b.link(projected, gn_builder._sock(mix.inputs, "B"))
    b.link(gn_builder._sock(mix.outputs, "Result"), bsdf.inputs["Base Color"])
    return mat


def _material_ok(mat, n):
    """Built for exactly `n` cameras (CamTex_1..n, no CamTex_n+1)."""
    if mat is None or not mat.node_tree or not mat.node_tree.nodes.get(ORIGINAL_SCAN):
        return False
    nodes = mat.node_tree.nodes
    return (mat.get(MAT_VERSION_KEY) == MAT_VERSION
            and all(nodes.get(f"CamTex_{i}") for i in range(1, n + 1)) and not nodes.get(f"CamTex_{n + 1}"))


def ensure_material(obj):
    """Each object owns MCP_<name>. A duplicated object carries the original's
    pointer, so a material with another name is not reused."""
    mat = bpy.data.materials.get(material_name(obj))
    if not _material_ok(mat, slot_count(data(obj))):
        mat = build_material(obj)
    data(obj).material = mat
    return mat


def _move_slot_to_top(obj, index):
    obj.active_material_index = index
    with bpy.context.temp_override(object=obj, active_object=obj):
        for _ in range(index):
            bpy.ops.object.material_slot_move(direction='UP')   # also remaps the faces


def _remove_legacy(obj):
    """Drop the Convert Material button's modifier (and its group once unused)."""
    for m in [m for m in obj.modifiers if m.type == 'NODES' and m.node_group
              and m.node_group.name == gn_builder.MATINDEX]:
        obj.modifiers.remove(m)
    ng = bpy.data.node_groups.get(gn_builder.MATINDEX)
    if ng and ng.users == 0:
        bpy.data.node_groups.remove(ng)


def place_material(obj):
    """MCP_<name> in slot 1; the other slots move down. A MATMCP_ slot is taken over
    in place and the MATMCP_ material deleted."""
    name = material_name(obj)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat[MAT_TAG] = True
    slots = obj.material_slots
    idx = next((i for i, s in enumerate(slots) if s.material == mat), None)
    legacy = bpy.data.materials.get(LEGACY_PREFIX + obj.name)
    if legacy:
        for i, s in enumerate(slots):
            if s.material == legacy:
                if idx is None:
                    s.material, idx = mat, i
                else:
                    s.material = None
    if idx is None:
        obj.data.materials.append(mat)
        idx = len(slots) - 1
    if idx:
        _move_slot_to_top(obj, idx)
    if legacy and legacy.users == 0:
        bpy.data.materials.remove(legacy)
    obj.active_material_index = 0
    return mat


def write_uv_index(obj):
    """uv_index (face, int) = the face's material slot, read by ORIGINAL MATERIALS.
    Faces on slot 1 - the combined material, e.g. after a bake - keep their value."""
    me = obj.data
    n = len(me.polygons)
    mi = np.empty(n, dtype=np.int32)
    me.polygons.foreach_get("material_index", mi)
    old = np.zeros(n, dtype=np.int32)
    attr = me.attributes.get(UV_INDEX)
    if attr is not None and attr.domain == 'FACE' and attr.data_type == 'INT':
        attr.data.foreach_get("value", old)
    elif attr is not None:          # e.g. the 2D corner version baked from the old modifier
        if attr.domain == 'CORNER' and attr.data_type == 'FLOAT2':
            buf = np.empty(len(me.loops) * 2, dtype=np.float32)
            attr.data.foreach_get("vector", buf)
            starts = np.empty(n, dtype=np.int32)
            me.polygons.foreach_get("loop_start", starts)
            old = buf[starts * 2].round().astype(np.int32)
        me.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = me.attributes.new(UV_INDEX, 'INT', 'FACE')
    attr.data.foreach_set("value", np.where(mi == 0, old, mi))


def combine_materials(obj):
    """Setup/Reload All step: one material for the scan and the projection.
    Returns a list of warning strings."""
    warnings = []
    _remove_legacy(obj)
    place_material(obj)
    write_uv_index(obj)
    data(obj).material = build_material(obj, warnings)
    return warnings


# ---------------------------------------------------------------- modifier

def get_modifier(obj):
    mod = obj.modifiers.get(MOD_NAME)
    if mod and mod.type == 'NODES':
        return mod
    for m in obj.modifiers:
        if m.type == 'NODES' and gn_builder.is_main(m.node_group):
            return m
    return None


KEEP_INPUTS = ("Mode", "Previous Bake", "Occlusion")
_OLD_NAMES = {"Previous Bake": "Original Blend"}    # before the 2026-09-24 rename


def _read_keep(mod):
    keep = {}
    if mod and mod.node_group:
        for k in KEEP_INPUTS:
            for name in (k, _OLD_NAMES.get(k)):
                try:
                    keep[k] = get_input(mod, name)
                    break
                except (KeyError, AttributeError, TypeError):
                    pass
    return keep


def _write_keep(mod, keep):
    for k, v in keep.items():
        try:
            if get_input(mod, k) != v:
                set_input(mod, k, v)
        except (KeyError, AttributeError, TypeError):
            pass


def _setup_objects(exclude=None):
    return [o for o in bpy.data.objects
            if o is not exclude and o.type == 'MESH' and data(o).is_setup and get_modifier(o)]


def ensure_modifier(obj):
    """The object's modifier on the group for its slot count, with the groups up to date.
    The groups are shared (and all use the single-camera group): a rebuild resets every
    object's inputs and breaks its lens drivers, so every group in use is rebuilt and all
    other set-up objects get their settings back and are rewired too."""
    mod = get_modifier(obj)
    n = slot_count(data(obj))
    others = _setup_objects(exclude=obj)
    counts = {n} | {slot_count(data(o)) for o in others}
    rebuilt = not all(gn_builder.up_to_date(k) for k in counts)
    others = [(o, _read_keep(get_modifier(o))) for o in others] if rebuilt else []
    keep = _read_keep(mod)
    for k in counts - {n}:
        gn_builder.ensure_node_groups(k)
    main = gn_builder.ensure_node_groups(n)
    if mod is None:
        mod = obj.modifiers.new(MOD_NAME, 'NODES')
        rebuilt = True      # a fresh modifier's Mode menu needs the update below too
    if mod.node_group != main:
        mod.node_group = main
        rebuilt = True      # other group: its Mode menu needs the update below too
    if rebuilt:
        # a rebuilt group's Mode menu has no items until the next update - restoring
        # "Sharp" before that fails silently
        bpy.context.view_layer.update()
    _write_keep(mod, keep)
    for o, k in others:     # groups are up to date now, so this does not recurse
        _write_keep(get_modifier(o), k)
        apply_slots(o, bpy.context.scene)
    return mod


def ident(ng, name):
    for it in ng.interface.items_tree:
        if getattr(it, "in_out", None) == "INPUT" and it.name == name:
            return it.identifier
    raise KeyError(name)


def required_inputs(n):
    return ("Material", "Mode", "Previous Bake", "Occlusion") + tuple(
        f"{k} {i}" for i in range(1, n + 1)
        for k in ("Camera", "Focal", "Sensor", "Aspect", "Ortho", "Ortho Scale")) + tuple(
        f"UV Shift Cam{i}" for i in range(1, n + 1))


def missing_inputs(ng, n):
    """Inputs apply_slots needs (for `n` slots) but a hand-edited group no longer has."""
    names = {it.name for it in ng.interface.items_tree
             if getattr(it, "in_out", None) == "INPUT"}
    return [k for k in required_inputs(n) if k not in names]


def input_socket(mod, name):
    """Blender 5.2: modifier inputs live on mod.properties.inputs.<Socket_N>.
    Returns the RNA holder with `.value`."""
    return getattr(mod.properties.inputs, ident(mod.node_group, name))


def get_input(mod, name):
    return input_socket(mod, name).value


def set_input(mod, name, value):
    input_socket(mod, name).value = value


def input_path(mod, name):
    return f'modifiers["{mod.name}"].properties.inputs.{ident(mod.node_group, name)}.value'


def _set_driver(obj, path, cam, prop):
    if cam is None:
        return
    d = obj.driver_add(path).driver
    d.type = "AVERAGE"
    var = d.variables.new()
    var.name = "v"
    var.targets[0].id_type = "CAMERA"
    var.targets[0].id = cam.data
    var.targets[0].data_path = prop


def remove_drivers(obj, mod):
    """Remove every driver on this modifier, including stale ones whose socket
    identifier no longer exists after a node-group rebuild."""
    ad = obj.animation_data
    if not ad:
        return
    prefix = f'modifiers["{mod.name}"]'
    for fc in [fc for fc in ad.drivers if fc.data_path.startswith(prefix)]:
        ad.drivers.remove(fc)


# ---------------------------------------------------------------- pixel shift

def cam_data(cam):
    return cam.multicamproject_camera


def is_global(cam):
    return cam is not None and cam_data(cam).is_global


def shift_holder(obj, cam, create=False):
    """What holds the shift of `cam` for `obj` (has `.shift`): the camera itself when it is
    global (one shift for every object), else the object's shift item."""
    if is_global(cam):
        return cam_data(cam)
    return shift_item(obj, cam, create)


def users(cam):
    """Set-up objects that have `cam` in one of their slots."""
    return [o for o in bpy.data.objects
            if o.type == 'MESH' and data(o).is_setup and cam in get_slots(data(o))]


def push_global_shift(cam):
    """A global camera's shift into every object that slots it, and its background offset."""
    shift = tuple(cam_data(cam).shift)
    for o in users(cam):
        mod = get_modifier(o)
        if mod and mod.node_group:
            set_input(mod, f"UV Shift Cam{slot_of(o, cam)}", shift)
    set_camera_offset(cam, shift)


def set_global(cam, value, obj, scene):
    """Mark `cam` global / object-attached. The shift carries over from / to `obj`'s own
    shift, so the photo does not jump; every object using the camera is rewired."""
    if is_global(cam) == value:
        return
    cd = cam_data(cam)
    if value:
        it = shift_item(obj, cam) if obj else None
        cd.is_global = True
        if it:
            cd["shift"] = tuple(it.shift)     # raw write: rewire below pushes it
    else:
        cd.is_global = False
        if obj:
            shift_item(obj, cam, create=True)["shift"] = tuple(cd.shift)
    for o in users(cam):
        apply_slots(o, scene)
    holder = shift_holder(obj, cam) if obj else cd
    set_camera_offset(cam, holder.shift)


def shift_item(obj, cam, create=False):
    """The object's remembered pixel shift for `cam` (initialised to 0,0)."""
    d = data(obj)
    for it in d.shifts:
        if it.camera == cam:
            return it
    if not create or cam is None:
        return None
    it = d.shifts.add()
    it.camera = cam
    return it


def migrate_shifts(obj, scene):
    """One-time: shifts used to be stored in pixels, now in UV range (-1..1).
    Writes the raw ID property so no update callback touches the cameras."""
    d = data(obj)
    if d.shift_version >= 1:
        return
    for it in d.shifts:
        if it.camera is None:
            continue
        img = cam_image(it.camera)
        if image_ok(img) and img.size[0] and img.size[1]:
            w, h = img.size
        else:
            w, h = scene.render.resolution_x, scene.render.resolution_y
        px = it.shift
        it["shift"] = (max(-1.0, min(1.0, px[0] / w)), max(-1.0, min(1.0, px[1] / h)))
    d.shift_version = 1


def migrate_material_names():
    """One-time (2026-09-25): the projection material MAT_<obj> is now MCP_<obj>, MAT_ is
    the baked export material. An untagged MCP_ already holding the name steps aside -
    the tagged (addon-built) one wins."""
    for mat in [m for m in bpy.data.materials
                if m.get(MAT_TAG) and m.name.startswith(OLD_PREFIX) and not m.library]:
        new = PREFIX + mat.name[len(OLD_PREFIX):]
        other = bpy.data.materials.get(new)
        if other is not None:
            if other.get(MAT_TAG):
                continue        # already migrated - the MAT_ one is a stale copy
            other.name = new + ".old"
        mat.name = new
    ng = bpy.data.node_groups.get(gn_builder.MATINDEX)
    if ng and ng.users == 0:
        bpy.data.node_groups.remove(ng)


def migrate_all():
    scene = bpy.context.scene
    migrate_material_names()
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and data(obj).is_setup and data(obj).shift_version < 1:
            migrate_shifts(obj, scene)
            if get_modifier(obj):
                apply_slots(obj, scene)


def set_camera_offset(cam, shift):
    """The shift is in UV units (-1..1 = one full photo width/height). Blender
    measures the background offset in frame width (X) / frame height (Y) -
    measured live in 5.2 - and the photo fills the frame, so it is used as-is."""
    bg = bg_entry(cam)
    if bg:
        bg.offset = tuple(shift)


def push_shift(obj, item, scene, to_camera):
    """Write one shift item into the modifier input of its slot and, optionally,
    into the camera's background offset."""
    cam = item.camera
    if cam is None:
        return
    slot = slot_of(obj, cam)
    mod = get_modifier(obj)
    if slot and mod and mod.node_group:
        set_input(mod, f"UV Shift Cam{slot}", tuple(item.shift))
    if to_camera:
        set_camera_offset(cam, item.shift)


def min_coverage(d):
    return {k: v for k, _l, v in COVERAGE_FILTERS}[d.coverage_filter]


def passes(d, item):
    return item.coverage >= min_coverage(d)


def display_order(obj):
    """Camera list items for the UI: the slots first, then the cameras that pass the
    coverage filter, most coverage first."""
    d = data(obj)
    slots = get_slots(d)
    items = [it for it in d.cameras if it.camera]
    top = sorted((it for it in items if it.camera in slots), key=lambda it: slots.index(it.camera))
    rest = sorted((it for it in items if it.camera not in slots and passes(d, it)),
                  key=lambda it: (-it.coverage, it.camera.name.lower()))
    return top, rest


def view_axis(cam):
    """Unit direction the camera looks along (world)."""
    v = -cam.matrix_world.to_3x3().col[2]
    return v.normalized() if v.length else v


def axis_pick(d, n):
    """Cameras for slots 1..n: Camera 1 looks most along +-Y, 2 along +-X, 3 along +-Z (a tie
    goes to more coverage), 4..n the most coverage left. Only cameras with an image that pass
    the coverage filter; when too few pass, the next best by coverage fill up.
    Returns (cameras, warning or '')."""
    items = [it for it in d.cameras if it.camera and image_ok(cam_image(it.camera))]
    ok = [it for it in items if passes(d, it)]
    picked, below = [], 0

    def best_along(pool, axis):
        free = [it for it in pool if it.camera not in picked]
        if not free:
            return None
        return max(free, key=lambda it: (round(abs(view_axis(it.camera).dot(axis)), 3), it.coverage))

    for axis in AXES[:n]:
        it = best_along(ok, axis)
        if it is None:          # none left that passes: the axis rule still holds below it
            it = best_along(items, axis)
            below += it is not None
        if it is not None:
            picked.append(it.camera)
    for pool in (ok, items):    # slots 4-6: the most coverage left
        for it in sorted(pool, key=lambda it: -it.coverage):
            if len(picked) >= n:
                break
            if it.camera not in picked:
                picked.append(it.camera)
                below += pool is items
    warning = (f"Only {len(ok)} camera(s) pass the coverage filter - {below} picked below it"
               if below else "")
    return (picked + [None] * n)[:n], warning


def measured(d):
    """False when the list was made before coverage was stored (every item at 0)."""
    return not len(d.cameras) or any(it.coverage > 0 for it in d.cameras)


def rescore(obj, scene):
    """Score every scene camera against the object and rebuild the camera list (most
    coverage first). Photos are not loaded. Returns the set of cameras that see it."""
    d = data(obj)
    if obj.mode == 'EDIT':
        obj.update_from_editmode()      # score the mesh as edited, not as last left
    co_w, nr_w = _object_samples(obj)
    removed = {r.camera for r in d.removed if r.camera}
    scored = []
    if co_w is not None:
        scored = [(*score_camera(c, co_w, nr_w, scene), c) for c in scene_cameras(scene)
                  if c not in removed]
    scored = sorted([sc for sc in scored if sc[0] >= SCORE_MIN], key=lambda sc: -sc[1])
    # every camera that sees the object is kept; the coverage filter only hides / skips
    d.cameras.clear()
    for sc, cov, c in scored:
        it = d.cameras.add()
        it.camera = c
        it.score = sc
        it.coverage = cov
    return {c for _s, _cov, c in scored}


def remove_camera(obj, cam):
    """Take `cam` out of the object's list for good (until restored): Reload All and the
    coverage refresh skip it. A slot holding it keeps it."""
    d = data(obj)
    if not any(r.camera == cam for r in d.removed):
        d.removed.add().camera = cam
    for i, it in enumerate(d.cameras):
        if it.camera == cam and cam not in get_slots(d):
            d.cameras.remove(i)
            break


def restore_cameras(obj, scene):
    """Bring every removed camera back into the list (measured again)."""
    data(obj).removed.clear()
    rescore(obj, scene)


def auto_pick(obj, scene):
    """Measure again, then slots by axis_pick; afterwards the slots count as not picked
    by hand."""
    d = data(obj)
    rescore(obj, scene)
    cams, warning = axis_pick(d, slot_count(d))
    set_slots(d, cams)
    d.user_picked = False
    apply_slots(obj, scene)
    return warning


def apply_slots(obj, scene):
    """Push slots 1/2/3 into the modifier (camera, lens drivers, aspect) and material."""
    d = data(obj)
    migrate_shifts(obj, scene)
    mod = ensure_modifier(obj)
    mat = ensure_material(obj)
    set_input(mod, "Material", mat)
    remove_drivers(obj, mod)
    for i, cam in enumerate(get_slots(d), 1):
        set_input(mod, f"Camera {i}", cam)
        _set_driver(obj, input_path(mod, f"Focal {i}"), cam, "lens")
        _set_driver(obj, input_path(mod, f"Sensor {i}"), cam, "sensor_width")
        _set_driver(obj, input_path(mod, f"Ortho {i}"), cam, "type")    # enum -> 0 persp, 1 ortho
        _set_driver(obj, input_path(mod, f"Ortho Scale {i}"), cam, "ortho_scale")
        img = cam_image(cam) if cam else None
        set_input(mod, f"Aspect {i}", image_aspect(img, scene))
        it = shift_holder(obj, cam, create=True) if cam else None
        set_input(mod, f"UV Shift Cam{i}", tuple(it.shift) if it else (0.0, 0.0))
        tex = mat.node_tree.nodes[f"CamTex_{i}"]
        tex.image = img
        tex.label = f"Cam {i}: {cam.name}" if cam else f"Cam {i}"
    obj.update_tag()


def assign_slot(obj, cam, slot, scene):
    """Put `cam` in slot 1..slot_count. A camera lives in one slot only - if it already sat in
    another slot, the two slots swap."""
    d = data(obj)
    slots = get_slots(d)
    idx = slot - 1
    if cam in slots:
        k = slots.index(cam)
        if k != idx:
            slots[k] = slots[idx]
    slots[idx] = cam
    set_slots(d, slots)
    d.user_picked = True
    apply_slots(obj, scene)


def slot_of(obj, cam):
    slots = get_slots(data(obj))
    return slots.index(cam) + 1 if cam in slots else 0


def change_slot_count(obj, scene):
    """After the Cameras dropdown: empty new slots get the best-scoring unused cameras
    with an image, then the modifier moves to the group for the new count and the
    material is rebuilt for it. Slots above the count keep their camera."""
    d = data(obj)
    if not d.is_setup:
        return
    slots = get_slots(d)
    free = [it.camera for it in d.cameras            # coverage order
            if it.camera and it.camera not in slots and passes(d, it)
            and image_ok(cam_image(it.camera))]
    for i, cam in enumerate(slots):
        if cam is None and free:
            slots[i] = free.pop(0)
    set_slots(d, slots)
    apply_slots(obj, scene)


# ---------------------------------------------------------------- vertex paint

_RGB = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
SLOT_COLORS = {i: _RGB[(i - 1) % 3] for i in range(1, 7)}  # R/G/B of VCMix (1-3), VCMix2 (4-6)


def slot_layer(slot):
    """Color layer a slot paints into."""
    return gn_builder.LAYERS[(slot - 1) // 3]
PAINT_BRUSH = "brushes/essentials_brushes-mesh_vertex.blend/Brush/Paint Hard"


def ensure_paint_layer(obj, layer="VCMix"):
    """Painting edits the mesh's own VCMix / VCMix2 - the layers Previous Bake blends in,
    since the modifier recomputes them. Without one (never baked), each starts as a copy
    of what the modifier shows now. Previous Bake goes to 1 so strokes show 1:1.
    Object mode only. Returns a list of info strings."""
    info = []
    me = obj.data
    # both layers at once (Previous Bake blends every layer the mesh has); read what the
    # modifier shows before adding anything to the mesh
    missing = [n for n in gn_builder.LAYERS[:1 + (slot_count(data(obj)) > 3)]
               if me.color_attributes.get(n) is None]
    copies = {}
    if missing:
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        for name in missing:
            src = ev.color_attributes.get(name)
            if src is not None:
                buf = np.empty(len(src.data) * 4, dtype=np.float32)
                src.data.foreach_get("color", buf)
                copies[name] = (src.domain, buf)
    for name in missing:
        domain, buf = copies.get(name, ('CORNER', None))
        new = me.color_attributes.new(name, 'FLOAT_COLOR', domain)
        if buf is not None and len(buf) == len(new.data) * 4:
            new.data.foreach_set("color", buf)
            info.append(f"{name} copied into the mesh to paint on")
    base = me.color_attributes.get(layer)
    me.color_attributes.active_color = base
    mod = get_modifier(obj)
    if mod and get_input(mod, "Previous Bake") < 1.0:
        set_input(mod, "Previous Bake", 1.0)
        info.append("Previous Bake set to 1 so the paint shows")
    return info


def set_paint_brush(context, color=None, blend='MIX'):
    """Vertex Paint brush for VCMix: a Draw brush (Shift+drag smooths, Blender's own
    keymap) with the camera's color and the given blend."""
    vp = context.tool_settings.vertex_paint
    if vp.brush is None or vp.brush.vertex_brush_type != 'DRAW':
        bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS',
                                     relative_asset_identifier=PAINT_BRUSH)
    br = vp.brush
    br.blend = blend
    if color is not None:
        br.color = color
        vp.unified_paint_settings.color = color     # used when the color is unified


# ---------------------------------------------------------------- reload all

def refresh(obj, scene):
    """Reload All: images -> clipping -> scoring -> slot check -> material -> rewire.
    Returns a list of warning strings."""
    d = data(obj)
    warnings = []

    cams = scene_cameras(scene)
    folder, listing = _folder_listing(d.image_folder)
    if d.image_folder and not listing:
        warnings.append(f"Image folder not found or empty: {d.image_folder}")
    for cam in cams:
        if is_global(cam):      # image and clipping belong to the global setup
            continue
        resolve_cam_image(cam, folder, listing)
        cam.data.clip_start = d.clip_start
        cam.data.clip_end = d.clip_end

    sees = rescore(obj, scene)
    slots = get_slots(d)
    if not d.user_picked:
        slots, warning = axis_pick(d, slot_count(d))
        if warning:
            warnings.append(warning)
    else:
        for i, cam in enumerate(slots):
            if cam is not None and cam in sees and image_ok(cam_image(cam)):
                continue        # the user's pick: kept, whatever its coverage
            free = [it.camera for it in d.cameras if it.camera not in slots and passes(d, it)
                    and image_ok(cam_image(it.camera))]
            new = free[0] if free else None
            if cam is not None:
                why = "no longer sees the object" if cam not in sees else "has no loaded image"
                warnings.append(f"Camera {i + 1}: '{cam.name}' {why} -> "
                                f"{new.name if new else 'left empty'}")
            slots[i] = new
    set_slots(d, slots)

    missing = [it.camera.name for it in d.cameras
               if passes(d, it) and not image_ok(cam_image(it.camera))]
    if missing:
        warnings.append(f"{len(missing)} camera(s) without image: {', '.join(missing[:5])}"
                        + ("..." if len(missing) > 5 else ""))
    warnings += combine_materials(obj)
    apply_slots(obj, scene)
    return warnings


def setup(obj, scene):
    d = data(obj)
    mod = ensure_modifier(obj)
    if not d.is_setup:
        set_input(mod, "Mode", "Sharp")
        d.user_picked = False
        if not len(d.shifts):
            d.shift_version = 1
    d.is_setup = True
    return refresh(obj, scene)
