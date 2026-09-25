"""Cycles bake into ALB_ / NOR_ images.

The render/bake settings save + restore and the image preparation follow BakeLab 2
(GPL-3, Shahzod Boyxonov) - only this small subset is ported.

Every object is baked in its own bpy.ops.object.bake call: the scans share their scan
materials, and a shared material can only point its active image node at one object's
image at a time.
"""
import os
import time
from contextlib import contextmanager

import bpy

from ..camera_project import core as cp
from . import common, fingerprint, gn_final, material

TMP_NODE = "MCP_BAKE_TMP"
TMP_IMAGE = "MCP_ALB_BAKE_TMP"

_RENDER_ATTRS = ("engine",)
_CYCLES_ATTRS = ("device", "samples", "bake_type", "use_denoising", "use_adaptive_sampling")
_BAKE_ATTRS = ("margin", "margin_type", "target", "use_clear", "use_selected_to_active",
               "use_cage", "cage_extrusion", "max_ray_distance", "normal_space", "normal_r",
               "normal_g", "normal_b", "use_pass_direct", "use_pass_indirect", "use_pass_color",
               "use_pass_emit", "use_pass_diffuse", "use_pass_glossy", "use_pass_transmission")


def _save(holder, attrs):
    return {a: getattr(holder, a) for a in attrs if hasattr(holder, a)}


def _restore(holder, saved):
    for a, v in saved.items():
        try:
            setattr(holder, a, v)
        except (AttributeError, TypeError, ValueError):
            pass


@contextmanager
def render_state(scene):
    """save_defaults / restore_defaults: everything the bake changes comes back."""
    saved = (_save(scene.render, _RENDER_ATTRS), _save(scene.cycles, _CYCLES_ATTRS),
             _save(scene.render.bake, _BAKE_ATTRS))
    try:
        yield
    finally:
        _restore(scene.render, saved[0])
        _restore(scene.cycles, saved[1])
        _restore(scene.render.bake, saved[2])


@contextmanager
def selection(context, active, selected):
    """Exactly `selected` selected and `active` active; the user's selection comes back."""
    vl = context.view_layer
    old_sel = [o for o in context.selected_objects]
    old_active = vl.objects.active
    try:
        for o in old_sel:
            o.select_set(False)
        for o in selected:
            o.select_set(True)
        vl.objects.active = active
        yield
    finally:
        for o in context.selected_objects:
            o.select_set(False)
        for o in old_sel:
            if o.name in vl.objects:
                o.select_set(True)
        vl.objects.active = old_active


def prepare_image(current, name, size, is_float, colorspace):
    """Reuse the image by pointer (then by name) - never a .001. Resized when needed."""
    img = current or bpy.data.images.get(name)
    if img is not None and img.is_float != is_float:
        # e.g. GN-Final sampled ALB (GN adds a float buffer): drop the buffers, the
        # image reloads from its file as it was saved
        img.buffers_free()
    if img is None:
        img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=is_float)
    elif img.name != name and not bpy.data.images.get(name):
        img.name = name
    if tuple(img.size) != (size, size):
        img.scale(size, size)
    img.colorspace_settings.name = colorspace
    return img


def _eval_materials(obj):
    """The materials Cycles sees on the object: its slots plus the projection's GN material."""
    mats = [s.material for s in obj.material_slots if s.material]
    mod = common.cp_modifier(obj)
    if mod is not None and mod.show_render and mod.node_group:
        try:
            m = cp.get_input(mod, "Material")
        except (KeyError, AttributeError):
            m = None
        if m is not None:
            mats.append(m)
    return list(dict.fromkeys(mats))


@contextmanager
def target_nodes(obj, image):
    """A temporary active Image Texture node on `image` in every material the bake sees."""
    added = []
    try:
        for mat in _eval_materials(obj):
            mat.use_nodes = True
            nt = mat.node_tree
            n = nt.nodes.new("ShaderNodeTexImage")
            n.name = TMP_NODE
            n.image = image
            n.location = (-1200, 1200)
            nt.nodes.active = n
            added.append((nt, n))
        yield
    finally:
        for nt, n in added:
            try:
                nt.nodes.remove(n)
            except (ReferenceError, RuntimeError):
                pass


