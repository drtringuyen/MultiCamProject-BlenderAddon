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
TOGGLE = "Paint Textures"           # MCP_'s Value node: 1 (0 = vertex layers only, old files)
UV_NORMAL = "uv_normal"
BRUSH = "brushes/essentials_brushes-mesh_texture.blend/Brush/Paint Hard"
REDO_KEEP = 20                      # writes per image a redo can still put back


def node_name(kind):
    return f"{kind} Paint"


def mode(obj):
    """'TEXTURE' (CMX1_ / CMX2_) or 'VERTEX' (VCMix / VCMix2): the object's Paint switch."""
    d = getattr(obj, "multicamproject_cam", None)
    return getattr(d, "paint_mode", 'TEXTURE') if d is not None else 'TEXTURE'


CHOICES = (('VERTEX_SHARP', "Vertex Sharp", "Paint the mesh's VCMix / VCMix2 vertex colors; "
                                            "each face shows its best camera (Mode Sharp)",
             'VPAINT_HLT', 0),
            ('VERTEX_SMOOTH', "Vertex Smooth", "Paint the mesh's VCMix / VCMix2 vertex colors; "
                                              "cameras blend across the vertices (Mode "
                                              "Combined)", 'SMOOTHCURVE', 1),
            ('TEXTURE', "Texture Mask", "Paint CMX1_ / CMX2_, 1K on uv_normal: precise, not "
                                       "tied to the vertices", 'TEXTURE', 2))
GN_MODE = {'VERTEX_SHARP': "Sharp", 'VERTEX_SMOOTH': "Combined"}


def choice(obj):
    """The 04 dropdown's item for obj: the Paint switch + the projection Mode. An old
    Mode Smooth reads as Vertex Smooth."""
    if mode(obj) == 'TEXTURE':
        return 'TEXTURE'
    from . import core
    mod = core.get_modifier(obj)
    try:
        cur = core.get_input(mod, "Mode") if mod is not None else "Sharp"
    except (KeyError, AttributeError, TypeError):
        cur = "Sharp"
    return 'VERTEX_SHARP' if cur == "Sharp" else 'VERTEX_SMOOTH'


def can_paint(obj, scene=None):
    """obj paints into CMX1_ / CMX2_: Paint on Texture and possible (else: into VCMix /
    VCMix2)."""
    return mode(obj) == 'TEXTURE' and not problem(obj, scene or bpy.context.scene)


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
        return "No uv_normal to paint on yet (step 02 makes it)"
    from ..baking import common
    if not common.settings(scene).output_dir:
        return "No bake folder set - the paint textures are saved there"
    return ""


def ensure(obj, kind, scene, px=None):
    """obj's CMX1_ / CMX2_: its own (brought to the current format), or a new 1K PNG in the
    bake folder holding what VCMix / VCMix2 show now. Object Mode. Returns (image, created)."""
    import os
    from ..baking import common, engine
    img = get(obj, kind)
    if img is not None:
        if img.get(VERSION_KEY, 1) < VERSION:
            migrate(obj)
        return img, False
    if px is None:
        px = shown_pixels(obj)[kind]
    name = image_name(obj, kind)
    path = common.texture_path(scene, name)
    try:                                # a work window: straight into the main 01.Bake
        from ..linking import core as linking
        main = linking.paint_dir(scene)
    except ImportError:
        main = ""
    if main:
        path = os.path.join(main, name + ".png")
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


def layer_pixels(obj, kind, previous=None, keep_alpha=False):
    """(SIZE*SIZE, 4) pixels of VCMix (CMX1) / VCMix2 (CMX2) as MCP_ shows them now
    (`previous` 0: the cameras' own weights, Reset Camera Mix), rasterized on uv_normal and
    grown into the free UV space. CMX2's alpha is 0 (the stroke marker) unless
    `keep_alpha`."""
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
    if kind == "CMX2" and not keep_alpha:
        px[:, 3] = 0.0
    return px


def shown_pixels(obj):
    """{'CMX1', 'CMX2' (4+ slots)}: what VCMix / VCMix2 show now, as the textures hold it -
    CMX1_ alpha = the blend mask = VCMix alpha x VCMix2 alpha."""
    from . import core
    out = {"CMX1": layer_pixels(obj, "CMX1")}
    if core.slot_count(core.data(obj)) > 3:
        px2 = layer_pixels(obj, "CMX2", keep_alpha=True)
        out["CMX1"][:, 3] *= px2[:, 3]
        px2[:, 3] = 0.0                 # CMX2_ alpha: the stroke marker
        out["CMX2"] = px2
    return out


# ---------------------------------------------------------------- Paint switch: convert

CONV = "_mcp_conv_"     # + VCMix / VCMix2: the layer as Paint -> Vertex wrote it


