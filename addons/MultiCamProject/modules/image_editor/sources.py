"""Inpaint sources: the images Inpaint works on, and their `_inpaint` copies on disk.

  ALB  an object's ALB_     ALB_<name>_inpaint.png in the bake folder (bake.alb_inpaint)
  BAO  an object's BAo_     BAo_<name>_inpaint.png in the bake folder (bake.ba_inpaint)
  BG   a camera's photo     <bake folder>/_inpaint/<photo>_inpaint.png - shared by every
                            object the camera projects on: the camera's background image
                            becomes the copy, the photo waits in camera.bg_original

The original is never written. Save writes the _inpaint file (and the materials take it),
Reload copies the original over it, Remove sends it to the Recycle Bin and the materials take
the original again. Nothing here runs on a timer: only these operations relink."""
import os
from collections import namedtuple

import bpy

SUFFIX = "_inpaint"
BG_DIR = "_inpaint"         # <bake folder>/_inpaint: the photos' copies (Clean never looks there)

Source = namedtuple("Source", "kind owner")      # 'ALB' / 'BAO' / 'BG', object or camera
KINDS = {'ALB': "ALB_", 'BAO': "BAo_", 'BG': "Camera BG"}


def _common():
    from ..baking import common
    return common


def _cp():
    from ..camera_project import core
    return core


def _cam_data(cam):
    return getattr(cam, "multicamproject_camera", None)


# ---------------------------------------------------------------- what a source holds

def original(src):
    """The image the source starts from (never written)."""
    if src.kind == 'BG':
        cd = _cam_data(src.owner)
        if cd is not None and cd.bg_original is not None:
            return cd.bg_original
        return _cp().cam_image(src.owner)
    d = src.owner.multicamproject_bake
    return d.alb_image if src.kind == 'ALB' else d.ba_image


def inpaint_image(src):
    """The source's _inpaint image, or None."""
    if src.kind == 'BG':
        cd = _cam_data(src.owner)
        if cd is None or cd.bg_original is None:
            return None
        if cd.bg_inpaint_off is not None:       # the original shown, the inpaint parked
            return cd.bg_inpaint_off
        return _cp().cam_image(src.owner)
    d = src.owner.multicamproject_bake
    return d.alb_inpaint if src.kind == 'ALB' else d.ba_inpaint


def uses_inpaint(src):
    """The source shows its _inpaint, not switched back to the original (06's dropdown)."""
    if src.kind == 'BG':
        cd = _cam_data(src.owner)
        return cd is None or cd.bg_inpaint_off is None
    d = src.owner.multicamproject_bake
    return d.alb_use_inpaint if src.kind == 'ALB' else d.ba_use_inpaint


def show_prop(src):
    """(data, property) of the source's original / _inpaint dropdown."""
    if src.kind == 'BG':
        return _cam_data(src.owner), "bg_show"
    return src.owner.multicamproject_bake, "alb_show" if src.kind == 'ALB' else "ba_show"


def has_inpaint(src):
    img = inpaint_image(src)
    return img is not None and _common().file_ok(img)


def current(src):
    """What the source shows now: its _inpaint when there is one (unless switched to the
    original in 06), else the original."""
    if not uses_inpaint(src):
        return original(src)
    return inpaint_image(src) if has_inpaint(src) else original(src)


def label(src):
    if src.kind == 'BG':
        return f"Camera BG of {src.owner.name}"
    return f"{KINDS[src.kind]} of {src.owner.name}"


def classify(img):
    """The Source `img` belongs to (an original or its _inpaint), or None."""
    if img is None:
        return None
    for o in bpy.data.objects:
        d = getattr(o, "multicamproject_bake", None)
        if d is None or o.type != 'MESH':
            continue
        if img in (d.alb_image, d.alb_inpaint):
            return Source('ALB', o)
        if img in (d.ba_image, d.ba_inpaint):
            return Source('BAO', o)
    for cam in bpy.data.objects:
        if cam.type != 'CAMERA':
            continue
        cd = _cam_data(cam)
        if img == _cp().cam_image(cam) or (cd is not None and img in (cd.bg_original,
                                                                      cd.bg_inpaint_off)):
            return Source('BG', cam)
    return None


def object_sources(obj):
    """The sources of a mesh: ALB_, BAo_ (when baked), then its slot cameras' photos."""
    out = []
    d = getattr(obj, "multicamproject_bake", None)
    if d is None or obj.type != 'MESH':
        return out
    if d.alb_image is not None:
        out.append(Source('ALB', obj))
    if d.ba_image is not None:
        out.append(Source('BAO', obj))
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None and cam.is_setup:
        for c in _cp().get_slots(cam):
            if c is not None and _cp().cam_image(c) is not None:
                out.append(Source('BG', c))
    return out


def preferred(obj):
    """The image an editor loads for a selected object: ALB_ inpainted > ALB_ > BAo_
    inpainted > BAo_ > nothing."""
    d = getattr(obj, "multicamproject_bake", None) if obj is not None else None
    if d is None:
        return None
    common = _common()
    for img in (d.alb_inpaint, d.alb_image, d.ba_inpaint, d.ba_image):
        if img is not None and common.file_ok(img):
            return img
    return None


# ---------------------------------------------------------------- files

def inpaint_name(src):
    if src.kind == 'BG':
        photo = original(src)
        stem = os.path.splitext(os.path.basename(photo.filepath or photo.name))[0]
        return stem + SUFFIX
    common = _common()
    return (common.alb_name(src.owner) if src.kind == 'ALB'
            else common.ba_name(src.owner)) + SUFFIX


