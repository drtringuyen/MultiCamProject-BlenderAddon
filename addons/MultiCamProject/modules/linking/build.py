"""Run by linking.core.send_out in a new Blender (never imported): the object written by the
main file - with its cameras, original, materials and GN - goes into an empty scene, its
transforms applied and the parents dropped. The file stays untitled: nothing saves it.

blender --python build.py -- <settings.json>
"""
import importlib
import json
import os
import sys

import bpy
from mathutils import Matrix

with open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8") as _f:
    settings = json.load(_f)

# core.WORK_KEY / WORK_OBJECT_KEY / WORK_ID_KEY / WORK_BAKES_KEY / WORK_SAVE_AS_KEY (this script
# runs outside the add-on)
SCENE_KEYS = {"main": "multicamproject_work_of", "object": "multicamproject_work_object",
              "id": "multicamproject_work_id", "work_bakes": "multicamproject_work_bakes",
              "save_as": "multicamproject_work_save_as"}


def _world(o):
    """o's world matrix from its parent chain - no scene evaluation (a freshly loaded
    object's matrix_world is stale; a view layer update would evaluate every modifier of
    the scan, seconds). The add-on's objects have no constraints."""
    m = o.matrix_basis.copy()
    while o.parent is not None:
        m = o.parent.matrix_basis @ o.matrix_parent_inverse @ m
        o = o.parent
    return m


def _mute_pending(obj):
    if obj is None:
        return
    try:
        wf = importlib.import_module(f"{settings['addon']}.modules.remesh.workflow")
        if wf.decimate_pending(obj):
            wf.set_gn(obj, False)
    except Exception as e:
        print(f"[MultiCamProject] work window: GN mute skipped: {e}")


def _apply_world(objs):
    """Unparent, keeping where everything is: meshes get their world transform applied
    (one mesh copy each), cameras keep their world matrix (their photos don't move)."""
    world = {o: _world(o) for o in objs}
    for o in objs:
        o.parent = None
        if o.type == 'MESH':
            if o.data.users > 1:
                o.data = o.data.copy()
            o.data.transform(world[o])
            if world[o].determinant() < 0:
                o.data.flip_normals()
            o.matrix_world = Matrix.Identity(4)
        else:
            o.matrix_world = world[o]


def _scene_settings(scene):
    for group, vals in settings["scene"].items():
        pg = getattr(scene, group, None)
        if pg is None:
            continue
        flags = {p.identifier for p in pg.bl_rna.properties
                 if p.type == 'ENUM' and p.is_enum_flag}
        for k, v in vals.items():
            try:
                setattr(pg, k, set(v) if k in flags else v)
            except (AttributeError, TypeError, ValueError):
                pass


CAMERA_COLLECTION = "Cameras"
WIREFRAME_OPACITY = 0.262


def _camera_collection(scene):
    """Every camera into one "Cameras" collection (not the scene's top level), excluded
    from the view layer so no camera is in the way - as in the main file, the projection
    and Solo work with it excluded."""
    cams = [o for o in scene.collection.objects if o.type == 'CAMERA']
    if not cams:
        return
    coll = bpy.data.collections.new(CAMERA_COLLECTION)
    scene.collection.children.link(coll)
    for o in cams:
        coll.objects.link(o)
        scene.collection.objects.unlink(o)
    for vl in scene.view_layers:
        lc = vl.layer_collection.children.get(coll.name)
        if lc is not None:
            lc.exclude = True


def _top_left(screen):
    """The screen's top-left editor (not the top bar / status bar)."""
    areas = [a for a in screen.areas if a.type not in {'TOPBAR', 'STATUSBAR'}]
    return min(areas, key=lambda a: (a.x, -(a.y + a.height))) if areas else None


ADDON_TAB = "MultiCamProject"      # the sidebar tab the window opens on
_tab_tries = [0]


def _input(mod, name):
    """A GN modifier's input value by its name (False when it has none)."""
    for item in mod.node_group.interface.items_tree if mod.node_group else ():
        if getattr(item, "in_out", "") == 'INPUT' and item.name == name:
            try:
                return mod[item.identifier]
            except KeyError:
                return False
    return False


def _addon_tab():
    """The sidebar on the add-on's tab: a region knows its tabs only once drawn, so this
    retries a few times after the window is up."""
    _tab_tries[0] += 1
    done = True
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type == 'UI':
                    try:
                        region.active_panel_category = ADDON_TAB
                    except (TypeError, AttributeError):
                        pass
                    done = done and region.active_panel_category == ADDON_TAB
                    region.tag_redraw()
    return None if done or _tab_tries[0] >= 20 else 0.25


