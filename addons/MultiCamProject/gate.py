"""The add-on's start gate: until the file has a Name Prefix and Setup File IO was run once,
every panel is greyed out and Setup shows only [Name Prefix] [Setup File IO]. A work window
is never gated (it is set up by its main file)."""
import bpy


def prefix_ok(scene):
    """The Name Prefix is filled (no Export module: nothing to fill)."""
    es = getattr(scene, "multicamproject_export", None)
    return es is None or bool(es.name_prefix.strip())


def _work_window(scene):
    try:
        from .modules.linking import core
    except ImportError:
        return False
    return core.is_work_window(scene)


def ready(scene):
    """The add-on is unlocked in this scene."""
    props = getattr(scene, "multicamproject_props", None)
    if props is None or _work_window(scene):
        return True
    return props.file_io_done and prefix_ok(scene)


def decimate_pending(obj):
    """The active object waits for its Decimate decision (Cutting & Modelling)."""
    try:
        from .modules.remesh import workflow as wf
    except ImportError:
        return False
    return wf.decimate_pending(obj)


def lock(layout, context, step=None):
    """Grey out a panel until the gate is open, and while the active object's Decimate is
    not decided. `step` (2-8): also until the active object has done the steps before it
    (steps.why_locked). Returns True when open."""
    ok = ready(context.scene) and not decimate_pending(context.active_object)
    if ok and step is not None:
        from . import steps
        ok = not steps.why_locked(context.active_object, step, context.scene)
    layout.enabled = ok
    return ok


def step_box(layout, context, step):
    """A step panel's top: the reason it is locked (a greyed line), and the layout to draw
    into - greyed while locked. Returns (layout, open)."""
    from . import steps
    why = "" if _work_window(context.scene) else steps.why_locked(context.active_object, step,
                                                                  context.scene)
    if not why:
        return layout, True
    row = layout.row(align=True)
    row.alert = True
    obj = context.active_object
    d = getattr(obj, "multicamproject_bake", None) if obj is not None else None
    if d is not None and d.locked and hasattr(bpy.types, "MULTICAMPROJECT_OT_lock_finish"):
        # Locked Finish: the lock is the Unlock button (back to Baked)
        op = row.operator("multicamproject.lock_finish", text="", icon='LOCKED', depress=True)
        op.action, op.object_name = 'UNLOCK', obj.name
        row.label(text=why)
    else:
        row.label(text=why, icon='LOCKED')
    col = layout.column()
    col.enabled = False
    return col, False


def draw_start(layout, context):
    """01 Setup: [Name Prefix] [Setup File IO] (+ a hint while the add-on is locked). Not in
    a work window: its main file sets it up."""
    scene = context.scene
    if _work_window(scene):
        return
    es = getattr(scene, "multicamproject_export", None)
    row = layout.row(align=True)
    row.scale_y = 1.3
    if es is not None:
        field = row.row(align=True)
        field.alert = not prefix_ok(scene)
        field.prop(es, "name_prefix", text="", placeholder="Name Prefix: 00_30stBR")
    sub = row.row(align=True)
    sub.enabled = prefix_ok(scene)
    sub.operator("multicamproject.project_setup", text="Setup File IO", icon='VIEWZOOM')
    if ready(scene):
        return
    hint = layout.row()
    hint.active = False
    hint.label(text="Name Prefix like 00_30stBR, then Setup File IO - the add-on unlocks"
               if not prefix_ok(scene) else "Setup File IO unlocks the add-on", icon='INFO')
