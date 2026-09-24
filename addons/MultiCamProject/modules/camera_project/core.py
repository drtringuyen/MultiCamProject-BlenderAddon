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
MAX_SAMPLES = 4000      # vertices sampled for scoring


def data(obj):
    return obj.multicamproject_cam


def get_slots(d):
    return [d.slot_1, d.slot_2, d.slot_3]


def set_slots(d, cams):
    d.slot_1, d.slot_2, d.slot_3 = cams


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
    """Same projection as the GN group: coverage (share of vertices in frame)
    x mean facing of those vertices."""
    cd = cam.data
    if cd.type != 'PERSP':
        return 0.0
    cmi = np.array(cam.matrix_world.inverted_safe(), dtype=np.float32)
    p = co_w @ cmi[:3, :3].T + cmi[:3, 3]
    depth = -p[:, 2]
    front = depth > 1e-6
    if not front.any():
        return 0.0
    scale = cd.lens / cd.sensor_width
    aspect = image_aspect(cam_image(cam), scene, load=False)
    d = np.where(front, depth, 1.0)
    u = p[:, 0] / d * scale + 0.5
    v = p[:, 1] / d * scale * aspect + 0.5
    inside = front & (u > 0) & (u < 1) & (v > 0) & (v < 1)
    if not inside.any():
        return 0.0
    to_cam = np.array(cam.matrix_world.translation, dtype=np.float32) - co_w[inside]
    to_cam /= np.maximum(np.linalg.norm(to_cam, axis=1, keepdims=True), 1e-9)
    facing = np.clip((nr_w[inside] * to_cam).sum(axis=1), 0.0, None).mean()
    return float(inside.mean() * facing)


def scene_cameras(scene):
    return [o for o in scene.objects if o.type == 'CAMERA']


# ---------------------------------------------------------------- material
# One material per object, MAT_<name>, in material slot 1. Three frames:
#   ORIGINAL MATERIALS  the scan's Base Color images, picked per face by uv_index
#   PROJECTION          Cam 1/2/3 on UV_cam1/2/3, weighted by VCMix
#   BLEND               Original Scan slider (0 = projection, 1 = scan), masked by VCMix alpha

MAT_TAG = "multicamproject_material"
LEGACY_PREFIX = "MATMCP_"           # material of the removed Convert Material button
ORIGINAL_SCAN = "Original Scan"     # name of the slider's Value node
UV_INDEX = "uv_index"               # face attribute: the face's material slot


def material_name(obj):
    return f"MAT_{obj.name}"


def _is_ours(mat):
    return bool(mat.get(MAT_TAG)) or mat.name.startswith(LEGACY_PREFIX)


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
    """The scan's UV map - the mesh's only one besides the addon's UV_camN."""
    uvs = [u for u in obj.data.uv_layers if not u.name.startswith("UV_cam")]
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


def _build_projection(b):
    """PROJECTION frame: the 3 camera photos weighted by VCMix (normalised).
    Returns (color, VCMix alpha)."""
    f = b.frame("PROJECTION", (-1730, 1500))
    vc = b.n("ShaderNodeVertexColor", (350, -950), f, layer_name="VCMix")
    sepc = b.n("ShaderNodeSeparateColor", (550, -950), f)
    b.link(vc.outputs["Color"], sepc.inputs[0])
    ws = [sepc.outputs["Red"], sepc.outputs["Green"], sepc.outputs["Blue"]]
    acc = None
    for i in range(3):
        y = -50 - i * 300
        uvn = b.n("ShaderNodeUVMap", (50, y), f, uv_map=f"UV_cam{i + 1}")
        uvn.name = f"CamUV_{i + 1}"
        tex = b.n("ShaderNodeTexImage", (300, y), f, extension="CLIP")
        tex.name = f"CamTex_{i + 1}"
        tex.label = f"Cam {i + 1}"
        b.link(uvn.outputs["UV"], tex.inputs["Vector"])
        sc = b.vmath("SCALE", tex.outputs["Color"], None, (700, y), f)
        b.link(ws[i], sc.inputs["Scale"])
        acc = sc.outputs[0] if acc is None else b.vmath("ADD", acc, sc.outputs[0], (950, -150 - i * 200), f).outputs[0]
    tot = b.math("ADD", b.math("ADD", ws[0], ws[1], (800, -950), parent=f), ws[2], (950, -950), parent=f)
    inv = b.math("DIVIDE", 1.0, b.math("MAXIMUM", tot, 1e-6, (1100, -950), parent=f), (1250, -950), parent=f)
    norm = b.vmath("SCALE", acc, None, (1450, -450), f)
    b.link(inv, norm.inputs["Scale"])
    return norm.outputs[0], vc.outputs["Alpha"]


