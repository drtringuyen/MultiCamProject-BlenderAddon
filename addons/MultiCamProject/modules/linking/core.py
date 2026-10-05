"""Linking: the Work Window. Send one object out of the main file into a second, unsaved Blender -
with its whole setup (MCP_ / MAT_, GN, its cameras and photos, its Bake Source) - model,
paint VCMix and bake there, then receive only its mesh and bakes back.

Main file    -> Send Out (EXPORT row / Cutting & Modelling): the object is written with
                everything it points to into <temp>/mcp_workfile/<id>/open.blend and a new
                Blender opens it (build.py): transforms applied, parents (the
                `transform` empty) gone, bakes into <id>/bakes. The object records the
                send-out (OUT_KEY); the main file is not changed otherwise.
Work window  -> Send Back: the base mesh (modifiers live on both sides), which modifiers
                are left, slot picks and per-object shifts, the bakes made there and which
                of them were up to date -> <id>/mesh.blend + manifest.json.
Main file    -> Receive: Replace (default) swaps the mesh data, removes the modifiers the
                window applied, takes the camera list, slots / shifts and bakes, refreshes
                the materials and stamps what was up to date there. Add joins the window's
                mesh instead (nothing else). The link stays: every new Send Back can be
                received; X ends it. Send Out is blocked while linked.
Saved window -> its bakes move to <main bake folder>/_work/<object>/ (the temp folder is
                cleaned) and it tells the main file where it is (window.json): the row's
                open button opens it again, still linked.
The link is the send-out id: on the main object (OUT_KEY) and in the window's scene.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid

import bpy
from bpy.app.handlers import persistent

from ..remesh import workflow as wf

OUT_KEY = "multicamproject_out"             # main object: the send-out record (JSON)
WORK_KEY = "multicamproject_work_of"        # work window scene: the main .blend
WORK_OBJECT_KEY = "multicamproject_work_object"     # work window scene: the object's name
WORK_ID_KEY = "multicamproject_work_id"     # work window scene: the send-out id
WORK_BAKES_KEY = "multicamproject_work_bakes"   # work window scene: main bake folder/_work/<obj>
WINDOW_FILE = "window.json"     # the saved work file's path, written by the window
WORK_SUBDIR = "_work"           # in the main bake folder: saved work windows' bakes
MANIFEST = "manifest.json"
MESH_FILE = "mesh.blend"
OPEN_FILE = "open.blend"
SETTINGS_FILE = "settings.json"
KEEP_DAYS = 30          # unlinked hand-off folders older than this go at the next Send Out
# the textures Receive takes: pointer on multicamproject_bake -> name function in common
TEXTURES = (("ba_image", "ba_name", False), ("bn_image", "bn_name", True),
            ("bap_image", "bap_name", False), ("bnp_image", "bnp_name", True),
            ("bng_image", "bng_name", True), ("alb_image", "alb_name", False),
            ("nor_image", "nor_name", True))
META = ("ba_size", "bp_size", "alb_size", "nor_size", "nor_source_used", "nor_times",
        "last_ba_seconds", "last_bp_seconds", "last_bake_seconds", "last_nor_seconds",
        "ba_far_share", "ba_far_max", "ba_fit_cage")
SCENE_GROUPS = ("multicamproject_bake_settings", "multicamproject_export",
                "multicamproject_sides", "multicamproject_props")


# ---------------------------------------------------------------- paths

def exchange_dir():
    """<temp>/mcp_workfile: what the two Blenders hand each other (never next to the .blend)."""
    folder = os.path.join(os.path.dirname(os.path.normpath(bpy.app.tempdir)), "mcp_workfile")
    os.makedirs(folder, exist_ok=True)
    return folder


def job_dir(job_id):
    return os.path.join(exchange_dir(), job_id)


def bakes_dir(job_id):
    return os.path.join(job_dir(job_id), "bakes")


def _clear_old_jobs():
    """Old hand-off folders go - never one an object of this file is still linked to."""
    now = time.time()
    linked = {r["id"] for r in (out_record(o) for o in bpy.data.objects) if r}
    for name in os.listdir(exchange_dir()):
        p = os.path.join(exchange_dir(), name)
        if name in linked:
            continue
        try:
            if os.path.isdir(p) and now - os.path.getmtime(p) > KEEP_DAYS * 86400:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def is_work_window(scene):
    return bool(scene.get(WORK_KEY))


# ---------------------------------------------------------------- main file: the record

def out_record(obj):
    """The send-out of obj ({id, time, matrix, sig}), None when it is home."""
    raw = obj.get(OUT_KEY) if obj is not None else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def sent_back(obj):
    """The manifest of what the work window sent back for obj (None = nothing yet)."""
    rec = out_record(obj)
    if rec is None:
        return None
    path = os.path.join(job_dir(rec["id"]), MANIFEST)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def new_send(obj):
    """The manifest when the window sent back something not received yet, else None."""
    man = sent_back(obj)
    if man is None:
        return None
    return man if man.get("time", 0) > out_record(obj).get("received", 0) else None


def saved_window(obj):
    """The linked work window's saved file ('' = not saved / gone)."""
    rec = out_record(obj)
    if rec is None:
        return ""
    try:
        with open(os.path.join(job_dir(rec["id"]), WINDOW_FILE), encoding="utf-8") as f:
            path = json.load(f).get("path", "")
    except (OSError, ValueError):
        return ""
    return path if path and os.path.isfile(path) else ""


