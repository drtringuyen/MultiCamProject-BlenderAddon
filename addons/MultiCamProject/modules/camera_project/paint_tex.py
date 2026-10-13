"""Paint Textures: the camera mix and the blend mask painted at 1K on uv_normal, so the
paint no longer follows the mesh's vertices. They work exactly as VCMix / VCMix2:

    CMX1_<name>  R, G, B = cameras 1-3, A = blend mask (1 projected, 0 baked)
    CMX2_<name>  R, G, B = cameras 4-6, A = stroke marker (paint_sync), kept at 0

Each starts as a copy of what VCMix / VCMix2 show now (rasterized on uv_normal), then MCP_
reads the texture instead of the vertex layer - the scene's Paint Textures toggle (a Value
node, no rebuild) switches back to the vertex layers. Painting a camera brings the
projection back (alpha 1), Erase lowers CMX1_ alpha (the baked scan shows), Shift+Erase
raises it again.

PNG files in the bake folder, named like the export (typed_name), pointers on the
object's multicamproject_bake. Created on the first stroke, saved with the .blend.

Undo: a Python write into an image is not undoable in Blender. write() first runs
Image > Invert (an image operator with undo: Blender keeps the image as it was), then
writes the real pixels. Ctrl+Z gives the old image back; a redo would give the inverted
one, so _on_redo puts the written pixels back when it sees that state. Writes asked for
inside an operator (Fill, Select & Set) wait for a timer (queue): the operator's own undo
step comes first, then the image step.
"""
import hashlib

import bpy
import numpy as np
from bpy.app.handlers import persistent

SIZE = 1024
KINDS = ("CMX1", "CMX2")
POINTER = {"MSK": "msk_image", "CMX1": "cmx1_image", "CMX2": "cmx2_image"}  # MSK_: before v2
LAYER = {"CMX1": "VCMix", "CMX2": "VCMix2"}
VERSION_KEY = "multicamproject_paint_version"   # on the image: 2 = alpha is the mask
VERSION = 2
DILATE = 8                          # pixels the copy grows into the UV space no face uses
TOGGLE = "Paint Textures"           # MCP_'s Value node: the scene toggle (0 = vertex layers only)
UV_NORMAL = "uv_normal"
BRUSH = "brushes/essentials_brushes-mesh_texture.blend/Brush/Paint Hard"
REDO_KEEP = 20                      # writes per image a redo can still put back


def node_name(kind):
    return f"{kind} Paint"


def enabled(scene=None):
    scene = scene or bpy.context.scene
    props = getattr(scene, "multicamproject_props", None)
    return bool(getattr(props, "paint_textures", False))


def kind_of_slot(slot):
    return "CMX1" if slot <= 3 else "CMX2"


def image_name(obj, kind):
    from ..baking.naming import typed_name
    return typed_name(kind, obj)


def get(obj, kind):
    d = getattr(obj, "multicamproject_bake", None)
    return getattr(d, POINTER[kind], None) if d is not None else None


def images(obj):
    return {k: img for k in KINDS if (img := get(obj, k)) is not None}


def problem(obj, scene):
    """Why this object cannot paint into textures ('' when it can)."""
    if getattr(obj, "multicamproject_bake", None) is None:
        return "Paint Textures need the Baking module"
    if obj.data.uv_layers.get(UV_NORMAL) is None:
        return "No uv_normal to paint on - make it first (Bake), or turn Paint Textures off"
    from ..baking import common
    if not common.settings(scene).output_dir:
        return "No bake folder set - the paint textures are saved there"
    return ""


