"""Run by workfile.make_work_file in a background Blender (never imported): the collection
written from the main file goes into an empty scene, which is saved as the work file.

blender -b --factory-startup --python workfile_build.py -- <lib> <out> <json settings>
(A Scene cannot be written with bpy.data.libraries.write: Blender 5.2 crashes.)
"""
import json
import sys

import bpy

lib, out, settings = sys.argv[sys.argv.index("--") + 1:][:3]
settings = json.loads(settings)

bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene
scene.name = "MCP Work"
with bpy.data.libraries.load(lib, link=False) as (src, dst):
    dst.collections = [settings["collection"]]
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
active = bpy.data.objects.get(settings["names"].get(settings["active"], settings["active"]))
if active is not None:
    bpy.context.view_layer.objects.active = active
    active.select_set(True)
bpy.ops.wm.save_as_mainfile(filepath=out)
