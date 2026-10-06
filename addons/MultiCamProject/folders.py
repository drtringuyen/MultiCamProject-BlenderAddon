"""Role folders: each collection role of the Linking panel has its folder and a Reload.

    Objects        Scan Textures  the objects' original scan textures   (//00.Scan/01.FBX/)
    Original Mesh  Bake Folder    the ALB_/NOR_ + work bakes            (//01.Bake/)
    Export         Export Folder  the FBX + Textures/                   (//Export/)
    Cameras        Camera Photos  every camera photo                    (//00.Scan/00.Photos/)

The bake and export folders are the Baking / 07 settings themselves (one setting, two
places); the camera one is the scene's: it relinks the cameras' background photos (also before
any Camera Projection setup) and is pushed to every object with Camera Projection.
Reload points every texture of the role at the file of the same name in its folder - unless
it already is in that folder (or below it, e.g. a work window's _work bakes) - then reloads
them. Files that are not found keep their path and are reported. The bake and export folders
are made on disk when missing (file load / save / Reload, only in files that use the add-on)."""
import os
import time

import bpy
from bpy.app.handlers import persistent

from . import roles

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga", ".webp", ".bmp")
# the defaults these folders had before; a file that never set them keeps them (_pin_old)
OLD_DEFAULTS = (("multicamproject_bake_settings", "output_dir", "//01.Baking/"),)


# ---------------------------------------------------------------- the four folders

def _bake_settings(scene):
    return getattr(scene, "multicamproject_bake_settings", None)


def _export_settings(scene):
    return getattr(scene, "multicamproject_export", None)


def photo_objects():
    """The objects with Camera Projection set up (empty when the module is off)."""
    try:
        from .modules.camera_project import core
    except ImportError:
        return []
    if not hasattr(bpy.types.Object, "multicamproject_cam"):
        return []
    return core._setup_objects()


def holder(scene, role):
    """(property group, property name) of the role's folder, or (None, why not)."""
    if role == 'OBJECTS':
        return scene.multicamproject_props, "scan_textures_folder"
    if role == 'ORIGINALS':
        s = _bake_settings(scene)
        return (s, "output_dir") if s is not None else (None, "Baking module is off")
    if role == 'EXPORT':
        s = _export_settings(scene)
        return (s, "folder") if s is not None else (None, "Export module is off")
    return scene.multicamproject_props, "photos_folder"


LABELS = {'OBJECTS': "Scan Textures", 'ORIGINALS': "Bake Folder", 'EXPORT': "Export Folder",
          'CAMERAS': "Camera Photos"}


def folder(scene, role):
    """The role's folder as an absolute path ("" when unknown)."""
    pg, attr = holder(scene, role)
    if pg is None:
        return ""
    path = getattr(pg, attr)
    return os.path.normpath(bpy.path.abspath(path)) if path else ""


_DIRS = {}      # path: (time read, is a folder) - panels redraw often, the drive is slow


def exists(path, ttl=2.0):
    """os.path.isdir for the panel, read again after `ttl` seconds."""
    now = time.monotonic()
    hit = _DIRS.get(path)
    if hit is None or now - hit[0] > ttl:
        hit = _DIRS[path] = (now, bool(path) and os.path.isdir(path))
    return hit[1]


# ---------------------------------------------------------------- matching + relinking

def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _inside(path, top):
    """True when `path` is in `top` or a folder below it."""
    p, t = _norm(path), _norm(top)
    return p == t or p.startswith(t.rstrip(os.sep) + os.sep)


def _listing(top):
    """{lower-case name: name} of the files in `top`. One directory read (scandir knows
    file / folder without a stat per entry - every stat is slow on the network drive)."""
    if not top:
        return {}
    try:
        with os.scandir(top) as it:
            return {e.name.lower(): e.name for e in it if e.is_file()}
    except OSError:
        return {}


def _candidates(img):
    """File names to look for: the current file's name, then the image's name (with and
    without Blender's .001 suffix, with the usual extensions when it has none)."""
    names = []
    if img.filepath:
        names.append(os.path.basename(bpy.path.abspath(img.filepath, library=img.library)))
    stems = [img.name]
    base, dot, tail = img.name.rpartition(".")
    if dot and tail.isdigit() and len(tail) == 3:
        stems.append(base)
    for stem in stems:
        names.append(stem)
        if os.path.splitext(stem)[1].lower() not in IMAGE_EXTS:
            names += [stem + ext for ext in IMAGE_EXTS]
    return names


