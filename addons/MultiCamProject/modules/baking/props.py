"""Baking data: per object (its ALB/NOR images, MAT_ material, last bake state) and per
scene (the bake settings)."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)


def _on_material(data):
    from . import matsync
    matsync.picked(data.id_data)


def _on_handmade(data, context):
    from . import handmade
    handmade.on_toggle(data)


class MULTICAMPROJECT_BakeData(bpy.types.PropertyGroup):
    handmade: BoolProperty(
        name="Handmade", update=_on_handmade,
        description="Baked by hand: its own material and textures become MAT_ / ALB_ / NOR_. "
                    "Only the names and uv_normal are checked; it is exported through GN-Final "
                    "(one material, one UV), never baked by the add-on")
    alb_image: PointerProperty(type=bpy.types.Image, name="Albedo")
    nor_image: PointerProperty(type=bpy.types.Image, name="Normal")
    # baked from the Bake Source (04): packed work textures under the projection, never
    # exported - BA_<name> (albedo) and BN_<name> (normal), overwritten on every bake
    ba_image: PointerProperty(type=bpy.types.Image, name="Baked Albedo",
                              description="BA_<name>: albedo baked from the Bake Source (packed)")
    bn_image: PointerProperty(type=bpy.types.Image, name="Baked Normal",
                              description="BN_<name>: normal baked from the Bake Source (packed)")
    ba_fingerprint: StringProperty(description="State of the mesh and uv_normal at the last "
                                               "Bake from Source")
    ba_size: IntProperty(description="Resolution of the last Bake from Source (0 = never)")
    ba_far_share: FloatProperty(description="Share of the low poly farther from the source "
                                            "than the Cage at the last Bake from Source")
    ba_far_max: FloatProperty(description="Largest low poly - source distance at the last "
                                          "Bake from Source", unit='LENGTH')
    ba_fit_cage: FloatProperty(description="Cage that reaches 99% of the low poly", unit='LENGTH')
    last_ba_seconds: FloatProperty()
    material: PointerProperty(
        type=bpy.types.Material, name="Final Material",
        poll=lambda self, m: bool(m.get("multicamproject_baked")),
        update=lambda self, context: _on_material(self),
        description="MAT_<name>: the object's baked export material (slot 2). Picking another "
                    "one takes it over - it is renamed after the object")
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
    # the Remesh original: this object's high poly for Bake from mesh (before hp_object)
    source: PointerProperty(
        type=bpy.types.Object, name="Remesh Source", poll=lambda self, o: o.type == 'MESH',
        description="The original this Remesh copy was made from (the Remesh link; the bake "
                    "reads Bake Source)")
    # the high poly this object bakes BA_ / BN_ from (Selected to Active). Starts as the
    # Remesh original, any other mesh can be picked. Kept apart from `source`: Remesh
    # repairs every `source` on load, a picked scan must never be touched
    bake_source: PointerProperty(
        type=bpy.types.Object, name="Bake Source",
        poll=lambda self, o: o.type == 'MESH' and o != self.id_data,
        description="The high poly this object bakes BA_ (its colors) and BN_ (its surface) "
                    "from - Bake from Source, Selected to Active. Empty = projection only")


VIEW_ITEMS = (('PROJECTION', "Projection", "The camera projection setup, editable", 'CAMERA_DATA', 0),
              ('FINAL', "Final", "The baked result: MAT_, Color and uv_normal only - the projection "
                                 "is switched off", 'SHADING_TEXTURE', 1))


def _get_obj_view(self):
    from . import gn_final
    return 1 if gn_final.is_final(self) else 0


def _set_obj_view(self, value):
    """Clicking the toggle of the active object switches every selected mesh with it."""
    from . import common, gn_final
    ctx = bpy.context
    objs = common.selected_meshes(ctx)
    if self not in objs:
        objs.append(self)
    for o in objs:
        gn_final.set_final(o, ctx.scene, bool(value))


_nor_items = []     # Blender needs the item strings to stay alive


def _nor_sources(self, context):
    from .normal import available_sources
    _nor_items[:] = [(k, label, desc, i) for i, (k, label, desc) in enumerate(available_sources())]
    return _nor_items


class MULTICAMPROJECT_BakeSettings(bpy.types.PropertyGroup):
    resolution: IntProperty(
        name="Resolution", default=1024, min=1024, max=16384,
        description="Width and height of every baked texture - ALB_, NOR_, BA_ and BN_ - in "
                    "pixels. The one control: the 1K-8K toggles next to Bake (the whole scene)")
    margin: IntProperty(
        name="Margin at 8K", default=16, min=0, max=256, subtype='PIXEL',
        description="Pixels the bake extends past the UV islands (against seams in mip maps), "
                    "at 8K - scaled with the resolution: 16 at 8K = 8 at 4K = 2 at 1K")
    device: EnumProperty(
        name="Device", default='GPU',
        items=(('GPU', "GPU", "Bake on the GPU set in Preferences > System (CPU if none)"),
               ('CPU', "CPU", "Bake on the CPU")))
    anti_alias: IntProperty(
        name="Samples", default=1, min=1, max=64,
        description="Cycles samples per pixel. 1 is enough for the diffuse color")
    output_dir: StringProperty(
        name="Folder", default="//01.Baking/", subtype='DIR_PATH',
        description="Folder for ALB_<name>.png and NOR_<name>.png (same file on every bake)")

    bake_what: EnumProperty(
        name="Bake", default='BOTH',
        items=(('ALBEDO', "Albedo", "Bake only ALB_ (the projection's colors)", 'RENDER_STILL', 0),
               ('NORMAL', "Normal", "Make only NOR_ (from the albedo or the high poly)", 'NORMALS_FACE', 1),
               ('BOTH', "Both", "Bake ALB_, then make NOR_ from it", 'RENDER_RESULT', 2)),
        description="What the Bake button makes")
    nor_lite_source: StringProperty(default='HIGHPASS',
                                    description="The Lite method last used (Lite comes back to it)")
    nor_source: EnumProperty(name="Normal Source", items=_nor_sources,
                             description="How NOR_ is made")
    nor_strength: FloatProperty(name="Strength", default=2.0, min=0.0, soft_max=20.0,
                                description="Height of the relief read from the albedo")
    nor_radius: IntProperty(name="Radius", default=24, min=1, soft_max=256, subtype='PIXEL',
                            description="High-pass radius in pixels at 8K (scaled with the "
                                        "resolution): details smaller than this become relief, "
                                        "larger shading is ignored")
    nor_invert: BoolProperty(name="Invert", default=False,
                             description="Dark = raised instead of dark = recessed")
    cage_extrusion: FloatProperty(name="Cage Extrusion", default=0.02, min=0.0, unit='LENGTH',
                                  description="Bake from Source: how far rays start outside the "
                                              "low poly's surface to find the high poly")
    smooth_source: BoolProperty(name="Smooth Source", default=False,
                                description="Bake from Source: BN_ from a smoothed temporary "
                                            "copy of the high poly (against scan noise)")
    smooth_iterations: IntProperty(name="Iterations", default=5, min=1, max=100)

    color_source: EnumProperty(
        name="Vertex Color", default='SCAN_ATTRIBUTE',
        items=(('SCAN_ATTRIBUTE', "Scan Attribute", "Color = the scan's own color attribute "
                "(falls back to the albedo where it is missing)"),
               ('FROM_ALB', "Sampled from ALB", "Color = the baked albedo at each corner")))
    scan_color_name: StringProperty(name="Scan Attribute", default="Attribute",
                                    description="The scan's color attribute that becomes Color")
    own_files: StringProperty(
        options={'HIDDEN'}, description="JSON: the ALB_/NOR_ files this .blend used or wrote "
                                        "(baking.owned) - the only ones a clean-up may remove")
    png_compression: IntProperty(
        name="PNG Compression", default=1, min=0, max=9,
        description="zlib level of NOR_ (0 = fastest, 9 = smallest). At 8K: 1 = ~6 s / 205 MB, "
                    "6 = ~21 s / 190 MB - Unity compresses the texture anyway")


def register():
    bpy.utils.register_class(MULTICAMPROJECT_BakeData)
    bpy.utils.register_class(MULTICAMPROJECT_BakeSettings)
    bpy.types.Object.multicamproject_bake = PointerProperty(type=MULTICAMPROJECT_BakeData)
    bpy.types.Object.multicamproject_view = EnumProperty(
        name="View", items=VIEW_ITEMS, get=_get_obj_view, set=_set_obj_view,
        options={'SKIP_SAVE'}, description="Projection setup or Final baked result (GN-Final)")
    bpy.types.Scene.multicamproject_bake_settings = PointerProperty(type=MULTICAMPROJECT_BakeSettings)


def unregister():
    del bpy.types.Scene.multicamproject_bake_settings
    del bpy.types.Object.multicamproject_view
    del bpy.types.Object.multicamproject_bake
    bpy.utils.unregister_class(MULTICAMPROJECT_BakeSettings)
    bpy.utils.unregister_class(MULTICAMPROJECT_BakeData)
