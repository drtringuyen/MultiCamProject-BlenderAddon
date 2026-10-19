"""uvMesh: Edit Mode modelling that keeps uv_normal unfoldable - uvTube (edges to a tube),
uvExtrude (an extrusion with its seams) and Close Hole (faces to a flat triangulated cap).
Its buttons sit in 03. Mesh Editing (remesh/ui.py)."""
from . import operators, tube, ui


def register():
    operators.register()
    tube.register()
    ui.register()


def unregister():
    ui.unregister()
    tube.unregister()
    operators.unregister()
