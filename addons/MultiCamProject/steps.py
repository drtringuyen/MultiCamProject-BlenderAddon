"""The per-object workflow, steps 01-09, and its stage (Raw / Processed / Baked / Locked).

Everything except `locked` is derived from what the object already holds (the workflow, its
Decimate, uv_normal, the bake stamps), so a change that outdates a stamp sends the object back
to that step on its own - no stored step to keep in sync, no timer. An outdated result only
warns the steps after it (outdated_note); a missing one locks them.

  01 Setup            the file: Name Prefix + Setup File IO (gate.ready)
  02 Mesh Workflow    2B Remesh / 2C Retopo / 2D Only Project, Decimate applied, uv_normal
  03 Mesh Editing     BAo_ baked and fresh
  04 Projection       optional: never blocks 05
  05 Bake Albedo      ALB_ fresh
  06 Final Texture Fix optional (Inpainting, Seam Fix): never blocks 07
  07 Generate Normal  NOR_ fresh
  08 Lock Finish      by hand only
  09 Export           never locked (nor is Polycount Estimation)"""
import time
from collections import namedtuple

import bpy
from bpy.app.handlers import persistent

from . import gate

STAGES = {      # stage -> (label, icon)
    'RAW': ("Raw", 'STRIP_COLOR_09'),
    'PROCESSED': ("Processed", 'STRIP_COLOR_03'),
    'BAKED': ("Baked", 'STRIP_COLOR_04'),
    'LOCKED': ("Locked", 'LOCKED'),
}

STEP_NAMES = {1: "01. Setup", 2: "02. Mesh Workflow", 3: "03. Mesh Editing",
              4: "04. Projection Painting", 5: "05. Bake Albedo", 6: "06. Final Texture Fix",
              7: "07. Generate Normal", 8: "08. Lock Finish", 9: "09. Export"}

# done: the step's result is there and current. made: it is there (maybe outdated) - what
# the next steps wait for: a changed mesh / uv_normal warns (outdated_note), it locks nothing
State = namedtuple("State", "workflow done stage made")


def _bake(obj):
    return getattr(obj, "multicamproject_bake", None)


def _wf():
    try:
        from .modules.remesh import workflow as wf
    except ImportError:
        return None
    return wf


def workflow(obj):
    """The object's mesh workflow: REMESH / RETOPO / PROJECT / HANDMADE / NONE. Picked in 02
    (stored), else read from what the object is (files from before 2026-10-12)."""
    d = _bake(obj)
    if d is None:
        return 'NONE'
    if d.handmade:
        return 'HANDMADE'
    if d.workflow != 'NONE':
        return d.workflow
    wf = _wf()
    if wf is not None and wf.is_low_poly(obj):
        return 'RETOPO' if wf.is_retopo(obj) else 'REMESH'
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None and cam.is_setup:
        return 'PROJECT'
    return 'NONE'


def _uv_normal(obj):
    from .modules.baking import common
    return common.has_uv_normal(obj)


def mesh_done(obj):
    """02: a workflow is chosen, its Decimate decided and applied, uv_normal made."""
    w = workflow(obj)
    if w == 'NONE':
        return False
    wf = _wf()
    if wf is not None and wf.decimate_pending(obj):
        return False
    if w == 'REMESH' and wf is not None and wf.decimate_modifier(obj) is not None:
        return False        # a live Decimate: the topology is not final yet
    return _uv_normal(obj)


def bao_fresh(obj):
    """03: BAo_ is on disk and matches the mesh, uv_normal and the Bake Source."""
    from .modules.baking import common, fingerprint
    d = _bake(obj)
    if d is None:
        return False
    if d.handmade:          # no scan behind it: its own textures are the start
        return True
    return (d.ba_image is not None and common.file_ok(d.ba_image)
            and not fingerprint.ba_outdated(obj))


def alb_fresh(obj):
    """05: ALB_ is on disk and matches its route's inputs (BAo_, the projection, the paint)."""
    from .modules.baking import common, fingerprint
    d = _bake(obj)
    if d is None or d.alb_image is None or not common.file_ok(d.alb_image):
        return False
    if d.handmade or d.alb_mode == 'LOAD':     # Load (TexOverride): its file is the result
        return True
    return not (fingerprint.is_outdated(obj) or fingerprint.original_outdated(obj)
                or fingerprint.projection_outdated(obj))


def _file_there(img):
    from .modules.baking import common
    return img is not None and common.file_ok(img)


