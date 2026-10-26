"""Check Project / Fix (IO Folders & Collections): the open main file is the table.

Check reads, changes nothing: the role folders, every object's own textures (BAo_ / BAp_ /
ALB_ / NOR_ / MSK_ / CMX1_ ...: they belong in the Bake Folder, a Loaded ALB_ / NOR_ in
Overrides) and each linked work file - read by a background Blender: where it is (the top of
the Bake Folder), which main file / bake folder it points at, which textures it uses. A file
name found in two places is the same file (merged) or not (the user picks, the linked copy
by default). Only the add-on's own files are looked at; scan .blends, photos and scan
textures are only counted.

Fix, one object at a time: its textures go into the Bake Folder, its work file is saved there
by a background Blender (paths remapped, keys pointing at this file), the object is relinked
here and checked (its link state, its images, its work file). The first failure stops Fix,
nothing is removed then. Once every object passed: the copies left behind go to the Recycle
Bin, the folders they leave empty are removed and the main file is saved."""
import filecmp
import json
import os
import shutil
import subprocess
import time

import bpy
from bpy.props import EnumProperty, StringProperty

from ... import folders, roles
from . import core

REPORT = None       # the last Check of the open file (Report)
_READS = {}         # work file: ((mtime, size), its read) - a second Check reads only changes
SCRIPT = os.path.join(os.path.dirname(__file__), "check_work.py")


def _n(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path))) if path else ""


SAME_DEPTH = 5      # folders a path must share from its end to be the same place elsewhere


def same_place(a, b):
    """The same path - also where the shared Drive is mounted elsewhere: D:\\ggdrive\\... on
    one computer is I:\\... on another, and a work file saved there names I:\\... for the
    same folder. Equal from the end for at least SAME_DEPTH folders, the shorter one whole."""
    if not a or not b:
        return False
    a, b = _n(a), _n(b)
    if a == b:
        return True
    pa = os.path.splitdrive(a)[1].strip(os.sep).split(os.sep)
    pb = os.path.splitdrive(b)[1].strip(os.sep).split(os.sep)
    n = min(len(pa), len(pb))
    return n >= SAME_DEPTH and pa[-n:] == pb[-n:]


def _when(path):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    except OSError:
        return "?"


def _same(a, b):
    """Byte-identical (a different size is never read: 8K files on the Drive are slow)."""
    try:
        return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _common():
    from ..baking import common
    return common


class Tex:
    """One texture file name of an object: where it should be (`target`), who uses which copy.
    state: ok | relink (paths only) | move (only elsewhere) | same (a copy elsewhere is
    identical) | pick (copies differ: `key` in the picks) | missing | stale (an ALB_ inpaint
    link with no file that nothing uses: Fix clears it)."""

    def __init__(self, name, target):
        self.name = name
        self.target = target
        self.main = ""          # the main file's image path
        self.work = ""          # the work file's
        self.state = "ok"
        self.source = ""        # the copy elsewhere (pick / move / same)
        self.locked = False
        self.droppable = False  # alb_inpaint only its object uses: a missing file is stale

    @property
    def key(self):
        return self.target

    def users_off(self):
        """Paths in use that are not the target."""
        return [p for p in (self.work, self.main) if p and not same_place(p, self.target)]


class Item:
    """An object of the main file with its own textures (and maybe a work file)."""

    def __init__(self, obj):
        self.name = obj.name
        self.job_id = (core.out_record(obj) or {}).get("id", "")
        self.work = ""          # the work file now ('' = none / not found)
        self.work_dst = ""      # where it belongs
        self.read = None        # check_work's read
        self.problems = []      # red lines Fix mends
        self.unfixable = []     # red lines it can't (a missing file, no work file)
        self.notes = []
        self.tex = []

    def needs_fix(self):
        return bool(self.problems) or any(t.state not in {"ok", "missing"} for t in self.tex)

    def is_ok(self):
        return not self.needs_fix() and not self.unfixable


class Report:
    def __init__(self):
        self.file = bpy.data.filepath
        self.time = time.time()
        self.folders = []       # (label, path, ok, note)
        self.make = []          # folders Fix creates
        self.items = []
        self.strays = []        # files that go to the Recycle Bin after every object passed
        self.dirs = []          # folders removed when they are empty after that
        self.errors = []        # Fix can't run (red)
        self.counts = []        # photos / scan textures (only counted)

    def to_fix(self):
        return bool(self.make or self.strays or any(i.needs_fix() for i in self.items))


