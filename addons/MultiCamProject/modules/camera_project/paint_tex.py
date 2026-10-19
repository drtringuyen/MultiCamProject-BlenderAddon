"""Paint Textures: the camera mix and the blend mask painted at 1K on uv_normal, so the
paint no longer follows the mesh's vertices (version 5, 2026-10-19):

    CMX1_<name>  R, G, B = the weights of cameras 1-3
    CMX2_<name>  R, G, B = the weights of cameras 4-6
    MSK_<name>   R = scan (1) or projection (0)
                 G = cameras 1-3 (0) or 4-6 (1)
                 B = SIG (marks the format: older MSK_ files hold a grey mask)

No alpha is data: Blender paints every image softly with alpha 1 (an alpha-0 pixel takes
the brush's full color wherever it touches - the hard, stair-stepped edges of before).

Headroom: the camera weights sum to HEAD (0.5) at most between strokes - MCP_ divides the
colors by their sum and counts the cover as sum / HEAD, so this does not show. A camera
stroke raises its texture's weights toward 1 by the brush's strength, even where that
camera already showed; paint_sync reads that strength after the stroke and moves MSK_ by
it: G toward the stroke's group, R toward the projection. prime() brings a texture back
under HEAD when a camera brush starts. Erase paints MSK_ R up (Add), Shift+Erase down
(Subtract) - G stays.

Each texture starts from what VCMix / VCMix2 show now (from_layers), then MCP_ reads them
instead of the vertex layers - the object's Paint switch (a Value node, no rebuild)
switches back.

PNG files in the bake folder, named like the export (typed_name), pointers on the
object's multicamproject_bake. Created on the first stroke, saved with the .blend.

Undo: a Python write into an image is not undoable in Blender. write() wraps it in two
Image > Invert steps so Blender records the image before and after (see write()). Writes
asked for inside an operator (Fill, Select & Set) wait for a timer (queue): the operator's
own undo step comes first, then the image steps.
"""
import bpy
import numpy as np
from bpy.app.handlers import persistent

SIZE = 1024
KINDS = ("CMX1", "CMX2", "MSK")
CAM_KINDS = ("CMX1", "CMX2")
POINTER = {"MSK": "msk_image", "CMX1": "cmx1_image", "CMX2": "cmx2_image"}
LAYER = {"CMX1": "VCMix", "CMX2": "VCMix2"}
GROUP = {"CMX1": 0.0, "CMX2": 1.0}  # MSK_ G of a camera texture's cameras
VERSION_KEY = "multicamproject_paint_version"   # on the image
VERSION = 5     # 1: MSK_ + painted-here alphas; 2 / 4: CMX1_ alpha = mask, cameras 1-3 on
                # top, CMX2_ alpha a stroke marker; 3: MSK_ grey = mask, cameras 4-6 on top
HEAD = 0.5                          # camera weights sum to this at most between strokes
SIG = 128 / 255                     # MSK_ B
DILATE = 8                          # pixels the copy grows into the UV space no face uses
TOGGLE = "Paint Textures"           # MCP_'s Value node: 1 (0 = vertex layers only, old files)
UV_NORMAL = "uv_normal"
BRUSH = "brushes/essentials_brushes-mesh_texture.blend/Brush/Paint Hard"
ERASE_COLOR = (1.0, 0.0, 0.0)       # Erase / Shift+Erase: MSK_ R up / down (Add / Subtract)


def node_name(kind):
    return f"{kind} Paint"


def mode(obj):
    """'TEXTURE' (CMX1_ / CMX2_ / MSK_) or 'VERTEX' (VCMix / VCMix2): the object's Paint
    switch."""
    d = getattr(obj, "multicamproject_cam", None)
    return getattr(d, "paint_mode", 'TEXTURE') if d is not None else 'TEXTURE'