def bao_made(obj):
    """03 baked once: BAo_ on disk (outdated or not)."""
    d = _bake(obj)
    return d is not None and (d.handmade or _file_there(d.ba_image))


def nor_fresh(obj):
    """07: NOR_ is on disk and made from the current ALB_ (its inpaint) and settings."""
    from .modules.baking import common, normal
    d = _bake(obj)
    if d is None or d.nor_image is None or not common.file_ok(d.nor_image):
        return False
    if d.handmade or d.nor_mode == 'LOAD':     # Load (TexOverride): its file is the result
        return True
    return alb_fresh(obj) and not normal.nor_outdated(obj)


# ---------------------------------------------------------------- the panels' memo
# A sidebar redraw asks for the same object's state ~30 times (Stage cells, step locks,
# headers) and scrolling redraws it many times a second. The answers are kept until anything
# changes (depsgraph update, undo, load, the end of a bake job) or for MEMO_TTL at most (a
# file changed outside Blender shows after that).

MEMO_TTL = 5.0
_memo = {}


def memo(key, fn):
    """fn() kept under `key` until the next change (forget) or MEMO_TTL."""
    now = time.monotonic()
    hit = _memo.get(key)
    if hit is not None and now - hit[0] < MEMO_TTL:
        return hit[1]
    val = fn()
    _memo[key] = (now, val)
    return val


def forget(*_args):
    _memo.clear()


def state(obj, scene=None, fresh=False):
    """The object's workflow, which steps are done ({1..8}) and its stage. Memoized for the
    panels (memo); `fresh`: computed now (operators)."""
    scene = scene or bpy.context.scene
    if fresh or obj is None:
        return _state(obj, scene)
    return memo(("state", obj.name, scene.name), lambda: _state(obj, scene))


def _state(obj, scene):
    d = _bake(obj)
    done = set()
    if gate.ready(scene):
        done.add(1)
    if obj is None or obj.type != 'MESH' or d is None:
        return State('NONE', done, 'RAW', set(done))
    made = set(done)
    if mesh_done(obj):
        done.add(2)
        made.add(2)
        loaded = d.alb_mode == 'LOAD'       # TexOverride: ALB_ needs no BAo_ behind it
        if bao_fresh(obj):
            done.add(3)
        if (3 in done or loaded) and alb_fresh(obj):
            done.add(5)
            if nor_fresh(obj):
                done.add(7)
        if bao_made(obj):
            made.add(3)
        if (3 in made or loaded) and _file_there(d.alb_image):
            made.add(5)
            if _file_there(d.nor_image):
                made.add(7)
    if d.locked:
        stage = 'LOCKED'
    elif 7 in done:
        stage = 'BAKED'
    elif 3 in done:
        stage = 'PROCESSED'
    else:
        stage = 'RAW'
    return State(workflow(obj), done, stage, made)


# what each step waits for: (the step that must be done, what to tell the user)
_NEEDS = {
    2: (1, "Finish 01: Name Prefix + Setup File IO"),
    3: (2, "Finish 02: pick a workflow, apply the Decimate, make uv_normal"),
    4: (3, "Finish 03: bake BAo_"),
    5: (3, "Finish 03: bake BAo_"),
    6: (5, "Finish 05: bake the Albedo"),
    7: (3, "Finish 03: bake BAo_"),            # 07 bakes an outdated ALB_ first
    8: (7, "Finish 07: generate the Normal"),
}


def why_locked(obj, step, scene=None):
    """'' when `step` is open for the object, else why not (one line for the panel)."""
    if step in (1, 9):
        return ""
    scene = scene or bpy.context.scene
    if not gate.ready(scene):
        return _NEEDS[2][1]
    d = _bake(obj) if obj is not None else None
    if d is not None and d.locked and step != 8:
        return "Locked Finish - click the lock to unlock it"
    if step == 2:
        return ""
    if obj is None or obj.type != 'MESH':
        return "Select a mesh"
    st = state(obj, scene)
    need, text = _NEEDS[step]
    if step == 7 and d is not None and d.alb_mode == 'LOAD' and 5 in st.made:
        return ""           # NOR_ from the loaded ALB_ (no file: 07 bakes ALB_, needs 03)
    return "" if need in st.made else text


