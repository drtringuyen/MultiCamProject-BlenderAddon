"""Run by workfile.open_work_window in a new Blender (never imported): the collection
written by the main file goes into an empty scene, which stays untitled - nothing saves it.

blender --python workfile_build.py -- <json settings>
(A Scene cannot be written with bpy.data.libraries.write: Blender 5.2 crashes.)
"""
import json
import os
import sys

import bpy

settings = json.loads(sys.argv[sys.argv.index("--") + 1])


def _build():
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    scene.name = "MCP Work"
    lib = settings["lib"]
    with bpy.data.libraries.load(lib, link=False) as (src, dst):
        dst.collections = [settings["collection"]]
    try:
        os.remove(lib)
    except OSError:
        pass
    coll = dst.collections[0]
    scene.collection.children.link(coll)
    for o in coll.objects:          # names that clashed in the main file get theirs back
        name = settings["names"].get(o.name)
        if name:
            o.name = name
            o.data.name = name
    for key, value in settings["keys"].items():
        scene[key] = value
    scene.unit_settings.system = settings["unit_system"]
    scene.unit_settings.scale_length = settings["unit_scale"]
    s = getattr(scene, "multicamproject_bake_settings", None)
    if s is not None:               # no bake from here may reach the main file's folder
        s.output_dir = os.path.join(bpy.app.tempdir, "mcp_work_bakes", "")
    active = bpy.data.objects.get(settings["names"].get(settings["active"], settings["active"]))
    if active is not None:
        bpy.context.view_layer.objects.active = active
        active.select_set(True)
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next(r for r in area.regions if r.type == 'WINDOW')
                with bpy.context.temp_override(window=win, area=area, region=region):
                    bpy.ops.view3d.view_all()
    return None


bpy.app.timers.register(_build, first_interval=0.2)
