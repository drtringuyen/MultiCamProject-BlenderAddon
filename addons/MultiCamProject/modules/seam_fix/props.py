import bpy


class MULTICAMPROJECT_PG_seamfix(bpy.types.PropertyGroup):
    blend_px: bpy.props.IntProperty(
        name="Blend", default=16, min=2, max=256, subtype='PIXEL',
        description="Fix Seam: how far (texture pixels) the source's continuation fades "
                    "into the target side - also the free space each island gets in uv_new")
    level_px: bpy.props.IntProperty(
        name="Falloff", default=128, min=8, max=2048, subtype='PIXEL',
        description="Seam-leveling: how far (texture pixels) the colour correction fades "
                    "into each island")
    max_jump: bpy.props.IntProperty(
        name="Max Jump", default=64, min=1, max=255,
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
