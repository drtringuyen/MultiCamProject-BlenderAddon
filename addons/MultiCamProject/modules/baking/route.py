"""The Bake Route of each object - where 06 Bake Final takes ALB_ / NOR_ from:

    ORIGINAL    BAo_ / BNo_ baked from the Bake Source (Cycles); ALB_ / NOR_ = their copies
    PROJECTION  BAp_ rendered from the projection (EEVEE), BNp_ generated from it;
                ALB_ / NOR_ = their copies
    MIXED       VCMix / VCMix2 alpha blends BAo_ -> projection and BNo_ -> BNp_

A Setup step picks it (0B -> Projection, 0C -> Original, 0D -> Projection); when a second
workflow is added (or after 0D) 06 shows a prompt to pick. Mixed is only ever picked by
hand. MCP_ holds all three: two Value nodes switch it (camera_project.core.set_route)."""
from contextlib import contextmanager

import bpy

from . import common

ORIGINAL, PROJECTION, MIXED = 'ORIGINAL', 'PROJECTION', 'MIXED'
LABELS = {ORIGINAL: "From Original", PROJECTION: "From Projection", MIXED: "Mixed"}

_auto = [False]         # set by the add-on (not the user's pick)


@contextmanager
def automatic():
    _auto[0] = True
    try:
        yield
    finally:
        _auto[0] = False


def has_projection(obj):
    cam = getattr(obj, "multicamproject_cam", None)
    return bool(cam is not None and cam.is_setup and common.cp_modifier(obj) is not None)


def has_original(obj):
    return common.data(obj).bake_source is not None


def get(obj):
    return common.data(obj).route


def uses_original(obj):
    return get(obj) in {ORIGINAL, MIXED}


def uses_projection(obj):
    return get(obj) in {PROJECTION, MIXED}


def suggest(obj):
    """The route the object's workflows ask for (never Mixed)."""
    if common.data(obj).retopo:
        return PROJECTION
    if has_original(obj) and not has_projection(obj):
        return ORIGINAL
    return PROJECTION


def problem(obj):
    """Why the object's route cannot bake ('' = it can)."""
    r = get(obj)
    need = []
    if r in {ORIGINAL, MIXED} and not has_original(obj):
        need.append("a Bake Source (0C / 0D, or pick the original)")
    if r in {PROJECTION, MIXED} and not has_projection(obj):
        need.append("a camera projection (0B)")
    return f"{LABELS[r]} needs {' and '.join(need)}" if need else ""


def set_route(obj, value, user=False):
    """Set the route (writes MCP_'s switch through the update). `user`: picked by hand."""
    d = common.data(obj)
    if user:
        d.route = value
        d.route_user = True
        d.route_prompt = ""
        return
    with automatic():
        if d.route != value:
            d.route = value
        else:
            changed(obj)


def changed(obj):
    """route's update: MCP_ follows; a pick by hand clears the prompt."""
    d = common.data(obj)
    if not _auto[0]:
        d.route_user = True
        d.route_prompt = ""
    try:
        from ..camera_project import core as cp
        cp.set_route(obj)
    except (ImportError, AttributeError):
        pass
    try:
        from . import cache
        cache.clear()
    except ImportError:
        pass


def after_step(obj, step):
    """A Setup step ran on `obj` ('PROJECTION' 0A/0B, 'ORIGINAL' 0C / Use Existing,
    'RETOPO' 0D): the first workflow sets the route, a second one asks. 0D sets From
    Original (bakes from the scan it was modelled on) without asking."""
    if obj is None or obj.type != 'MESH':
        return
    d = common.data(obj)
    if d.handmade:
        return
    both = has_projection(obj) and has_original(obj)
    if step == 'RETOPO':
        set_route(obj, ORIGINAL)
        d.route_prompt = ""
    elif both:                  # a second workflow: the route stays, the user is asked
        set_route(obj, d.route)
        d.route_prompt = (f"{'0B Projection' if step == 'PROJECTION' else '0C Original'} added - "
                          f"{LABELS[d.route]} kept")
    elif not d.route_user or problem(obj):
        set_route(obj, suggest(obj))


def draw_prompt(layout, obj):
    """The prompt row of 06: the reason and one button per route."""
    d = common.data(obj)
    box = layout.box().column(align=True)
    box.alert = True
    box.label(text=d.route_prompt, icon='QUESTION')
    row = box.row(align=True)
    box.alert = False
    for key in (ORIGINAL, PROJECTION, MIXED):
        op = row.operator("multicamproject.set_route", text=LABELS[key],
                          depress=d.route == key)
        op.route = key
    row.operator("multicamproject.set_route", text="", icon='X').route = 'KEEP'


class MULTICAMPROJECT_OT_SetRoute(bpy.types.Operator):
    """Bake Final takes ALB_ / NOR_ from this route (the active object and the selected ones)"""
    bl_idname = "multicamproject.set_route"
    bl_label = "Bake Route"
    bl_options = {'REGISTER', 'UNDO'}

    route: bpy.props.EnumProperty(items=(
        (ORIGINAL, "From Original", ""), (PROJECTION, "From Projection", ""), (MIXED, "Mixed", ""),
        ('KEEP', "Keep", "Keep the route as it is")))

    @classmethod
    def description(cls, context, props):
        if props.route == 'KEEP':
            return "Keep the route as it is (hides this prompt)"
        from .props import ROUTE_ITEMS
        return next(desc for key, _l, desc, *_r in ROUTE_ITEMS if key == props.route)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        objs = common.selected_meshes(context)
        if context.active_object not in objs:
            objs.append(context.active_object)
        for o in objs:
            d = common.data(o)
            if d.handmade:
                continue
            if self.route == 'KEEP':
                d.route_prompt = ""
                d.route_user = True
            else:
                set_route(o, self.route, user=True)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(MULTICAMPROJECT_OT_SetRoute)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_SetRoute)
