"""Light Preview (a test switch, scene multicamproject_props.light_preview): in Preview the
add-on's materials (MCP_ / MAT_) show 2K copies of their big images - camera photos, BAo_,
ALB_ - instead of the 8K files.

Why: Blender rebuilds every image's GPU texture when the texture cap changes and when the
object enters or leaves Texture Paint. With ~50 8K images (each shrunk to the 2K cap on the
CPU) that is 6-15 s every time. 2K copies upload in a fraction of it, and the cap no longer
has to change between Preview and Final.

The copies are files: <the image's folder>/_preview/<its name>.jpg / .png, made by
OpenImageIO in worker processes (preview_copies_worker.py; the first time only, again when the image's file is newer).
A copy is an Image datablock whose FULL_KEY points at the image it stands for: full(img)
gives that image back, and every reader that compares or hashes node images goes through it
(fingerprints, Handmade, ownership, folders) - a copy never counts as a change. Bakes and
the export see the full images (quality.final restores them).
"""
import os
import subprocess
import sys

import bpy

SIZE = 2048             # the copies' long side (= Preview's old texture cap)
FOLDER = "_preview"
FULL_KEY = "multicamproject_full"       # on a copy: the full image it stands for
WORKERS = 4
POLL = 0.5              # seconds between checks of the worker processes
RESYNC = 2.0            # seconds between re-checks of the materials (a rebuild puts full images back)

WORKER = os.path.join(os.path.dirname(__file__), "preview_copies_worker.py")
_waiting = []           # source paths to copy, in order
_jobs = {}              # source path -> its running worker (Popen), or None while waiting
_small = set()          # source paths no larger than SIZE: never copied
_rewritten = set()      # copy paths written again this session: a loaded copy reloads
_held = [0]             # quality.final() blocks open (bakes, export): no copies meanwhile


def hold(on):
    _held[0] = max(0, _held[0] + (1 if on else -1))


def held():
    return _held[0] > 0


def busy():
    """Copies are being made."""
    return bool(_jobs)


def full(img):
    """The image a copy stands for (img itself when it is no copy)."""
    if img is None:
        return None
    f = img.get(FULL_KEY)
    return f if isinstance(f, bpy.types.Image) else img


def is_copy(img):
    return img is not None and isinstance(img.get(FULL_KEY), bpy.types.Image)


def _src(img):
    if img.library or img.packed_file or img.source != 'FILE' or not img.filepath:
        return None
    return os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))


def copy_path(src):
    base, ext = os.path.splitext(os.path.basename(src))
    ext = ".jpg" if ext.lower() in {".jpg", ".jpeg"} else ".png"
    return os.path.join(os.path.dirname(src), FOLDER, base + ext)


def _fresh(src, dst):
    try:
        return os.path.getmtime(dst) >= os.path.getmtime(src)
    except OSError:
        return False


def _start(src):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _jobs[src] = subprocess.Popen([sys.executable, "-I", WORKER, str(SIZE), src, copy_path(src)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  creationflags=flags)


def _queue(src):
    if src in _jobs or src in _small:
        return
    _jobs[src] = None
    _waiting.append(src)
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.0)


def _copies():
    """{full image: its copy} of the copies loaded in this file."""
    return {full(i): i for i in bpy.data.images if is_copy(i)}


def _load(orig, dst, have):
    cp = have.get(orig)
    if cp is not None:
        if os.path.normcase(bpy.path.abspath(cp.filepath)) != os.path.normcase(dst):
            cp.filepath = dst           # loads the new file
        elif dst in _rewritten:
            cp.reload()
        _rewritten.discard(dst)
        return cp
    _rewritten.discard(dst)
    cp = bpy.data.images.load(dst, check_existing=False)
    cp.name = f"{orig.name} (2K)"
    cp.colorspace_settings.name = orig.colorspace_settings.name
    cp.alpha_mode = orig.alpha_mode
    cp[FULL_KEY] = orig
    have[orig] = cp
    return cp


def _nodes(mats):
    for m in mats:
        nt = m.node_tree if m is not None else None
        if nt is None:
            continue
        for n in nt.nodes:
            if n.type == 'TEX_IMAGE' and n.image is not None:
                yield n


def use(mats, skip=()):
    """Every big image node of `mats` on its 2K copy (made in the background when missing
    or older than its file). `skip`: images that stay full (the normal maps: not drawn in
    Preview). An image with unsaved edits stays full, so they show."""
    have = None
    for n in _nodes(mats):
        img = n.image
        orig = full(img)
        if orig in skip:
            continue
        if orig.is_dirty:
            if img is not orig:
                n.image = orig
            continue
        if img is not orig:
            continue                    # on its copy already
        if orig.has_data and max(orig.size) <= SIZE:
            continue
        src = _src(orig)
        if src is None or src in _small:
            continue
        dst = copy_path(src)
        if not _fresh(src, dst):
            _queue(src)
            continue
        if have is None:
            have = _copies()
        cp = _load(orig, dst, have)
        if n.image != cp:
            n.image = cp


def restore(mats):
    """Every node of `mats` back on its full image."""
    for n in _nodes(mats):
        orig = full(n.image)
        if orig is not n.image:
            n.image = orig


def _active():
    from . import quality
    scene = bpy.context.scene
    return not _held[0] and scene is not None and enabled(scene) and quality.mode(scene) == 'PREVIEW'


def enabled(scene):
    props = getattr(scene, "multicamproject_props", None)
    return bool(getattr(props, "light_preview", False))


def _poll():
    """Copies the workers finished go in; then a slower re-check of the materials (a rebuild,
    a new camera or an _inpaint puts full images in)."""
    done = [s for s, p in _jobs.items() if p is not None and p.poll() is not None]
    for src in done:
        p = _jobs.pop(src)
        if p.returncode == 0:
            _rewritten.add(copy_path(src))
        else:
            _small.add(src)             # small already, or failed: not again this session
            if p.returncode != 3:
                err = p.stderr.read().decode(errors="replace").strip() if p.stderr else ""
                print(f"[MultiCamProject] no 2K copy of {src}: {err or p.returncode}")
        if p.stderr:
            p.stderr.close()
    while _waiting and sum(1 for p in _jobs.values() if p is not None) < WORKERS:
        _start(_waiting.pop(0))
    try:
        if _active():
            from . import quality
            mats = quality.materials()
            use(mats, quality._normal_images(mats))
        if done and not _jobs:          # all copies made: the cap can stay Final's now
            from . import quality
            quality.set_limit(quality.limit())
        if done:                        # the panel's count
            for win in bpy.context.window_manager.windows:
                for area in win.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        elif not _jobs:
            return None
    except Exception as e:
        print(f"[MultiCamProject] Light Preview: {e}")
    return POLL if _jobs else RESYNC


def status():
    """(copies in use, copies being made) for the panel."""
    from . import quality
    used = sum(1 for n in _nodes(quality.materials()) if is_copy(n.image))
    return used, len(_jobs)


def start():
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    _waiting.clear()
    for p in _jobs.values():            # a running worker finishes its file on its own
        if p is not None and p.stderr:
            p.stderr.close()
    _jobs.clear()
