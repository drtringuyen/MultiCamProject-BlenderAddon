"""Estimation: a triangle budget for the OBJECTS collection, split by surface area. Lists
each object's world-space area, its share of the budget, its current triangle count and
how many triangles are left (+) or to remove (-)."""
from . import props, operators, automate, ui


def register():
    props.register()
    operators.register()
    automate.register()
    ui.register()


def unregister():
    ui.unregister()
    automate.unregister()
    operators.unregister()
    props.unregister()
