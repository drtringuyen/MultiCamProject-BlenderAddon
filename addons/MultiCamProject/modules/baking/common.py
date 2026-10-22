"""Names and small lookups shared by the baking and export modules."""
import os
from contextlib import contextmanager

import bpy

UV_NORMAL = "uv_normal"
COLOR = "Color"
CP_MOD = "GN-CameraProject"
RESOLUTIONS = (1024, 2048, 4096, 8192)      # the toggles next to Bake
REFERENCE = 8192        # margin and high-pass radius are set at 8K, scaled to the resolution


def data(obj):
    return obj.multicamproject_bake


def settings(scene):
    return scene.multicamproject_bake_settings


def resolution(obj, scene=None):
    """The texture size `obj` bakes at: its own 1K-8K pick (06 / the EXPORT list), or the
    scene's resolution (A = Auto)."""
    size = obj.multicamproject_bake.tex_size
    if size != 'AUTO':
        return int(size)
    return settings(scene or bpy.context.scene).resolution


def cage(obj):
    """`obj`'s Cage Extrusion for Bake from Source (one per object)."""
    return obj.multicamproject_bake.cage


def margin_px(s, size=None):
    """The bake margin at `size` (default: the scene's resolution); Margin is set at 8K."""
    size = size or s.resolution
    return max(1, round(s.margin * size / REFERENCE)) if s.margin else 0


def alb_name(obj):
    from .naming import typed_name
    return typed_name("ALB", obj)


def nor_name(obj):
    from .naming import typed_name
    return typed_name("NOR", obj)


# the add-on's texture files: the final ALB_/NOR_ (<export>/Textures) and the work textures
# of the two routes - BAo_/BNo_ baked from the Original (Bake from Source), BAp_/BNp_ from
# the Projection. Only files with these prefixes are ever cleaned up
FINAL_PREFIXES = ("ALB_", "NOR_")
PAINT_PREFIXES = ("MSK_", "CMX1_", "CMX2_")     # Paint Textures (camera_project.paint_tex)
WORK_PREFIXES = ("BAo_", "BNo_", "BNoG_", "BAp_", "BNp_") + PAINT_PREFIXES
OWN_PREFIXES = FINAL_PREFIXES + WORK_PREFIXES
LEGACY_PREFIXES = ("BA_", "BN_")        # packed BA_/BN_ before 2026-10-05 (now BAo_/BNo_)


def ba_name(obj):
    """BAo_<name>: the albedo baked from the Original (Bake Source)."""
    from .naming import typed_name
    return typed_name("BAo", obj)


def bn_name(obj):
    """BNo_<name>: the normal baked from the Original (Bake Source)."""
    from .naming import typed_name
    return typed_name("BNo", obj)


def bng_name(obj):
    """BNoG_<name>: the original's side of the normal generated from BAo_ (High-pass / AI)."""
    from .naming import typed_name
    return typed_name("BNoG", obj)


def bap_name(obj):
    """BAp_<name>: the albedo rendered from the Projection."""
    from .naming import typed_name
    return typed_name("BAp", obj)


def bnp_name(obj):
    """BNp_<name>: the normal generated from BAp_."""
    from .naming import typed_name
    return typed_name("BNp", obj)


def msk_name(obj):
    """MSK_<name>: the painted blend mask."""
    from .naming import typed_name
    return typed_name("MSK", obj)


def cmx1_name(obj):
    """CMX1_<name>: the painted mix of cameras 1-3."""
    from .naming import typed_name
    return typed_name("CMX1", obj)


def cmx2_name(obj):
    """CMX2_<name>: the painted mix of cameras 4-6."""
    from .naming import typed_name
    return typed_name("CMX2", obj)


PAINT_IMAGES = (("msk_image", msk_name), ("cmx1_image", cmx1_name), ("cmx2_image", cmx2_name))


def alb_inpaint_name(obj):
    """ALB_<name>_inpaint: ALB_ inpainted (06)."""
    return alb_name(obj) + "_inpaint"


def ba_inpaint_name(obj):
    """BAo_<name>_inpaint: BAo_ inpainted (06)."""
    return ba_name(obj) + "_inpaint"


# the object's own files beside the bakes: renamed and owned with it
OBJECT_IMAGES = PAINT_IMAGES + (("alb_inpaint", alb_inpaint_name), ("ba_inpaint", ba_inpaint_name))


def mat_name(obj):
    from .naming import typed_name
    return typed_name("MAT", obj)


def has_uv_normal(obj):
    return obj.type == 'MESH' and obj.data.uv_layers.get(UV_NORMAL) is not None