def configure(scene, bake_type):
    s = common.settings(scene)
    scene.render.engine = 'CYCLES'
    scene.cycles.device = s.device
    scene.cycles.samples = s.anti_alias
    scene.cycles.bake_type = bake_type
    b = scene.render.bake
    b.target = 'IMAGE_TEXTURES'
    b.use_clear = True
    b.margin = s.margin
    b.margin_type = 'EXTEND'
    b.use_selected_to_active = False
    if bake_type == 'DIFFUSE':
        b.use_pass_direct = False
        b.use_pass_indirect = False
        b.use_pass_color = True


def save_png8(img, path):
    """8-bit PNG at `path`, always the same file; the image then points at it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save(filepath=path)
    link_file(img, path)


def link_file(img, path):
    """Point the image at its file (relative when the .blend is saved) and reload."""
    rel = bpy.path.relpath(path) if bpy.data.filepath else path
    img.source = 'FILE'
    img.filepath = rel
    img.reload()


def bake_albedo(context, obj, progress=None):
    """Bake one object's projection into ALB_<name>, save it, build MAT_, store the
    fingerprint. The object ends up in Final."""
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    t0 = time.perf_counter()
    uvs = obj.data.uv_layers
    gn_final.set_final(obj, scene, False)
    context.view_layer.update()
    prev = uvs.active.name if uvs.active else ""
    uvs.active = uvs[common.UV_NORMAL]
    name = common.alb_name(obj)
    path = common.texture_path(scene, name)
    # bake into a fresh 8-bit image nothing else uses: the ALB image itself may hold a float
    # buffer (GN-Final samples it for "Sampled from ALB"), and a float buffer saves as 16-bit
    tmp = bpy.data.images.new(TMP_IMAGE, s.resolution, s.resolution, alpha=False,
                              float_buffer=False)
    tmp.colorspace_settings.name = 'sRGB'
    try:
        with render_state(scene), selection(context, obj, [obj]), target_nodes(obj, tmp):
            configure(scene, 'DIFFUSE')
            with context.temp_override(active_object=obj, object=obj,
                                       selected_objects=[obj], selected_editable_objects=[obj]):
                bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, margin=s.margin,
                                    use_clear=True, target='IMAGE_TEXTURES',
                                    uv_layer=common.UV_NORMAL)
        if progress:
            progress()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp.filepath_raw = path
        tmp.file_format = 'PNG'
        tmp.save(filepath=path)
    finally:
        bpy.data.images.remove(tmp)
        if uvs.get(prev):
            uvs.active = uvs[prev]
    # ALB_<name>: the same image every time (no .001), pointing at the new file
    img = d.alb_image or bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name
    elif img.name != name and not bpy.data.images.get(name):
        img.name = name
    img.colorspace_settings.name = 'sRGB'
    link_file(img, path)
    d.alb_image = img
    d.alb_size = s.resolution
    material.build(obj, scene)
    d.fingerprint = fingerprint.compute(obj)
    d.last_bake_seconds = time.perf_counter() - t0
    gn_final.set_final(obj, scene, True)
    return d.last_bake_seconds


def bake_normal_from_mesh(context, obj, source, img):
    """Cycles NORMAL, tangent space, Selected to Active from `source` onto `obj`, into the
    float image `img` (pixels stay in memory for the caller to save)."""
    scene = context.scene
    s = common.settings(scene)
    uvs = obj.data.uv_layers
    gn_final.set_final(obj, scene, False)
    context.view_layer.update()
    prev = uvs.active.name if uvs.active else ""
    uvs.active = uvs[common.UV_NORMAL]
    try:
        with render_state(scene), selection(context, obj, [obj, source]), target_nodes(obj, img):
            configure(scene, 'NORMAL')
            b = scene.render.bake
            b.use_selected_to_active = True
            b.use_cage = False
            b.cage_extrusion = s.cage_extrusion
            b.normal_space = 'TANGENT'
            b.normal_r, b.normal_g, b.normal_b = 'POS_X', 'POS_Y', 'POS_Z'     # OpenGL, Y+
            with context.temp_override(active_object=obj, object=obj,
                                       selected_objects=[obj, source],
                                       selected_editable_objects=[obj, source]):
                bpy.ops.object.bake(type='NORMAL', margin=s.margin, use_clear=True,
                                    use_selected_to_active=True, cage_extrusion=s.cage_extrusion,
                                    normal_space='TANGENT', target='IMAGE_TEXTURES',
                                    uv_layer=common.UV_NORMAL)
    finally:
        if uvs.get(prev):
            uvs.active = uvs[prev]