# ---------------------------------------------------------------- the background Blender

def _start(mode, src, out, **cfg):
    cfg.update(mode=mode, src=src, out=out)
    path = out[:-5] + "_cfg.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    args = [bpy.app.binary_path, "--background"]
    if mode == "read":
        args.append("--factory-startup")    # nothing of the add-on runs on the file
    args += [src, "--python", SCRIPT, "--", path]
    log = open(out[:-5] + ".log", "w", encoding="utf-8")
    try:
        return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        log.close()


def _run(mode, src, label, **cfg):
    """Generator: the background Blender on `src`; returns its result dict."""
    from ..baking import jobs
    out = os.path.join(core.exchange_dir(), f"check_{mode}_{int(time.time() * 1000)}.json")
    proc = _start(mode, src, out, **cfg)
    t0 = time.monotonic()
    while proc.poll() is None:
        if jobs.stop_requested():
            proc.kill()
            raise RuntimeError("stopped")
        yield jobs.Step(f"{label} ({int(time.monotonic() - t0)} s)")
        if bpy.app.background:      # run_sync: no timer between the steps
            time.sleep(0.2)
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"ok": False, "errors": [f"no result (exit {proc.returncode}), log: "
                                        f"{out[:-5]}.log"]}


def _read(path, job_id, label):
    try:
        st = os.stat(path)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return {"ok": False, "errors": [f"{path} not found"]}
    hit = _READS.get(_n(path))
    if hit is not None and hit[0] == sig:
        return hit[1]
    res = yield from _run("read", path, label, id=job_id)
    if res.get("ok"):
        _READS[_n(path)] = (sig, res)
    return res


# ---------------------------------------------------------------- Check

def _own(name):
    return name.startswith(_common().OWN_PREFIXES)


def _object_textures(scene, obj, bake, overrides):
    """{file name: Tex} of obj's own images (core.TEXTURES) in the main file."""
    from ..tex_override import core as tov
    common = _common()
    d = common.data(obj)
    out = {}
    for ptr, _fn, _n_ in core.TEXTURES:
        img = getattr(d, ptr, None)
        path = common.image_file(img)
        if not path:
            continue
        name = os.path.basename(path)
        top = bake
        for kind, p in (('ALB', "alb_image"), ('NOR', "nor_image")):
            if ptr == p and tov.is_load(obj, kind) and overrides:
                top = overrides
        t = out.setdefault(name, Tex(name, os.path.join(top, name)))
        t.main = path
        t.locked = bool(getattr(d, "locked", False))
        if ptr == "alb_inpaint":
            users = bpy.data.user_map(subset=[img]).get(img, set())
            t.droppable = all(u == obj for u in users)
    return out


def _decide(t):
    """t.state from its copies."""
    there = os.path.isfile(t.target)
    off = [p for p in t.users_off() if os.path.isfile(p)]
    if not off:
        t.state = "ok" if there and not t.users_off() else ("relink" if there else "missing")
        if t.state == "missing" and t.droppable:
            t.state = "stale"
        return
    t.source = t.work if t.work in off else off[0]      # the work file's copy first
    if not there:
        t.state = "move"
    elif _same(t.source, t.target):
        t.state = "same"
    else:
        t.state = "pick"


def in_room(path):
    """`path` lies below the open .blend's own folder: nothing outside it is ever removed (a
    work file copied from another project still names that project's folders)."""
    room = _n(os.path.dirname(bpy.data.filepath))
    p = _n(path)
    return bool(room) and p.startswith(room.rstrip(os.sep) + os.sep)


def _old_dirs(report, bake, export, overrides):
    """Folders this file's things were left in (not the role folders, only inside this file's
    folder): their own-prefix files with this file's names and their work files are strays
    once Fix moved them."""
    keep = {_n(p) for p in (bake, export, overrides) if p}
    dirs = set()
    for it in report.items:
        for t in it.tex:
            for p in [t.main, t.work]:
                if p and _n(os.path.dirname(p)) not in keep:
                    dirs.add(os.path.dirname(os.path.normpath(p)))
        if it.work and _n(os.path.dirname(it.work)) not in keep:
            dirs.add(os.path.dirname(it.work))
        wb = (it.read or {}).get("keys", {}).get("multicamproject_work_bakes", "")
        if wb and _n(wb) not in keep and os.path.isdir(wb):
            dirs.add(os.path.normpath(wb))
    return sorted((d for d in dirs if in_room(d)), key=_n)


