"""Export: the active scene's EXPORT collection as FBX for Unity (URP), with a status
list, checks and fixes. Needs the baking module."""
from . import live, props, operators, split, ui


def register():
    props.register()
    operators.register()
    split.register()
    ui.register()
    live.register()


def unregister():
    live.unregister()
    ui.unregister()
    split.unregister()
    operators.unregister()
    props.unregister()
