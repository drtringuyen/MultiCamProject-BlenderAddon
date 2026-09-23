import bpy
from bpy.app.handlers import persistent

from . import props, operators, ui


@persistent
def _on_load(_):
    from . import core
    core.migrate_all()


def _migrate_now():
    from . import core
    try:
        core.migrate_all()
    except Exception as e:  # never block addon startup
        print(f"[MultiCamProject] shift migration skipped: {e}")
    return None


def register():
    props.register()
    operators.register()
    ui.register()
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_migrate_now, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    ui.unregister()
    operators.unregister()
    props.unregister()
