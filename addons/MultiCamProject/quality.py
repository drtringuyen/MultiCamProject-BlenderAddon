"""Preview / Final: how heavy the add-on's materials (MCP_, MAT_) are in the viewport - one
setting of the file, the icon in front of every Processing | Baked bar.

Preview  every MCP_ / MAT_ shows its color as an Emission (a second Material Output, made
         active): no lighting, no normal map - the normal textures (8K float, 1 GB each)
         are not in the shader, so they leave the GPU, and their RAM is freed.
         GPU textures capped at 2K (Preferences > Viewport > Texture Limit: global).
Final    the Principled BSDF with its normal maps (the original output), cap 8K.

The original node chain is never changed: only which output is active. Bakes and the export
see Final for their duration (final()), the cap lifted - it applies to EEVEE renders too.
"""
from contextlib import contextmanager

import bpy
from bpy.app.handlers import persistent

PREVIEW_OUT = "MCP Preview Output"
PREVIEW_EMIT = "MCP Preview Emission"
LIMITS = {'PREVIEW': 'CLAMP_2048', 'FINAL': 'CLAMP_8192'}
# the tags of MCP_ (camera_project) and MAT_ (baking) - camera_project.core MAT_TAG / BAKED_TAG
TAGS = ("multicamproject_material", "multicamproject_baked")
ITEMS = (('PREVIEW', "Preview", "Fast: the add-on's materials as flat color (Emission), no "
                                "lighting or normal maps, textures capped at 2K",
          'SHADING_TEXTURE', 0),
         ('FINAL', "Final", "Quality: Principled BSDF with normal maps, textures up to 8K",
          'SHADING_RENDERED', 1))


def mode(scene=None):
    props = getattr(scene or bpy.context.scene, "multicamproject_props", None)
    return props.quality if props is not None else 'FINAL'


def materials():
    """Every MCP_ / MAT_ of this file."""
    return [m for m in bpy.data.materials
            if not m.library and m.use_nodes and m.node_tree and any(m.get(t) for t in TAGS)]


def _main_output(nt):
    out = nt.nodes.get("Material Output")
    if out is not None and out.type == 'OUTPUT_MATERIAL':
        return out
    return next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL' and n.name != PREVIEW_OUT),
                None)


def _color(nt, out):
    """(linked socket, default) of what the main output's BSDF shows as Base Color."""
    link = next((lk for lk in nt.links if lk.to_socket == out.inputs["Surface"]), None)
    if link is None:
        return None, None
    node = link.from_node
    sock = node.inputs.get("Base Color") or node.inputs.get("Color")
    if sock is None:
        return None, None
    return (sock.links[0].from_socket if sock.is_linked else None), sock.default_value


def _ensure_preview(nt):
    """The Emission + its output (made / relinked: a rebuild may change the color chain)."""
    out = _main_output(nt)
    if out is None:
        return None
    src, default = _color(nt, out)
    emit = nt.nodes.get(PREVIEW_EMIT)
    if emit is None:
        emit = nt.nodes.new("ShaderNodeEmission")
        emit.name = emit.label = PREVIEW_EMIT
    pout = nt.nodes.get(PREVIEW_OUT)
    if pout is None:
        pout = nt.nodes.new("ShaderNodeOutputMaterial")
        pout.name = pout.label = PREVIEW_OUT
    emit.location = (out.location.x, out.location.y + 260)
    pout.location = (out.location.x + 220, out.location.y + 260)
    color = emit.inputs["Color"]
    if src is not None:
        if not color.is_linked or color.links[0].from_socket != src:
            nt.links.new(src, color)
    else:
        for lk in list(color.links):
            nt.links.remove(lk)
        if default is not None:
            color.default_value = default
    if not pout.inputs["Surface"].is_linked:
        nt.links.new(emit.outputs["Emission"], pout.inputs["Surface"])
    return pout


def set_material(mat, preview):
    """mat shows its Emission (preview) or its BSDF. Nothing is written when already so."""
    nt = mat.node_tree if mat is not None else None
    if nt is None:
        return
    if preview:
        pout = _ensure_preview(nt)
        if pout is not None and not pout.is_active_output:
            pout.is_active_output = True
        return
    out = _main_output(nt)
    if out is not None and not out.is_active_output:
        out.is_active_output = True


