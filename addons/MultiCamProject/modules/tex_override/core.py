"""TexOverride: where each object's ALB_ and NOR_ come from - Bake or Load.

    Bake  the add-on makes it (05 / 07) into the bake folder: 01.Bake/ALB_<name>.png
    Load  a finished file in the Overrides folder (02.Overrides/ALB_<name>.png, next to
          01.Bake): the image points right at it - nothing is baked or copied in between

The two modes are on the object (multicamproject_bake.alb_mode / nor_mode), saved in the
.blend and carried by Send Out / Send Back. The files are never packed. A Loaded texture is
current as long as its file is there (no bake stamp); Lock Finish then copies it into
Export like a baked one. Switching back to Bake asks for a new bake (the stamp is
outdated on purpose).

Data-level helpers: baking, export and linking call these whether the module's panel
buttons are on or not."""
import os

import bpy

BAKE, LOAD = 'BAKE', 'LOAD'
KINDS = ('ALB', 'NOR')
DEFAULT_FOLDER = "//02.Overrides/"
# a stamp no bake state equals: the Bake side must be made again (Load -> Bake)
SWITCHED = "texoverride-switched"


def _common():
    from ..baking import common
    return common


def mode(obj, kind):
    d = getattr(obj, "multicamproject_bake", None)
    return getattr(d, "alb_mode" if kind == 'ALB' else "nor_mode", BAKE) if d else BAKE


def is_load(obj, kind):
    return mode(obj, kind) == LOAD


def image(obj, kind):
    d = _common().data(obj)
    return d.alb_image if kind == 'ALB' else d.nor_image


def name(obj, kind):
    c = _common()
    return c.alb_name(obj) if kind == 'ALB' else c.nor_name(obj)


def folder(scene=None):
    """The Overrides folder (absolute, normalized) - '' when none is set. A work window
    holds the main file's folder as an absolute path (Send Out writes it so)."""
    scene = scene or bpy.context.scene
    props = getattr(scene, "multicamproject_props", None)
    top = getattr(props, "texture_override_folder", "") if props is not None else ""
    return os.path.normpath(bpy.path.abspath(top)) if top else ""


def override_path(scene, obj, kind):
    """<Overrides>/ALB_<name>.png (NOR_) - where the file is or would be ('' without a
    folder)."""
    top = folder(scene)
    return os.path.join(top, name(obj, kind) + ".png") if top else ""


def available(scene, obj, kind):
    """The override file is there: Load can be picked."""
    from ..baking import cache
    path = override_path(scene, obj, kind)
    return bool(path) and cache.isfile(path)


def in_folder(path, scene=None):
    """`path` lies in the Overrides folder (never recycled with its object)."""
    top = folder(scene)
    if not top or not path:
        return False
    return (os.path.normcase(os.path.dirname(os.path.abspath(path)))
            == os.path.normcase(os.path.abspath(top)))


def problem(scene, obj, kind):
    """'' or why a Loaded texture can't be used (the file is gone)."""
    if not is_load(obj, kind):
        return ""
    if not folder(scene):
        return "no Overrides folder set (Project Setup)"
    if not available(scene, obj, kind):
        return f"override missing: {os.path.basename(override_path(scene, obj, kind))}"
    return ""


def _png_width(path):
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return 0
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return 0
    return struct.unpack(">II", head[16:24])[0]


def _point(obj, kind, path):
    """The object's ALB_ / NOR_ image at `path` (made when it has none), Non-Color for NOR_,
    no float copy of an 8K ALB_ kept in memory."""
    from ..baking import engine
    d = _common().data(obj)
    nm = name(obj, kind)
    normal = kind == 'NOR'
    img = image(obj, kind) or bpy.data.images.get(nm)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
    if img.name != nm and not bpy.data.images.get(nm):
        img.name = nm
    img.colorspace_settings.name = 'Non-Color' if normal else 'sRGB'
    engine.link_file(img, path)
    if img.is_float and not normal:
        img.buffers_free()
    if normal:
        d.nor_image = img
    else:
        d.alb_image = img
    return img


def link(scene, obj, kind):
    """Load: the image at the override file, MAT_ rebuilt. Raises when the file is gone."""
    why = problem(scene, obj, kind)
    if why:
        raise RuntimeError(why)
    from ..baking import cache, gn_final, material
    path = override_path(scene, obj, kind)
    _point(obj, kind, path)
    d = _common().data(obj)
    size = _png_width(path)
    if kind == 'ALB':
        d.alb_size = size
    else:
        d.nor_size = size
        d.nor_source_used = LOAD
    material.build(obj, scene)
    gn_final.write_inputs(obj, scene)
    if kind == 'ALB':
        gn_final.set_final(obj, scene, True)
    cache.clear()
    return path


def unlink(scene, obj, kind):
    """Bake: the image back at the bake folder's file (missing until it is baked) and its
    stamp outdated - the bake is made again by hand."""
    c = _common()
    d = c.data(obj)
    img = image(obj, kind)
    if img is not None and in_folder(c.image_file(img), scene):
        _point(obj, kind, c.texture_path(scene, name(obj, kind)))
    if kind == 'ALB':
        d.fingerprint = SWITCHED
    else:
        d.nor_fingerprint = SWITCHED
    from ..baking import material
    material.build(obj, scene)


