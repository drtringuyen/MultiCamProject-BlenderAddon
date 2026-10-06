"""Liquify brush settings and the lasso's hole fill (per scene)."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty)

BRUSHES = [
    ('WARP', "Forward Warp", "Push the photo along the stroke (W)", 'FORWARD', 0),
    ('RECONSTRUCT', "Reconstruct", "Paint the warp back to the original photo (R)", 'LOOP_BACK', 1),
    ('SMOOTH', "Smooth", "Even out the warp under the brush (S)", 'MOD_SMOOTH', 2),
    ('PUCKER', "Pucker", "Pull the photo toward the brush centre - hold to keep going. "
                         "Alt: Bloat (P)", 'FULLSCREEN_EXIT', 3),
    ('BLOAT', "Bloat", "Push the photo away from the brush centre - hold to keep going. "
                       "Alt: Pucker (B)", 'FULLSCREEN_ENTER', 4),
]


class MULTICAMPROJECT_LiquifySettings(bpy.types.PropertyGroup):
    brush: EnumProperty(name="Brush", items=BRUSHES, default='WARP')
    size: IntProperty(
        name="Size", default=120, min=4, max=2048, subtype='PIXEL',
        description="Brush diameter in preview pixels ([ and ] to change, F to drag)")
    strength: FloatProperty(
        name="Strength", default=0.7, min=0.01, max=1.0, subtype='FACTOR',
        description="How strongly each dab warps")
    use_pressure: BoolProperty(
        name="Pressure", default=True,
        description="Tablet pen pressure scales the strength")
    preview_res: EnumProperty(
        name="Preview", default='1024',
        items=[('1024', "1K", "Paint on a 1024 px preview: fastest"),
               ('2048', "2K", "Paint on a 2048 px preview: sharper on the camera background "
                              "and the mesh; big brushes cost about 4x more"),
               ('FULL', "Original", "Paint on the photo at its own size: exact, slowest "
                                    "(brush cost grows with the square of the scale)")],
        description="Long side of the preview Liquify paints on (taken at Start; "
                    "Bake always writes the original size)")


class MULTICAMPROJECT_LassoSettings(bpy.types.PropertyGroup):
    fill: EnumProperty(
        name="Hole", default='AUTO',
        items=[('AUTO', "Auto", "Colour for photos without alpha (JPG, RGB PNG), "
                                "transparent for images with alpha"),
               ('COLOR', "Colour", "Fill the hole a cut leaves with the colour"),
               ('TRANSPARENT', "Transparent", "Leave a transparent hole (alpha 0)")],
        description="What a lasso cut leaves where the pixels were lifted")
    color: FloatVectorProperty(
        name="Colour", subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0),
        description="Colour that fills the hole of a cut (the eyedropper picks it from the photo)")


def settings(context):
    return context.scene.multicamproject_liquify


def lasso_settings(context=None):
    return (context or bpy.context).scene.multicamproject_lasso


def preview_long_side(context=None):
    """Long side of the preview; field.preview_size never upscales, so a huge value means
    the photo's own size."""
    res = settings(context or bpy.context).preview_res
    return 1 << 20 if res == 'FULL' else int(res)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_LiquifySettings)
    bpy.utils.register_class(MULTICAMPROJECT_LassoSettings)
    bpy.types.Scene.multicamproject_liquify = PointerProperty(type=MULTICAMPROJECT_LiquifySettings)
    bpy.types.Scene.multicamproject_lasso = PointerProperty(type=MULTICAMPROJECT_LassoSettings)


def unregister():
    del bpy.types.Scene.multicamproject_lasso
    del bpy.types.Scene.multicamproject_liquify
    bpy.utils.unregister_class(MULTICAMPROJECT_LassoSettings)
    bpy.utils.unregister_class(MULTICAMPROJECT_LiquifySettings)
