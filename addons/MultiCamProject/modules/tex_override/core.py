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
OLD_DIR = "02.Overrides"    # its name - also of the old per-work-file one in 01.Bake
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


def main_file(scene):
    """A work window's main .blend (absolute) - '' in the main file itself."""
    from ..baking import common
    if scene is None or not common.is_work_window(scene):
        return ""
    path = str(scene.get(common.WORK_WINDOW_KEY, ""))
    return os.path.normpath(bpy.path.abspath(path)) if path else ""


def folder(scene=None):
    """The Overrides folder (absolute, normalized) - '' when none is set. One per room: a
    relative one is next to the main file. A work window (saved in 01.Bake) reads it against
    its main file, never against itself - every file of the room shares the one folder."""
    scene = scene or bpy.context.scene
    props = getattr(scene, "multicamproject_props", None)
    top = getattr(props, "texture_override_folder", "") if props is not None else ""
    if not top:
        return ""
    main = main_file(scene)
    # //..\02.Overrides: an older window's path from its own folder (01.Bake) up to the room
    if main and top.startswith("//") and not top[2:].startswith(".."):
        return os.path.normpath(os.path.join(os.path.dirname(main), top[2:]))
    return os.path.normpath(bpy.path.abspath(top))


def is_overrides_dir(path):
    """A folder named 02.Overrides (the room's, or an old one inside 01.Bake)."""
    return os.path.basename(os.path.normpath(path)).lower() == OLD_DIR.lower()


def legacy_path(scene, obj, kind):
    """Where a work window used to look before the Overrides folder was one per room:
    <its own folder>/02.Overrides/ALB_<name>.png - '' outside a saved work window."""
    if not main_file(scene) or not bpy.data.filepath:
        return ""
    path = os.path.join(os.path.dirname(bpy.data.filepath), OLD_DIR, name(obj, kind) + ".png")
    return path if os.path.normcase(os.path.dirname(path)) != os.path.normcase(folder(scene)) \
        else ""


def _legacy_only(scene, obj, kind):
    """The file is only at the old place: Load stays on (never flipped to Bake, which
    would lose it) - Check Project's Fix moves it into the room's Overrides."""
    path = legacy_path(scene, obj, kind)
    if path and os.path.isfile(path):
        print(f"[MultiCamProject] {obj.name}: {os.path.basename(path)} is still in the old "
              f"{os.path.dirname(path)} - Check Project / Fix in the main file moves it into "
              f"{folder(scene)}; Load stays on")
        return True
    return False


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
    cs = 'Non-Color' if normal else 'sRGB'
    if img.colorspace_settings.name != cs:
        img.colorspace_settings.name = cs
    # reloaded from the new file: an 8-bit PNG comes back as bytes. Never ask img.is_float
    # here - it decodes the whole 8K file (~0.6 s) just to answer
    engine.link_file(img, path)
    if normal:
        d.nor_image = img
    else:
        d.alb_image = img
    return img


def _material_current(obj):
    """MAT_ already shows the object's ALB_ (its source) and NOR_ images: re-pointing an
    image at another file needs no rebuild. A rebuild makes Material Preview compile the
    shader again (seconds) and GN-Final evaluate the object again."""
    from ..baking import common, material
    d = common.data(obj)
    mat = d.material
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    nodes = mat.node_tree.nodes
    alb = nodes.get(material.P + "alb")
    nor = nodes.get(material.P + "nor")
    if alb is None or alb.image != common.alb_source(obj):
        return False
    if d.nor_image is None:
        return nor is None
    return nor is not None and nor.image == d.nor_image


def link(scene, obj, kind):
    """Load: the image at the override file, MAT_ rebuilt when it does not show it yet.
    Raises when the file is gone."""
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
    if not _material_current(obj):
        material.build(obj, scene)
        gn_final.write_inputs(obj, scene)
    if kind == 'ALB' and not gn_final.is_final(obj):
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
    if not _material_current(obj):
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


def bake_both(scene, obj):
    """ALB_ and NOR_ back to Bake (a camera tool changes the projection they come from).
    A Locked Finish keeps its textures."""
    if _common().data(obj).locked:
        return
    for kind in ('ALB', 'NOR'):
        if is_load(obj, kind):
            set_mode(scene, obj, kind, BAKE)


def warn(text):
    """A warning the user sees (a dropdown's setter can't report): a small popup, and the
    console."""
    print(f"[MultiCamProject] {text}")

    def show():
        wm = bpy.context.window_manager
        if wm is not None and wm.windows:
            wm.popup_menu(lambda m, _c: m.layout.label(text=text), title="Overrides",
                          icon='ERROR')
        return None
    bpy.app.timers.register(show, first_interval=0.05)


def loads(scene, obj, kind):
    """A bake asks for `kind`: True when it is loaded (its file is there); a Load whose
    file is gone goes back to Bake (False: bake it)."""
    if not is_load(obj, kind):
        return False
    if available(scene, obj, kind) or _legacy_only(scene, obj, kind):
        return True
    set_mode(scene, obj, kind, BAKE)
    print(f"[MultiCamProject] {obj.name}: {name(obj, kind)} not found in 02.Overrides - back to Bake")
    return False


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


def relink_loads(scene):
    """On load: each Loaded texture at the room's override file - a work file from before
    one Overrides per room (or the missing-file relink, which takes the same name in Export)
    points elsewhere. Bake textures are left alone. Returns the names relinked."""
    done = []
    c = _common()
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.library or getattr(obj, "multicamproject_bake", None) is None:
            continue
        for kind in KINDS:
            if not is_load(obj, kind) or not available(scene, obj, kind):
                continue
            img = image(obj, kind)
            if img is None or (os.path.normcase(os.path.normpath(c.image_file(img)))
                               != os.path.normcase(override_path(scene, obj, kind))):
                try:
                    link(scene, obj, kind)
                    done.append(name(obj, kind))
                except Exception as e:
                    print(f"[MultiCamProject] TexOverride {obj.name} {kind}: {e}")
    if done:
        print(f"[MultiCamProject] TexOverride: relinked to {folder(scene)} - {', '.join(done)}")
        _forget()
    return done


def refresh(scene, obj, kind):
    """The texture's file read again (edited outside Blender). Returns 'reloaded',
    'unsaved' (changed in Blender: kept) or ''."""
    from ..baking import cache
    from ..baking.normal import reload_from_disk
    if is_load(obj, kind) and not available(scene, obj, kind) \
            and not _legacy_only(scene, obj, kind):
        set_mode(scene, obj, kind, BAKE)        # its file is gone: back to Bake
        return "bake"
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
    once per file. The Overrides folder defaults to 02.Overrides when none was set (in a
    work window that is the main file's, see folder())."""
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
