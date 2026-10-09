"""Linking: work windows. An object is sent out into a second Blender with its setup, and
its mesh, painting, cameras and bakes are received back - as often as it is sent back. The
Linking panel lists which work file is linked to which object."""
from . import core, operators, originals, ui


def register():
    core.register()
    operators.register()
    originals.register()
    ui.register()


def unregister():
    ui.unregister()
    originals.unregister()
    operators.unregister()
    core.unregister()
