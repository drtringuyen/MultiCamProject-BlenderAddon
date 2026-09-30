"""Baking: the camera projection (plus the scan) into ALB_/NOR_ on the user's uv_normal,
the MAT_ material, and GN-Final to flip between the projection and the baked result.
Works without camera_project: any mesh with uv_normal can be baked."""
import bpy
from bpy.app.handlers import persistent

from . import cache, matsync, owned, props, operators, ui


_SOURCE_KEY = "bake_source_migrated"


def _migrate_bake_source():
    """(2026-09-29) Remesh copies from before Bake Source bake from their original - once,
    so a Bake Source the user cleared stays empty."""
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.library:
            continue
        d = obj.multicamproject_bake
        if d.source is not None and d.bake_source is None and not d.get(_SOURCE_KEY):
            d.bake_source = d.source
        if d.source is not None:
            d[_SOURCE_KEY] = 1


def _migrate():
    from . import gn_final
    try:
        gn_final.migrate_wrappers()
        _migrate_bake_source()
    except Exception as e:  # never block addon startup or a file load
        print(f"[MultiCamProject] Baking migration skipped: {e}")
    return None


@persistent
def _on_load(_):
    _migrate()


def register():
    props.register()
    operators.register()
    ui.register()
    cache.register()
    matsync.register()
    owned.register()
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_migrate, first_interval=0.1)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    owned.unregister()
    matsync.unregister()
    cache.unregister()
    ui.unregister()
    operators.unregister()
    props.unregister()