CHOICES = (('VERTEX_SHARP', "Vertex Sharp", "Paint the mesh's VCMix / VCMix2 vertex colors; "
                                            "each face shows its best camera (Mode Sharp)",
             'VPAINT_HLT', 0),
            ('VERTEX_SMOOTH', "Vertex Smooth", "Paint the mesh's VCMix / VCMix2 vertex colors; "
                                              "cameras blend across the vertices (Mode "
                                              "Combined)", 'SMOOTHCURVE', 1),
            ('TEXTURE', "Texture Mask", "Paint CMX1_ / CMX2_ / MSK_, 1K on uv_normal: precise, "
                                       "not tied to the vertices", 'TEXTURE', 2))
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
    """obj paints into CMX1_ / CMX2_ / MSK_: Paint on Texture and possible (else: into
    VCMix / VCMix2)."""
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


def _two(obj):
    from . import core
    return core.slot_count(core.data(obj)) > 3


def wanted(obj):
    """The kinds obj paints into: CMX1_ (CMX2_ with 4+ slots) and MSK_."""
    return ("CMX1", "CMX2", "MSK") if _two(obj) else ("CMX1", "MSK")


def ensure(obj, kind, scene):
    """obj's CMX1_ / CMX2_ / MSK_: its own (brought to the current format), else a new 1K
    PNG in the bake folder - made together with the other missing ones, from what VCMix /
    VCMix2 show now, so nothing changes on screen. Object Mode. Returns (image, created)."""
    if any(_version(obj, i) < VERSION for i in images(obj).values()):
        migrate(obj, scene)
    img = get(obj, kind)
    if img is not None:
        return img, False
    missing = [k for k in set(wanted(obj)) | {kind} if get(obj, k) is None]
    px = shown_pixels(obj)
    for k in missing:
        _create(obj, k, scene, px[k] if k in px else _empty(k))
    return get(obj, kind), True


def _empty(kind):
    px = np.zeros((SIZE * SIZE, 4), np.float32)
    px[:, 3] = 1.0
    if kind == "MSK":
        px[:, 2] = SIG
    return px


def _path(obj, kind, scene):
    """Where obj's `kind` is made: the bake folder (a work window: the main 01.Bake)."""
    import os
    from ..baking import common
    name = image_name(obj, kind)
    try:                                # a work window: straight into the main 01.Bake
        from ..linking import core as linking
        main = linking.paint_dir(scene)
    except ImportError:
        main = ""
    return os.path.join(main, name + ".png") if main else common.texture_path(scene, name)


def _load(obj, kind, path):
    """The PNG at path as obj's `kind` (an image of that name from elsewhere: renamed; the
    same file already loaded: read again)."""
    from ..baking import engine
    name = image_name(obj, kind)
    img = bpy.data.images.get(name)
    if img is not None and img.source == 'FILE' and bpy.path.abspath(img.filepath) == path:
        img.reload()
    else:
        if img is not None:
            img.name = name + "_stale"
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name
    setup_image(img)
    engine.link_file(img, path)
    img[VERSION_KEY] = VERSION
    setattr(obj.multicamproject_bake, POINTER[kind], img)
    return img


