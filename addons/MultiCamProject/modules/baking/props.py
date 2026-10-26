"""Baking data: per object (its ALB/NOR images, MAT_ material, last bake state) and per
scene (the bake settings)."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty,
                       StringProperty)


def _on_material(data):
    from . import matsync
    matsync.picked(data.id_data)


def _on_handmade(data, context):
    from . import handmade
    handmade.on_toggle(data)


def _on_route(data, context):
    from . import route
    route.changed(data.id_data)


ROUTE_ITEMS = (
    ('ORIGINAL', "From Original", "Bake only from the original mesh (Bake Source): BAo_ / BNo_ "
                                  "(Cycles), ALB_ / NOR_ are their copies. The projection is "
                                  "ignored", 'MESH_DATA', 0),
    ('PROJECTION', "From Projection", "Bake only from the camera projection: BAp_ (EEVEE) and "
                                      "BNp_ generated from it, ALB_ / NOR_ are their copies. The "
                                      "original is ignored", 'CAMERA_DATA', 1),
    ('MIXED', "Mixed", "Both: VCMix / VCMix2 RGB pick the cameras, their alpha blends BAo_ -> "
                       "projection (colors) and BNo_ -> BNp_ (normals)", 'MOD_MASK', 2))


def _on_show_normal(data):
    from . import material
    material.apply_normal_visibility(data.id_data)


def _on_original_normal(data):
    """The Processing material shows the original's side as picked (BNo_ / BNoG_)."""
    obj = data.id_data
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None and cam.is_setup:
        from ..camera_project import core as cp
        cp.ensure_material(obj)


def _on_tex_size(data, context):
    from . import cache
    cache.clear()


_ba_items = []      # kept alive: Blender only borrows a dynamic enum's strings
_alb_items = []


def _show_items(keep, orig, ip, what):
    """[original, its _inpaint] as dropdown items - '<folder>/<file>' each (06's rows)."""
    from . import common
    from ... import thumbs
    keep[:] = [('ORIGINAL', common.file_label(orig) if orig else f"(no {what})",
                f"The {what} as baked", thumbs.icon(orig) or 'IMAGE_DATA', 0)]
    if ip is not None:
        keep.append(('INPAINT', common.file_label(ip), f"{what}'s inpainted copy (06)",
                     thumbs.icon(ip) or 'BRUSH_DATA', 1))
    return keep


def _ba_show_items(data, _context):
    """BAo_'s files: the original, and its _inpaint when there is one."""
    return _show_items(_ba_items, data.ba_image, data.ba_inpaint, "BAo_")


def _alb_show_items(data, _context):
    return _show_items(_alb_items, data.alb_image, data.alb_inpaint, "ALB_")


def _get_alb_show(data):
    return 1 if data.alb_inpaint is not None and data.alb_use_inpaint else 0


def _set_alb_show(data, value):
    if data.alb_use_inpaint == bool(value):
        return
    data.alb_use_inpaint = bool(value)
    try:            # MAT_ (and Export, 07) take it now
        from ..image_editor import sources
        sources.relink(sources.Source('ALB', data.id_data), bpy.context.scene)
    except ImportError:
        pass


_mode_items = {'ALB': [], 'NOR': []}     # kept alive (dynamic enum strings)


def _mode_items_for(kind, data):
    """[Bake, Load] - "Load (not found)" while 02.Overrides has no file for it."""
    from ..tex_override import core as tex
    obj = data.id_data
    found = tex.available(bpy.context.scene, obj, kind)
    n = "ALB_" if kind == 'ALB' else "NOR_"
    step = "05 bakes" if kind == 'ALB' else "07 generates"
    keep = _mode_items[kind]
    keep[:] = [('BAKE', "Bake", f"{step} {n} into 01.Bake", 'RENDER_STILL', 0),
               ('LOAD', "Load" if found else "Load (not found)",
                f"{n} is the finished file in 02.Overrides - nothing is baked" if found else
                f"No {tex.name(obj, kind)}.png in 02.Overrides - stays on Bake",
                'IMAGE_REFERENCE' if found else 'ERROR', 1)]
    return keep


def _get_mode(data, kind):
    return 1 if getattr(data, "alb_mode" if kind == 'ALB' else "nor_mode") == 'LOAD' else 0


def _set_mode(data, kind, value):
    """Load without its file goes (back) to Bake."""
    from ..tex_override import core as tex
    obj = data.id_data
    scene = bpy.context.scene
    want = 'LOAD' if value == 1 and tex.available(scene, obj, kind) else 'BAKE'
    try:
        tex.set_mode(scene, obj, kind, want)
    except RuntimeError as e:       # Locked Finish: the dropdown is greyed anyway
        print(f"[MultiCamProject] {obj.name}: {e}")


def _get_ba_show(data):
    return 1 if data.ba_inpaint is not None and data.ba_use_inpaint else 0