def set_mode(scene, obj, kind, new):
    """Switch one texture. Load needs the override file."""
    d = _common().data(obj)
    if d.locked:
        raise RuntimeError("Locked Finish - unlock it in 08 first")
    if new == mode(obj, kind):
        return
    if new == LOAD:
        if not available(scene, obj, kind):
            raise RuntimeError(f"no {os.path.basename(override_path(scene, obj, kind))} in "
                               "the Overrides folder")
        setattr(d, "alb_mode" if kind == 'ALB' else "nor_mode", LOAD)
        link(scene, obj, kind)
    else:
        setattr(d, "alb_mode" if kind == 'ALB' else "nor_mode", BAKE)
        unlink(scene, obj, kind)
    _forget()


def apply_modes(scene, obj):
    """Each texture where its mode says (after Receive / a load): Load at the override,
    Bake never left pointing into the Overrides folder."""
    for kind in KINDS:
        if is_load(obj, kind):
            if available(scene, obj, kind):
                img = image(obj, kind)
                c = _common()
                if img is None or (os.path.normcase(c.image_file(img))
                                   != os.path.normcase(override_path(scene, obj, kind))):
                    link(scene, obj, kind)
        else:
            img = image(obj, kind)
            if img is not None and in_folder(_common().image_file(img), scene):
                unlink(scene, obj, kind)


def refresh(scene, obj, kind):
    """The texture's file read again (edited outside Blender). Returns 'reloaded',
    'unsaved' (changed in Blender: kept) or ''."""
    from ..baking import cache
    from ..baking.normal import reload_from_disk
    if is_load(obj, kind) and available(scene, obj, kind):
        img = image(obj, kind)
        c = _common()
        if img is None or (os.path.normcase(c.image_file(img))
                           != os.path.normcase(override_path(scene, obj, kind))):
            link(scene, obj, kind)
            _forget()
            return "reloaded"
    img = image(obj, kind)
    what = reload_from_disk(img) if img is not None else ""
    if kind == 'ALB':
        d = _common().data(obj)
        ip = getattr(d, "alb_inpaint", None)
        if ip is not None and not is_load(obj, kind):
            reload_from_disk(ip)
    if what == "reloaded":
        img.update()
    cache.clear()
    _forget()
    return what


def delete_override(scene, obj, kind):
    """The override file to the Recycle Bin; the texture goes back to Bake (bake it
    again). Returns the path removed."""
    d = _common().data(obj)
    if d.locked:
        raise RuntimeError("Locked Finish - unlock it in 08 first")
    path = override_path(scene, obj, kind)
    if not path or not os.path.isfile(path):
        raise RuntimeError("no override file")
    try:
        from ..export.fixes import to_recycle_bin
    except ImportError:
        raise RuntimeError("needs the Export module (Recycle Bin)")
    if is_load(obj, kind):
        setattr(d, "alb_mode" if kind == 'ALB' else "nor_mode", BAKE)
        unlink(scene, obj, kind)
    if not to_recycle_bin([path]):
        raise RuntimeError(f"{os.path.basename(path)} could not be removed")
    from ..baking import cache
    cache.clear()
    _forget()
    return path


def copy_to_new_name(scene, obj, kind, old_name):
    """The object was renamed: a Loaded texture's override file is copied to the new name
    (the old file stays - another .blend may use it), the image pointed at the copy."""
    import shutil
    top = folder(scene)
    if not top or not is_load(obj, kind):
        return ""
    old = os.path.join(top, old_name + ".png")
    new = override_path(scene, obj, kind)
    if os.path.normcase(old) == os.path.normcase(new) or not os.path.isfile(old):
        return ""
    if not os.path.isfile(new):
        shutil.copy2(old, new)
    _point(obj, kind, new)
    return new


# ---------------------------------------------------------------- once per file

def migrate(scene):
    """Before TexOverride an override file simply won: objects that have one start on Load,
    once per file. The Overrides folder defaults to 02.Overrides when none was set."""
    props = getattr(scene, "multicamproject_props", None)
    if props is None or props.tex_modes_migrated:
        return []
    props.tex_modes_migrated = True
    if not props.texture_override_folder:
        props.texture_override_folder = DEFAULT_FOLDER
    switched = []
    c = _common()
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.library or getattr(obj, "multicamproject_bake", None) is None:
            continue
        d = c.data(obj)
        if d.handmade:
            continue
        for kind in KINDS:
            if mode(obj, kind) == BAKE and available(scene, obj, kind):
                setattr(d, "alb_mode" if kind == 'ALB' else "nor_mode", LOAD)
                if kind == 'ALB':
                    d.fingerprint = SWITCHED
                else:
                    d.nor_fingerprint = SWITCHED
                try:
                    link(scene, obj, kind)
                except Exception as e:
                    print(f"[MultiCamProject] TexOverride {obj.name} {kind}: {e}")
                switched.append(f"{obj.name} {kind}")
    if switched:
        print(f"[MultiCamProject] TexOverride: Load from 02.Overrides - {', '.join(switched)}")
    _forget()
    return switched


def _forget():
    try:
        from ... import steps
        steps.forget()
    except ImportError:
        pass
