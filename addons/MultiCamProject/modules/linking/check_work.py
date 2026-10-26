"""Check Project's background Blender, one work file at a time (check.py starts it).

    read   (--factory-startup): which images the work file uses and where, its scene keys
    fix    (the add-on on: its bake folder setting): the images in `relink` point at their
           file in the main Bake Folder, the keys point at the main file, then the file is
           saved at `dst` (paths remapped) - in place when dst is where it already is

argv after --: settings json {mode, src, dst, main, bake, id, relink {file name: path}, out}.
Writes the result json at `out`."""
import json
import os
import sys

import bpy

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
KEYS = ("multicamproject_work_of", "multicamproject_work_object", "multicamproject_work_id",
        "multicamproject_work_bakes", "multicamproject_work_save_as")
PHOTO_EXTS = (".jpg", ".jpeg")
res = {"ok": False, "errors": []}


def _n(p):
    return os.path.normcase(os.path.normpath(p))


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
            target = cfg["relink"].get(os.path.basename(bpy.path.abspath(im.filepath)))
            if target and _n(bpy.path.abspath(im.filepath)) != _n(target):
                if not os.path.isfile(target):
                    res["errors"].append(f"{im.name}: {target} not found")
                    continue
                im.filepath = bpy.path.relpath(target)  # save_as remaps it to //<name>.png
        scene[KEYS[0]] = cfg["main"]
        scene[KEYS[3]] = cfg["bake"]
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
    res["file"] = bpy.data.filepath
    res["keys"] = {k: str(scene.get(k, "")) for k in KEYS}
    res["images"], res["photos"], res["photos_missing"] = _images()
    res["ok"] = True
except Exception as e:
    res["errors"].append(f"{type(e).__name__}: {e}")
with open(cfg["out"], "w", encoding="utf-8") as f:
    json.dump(res, f, indent=1)
