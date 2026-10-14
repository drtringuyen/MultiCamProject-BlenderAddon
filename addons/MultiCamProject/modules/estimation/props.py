"""Estimation settings and the last Calculate's rows - on the Scene, so they belong to the
file. The rows are a snapshot: Calculate again after the meshes change."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def _row_color(self):
    """The row's colour block: its state as the panel last drew it (read-only)."""
    from . import core
    return core.COLORS[core.STATE.get(self.name, core.NEW)]


def _total_color(self):
    """The total line's colour block: the budget's state as the panel last drew it."""
    from . import core
    return core.COLORS[core.TOTAL_STATE[0]]


def _on_row(self, context):
    """A click on a row of the list focuses its object."""
    from . import core
    if 0 <= self.active_index < len(self.rows):
        core.focus(context, self.rows[self.active_index].name)
        core.fill_new_name(context, self.rows[self.active_index].name)


def _budget_color(self):
    """A work window's Send Back blocks: green within the budget, orange over it."""
    from . import core
    b = core.window_budget(bpy.context)
    if b is None:
        return core.COLORS[core.NEW]
    return core.COLORS[core.DONE if b[1] <= b[0] else core.OVER]


class MULTICAMPROJECT_EstimationRow(bpy.types.PropertyGroup):
    # name = the object's name
    area: FloatProperty()           # world space, m²
    budget: IntProperty()           # triangles assigned by area
    tris: IntProperty()             # triangles now (as shown: modifiers + GN)
    room: BoolProperty()            # from ROOM: listed with its stage, not part of the budget
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
    rows: CollectionProperty(type=MULTICAMPROJECT_EstimationRow)
    active_index: IntProperty(default=-1, update=_on_row)
    collection_name: StringProperty()   # the OBJECTS collection of the last Calculate
    calculated_budget: IntProperty()    # the Budget the rows were split with
    sort_by: EnumProperty(              # the list's sort column (a header click)
        items=[('STATE', "State", ""), ('NAME', "Name", ""), ('AREA', "Area", ""),
               ('BUDGET', "Budget", ""), ('NOW', "Now", ""), ('TOGO', "To Go", ""),
               ('STAGE', "Stage", "")], default='NAME')
    sort_reverse: BoolProperty()
    color: FloatVectorProperty(
        size=3, subtype='COLOR_GAMMA', get=_total_color,
        description="Green: all the objects together are within the Budget · Orange: over it")


CLASSES = (MULTICAMPROJECT_EstimationRow, MULTICAMPROJECT_EstimationSettings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.multicamproject_estimation = PointerProperty(
        type=MULTICAMPROJECT_EstimationSettings)
    bpy.types.Scene.multicamproject_budget_color = FloatVectorProperty(
        size=3, subtype='COLOR_GAMMA', get=_budget_color, options={'SKIP_SAVE'},
        description="Green: the object is within its budget · Orange: over it")


def unregister():
    del bpy.types.Scene.multicamproject_budget_color
    del bpy.types.Scene.multicamproject_estimation
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
