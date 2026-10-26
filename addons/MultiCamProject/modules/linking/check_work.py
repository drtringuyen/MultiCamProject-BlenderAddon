"""Check Project's background Blender, one work file at a time (check.py starts it).

    read   (--factory-startup): which images the work file uses and where, its scene keys,
           which paths are absolute
    fix    (the add-on on: its bake folder setting): the images in `relink` point at their
           file in the main Bake Folder, every other path becomes relative (one that names
           the other computer's drive is found again in this room), the keys point at the
           main file, then the file is saved at `dst` (paths remapped) - in place when dst
           is where it already is. The add-on's save handler stores the keys relative.

argv after --: settings json {mode, src, dst, main, bake, room, id, relink {file name: path},
out}. Writes the result json at `out`."""
import json
import os
import sys

import bpy

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
KEYS = ("multicamproject_work_of", "multicamproject_work_object", "multicamproject_work_id",
        "multicamproject_work_bakes", "multicamproject_work_save_as")
PATH_KEYS = (KEYS[0], KEYS[3], KEYS[4])
PHOTO_EXTS = (".jpg", ".jpeg")
res = {"ok": False, "errors": []}


def _n(p):
    return os.path.normcase(os.path.normpath(p))


def _local(path):
    """A missing file named under another mount of the room (I:\\...\\<room>\\x) -> the same
    file in this room, when it is there."""
    room = cfg.get("room", "")
    if not room or os.path.isfile(path):
        return path
    parts = os.path.normpath(path).split(os.sep)
    name = os.path.basename(os.path.normpath(room)).lower()
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() == name:
            cand = os.path.join(room, *parts[i + 1:])
            return cand if os.path.exists(cand) else path
    return path


def _relative(p):
    try:
        return bpy.path.relpath(p)
    except ValueError:      # another drive: stays absolute
        return p


def _images():
    out, photos, missing = [], 0, 0
    for im in bpy.data.images:
        if im.library or im.packed_file or im.source != 'FILE' or not im.filepath:
            continue
        p = os.path.normpath(bpy.path.abspath(im.filepath))
        if p.lower().endswith(PHOTO_EXTS):
            photos += 1
            missing += not os.path.isfile(p)
            continue
        out.append([im.name, im.filepath, p, os.path.isfile(p)])
    return out, photos, missing


def _absolute():
    """Paths of the file that are not relative (images, libraries, the work keys)."""
    out = [im.name for im in bpy.data.images if not im.library and not im.packed_file
           and im.source in {'FILE', 'SEQUENCE', 'MOVIE'} and im.filepath
           and not im.filepath.startswith("//") and _fixable(im.filepath)]
    out += [lib.name for lib in bpy.data.libraries
            if lib.filepath and not lib.filepath.startswith("//") and _fixable(lib.filepath)]
    scene = next((s for s in bpy.data.scenes if s.get(KEYS[0])), None)
    if scene is not None:
        out += [k for k in PATH_KEYS if str(scene.get(k, "")) and
                not str(scene.get(k, "")).startswith("//")]
    return out


def _same_drive(p):
    return os.path.splitdrive(_n(p))[0] == os.path.splitdrive(_n(bpy.data.filepath))[0]


def _fixable(p):
    """An absolute path Fix can make relative: on this file's drive, or the other computer's
    name for a file in this room. Blender's own files (C:\\Program Files\\...) can't be."""
    p = os.path.normpath(p)
    return _same_drive(p) or _n(_local(p)) != _n(p)


try:
    scene = next((s for s in bpy.data.scenes if s.get(KEYS[0])), None)
    if scene is None:
        raise RuntimeError("not a work window (no Send Out keys)")
    if cfg.get("id") and scene.get(KEYS[2]) != cfg["id"]:
        raise RuntimeError(f"its send-out id is {scene.get(KEYS[2])}, not {cfg['id']}")
    if cfg["mode"] == "fix":
        for im in bpy.data.images:
            if im.library or im.packed_file or not im.filepath:
                continue
            p = os.path.normpath(bpy.path.abspath(im.filepath))
            target = cfg["relink"].get(os.path.basename(p))
            if target and _n(p) != _n(target):
                if not os.path.isfile(target):
                    res["errors"].append(f"{im.name}: {target} not found")
                    continue
                p = target
            else:
                p = _local(p)
            if not im.filepath.startswith("//") or _n(p) != _n(bpy.path.abspath(im.filepath)):
                im.filepath = _relative(p)  # save_as remaps // to the new place
        for lib in bpy.data.libraries:
            if lib.filepath and not lib.filepath.startswith("//"):
                lib.filepath = _relative(_local(os.path.normpath(lib.filepath)))
        scene[KEYS[0]] = cfg["main"]        # absolute here: the save handler makes them
        scene[KEYS[3]] = cfg["bake"]        # relative to where the file is saved
        scene[KEYS[4]] = cfg["dst"]
        s = getattr(scene, "multicamproject_bake_settings", None)
        if s is None:
            raise RuntimeError("the add-on is not on in the background Blender")
        s.output_dir = os.path.join(cfg["bake"], "")
        if res["errors"]:
            raise RuntimeError("textures missing - not saved")
        if _n(cfg["dst"]) == _n(bpy.data.filepath):
            bpy.ops.wm.save_mainfile()
        else:
            if os.path.exists(cfg["dst"]):
                raise RuntimeError(f"{cfg['dst']} is already there - not saved")
            bpy.ops.wm.save_as_mainfile(filepath=cfg["dst"], relative_remap=True)
        res["output_dir"] = s.output_dir
    res["file"] = bpy.data.filepath
    res["keys_raw"] = {k: str(scene.get(k, "")) for k in KEYS}
    # absolute, for the comparison with the main file (// is the work file's own folder)
    res["keys"] = {k: (os.path.normpath(bpy.path.abspath(v)) if k in PATH_KEYS and v else v)
                   for k, v in res["keys_raw"].items()}
    res["images"], res["photos"], res["photos_missing"] = _images()
    res["absolute"] = _absolute()
    res["ok"] = True
except Exception as e:
    res["errors"].append(f"{type(e).__name__}: {e}")
with open(cfg["out"], "w", encoding="utf-8") as f:
    json.dump(res, f, indent=1)
