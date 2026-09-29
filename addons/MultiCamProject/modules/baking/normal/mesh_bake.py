"""Normal map baked from a high-poly mesh (Cycles NORMAL, tangent space, Selected to
Active). An object baked onto itself gives flat normals - the source must be another mesh:
the object's Bake Source (the Remesh original or a picked mesh), else the scene's hp_object."""
from contextlib import contextmanager

import bpy
import numpy as np

from .. import common, engine

TMP_IMAGE = "MCP_NOR_BAKE_TMP"


def source(obj, s):
    """The high poly for `obj`: its Bake Source first, then the scene's High Poly."""
    return common.data(obj).bake_source or s.hp_object


@contextmanager
def visible(context, src):
    """The source shown for the bake (the Remesh original is kept hidden)."""
    hidden = src.hide_get(view_layer=context.view_layer)
    hv, hr = src.hide_viewport, src.hide_render
    src.hide_set(False, view_layer=context.view_layer)
    src.hide_viewport = src.hide_render = False
    try:
        yield
    finally:
        src.hide_viewport, src.hide_render = hv, hr
        src.hide_set(hidden, view_layer=context.view_layer)


@contextmanager
def smoothed_source(context, src, s):
    """The high poly, or a temporary smoothed copy of it (deleted afterwards)."""
    if not s.smooth_source:
        yield src
        return
    tmp = src.copy()
    tmp.data = src.data.copy()
    tmp.name = src.name + "_MCP_SMOOTH_TMP"
    context.scene.collection.objects.link(tmp)
    m = tmp.modifiers.new("MCP_Smooth", 'CORRECTIVE_SMOOTH')
    m.iterations = s.smooth_iterations
    m.smooth_type = 'SIMPLE'
    m.use_only_smooth = True
    m.rest_source = 'ORCO'
    try:
        yield tmp
    finally:
        me = tmp.data
        bpy.data.objects.remove(tmp)
        if me.users == 0:
            bpy.data.meshes.remove(me)


def problem(obj, s):
    """Why the mesh bake cannot run for `obj` ('' = it can)."""
    src = source(obj, s)
    if src is None:
        return "Pick a High Poly mesh (an object baked onto itself gives flat normals)"
    if src == obj:
        return "High Poly is the object itself - that gives flat normals"
    if bpy.context.view_layer.objects.get(src.name) != src:
        return f"'{src.name}' is not in the view layer (collection excluded?)"
    return ""


def generate(context, obj, size):
    """Normal map RGB (size, size, 3), rows bottom-up."""
    s = common.settings(context.scene)
    img = bpy.data.images.new(TMP_IMAGE, size, size, alpha=False, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    try:
        hp = source(obj, s)
        with visible(context, hp), smoothed_source(context, hp, s) as src:
            engine.bake_normal_from_mesh(context, obj, src, img)
        buf = np.empty(size * size * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        return buf.reshape(size, size, 4)[:, :, :3].copy()
    finally:
        bpy.data.images.remove(img)