def ensure(obj, kind, scene):
    """obj's CMX1_ / CMX2_: its own (brought to the current format), or a new 1K PNG in the
    bake folder holding what VCMix / VCMix2 show now. Object Mode. Returns (image, created)."""
    import os
    from ..baking import common, engine
    img = get(obj, kind)
    if img is not None:
        if img.get(VERSION_KEY, 1) < VERSION:
            migrate(obj)
        return img, False
    px = layer_pixels(obj, kind)
    name = image_name(obj, kind)
    path = common.texture_path(scene, name)
    engine._check_length(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = bpy.data.images.new(name + "_MCP_TMP", SIZE, SIZE, alpha=True)
    tmp.pixels.foreach_set(px.ravel())
    tmp.filepath_raw = path
    tmp.file_format = 'PNG'
    tmp.save(filepath=path)
    bpy.data.images.remove(tmp)
    img = bpy.data.images.get(name)
    if img is None or img.source != 'FILE':
        if img is not None:
            img.name = name + "_stale"
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name
    setup_image(img)
    engine.link_file(img, path)
    img[VERSION_KEY] = VERSION
    setattr(obj.multicamproject_bake, POINTER[kind], img)
    from ..baking import owned
    owned.add(scene, [path])
    return img, True


def layer_pixels(obj, kind, previous=None):
    """(SIZE*SIZE, 4) pixels of VCMix (CMX1) / VCMix2 (CMX2) as MCP_ shows them now
    (`previous` 0: the cameras' own weights, Reset Camera Mix), rasterized on uv_normal and
    grown into the free UV space. CMX2's alpha is 0 (the stroke marker)."""
    from . import core
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    me = obj.data
    got = core.projection_layers(obj, [LAYER[kind]], previous).get(LAYER[kind])
    me.calc_loop_triangles()
    nloops = len(me.loops)
    if got is None:
        raise RuntimeError(f"{LAYER[kind]} could not be read from the projection (a live "
                           "Decimate? apply it first)")
    domain, buf = got
    col = buf.reshape(-1, 4)
    if domain == 'POINT':
        vi = np.empty(nloops, np.int32)
        me.loops.foreach_get("vertex_index", vi)
        if len(col) != len(me.vertices):
            raise RuntimeError(f"{LAYER[kind]} does not match the mesh")
        col = col[vi]
    elif len(col) != nloops:
        raise RuntimeError(f"{LAYER[kind]} does not match the mesh")
    t = len(me.loop_triangles)
    loops = np.empty(t * 3, np.int32)
    me.loop_triangles.foreach_get("loops", loops)
    tris = mesh_triangles(me)
    px, cov = raster_colors(tris, col[loops].reshape(t, 3, 4))
    px = grow(px, cov)
    if kind == "CMX2":
        px[:, 3] = 0.0
    return px


def migrate(obj):
    """CMX1_ / CMX2_ of v1 (alpha = painted here, MSK_ = the mask) -> v2 (alpha = the mask):
    the vertex layers under the painted pixels, MSK_ into CMX1_'s alpha."""
    msk = getattr(obj.multicamproject_bake, POINTER["MSK"], None)
    done = False
    for kind in KINDS:
        img = get(obj, kind)
        if img is None or img.get(VERSION_KEY, 1) >= VERSION:
            continue
        old = read(img)
        new = layer_pixels(obj, kind)
        a = old[:, 3:4]
        new[:, :3] = new[:, :3] * (1.0 - a) + old[:, :3] * a
        if kind == "CMX1" and msk is not None and msk.has_data:
            m = read(msk)
            new[:, 3] = new[:, 3] * (1.0 - m[:, 3]) + m[:, 0] * m[:, 3]
        img.pixels.foreach_set(new.ravel())
        img.update()
        img[VERSION_KEY] = VERSION
        bump(img)
        done = True
        print(f"[MultiCamProject] {img.name}: alpha is the blend mask now (converted)")
    if done:
        from . import core
        core.ensure_material(obj)       # MCP_ reads the alpha as the mask


def migrate_all():
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and not obj.library and any(
                img.get(VERSION_KEY, 1) < VERSION for img in images(obj).values()):
            try:
                migrate(obj)
            except Exception as e:
                print(f"[MultiCamProject] {obj.name}: paint textures not converted: {e}")


def setup_image(img):
    """Non-Color, alpha kept apart from color (no premultiply). Only set when different:
    setting either reloads the image from its file - unsaved paint would be lost."""
    if img.colorspace_settings.name != 'Non-Color':
        img.colorspace_settings.name = 'Non-Color'
    if img.alpha_mode != 'CHANNEL_PACKED':
        img.alpha_mode = 'CHANNEL_PACKED'


# ---------------------------------------------------------------- pixels

def read(img):
    buf = np.empty(len(img.pixels), np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(-1, 4)


def _digest(arr):
    return hashlib.blake2b(np.round(arr * 255).astype(np.uint8).tobytes(), digest_size=16).digest()


_redo = {}          # image name -> [(digest of the inverted state, pixels written)]
_rev = {}           # image name -> change counter (fingerprint cache)


def bump(img):
    _rev[img.name] = _rev.get(img.name, 0) + 1


def bump_canvas(obj, canvas):
    """A stroke went into `canvas`: one of obj's paint textures changed."""
    if canvas is not None and obj is not None and canvas in images(obj).values():
        bump(canvas)


def revision(img):
    return _rev.get(img.name, 0)


def _context(img):
    wm = bpy.context.window_manager
    if not wm.windows:
        return None
    win = wm.windows[0]
    area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), win.screen.areas[0])
    return dict(window=win, screen=win.screen, area=area, edit_image=img)