COLLAPSED = 0.25        # share of faces without UV area: uv_normal is broken
MIN_UV_AREA = 0.01      # the islands together cover less of 0-1 than this: broken too


def uv_coverage(obj):
    """(share of uv_normal's triangles with (almost) no UV area, the islands' total area in
    0-1 units). A scan's uv_normal after Decimate collapses into points: the bake writes next
    to no pixels (black BA_ / ALB_)."""
    import numpy as np
    me = obj.data
    uv = me.uv_layers.get(UV_NORMAL)
    if uv is None or obj.mode == 'EDIT':     # Edit Mode: the mesh has no UV data until left
        return 0.0, 1.0
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    if not nt:
        return 0.0, 1.0
    co = np.empty(len(me.loops) * 2, np.float32)
    uv.data.foreach_get("uv", co)
    loops = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("loops", loops)
    t = co.reshape(-1, 2)[loops].reshape(nt, 3, 2).astype(np.float64)
    a = 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1])
                     - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))
    return float((a < 5e-11).mean()), float(a.sum())


def uv_collapsed_text(obj):
    """'' or why uv_normal cannot be baked into."""
    dead, area = uv_coverage(obj)
    if dead > COLLAPSED:
        return f"{UV_NORMAL} collapsed ({dead:.0%} of faces) - unwrap it again (U)"
    if area < MIN_UV_AREA:
        return f"{UV_NORMAL} covers {area:.1%} of the texture - unwrap it again (U)"
    return ""


def cp_modifier(obj):
    """The camera projection modifier, or None (baking works on any mesh)."""
    from ..camera_project import gn_builder
    mod = obj.modifiers.get(CP_MOD)
    if mod and mod.type == 'NODES':
        return mod
    return next((m for m in obj.modifiers if m.type == 'NODES' and gn_builder.is_main(m.node_group)),
                None)


def mesh_objects(objs):
    return [o for o in objs if o.type == 'MESH']


WORK_WINDOW_KEY = "multicamproject_work_of"     # scene of a Work Window: the main .blend


def is_work_window(scene=None):
    """A Work Window (linking module): an untitled Blender holding one object sent out of a
    main file. Its texture links point at the main file's real bakes: nothing here may
    delete or recycle files."""
    scenes = [scene] if scene is not None else list(bpy.data.scenes)
    return any(sc.get(WORK_WINDOW_KEY) for sc in scenes)


def output_dir(scene):
    return bpy.path.abspath(settings(scene).output_dir)


EXPORT_TEXTURES = "Textures"    # <export folder>/Textures: the FBX's textures


def export_textures_dir(scene):
    """<export folder>/Textures, next to the FBX: Export copies ALB_ / NOR_ there (the
    ALB_ as inpainted when it was). '' without the Export module or its folder."""
    ex = getattr(scene, "multicamproject_export", None)
    if ex is None or not ex.folder:
        return ""
    return os.path.normpath(os.path.join(bpy.path.abspath(ex.folder), EXPORT_TEXTURES))


def texture_path(scene, image_name):
    """<name>.png in the bake folder - every bake, ALB_ / NOR_ too (since 2026-10-12: Export
    copies them into <export folder>/Textures)."""
    return os.path.join(output_dir(scene), image_name + ".png")


def override_file(scene, image_name):
    """<Texture Override folder>/<name>.png when the file is there (a finished ALB_ / NOR_
    that replaces the bake), else ''."""
    props = getattr(scene, "multicamproject_props", None)
    top = getattr(props, "texture_override_folder", "")
    if not top:
        return ""
    from . import cache
    path = os.path.normpath(os.path.join(bpy.path.abspath(top), image_name + ".png"))
    return path if cache.isfile(path) else ""


def bao_source(obj):
    """The BAo_ MCP_ and 05 take: BAo_<name>_inpaint when the user inpainted and saved it,
    else BAo_<name>."""
    d = data(obj)
    ip = getattr(d, "ba_inpaint", None)
    if ip is not None and getattr(d, "ba_use_inpaint", True) and file_ok(ip):
        return ip
    return d.ba_image


def alb_source(obj):
    """The ALB_ to export and to make NOR_ from: ALB_<name>_inpaint when the user inpainted
    and saved it (and did not switch back to the original in 06), else ALB_<name>."""
    d = data(obj)
    ip = getattr(d, "alb_inpaint", None)
    if ip is not None and getattr(d, "alb_use_inpaint", True) and file_ok(ip):
        return ip
    return d.alb_image


def image_file(img):
    """Absolute path of an image's file ('' when it has none). Never loads pixels."""
    if img is None or img.source != 'FILE' or not img.filepath:
        return ""
    # normalized: a '//..\..' path can pass Windows' 260 characters while the file's own does not
    return os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))