def _rel(path):
    """Relative to the .blend when it is saved (and on the same drive)."""
    if not bpy.data.filepath:
        return path
    try:
        return bpy.path.relpath(path)
    except ValueError:
        return path


def _relinkable(img):
    return (img is not None and img.source == 'FILE' and not img.packed_file
            and not img.library)


def _is_there(path, top, listing):
    """`path` is a file in `top` or below: files right in `top` are looked up in its listing
    (no stat), deeper ones (e.g. _work bakes) are stat'ed."""
    if not path or not _inside(path, top):
        return False
    if _norm(os.path.dirname(path)) == _norm(top):
        return os.path.basename(path).lower() in listing
    return os.path.isfile(path)


def relink(images, top, listing=None):
    """Point every image at its file in `top` (unless it is already in `top` or below), then
    reload it. `listing`: top's, when the caller already read it.
    Returns (relinked, reloaded, [names not found])."""
    if listing is None:
        listing = _listing(top)
    relinked, reloaded, missing = 0, 0, []
    for img in images:
        if not _relinkable(img):
            continue
        cur = bpy.path.abspath(img.filepath) if img.filepath else ""
        if not _is_there(cur, top, listing):
            real = next((listing[n.lower()] for n in _candidates(img) if n.lower() in listing),
                        None)
            if real is None:
                missing.append(img.name)
            else:
                path = os.path.join(top, real)
                if not cur or _norm(cur) != _norm(path):
                    img.filepath = _rel(path)
                    relinked += 1
        img.reload()
        reloaded += 1
    return relinked, reloaded, missing


def _tree_images(tree, out, seen):
    if tree is None or tree in seen:
        return
    seen.add(tree)
    for node in tree.nodes:
        if node.type in {'TEX_IMAGE', 'TEX_ENVIRONMENT'} and node.image is not None:
            out.add(node.image)
        elif node.type == 'GROUP':
            _tree_images(node.node_tree, out, seen)


def _photos():
    """Every image a camera shows as its background (the camera photos)."""
    return {bg.image for cam in bpy.data.cameras for bg in cam.background_images
            if bg.image is not None}


def _own_prefixes():
    try:
        from .modules.baking import common
        return common.OWN_PREFIXES
    except ImportError:
        return ("ALB_", "NOR_", "BAo_", "BNo_", "BNoG_", "BAp_", "BNp_")


def _role_objects(scene, view_layer, role):
    colls = [c for c, _lc in roles.collections(scene, view_layer, role)]
    if not colls:
        c = roles.find(scene, role)
        colls = [c] if c is not None else []
    objs = []
    for c in colls:
        objs += [o for o in c.all_objects if o not in objs]
    return objs


def scan_images(scene, view_layer):
    """The textures of the Objects' materials, without the add-on's bakes and the camera
    photos (those have their own folders)."""
    out, seen = set(), set()
    for obj in _role_objects(scene, view_layer, 'OBJECTS'):
        for slot in obj.material_slots:
            if slot.material is not None and slot.material.use_nodes:
                _tree_images(slot.material.node_tree, out, seen)
    own, photos = _own_prefixes(), _photos()
    return sorted((i for i in out if i not in photos and not i.name.startswith(own)),
                  key=lambda i: i.name)


def bake_images():
    own = _own_prefixes()
    return sorted((i for i in bpy.data.images if i.name.startswith(own)), key=lambda i: i.name)


def export_images(top):
    return [i for i in bpy.data.images if _relinkable(i) and i.filepath
            and _inside(bpy.path.abspath(i.filepath), top)]


def _cameras(scene, view_layer):
    """The Cameras role's camera objects, else every camera of the scene."""
    cams = [o for o in _role_objects(scene, view_layer, 'CAMERAS') if o.type == 'CAMERA']
    return cams or [o for o in scene.objects if o.type == 'CAMERA']


