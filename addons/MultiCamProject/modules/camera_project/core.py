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

def build_material(obj):
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
    for i in range(3):
        uvn = b.n("ShaderNodeUVMap", (-900, 400 - i * 300), uv_map=f"UV_cam{i + 1}")
        uvn.name = f"CamUV_{i + 1}"
        tex = b.n("ShaderNodeTexImage", (-650, 400 - i * 300), extension="CLIP")
        tex.name = f"CamTex_{i + 1}"
        tex.label = f"Cam {i + 1}"
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


def _material_ok(mat):
    return mat is not None and mat.node_tree and all(
        mat.node_tree.nodes.get(f"CamTex_{i}") for i in (1, 2, 3))


def ensure_material(obj):
    """Each object owns MAT_<name>. A duplicated object carries the original's
    pointer, so a material with another name is not reused."""
    d = data(obj)
    mat = d.material
    if mat is None or mat.name != f"MAT_{obj.name}":
        mat = bpy.data.materials.get(f"MAT_{obj.name}")
    if not _material_ok(mat):
        mat = build_material(obj)
    d.material = mat
    return mat


# ---------------------------------------------------------------- modifier

def get_modifier(obj):
    mod = obj.modifiers.get(MOD_NAME)
    if mod and mod.type == 'NODES':
        return mod
    for m in obj.modifiers:
        if m.type == 'NODES' and m.node_group and m.node_group.name == gn_builder.MAIN:
            return m
    return None


_KEEP_INPUTS = ("Mode", "Original Blend", "Occlusion")


def ensure_modifier(obj):
    mod = get_modifier(obj)
    keep = {}
    if mod and mod.node_group:     # user settings survive a node-group rebuild
        for k in _KEEP_INPUTS:
            try:
                keep[k] = get_input(mod, k)
            except (KeyError, AttributeError):
                pass
    main = gn_builder.ensure_node_groups()
    mod = mod or obj.modifiers.new(MOD_NAME, 'NODES')
    if mod.node_group != main:
        mod.node_group = main
    for k, v in keep.items():
        try:
            if get_input(mod, k) != v:
                set_input(mod, k, v)
        except (KeyError, AttributeError, TypeError):
            pass
    return mod


def ident(ng, name):
    for it in ng.interface.items_tree:
        if getattr(it, "in_out", None) == "INPUT" and it.name == name:
            return it.identifier
    raise KeyError(name)


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


def _image_px(img, scene):
    if image_ok(img):
        w, h = img.size
        if w and h:
            return w, h
    r = scene.render
    return r.resolution_x, r.resolution_y


def uv_shift(cam, px, scene):
    """Pixels -> UV units (0..1 across the photo)."""
    w, h = _image_px(cam_image(cam), scene)
    return px[0] / w, px[1] / h


def bg_offset(cam, px, scene):
    """Pixels -> camera background-image offset. Blender measures the offset in
    frame width (X) and frame height (Y) - measured live in 5.2. The photo fills
    the frame (same aspect), so this equals the UV shift."""
    return uv_shift(cam, px, scene)


def set_camera_offset(cam, px, scene):
    bg = bg_entry(cam)
    if bg:
        bg.offset = bg_offset(cam, px, scene)


def push_shift(obj, item, scene, to_camera):
    """Write one shift item into the modifier input of its slot and, optionally,
    into the camera's background offset."""
    cam = item.camera
    if cam is None:
        return
    slot = slot_of(obj, cam)
    mod = get_modifier(obj)
    if slot and mod and mod.node_group:
        du, dv = uv_shift(cam, item.shift, scene)
        set_input(mod, f"UV Shift {slot}", (du, dv, 0.0))
    if to_camera:
        set_camera_offset(cam, item.shift, scene)


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
        du, dv = uv_shift(cam, it.shift, scene) if it else (0.0, 0.0)
        set_input(mod, f"UV Shift {i}", (du, dv, 0.0))
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
    """Reload All: images -> clipping -> scoring -> slot check -> rewire.
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
    apply_slots(obj, scene)
    return warnings


def setup(obj, scene):
    d = data(obj)
    mod = ensure_modifier(obj)
    ensure_material(obj)
    if not d.is_setup:
        set_input(mod, "Mode", "Sharp")
        d.user_picked = False
    d.is_setup = True
    return refresh(obj, scene)