def outdated_note(obj, step, scene=None):
    """'' or a warning for an open step whose earlier result is there but outdated (the mesh
    or uv_normal changed since): the step works, its result follows the old bake."""
    if step not in _NEEDS or obj is None or obj.type != 'MESH':
        return ""
    st = state(obj, scene)
    need = _NEEDS[step][0]
    d = _bake(obj)
    if step == 7 and d is not None and d.alb_mode == 'LOAD':
        return ""           # NOR_ from the loaded ALB_: 03 does not matter
    if need not in st.made or need in st.done:
        return ""
    if need == 3:
        from .modules.baking import fingerprint
        why = fingerprint.ba_why(obj) or "BAo_ outdated"
        if step in (5, 7):          # their bake makes BAo_ again first (needs_source_bake)
            return f"03 outdated ({why}) - this bake redoes BAo_ first"
        return f"03 outdated ({why}) - bake BAo_ again to update"
    if need == 5:
        return "05 is outdated - bake the Albedo again to update"
    return "07 is outdated - generate the Normal again to update"


def sent_out(obj, scene=None):
    """The main file's object is out in a work window: 03 is worked on there, never here."""
    scene = scene or bpy.context.scene
    try:
        from .modules.linking import core
    except ImportError:
        return False
    return (obj is not None and not core.is_work_window(scene)
            and core.out_record(obj) is not None)


def stage(obj, scene=None):
    return state(obj, scene).stage


def draw_stage(layout, context, obj, units=3.6):
    """The Stage column of the EXPORT and Estimation lists (red: Locked with a file
    missing in the Export folder)."""
    stg = stage(obj, context.scene)
    col = layout.row(align=True)
    col.ui_units_x = units
    if stg == 'LOCKED' and locked_missing(obj, context.scene):
        col.alert = True
    # the square is the lock toggle: green (Baked) locks, the lock unlocks; else a label
    if stg in {'BAKED', 'LOCKED'} and hasattr(bpy.types, "MULTICAMPROJECT_OT_lock_finish"):
        op = col.operator("multicamproject.lock_finish", text="", emboss=False,
                          icon=STAGES[stg][1], depress=stg == 'LOCKED')
        op.action = 'LOCK' if stg == 'BAKED' else 'UNLOCK'
        op.object_name = obj.name
    else:
        col.label(text="", icon=STAGES[stg][1])
    col.label(text="Missing" if col.alert else STAGES[stg][0])


# ---------------------------------------------------------------- 08 Lock Finish

def published(obj, scene=None):
    """[(kind, path)] the Export folder holds for a Locked object: <export>/Textures/
    ALB_<name>.png and NOR_<name>.png ([] without the Export module / folder)."""
    import os
    from .modules.baking import common
    scene = scene or bpy.context.scene
    folder = common.export_textures_dir(scene)
    d = _bake(obj)
    if not folder or d is None:
        return []
    return [(k, os.path.join(folder, img.name + ".png"))
            for k, img in (("ALB_", d.alb_image), ("NOR_", d.nor_image)) if img is not None]


def locked_missing(obj, scene=None):
    """What a Locked object misses: ALB_ / NOR_ itself, or its copy in the Export folder."""
    from .modules.baking import cache
    d = _bake(obj)
    if d is None or not d.locked:
        return []
    out = [k for k, img in (("ALB_", d.alb_image), ("NOR_", d.nor_image)) if img is None]
    out += [f"Export/Textures/{k}" for k, p in published(obj, scene) if not cache.isfile(p)]
    return out


def protected_files():
    """Normalized paths no clean-up may remove: every Locked object's ALB_, its inpaint,
    NOR_ and their copies in the Export folder."""
    import os
    from .modules.baking import common
    out = set()
    for o in bpy.data.objects:
        d = _bake(o)
        if d is None or o.type != 'MESH' or not d.locked:
            continue
        for img in (d.alb_image, d.alb_inpaint, d.nor_image):
            f = common.image_file(img) if img is not None else ""
            if f:
                out.add(os.path.normcase(os.path.abspath(f)))
        for scene in o.users_scene:
            out.update(os.path.normcase(os.path.abspath(p)) for _k, p in published(o, scene))
    return out


_HANDLERS = (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.undo_post,
             bpy.app.handlers.redo_post, bpy.app.handlers.load_post)


@persistent
def _on_change(*_args):
    _memo.clear()


def register():
    for lst in _HANDLERS:
        if _on_change not in lst:
            lst.append(_on_change)


def unregister():
    for lst in _HANDLERS:
        if _on_change in lst:
            lst.remove(_on_change)
    _memo.clear()
