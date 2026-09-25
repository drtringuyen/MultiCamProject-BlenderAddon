"""Normal map baked from a high-poly mesh (Cycles NORMAL, tangent space, Selected to
Active). An object baked onto itself gives flat normals - hp_object must be another mesh."""
from contextlib import contextmanager

import bpy
import numpy as np

from .. import common, engine

TMP_IMAGE = "MCP_NOR_BAKE_TMP"


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
    if s.hp_object is None:
        return "Pick a High Poly mesh (an object baked onto itself gives flat normals)"
    if s.hp_object == obj:
        return "High Poly is the object itself - that gives flat normals"
    return ""


def generate(context, obj, size):
    """Normal map RGB (size, size, 3), rows bottom-up."""
    s = common.settings(context.scene)
    img = bpy.data.images.new(TMP_IMAGE, size, size, alpha=False, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    try:
        with smoothed_source(context, s.hp_object, s) as src:
            engine.bake_normal_from_mesh(context, obj, src, img)
        buf = np.empty(size * size * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        return buf.reshape(size, size, 4)[:, :, :3].copy()
    finally:
        bpy.data.images.remove(img)