def relink_photos(scene, view_layer, top):
    """Every camera's background photo from `top` (by its file name, then the camera's name);
    a camera without one gets <camera name>.<ext> when it is there. Works without any
    Camera Projection setup. Returns (relinked, reloaded, [cameras not found])."""
    listing = _listing(top)
    relinked, reloaded, missing = 0, 0, []
    for cam in _cameras(scene, view_layer):
        bgs = cam.data.background_images
        img = bgs[0].image if len(bgs) else None
        if img is not None:
            r, n, miss = relink([img], top, listing)
            if miss:        # the image's own name failed: try the camera's
                real = next((listing[(cam.name + e).lower()] for e in IMAGE_EXTS
                             if (cam.name + e).lower() in listing), None)
                if real is None:
                    missing.append(cam.name)
                else:
                    img.filepath = _rel(os.path.join(top, real))
                    img.reload()
                    r += 1
            relinked, reloaded = relinked + r, reloaded + n
            continue
        real = next((listing[(cam.name + e).lower()] for e in IMAGE_EXTS
                     if (cam.name + e).lower() in listing), None)
        if real is None:
            missing.append(cam.name)
            continue
        img = bpy.data.images.load(_rel(os.path.join(top, real)), check_existing=True)
        bg = bgs[0] if len(bgs) else bgs.new()
        bg.image = img
        cam.data.show_background_images = True
        relinked, reloaded = relinked + 1, reloaded + 1
    return relinked, reloaded, missing


def _missing_text(missing):
    shown = ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")
    return f"{len(missing)} not in the folder (kept): {shown}"


# ---------------------------------------------------------------- reload per role

def push_photos(scene):
    """The scene's photo folder onto every object with Camera Projection (its Folder field
    relinks the cameras whose photo is missing)."""
    path = scene.multicamproject_props.photos_folder
    n = 0
    for obj in photo_objects():
        d = obj.multicamproject_cam
        if d.image_folder != path:
            d.image_folder = path
            n += 1
    return n


def reload(scene, view_layer, role):
    """Reload the role's folder. Returns (report text, warnings)."""
    top = folder(scene, role)
    pg, why = holder(scene, role)
    if pg is None:
        return why, []
    if role in MADE:
        make_dir(scene, role)
    if not top or not os.path.isdir(top):
        return f"{LABELS[role]} not found: {top or '(empty)'}", []
    warnings = []
    if role == 'CAMERAS':
        relinked, reloaded, missing = relink_photos(scene, view_layer, top)
        if missing:
            warnings.append(_missing_text(missing))
        text = f"{reloaded} camera photo(s) reloaded, {relinked} relinked to {top}"
        objs = photo_objects()
        if objs:
            from .modules.camera_project import core
            push_photos(scene)
            for obj in objs:
                warnings += [f"{obj.name}: {w}" for w in core.refresh(obj, scene)]
            text += f" · Reload All on {len(objs)} object(s)"
        return text, warnings
    if role == 'OBJECTS':
        images = scan_images(scene, view_layer)
    elif role == 'ORIGINALS':
        images = bake_images()
    else:
        images = export_images(top)
    relinked, reloaded, missing = relink(images, top)
    if missing:
        warnings.append(_missing_text(missing))
    return f"{reloaded} texture(s) reloaded, {relinked} relinked to {top}", warnings


# ---------------------------------------------------------------- missing files fixed on their own

def _missing(images):
    """The images whose file is not there - one listing per folder, no stat per file."""
    by_dir, out = {}, []
    for img in images:
        if not _relinkable(img):
            continue
        path = os.path.normpath(bpy.path.abspath(img.filepath)) if img.filepath else ""
        if not path:
            out.append(img)
            continue
        d = os.path.dirname(path)
        if d not in by_dir:
            by_dir[d] = _listing(d)
        if os.path.basename(path).lower() not in by_dir[d]:
            out.append(img)
    return out


def _file_images():
    """Every file image of the .blend that is not a bake or a camera photo."""
    own, photos = _own_prefixes(), _photos()
    return [i for i in bpy.data.images
            if i.source == 'FILE' and i not in photos and not i.name.startswith(own)]


