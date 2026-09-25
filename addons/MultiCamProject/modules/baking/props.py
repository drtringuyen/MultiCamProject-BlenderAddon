"""Baking data: per object (its ALB/NOR images, MAT_ material, last bake state) and per
scene (the bake settings)."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)


class MULTICAMPROJECT_BakeData(bpy.types.PropertyGroup):
    alb_image: PointerProperty(type=bpy.types.Image, name="Albedo")
    nor_image: PointerProperty(type=bpy.types.Image, name="Normal")
    material: PointerProperty(type=bpy.types.Material, name="Baked Material")
    fingerprint: StringProperty(description="State of the projection at the last albedo bake")
    alb_size: IntProperty(description="Resolution of the last albedo bake (0 = never baked)")
    nor_size: IntProperty(description="Resolution of the last normal map (0 = none)")
    nor_source_used: StringProperty(description="Normal source of the last normal map")
    last_bake_seconds: FloatProperty()
    last_nor_seconds: FloatProperty()
    nor_times: StringProperty(description="JSON: seconds of the last run per normal source and size")
    # active / active-render UV before Final put uv_normal there (restored by Projection)
    prev_uv_active: StringProperty()
    prev_uv_render: StringProperty()


_nor_items = []     # Blender needs the item strings to stay alive


def _nor_sources(self, context):
    from .normal import available_sources
    _nor_items[:] = [(k, label, desc, i) for i, (k, label, desc) in enumerate(available_sources())]
    return _nor_items


class MULTICAMPROJECT_BakeSettings(bpy.types.PropertyGroup):
    resolution: IntProperty(
        name="Resolution", default=8192, min=1024, max=16384,
        description="Width and height of ALB_ and NOR_ in pixels")
    margin: IntProperty(
        name="Margin", default=16, min=0, max=256, subtype='PIXEL',
        description="Pixels the bake extends past the UV islands (against seams in mip maps). "
                    "16 at 8K")
    device: EnumProperty(
        name="Device", default='GPU',
        items=(('GPU', "GPU", "Bake on the GPU set in Preferences > System (CPU if none)"),
               ('CPU', "CPU", "Bake on the CPU")))
    anti_alias: IntProperty(
        name="Samples", default=1, min=1, max=64,
        description="Cycles samples per pixel. 1 is enough for the diffuse color")
    output_dir: StringProperty(
        name="Folder", default="//Textures/", subtype='DIR_PATH',
        description="Folder for ALB_<name>.png and NOR_<name>.png (same file on every bake)")
    roughness: FloatProperty(name="Roughness", default=0.8, min=0.0, max=1.0, subtype='FACTOR',
                             description="Roughness of the MAT_ material")

    nor_source: EnumProperty(name="Normal Source", items=_nor_sources,
                             description="How NOR_ is made")
    nor_strength: FloatProperty(name="Strength", default=2.0, min=0.0, soft_max=20.0,
                                description="Height of the relief read from the albedo")
    nor_radius: IntProperty(name="Radius", default=24, min=1, soft_max=256, subtype='PIXEL',
                            description="High-pass radius in pixels at full resolution: details "
                                        "smaller than this become relief, larger shading is ignored")
    nor_invert: BoolProperty(name="Invert", default=False,
                             description="Dark = raised instead of dark = recessed")
    nor_preview_2k: BoolProperty(name="Preview at 2K", default=False,
                                 description="Make the normal map at 2048 px to tune the settings "
                                             "quickly (the file is overwritten by the full-size run)")
    hp_object: PointerProperty(
        type=bpy.types.Object, name="High Poly", poll=lambda self, o: o.type == 'MESH',
        description="Mesh whose surface detail is baked onto the object (Bake from mesh)")
    cage_extrusion: FloatProperty(name="Cage Extrusion", default=0.02, min=0.0, unit='LENGTH',
                                  description="How far rays start outside the object's surface")
    smooth_source: BoolProperty(name="Smooth Source", default=False,
                                description="Smooth a temporary copy of the high poly first "
                                            "(against scan noise)")
    smooth_iterations: IntProperty(name="Iterations", default=5, min=1, max=100)

    color_source: EnumProperty(
        name="Vertex Color", default='SCAN_ATTRIBUTE',
        items=(('SCAN_ATTRIBUTE', "Scan Attribute", "Color = the scan's own color attribute "
                "(falls back to the albedo where it is missing)"),
               ('FROM_ALB', "Sampled from ALB", "Color = the baked albedo at each corner")))
    scan_color_name: StringProperty(name="Scan Attribute", default="Attribute",
                                    description="The scan's color attribute that becomes Color")
    png_compression: IntProperty(
        name="PNG Compression", default=1, min=0, max=9,
        description="zlib level of NOR_ (0 = fastest, 9 = smallest). At 8K: 1 = ~6 s / 205 MB, "
                    "6 = ~21 s / 190 MB - Unity compresses the texture anyway")


def register():
    bpy.utils.register_class(MULTICAMPROJECT_BakeData)
    bpy.utils.register_class(MULTICAMPROJECT_BakeSettings)
    bpy.types.Object.multicamproject_bake = PointerProperty(type=MULTICAMPROJECT_BakeData)
    bpy.types.Scene.multicamproject_bake_settings = PointerProperty(type=MULTICAMPROJECT_BakeSettings)


def unregister():
    del bpy.types.Scene.multicamproject_bake_settings
    del bpy.types.Object.multicamproject_bake
    bpy.utils.unregister_class(MULTICAMPROJECT_BakeSettings)
    bpy.utils.unregister_class(MULTICAMPROJECT_BakeData)
