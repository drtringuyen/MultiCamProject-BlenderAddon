"""Liquify brush settings (per scene)."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty

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


def settings(context):
    return context.scene.multicamproject_liquify


def register():
    bpy.utils.register_class(MULTICAMPROJECT_LiquifySettings)
    bpy.types.Scene.multicamproject_liquify = PointerProperty(type=MULTICAMPROJECT_LiquifySettings)


def unregister():
    del bpy.types.Scene.multicamproject_liquify
    bpy.utils.unregister_class(MULTICAMPROJECT_LiquifySettings)