def link_text(obj):
    path = saved_window(obj)
    return (f"Linked to {os.path.basename(path)}" if path
            else "Linked to an unsaved work window")


def forget_copies(new_objs):
    """A copy (Shift+D) of a linked object carries the same record: only the original stays
    linked."""
    new = set(new_objs)
    for o in new_objs:
        rec = out_record(o)
        if rec is None:
            continue
        if any(x not in new and (out_record(x) or {}).get("id") == rec["id"]
               for x in bpy.data.objects):
            del o[OUT_KEY]


def _stamp_record(obj, **extra):
    """The record's placement and mesh signature as they are now (+ extra fields)."""
    rec = out_record(obj) or {}
    rec.update(matrix=_matrix_list(obj.matrix_world), sig=mesh_signature(obj), **extra)
    obj[OUT_KEY] = json.dumps(rec)


def mesh_signature(obj):
    """The mesh (shape, uv_normal) and its painted VCMix layers: edits in the main file
    while the object is out are noticed at Receive."""
    parts = []
    try:
        from ..baking import fingerprint
        parts += fingerprint._source_parts(obj) + list(fingerprint._checksums(obj))
    except ImportError:
        me = obj.data
        parts += [len(me.vertices), len(me.polygons)]
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def _matrix_list(m):
    return [round(v, 6) for row in m for v in row]


def receive_warnings(obj):
    """What changed in the main file since obj went out or was last received (Receive asks
    first)."""
    rec = out_record(obj)
    if rec is None:
        return []
    out = []
    if max(abs(a - b) for a, b in zip(_matrix_list(obj.matrix_world), rec["matrix"])) > 1e-4:
        out.append("it was moved (the mesh comes where the object is now)")
    if mesh_signature(obj) != rec["sig"]:
        out.append("its mesh or painting was edited here (those edits are replaced)")
    return out


# ---------------------------------------------------------------- main file: Send Out

def _scene_settings(scene):
    """The add-on's scene settings as plain values (pointers / collections left out)."""
    out = {}
    for group in SCENE_GROUPS:
        pg = getattr(scene, group, None)
        if pg is None:
            continue
        vals = {}
        for p in pg.bl_rna.properties:
            if p.identifier == "rna_type" or p.type in {'POINTER', 'COLLECTION'} or p.is_readonly:
                continue
            v = getattr(pg, p.identifier)
            if p.type == 'ENUM' and p.is_enum_flag:
                v = sorted(v)
            elif hasattr(v, "__len__") and not isinstance(v, str):
                v = list(v)
            vals[p.identifier] = v
        out[group] = vals
    return out


