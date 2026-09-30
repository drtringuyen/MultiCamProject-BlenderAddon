import bpy
from bpy.app.handlers import persistent

from . import props, operators, ui, paint_sync


@persistent
def _on_load(_):
    from . import core
    core.migrate_all()


@persistent
def _on_save_pre(_):
    """Remember each object's valid Mode (and put back a lost one) with the file."""
    from . import core
    try:
        for obj in core._setup_objects():
            if not obj.library:
                core.keep_mode(obj)
    except Exception as e:  # never block a save
        print(f"[MultiCamProject] Mode check skipped: {e}")


def _migrate_now():
    from . import core
    try:
        core.migrate_all()
    except Exception as e:  # never block addon startup
        print(f"[MultiCamProject] migration skipped: {e}")
    return None


def register():
    props.register()
    operators.register()
    ui.register()
    paint_sync.register()
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.timers.register(_migrate_now, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save_pre)
    paint_sync.unregister()
    ui.unregister()
    operators.unregister()
    props.unregister()
