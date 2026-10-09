"""Estimation: a triangle budget for the OBJECTS collection, split by surface area. Lists
each object's world-space area, its share of the budget, its current triangle count and
how many triangles are left (+) or to remove (-). Show in Viewport: the same list as a
coloured table in the 3D Viewport (overlay.py)."""
from . import props, operators, overlay, ui


def register():
    props.register()
    operators.register()
    overlay.register()
    ui.register()


def unregister():
    ui.unregister()
    overlay.unregister()
    operators.unregister()
    props.unregister()