def relink_missing(scene):
    """Only the images whose file is missing get it from their role's folder: textures from
    Scan Textures, bakes from the Bake Folder, camera photos from Camera Photos. Images
    that load fine are left alone (no reload). Returns how many were relinked."""
    n = 0
    for role, images in (('OBJECTS', _file_images), ('ORIGINALS', bake_images),
                         ('CAMERAS', lambda: list(_photos()))):
        top = folder(scene, role)
        if not top:
            continue
        missing = _missing(images())
        if not missing:
            continue
        listing = _listing(top)
        if not listing:
            continue
        r, _n, _miss = relink(missing, top, listing)
        n += r
    return n


def relink_all_missing():
    """relink_missing for every scene (file load, add-on install)."""
    if not bpy.data.filepath:
        return 0
    n = 0
    for scene in bpy.data.scenes:
        try:
            n += relink_missing(scene)
        except Exception as e:      # never break a load over a texture
            print(f"[MultiCamProject] relink of missing files skipped: {e}")
    if n:
        print(f"[MultiCamProject] {n} missing texture(s) / photo(s) relinked from the folders")
    return n


# ---------------------------------------------------------------- auto detect

DEFAULTS = {'OBJECTS': "//00.Scan/01.FBX/", 'ORIGINALS': "//01.Bake/", 'EXPORT': "//Export/",
            'CAMERAS': "//00.Scan/00.Photos/"}
SEARCH_DEPTH = 2        # sub-folders of the default looked into (e.g. 01.FBX/textures_2k)


def _wanted(scene, view_layer, role):
    """[set of lower-case file names] per image the role's folder should hold."""
    if role == 'OBJECTS':
        return [{n.lower() for n in _candidates(i)} for i in scan_images(scene, view_layer)]
    if role == 'ORIGINALS':
        return [{n.lower() for n in _candidates(i)} for i in bake_images() if _relinkable(i)]
    if role == 'CAMERAS':
        out = []
        for cam in _cameras(scene, view_layer):
            bgs = cam.data.background_images
            img = bgs[0].image if len(bgs) else None
            names = {n.lower() for n in _candidates(img)} if img is not None else set()
            out.append(names | {(cam.name + e).lower() for e in IMAGE_EXTS})
        return out
    return []


def _subfolders(top, depth):
    out = []
    if depth <= 0:
        return out
    try:
        entries = [e for e in os.scandir(top) if e.is_dir() and not e.name.startswith(".")]
    except OSError:
        return out
    for e in entries:
        out.append(e.path)
        out += _subfolders(e.path, depth - 1)
    return out


def _current_dirs(scene, view_layer, role):
    """The folders the role's files are in now (existing files only)."""
    if role == 'OBJECTS':
        imgs = scan_images(scene, view_layer)
    elif role == 'ORIGINALS':
        imgs = bake_images()
    elif role == 'CAMERAS':
        imgs = [c.data.background_images[0].image for c in _cameras(scene, view_layer)
                if len(c.data.background_images) and c.data.background_images[0].image]
    else:
        return []
    by_dir = {}         # folder: file names in it (one listing per folder, no stat per file)
    for img in imgs:
        if _relinkable(img) and img.filepath:
            path = os.path.normpath(bpy.path.abspath(img.filepath))
            by_dir.setdefault(os.path.dirname(path), set()).add(os.path.basename(path).lower())
    return sorted(d for d, names in by_dir.items() if names & _listing(d).keys())


def detect(scene, view_layer, role):
    """The folder holding most of the role's files: the default folder (and its sub-folders)
    or where the files are now. Nothing to match (or no match): the default.
    Returns (value for the setting, files matched, files wanted)."""
    default = DEFAULTS[role]
    wanted = _wanted(scene, view_layer, role)
    if not wanted or not bpy.data.filepath:
        return default, 0, len(wanted)
    top = os.path.normpath(bpy.path.abspath(default))
    places = ([top] + _subfolders(top, SEARCH_DEPTH)) if os.path.isdir(top) else []
    places += [d for d in _current_dirs(scene, view_layer, role) if d not in places]
    best, best_n = None, 0
    for place in places:        # the default first: it wins a tie
        listing = _listing(place)
        n = sum(1 for names in wanted if names & listing.keys())
        if n > best_n:
            best, best_n = place, n
    if best is None:
        return default, 0, len(wanted)
    if _norm(best) == _norm(top):
        return default, best_n, len(wanted)
    return os.path.join(_rel(best), ""), best_n, len(wanted)