def write(img, arr):
    """img's pixels = arr ((n, 4) float) as its own undo step (see the module doc)."""
    ctx = _context(img)
    if ctx is not None:
        with bpy.context.temp_override(**ctx):
            if bpy.ops.image.invert.poll():
                bpy.ops.image.invert(invert_r=True)     # Blender keeps the image as it was
        lst = _redo.setdefault(img.name, [])
        lst.append((_digest(read(img)), arr.astype(np.float32)))
        del lst[:-REDO_KEEP]
    img.pixels.foreach_set(arr.astype(np.float32).ravel())
    img.update()
    bump(img)


@persistent
def _on_redo(*_):
    for name, lst in list(_redo.items()):
        img = bpy.data.images.get(name)
        if img is None or not lst or not img.has_data:
            continue
        cur = _digest(read(img))
        for key, arr in reversed(lst):
            if key == cur:
                img.pixels.foreach_set(arr.ravel())
                img.update()
                break


@persistent
def _on_undo(*_):
    for name in list(_rev):
        _rev[name] += 1


_queue = []         # (object name, kind, fn(arr) -> arr)


def queue(obj, kind, fn):
    """write() once the running operator is done (its undo step first, then this one)."""
    _queue.append((obj.name, kind, fn))
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=0.0)


def _flush():
    jobs, _queue[:] = list(_queue), []
    touched = set()
    for name, kind, fn in jobs:
        obj = bpy.data.objects.get(name)
        img = get(obj, kind) if obj is not None else None
        if img is None:
            continue
        write(img, fn(read(img)))
        touched.add(obj)
    from . import paint_sync
    for obj in touched:
        obj.update_tag()
        paint_sync.reset_texture(obj)   # a fill is no stroke: nothing for paint_sync to claim
    return None


# ---------------------------------------------------------------- UV raster (GPU)

def mesh_triangles(me, faces=None):
    """(n, 3, 2) uv_normal triangles of the mesh (Object Mode), of `faces` (bool per face)."""
    uv = me.uv_layers.get(UV_NORMAL)
    if uv is None:
        return np.zeros((0, 3, 2), np.float32)
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    loops = np.empty(n * 3, np.int32)
    me.loop_triangles.foreach_get("loops", loops)
    co = np.empty(len(me.loops) * 2, np.float32)
    uv.data.foreach_get("uv", co)
    tris = co.reshape(-1, 2)[loops].reshape(n, 3, 2)
    if faces is not None:
        poly = np.empty(n, np.int32)
        me.loop_triangles.foreach_get("polygon_index", poly)
        tris = tris[faces[poly]]
    return tris


def bmesh_triangles(bm, faces):
    """(n, 3, 2) uv_normal triangles of `faces` (bmesh faces, Edit Mode)."""
    lay = bm.loops.layers.uv.get(UV_NORMAL)
    if lay is None:
        return np.zeros((0, 3, 2), np.float32)
    want = set(faces)
    out = [[lp[lay].uv[:] for lp in tri] for tri in bm.calc_loop_triangles() if tri[0].face in want]
    return np.array(out, np.float32).reshape(-1, 3, 2)


