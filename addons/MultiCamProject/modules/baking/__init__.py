"""Baking: the camera projection (plus the scan) into ALB_/NOR_ on the user's uv_normal,
the MAT_ material, and GN-Final to flip between the projection and the baked result.
Works without camera_project: any mesh with uv_normal can be baked."""
from . import cache, props, operators, ui


def register():
    props.register()
    operators.register()
    ui.register()
    cache.register()


def unregister():
    cache.unregister()
    ui.unregister()
    operators.unregister()
    props.unregister()
