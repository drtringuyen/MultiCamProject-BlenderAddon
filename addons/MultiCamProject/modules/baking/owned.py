"""The ALB_ / NOR_ (and BAo_/BNo_/BAp_/BNp_ work) files this .blend has used or written - the only ones a clean-up may
remove. Bake folders and export folders are often shared by several .blend files (a
team's Drive folder); "no image of this .blend uses it" alone once sent another file's
bakes to the Recycle Bin.

Recorded by file name (clean-ups only look in the bake folder and <export>/Textures, and
a name survives another Drive mount or Save As) when the file is saved, after a bake,
before a rename and for the texture copies an export writes - never on load, so opening
a file does not mark it changed. A file never saved with this has an empty list: its
clean-up removes nothing. Two .blend files using the very same file name in the same
folder both count it as theirs."""
import json
import os

import bpy
from bpy.app.handlers import persistent

from . import common

OWN = common.OWN_PREFIXES


def _name(path):
    return os.path.basename(bpy.path.abspath(path)).lower()


def names(scene):
    """{lower-case file name} recorded for `scene`."""
    try:
        return set(json.loads(common.settings(scene).own_files or "[]"))
    except ValueError:
        return set()


def is_own(scene, path, own=None):
    return _name(path) in (names(scene) if own is None else own)


def add(scene, files):
    """Record `files` (absolute or // paths) - only the add-on's own PNGs (OWN)."""
    new = {_name(f) for f in files if f and os.path.basename(bpy.path.abspath(f)).startswith(OWN)}
    have = names(scene)
    if new <= have:
        return
    common.settings(scene).own_files = json.dumps(sorted(have | new))


def used_now():
    """The add-on's files (OWN) the images of this .blend point at."""
    return [common.image_file(i) for i in bpy.data.images
            if i.source == 'FILE' and i.filepath and not i.library
            and os.path.basename(common.image_file(i)).startswith(OWN)]


def record_used(scene=None):
    scenes = [scene] if scene is not None else list(bpy.data.scenes)
    used = used_now()
    for sc in scenes:
        if getattr(sc, "multicamproject_bake_settings", None) is not None:
            add(sc, used)


@persistent
def _on_save_pre(_):
    if common.is_work_window():     # the files belong to the main file
        return
    try:
        record_used()
    except Exception as e:          # never block a save
        print(f"[MultiCamProject] own-file record skipped: {e}")


def register():
    bpy.app.handlers.save_pre.append(_on_save_pre)


def unregister():
    if _on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save_pre)