def raster(tris, size=SIZE):
    """Coverage (size * size bool, rows bottom-up like Image.pixels) of uv triangles."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix
    if not len(tris):
        return np.zeros(size * size, bool)
    pos = tris.reshape(-1, 2) * 2.0 - 1.0
    pos = np.c_[pos, np.zeros(len(pos))].astype(np.float32)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": pos})
    off = gpu.types.GPUOffScreen(size, size, format='RGBA8')
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0))
            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                gpu.state.blend_set('NONE')
                shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
                batch.draw(shader)
            buf = fb.read_color(0, 0, size, size, 4, 0, 'UBYTE')
    finally:
        off.free()
    buf.dimensions = size * size * 4
    return np.asarray(buf, dtype=np.uint8).reshape(-1, 4)[:, 0] > 127


def raster_colors(tris, colors, size=SIZE, depth=None, clip=True):
    """(pixels (size*size, 4) float, coverage bool) of uv triangles with per-corner colors
    (n, 3, 4), interpolated over each triangle. `depth` (n, 3): per-corner z (-1..1), the
    nearest triangle wins. clip=False keeps values outside 0-1."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix
    if not len(tris):
        return np.zeros((size * size, 4), np.float32), np.zeros(size * size, bool)
    pos = tris.reshape(-1, 2) * 2.0 - 1.0
    z = np.zeros(len(pos)) if depth is None else depth.reshape(-1)
    pos = np.c_[pos, z].astype(np.float32)
    shader = gpu.shader.from_builtin('SMOOTH_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": pos,
                                              "color": colors.reshape(-1, 4).astype(np.float32)})
    off = gpu.types.GPUOffScreen(size, size, format='RGBA32F')
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, -1.0), depth=1.0)     # alpha -1: no face here
            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                gpu.state.blend_set('NONE')
                if depth is not None:
                    gpu.state.depth_test_set('LESS')
                    gpu.state.depth_mask_set(True)
                batch.draw(shader)
                if depth is not None:
                    gpu.state.depth_test_set('NONE')
                    gpu.state.depth_mask_set(False)
            buf = fb.read_color(0, 0, size, size, 4, 0, 'FLOAT')
    finally:
        off.free()
    buf.dimensions = size * size * 4
    px = np.asarray(buf, dtype=np.float32).reshape(-1, 4).copy()
    cov = px[:, 3] > -0.5
    px[~cov] = 0.0
    return (np.clip(px, 0.0, 1.0) if clip else px), cov


DEPTH_SIZE = 1024       # the camera's depth map for seen_pixels (the GPU read back is the cost)
SEEN_REL = 0.02         # a pixel this much (of its distance) behind the nearest surface is still seen
SEEN_ABS = 0.01         # + this many metres


_seen = {}          # (object, camera, their state) -> seen_pixels, the last few
SEEN_KEEP = 6


def _seen_key(obj, cam, scene):
    from . import core
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    cd = cam.data
    return (obj.name, cam.name, len(me.polygons), float(co.sum()), float(np.abs(co).sum()),
            tuple(round(x, 6) for row in core.world_matrix(obj) for x in row),
            tuple(round(x, 6) for row in core.world_matrix(cam) for x in row),
            cd.type, round(cd.lens, 5), round(cd.sensor_width, 5), round(cd.ortho_scale, 5),
            round(cd.clip_start, 6), round(core.image_aspect(core.cam_image(cam), scene, load=False), 6))


def seen_pixels(obj, cam, scene, size=SIZE):
    """seen_pixels_now, remembered: clicking the same camera again (or back) is instant
    until the mesh, the object or the camera moves."""
    key = _seen_key(obj, cam, scene)
    got = _seen.get(key)
    if got is None:
        got = _seen[key] = seen_pixels_now(obj, cam, scene, size)
        for k in list(_seen)[:-SEEN_KEEP]:
            del _seen[k]
    return got


