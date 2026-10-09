"""Estimation: a triangle budget for the OBJECTS collection, split by surface area. Lists
each object's world-space area, its share of the budget, its current triangle count and
how many triangles are left (+) or to remove (-)."""
from . import props, operators, ui


def register():
    props.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    props.unregister()
