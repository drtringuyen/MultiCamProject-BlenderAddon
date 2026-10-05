import bpy
from bpy.app.handlers import persistent

from . import operators, tool, ui, workfile


def _repair():
    from . import workflow
    try:
        workflow.repair_originals()     # originals made before the repairs existed
    except Exception as e:  # never block loading
        print(f"[MultiCamProject] Remesh original repair skipped: {e}")
    return None


@persistent
def _on_load(_):
    _repair()


def register():
    operators.register()
    workfile.register()
    tool.register()
    ui.register()
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_repair, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    ui.unregister()
    tool.unregister()
    workfile.unregister()
    operators.unregister()
