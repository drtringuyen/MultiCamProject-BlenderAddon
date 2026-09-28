"""Baking: the camera projection (plus the scan) into ALB_/NOR_ on the user's uv_normal,
the MAT_ material, and GN-Final to flip between the projection and the baked result.
Works without camera_project: any mesh with uv_normal can be baked."""
import bpy
from bpy.app.handlers import persistent

from . import cache, props, operators, ui


def _migrate():
    from . import gn_final
    try:
        gn_final.migrate_wrappers()
    except Exception as e:  # never block addon startup or a file load
        print(f"[MultiCamProject] GN-Final migration skipped: {e}")
    return None


@persistent
def _on_load(_):
    _migrate()


def register():
    props.register()
    operators.register()
    ui.register()
    cache.register()
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_migrate, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    cache.unregister()
    ui.unregister()
    operators.unregister()
    props.unregister()