def sync_material(mat):
    """A builder made / rebuilt mat: it follows the file's mode."""
    set_material(mat, mode() == 'PREVIEW')


def _normal_images(mats):
    """The images that feed a Normal Map node in `mats`."""
    out = set()
    for m in mats:
        for lk in m.node_tree.links:
            if lk.to_node.type == 'NORMAL_MAP' and lk.from_node.type == 'TEX_IMAGE' \
                    and lk.from_node.image is not None:
                out.add(lk.from_node.image)
    return out


def set_limit(value):
    system = bpy.context.preferences.system
    if system.gl_texture_limit != value:
        system.gl_texture_limit = value


def apply(scene):
    """Every MCP_ / MAT_ and the texture cap as the file's mode says."""
    preview = mode(scene) == 'PREVIEW'
    mats = materials()
    for m in mats:
        set_material(m, preview)
    set_limit(LIMITS[mode(scene)])
    if preview:                 # out of the shader now: their RAM goes too
        for img in _normal_images(mats):
            if img.has_data and not img.is_dirty:
                img.buffers_free()


@contextmanager
def final(mats=None):
    """Final for a bake / export: `mats` (default: all) on their BSDF and no texture cap,
    as before afterwards."""
    system = bpy.context.preferences.system
    limit = system.gl_texture_limit
    preview = mode() == 'PREVIEW'
    mats = [m for m in (materials() if mats is None else mats) if m is not None]
    try:
        set_limit('CLAMP_OFF')
        if preview:
            for m in mats:
                set_material(m, False)
        yield
    finally:
        set_limit(limit)
        if preview:
            for m in mats:
                try:
                    set_material(m, True)
                except ReferenceError:
                    pass


def object_materials(obj):
    """obj's MCP_ / MAT_ (its slots and the projection's GN material)."""
    mats = {s.material for s in obj.material_slots if s.material}
    cam = getattr(obj, "multicamproject_cam", None)
    if cam is not None and cam.material is not None:
        mats.add(cam.material)
    return [m for m in mats if any(m.get(t) for t in TAGS)]


VIEWPORT_SAMPLES = 8        # EEVEE viewport samples (Blender's default: 16)


def setup_file(scene):
    """Setup File IO: the settings every file works faster with. Returns report lines.
    EEVEE: fewer viewport samples, no shadows (the file has no lights: the studio light of
    Material Preview needs none); the add-on's materials on Preview."""
    e = scene.eevee
    e.taa_samples = min(e.taa_samples, VIEWPORT_SAMPLES) or VIEWPORT_SAMPLES
    e.use_shadows = False
    scene.multicamproject_props.quality = 'PREVIEW'     # -> apply
    return [f"Viewport: Preview, EEVEE {e.taa_samples} samples, no shadows"]


class MULTICAMPROJECT_OT_ToggleQuality(bpy.types.Operator):
    """Preview (fast: flat color, no lighting or normal maps, textures capped at 2K) or Final
    (Principled BSDF with normal maps, up to 8K) for every MCP_ / MAT_ of the file. The
    texture cap is a Blender preference: it applies to every open file"""
    bl_idname = "multicamproject.toggle_quality"
    bl_label = "Preview / Final"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.multicamproject_props
        props.quality = 'FINAL' if props.quality == 'PREVIEW' else 'PREVIEW'
        self.report({'INFO'}, f"{props.quality.title()} for {len(materials())} materials")
        return {'FINISHED'}


def draw_toggle(layout, context):
    """The icon in front of a Processing | Baked bar."""
    q = mode(context.scene)
    icon = next(i[3] for i in ITEMS if i[0] == q)
    layout.operator(MULTICAMPROJECT_OT_ToggleQuality.bl_idname, text="", icon=icon,
                    depress=q == 'FINAL')


@persistent
def _on_load(_):
    """The cap follows the opened file's mode (a preference: the last file set it)."""
    try:
        set_limit(LIMITS[mode()])
    except Exception:
        pass


def register():
    bpy.utils.register_class(MULTICAMPROJECT_OT_ToggleQuality)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ToggleQuality)