def _layout():
    """The work window's view: a UV Editor top left (unless it is the main 3D View),
    statistics, wireframe, face orientation and the sidebar on MultiCamProject in every
    3D View, and the Outliners folded
    on the cameras (Python can't fold one item: their camera filter does it)."""
    for win in bpy.context.window_manager.windows:
        screen = win.screen
        tl = _top_left(screen)
        views = [a for a in screen.areas if a.type == 'VIEW_3D']
        if tl is not None and not (tl.type == 'VIEW_3D' and len(views) == 1):
            tl.type = 'IMAGE_EDITOR'
            tl.ui_type = 'UV'
        # the sidebar on the add-on's tab. show_region_ui's update needs the window in the
        # context - set from a timer without it, Blender 5.2 crashes (no active window)
        for area in views:
            with bpy.context.temp_override(window=win, screen=screen, area=area):
                area.spaces.active.show_region_ui = True
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    ov = space.overlay
                    ov.show_stats = True
                    ov.show_wireframes = True
                    ov.wireframe_threshold = 1.0
                    ov.wireframe_opacity = WIREFRAME_OPACITY
                    ov.show_face_orientation = True
                elif space.type == 'OUTLINER':
                    space.use_filter_object_camera = False


def _build():
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    scene.name = "MCP Work"
    lib = settings["lib"]
    with bpy.data.libraries.load(lib, link=False) as (src, dst):
        dst.objects = list(src.objects)
    try:
        os.remove(lib)
    except OSError:
        pass
    objs = [o for o in dst.objects if o is not None]
    wanted = set(settings["keep"])
    keep = [o for o in objs if o.name in wanted and o.type in {'MESH', 'CAMERA'}]
    for o in keep:              # straight into the scene (their collections are not)
        if scene.collection.objects.get(o.name) is None:
            scene.collection.objects.link(o)
    obj = bpy.data.objects.get(settings["object"])
    _mute_pending(obj)          # before anything evaluates: an undecided Decimate's GN is off
    _apply_world(keep)
    # the `transform` empty, other parents and anything else that came along: not needed
    bpy.data.batch_remove([o for o in objs if o not in keep])
    bpy.data.batch_remove(list(bpy.data.collections))
    bpy.data.orphans_purge(do_recursive=True)
    for k, key in SCENE_KEYS.items():
        scene[key] = settings.get(k, "")
    _scene_settings(scene)
    r = scene.render
    r.resolution_x, r.resolution_y, r.pixel_aspect_x, r.pixel_aspect_y = settings["render"]
    scene.unit_settings.system = settings["unit_system"]
    scene.unit_settings.scale_length = settings["unit_scale"]
    s = getattr(scene, "multicamproject_bake_settings", None)
    if s is not None:           # every bake here goes to the hand-off folder, never the real one
        s.output_dir = os.path.join(settings["bakes"], "")
    _camera_collection(scene)
    orig = bpy.data.objects.get(settings["original"]) if settings["original"] else None
    if orig is not None and bpy.context.view_layer.objects.get(orig.name) is not None:
        orig.hide_set(True)     # as in the main file: the low poly is what you work on
    if obj is not None:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:                    # always open on the Processing material (painting, 04-06)
            gn_final = importlib.import_module(f"{settings['addon']}.modules.baking.gn_final")
            mod = gn_final.get_modifier(obj)
            # only when it shows Final: set_final rebuilds the wrappers with a full scene
            # evaluation each (~5 s on a scan) - for nothing when it is on Processing already
            if mod is not None and _input(mod, "Final"):
                gn_final.set_final(obj, scene, False)
        except Exception as e:  # never stop the window over the view
            print(f"[MultiCamProject] work window: Processing view skipped: {e}")
    try:
        _layout()
    except Exception as e:      # never stop the window over its layout
        print(f"[MultiCamProject] work window: layout skipped: {e}")
    _mute_pending(obj)          # (set_final may have switched GN on again)
    if obj is not None:         # last: its evaluation is the one the first draw uses too
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    region = next(r for r in area.regions if r.type == 'WINDOW')
                    with bpy.context.temp_override(window=win, area=area, region=region):
                        bpy.ops.view3d.view_selected()
    # the 0C / 0D buttons' file is not written now (~5 s for a scan): Ctrl+S / Save saves
    # it there (scene key save_as, core.save_target)
    _tab_tries[0] = 0           # the sidebar tab - also when the layout failed
    bpy.app.timers.register(_addon_tab, first_interval=0.3)
    if obj is not None and settings.get("start"):
        _start(settings["start"])
    return None


def _start(start):
    """Remesh / Retopo + Send Out: the window opens in the PolyCut tool / on the retopo."""
    if start == 'REMESH':     # 0C: the Decimate is decided first (Object Mode, its box)
        return
    op = {'RETOPO': "retopo_focus"}.get(start)
    if op is None:
        return
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next(r for r in area.regions if r.type == 'WINDOW')
            try:
                with bpy.context.temp_override(window=win, area=area, region=region):
                    getattr(bpy.ops.multicamproject, op)()
            except Exception as e:  # never stop the window over the tool
                print(f"[MultiCamProject] work window: {op} skipped: {e}")
            return


bpy.app.timers.register(_build, first_interval=0.2)