def auto_fill(scene, view_layer):
    """Auto Detect and Fill: empty collection pickers, Original Mesh / EXPORT made when they
    are not in the scene, then every role's folder (missing bake / export folders are made).
    Returns report lines."""
    lines = []
    filled = roles.autofill(scene)
    if filled:
        lines.append("Collections: " + ", ".join(roles.ROLES[r][1] for r in filled))
    made = []
    for role in roles.CREATABLE:       # Original Mesh / EXPORT: made when not in the scene
        if not roles.collections(scene, view_layer, role):
            made.append(roles.ensure(scene, role).name)
    if made:
        lines.append("Added to the scene: " + ", ".join(made))
    for role in roles.ORDER:
        pg, attr = holder(scene, role)
        if pg is None:
            lines.append(f"{LABELS[role]}: {attr}")
            continue
        value, n, total = detect(scene, view_layer, role)
        if getattr(pg, attr) != value:
            setattr(pg, attr, value)
        if role in MADE:
            make_dir(scene, role)
        found = f" ({n}/{total} files)" if total else ""
        lines.append(f"{LABELS[role]}: {value}{found}")
    _DIRS.clear()
    n = relink_missing(scene)
    if n:
        lines.append(f"{n} missing texture(s) / photo(s) relinked")
    return lines


# ---------------------------------------------------------------- old files keep their folders

def _pin_old():
    """A file saved before the default changed (01.Baking -> 01.Bake) and that never set the
    folder keeps its old one when it exists."""
    if not bpy.data.filepath:
        return None
    for scene in bpy.data.scenes:
        for group, attr, old in OLD_DEFAULTS:
            pg = getattr(scene, group, None)
            if pg is None or pg.is_property_set(attr):
                continue
            if os.path.isdir(bpy.path.abspath(old)):
                setattr(pg, attr, old)
                print(f"[MultiCamProject] {scene.name}: kept the old folder {old}")
    return None


# ---------------------------------------------------------------- the add-on makes its folders

MADE = ('ORIGINALS', 'EXPORT')      # the bake + export folders are made on disk when missing


def _is_project(scene):
    """A file that uses the add-on (other .blends opened never get folders made)."""
    return any(roles.find(scene, r) is not None for r in ('OBJECTS', 'EXPORT', 'ORIGINALS'))         or bool(photo_objects())


def make_dir(scene, role):
    """Create the role's folder when it is missing (saved .blend only: // needs a place).
    Returns the path made, else None."""
    if not bpy.data.filepath:
        return None
    path = folder(scene, role)
    if not path or os.path.isdir(path):
        return None
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        print(f"[MultiCamProject] could not create {path}: {e}")
        return None
    _DIRS.pop(path, None)
    print(f"[MultiCamProject] created {path}")
    return path


def make_dirs():
    """Bake + export folders of every scene that uses the add-on."""
    if not bpy.data.filepath:
        return None
    made = False
    for scene in bpy.data.scenes:
        try:
            if _is_project(scene):
                for role in MADE:
                    if holder(scene, role)[0] is not None:
                        made = bool(make_dir(scene, role)) or made
        except Exception as e:      # never break a load / save over a folder
            print(f"[MultiCamProject] folders skipped: {e}")
    if made:
        _redraw()
    return None


def _redraw():
    """The sidebars drew the folder red before it was made, and nothing redraws them."""
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _fill_roles():
    """Empty collection pickers get OBJECTS / Original Mesh / EXPORT / CAMERAS when found."""
    filled = False
    for scene in bpy.data.scenes:
        try:
            filled = bool(roles.autofill(scene)) or filled
        except Exception as e:
            print(f"[MultiCamProject] collection roles skipped: {e}")
    if filled:
        _redraw()


def _startup():
    _pin_old()      # first: a file kept on 01.Baking never gets an empty 01.Bake
    _fill_roles()
    make_dirs()
    relink_all_missing()
    return None


@persistent
def _on_load(_):
    _startup()


@persistent
def _on_save(_):
    make_dirs()


def register():
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.save_post.append(_on_save)
    # the file already open when the add-on is (re)installed; after the modules registered
    bpy.app.timers.register(_startup, first_interval=0.2)


def unregister():
    if _on_save in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_on_save)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