def _changed_faces(obj):
    """Faces whose VCMix / VCMix2 changed since Paint -> Vertex wrote them (bool per face),
    or None: no snapshot (every face counts as changed). The snapshots go."""
    from . import gn_builder
    me = obj.data
    out, had = np.zeros(len(me.polygons), bool), False
    vi = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", vi)
    starts = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", starts)
    for name in gn_builder.LAYERS:
        snap = me.attributes.get(CONV + name)
        a = me.color_attributes.get(name)
        if snap is None:
            continue
        had = True
        if a is None or a.domain != snap.domain or len(a.data) != len(snap.data):
            out[:] = True
        else:
            cur = np.empty(len(a.data) * 4, np.float32)
            old = np.empty(len(a.data) * 4, np.float32)
            a.data.foreach_get("color", cur)
            snap.data.foreach_get("color", old)
            diff = np.abs(cur - old).reshape(-1, 4).max(axis=1) > 1e-4
            per_loop = diff[vi] if a.domain == 'POINT' else diff
            if len(per_loop) == len(me.loops) and len(starts):
                out |= np.maximum.reduceat(per_loop.astype(np.int8), starts) > 0
        me.attributes.remove(me.attributes[CONV + name])
    return out if had else None


def to_textures(obj, scene):
    """Paint -> Vertex -> Texture: CMX1_ / CMX2_ take what VCMix / VCMix2 show now on the
    faces painted meanwhile; everywhere else the textures keep their own (finer) pixels.
    Never converted before: the textures become all of it (made, or written as an undo step
    after the running operator). Object Mode. Returns the names."""
    changed = _changed_faces(obj)
    if changed is not None and not changed.any():
        px, keep = {}, None                 # nothing painted as vertices: textures as they were
    else:
        px = shown_pixels(obj)
        keep = None
        if changed is not None:             # only the repainted faces (and their margin)
            me = obj.data
            all_tris = mesh_triangles(me)
            keep = ~dilate(raster(mesh_triangles(me, changed)), ~raster(all_tris), px=DILATE)
    done, made = [], False
    for kind in ("CMX1", "CMX2"):
        img = get(obj, kind)
        arr = px.get(kind)
        if arr is None:
            if img is not None:
                done.append(img.name)
            continue
        if img is None:
            img, new = ensure(obj, kind, scene, arr)
            made |= new
        elif keep is None:
            queue(obj, kind, lambda _cur, arr=arr: arr)
        else:
            queue(obj, kind, lambda cur, arr=arr: np.where(keep[:, None], cur, arr))
        done.append(img.name)
    obj.multicamproject_cam.paint_mode = 'TEXTURE'
    if made:
        from . import core
        core.ensure_material(obj)       # MCP_ gets the textures' nodes
    set_toggle(obj)
    return done


