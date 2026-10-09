"""Estimation: a triangle budget for the OBJECTS collection, split by surface area. Lists
each object's world-space area, its share of the budget, its current triangle count and how
much of it can be removed (= 1 - the Decimate ratio that reaches the share)."""
from . import props, operators, ui


def register():
    props.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    props.unregister()