def _strays(report, bake, export, overrides):
    names = {t.name.lower() for it in report.items for t in it.tex}
    works = {os.path.basename(it.work).lower() for it in report.items if it.work}
    works |= {w + "1" for w in works}               # Blender's .blend1 backups
    final = {n for n in names if n.startswith(("alb_", "nor_"))}
    out = []
    for d in _old_dirs(report, bake, export, overrides):
        report.dirs.append(d)
        for e in _scan(d):
            low = e.lower()
            if (low in names and _own(e)) or low in works:
                out.append(os.path.join(d, e))
        ex = os.path.join(os.path.dirname(d), "Export")     # an Export made next to it
        for sub in (os.path.join(ex, "Textures"), ex):
            if _n(sub) in {_n(export), _n(os.path.join(export, "Textures"))}:
                continue
            files = _scan(sub)
            hits = [f for f in files if f.lower() in final]
            if hits:
                out += [os.path.join(sub, f) for f in hits]
                report.dirs.append(sub)
    return sorted(set(out), key=_n)


def _scan(d):
    try:
        with os.scandir(d) as it:
            return [e.name for e in it if e.is_file()]
    except OSError:
        return []


def check_steps(context):
    """Generator (jobs): the Report of the open file."""
    from ..baking import jobs
    from ..tex_override import core as tov
    scene = context.scene
    rep = Report()
    main = bpy.data.filepath
    bake = folders.folder(scene, 'ORIGINALS')
    export = folders.folder(scene, 'EXPORT')
    overrides = tov.folder(scene)
    yield jobs.Step("folders")
    if folders.in_scan_folder():
        rep.errors.append("This .blend is inside 00.Scan - open the room's main file and "
                          "check that one")
        return rep
    for role in roles.ORDER:
        pg, why = folders.holder(scene, role)
        if pg is None:
            continue
        path = folders.folder(scene, role)
        ok = bool(path) and os.path.isdir(path)
        note = ""
        if not ok and role in folders.MADE:
            rep.make.append(path)
            note = "missing - made by Fix"
        elif not ok:
            note = "not found - pick it in Project Setup (Fix leaves it)"
        rep.folders.append((folders.LABELS[role], path, ok, note))
    if overrides:
        ok = os.path.isdir(overrides)
        if not ok:
            rep.make.append(overrides)
        rep.folders.append(("Overrides", overrides, ok, "" if ok else "missing - made by Fix"))
    if not bake:
        return rep
    photos = list(folders._photos())
    miss = folders._missing(photos)
    if miss:
        rep.counts.append(f"Camera Photos: {len(miss)} of {len(photos)} photos missing")
    common = _common()
    objs = [o for o in bpy.data.objects if not o.library and o.type == 'MESH'
            and hasattr(o, "multicamproject_bake")]
    by_obj = {}
    for o in objs:
        tex = _object_textures(scene, o, bake, overrides)
        if tex or core.out_record(o):
            by_obj[o] = tex
    for o in sorted(by_obj, key=lambda o: o.name.lower()):
        it = Item(o)
        tex = by_obj[o]
        rec = core.out_record(o)
        if rec:
            jobs.item_start(o.name)
            it.work = core._saved_window(rec)
            if not it.work:
                it.unfixable.append("work file not found - Find Work Files in the Linking "
                                    "panel")
            else:
                it.work_dst = os.path.join(bake, os.path.basename(it.work))
                res = yield from _read(it.work, it.job_id, f"reading {os.path.basename(it.work)}")
                if not res.get("ok"):
                    it.unfixable += [f"work file not read: {e}" for e in res.get("errors", [])]
                else:
                    it.read = res
                    if _n(it.work) != _n(it.work_dst):
                        it.problems.append(f"{os.path.basename(it.work)} is in "
                                           f"{_short(os.path.dirname(it.work))} - moves to "
                                           f"{_short(bake)}")
                        if os.path.exists(it.work_dst):
                            it.notes.append(f"{_short(it.work_dst)} (not linked) goes to "
                                            "the Recycle Bin")
                            rep.strays.append(it.work_dst)
                    k = res["keys"]
                    wrong = [label for label, key, want in (
                        ("main file", "multicamproject_work_of", main),
                        ("bake folder", "multicamproject_work_bakes", bake),
                        ("save path", "multicamproject_work_save_as", it.work_dst))
                        if not same_place(k.get(key, ""), want)]
                    if wrong:
                        it.problems.append("its " + ", ".join(wrong) + " point elsewhere")
                    for name_, _fp, path, _ok in res["images"]:
                        fname = os.path.basename(path)
                        if not _own(fname):
                            continue
                        t = tex.setdefault(fname, Tex(fname, os.path.join(bake, fname)))
                        t.work = path
                    if res.get("photos_missing"):
                        it.notes.append(f"{res['photos_missing']} of {res['photos']} photos "
                                        "missing in the work file")
            jobs.item_end(o.name)
        for t in tex.values():
            _decide(t)
            if t.state == "missing":
                it.unfixable.append(f"{t.name}: not found anywhere")
        it.tex = sorted(tex.values(), key=lambda t: t.name.lower())
        rep.items.append(it)
    rep.strays = sorted((p for p in set(rep.strays) | set(_strays(rep, bake, export, overrides))
                         if in_room(p)), key=_n)
    _sync_picks(context, rep)
    return rep