def _create(obj, kind, scene, px):
    """A new 1K PNG in the bake folder holding px, obj's `kind` from now on."""
    import os
    from ..baking import engine
    name = image_name(obj, kind)
    path = _path(obj, kind, scene)
    engine._check_length(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = bpy.data.images.new(name + "_MCP_TMP", SIZE, SIZE, alpha=True)
    tmp.pixels.foreach_set(np.ascontiguousarray(px, np.float32).ravel())
    tmp.filepath_raw = path
    tmp.file_format = 'PNG'
    tmp.save(filepath=path)
    bpy.data.images.remove(tmp)
    img = _load(obj, kind, path)
    from ..baking import owned
    owned.add(scene, [path])
    return img


def layer_pixels(obj, kind, previous=None):
    """(SIZE*SIZE, 4) pixels of VCMix (CMX1) / VCMix2 (CMX2) as MCP_ shows them now
    (`previous` 0: the cameras' own weights, Reset Camera Mix), rasterized on uv_normal and
    grown into the free UV space - as the layer holds them (cameras 1-3 on top, alpha =
    its share of the mask)."""
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
    return grow(px, cov)


# ---------------------------------------------------------------- the formats

def _capped(c):
    """Weights summing to 1 at most - MCP_ clamps the sum and normalises the colors, so
    nothing changes on screen."""
    c = np.clip(c, 0.0, None)
    return c / np.maximum(c.sum(axis=1), 1.0)[:, None]


def _sums(c):
    return np.clip(c.sum(axis=1), 0.0, 1.0)


def _ratios(c):
    s = c.sum(axis=1)
    return np.where(s[:, None] > 1e-6, c / np.maximum(s, 1e-6)[:, None], 0.0)


def from_layers(c1, c2, mask):
    """The layers' form - c1, c2 (R, G, B (n, 3); c2 None with 3 slots) with cameras 1-3 on
    top (w1 = s1, w2 = s2 x (1 - s1)), mask (n,) 1 = projected - as version 5 holds it, the
    same look: both groups' weights sum to (w1 + w2) x HEAD (their own ratios), MSK_ G =
    w2 / (w1 + w2), R = 1 - mask. Returns {'CMX1', ('CMX2',) 'MSK'} (n, 4)."""
    n = len(c1)
    c1 = _capped(c1)
    w1 = _sums(c1)
    w2 = np.zeros(n, np.float32)
    if c2 is not None:
        c2 = _capped(c2)
        w2 = _sums(c2) * (1.0 - w1)
    tot = w1 + w2
    g = np.where(tot > 1e-6, w2 / np.maximum(tot, 1e-6), 0.0)
    out = {}
    for kind, c in (("CMX1", c1), ("CMX2", c2)):
        if c is None:
            continue
        px = np.ones((n, 4), np.float32)
        px[:, :3] = _ratios(c) * (tot * HEAD)[:, None]
        out[kind] = px
    m = np.ones((n, 4), np.float32)
    m[:, 0] = 1.0 - np.clip(mask, 0.0, 1.0)
    m[:, 1] = g
    m[:, 2] = SIG
    out["MSK"] = m
    return out


def to_layers(px):
    """from_layers back: {'CMX1', ('CMX2',) 'MSK'} -> (c1, c2 or None, mask), cameras 1-3 on
    top - the groups' shares (1 - G) x s1 : G x s2 of the cover max(s1, s2)."""
    m = px["MSK"]
    g = np.clip(m[:, 1], 0.0, 1.0)
    c1 = px["CMX1"][:, :3]
    s1 = np.clip(c1.sum(axis=1) / HEAD, 0.0, 1.0)
    if "CMX2" not in px:
        return _ratios(c1) * s1[:, None], None, 1.0 - np.clip(m[:, 0], 0.0, 1.0)
    c2 = px["CMX2"][:, :3]
    s2 = np.clip(c2.sum(axis=1) / HEAD, 0.0, 1.0)
    a, b = (1.0 - g) * s1, g * s2
    cover = np.maximum(s1, s2)
    tot = a + b
    a = np.where(tot > 1e-6, a / np.maximum(tot, 1e-6), 0.0) * cover
    b = np.where(tot > 1e-6, b / np.maximum(tot, 1e-6), 0.0) * cover
    v1 = _ratios(c1) * a[:, None]
    v2 = _ratios(c2) * np.clip(b / np.maximum(1.0 - a, 1e-6), 0.0, 1.0)[:, None]
    return v1, v2, 1.0 - np.clip(m[:, 0], 0.0, 1.0)


def shown_pixels(obj):
    """What VCMix / VCMix2 show now, as version 5 holds it (from_layers). The mask =
    VCMix alpha x VCMix2 alpha."""
    c1 = layer_pixels(obj, "CMX1")
    mask = c1[:, 3].copy()
    c2 = None
    if _two(obj):
        c2 = layer_pixels(obj, "CMX2")
        mask *= c2[:, 3]
        c2 = c2[:, :3]
    return from_layers(c1[:, :3], c2, mask)


def primed(px):
    """A camera texture's pixels with every weight sum brought down to HEAD (no change on
    screen), or None when none is above it."""
    s = px[:, :3].sum(axis=1)
    over = s > HEAD + 0.5 / 255
    if not over.any():
        return None
    px = px.copy()
    px[over, :3] *= (HEAD / s[over])[:, None]
    return px


def prime(obj, kind):
    """Before a camera brush paints `kind`: its weights under HEAD again (queued: after the
    running operator, its own undo step), so every stroke shows its strength."""
    img = get(obj, kind)
    if img is not None and primed(read(img)) is not None:
        queue(obj, kind, lambda px: primed(px) if primed(px) is not None else px)


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
    """Paint -> Vertex -> Texture: CMX1_ / CMX2_ / MSK_ take what VCMix / VCMix2 show now
    on the faces painted meanwhile; everywhere else the textures keep their own (finer)
    pixels. Never converted before: the textures become all of it (made, or written as an
    undo step after the running operator). Object Mode. Returns the names."""
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
    if px and any(_version(obj, i) < VERSION for i in images(obj).values()):
        migrate(obj, scene)
    done, made = [], False
    for kind in KINDS:
        img = get(obj, kind)
        arr = px.get(kind)
        if arr is None:
            if img is not None:
                done.append(img.name)
            continue
        if img is None:
            img = _create(obj, kind, scene, arr)
            made = True
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
    """Paint -> Vertex: VCMix / VCMix2 on the mesh become what CMX1_ / CMX2_ / MSK_ show
    (sampled at uv_normal, to_layers: cameras 1-3 on top, the mask split as VCMix a x
    VCMix2 a), Previous Bake 1 so they show 1:1. Without textures nothing is written.
    Object Mode. Returns the layers written."""
    from . import core, gn_builder
    me = obj.data
    imgs = images(obj)
    uvl = me.uv_layers.get(UV_NORMAL)
    done = []
    if imgs.get("CMX1") is not None and uvl is not None:
        uv = np.empty(len(me.loops) * 2, np.float32)
        uvl.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        two = _two(obj)
        px = {"CMX1": _sample(imgs["CMX1"], uv)}
        n = len(px["CMX1"])
        if imgs.get("MSK") is not None:
            px["MSK"] = _sample(imgs["MSK"], uv)
        else:
            px["MSK"] = np.zeros((n, 4), np.float32)
        if two:
            px["CMX2"] = (_sample(imgs["CMX2"], uv) if imgs.get("CMX2") is not None
                          else np.zeros((n, 4), np.float32))
        v1, v2, mask = to_layers(px)
        a = np.sqrt(mask) if two else mask
        cols = {gn_builder.LAYERS[0]: np.c_[v1, a]}
        if two:
            cols[gn_builder.LAYERS[1]] = np.c_[v2, a]
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


# ---------------------------------------------------------------- older versions

def _version(obj, img):
    """img's format (VERSION_KEY). Without the mark (loaded again without its ID
    properties, e.g. received from another file): 2 - unless an unmarked MSK_ sits
    beside a camera texture (version 1 always had one)."""
    v = img.get(VERSION_KEY)
    if v is not None:
        return v
    msk = get(obj, "MSK")
    if img != msk and msk is not None and msk.get(VERSION_KEY) is None:
        return 1
    return 2


def migrate(obj, scene=None):
    """_migrate with the bake stamps kept (the look does not change)."""
    from ... import module_manager
    if not module_manager.is_loaded("baking"):
        return _migrate(obj, scene)
    from ..baking import fingerprint
    return fingerprint.restamped(obj, lambda: _migrate(obj, scene))


def _converted_elsewhere(obj, scene):
    """Another Blender sharing the files (the work window, the main file) converted them to
    5 and saved them: a v5 MSK_ file (B = SIG everywhere) beside a CMX1_ whose alpha is 1
    everywhere. Then only the pointers and marks, never a second conversion."""
    import os
    c1 = get(obj, "CMX1")
    if c1 is None or read(c1)[:, 3].min(initial=1.0) < 0.999:
        return False
    path = _path(obj, "MSK", scene)
    if not os.path.isfile(path):
        return False
    tmp = bpy.data.images.load(path, check_existing=False)
    try:
        setup_image(tmp)
        ok = tmp.size[0] > 0 and np.abs(read(tmp)[:, 2] - SIG).max(initial=1.0) < 1.5 / 255
    finally:
        bpy.data.images.remove(tmp)
    if not ok:
        return False
    _load(obj, "MSK", path)
    for k in CAM_KINDS:
        if get(obj, k) is not None:
            get(obj, k)[VERSION_KEY] = VERSION
    return True


def _migrate(obj, scene=None):
    """Paint textures of an older version -> 5, the same look. First to the layers' form
    (c1, c2, mask; cameras 1-3 on top):
    1 (alpha = painted here, MSK_ = the mask): the vertex layers under the unpainted pixels,
      MSK_ into the mask;
    2 / 4: CMX1_ alpha = the mask;
    3 (MSK_ grey = the mask, cameras 4-6 on top; never saved to a file): one_on_top.
    Then from_layers; MSK_ is made (or written over) in the new format."""
    from . import core
    scene = scene or bpy.context.scene
    msk = get(obj, "MSK")
    cams = {k: get(obj, k) for k in CAM_KINDS}
    vers = {k: _version(obj, img) for k, img in cams.items() if img is not None}
    if not vers or min(vers.values()) >= VERSION:
        return
    if _converted_elsewhere(obj, scene):
        print(f"[MultiCamProject] {obj.name}: paint textures already converted (MSK_ found)")
        core.ensure_material(obj)
        return
    px = {k: read(img).copy() for k, img in cams.items() if img is not None}
    if "CMX1" not in px:
        px["CMX1"] = layer_pixels(obj, "CMX1")
    two = _two(obj)
    if two and "CMX2" not in px:
        try:
            px["CMX2"] = layer_pixels(obj, "CMX2")
        except RuntimeError as e:
            print(f"[MultiCamProject] {obj.name}: VCMix2 not read ({e}) - CMX2_ empty")
            px["CMX2"] = np.zeros_like(px["CMX1"])
    if any(v == 3 for v in vers.values()):
        mask = read(msk)[:, 0] if msk is not None and msk.has_data else np.ones(len(px["CMX1"]))
        if two:
            px["CMX1"][:, :3], px["CMX2"][:, :3] = one_on_top(px["CMX1"][:, :3], px["CMX2"][:, :3])
    else:
        for kind, img in cams.items():
            if img is not None and vers[kind] < 2:      # 1 -> 2
                old = px[kind]
                new = layer_pixels(obj, kind)
                a = old[:, 3:4]
                new[:, :3] = new[:, :3] * (1.0 - a) + old[:, :3] * a
                if kind == "CMX1" and msk is not None and msk.has_data:
                    m = read(msk)
                    new[:, 3] = new[:, 3] * (1.0 - m[:, 3]) + m[:, 0] * m[:, 3]
                px[kind] = new
        mask = px["CMX1"][:, 3].copy()
    out = from_layers(px["CMX1"][:, :3], px["CMX2"][:, :3] if two else None, mask)
    for kind, arr in out.items():           # new files first: a failure changes nothing
        if get(obj, kind) is None or kind == "MSK":
            _create(obj, kind, scene, arr)
    for kind in CAM_KINDS:
        img = get(obj, kind)
        if img is not None and kind in out:
            setup_image(img)
            img.pixels.foreach_set(np.ascontiguousarray(out[kind], np.float32).ravel())
            img.update()
            img[VERSION_KEY] = VERSION
            bump(img)
    print(f"[MultiCamProject] {obj.name}: paint textures converted - MSK_ R = scan, "
          "G = cameras 1-3 / 4-6, no alpha")
    core.ensure_material(obj)


def one_on_top(c1, c2):
    """Camera weights (R, G, B (n, 3) each) with cameras 4-6 on top (w1 = s1 x (1 - s2),
    w2 = s2; version 3) -> with cameras 1-3 on top (w1 = s1, w2 = s2 x (1 - s1)), the same
    mix. Returns new (c1, c2)."""
    c1, c2 = _capped(c1), _capped(c2)
    c1 = c1 * (1.0 - _sums(c2))[:, None]
    c2 = np.clip(c2 / np.maximum(1.0 - _sums(c1), 1e-6)[:, None], 0.0, 1.0)
    return c1, c2


def migrate_all():
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and not obj.library and any(
                _version(obj, img) < VERSION for k, img in images(obj).items() if k != "MSK"):
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
    """img's pixels = arr ((n, 4) float) as one undo step Blender records itself.

    Blender's image undo takes a step's "before" from the previous image step when that
    one holds the same image, and stores the image as it is when the step ends. So: an
    Invert of no channel (a step that records the image as it is now), then the pixels
    with red inverted, then Invert red - a step from "as it was" to arr. Ctrl+Z gives the
    old image back, Redo gives arr, also across other undo steps (camera switches). The
    first step changes nothing: one Ctrl+Z more. (Before 2026-10-19 a single Invert step
    recorded the inverted image: undo after a camera switch brought it back.)"""
    arr = np.ascontiguousarray(arr, np.float32)
    ctx = _context(img)
    done = False
    if ctx is not None:
        with bpy.context.temp_override(**ctx):
            if bpy.ops.image.invert.poll():
                bpy.ops.image.invert()
                inv = arr.copy()
                inv[:, 0] = 1.0 - inv[:, 0]
                img.pixels.foreach_set(inv.ravel())
                bpy.ops.image.invert(invert_r=True)
                done = True
    if not done:
        img.pixels.foreach_set(arr.ravel())
    img.update()
    bump(img)


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


def fill_fn(cov, rgb):
    """fn for queue() on a camera texture: `rgb` over the covered pixels (cov: bool or
    0-1)."""
    c = cov.astype(np.float32)[:, None]

    def fn(arr):
        arr[:, :3] = arr[:, :3] * (1.0 - c) + np.asarray(rgb, np.float32) * c
        arr[:, 3] = 1.0
        return arr
    return fn


def mask_fn(cov, value, group=None):
    """fn for queue() on MSK_: projected = `value` (1: R = 0, 0: R = 1, the scan) over the
    covered pixels (cov None: all of them); `group` (0 / 1): G too (which cameras show)."""
    c = None if cov is None else cov.astype(np.float32)

    def fn(arr):
        k = 1.0 if c is None else c
        arr[:, 0] = arr[:, 0] * (1.0 - k) + (1.0 - value) * k
        if group is not None:
            arr[:, 1] = arr[:, 1] * (1.0 - k) + group * k
        arr[:, 2] = SIG
        arr[:, 3] = 1.0
        return arr
    return fn


def group_fn(g):
    """fn for queue() on MSK_: G = g ((n,): which cameras show), R stays."""
    def fn(arr):
        arr[:, 1] = g
        arr[:, 2] = SIG
        arr[:, 3] = 1.0
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


def save_dirty(objs=None):
    """Every dirty paint texture (of `objs`, default: all objects) into its file. Returns
    the names saved."""
    saved = []
    for obj in (bpy.data.objects if objs is None else objs):
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
    _rev.clear()
    _queue.clear()
    bpy.app.timers.register(_migrate_later, first_interval=1.0)


def register():
    bpy.app.handlers.redo_post.append(_on_undo)
    bpy.app.handlers.undo_post.append(_on_undo)
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_migrate_later, first_interval=1.0)     # an add-on reload too


def unregister():
    for handlers, fn in ((bpy.app.handlers.redo_post, _on_undo),
                         (bpy.app.handlers.undo_post, _on_undo),
                         (bpy.app.handlers.save_pre, _on_save_pre),
                         (bpy.app.handlers.load_post, _on_load)):
        if fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