def build_material(obj, warnings=None):
    """(Re)build MAT_<name> in place. Keeps the Original Scan value."""
    warnings = [] if warnings is None else warnings
    name = material_name(obj)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat[MAT_TAG] = True
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
    projected, mask = _build_projection(b)

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


def _material_ok(mat):
    return mat is not None and mat.node_tree and mat.node_tree.nodes.get(ORIGINAL_SCAN) and all(
        mat.node_tree.nodes.get(f"CamTex_{i}") for i in (1, 2, 3))


def ensure_material(obj):
    """Each object owns MAT_<name>. A duplicated object carries the original's
    pointer, so a material with another name is not reused."""
    mat = bpy.data.materials.get(material_name(obj))
    if not _material_ok(mat):
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
    """MAT_<name> in slot 1; the other slots move down. A MATMCP_ slot is taken over
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
        if m.type == 'NODES' and m.node_group and m.node_group.name == gn_builder.MAIN:
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


def ensure_modifier(obj):
    """The object's GN-CameraProject modifier, with the groups up to date. The groups
    are shared: a rebuild resets every object's inputs and breaks its lens drivers, so
    all other set-up objects get their settings back and are rewired too."""
    mod = get_modifier(obj)
    rebuilt = not gn_builder.up_to_date()
    others = []
    if rebuilt:
        others = [(o, _read_keep(get_modifier(o))) for o in bpy.data.objects
                  if o is not obj and o.type == 'MESH' and data(o).is_setup and get_modifier(o)]
    keep = _read_keep(mod)
    main = gn_builder.ensure_node_groups()
    mod = mod or obj.modifiers.new(MOD_NAME, 'NODES')
    if mod.node_group != main:
        mod.node_group = main
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


REQUIRED_INPUTS = ("Material", "Mode", "Previous Bake", "Occlusion") + tuple(
    f"{k} {i}" for i in (1, 2, 3) for k in ("Camera", "Focal", "Sensor", "Aspect")) + tuple(
    f"UV Shift Cam{i}" for i in (1, 2, 3))


def missing_inputs(ng):
    """Inputs apply_slots needs but a hand-edited group no longer has."""
    names = {it.name for it in ng.interface.items_tree
             if getattr(it, "in_out", None) == "INPUT"}
    return [n for n in REQUIRED_INPUTS if n not in names]


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


def migrate_all():
    scene = bpy.context.scene
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


def display_order(obj):
    """Camera list items for the UI: slots 1/2/3 first, the rest alphabetical."""
    d = data(obj)
    slots = get_slots(d)
    items = [it for it in d.cameras if it.camera]
    top = sorted((it for it in items if it.camera in slots), key=lambda it: slots.index(it.camera))
    rest = sorted((it for it in items if it.camera not in slots), key=lambda it: it.camera.name.lower())
    return top, rest


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
        img = cam_image(cam) if cam else None
        set_input(mod, f"Aspect {i}", image_aspect(img, scene))
        it = shift_item(obj, cam, create=True) if cam else None
        set_input(mod, f"UV Shift Cam{i}", tuple(it.shift) if it else (0.0, 0.0))
        tex = mat.node_tree.nodes[f"CamTex_{i}"]
        tex.image = img
        tex.label = f"Cam {i}: {cam.name}" if cam else f"Cam {i}"
    obj.update_tag()


def assign_slot(obj, cam, slot, scene):
    """Put `cam` in slot 1..3. A camera lives in one slot only - if it already sat in
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
        resolve_cam_image(cam, folder, listing)
        cam.data.clip_start = d.clip_start
        cam.data.clip_end = d.clip_end

    co_w, nr_w = _object_samples(obj)
    scored = []
    if co_w is not None:
        scored = [(score_camera(c, co_w, nr_w, scene), c) for c in cams]
    scored = sorted([sc for sc in scored if sc[0] >= SCORE_MIN], key=lambda sc: -sc[0])

    d.cameras.clear()
    for s, c in scored:
        it = d.cameras.add()
        it.camera = c
        it.score = s

    hitting = [c for _, c in scored]
    usable = [c for c in hitting if image_ok(cam_image(c))]
    slots = get_slots(d)
    if not d.user_picked:
        slots = (usable + [None, None, None])[:3]
    else:
        for i, cam in enumerate(slots):
            if cam is not None and cam in usable:
                continue
            free = [c for c in usable if c not in slots]
            new = free[0] if free else None
            if cam is not None:
                why = "no longer sees the object" if cam not in hitting else "has no loaded image"
                warnings.append(f"Camera {i + 1}: '{cam.name}' {why} -> "
                                f"{new.name if new else 'left empty'}")
            slots[i] = new
    set_slots(d, slots)

    missing = [c.name for c in hitting if c not in usable]
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
