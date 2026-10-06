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
    if not hasattr(bpy.types, "MULTICAMPROJECT_OT_auto_fill_folders"):
        return prefix_ok(scene)         # linking module off: no Setup File IO to run
    return props.file_io_done and prefix_ok(scene)


def lock(layout, context):
    """Grey out a panel until the gate is open. Returns True when open."""
    ok = ready(context.scene)
    layout.enabled = ok
    return ok


def draw_start(layout, context):
    """Setup's first row while locked: [Name Prefix] [Setup File IO]."""
    scene = context.scene
    es = getattr(scene, "multicamproject_export", None)
    row = layout.row(align=True)
    row.scale_y = 1.3
    if es is not None:
        field = row.row(align=True)
        field.alert = not prefix_ok(scene)
        field.prop(es, "name_prefix", text="", placeholder="Name Prefix: 00_st")
    sub = row.row(align=True)
    sub.enabled = prefix_ok(scene)
    sub.operator("multicamproject.auto_fill_folders", text="Setup File IO", icon='VIEWZOOM')
    hint = layout.row()
    hint.active = False
    hint.label(text="Name Prefix like 00_st, then Setup File IO - the add-on unlocks"
               if not prefix_ok(scene) else "Setup File IO unlocks the add-on", icon='INFO')