def _sample(img, uv):
    """img's pixels (bilinear, clamped) at uv ((n, 2) in 0..1) -> (n, 4)."""
    w, h = img.size
    px = read(img).reshape(h, w, 4)
    x = np.clip(uv[:, 0] * w - 0.5, 0.0, w - 1.0)
    y = np.clip(uv[:, 1] * h - 0.5, 0.0, h - 1.0)
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = px[y0, x0] * (1 - fx) + px[y0, x1] * fx
    bot = px[y1, x0] * (1 - fx) + px[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def to_vertices(obj):
    """Paint -> Vertex: VCMix / VCMix2 on the mesh become what CMX1_ / CMX2_ hold (sampled
    at uv_normal; the blend mask split as VCMix a x VCMix2 a), Previous Bake 1 so they show
    1:1. Without textures nothing is written. Object Mode. Returns the layers written."""
    from . import core, gn_builder
    me = obj.data
    imgs = images(obj)
    uvl = me.uv_layers.get(UV_NORMAL)
    done = []
    if imgs.get("CMX1") is not None and uvl is not None:
        uv = np.empty(len(me.loops) * 2, np.float32)
        uvl.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        two = core.slot_count(core.data(obj)) > 3
        c1 = _sample(imgs["CMX1"], uv)
        cols = {gn_builder.LAYERS[0]: c1}
        if two:
            mask = c1[:, 3].copy()
            c2 = _sample(imgs["CMX2"], uv) if imgs.get("CMX2") is not None else np.zeros_like(c1)
            c1[:, 3] = c2[:, 3] = np.sqrt(np.clip(mask, 0.0, 1.0))
            cols[gn_builder.LAYERS[1]] = c2
        vi = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get("vertex_index", vi)
        for name, col in cols.items():
            a = me.color_attributes.get(name)
            if a is None:
                a = me.color_attributes.new(name, 'FLOAT_COLOR', 'CORNER')
            if a.domain == 'POINT':         # the corners' mean per vertex
                acc = np.zeros((len(me.vertices), 4), np.float32)
                cnt = np.zeros(len(me.vertices), np.float32)
                np.add.at(acc, vi, col)
                np.add.at(cnt, vi, 1.0)
                col = acc / np.maximum(cnt, 1.0)[:, None]
            flat = np.ascontiguousarray(col, np.float32).ravel()
            a.data.foreach_set("color", flat)
            snap = me.attributes.get(CONV + name)      # what Texture compares with later
            if snap is not None:
                me.attributes.remove(snap)
            snap = me.color_attributes.new(CONV + name, 'FLOAT_COLOR', a.domain)
            snap.data.foreach_set("color", flat)
            done.append(name)
        me.color_attributes.active_color = me.color_attributes[gn_builder.LAYERS[0]]
        stash = me.attributes.get(gn_builder.MASK2_STASH)
        if stash is not None:
            me.attributes.remove(stash)
        mod = core.get_modifier(obj)
        if mod is not None and core.get_input(mod, "Previous Bake") < 1.0:
            core.set_input(mod, "Previous Bake", 1.0)
        me.update()
    obj.multicamproject_cam.paint_mode = 'VERTEX'
    set_toggle(obj)
    obj.update_tag()
    return done


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


def on_airbrush(context):
    """Airbrush toggled while a camera brush paints: switch it now, same color and blend."""
    from . import core, paint_session
    obj = context.active_object
    using = paint_session.current(obj)
    if using is None:
        return
    color = paint_session.color() if using[1] == 'PAINT' else None
    if obj.mode == 'TEXTURE_PAINT':
        set_brush(context, color, context.tool_settings.image_paint.brush.blend, force=True)
    elif obj.mode == 'VERTEX_PAINT':
        core.set_paint_brush(context, color, context.tool_settings.vertex_paint.brush.blend,
                             force=True)


STENCIL = "MCP_Camera Sees"     # the removed Camera Mask's stencil image (old files: removed)


def drop_stencil(context=None):
    """The removed Camera Mask: Texture Paint's Stencil Mask off and its image gone."""
    ctx = context or bpy.context
    ts = getattr(ctx, "tool_settings", None) or getattr(ctx.scene, "tool_settings", None)
    if ts is not None and ts.image_paint.use_stencil_layer:
        ts.image_paint.use_stencil_layer = False
    img = bpy.data.images.get(STENCIL)
    if img is not None:
        bpy.data.images.remove(img)


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
    """MCP_'s Paint Textures Value at 1 (no rebuild) - old files had a toggle."""
    d = getattr(obj, "multicamproject_cam", None)
    mat = d.material if d is not None else None
    n = mat.node_tree.nodes.get(TOGGLE) if mat is not None and mat.node_tree else None
    v = 1.0 if mode(obj) == 'TEXTURE' else 0.0
    if n is not None and abs(n.outputs[0].default_value - v) > 1e-6:
        n.outputs[0].default_value = v


# ---------------------------------------------------------------- brush / saving

AIRBRUSH = "Airbrush"       # the essentials brush (texture and vertex paint both have one)


def airbrush_on(scene):
    return bool(getattr(getattr(scene, "multicamproject_props", None), "airbrush", False))


def brush_asset(path, brush, airbrush, draw_ok, force=False):
    """Activate the right essentials brush: Airbrush when asked for, else keep any Draw
    brush (`draw_ok`) - `force`: back to `path` (Paint Hard) even so."""
    if airbrush:
        want = path.rsplit("/", 1)[0] + "/" + AIRBRUSH
        ok = brush is not None and brush.name.startswith(AIRBRUSH)
    else:
        want = path
        ok = draw_ok and not (force and brush.name.startswith(AIRBRUSH))
    if not ok:
        bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS',
                                     relative_asset_identifier=want)


def set_brush(context, color, blend='MIX', force=False):
    """Texture Paint brush: a Draw brush (Airbrush when the panel says so) with `color`
    (None: kept) and `blend`."""
    ip = context.tool_settings.image_paint
    br = ip.brush
    brush_asset(BRUSH, br, airbrush_on(context.scene),
                br is not None and br.image_brush_type == 'DRAW', force)
    br = ip.brush
    br.blend = blend
    if color is not None:
        br.color = color
        ip.unified_paint_settings.color = color


def start_painting(context, obj, img):
    """Texture Paint on `img` through uv_normal (Object Mode -> Texture Paint). The brush
    always stops at the first surface under it (Occlude)."""
    me = obj.data
    me.uv_layers.active = me.uv_layers[UV_NORMAL]
    if obj.mode != 'TEXTURE_PAINT':
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    ip = context.tool_settings.image_paint
    ip.mode = 'IMAGE'
    ip.canvas = img
    ip.use_occlude = True
    ip.use_backface_culling = True
    if ip.use_stencil_layer:
        ip.use_stencil_layer = False


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


def _sides_default(obj):
    """A Project from Sides object from before the Paint switch, never painted into
    textures: Vertex, as a new one gets."""
    from . import core
    d = getattr(obj, "multicamproject_cam", None)
    if d is None or not d.is_setup or d.is_property_set("paint_mode") or images(obj):
        return
    if any(c is not None and core.cam_data(c).side for c in core.get_slots(d)):
        d.paint_mode = 'VERTEX'


def _migrate_later():
    try:
        migrate_all()
        drop_stencil()
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and not obj.library:
                _sides_default(obj)
                set_toggle(obj)             # MCP_'s Value follows the object's Paint switch
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