def _short(path):
    """`path` relative to the .blend's folder (//00.Scan\\01.FBX\\01.Bake)."""
    try:
        return bpy.path.relpath(path)
    except ValueError:
        return path


# ---------------------------------------------------------------- the picks (window manager)

class MULTICAMPROJECT_PG_CheckPick(bpy.types.PropertyGroup):
    key: StringProperty()
    keep: EnumProperty(items=(('SOURCE', "Linked copy", "The copy the work file / this file "
                                         "uses replaces the Bake Folder's"),
                              ('TARGET', "Bake Folder copy", "The Bake Folder's copy stays, "
                                         "the other one goes to the Recycle Bin")),
                       default='SOURCE')


def _picks(context):
    return context.window_manager.multicamproject_check_picks


def _sync_picks(context, rep):
    picks = _picks(context)
    old = {p.key: p.keep for p in picks}
    picks.clear()
    for it in rep.items:
        for t in it.tex:
            if t.state == "pick":
                p = picks.add()
                p.key = t.key
                # a Locked object's ALB_ / NOR_ is final: its Bake Folder copy stays
                p.keep = old.get(t.key, 'TARGET' if t.locked else 'SOURCE')


def pick_of(context, t):
    return next((p for p in _picks(context) if p.key == t.key), None)


# ---------------------------------------------------------------- Fix

def _state_text(obj):
    from . import ui
    return ui._state(obj)[0]


def _recycle(paths):
    """To the Recycle Bin - only files below this .blend's folder. Returns the ones kept."""
    from ..export import fixes
    outside = [p for p in paths if not in_room(p)]
    if outside:
        print(f"[MultiCamProject] Check Project: kept {len(outside)} file(s) outside "
              f"{os.path.dirname(bpy.data.filepath)}")
    paths = [p for p in paths if os.path.isfile(p) and in_room(p)]
    gone = fixes.to_recycle_bin(paths) if paths else []
    return outside + [p for p in paths if p not in gone]


