"""The add-on's own icons: icons/<name>.png, loaded on first use into one preview collection.

They are Blender's toolbar icons (bucket, eraser, lasso, the Density brush) drawn as plain
square images with even margins: a toolbar icon itself sits off centre and clipped in a
normal button, while a preview icon is centred like any other."""
import os

import bpy

_previews = None
FOLDER = os.path.join(os.path.dirname(__file__), "icons")


def icon_id(name):
    """icon_value of icons/<name>.png (0 when the file is missing)."""
    global _previews
    import bpy.utils.previews
    if _previews is None:
        _previews = bpy.utils.previews.new()
    if name not in _previews:
        path = os.path.join(FOLDER, name + ".png")
        if not os.path.isfile(path):
            return 0
        _previews.load(name, path, 'IMAGE')
    return _previews[name].icon_id


def kw(name, fallback='QUESTION'):
    """Keyword for layout.operator / prop: the add-on icon, else a stock one."""
    value = icon_id(name)
    return {"icon_value": value} if value else {"icon": fallback}


def free():
    global _previews
    if _previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews)
        _previews = None