def clear_seen():
    _seen.clear()


def seen_pixels_now(obj, cam, scene, size=SIZE):
    """Per pixel of uv_normal: `cam` really sees it - in its frame, facing it and not
    behind another part of the mesh (a chair in front of the table). Two GPU passes: the
    mesh's depth from the camera, then every pixel's own depth compared with it."""
    me = obj.data
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    uv = mesh_triangles(me)
    if not n or len(uv) != n:
        return np.zeros(size * size, bool)
    tv = np.empty(n * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    poly = np.empty(n, np.int32)
    me.loop_triangles.foreach_get("polygon_index", poly)
    vco = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", vco)
    from . import core
    om = core.world_matrix(obj)
    mw = np.array(om, dtype=np.float32)
    co = vco.reshape(-1, 3)[tv] @ mw[:3, :3].T + mw[:3, 3]        # (n*3, 3) world corners
    fn = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("normal", fn)
    nmat = np.array(om.to_3x3().inverted_safe().transposed(), dtype=np.float32)
    nr = np.repeat(fn.reshape(-1, 3)[poly] @ nmat.T, 3, axis=0)
    u, v, d = core.cam_uvd(cam, scene, co)
    cm = core.world_matrix(cam)
    if cam.data.type == 'ORTHO':
        to_cam = np.array(cm.to_3x3().col[2], dtype=np.float32)[None, :]
        far = max(float(d.max(initial=1.0)), 1e-3) * 1.01
        z = d / far * 2.0 - 1.0                     # ortho: depth is linear on screen
    else:
        to_cam = np.array(cm.translation, dtype=np.float32) - co
        near = max(cam.data.clip_start, 1e-3)
        z = 1.0 - 2.0 * near / np.maximum(d, near)  # perspective: 1/depth is linear on screen
    facing = ((nr * to_cam).sum(axis=1) > 0.0).astype(np.float32)
    front = (d > 1e-6).reshape(n, 3).all(axis=1)    # a triangle through the camera plane: left out

    # pass 1: the camera's depth map (nearest surface per photo pixel)
    tri_uv = np.c_[u, v].astype(np.float32).reshape(n, 3, 2)[front]
    tri_z = z.reshape(n, 3)[front]
    zc = np.c_[np.repeat(tri_z.reshape(-1, 1), 3, axis=1), np.ones(tri_z.size)].reshape(-1, 3, 4)
    zmap, zcov = raster_colors(tri_uv, zc, DEPTH_SIZE, depth=tri_z, clip=False)
    zmap = np.where(zcov, zmap[:, 0], 2.0).reshape(DEPTH_SIZE, DEPTH_SIZE)

    # pass 2: every uv_normal pixel's photo position, depth and facing
    attrs = np.c_[u, v, z, facing * front.repeat(3)].astype(np.float32).reshape(n, 3, 4)
    px, cov = raster_colors(uv, attrs, size, clip=False)
    pu, pv, pz = px[:, 0], px[:, 1], px[:, 2]
    seen = cov & (px[:, 3] > 0.5) & (pu > 0.0) & (pu < 1.0) & (pv > 0.0) & (pv < 1.0)
    iu = np.clip((pu * DEPTH_SIZE).astype(np.int32), 0, DEPTH_SIZE - 1)
    iv = np.clip((pv * DEPTH_SIZE).astype(np.int32), 0, DEPTH_SIZE - 1)
    ref = zmap[iv, iu]
    if cam.data.type == 'ORTHO':
        tol = (SEEN_REL * (pz + 1.0) + SEEN_ABS * 2.0 / far)
    else:
        inv = np.clip((1.0 - pz) * 0.5, 1e-6, 1.0)  # near / depth
        tol = 2.0 * inv * (SEEN_REL + SEEN_ABS * inv / near)
    seen &= pz <= ref + tol
    return dilate(seen, ~cov)


STENCIL = "MCP_Camera Sees"     # the stencil image: white where the camera sees the object
_stencil_holds = [None]         # the seen_pixels array the stencil image holds now


def set_stencil(context, obj, seen):
    """Texture Paint's Stencil Mask = `seen` (None: off) on uv_normal - the brush only
    paints where the camera sees the mesh, no face selection needed."""
    ip = context.tool_settings.image_paint
    if seen is None:
        ip.use_stencil_layer = False
        return
    img = bpy.data.images.get(STENCIL)
    if img is None or tuple(img.size) != (SIZE, SIZE):
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(STENCIL, SIZE, SIZE, alpha=False)
        img.colorspace_settings.name = 'Non-Color'
        _stencil_holds[0] = None
    if _stencil_holds[0] is not seen:       # the same (cached) mask again: no 1K write
        v = seen.astype(np.float32)
        img.pixels.foreach_set(np.c_[v, v, v, np.ones_like(v)].ravel())
        img.update()
        _stencil_holds[0] = seen
    me = obj.data
    me.uv_layer_stencil = me.uv_layers[UV_NORMAL]
    ip.stencil_image = img
    ip.invert_stencil = False
    ip.use_stencil_layer = True


def camera_mask_on(scene, obj):
    """The brush keeps to what the camera sees: the panel's Camera Mask, Project through off."""
    from . import core
    props = getattr(scene, "multicamproject_props", None)
    return bool(getattr(props, "camera_mask", True)) and not core.data(obj).project_through


def on_camera_mask(context):
    """Camera Mask toggled while a camera brush paints: its stencil on / off right away."""
    from . import paint_session
    obj = context.active_object
    using = paint_session.current(obj)
    if using is None or obj.mode != 'TEXTURE_PAINT':
        return
    cam = bpy.data.objects.get(using[0])
    if using[1] == 'PAINT' and cam is not None and camera_mask_on(context.scene, obj):
        set_stencil(context, obj, seen_pixels(obj, cam, context.scene))
    else:
        set_stencil(context, obj, None)


def grow(px, cov, steps=DILATE, size=SIZE):
    """Pixels no face covers take their covered neighbours' mean, `steps` rings out: the
    texture filters across an island's border without a seam."""
    img = px.reshape(size, size, 4).copy()
    have = cov.reshape(size, size).copy()
    for _ in range(steps):
        acc = np.zeros_like(img)
        cnt = np.zeros((size, size), np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(have, (dy, dx), axis=(0, 1))
            acc += np.roll(img, (dy, dx), axis=(0, 1)) * sh[:, :, None]
            cnt += sh
        add = ~have & (cnt > 0)
        if not add.any():
            break
        img[add] = acc[add] / cnt[add][:, None]
        have |= add
    return img.reshape(-1, 4)


def dilate(cov, free, px=2, size=SIZE):
    """cov grown by `px` pixels into `free` (UV space no face covers): no seam shows
    where the texture filters across an island's border."""
    grid = cov.reshape(size, size).copy()
    room = free.reshape(size, size)
    for _ in range(px):
        g = grid.copy()
        g[1:, :] |= grid[:-1, :]
        g[:-1, :] |= grid[1:, :]
        g[:, 1:] |= grid[:, :-1]
        g[:, :-1] |= grid[:, 1:]
        grid |= g & room
    return grid.ravel()


def coverage(obj, tris, all_tris=None):
    """Pixels of `tris`, grown into the UV space no face of obj uses."""
    cov = raster(tris)
    if all_tris is None:
        all_tris = mesh_triangles(obj.data)
    return dilate(cov, ~raster(all_tris))


def fill_fn(cov, rgb, alpha=1.0):
    """fn for queue(): `rgb` with `alpha` over the covered pixels (cov: bool or 0-1)."""
    c = cov.astype(np.float32)[:, None]

    def fn(arr):
        arr[:, :3] = arr[:, :3] * (1.0 - c) + np.asarray(rgb, np.float32) * c
        arr[:, 3:] = np.maximum(arr[:, 3:], c * alpha)
        return arr
    return fn


def claim_fn(claim):
    """fn for queue() / write() on CMX1: cameras 1-3 off where cameras 4-6 claimed."""
    c = claim.astype(np.float32)

    def fn(arr):
        arr[:, :3] *= (1.0 - c)[:, None]
        arr[:, 3] = np.maximum(arr[:, 3], c)
        return arr
    return fn


def mask_fn(cov, value):
    """fn for queue() on CMX1: the blend mask (alpha) = value over the covered pixels
    (cov None: all of them)."""
    c = None if cov is None else cov.astype(np.float32)

    def fn(arr):
        arr[:, 3] = value if c is None else arr[:, 3] * (1.0 - c) + value * c
        return arr
    return fn


def weights_fn(fresh):
    """fn for queue(): R, G, B = `fresh` (the cameras' own weights), the alpha stays."""
    def fn(arr):
        arr[:, :3] = fresh[:, :3]
        return arr
    return fn


# ---------------------------------------------------------------- material toggle

def set_toggle(obj, scene=None):
    """MCP_'s Paint Textures Value = the scene toggle (no rebuild)."""
    d = getattr(obj, "multicamproject_cam", None)
    mat = d.material if d is not None else None
    n = mat.node_tree.nodes.get(TOGGLE) if mat is not None and mat.node_tree else None
    v = 1.0 if enabled(scene) else 0.0
    if n is not None and abs(n.outputs[0].default_value - v) > 1e-6:
        n.outputs[0].default_value = v


def on_toggle(props, context):
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and getattr(obj, "multicamproject_cam", None) is not None:
            set_toggle(obj, context.scene)


# ---------------------------------------------------------------- brush / saving

def set_brush(context, color, blend='MIX'):
    """Texture Paint brush: a Draw brush with `color` (None: kept) and `blend`."""
    ip = context.tool_settings.image_paint
    if ip.brush is None or ip.brush.image_brush_type != 'DRAW':
        bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS',
                                     relative_asset_identifier=BRUSH)
    br = ip.brush
    br.blend = blend
    if color is not None:
        br.color = color
        ip.unified_paint_settings.color = color


def start_painting(context, obj, img, seen=None):
    """Texture Paint on `img` through uv_normal (Object Mode -> Texture Paint). The brush
    always stops at the first surface under it (Occlude); `seen` (per pixel, None: all)
    is where the camera sees the mesh - the stencil keeps the paint there."""
    me = obj.data
    me.uv_layers.active = me.uv_layers[UV_NORMAL]
    if obj.mode != 'TEXTURE_PAINT':
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    ip = context.tool_settings.image_paint
    ip.mode = 'IMAGE'
    ip.canvas = img
    ip.use_occlude = True
    ip.use_backface_culling = True
    set_stencil(context, obj, seen)


def save_dirty():
    """Every dirty paint texture into its file. Returns the names saved."""
    saved = []
    for obj in bpy.data.objects:
        for img in images(obj).values():
            if img.is_dirty and img.source == 'FILE' and img.filepath and not img.library:
                try:
                    img.save()
                    saved.append(img.name)
                except RuntimeError as e:
                    print(f"[MultiCamProject] {img.name} not saved: {e}")
    return saved


@persistent
def _on_save_pre(*_):
    try:
        save_dirty()
    except Exception as e:      # never block a save
        print(f"[MultiCamProject] paint textures not saved: {e}")


def _migrate_later():
    try:
        migrate_all()
    except Exception as e:      # never break loading
        print(f"[MultiCamProject] paint texture conversion skipped: {e}")
    return None


@persistent
def _on_load(*_):
    _redo.clear()
    _rev.clear()
    _queue.clear()
    bpy.app.timers.register(_migrate_later, first_interval=1.0)


def register():
    bpy.app.handlers.redo_post.append(_on_redo)
    bpy.app.handlers.undo_post.append(_on_undo)
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_migrate_later, first_interval=1.0)     # an add-on reload too


def unregister():
    for handlers, fn in ((bpy.app.handlers.redo_post, _on_redo),
                         (bpy.app.handlers.undo_post, _on_undo),
                         (bpy.app.handlers.save_pre, _on_save_pre),
                         (bpy.app.handlers.load_post, _on_load)):
        if fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