def _fix_object(context, rep, it, bake):
    """Generator: one object - textures, work file, relink here, check. Raises on a failure."""
    from ... import steps
    from ..baking import jobs
    obj = bpy.data.objects.get(it.name)
    if obj is None:
        raise RuntimeError(f"{it.name} is not in the file any more")
    before = _state_text(obj) if it.job_id else ""
    for t in it.tex:
        if t.state == "move":
            yield jobs.Step(f"{t.name} -> {_short(bake)}")
            shutil.copy2(t.source, t.target)
        elif t.state == "pick":
            p = pick_of(context, t)
            if p is not None and p.keep == 'SOURCE':
                yield jobs.Step(f"{t.name}: the linked copy replaces the Bake Folder's")
                if _recycle([t.target]):
                    raise RuntimeError(f"{t.name}: the Bake Folder copy could not be "
                                       "removed (Locked?)")
                shutil.copy2(t.source, t.target)
    if it.read is not None:
        if it.work_dst and _n(it.work) != _n(it.work_dst) and os.path.exists(it.work_dst):
            if _recycle([it.work_dst]):
                raise RuntimeError(f"{_short(it.work_dst)} is in the way")
        relink = {t.name: t.target for t in it.tex if os.path.isfile(t.target)}
        res = yield from _run("fix", it.work, f"saving {os.path.basename(it.work_dst)}",
                              dst=it.work_dst, main=bpy.data.filepath, bake=bake,
                              id=it.job_id, relink=relink)
        if not res.get("ok"):
            raise RuntimeError(f"{os.path.basename(it.work)}: "
                               + "; ".join(res.get("errors", [])))
        bad = [n for n, _fp, path, ok in res["images"] if _own(os.path.basename(path))
               and (not ok or _n(os.path.dirname(path)) not in {_n(os.path.dirname(t.target))
                                                                for t in it.tex})]
        if bad:
            raise RuntimeError(f"{os.path.basename(it.work_dst)}: {', '.join(bad)} not in place")
        if res.get("photos_missing", 0) > (it.read.get("photos_missing") or 0):
            raise RuntimeError(f"{os.path.basename(it.work_dst)}: photos lost their files")
        _READS.pop(_n(it.work), None)
        core._set_file(obj, it.work_dst)
    yield jobs.Step(f"{it.name}: relinked here")
    targets = {t.name: t.target for t in it.tex}
    common = _common()
    d = common.data(obj)
    stale = {t.name for t in it.tex if t.state == "stale"}
    img = d.alb_inpaint
    if img is not None and os.path.basename(common.image_file(img)) in stale:
        d.alb_inpaint = None        # its file is gone and nothing shows it
        if img.users == 0:
            bpy.data.images.remove(img)
    for ptr, _fn, _n_ in core.TEXTURES:
        img = getattr(d, ptr, None)
        path = common.image_file(img)
        target = targets.get(os.path.basename(path)) if path else None
        if target and _n(path) != _n(target) and os.path.isfile(target):
            img.filepath = bpy.path.relpath(target)
            img.reload()
    steps.forget()
    # the check: images where they belong, the link state as before, the work file found
    missing = {t.name for t in it.tex if t.state == "missing"}     # red in the report already
    for ptr, _fn, _n_ in core.TEXTURES:
        path = common.image_file(getattr(d, ptr, None))
        if path and not os.path.isfile(path) and os.path.basename(path) not in missing:
            raise RuntimeError(f"{it.name}: {os.path.basename(path)} has no file")
    if it.job_id:
        if it.read is not None and _n(core._saved_window(core.out_record(obj))) != _n(it.work_dst):
            raise RuntimeError(f"{it.name}: its work file is not found at {_short(it.work_dst)}")
        after = _state_text(obj)
        if after != before:
            raise RuntimeError(f"{it.name}: link state changed ({before} -> {after})")


def fix_steps(context, rep):
    """Generator (jobs): Fix the Report; returns report lines."""
    from ..baking import jobs
    scene = context.scene
    bake = folders.folder(scene, 'ORIGINALS')
    lines = []
    for path in rep.make:
        os.makedirs(path, exist_ok=True)
        lines.append(f"made {_short(path)}")
    folders._DIRS.clear()
    done = 0
    for it in rep.items:
        if not it.needs_fix():
            continue
        jobs.item_start(it.name)
        try:
            yield from _fix_object(context, rep, it, bake)
        except Exception as e:
            jobs.item_end(it.name, str(e))
            jobs.skip_rest("an object failed its check")
            lines.append(f"stopped at {it.name}: {e} - nothing was removed")
            return lines
        jobs.item_end(it.name)
        done += 1
    yield jobs.Step("Recycle Bin")
    # never what Fix just put in place (a work file saved where an unlinked one was)
    placed = {_n(t.target) for it in rep.items for t in it.tex}
    placed |= {_n(it.work_dst) for it in rep.items if it.work_dst}
    strays = [p for p in rep.strays if _n(p) not in placed]
    left = _recycle(strays)
    lines.append(f"{done} object(s) fixed, {len(strays) - len(left)} file(s) to the "
                 "Recycle Bin")
    if left:
        lines.append(f"kept (outside the project or locked): {', '.join(os.path.basename(p) for p in left)}")
    for d in sorted(set(rep.dirs), key=lambda p: -len(p)):     # the deepest first
        if _remove_empty(d):
            lines.append(f"removed the empty {_short(d)}")
            parent = os.path.dirname(d)
            if os.path.basename(parent).lower() == "export" and _remove_empty(parent):
                lines.append(f"removed the empty {_short(parent)}")
    bpy.ops.wm.save_mainfile()
    lines.append(f"{os.path.basename(bpy.data.filepath)} saved")
    return lines


