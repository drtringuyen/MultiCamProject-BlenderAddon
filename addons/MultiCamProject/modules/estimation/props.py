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


AUTO_STEPS = (
    ('SKIP', "Skip", "Not part of Automate - stays Raw"),
    ('REMESH', "2B Remesh", "A low poly copy of the scan (its Decimate is decided when you "
                            "open the work file), named ENV_<prefix>.<##>_<Name>"),
    ('RETOPO', "2C Retopo", "A new object started from the 'From' mesh (a ROOM_Template "
                            "part) - or empty - on the scan, named ENV_<prefix>.<##>_<Name>"),
    ('PROJECT', "2D Only Project", "The camera projection on the scan itself (it keeps its "
                                   "name)"),
)


def _from_poll(self, obj):
    return obj.type == 'MESH' and obj.name != self.name and not obj.library


def _changed(self, context):
    from ... import steps
    steps.forget()          # the memoized table / checks


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
    # Automate (picked in the Raw cell's popup): what 02 does to the row's scan
    auto_step: EnumProperty(name="Step", items=AUTO_STEPS, default='SKIP', update=_changed)
    auto_name: StringProperty(
        name="Name", update=_changed,
        description="The new object's name: ENV_<prefix>.<next ##>_<Name>; its work file is "
                    "Bake Folder/<Name>.blend")
    auto_from: PointerProperty(
        name="From", type=bpy.types.Object, poll=_from_poll, update=_changed,
        description="2C starts from this mesh (copied in, its UV as uv_normal) - empty: an "
                    "empty retopo. Picked from ROOM_Template by where the scan lies")
    auto_local: BoolProperty(
        name="Keep Local", default=False, update=_changed,
        description="Made here and worked on in this file: no work file is built or linked")
    auto_share: FloatProperty()     # how much of the scan lies on auto_from (the match)
    auto_filled: BoolProperty()     # the defaults were filled once


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
    # Automate's dialog: what runs after the 02 steps, in the same job
    auto_fix: BoolProperty(
        name="Fix", default=True,
        description="Then Fix every object Automate kept in this file (Keep Local): rename, "
                    "transform, uv_normal, bake - as its row's Fix button. Objects sent to a "
                    "work file are fixed when they come back")
    auto_export: BoolProperty(
        name="Export FBX", default=True,
        description="Then Fix + Export FBX, as the Export button (objects linked to a work "
                    "file are not fixed or baked here)")
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