def _scene_driver_targets():
    """Driver targets that point at a Scene (e.g. a scan material driven by a scene
    property). libraries.write follows them and would write the whole scene - every object,
    camera and image of the file - into the hand-off."""
    out = []
    for scene in bpy.data.scenes:
        for idb in bpy.data.user_map(subset=[scene]).get(scene, ()):
            if isinstance(idb, (bpy.types.Scene, bpy.types.WindowManager, bpy.types.Screen,
                                bpy.types.WorkSpace)):
                continue
            for holder in (idb, getattr(idb, "node_tree", None)):
                ad = getattr(holder, "animation_data", None) if holder is not None else None
                if ad is None:
                    continue
                for fc in ad.drivers:
                    for var in fc.driver.variables:
                        for t in var.targets:
                            if t.id == scene:
                                out.append((t, scene))
    return out


def _write_object(path, obj):
    """obj with what it points to, without any Scene: the scene links of drivers are
    off only while the file is written (put back right after, nothing is evaluated)."""
    cut = _scene_driver_targets()
    try:
        for t, _scene in cut:
            t.id = None
        bpy.data.libraries.write(path, {obj}, path_remap='ABSOLUTE')
    finally:
        for t, scene in cut:
            t.id = scene


def _keep_names(obj):
    """What the work window keeps: the object, its Bake Source and its cameras."""
    names = [obj.name]
    src = wf.bake_source_of(obj)
    if src is not None:
        names.append(src.name)
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None:
        cams = [it.camera for it in cam.cameras]
        cams += [getattr(cam, f"slot_{i}") for i in range(1, 7)]
        names += sorted({c.name for c in cams if c is not None})
    return names


def send_out(context, obj):
    """Write obj (with everything it points to) for a new Blender and open it there.
    Returns the send-out id."""
    main = bpy.data.filepath
    if not main:
        raise RuntimeError("Save the main file first (Receive copies the bakes into its folder)")
    if obj.mode != 'OBJECT':
        obj.update_from_editmode()
    _clear_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    os.makedirs(bakes_dir(job_id), exist_ok=True)
    lib = os.path.join(job_dir(job_id), OPEN_FILE)
    # the real object, written with its dependencies (the main file is not changed): its
    # mesh, MCP_ / MAT_ and textures, GN, the cameras of its list with their photos, the
    # Bake Source and the parents. A Scene can't be written (Blender 5.2 crashes)
    _write_object(lib, obj)
    src = wf.bake_source_of(obj)
    scene = context.scene
    settings = {
        "id": job_id, "lib": lib, "object": obj.name, "main": main, "keep": _keep_names(obj),
        "addon": __package__.rsplit(".modules", 1)[0],
        "original": src.name if src is not None else "",
        "bakes": bakes_dir(job_id), "scene": _scene_settings(scene),
        "work_bakes": _work_bakes(scene, obj),
        "render": [scene.render.resolution_x, scene.render.resolution_y,
                   scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
        "unit_system": scene.unit_settings.system,
        "unit_scale": scene.unit_settings.scale_length}
    settings_path = os.path.join(job_dir(job_id), SETTINGS_FILE)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f)
    obj[OUT_KEY] = json.dumps({"id": job_id, "time": time.time(),
                               "matrix": _matrix_list(obj.matrix_world),
                               "sig": mesh_signature(obj)})
    script = os.path.join(os.path.dirname(__file__), "build.py")
    subprocess.Popen([bpy.app.binary_path, "--python", script, "--", settings_path])
    return job_id


def _work_bakes(scene, obj):
    """Where a saved work window keeps its bakes: <main bake folder>/_work/<object> (never
    overwrites the main file's own files; Clean only looks at the folder itself)."""
    try:
        from ..baking import common
        folder = common.output_dir(scene)
    except ImportError:
        folder = os.path.join(os.path.dirname(bpy.data.filepath), "01.Baking")
    return os.path.join(folder, WORK_SUBDIR, bpy.path.clean_name(obj.name))


def open_saved(obj):
    path = saved_window(obj)
    if not path:
        raise RuntimeError("The work window was not saved (or its file is gone)")
    subprocess.Popen([bpy.app.binary_path, path])


def cancel(obj):
    """Forget the send-out; the main object stays as it is."""
    if OUT_KEY in obj:
        del obj[OUT_KEY]


# ---------------------------------------------------------------- work window: Send Back