def _set_ba_show(data, value):
    if data.ba_use_inpaint == bool(value):
        return
    data.ba_use_inpaint = bool(value)
    obj = data.id_data
    try:            # MCP_'s BAKED frame takes it now
        from ..image_editor import sources
        sources.relink(sources.Source('BAO', obj), bpy.context.scene)
    except ImportError:
        pass


class MULTICAMPROJECT_BakeData(bpy.types.PropertyGroup):
    handmade: BoolProperty(
        name="Handmade", update=_on_handmade,
        description="Baked by hand: its own material and textures become MAT_ / ALB_ / NOR_. "
                    "Only the names and uv_normal are checked; it is exported through GN-Final "
                    "(one material, one UV), never baked by the add-on")
    tex_size: EnumProperty(
        name="Texture Size", default='AUTO', update=_on_tex_size,
        items=(('AUTO', "Auto Resolution", "Auto: the scene's resolution, set in front of Export", 0),
               ('1024', "1K", "ALB_, NOR_ and the work textures of this object at 1024 x 1024", 1),
               ('2048', "2K", "ALB_, NOR_ and the work textures of this object at 2048 x 2048", 2),
               ('4096', "4K", "ALB_, NOR_ and the work textures of this object at 4096 x 4096", 3),
               ('8192', "8K", "ALB_, NOR_ and the work textures of this object at 8192 x 8192", 4)),
        description="This object's texture size. A = the scene's resolution. Textures baked at "
                    "another size are listed to bake again")
    alb_image: PointerProperty(type=bpy.types.Image, name="Albedo")
    nor_image: PointerProperty(type=bpy.types.Image, name="Normal")
    # TexOverride: each one baked here (01.Bake) or a finished file loaded (02.Overrides)
    alb_mode: EnumProperty(
        name="ALB_", default='BAKE',
        items=(('BAKE', "Bake", "05 bakes ALB_ into the bake folder (01.Bake)", 0),
               ('LOAD', "Load", "ALB_ is the finished file in the Overrides folder "
                                "(02.Overrides) - nothing is baked", 1)),
        description="Where ALB_ comes from: baked (05) or loaded from 02.Overrides")
    nor_mode: EnumProperty(
        name="NOR_", default='BAKE',
        items=(('BAKE', "Bake", "07 generates NOR_ into the bake folder (01.Bake)", 0),
               ('LOAD', "Load", "NOR_ is the finished file in the Overrides folder "
                                "(02.Overrides) - nothing is generated", 1)),
        description="Where NOR_ comes from: generated (07) or loaded from 02.Overrides")
    # the dropdowns of alb_mode / nor_mode: "Load (not found)" without the file, back to Bake
    alb_pick: EnumProperty(
        name="ALB_", items=lambda d, _c: _mode_items_for('ALB', d),
        get=lambda d: _get_mode(d, 'ALB'), set=lambda d, v: _set_mode(d, 'ALB', v),
        options={'SKIP_SAVE'},
        description="Where ALB_ comes from: Bake (05, 01.Bake) or Load (02.Overrides)")
    nor_pick: EnumProperty(
        name="NOR_", items=lambda d, _c: _mode_items_for('NOR', d),
        get=lambda d: _get_mode(d, 'NOR'), set=lambda d, v: _set_mode(d, 'NOR', v),
        options={'SKIP_SAVE'},
        description="Where NOR_ comes from: Bake (07, 01.Bake) or Load (02.Overrides)")
    # 06 Inpainting: the saved copies (files next to the originals, which stay as baked)
    alb_inpaint: PointerProperty(type=bpy.types.Image, name="Albedo Inpainted",
                                 description="ALB_<name>_inpaint: ALB_ inpainted - exported as "
                                             "ALB_ and NOR_ is made from it")
    alb_use_inpaint: BoolProperty(default=True, description="MAT_, 07 and Export take ALB_'s "
                                                            "_inpaint (off: the original, the "
                                                            "inpaint kept)")
    alb_show: EnumProperty(name="ALB_ used", items=_alb_show_items, get=_get_alb_show,
                           set=_set_alb_show, options={'SKIP_SAVE'},
                           description="Which ALB_ MAT_, 07 Generate Normal and Export take: the "
                                       "original or its inpaint (both files stay)")
    ba_inpaint: PointerProperty(type=bpy.types.Image, name="Baked Albedo Inpainted",
                                description="BAo_<name>_inpaint: BAo_ inpainted - MCP_ and "
                                            "05 Bake Albedo use it")
    ba_use_inpaint: BoolProperty(default=True, description="MCP_ and 05 take BAo_'s _inpaint "
                                                           "(off: the original, the inpaint kept)")
    ba_show: EnumProperty(name="BAo_ shown", items=_ba_show_items, get=_get_ba_show,
                          set=_set_ba_show, options={'SKIP_SAVE'},
                          description="Which BAo_ MCP_ and 05 Bake Albedo take: the original or "
                                      "its inpaint (both files stay)")
    # handmade Rebake: the textures as painted on uv_old (files in <bake folder>/_previous)
    prev_alb: PointerProperty(type=bpy.types.Image, name="Previous Albedo")
    prev_nor: PointerProperty(type=bpy.types.Image, name="Previous Normal")
    uv_rebaked: StringProperty(description="uv_normal when ALB_ / NOR_ last matched it (Edit UV "
                                           "or a Rebake): another one means Rebake first")
    rebaked: BoolProperty(description="A Rebake wrote ALB_ / NOR_ since Edit UV (Cancel puts "
                                      "the _previous files back)")
    workflow: EnumProperty(
        name="Mesh Workflow", default='NONE',
        items=(('NONE', "None", "No mesh workflow chosen yet (02)", 0),
               ('REMESH', "Remesh", "2B: a decimated copy of the scan, baked from it", 1),
               ('RETOPO', "Retopo", "2C: modelled by hand on top of the scan, baked from it", 2),
               ('PROJECT', "Only Project", "2D: the scan itself, BAo_ from its own textures", 3)),
        description="The mesh workflow picked in 02 (set by 2B / 2C / 2D)")
    locked: BoolProperty(
        name="Locked Finish", default=False,
        description="08 Lock Finish: the object is final - its ALB_ / NOR_ are never removed "
                    "and every step is locked until it is unlocked")
    retopo: BoolProperty(description="Made by 2C Retopo: modelled by hand on top of the "
                                     "original, no Decimate")
    # where 06 Bake Final takes ALB_ / NOR_ from (each object its own)
    route: EnumProperty(name="Bake Route", items=ROUTE_ITEMS, default='MIXED', update=_on_route,
                        description="Where 05 Bake Albedo takes ALB_ (and 07 NOR_) from")
    original_normal: EnumProperty(
        name="Normal", default='GENERATED',
        items=(('BAKED', "Bake from Scan",
                "07: BNo_, the scan's surface baked onto the low poly (Cycles), where the "
                "projection mask is 0 - the High-pass of ALB_ where it is 1", 'MESH_DATA', 0),
               ('GENERATED', "High-pass of ALB_",
                "07: NOR_ generated from ALB_ (its inpaint when there is one) with the engine "
                "below - High-pass or AI", 'IMAGE_RGB', 1)),
        update=lambda self, context: _on_original_normal(self),
        description="From Original / Mixed: the normal on the original's side")
    show_normal: BoolProperty(
        name="Normal Map", default=True,
        update=lambda self, context: _on_show_normal(self),
        description="Show the normal map in the viewport (MAT_ and the Processing material). "
                    "Viewing only: the export always writes it")
    route_user: BoolProperty(description="The route was picked by hand (never changed by a "
                                          "Setup step then)")
    route_prompt: StringProperty(description="A Setup step added a workflow: shown in 05 until a "
                                             "route is picked")
    # work textures, PNG files in the bake folder (never exported, overwritten on every bake):
    # from the Original (04 Bake from Source) BAo_<name> / BNo_<name>, from the Projection
    # BAp_<name> / BNp_<name>
    ba_image: PointerProperty(type=bpy.types.Image, name="Baked Albedo (Original)",
                              description="BAo_<name>: albedo baked from the Bake Source")
    bn_image: PointerProperty(type=bpy.types.Image, name="Baked Normal (Original)",
                              description="BNo_<name>: normal baked from the Bake Source")
    bn_stale: BoolProperty(description="The last Bake from Source left BNo_ out (Original "
                                       "Normal = Generated): it is older than BAo_")
    bap_image: PointerProperty(type=bpy.types.Image, name="Baked Albedo (Projection)",
                               description="BAp_<name>: albedo rendered from the projection")
    bnp_image: PointerProperty(type=bpy.types.Image, name="Baked Normal (Projection)",
                               description="BNp_<name>: normal generated from BAp_")
    bng_image: PointerProperty(type=bpy.types.Image, name="Generated Normal (Original)",
                               description="BNoG_<name>: normal generated from BAo_")
    # Paint Textures (camera_project.paint_tex), 1K PNGs in the bake folder, painted on
    # uv_normal: MSK_<name> blend mask, CMX1_ / CMX2_<name> cameras 1-3 / 4-6
    msk_image: PointerProperty(type=bpy.types.Image, name="Paint Mask",
                               description="MSK_<name>: the painted blend mask")
    cmx1_image: PointerProperty(type=bpy.types.Image, name="Paint Cameras 1-3",
                                description="CMX1_<name>: the painted mix of cameras 1-3")
    cmx2_image: PointerProperty(type=bpy.types.Image, name="Paint Cameras 4-6",
                                description="CMX2_<name>: the painted mix of cameras 4-6")
    bng_fingerprint: StringProperty(description="BAo_ state + normal settings at the last BNoG_")
    bp_fingerprint: StringProperty(description="State of the projection at the last BAp_")
    bp_size: IntProperty(description="Resolution of the last BAp_ (0 = never)")
    bnp_fingerprint: StringProperty(description="BAp_ state + normal settings at the last BNp_")
    last_bp_seconds: FloatProperty()
    ba_fingerprint: StringProperty(description="State of the mesh and uv_normal at the last "
                                               "Bake from Source")
    ba_parts: StringProperty(description="JSON: what the last Bake from Source depended on, "
                                         "readable (fingerprint.ba_why)")
    ba_size: IntProperty(description="Resolution of the last Bake from Source (0 = never)")
    ba_far_share: FloatProperty(description="Share of the low poly farther from the source "
                                            "than the Cage at the last Bake from Source")
    ba_far_max: FloatProperty(description="Largest low poly - source distance at the last "
                                          "Bake from Source", unit='LENGTH')
    ba_fit_cage: FloatProperty(description="Cage that reaches 99% of the low poly", unit='LENGTH')
    cage: FloatProperty(name="Cage Extrusion", default=0.02, min=0.0, unit='LENGTH',
                        description="Bake from Source: how far rays start outside this low "
                                    "poly's surface to find the high poly (each object its own)")
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
    nor_fingerprint: StringProperty(description="07: what the last NOR_ was made from (the "
                                                "ALB_ / its inpaint, the settings, BNo_)")
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


