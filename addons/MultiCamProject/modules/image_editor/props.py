"""Liquify brush settings, the lasso's hole fill and the Inpaint tool (per scene)."""
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


def _uv_changed(_self, _context):
    from . import inpaint
    inpaint.apply_uv()


class MULTICAMPROJECT_InpaintSettings(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Tool", default='BRUSH',
        items=[('BRUSH', "Brush", "Paint the selection over a spot (B). Alt / Ctrl: erase",
                'BRUSH_DATA', 0),
               ('LASSO', "Lasso", "Drag a free lasso, or click points (L). Alt / Ctrl: "
                                  "subtract", 'SELECT_SET', 1)])
    size: IntProperty(
        name="Size", default=64, min=2, max=2048, subtype='PIXEL',
        description="Brush diameter in pixels of the ALB_ ([ and ] to change)")
    grow: IntProperty(
        name="Grow", default=3, min=0, max=64, subtype='PIXEL',
        description="Grow the selection by this many pixels before the fill: takes the "
                    "soft edge of a marker too")
    grain: FloatProperty(
        name="Grain", default=1.0, min=0.0, max=2.0, subtype='FACTOR',
        description="Fine detail copied in from a nearby area, so the fill is not "
                    "smoother than the scan around it. 0: smooth fill only")
    auto_fill: BoolProperty(
        name="Fill on Release", default=True,
        description="Fill as soon as a brush stroke or a lasso ends. Off: build the "
                    "selection first, F fills it")
    show_uv: BoolProperty(
        name="UV", default=False, update=_uv_changed,
        description="Show the UV overlay over the image while Inpaint runs (off: the "
                    "wireframe does not hide the spots). The editor's own setting comes "
                    "back when Inpaint ends")
    uv_opacity: FloatProperty(
        name="UV Opacity", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_uv_changed, description="Opacity of the UV overlay while Inpaint runs")


def settings(context):
    return context.scene.multicamproject_liquify


def lasso_settings(context=None):
    return (context or bpy.context).scene.multicamproject_lasso


def inpaint_settings(context=None):
    return (context or bpy.context).scene.multicamproject_inpaint


def preview_long_side(context=None):
    """Long side of the preview; field.preview_size never upscales, so a huge value means
    the photo's own size."""
    res = settings(context or bpy.context).preview_res
    return 1 << 20 if res == 'FULL' else int(res)


def register():
    bpy.utils.register_class(MULTICAMPROJECT_LiquifySettings)
    bpy.utils.register_class(MULTICAMPROJECT_LassoSettings)
    bpy.utils.register_class(MULTICAMPROJECT_InpaintSettings)
    bpy.types.Scene.multicamproject_inpaint = PointerProperty(type=MULTICAMPROJECT_InpaintSettings)
    bpy.types.Scene.multicamproject_liquify = PointerProperty(type=MULTICAMPROJECT_LiquifySettings)
    bpy.types.Scene.multicamproject_lasso = PointerProperty(type=MULTICAMPROJECT_LassoSettings)


def unregister():
    del bpy.types.Scene.multicamproject_lasso
    del bpy.types.Scene.multicamproject_inpaint
    bpy.utils.unregister_class(MULTICAMPROJECT_InpaintSettings)
    del bpy.types.Scene.multicamproject_liquify
    bpy.utils.unregister_class(MULTICAMPROJECT_LassoSettings)
    bpy.utils.unregister_class(MULTICAMPROJECT_LiquifySettings)