def work_object(scene):
    obj = bpy.data.objects.get(scene.get(WORK_OBJECT_KEY, ""))
    return obj if obj is not None and obj.type == 'MESH' else None


def _fresh(obj):
    """Which bakes are up to date in the work window (its own stamps, read uncached)."""
    try:
        from ..baking import common, fingerprint
    except ImportError:
        return {}
    d = common.data(obj)
    size = common.resolution(obj)
    ba = bool(d.ba_image and d.ba_fingerprint and (not d.ba_size or d.ba_size == size)
              and d.ba_fingerprint.split(":")[0] == fingerprint.source_state(obj))
    bp = bool(d.bap_image and d.bp_fingerprint and (not d.bp_size or d.bp_size == size)
              and d.bp_fingerprint.split(":")[0] == fingerprint.projection_state(obj))
    return {
        "ba": ba, "bp": bp,
        "bng": bool(ba and d.bng_image and d.bng_fingerprint.startswith(d.ba_fingerprint + "|")),
        "bnp": bool(bp and d.bnp_image and d.bnp_fingerprint.startswith(d.bp_fingerprint + "|")),
        "alb": bool(d.alb_image and d.fingerprint and d.fingerprint == fingerprint.compute(obj)),
    }


def send_back(context):
    """The work object's base mesh + manifest into its hand-off folder. Returns faces."""
    scene = context.scene
    obj = work_object(scene)
    job_id = scene.get(WORK_ID_KEY, "")
    if obj is None or not job_id:
        raise RuntimeError("This window's object is gone")
    if obj.mode != 'OBJECT':
        obj.update_from_editmode()
    folder = job_dir(job_id)
    os.makedirs(folder, exist_ok=True)
    me = obj.data.copy()
    me.name = obj.name
    me.materials.clear()        # the faces keep their slot index; the main file has the slots
    try:
        bpy.data.libraries.write(os.path.join(folder, MESH_FILE), {me}, path_remap='ABSOLUTE')
        faces = len(me.polygons)
    finally:
        bpy.data.meshes.remove(me)
    man = {"mesh": obj.name, "time": time.time(),
           "modifiers": [m.name for m in obj.modifiers],
           "applied": bool(obj.get(wf.APPLIED_KEY))}
    dec, snap = wf.decimate_modifier(obj), wf.snap_modifier(obj)
    if dec is not None:
        man["decimate"] = {"ratio": dec.ratio, "show_viewport": dec.show_viewport}
    if snap is not None:
        man["snap"] = {"show_viewport": snap.show_viewport}
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None and cam.is_setup:
        from ..camera_project import core as cp
        man["slots"] = [getattr(cam, f"slot_{i}").name if getattr(cam, f"slot_{i}") else ""
                        for i in range(1, 7)]
        man["slot_count"] = cam.slot_count
        man["user_picked"] = cam.user_picked
        man["shifts"] = {it.camera.name: list(it.shift) for it in cam.shifts
                         if it.camera is not None and not cp.is_global(it.camera)}
        man["cameras"] = [(it.camera.name, it.score, it.coverage) for it in cam.cameras
                          if it.camera is not None]
        man["removed"] = [r.camera.name for r in cam.removed if r.camera is not None]
        man["coverage_filter"] = cam.coverage_filter
    try:
        from ..baking import common
        d = common.data(obj)
        bakes = os.path.normcase(os.path.abspath(common.output_dir(scene)))
        tex = {}
        for ptr, _fn, _n in TEXTURES:
            path = common.image_file(getattr(d, ptr))
            if path and os.path.normcase(os.path.dirname(os.path.abspath(path))) == bakes \
                    and os.path.isfile(path):
                tex[ptr] = path     # baked in this window
        man["textures"] = tex
        man["fresh"] = _fresh(obj)
        man["stamps"] = {k: getattr(d, k) for k in ("ba_fingerprint", "bng_fingerprint",
                                                    "bp_fingerprint", "bnp_fingerprint",
                                                    "fingerprint", "ba_parts")}
        man["meta"] = {k: getattr(d, k) for k in META}
    except ImportError:
        pass
    tmp = os.path.join(folder, MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f)
    os.replace(tmp, os.path.join(folder, MANIFEST))     # Receive never reads half a file
    return faces


