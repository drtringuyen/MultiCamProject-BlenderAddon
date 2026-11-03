"""ALB_ files written in place - Seam Fix's Apply and the Inpaint of 07's ALB_ row: before
each write a copy goes to <bake folder>/_previous; Undo puts the newest copy back. One stack
per object, shared by both, so Undo always takes back the last write whatever made it."""
import json
import os
import shutil

KEY = "seamfix_undo"        # object property [[file, copy], ...] - Seam Fix was its first user


def count(obj):
    return len(json.loads(obj.get(KEY, "[]"))) if obj is not None else 0


def push(obj, path, scene):
    """A copy of `path` in _previous, remembered for Undo. Returns the copy's path - call
    BEFORE writing the file."""
    from . import handmade
    copy = handmade.copy_to_previous(path, scene)
    stack = json.loads(obj.get(KEY, "[]"))
    stack.append([path, copy])
    obj[KEY] = json.dumps(stack)
    return copy


def undo(obj):
    """The file the last in-place write changed gets its copy back. Returns a status line."""
    from . import handmade
    stack = json.loads(obj.get(KEY, "[]"))
    if not stack:
        raise RuntimeError("Nothing to undo")
    path, copy = stack[-1]
    if not os.path.isfile(copy):
        stack.pop()
        obj[KEY] = json.dumps(stack)
        raise RuntimeError(f"{os.path.basename(copy)} is gone from _previous")
    shutil.copyfile(copy, path)     # a new mtime: the file counts as the newest copy
    handmade._reload_file(path)
    stack.pop()
    obj[KEY] = json.dumps(stack)
    return f"{os.path.basename(path)} back from {os.path.basename(copy)}"


def write_rgb(obj, path, scene, px):
    """`px` ((h, w, >=3) floats as stored, rows bottom-up) over the 8-bit PNG at `path` - a
    copy to _previous first - and every image on it reloaded."""
    from . import handmade
    from .normal import pngio
    push(obj, path, scene)
    pngio.write_rgb8(path, px)
    handmade._reload_file(path)
