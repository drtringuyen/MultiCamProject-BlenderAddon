"""The per-object workflow, steps 01-09, and its stage (Raw / Processed / Baked / Locked).

Everything except `locked` is derived from what the object already holds (the workflow, its
Decimate, uv_normal, the bake stamps), so a change that outdates a stamp sends the object back
to that step on its own - no stored step to keep in sync, no timer.

  01 Setup            the file: Name Prefix + Setup File IO (gate.ready)
  02 Mesh Workflow    2B Remesh / 2C Retopo / 2D Only Project, Decimate applied, uv_normal
  03 Mesh Editing     BAo_ baked and fresh
  04 Projection       optional: never blocks 05
  05 Bake Albedo      ALB_ fresh
  06 Inpainting       optional: never blocks 07
  07 Generate Normal  NOR_ fresh
  08 Lock Finish      by hand only
  09 Export           never locked (nor is Polycount Estimation)"""
from collections import namedtuple

import bpy

from . import gate

STAGES = {      # stage -> (label, icon)
    'RAW': ("Raw", 'STRIP_COLOR_09'),
    'PROCESSED': ("Processed", 'STRIP_COLOR_03'),
    'BAKED': ("Baked", 'STRIP_COLOR_04'),
    'LOCKED': ("Locked", 'LOCKED'),
}

STEP_NAMES = {1: "01. Setup", 2: "02. Mesh Workflow", 3: "03. Mesh Editing",
              4: "04. Projection Painting", 5: "05. Bake Albedo", 6: "06. Inpainting",
              7: "07. Generate Normal", 8: "08. Lock Finish", 9: "09. Export"}

State = namedtuple("State", "workflow done stage")


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
    if d.handmade:
        return True
    return not (fingerprint.is_outdated(obj) or fingerprint.original_outdated(obj)
                or fingerprint.projection_outdated(obj))


def nor_fresh(obj):
    """07: NOR_ is on disk and made from the current ALB_."""
    from .modules.baking import common
    d = _bake(obj)
    if d is None or d.nor_image is None or not common.file_ok(d.nor_image):
        return False
    return alb_fresh(obj)


def state(obj, scene=None):
    """The object's workflow, which steps are done ({1..8}) and its stage."""
    scene = scene or bpy.context.scene
    d = _bake(obj)
    done = set()
    if gate.ready(scene):
        done.add(1)
    if obj is None or obj.type != 'MESH' or d is None:
        return State('NONE', done, 'RAW')
    if mesh_done(obj):
        done.add(2)
        if bao_fresh(obj):
            done.add(3)
            if alb_fresh(obj):
                done.add(5)
                if nor_fresh(obj):
                    done.add(7)
    if d.locked:
        stage = 'LOCKED'
    elif 7 in done:
        stage = 'BAKED'
    elif 3 in done:
        stage = 'PROCESSED'
    else:
        stage = 'RAW'
    return State(workflow(obj), done, stage)


# what each step waits for: (the step that must be done, what to tell the user)
_NEEDS = {
    2: (1, "Finish 01: Name Prefix + Setup File IO"),
    3: (2, "Finish 02: pick a workflow, apply the Decimate, make uv_normal"),
    4: (3, "Finish 03: bake BAo_"),
    5: (3, "Finish 03: bake BAo_"),
    6: (5, "Finish 05: bake the Albedo"),
    7: (5, "Finish 05: bake the Albedo"),
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
        return "Locked Finish: unlock it in 08 to change it"
    if step == 2:
        return ""
    if obj is None or obj.type != 'MESH':
        return "Select a mesh"
    st = state(obj, scene)
    need, text = _NEEDS[step]
    return "" if need in st.done else text


def stage(obj, scene=None):
    return state(obj, scene).stage


def draw_stage(layout, context, obj, units=3.6):
    """The Stage column of the EXPORT and Estimation lists."""
    stg = stage(obj, context.scene)
    col = layout.row(align=True)
    col.ui_units_x = units
    col.label(text=STAGES[stg][0], icon=STAGES[stg][1])