# ---------------------------------------------------------------- main file: Receive

def _load_mesh(job_id, name):
    path = os.path.join(job_dir(job_id), MESH_FILE)
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.meshes = [n for n in src.meshes if n == name] or list(src.meshes[:1])
    if not dst.meshes or dst.meshes[0] is None:
        raise RuntimeError("The sent mesh could not be read - Send Back again")
    return dst.meshes[0]


def _to_local(me, obj):
    """The window's mesh is in world space (its transforms applied): into obj's space."""
    m = obj.matrix_world.inverted()
    me.transform(m)
    if m.determinant() < 0:
        me.flip_normals()


def _swap_mesh(obj, me):
    old = obj.data
    for m in old.materials:
        me.materials.append(m)
    name = old.name
    shared = old.users > 1
    obj.data = me
    if shared:
        old.name = name + "_before_work"
    else:
        bpy.data.meshes.remove(old)
    me.name = name


def _take_modifiers(obj, man):
    """Modifiers the window applied or removed go here too; Decimate / Snap take its values."""
    kept = set(man["modifiers"])
    for m in [m for m in obj.modifiers if m.type != 'NODES' and m.name not in kept]:
        obj.modifiers.remove(m)
    dec, snap = wf.decimate_modifier(obj), wf.snap_modifier(obj)
    if dec is not None and "decimate" in man:
        dec.ratio = man["decimate"]["ratio"]
        dec.show_viewport = man["decimate"]["show_viewport"]
    if snap is not None and "snap" in man:
        snap.show_viewport = man["snap"]["show_viewport"]
    if man.get("applied"):
        obj[wf.APPLIED_KEY] = True


def _take_cameras(obj, scene, man):
    """Slot picks and per-object shifts (VCMix's channels mean slots 1/2/3)."""
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is None or not cam.is_setup or "slots" not in man:
        return
    from ..camera_project import core as cp
    for i, name in enumerate(man["slots"], 1):
        c = bpy.data.objects.get(name) if name else None
        setattr(cam, f"slot_{i}", c if c is not None and c.type == 'CAMERA' else None)
    if cam.slot_count != man["slot_count"]:
        cam["slot_count"] = int(man["slot_count"])     # raw: no re-pick from the callback
    cam.user_picked = man["user_picked"]
    if "cameras" in man:            # the list as scored / edited in the window
        cam.cameras.clear()
        for name, score, cov in man["cameras"]:
            c = bpy.data.objects.get(name)
            if c is not None and c.type == 'CAMERA':
                it = cam.cameras.add()
                it.camera, it.score, it.coverage = c, score, cov
        cam.removed.clear()
        for name in man["removed"]:
            c = bpy.data.objects.get(name)
            if c is not None and c.type == 'CAMERA':
                cam.removed.add().camera = c
        cam["cam_index"] = -1           # raw: no solo from the callback
        cam.coverage_filter = man["coverage_filter"]
    for name, shift in man["shifts"].items():
        c = bpy.data.objects.get(name)
        if c is not None and c.type == 'CAMERA' and not cp.is_global(c):
            it = cp.shift_item(obj, c, create=True)
            if tuple(round(v, 6) for v in it.shift) != tuple(round(v, 6) for v in shift):
                it.shift = shift            # the slider's own update: modifier + photo offset
    cp.apply_slots(obj, scene)


