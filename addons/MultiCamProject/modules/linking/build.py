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

# core.WORK_KEY / WORK_OBJECT_KEY / WORK_ID_KEY / WORK_BAKES_KEY (this script runs outside the add-on)
SCENE_KEYS = {"main": "multicamproject_work_of", "object": "multicamproject_work_object",
              "id": "multicamproject_work_id", "work_bakes": "multicamproject_work_bakes"}


def _apply_world(objs):
    """Unparent, keeping where everything is: meshes get their world transform applied
    (one mesh copy each), cameras keep their world matrix (their photos don't move)."""
    world = {o: o.matrix_world.copy() for o in objs}
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
    bpy.context.view_layer.update()
    _apply_world(keep)
    # the `transform` empty, other parents and anything else that came along: not needed
    bpy.data.batch_remove([o for o in objs if o not in keep])
    bpy.data.batch_remove(list(bpy.data.collections))
    bpy.data.orphans_purge(do_recursive=True)
    for k, key in SCENE_KEYS.items():
        scene[key] = settings[k]
    _scene_settings(scene)
    r = scene.render
    r.resolution_x, r.resolution_y, r.pixel_aspect_x, r.pixel_aspect_y = settings["render"]
    scene.unit_settings.system = settings["unit_system"]
    scene.unit_settings.scale_length = settings["unit_scale"]
    s = getattr(scene, "multicamproject_bake_settings", None)
    if s is not None:           # every bake here goes to the hand-off folder, never the real one
        s.output_dir = os.path.join(settings["bakes"], "")
    orig = bpy.data.objects.get(settings["original"]) if settings["original"] else None
    if orig is not None and bpy.context.view_layer.objects.get(orig.name) is not None:
        orig.hide_set(True)     # as in the main file: the low poly is what you work on
    obj = bpy.data.objects.get(settings["object"])
    if obj is not None:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:                    # always open on the Processing material (painting, 04-06)
            gn_final = importlib.import_module(f"{settings['addon']}.modules.baking.gn_final")
            gn_final.set_final(obj, scene, False)
        except Exception as e:  # never stop the window over the view
            print(f"[MultiCamProject] work window: Processing view skipped: {e}")
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    region = next(r for r in area.regions if r.type == 'WINDOW')
                    with bpy.context.temp_override(window=win, area=area, region=region):
                        bpy.ops.view3d.view_selected()
    return None


bpy.app.timers.register(_build, first_interval=0.2)
