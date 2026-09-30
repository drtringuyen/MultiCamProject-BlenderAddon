"""Helpers for baking from the Bake Source (engine.bake_from_source): show the hidden
Remesh original for the bake, optionally smooth a temporary copy of it."""
from contextlib import contextmanager

import bpy


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
