"""Baking: the camera projection (plus the scan) into ALB_/NOR_ on the user's uv_normal,
the MAT_ material, and GN-Final to flip between the projection and the baked result.
Works without camera_project: any mesh with uv_normal can be baked."""
import bpy
from bpy.app.handlers import persistent

from . import cache, cleanup, jobs, matsync, owned, props, operators, route, ui


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


_CAGE_KEY = "cage_migrated"


def _migrate_cage():
    """(2026-10-05) The Cage was scene-wide: changing it outdated every BA_. Each object
    gets the Cage its BA_ was baked with (found by its stored state), else the scene's.
    Once per file (flag on the scenes): later objects keep the Cage they were given."""
    from . import fingerprint
    if not bpy.data.scenes or any(sc.multicamproject_bake_settings.get(_CAGE_KEY)
                                  for sc in bpy.data.scenes):
        return
    scene_cages = [sc.multicamproject_bake_settings.cage_extrusion for sc in bpy.data.scenes]
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.library:
            continue
        d = obj.multicamproject_bake
        scenes = [sc.multicamproject_bake_settings.cage_extrusion for sc in obj.users_scene]
        pick = (scenes or scene_cages or [d.cage])[0]
        if d.ba_fingerprint:
            stored = d.ba_fingerprint.split(":")[0]
            for c in dict.fromkeys(scenes + scene_cages + [0.02, d.ba_fit_cage]):
                if fingerprint.source_state(obj, cage=c) == stored:
                    pick = c
                    break
        d.cage = pick
    for sc in bpy.data.scenes:
        sc.multicamproject_bake_settings[_CAGE_KEY] = 1


def _migrate():
    from . import gn_final
    try:
        gn_final.migrate_wrappers()
        _migrate_bake_source()
        _migrate_cage()
        from . import migrate_route
        migrate_route.migrate()
    except Exception as e:  # never block addon startup or a file load
        print(f"[MultiCamProject] Baking migration skipped: {e}")
    return None


@persistent
def _on_load(_):
    _migrate()


def register():
    props.register()
    jobs.register()
    operators.register()
    route.register()
    cleanup.register()
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
    cleanup.unregister()
    route.unregister()
    operators.unregister()
    jobs.unregister()
    props.unregister()
