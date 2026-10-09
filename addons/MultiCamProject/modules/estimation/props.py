"""Estimation settings and the last Calculate's rows - on the Scene, so they belong to the
file. The rows are a snapshot: Calculate again after the meshes change."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)


def _row_color(self):
    """The row's colour block: its state as the panel last drew it (read-only)."""
    from . import core
    return core.COLORS[core.STATE.get(self.name, core.NEW)]


def _redraw(self, context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class MULTICAMPROJECT_EstimationRow(bpy.types.PropertyGroup):
    # name = the object's name
    area: FloatProperty()           # world space, m²
    budget: IntProperty()           # triangles assigned by area
    tris: IntProperty()             # triangles now (as shown: modifiers + GN)
    color: FloatVectorProperty(
        size=3, subtype='COLOR_GAMMA', get=_row_color,
        description="Green: in EXPORT and within its budget · Orange: in EXPORT, over its "
                    "budget · Black: not worked on yet")


class MULTICAMPROJECT_EstimationSettings(bpy.types.PropertyGroup):
    budget: IntProperty(
        name="Budget", default=150000, min=1, soft_max=1000000,
        description="Triangles for all the objects of the OBJECTS collection together "
                    "(the walls in ROOM are not part of it)")
    minimum: IntProperty(
        name="Minimum", default=200, min=0, soft_max=5000,
        description="No object gets fewer triangles than this, however small its area. "
                    "The rest of the budget is split by area among the others")
    show_overlay: BoolProperty(
        name="Show in Viewport", default=False, update=_redraw,
        description="The list as a coloured table in the 3D Viewport's corner (green: on "
                    "budget, orange: over, black: not worked on)")
    rows: CollectionProperty(type=MULTICAMPROJECT_EstimationRow)
    collection_name: StringProperty()   # the OBJECTS collection of the last Calculate
    calculated_budget: IntProperty()    # the Budget the rows were split with


CLASSES = (MULTICAMPROJECT_EstimationRow, MULTICAMPROJECT_EstimationSettings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.multicamproject_estimation = PointerProperty(
        type=MULTICAMPROJECT_EstimationSettings)


def unregister():
    del bpy.types.Scene.multicamproject_estimation
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