def _remove_empty(d):
    """Remove `d` when nothing but the Drive's desktop.ini is left in it."""
    try:
        names = os.listdir(d)
    except OSError:
        return False
    if any(n.lower() != "desktop.ini" for n in names):
        return False
    try:
        for n in names:
            p = os.path.join(d, n)
            os.chmod(p, 0o666)
            os.remove(p)
        os.rmdir(d)
    except OSError as e:
        print(f"[MultiCamProject] {d} not removed: {e}")
        return False
    return True


# ---------------------------------------------------------------- operators + the report

def _can_run(cls, context):
    from ..baking import jobs
    if not bpy.data.filepath:
        cls.poll_message_set("Save the .blend first")
        return False
    if core.is_work_window(context.scene):
        cls.poll_message_set("A work window: check its main file")
        return False
    if jobs.busy():
        cls.poll_message_set("A bake / export is running")
        return False
    if context.mode != 'OBJECT':
        cls.poll_message_set("Object Mode only")
        return False
    return True


def _open_report():
    wm = bpy.context.window_manager
    win = wm.windows[0] if wm and wm.windows else None
    if win is None:
        return None
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'),
               key=lambda a: a.width * a.height, default=None)
    with bpy.context.temp_override(window=win, screen=win.screen, area=area):
        bpy.ops.multicamproject.check_report('INVOKE_DEFAULT')
    return None


class MULTICAMPROJECT_OT_CheckProject(bpy.types.Operator):
    """Check this file's folders, its objects' textures and their work files - nothing is
    changed. The report opens when it is done: what is wrong in red, and one Fix button"""
    bl_idname = "multicamproject.check_project"
    bl_label = "Check Project"

    @classmethod
    def poll(cls, context):
        return _can_run(cls, context)

    def execute(self, context):
        from ..baking import jobs
        names = [o.name for o in core.linked_objects()]

        def finish(result, error):
            global REPORT
            if error is not None or result is None:
                return []
            REPORT = result
            bpy.app.timers.register(_open_report, first_interval=0.1)
            return [('INFO', f"Check Project: {_summary(result)}")]
        return jobs.start(self, context, "Check Project", names, check_steps(context), finish)


def _summary(rep):
    bad = sum(1 for it in rep.items if it.needs_fix())
    lost = sum(1 for it in rep.items if it.unfixable)
    if not rep.to_fix() and not rep.errors and not lost:
        return f"all in place ({len(rep.items)} objects)"
    parts = [f"{bad} of {len(rep.items)} object(s) to fix" if bad
             else f"all {len(rep.items)} objects in place"]
    if rep.make:
        parts.append(f"{len(rep.make)} folder(s) to make")
    if rep.strays:
        parts.append(f"{len(rep.strays)} file(s) left behind")
    if lost:
        parts.append(f"{lost} with missing files")
    if rep.errors:
        parts.append(f"{len(rep.errors)} error(s)")
    return ", ".join(parts)


_TEX_TEXT = {"relink": "relinked to the Bake Folder", "move": "moves into the Bake Folder",
             "stale": "old ALB_ inpaint link - no file, nothing uses it: Fix clears the link",
             "same": "identical copy elsewhere - merged", "missing": "not found anywhere"}