def _take_textures(obj, scene, man):
    """The bakes made in the window over the object's own files (the old ones to the Recycle
    Bin), its images pointed at them. Returns the pointers taken."""
    from ..baking import common, engine
    from ..export import fixes
    d = common.data(obj)
    taken = []
    for ptr, fn, normal in TEXTURES:
        src = man.get("textures", {}).get(ptr)
        if not src or not os.path.isfile(src):
            continue
        name = getattr(common, fn)(obj)
        dst = common.texture_path(scene, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(dst):
            fixes.to_recycle_bin([dst])
        shutil.copyfile(src, dst)
        img = getattr(d, ptr) or bpy.data.images.get(name)
        if img is None:
            img = bpy.data.images.load(dst, check_existing=False)
            img.name = name
        if normal:
            img.colorspace_settings.name = 'Non-Color'
        engine.link_file(img, dst)
        setattr(d, ptr, img)
        taken.append(ptr)
    for k, v in man.get("meta", {}).items():
        try:
            setattr(d, k, v)
        except (AttributeError, TypeError):
            pass
    return taken


def _restamp(obj, man, taken):
    """What was up to date in the window is up to date here (stamped from this file's own
    state); a bake taken that was not keeps the window's stamp, so it reads outdated."""
    from ..baking import common, fingerprint
    d = common.data(obj)
    fresh, stamps = man.get("fresh", {}), man.get("stamps", {})
    if fresh.get("ba"):
        d.ba_fingerprint = fingerprint.stamp_source(obj)
    elif "ba_image" in taken:
        d.ba_fingerprint, d.ba_parts = stamps.get("ba_fingerprint", ""), stamps.get("ba_parts", "")
    if fresh.get("bng"):
        rest = stamps["bng_fingerprint"][len(stamps["ba_fingerprint"]):]
        d.bng_fingerprint = d.ba_fingerprint + rest
    elif "bng_image" in taken:
        d.bng_fingerprint = stamps.get("bng_fingerprint", "")
    if fresh.get("bp"):
        d.bp_fingerprint = fingerprint.stamp_projection(obj)
    elif "bap_image" in taken:
        d.bp_fingerprint = stamps.get("bp_fingerprint", "")
    if fresh.get("bnp"):
        rest = stamps["bnp_fingerprint"][len(stamps["bp_fingerprint"]):]
        d.bnp_fingerprint = d.bp_fingerprint + rest
    elif "bnp_image" in taken:
        d.bnp_fingerprint = stamps.get("bnp_fingerprint", "")
    if fresh.get("alb"):
        d.fingerprint = fingerprint.compute(obj)
    elif "alb_image" in taken:
        d.fingerprint = stamps.get("fingerprint", "")


def _refresh(obj, scene):
    """The Refresh button for obj: MCP_ / MAT_ rebuilt with the new textures, cameras rewired."""
    try:
        from ..baking import cache, gn_final, matsync
    except ImportError:
        return
    matsync.sync(obj, scene, full=True)
    if gn_final.get_modifier(obj) is not None:
        gn_final.write_inputs(obj, scene)
    cache.clear()


def receive(context, obj, action='REPLACE'):
    """Take back what the work window sent. REPLACE: mesh, modifiers, slots / shifts, bakes,
    materials, stamps. ADD: the window's mesh joined into obj, nothing else."""
    man = new_send(obj) if obj is not None and out_record(obj) else None
    rec = out_record(obj)
    if man is None or rec is None:
        raise RuntimeError("Nothing new sent back - press Send Back in the work window")
    if obj.mode != 'OBJECT':
        raise RuntimeError("Object Mode first")
    scene = context.scene
    me = _load_mesh(rec["id"], man["mesh"])
    if action == 'ADD':
        tmp = bpy.data.objects.new(f"{obj.name}_work", me)     # world space, like the window
        scene.collection.objects.link(tmp)
        with context.temp_override(active_object=obj, object=obj,
                                   selected_objects=[obj, tmp],
                                   selected_editable_objects=[obj, tmp]):
            bpy.ops.object.join()
        faces = len(me.polygons)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        try:
            from ..baking import matsync
            matsync.sync(obj, scene)
        except ImportError:
            pass
    else:
        _to_local(me, obj)
        faces = len(me.polygons)
        _swap_mesh(obj, me)
        _take_modifiers(obj, man)
        _take_cameras(obj, scene, man)
        taken = []
        try:
            taken = _take_textures(obj, scene, man)
        except ImportError:
            pass
        _refresh(obj, scene)
        try:
            _restamp(obj, man, taken)
            from ..baking import cache
            cache.clear()
        except ImportError:
            pass
    _stamp_record(obj, received=man["time"])   # linked on: the next Send Back is new
    return faces


# ---------------------------------------------------------------- work window: saved

def _move_bakes_for_save(scene):
    """Before a work window is saved: its bake folder (temp) moves to the main file's
    _work/<object> folder and its images follow, so the saved file keeps its textures."""
    dest = scene.get(WORK_BAKES_KEY, "")
    obj = work_object(scene)
    if not dest or obj is None:
        return
    from ..baking import common, engine
    s = common.settings(scene)
    cur = os.path.normcase(os.path.abspath(common.output_dir(scene)))
    if cur == os.path.normcase(os.path.abspath(dest)):
        return
    os.makedirs(dest, exist_ok=True)
    d = common.data(obj)
    for ptr, _fn, _n in TEXTURES:
        img = getattr(d, ptr)
        path = common.image_file(img)
        if path and os.path.normcase(os.path.dirname(os.path.abspath(path))) == cur \
                and os.path.isfile(path):
            new = os.path.join(dest, os.path.basename(path))
            shutil.copyfile(path, new)
            engine.link_file(img, new)
    s.output_dir = os.path.join(dest, "")


@persistent
def _on_save_pre(*_args):
    try:
        for scene in bpy.data.scenes:
            if is_work_window(scene):
                _move_bakes_for_save(scene)
    except Exception as e:  # never block a save
        print(f"[MultiCamProject] work window bakes not moved: {e}")


@persistent
def _on_save_post(*_args):
    """A saved work window tells the main file where it is (the row's open button)."""
    try:
        for scene in bpy.data.scenes:
            job_id = scene.get(WORK_ID_KEY, "")
            if is_work_window(scene) and job_id and bpy.data.filepath:
                os.makedirs(job_dir(job_id), exist_ok=True)
                with open(os.path.join(job_dir(job_id), WINDOW_FILE), "w", encoding="utf-8") as f:
                    json.dump({"path": bpy.data.filepath, "time": time.time()}, f)
    except Exception as e:
        print(f"[MultiCamProject] work window path not recorded: {e}")


# ---------------------------------------------------------------- relink by hand

def read_work_file(path):
    """(send-out id, object name, main file) of a saved work window, read by linking its
    scene for a moment (its objects are not kept)."""
    if not os.path.isfile(path):
        raise RuntimeError(f"No file at {path}")
    with bpy.data.libraries.load(path, link=True) as (src, dst):
        dst.scenes = list(src.scenes[:1])
    scene = dst.scenes[0] if dst.scenes else None
    lib = scene.library if scene is not None else None
    try:
        if scene is None or not scene.get(WORK_ID_KEY):
            raise RuntimeError(f"{os.path.basename(path)} is not a work window "
                               "(saved from Send Out)")
        return scene[WORK_ID_KEY], scene.get(WORK_OBJECT_KEY, ""), scene.get(WORK_KEY, "")
    finally:
        if lib is not None:
            bpy.data.libraries.remove(lib)


def relink(obj, path):
    """Link obj to the saved work window at `path` (moved, renamed or never linked): the
    window's id goes on obj, the main file remembers where the file is. Another object of
    this file linked to the same window lets go. Returns the window's object name."""
    job_id, work_name, _main = read_work_file(path)
    for o in bpy.data.objects:
        if o != obj and (out_record(o) or {}).get("id") == job_id:
            cancel(o)
    rec = out_record(obj) or {}
    if rec.get("id") != job_id:
        rec = {"id": job_id, "time": time.time()}
        obj[OUT_KEY] = json.dumps(rec)
        _stamp_record(obj)
        man = sent_back(obj)        # what was sent before the relink counts as received
        if man is not None:
            _stamp_record(obj, received=man.get("time", 0))
    os.makedirs(job_dir(job_id), exist_ok=True)
    with open(os.path.join(job_dir(job_id), WINDOW_FILE), "w", encoding="utf-8") as f:
        json.dump({"path": os.path.abspath(path), "time": time.time()}, f)
    return work_name


def linked_objects():
    """The objects of this file linked to a work window, by name."""
    return sorted((o for o in bpy.data.objects if not o.library and out_record(o)),
                  key=lambda o: o.name.lower())


def register():
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.handlers.save_post.append(_on_save_post)


def unregister():
    for lst, fn in ((bpy.app.handlers.save_pre, _on_save_pre),
                    (bpy.app.handlers.save_post, _on_save_post)):
        if fn in lst:
            lst.remove(fn)