VIEW_ITEMS = (('PROJECTION', "Processing", "The Processing material (MCP_): the camera "
                                          "projection setup, editable", 'CAMERA_DATA', 0),
              ('FINAL', "Baked", "The baked result: MAT_, Color and uv_normal only - the "
                                 "projection is switched off", 'SHADING_TEXTURE', 1))


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


RES_ITEMS = tuple((str(n), f"{n // 1024}K", f"Bake at {n} x {n} px (every object set to A)", i)
                  for i, n in enumerate((1024, 2048, 4096, 8192)))


def _get_res_menu(self):
    keys = [int(k) for k, *_r in RES_ITEMS]
    return keys.index(self.resolution) if self.resolution in keys else 0


def _set_res_menu(self, value):
    self.resolution = int(RES_ITEMS[value][0])


class MULTICAMPROJECT_BakeSettings(bpy.types.PropertyGroup):
    resolution: IntProperty(
        name="Resolution", default=1024, min=1024, max=16384,
        description="Width and height of every baked texture - ALB_, NOR_ and the work textures - in "
                    "pixels, for every object set to A (Auto) - the dropdown in front of Export")
    resolution_menu: EnumProperty(
        name="Resolution", items=RES_ITEMS, get=_get_res_menu, set=_set_res_menu,
        description="Width and height of the baked textures of every object whose own size is "
                    "A (Auto). Textures baked at another size count as outdated")
    margin: IntProperty(
        name="Margin at 8K", default=16, min=0, max=256, subtype='PIXEL',
        description="Pixels the bake extends past the UV islands (against seams in mip maps), "
                    "at 8K - scaled with the resolution: 16 at 8K = 8 at 4K = 2 at 1K")
    device: EnumProperty(
        name="Device", default='GPU',
        items=(('GPU', "GPU", "Bake from Source (Cycles) on the GPU set in Preferences > "
                              "System (CPU if none)"),
               ('CPU', "CPU", "Bake from Source (Cycles) on the CPU")))
    anti_alias: IntProperty(
        name="Samples", default=1, min=1, max=64,
        description="Samples per pixel - Cycles for Bake from Source, EEVEE for the albedo "
                    "and the blend mask. 1 is enough for colors")
    output_dir: StringProperty(
        name="Folder", default="//01.Bake/", subtype='DIR_PATH',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
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
    # before 2026-10-05 the Cage was scene-wide: only read once, to give each object its own
    cage_extrusion: FloatProperty(name="Cage Extrusion", default=0.02, min=0.0, unit='LENGTH',
                                  options={'HIDDEN'})
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
    bpy.types.Scene.multicamproject_green = FloatVectorProperty(
        size=3, subtype='COLOR_GAMMA', get=lambda self: (0.20, 0.75, 0.25),
        options={'SKIP_SAVE'}, description="05 Bake Albedo")


def unregister():
    del bpy.types.Scene.multicamproject_green
    del bpy.types.Scene.multicamproject_bake_settings
    del bpy.types.Object.multicamproject_view
    del bpy.types.Object.multicamproject_bake
    bpy.utils.unregister_class(MULTICAMPROJECT_BakeSettings)
    bpy.utils.unregister_class(MULTICAMPROJECT_BakeData)
