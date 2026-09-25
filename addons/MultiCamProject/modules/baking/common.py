"""Names and small lookups shared by the baking and export modules."""
import os

import bpy

UV_NORMAL = "uv_normal"
COLOR = "Color"
CP_MOD = "GN-CameraProject"


def data(obj):
    return obj.multicamproject_bake


def settings(scene):
    return scene.multicamproject_bake_settings


def alb_name(obj):
    from .naming import typed_name
    return typed_name("ALB", obj)


def nor_name(obj):
    from .naming import typed_name
    return typed_name("NOR", obj)


def mat_name(obj):
    from .naming import typed_name
    return typed_name("MAT", obj)


def has_uv_normal(obj):
    return obj.type == 'MESH' and obj.data.uv_layers.get(UV_NORMAL) is not None


def cp_modifier(obj):
    """The camera projection modifier, or None (baking works on any mesh)."""
    from ..camera_project import gn_builder
    mod = obj.modifiers.get(CP_MOD)
    if mod and mod.type == 'NODES':
        return mod
    return next((m for m in obj.modifiers if m.type == 'NODES' and gn_builder.is_main(m.node_group)),
                None)


def mesh_objects(objs):
    return [o for o in objs if o.type == 'MESH']


def output_dir(scene):
    return bpy.path.abspath(settings(scene).output_dir)


def texture_path(scene, image_name):
    return os.path.join(output_dir(scene), image_name + ".png")


def image_file(img):
    """Absolute path of an image's file ('' when it has none). Never loads pixels."""
    if img is None or img.source != 'FILE' or not img.filepath:
        return ""
    return bpy.path.abspath(img.filepath, library=img.library)


def file_ok(img):
    path = image_file(img)
    return bool(path) and os.path.isfile(path)


def selected_meshes(context):
    return [o for o in context.selected_objects if o.type == 'MESH']


EXPORT = "EXPORT"


def _in_scene(coll, root):
    return coll == root or any(_in_scene(coll, c) for c in root.children)


def export_collection(scene):
    """The EXPORT collection, only when it is part of `scene`."""
    coll = bpy.data.collections.get(EXPORT)
    if coll is None or not _in_scene(coll, scene.collection):
        return None
    return coll


def export_objects(scene):
    """Meshes of the active scene's EXPORT collection (sub-collections included)."""
    coll = export_collection(scene)
    if coll is None:
        return []
    return [o for o in coll.all_objects if o.type == 'MESH' and scene.objects.get(o.name) == o]
