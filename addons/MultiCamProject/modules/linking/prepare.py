"""A work file gets ready on its own (user request 2026-11-16): 02 finished and 04 ready to
paint, once per file -

  1  the Decimate applied (its ratio is the Estimation's budget)
  2  uv_normal by Smart UV (a mesh that has uv_normal keeps it)
  3  the cameras resorted (the camera list scored on the low poly)
  4  the projection cleared: BAo_ shows everywhere, the Bake Route is Mixed
  5  BAo_ baked from the Bake Source (Cycles)

It runs where a window is (a new MSK_ is drawn on the GPU): at the end of a windowed build,
else on the first open of a headless (Automate) work file. A file that has PREPARED_KEY is
never touched again, so what the user did afterwards stays."""
import bpy

from ... import module_manager

PREPARED_KEY = "multicamproject_work_prepared"


def _override(context, obj):
    return context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                 selected_editable_objects=[obj])


def prepare(context, obj):
    """Steps 1-4 on the work window's object. Returns what was done (report lines)."""
    done = []
    if obj.mode != 'OBJECT':
        with _override(context, obj):
            bpy.ops.object.mode_set(mode='OBJECT')
    if module_manager.is_loaded("remesh"):
        from ..remesh import workflow as wf
        if wf.decimate_pending(obj):
            before, after = wf.apply_decimate(context, obj)
            done.append(f"Decimate applied: {before:,} -> {after:,} faces")
    if module_manager.is_loaded("baking") and module_manager.is_loaded("export"):
        from ..baking import common
        from ..export import autofix
        if obj.data.uv_layers.get(common.UV_NORMAL) is None:
            # Smart UV, not the scan's UV renamed: a delivery scan's UV sits in a UDIM tile
            # (u 4-5) and the Decimate tears it - BAo_ came out black (2026-11-16)
            autofix.make_uv_normal(context, obj)
            done.append("uv_normal: Smart UV")
    cam = getattr(obj, "multicamproject_cam", None)
    if module_manager.is_loaded("camera_project") and cam is not None and cam.is_setup:
        from ..camera_project import paint_tex
        with _override(context, obj):
            bpy.ops.multicamproject.auto_pick()
            done.append("Cameras resorted")
            bpy.ops.multicamproject.mask_fill(value=0.0)
        paint_tex._flush()          # the queued fill now: the file is saved right after
        done.append("Projection cleared: BAo_ shows")
        if module_manager.is_loaded("baking"):
            from ..baking import route
            d = obj.multicamproject_bake
            if not d.handmade and not d.locked and route.has_original(obj) \
                    and route.get(obj) != route.MIXED:
                route.set_route(obj, route.MIXED, user=True)
            done.append(f"Bake Route: {route.get(obj).title()}")
    if module_manager.is_loaded("baking"):
        done += bake_bao(context, obj)
    return done


def bake_bao(context, obj):
    """5: BAo_ from the Bake Source, so the cleared object shows it - a job with its progress
    bar in a window, straight through without one (headless)."""
    from ..baking import engine
    from ..baking.operators import source_poll_problem
    from . import core
    core.fix_scene_drivers(context.scene)   # a work file built before 2026-11-16: black else
    why = source_poll_problem(obj)
    if why:
        return [f"BAo_ not baked: {why}"]
    if not engine.needs_source_bake(obj):
        return ["BAo_ is current"]
    with _override(context, obj):
        bpy.ops.multicamproject.bake_from_source()
    return ["BAo_ baked" if bpy.app.background or context.window is None else "BAo_ baking"]


def run_once(context, scene):
    """The work window's object prepared, unless this file was before. Returns the lines
    (None: nothing to do)."""
    from . import core
    if bpy.app.background or not core.is_work_window(scene) or scene.get(PREPARED_KEY):
        return None
    obj = core.work_object(scene)
    if obj is None:
        return None
    scene[PREPARED_KEY] = True      # once, also when a step fails: never again on its own
    try:
        lines = prepare(context, obj)
    except Exception as e:          # never break opening the window
        print(f"[MultiCamProject] work file not prepared: {e}")
        return [f"Not prepared: {e}"]
    for line in lines:
        print(f"[MultiCamProject] {obj.name}: {line}")
    return lines