def inpaint_path(src, scene):
    common = _common()
    if src.kind == 'BG':
        return os.path.join(common.output_dir(scene), BG_DIR, inpaint_name(src) + ".png")
    return common.texture_path(scene, inpaint_name(src))


def _point(src, img):
    """The source's _inpaint pointer (None: back to the original)."""
    if src.kind == 'BG':
        cam, cd = src.owner, _cam_data(src.owner)
        bg = _cp().bg_entry(cam, create=True)
        cd.bg_inpaint_off = None    # a saved inpaint shows; a removed one: nothing parked
        if img is not None:
            if cd.bg_original is None:
                cd.bg_original = bg.image
            bg.image = img
        else:
            if cd.bg_original is not None:
                bg.image = cd.bg_original
            cd.bg_original = None
        return
    d = src.owner.multicamproject_bake
    setattr(d, "alb_inpaint" if src.kind == 'ALB' else "ba_inpaint", img)
    # a saved inpaint is what shows (the switch back on)
    setattr(d, "alb_use_inpaint" if src.kind == 'ALB' else "ba_use_inpaint", True)


def _image_on(path, name, colorspace, skip=None):
    """The image of `path` (reloaded), named `name` - never `skip` (the one just saved there,
    removed by the caller)."""
    from ..baking import engine
    img = next((i for i in bpy.data.images if i != skip and i.source == 'FILE'
                and _common().image_file(i) == os.path.normpath(path)), None)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
    if img.name != name:
        other = bpy.data.images.get(name)
        if other is not None and other != img:
            other.name = name + "_stale"
        img.name = name
    if img.colorspace_settings.name != colorspace:
        img.colorspace_settings.name = colorspace
    engine.link_file(img, path)         # reload: the new pixels
    return img


def save(src, scene, image):
    """`image` (pixels set) written as the source's _inpaint file; the materials take it.
    Returns the _inpaint image."""
    import numpy as np
    w, h = image.size
    px = np.empty(w * h * 4, np.float32)
    image.pixels.foreach_get(px)
    return save_pixels(src, scene, px.reshape(h, w, 4), skip=image)


def save_pixels(src, scene, px, skip=None):
    """`px` ((h, w, >=3) floats as stored, rows bottom-up) written as the source's _inpaint
    PNG - by pngio's parallel writer (an 8K texture in ~0.5 s, Blender's save takes ~2 s) -
    and the materials take it. Returns the _inpaint image."""
    from ..baking import owned
    from ..baking.normal import pngio
    path = inpaint_path(src, scene)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pngio.write_rgb8(path, px)
    orig = original(src)
    img = _image_on(path, inpaint_name(src),
                    orig.colorspace_settings.name if orig is not None else 'sRGB', skip=skip)
    _point(src, img)
    if src.kind != 'BG':
        owned.add(scene, [path])
    relink(src, scene)
    return img


def reload(src, scene):
    """The original's pixels into the _inpaint file again (a fresh start). Returns it."""
    orig = original(src)
    path = _common().image_file(orig)
    if not path or not os.path.isfile(path):
        raise RuntimeError(f"{orig.name if orig else 'The original'} has no file")
    tmp = bpy.data.images.load(path, check_existing=False)
    try:
        tmp.pixels[0]           # decoded before it is written in another format
        return save(src, scene, tmp)
    finally:
        bpy.data.images.remove(tmp)


def remove(src, scene):
    """The _inpaint file to the Recycle Bin; the source shows its original again."""
    from ..export import fixes
    img = inpaint_image(src)
    path = _common().image_file(img) if img is not None else ""
    _point(src, None)
    if img is not None and img.users == 0:
        bpy.data.images.remove(img)
    if path:
        fixes.to_recycle_bin([path])
    relink(src, scene)


def relink(src, scene):
    """Everything that shows the source takes what it shows now (current()):
    ALB  MAT_ (rebuilt), BAO  MCP_'s BAKED frame, BG  MCP_'s camera textures of every object
    the camera is a slot of."""
    from ... import steps
    from ..baking import cache, material
    cache.clear()
    steps.forget()
    cp = _cp()
    if src.kind == 'ALB':
        if src.owner.multicamproject_bake.material is not None:
            material.build(src.owner, scene, place_slots=False)
        return
    if src.kind == 'BAO':
        if getattr(src.owner, "multicamproject_cam", None) and src.owner.multicamproject_cam.is_setup:
            cp.ensure_material(src.owner)
        return
    for o in bpy.data.objects:
        cam = getattr(o, "multicamproject_cam", None)
        if o.type == 'MESH' and cam is not None and cam.is_setup and src.owner in cp.get_slots(cam):
            cp.fill_cam_images(o)


# ---------------------------------------------------------------- the 3D view's buttons

def draw_button(layout, kind, owner):
    """The Inpaint button of a source (blue while it has a saved _inpaint): shows it in the
    Image Editor and starts Inpaint there."""
    if owner is None:
        return
    op = layout.operator("multicamproject.inpaint_load", text="", icon='ANCHOR_CENTER',
                         depress=has_inpaint(Source(kind, owner)))
    op.kind, op.owner = kind, owner.name


def draw_ready(obj):
    """The Inpaint buttons show once the object's BAo_ is baked (03 done)."""
    from ... import module_manager, steps
    return module_manager.is_loaded("image_editor") and steps.bao_fresh(obj)