def file_label(img):
    """'<folder>/<file>' of an image's file - which texture it is, at a glance - else its
    name."""
    path = image_file(img)
    if not path:
        return img.name if img is not None else "(none)"
    return f"{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}"


def self_bake(obj):
    """2D Only Project: no Bake Source - BAo_ is the object's own scan textures (scan UV,
    the faces' uv_index slots) rendered onto uv_normal."""
    d = data(obj)
    if d.bake_source is not None or d.handmade:
        return False
    from ... import steps
    return steps.workflow(obj) == 'PROJECT'


def file_ok(img):
    from . import cache
    return cache.isfile(image_file(img))


def selected_meshes(context):
    """The selected meshes, without one that another selected mesh bakes from (its Bake
    Source - the scan stays selected next to its retopo / Remesh copy)."""
    objs = [o for o in context.selected_objects if o.type == 'MESH']
    sources = {data(o).bake_source for o in objs}
    return [o for o in objs if o not in sources]


def _layer_coll(view_layer, coll):
    """The view layer's entry for `coll`, looked up fresh: excluding a collection rebuilds
    the layer collections, so an entry held from before is stale (writes do nothing)."""
    todo = [view_layer.layer_collection]
    while todo:
        lc = todo.pop()
        if lc.collection == coll:
            return lc
        todo.extend(lc.children)
    return None


def _colls_holding(view_layer, objs):
    """Every collection (parents too) on the way to one of `objs`."""
    out = []

    def walk(lc):
        hit = any(o in objs for o in lc.collection.objects)
        for ch in lc.children:
            hit = walk(ch) or hit
        if hit:
            out.append(lc.collection)       # children before their parents
        return hit
    walk(view_layer.layer_collection)
    return out


@contextmanager
def shown(context, objs):
    """`objs` in the view layer and visible for the time of a bake or an export: only what
    the view layer shows is evaluated and bakeable - an excluded collection (a Remesh
    original kept out of the way, an excluded EXPORT) would bake nothing or export raw
    meshes. Collections, objects and render visibility are put back afterwards."""
    vl = context.view_layer
    objs = set(objs)
    colls = _colls_holding(vl, objs)
    saved_c = []
    for c in colls:
        lc = _layer_coll(vl, c)
        saved_c.append((c, lc.exclude, lc.hide_viewport))
    saved_o = [(o, o.hide_viewport, o.hide_render) for o in objs]
    hidden = {}
    try:
        for c in reversed(colls):               # parents first
            lc = _layer_coll(vl, c)
            if lc.exclude:
                lc.exclude = False
        for c in colls:
            _layer_coll(vl, c).hide_viewport = False
        # a collection shown only for this: the rest of it stays out of sight (e.g. every
        # other scan in "Original Mesh" - only the object and its original show)
        opened = [c for c, exc, hid in saved_c if exc or hid]
        for c in opened:
            for o in c.all_objects:
                if o not in objs and o not in hidden and vl.objects.get(o.name) == o:
                    hidden[o] = o.hide_get(view_layer=vl)
                    o.hide_set(True, view_layer=vl)
        for o in objs:
            o.hide_viewport = o.hide_render = False
            if vl.objects.get(o.name) == o:
                hidden.setdefault(o, o.hide_get(view_layer=vl))
                o.hide_set(False, view_layer=vl)
        vl.update()
        yield
    finally:
        for o, h in hidden.items():
            try:
                o.hide_set(h, view_layer=vl)
            except (ReferenceError, RuntimeError):
                pass
        for o, hv, hr in saved_o:
            try:
                o.hide_viewport, o.hide_render = hv, hr
            except ReferenceError:
                pass
        for c, exc, hid in saved_c:             # children first, then their parents
            lc = _layer_coll(vl, c)
            if lc is not None:
                lc.hide_viewport = hid
                if lc.exclude != exc:
                    lc.exclude = exc


EXPORT = "EXPORT"


def _in_scene(coll, root):
    return coll == root or any(_in_scene(coll, c) for c in root.children)


def export_collection(scene):
    """The EXPORT collection (picked in the Linking panel, else named EXPORT), only when it
    is part of `scene`."""
    from ... import roles
    coll = roles.find(scene, 'EXPORT')
    if coll is None or not _in_scene(coll, scene.collection):
        return None
    return coll


def export_objects(scene):
    """Meshes of the active scene's EXPORT collection (sub-collections included)."""
    coll = export_collection(scene)
    if coll is None:
        return []
    return [o for o in coll.all_objects if o.type == 'MESH' and scene.objects.get(o.name) == o]