class MULTICAMPROJECT_OT_CheckReport(bpy.types.Operator):
    """What Check Project found. Fix moves the files, relinks this file and the work files,
    one object at a time, then sends what is left behind to the Recycle Bin"""
    bl_idname = "multicamproject.check_report"
    bl_label = "Check Project"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return REPORT is not None and _n(REPORT.file) == _n(bpy.data.filepath)

    def invoke(self, context, event):
        fix = REPORT.to_fix()
        return context.window_manager.invoke_props_dialog(
            self, width=640, title=f"Check Project - {os.path.basename(REPORT.file)}",
            confirm_text="Fix" if fix else "OK")

    def draw(self, context):
        rep = REPORT
        col = self.layout.column(align=True)
        head = col.row()
        head.label(text=_summary(rep), icon='CHECKMARK' if not rep.to_fix() else 'ERROR')
        for e in rep.errors:
            _line(col, e, 'ERROR', alert=True)
        box = col.box().column(align=True)
        box.label(text="Folders", icon='FILE_FOLDER')
        for label, path, ok, note in rep.folders:
            row = box.row()
            row.alert = not ok
            split = row.split(factor=0.22)
            split.label(text=label, icon='CHECKMARK' if ok else 'ERROR')
            split.label(text=_short(path) + (f"   {note}" if note else ""))
        for c in rep.counts:
            _line(box, c, 'INFO', grey=True)
        for it in rep.items:
            self._draw_item(context, col, it)
        if rep.strays:
            box = col.box().column(align=True)
            box.label(text=f"Left behind - to the Recycle Bin after every object passed "
                           f"({len(rep.strays)})", icon='TRASH')
            for p in rep.strays[:8]:
                _line(box, _short(p), 'DOT', grey=True)
            if len(rep.strays) > 8:
                _line(box, f"... {len(rep.strays) - 8} more", 'BLANK1', grey=True)
        if rep.to_fix():
            hint = col.column(align=True)
            hint.active = False
            if any(it.job_id for it in rep.items if it.needs_fix()):
                hint.label(text="Close every work file first: a background Blender saves them",
                           icon='INFO')
            hint.label(text="One object at a time - the first failed check stops Fix, nothing "
                            "is removed then", icon='INFO')
            hint.label(text="This file is saved when Fix is done", icon='INFO')

    def _draw_item(self, context, col, it):
        box = col.box().column(align=True)
        if it.is_ok():
            row = box.row()
            row.active = False
            work = f" - {os.path.basename(it.work)}" if it.work else ""
            row.label(text=f"{it.name}{work}: all in place ({len(it.tex)} textures)",
                      icon='CHECKMARK')
            return
        box.label(text=it.name + (f"  -  {os.path.basename(it.work)}" if it.work else ""),
                  icon='OBJECT_DATA')
        for p in it.problems + it.unfixable:
            _line(box, p, 'ERROR', alert=True)
        for n in it.notes:
            _line(box, n, 'INFO', grey=True)
        for t in it.tex:
            if t.state in {"ok", "missing"}:       # missing: in the red lines above
                continue
            if t.state != "pick":
                _line(box, f"{t.name}: {_TEX_TEXT[t.state]}",
                      'ERROR' if t.state == "missing" else 'FILE_IMAGE',
                      alert=t.state in {"missing", "move", "relink"})
                continue
            pick = pick_of(context, t)
            older = os.path.getmtime(t.source) < os.path.getmtime(t.target) \
                if os.path.isfile(t.source) and os.path.isfile(t.target) else False
            row = box.row(align=True)
            row.alert = older and pick is not None and pick.keep == 'SOURCE'
            split = row.split(factor=0.5, align=True)
            split.label(text=f"{t.name}: copies differ", icon='DUPLICATE')
            if pick is not None:
                split.prop(pick, "keep", expand=True)
            who = "work file" if t.source == t.work else "this file"
            _line(box, f"      {who}: {_when(t.source)}  ({_short(os.path.dirname(t.source))})"
                       f"   ·   Bake Folder: {_when(t.target)}"
                       + ("   -  the linked copy is OLDER" if older else ""),
                  'BLANK1', grey=not older, alert=older)

    def execute(self, context):
        from ..baking import jobs
        rep = REPORT
        if not rep.to_fix():
            return {'FINISHED'}
        if rep.errors:
            self.report({'WARNING'}, "Fix the red folders first (Project Setup), then Check again")
            return {'CANCELLED'}
        names = [it.name for it in rep.items if it.needs_fix()]

        def finish(result, error):
            global REPORT
            REPORT = None       # the files moved: Check again to see them
            return [('INFO', line) for line in (result or [])]
        return jobs.start(self, context, "Fix Project", names, fix_steps(context, rep), finish,
                          block=True)


def _line(col, text, icon, alert=False, grey=False):
    row = col.row()
    row.alert = alert
    row.active = not grey
    row.label(text=text, icon=icon)


_CLASSES = (MULTICAMPROJECT_PG_CheckPick, MULTICAMPROJECT_OT_CheckProject,
            MULTICAMPROJECT_OT_CheckReport)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.multicamproject_check_picks = bpy.props.CollectionProperty(
        type=MULTICAMPROJECT_PG_CheckPick)


def unregister():
    del bpy.types.WindowManager.multicamproject_check_picks
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
