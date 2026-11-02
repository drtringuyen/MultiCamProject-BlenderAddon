import bpy


def _changed(self, context):
    """Level / Blur Seam: preview again (debounced) - on the previewed object, else the
    active one when a toggle was just switched on."""
    from . import direct
    obj = direct.target()
    if obj is None:
        if not (self.q_level or self.q_blur):
            return
        obj = context.active_object
    if obj is not None:
        direct.schedule(obj.name)


def _tint_changed(self, context):
    from . import direct
    obj = direct.target()
    if obj is not None:
        direct.set_tint(obj, self.q_tint)     # at once - the tint image is already there
        direct.schedule(obj.name)       # the status line names the counts


class MULTICAMPROJECT_PG_seamfix(bpy.types.PropertyGroup):
    q_tint: bpy.props.BoolProperty(
        name="Show Seams", default=False, update=_tint_changed,
        description="Preview: seams Level Seam leaves alone (jump over Max Jump) in red, "
                    "overlapping islands neither tool touches in magenta")
    q_level: bpy.props.BoolProperty(
        name="Level Seam", default=False, update=_changed,
        description="Preview: both sides of every seam meet at their average colour, "
                    "straight on ALB_ (no UV change) - Apply writes it into the file")
    q_blur: bpy.props.BoolProperty(
        name="Blur Seam", default=False, update=_changed,
        description="Preview: each island blurred inside itself next to its seams, "
                    "straight on ALB_ (no UV change) - Apply writes it into the file")
    blur_px: bpy.props.IntProperty(
        name="Radius", default=16, min=1, max=256, subtype='PIXEL', update=_changed,
        description="Blur Seam: how far (texture pixels) from the seam it blurs - and how "
                    "strongly")
    blend_px: bpy.props.IntProperty(
        name="Blend", default=16, min=2, max=256, subtype='PIXEL',
        description="Fix Seam: how far (texture pixels) the source's continuation fades "
                    "into the target side - also the free space each island gets in uv_new")
    level_px: bpy.props.IntProperty(
        name="Falloff", default=128, min=8, max=2048, subtype='PIXEL', update=_changed,
        description="Seam-leveling: how far (texture pixels) the colour correction fades "
                    "into each island")
    max_jump: bpy.props.IntProperty(
        name="Max Jump", default=64, min=1, max=255, update=_changed,
        description="Seam-leveling: seams whose two sides differ by more than this (0-255) "
                    "follow a real colour edge and are left alone")
    shrink: bpy.props.BoolProperty(
        name="Shrink Islands", default=True,
        description="Fix Seam: shrink islands in uv_new until each has the blend width of "
                    "free space around it")
    bake_margin: bpy.props.IntProperty(
        name="Bake Margin", default=16, min=0, max=128, subtype='PIXEL',
        description="Bake: pixels the baked islands are extended by")


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PG_seamfix)
    bpy.types.Scene.multicamproject_seamfix = bpy.props.PointerProperty(
        type=MULTICAMPROJECT_PG_seamfix)


def unregister():
    del bpy.types.Scene.multicamproject_seamfix
    bpy.utils.unregister_class(MULTICAMPROJECT_PG_seamfix)
