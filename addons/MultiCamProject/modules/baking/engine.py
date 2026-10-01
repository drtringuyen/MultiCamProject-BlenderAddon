"""Bakes: BA_ / BN_ from the Bake Source (04, Cycles), ALB_ and the blend mask from the
object's own Processing material (06, EEVEE).

Only Bake from Source needs Cycles: it casts rays from the low poly onto the high poly. What
the object shows on its own uv_normal needs no rays - EEVEE renders a copy of the evaluated
object laid out in UV space (uv_render), much faster than a Cycles bake.

The render/bake settings save + restore and the image preparation follow BakeLab 2
(GPL-3, Shahzod Boyxonov) - only this small subset is ported.

Every object is baked in its own bpy.ops.object.bake call: a shared material can only
point its active image node at one object's image at a time.
"""
import os
import tempfile
import time
from contextlib import contextmanager

import bpy
import numpy as np

from ..camera_project import core as cp
from . import common, fingerprint, gn_final, jobs, material

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
    b.margin = common.margin_px(s)
    b.margin_type = 'EXTEND'
    b.use_selected_to_active = False


def save_png8(img, path):
    """8-bit PNG at `path`, always the same file; the image then points at it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save(filepath=path)
    link_file(img, path)


def link_file(img, path):
    """Point the image at its file (relative when the .blend is saved) and reload."""
    try:
        rel = bpy.path.relpath(path) if bpy.data.filepath else path
    except ValueError:          # another drive than the .blend: no relative path
        rel = path
    img.source = 'FILE'
    img.filepath = rel
    img.reload()


SUBDIV = "MCP_BAKE_SUBDIV"
SUBDIV_BUDGET = 2_000_000       # faces the temporary subdivision may make


def subdiv_levels(obj):
    """Simple subdivision levels for the bake: 2 on a low poly, fewer on a dense mesh."""
    n = max(len(obj.data.polygons), 1)
    levels = 0
    while levels < 2 and n * 4 ** (levels + 1) <= SUBDIV_BUDGET:
        levels += 1
    return levels


@contextmanager
def subdivided(context, obj):
    """A temporary Simple Subdivision right before GN-CameraProject. The photos are placed
    per vertex, and a perspective camera's UV is not linear across a big triangle: on a
    low poly the photo would slide. More vertices keep it in place (uv_normal and the
    VCMix layers only get linear in-between values)."""
    cpm = common.cp_modifier(obj)
    levels = subdiv_levels(obj)
    if cpm is None or levels == 0:
        yield
        return
    m = obj.modifiers.new(SUBDIV, 'SUBSURF')
    m.subdivision_type = 'SIMPLE'
    m.levels = m.render_levels = levels
    if hasattr(m, "uv_smooth"):
        m.uv_smooth = 'NONE'
    name = m.name
    with context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_move_to_index(modifier=name, index=list(obj.modifiers).index(cpm))
    context.view_layer.update()
    try:
        yield
    finally:
        m = obj.modifiers.get(name)
        if m is not None:
            obj.modifiers.remove(m)
        context.view_layer.update()


def _bake(context, obj, selected, bake_type, size, image, **kw):
    """One bpy.ops.object.bake into `image` (the one of target_nodes), on uv_normal - a
    generator: yields the jobs.BakeJob (`yield from` it)."""
    s = common.settings(context.scene)
    yield jobs.BakeJob(context, obj, selected, image,
                       dict(type=bake_type, margin=common.margin_px(s, size), use_clear=True,
                            target='IMAGE_TEXTURES', uv_layer=common.UV_NORMAL, **kw))


def _uv_normal_active(obj):
    """uv_normal active while baking (the previous active UV comes back)."""
    uvs = obj.data.uv_layers
    prev = uvs.active.name if uvs.active else ""
    uvs.active = uvs[common.UV_NORMAL]
    return prev


def _uv_restore(obj, prev):
    uvs = obj.data.uv_layers
    if uvs.get(prev):
        uvs.active = uvs[prev]


def _work_image(name, size, colorspace):
    """A fresh generated image to bake a work texture into (a packed image keeps its old
    packed pixels when packed again, so a re-bake never goes into the old one)."""
    img = bpy.data.images.new(name + "_MCP_TMP", size, size, alpha=False, float_buffer=False)
    img.colorspace_settings.name = colorspace
    return img


def _pack_as(img, old, name):
    """Pack the baked `img` (PNG) and let it take `old`'s place: every user (MCP_'s node,
    the object's pointer) moves over, `old` goes, `img` gets the name - the same BA_/BN_
    name every time, never a .001."""
    img.file_format = 'PNG'
    img.pack()
    if old is not None and old != img:
        old.user_remap(img)
        bpy.data.images.remove(old)
    other = bpy.data.images.get(name)
    if other is not None and other != img:
        other.name = name + "_stale"
    img.name = name
    return img


# ---------------------------------------------------------------- EEVEE: the object on its uv_normal

UV_TMP = "MCP_UV_RENDER_TMP"
_OFFS = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _flat_group():
    """GN: every face on its own, each corner at its uv_normal (u, v, 0) - the mesh laid out
    in UV space, every attribute kept."""
    ng = bpy.data.node_groups.new(UV_TMP, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    split = nodes.new("GeometryNodeSplitEdges")         # one corner per vertex
    uv = nodes.new("GeometryNodeInputNamedAttribute")
    uv.data_type = 'FLOAT_VECTOR'
    uv.inputs["Name"].default_value = common.UV_NORMAL
    pos = nodes.new("GeometryNodeSetPosition")
    links.new(gi.outputs[0], split.inputs["Mesh"])
    links.new(split.outputs[0], pos.inputs["Geometry"])
    links.new(uv.outputs["Attribute"], pos.inputs["Position"])
    links.new(pos.outputs[0], go.inputs[0])
    return ng


def uv_render(context, obj, size, emit=True):
    """obj as its materials show it, rendered by EEVEE on uv_normal: (size, size, 4) linear
    floats, rows bottom-up, alpha > 0 where a face covers the pixel. `emit`: every material
    shows its color as Emission (the Diffuse Color a Cycles bake would read); else the
    materials must already show what to read (the blend mask).
    A copy of the evaluated object (projection, temporary subdivision) renders in a scene of
    its own: nothing of the user's scene renders along, obj is not touched."""
    s = common.settings(context.scene)
    dg = context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True,
                                         depsgraph=dg)
    sc = bpy.data.scenes.new(UV_TMP)
    flat = bpy.data.objects.new(UV_TMP, me)
    cam_data = bpy.data.cameras.new(UV_TMP)
    cam = bpy.data.objects.new(UV_TMP + "_cam", cam_data)
    ng = _flat_group()
    fd, path = tempfile.mkstemp(".exr", "mcp_uv_")
    os.close(fd)
    mats = list(dict.fromkeys(m for m in me.materials if m is not None))
    culled = [m for m in mats if m.use_backface_culling]      # mirrored islands face away
    img = None
    try:
        if me.attributes.get(common.UV_NORMAL) is None:
            raise RuntimeError(f"No '{common.UV_NORMAL}' after the modifiers")
        for m in culled:
            m.use_backface_culling = False
        sc.collection.objects.link(flat)
        sc.collection.objects.link(cam)
        flat.modifiers.new("UV", 'NODES').node_group = ng
        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = 1.0
        cam_data.clip_start, cam_data.clip_end = 0.1, 10.0
        cam.location = (0.5, 0.5, 1.0)          # looks down on UV 0-1
        sc.camera = cam
        r = sc.render
        r.engine = 'BLENDER_EEVEE'
        r.resolution_x = r.resolution_y = size
        r.resolution_percentage = 100
        r.film_transparent = True
        r.filter_size = 0.0                     # one texel = its own color, like a bake
        r.use_motion_blur = False
        r.use_compositing = r.use_sequencer = False
        r.image_settings.file_format = 'OPEN_EXR'
        r.image_settings.color_depth = '32'
        r.filepath = path
        sc.eevee.taa_render_samples = s.anti_alias
        v = sc.view_settings
        v.view_transform, v.look, v.exposure, v.gamma = 'Standard', 'None', 0.0, 1.0
        if emit:
            with emission_colors(mats, 'EEVEE'):
                bpy.ops.render.render(write_still=True, scene=sc.name)
        else:
            bpy.ops.render.render(write_still=True, scene=sc.name)
        img = bpy.data.images.load(path, check_existing=False)
        px = np.empty(size * size * 4, np.float32)
        img.pixels.foreach_get(px)
    finally:
        for m in culled:
            m.use_backface_culling = True
        if img is not None:
            bpy.data.images.remove(img)
        bpy.data.objects.remove(flat)
        bpy.data.objects.remove(cam)
        bpy.data.meshes.remove(me)
        bpy.data.cameras.remove(cam_data)
        bpy.data.node_groups.remove(ng)
        bpy.data.scenes.remove(sc)
        try:
            os.remove(path)
        except OSError:
            pass
    px = px.reshape(size, size, 4)
    a = px[..., 3]
    part = (a > 0.0) & (a < 1.0)            # an island's edge: premultiplied -> its own color
    px[part, :3] /= a[part, None]
    return px


def extend_margin(px, covered, margin):
    """Cycles' EXTEND margin: `margin` rings of uncovered pixels around the islands take a
    covered neighbour's value. px (h, w, c) and covered (h, w) change in place. Only the
    islands' borders are touched: about 1 s at 8K."""
    h, w = covered.shape
    flat = px.reshape(h * w, -1)
    cov = covered.reshape(-1)
    edge = np.zeros_like(covered)           # covered pixels next to an uncovered one
    edge[:, :-1] |= ~covered[:, 1:]
    edge[:, 1:] |= ~covered[:, :-1]
    edge[:-1] |= ~covered[1:]
    edge[1:] |= ~covered[:-1]
    front = np.flatnonzero(edge & covered)
    del edge
    for _ in range(margin):
        if not len(front):
            break
        y, x = np.divmod(front, w)
        dst, src = [], []
        for dy, dx in _OFFS:
            yy, xx = y + dy, x + dx
            ok = (yy >= 0) & (yy < h) & (xx >= 0) & (xx < w)
            n = yy[ok] * w + xx[ok]
            free = ~cov[n]
            dst.append(n[free])
            src.append(front[ok][free])
        dst = np.concatenate(dst)
        src = np.concatenate(src)
        dst, first = np.unique(dst, return_index=True)
        flat[dst] = flat[src[first]]
        cov[dst] = True
        front = dst


def to_srgb(px, rows=1024):
    """Linear -> sRGB-encoded (0-1, what an 8-bit sRGB image stores) in place, a block of
    rows at a time (no 8K-sized temporaries)."""
    for y in range(0, px.shape[0], rows):
        blk = px[y:y + rows]
        np.clip(blk, 0.0, 1.0, out=blk)
        blk[:] = np.where(blk <= 0.0031308, blk * 12.92,
                          1.055 * np.power(blk, 1.0 / 2.4) - 0.055)


# ---------------------------------------------------------------- 04 Bake from Source

SOURCE_EMIT = "MCP_SOURCE_EMIT_TMP"


def _color_socket(nt):
    """The color a scan material shows: a Principled BSDF's linked Base Color, else a
    linked Emission Color, else (None, a Base Color / Emission default value)."""
    nodes = list(nt.nodes)
    for kind, sock in (('BSDF_PRINCIPLED', "Base Color"), ('EMISSION', "Color")):
        for n in nodes:
            if n.type == kind and n.inputs[sock].links:
                return n.inputs[sock].links[0].from_socket, None
    for kind, sock in (('BSDF_PRINCIPLED', "Base Color"), ('EMISSION', "Color")):
        n = next((n for n in nodes if n.type == kind), None)
        if n is not None:
            return None, tuple(n.inputs[sock].default_value)
    return None, (0.8, 0.8, 0.8, 1.0)


def _used_materials(obj):
    """The materials obj's faces use (by material index)."""
    me = obj.data
    mi = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("material_index", mi)
    slots = obj.material_slots
    return list(dict.fromkeys(slots[i].material for i in np.unique(mi).tolist()
                              if i < len(slots) and slots[i].material is not None))


def _output(nt, target):
    """The Material Output `target` ('CYCLES' / 'EEVEE') renders with: the active one among
    those for All / target, else the first of those, else any."""
    outs = [n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL']
    mine = [n for n in outs if n.target in {'ALL', target}]
    return (next((n for n in mine if n.is_active_output), None) or (mine[0] if mine else None)
            or (outs[0] if outs else None))


def source_colors(src):
    """Every material the source's faces use shows its color as an Emission for one EMIT
    bake: scans are often unlit (image -> Emission, BSDF unconnected), which a Diffuse
    Color bake reads as black."""
    return emission_colors(_used_materials(src), 'CYCLES')


@contextmanager
def emission_colors(mats, target):
    """Every material of `mats` shows its color (_color_socket) as an Emission to `target`'s
    output - read unlit by an EMIT bake or an EEVEE render. The materials are put back after."""
    changed = []
    try:
        for mat in mats:
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            nt = mat.node_tree
            out = _output(nt, target)
            if out is None:
                continue
            surface = out.inputs["Surface"]
            old = surface.links[0].from_socket if surface.links else None
            sock, value = _color_socket(nt)
            emit = nt.nodes.new("ShaderNodeEmission")
            emit.name = SOURCE_EMIT
            emit.inputs["Strength"].default_value = 1.0
            if sock is not None:
                nt.links.new(sock, emit.inputs["Color"])
            else:
                emit.inputs["Color"].default_value = value
            nt.links.new(emit.outputs[0], surface)
            changed.append((nt, emit, surface, old))
        yield
    finally:
        for nt, emit, surface, old in changed:
            try:
                nt.nodes.remove(emit)
                if old is not None:
                    nt.links.new(old, surface)
            except (ReferenceError, RuntimeError):
                pass

def source_problem(obj, context=None):
    """Why Bake from Source cannot run for `obj` ('' = it can)."""
    context = context or bpy.context
    src = common.data(obj).bake_source
    if src is None:
        return "No Bake Source (0C Remesh, or pick the high poly)"
    if src == obj or src.type != 'MESH':
        return "The Bake Source must be another mesh"
    if context.scene.objects.get(src.name) != src:     # excluded is fine: shown for the bake
        return f"'{src.name}' is not in this scene"
    if not common.has_uv_normal(obj):
        return f"No '{common.UV_NORMAL}' UV map - unwrap the low poly first"
    return ""


def bake_from_source(context, obj):
    """bake_from_source_steps, blocking."""
    return jobs.run_sync(bake_from_source_steps(context, obj))


def bake_from_source_steps(context, obj):
    """BA_ (the source's colors) and BN_ (its surface, tangent normals) onto obj's
    uv_normal - Selected to Active from the Bake Source, at the scene's resolution. Both stay
    packed in the .blend and are overwritten on the next bake; MCP_ shows them under the
    projection. The low poly and its source are shown for the whole time (a Remesh original
    usually sits in an excluded collection). A generator (jobs); returns the seconds."""
    why = source_problem(obj, context)
    if why:
        raise RuntimeError(why)
    with common.shown(context, [obj, common.data(obj).bake_source]):
        return (yield from _bake_from_source(context, obj))


def _bake_from_source(context, obj):
    from .normal import mesh_bake
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    why = source_problem(obj, context)
    if why:
        raise RuntimeError(why)
    t0 = time.perf_counter()
    src = d.bake_source
    size = common.resolution(obj, scene)    # one resolution: BA_ / BN_ match ALB_ / NOR_
    try:
        from ..remesh import workflow
        workflow.clear_custom_normals(obj)      # they would aim the rays anywhere
    except ImportError:
        pass
    d.ba_far_share, d.ba_far_max, d.ba_fit_cage = source_distance(obj, src)
    was_final = gn_final.is_final(obj)
    ba = _work_image(common.ba_name(obj), size, 'sRGB')
    bn = _work_image(common.bn_name(obj), size, 'Non-Color')
    prev = _uv_normal_active(obj)
    try:
        with render_state(scene), \
                selection(context, obj, [obj, src]):
            # the source's colors as Emission (lit or unlit scan alike), baked as EMIT
            with source_colors(src), target_nodes(obj, ba):
                configure(scene, 'EMIT')
                yield jobs.Step("Bake from Source: colors (BA_)")
                yield from _bake(context, obj, [obj, src], 'EMIT', size, ba,
                                 use_selected_to_active=True, cage_extrusion=s.cage_extrusion)
            with mesh_bake.smoothed_source(context, src, s) as hp, target_nodes(obj, bn):
                configure(scene, 'NORMAL')
                b = scene.render.bake
                b.normal_space = 'TANGENT'
                b.normal_r, b.normal_g, b.normal_b = 'POS_X', 'POS_Y', 'POS_Z'     # OpenGL, Y+
                sel = [obj, hp]
                hp.select_set(True)
                yield jobs.Step("Bake from Source: surface (BN_)")
                yield from _bake(context, obj, sel, 'NORMAL', size, bn, use_selected_to_active=True,
                                 cage_extrusion=s.cage_extrusion, normal_space='TANGENT')
    except Exception:
        bpy.data.images.remove(ba)      # the old BA_ / BN_ stay as they were
        bpy.data.images.remove(bn)
        raise
    finally:
        _uv_restore(obj, prev)
        if gn_final.is_final(obj) != was_final:
            gn_final.set_final(obj, scene, was_final)
    old_ba = d.ba_image or bpy.data.images.get(common.ba_name(obj))
    old_bn = d.bn_image or bpy.data.images.get(common.bn_name(obj))
    d.ba_image = _pack_as(ba, old_ba, common.ba_name(obj))
    d.bn_image = _pack_as(bn, old_bn, common.bn_name(obj))
    d.ba_size = size
    d.ba_fingerprint = fingerprint.stamp_source(obj)
    d.last_ba_seconds = time.perf_counter() - t0
    if hasattr(obj, "multicamproject_cam") and obj.multicamproject_cam.is_setup:
        cp.build_material(obj)          # the BAKED frame shows the new BA_ / BN_
    return d.last_ba_seconds


def source_distance(obj, src, samples=4000):
    """(share farther than the Cage, largest distance, the Cage that reaches 99%) from the
    low poly's vertices to the source's surface. Rays start Cage outside the low poly: a
    part farther away than that misses the source (black in BA_) or hits the wrong side."""
    from mathutils.bvhtree import BVHTree
    s = common.settings(bpy.context.scene)
    dg = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(src.evaluated_get(dg), dg)
    me = obj.data
    n = len(me.vertices)
    if not n:
        return 0.0, 0.0, s.cage_extrusion
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)[np.linspace(0, n - 1, min(samples, n)).astype(int)]
    m = np.array(src.matrix_world.inverted() @ obj.matrix_world, np.float32)
    co = co @ m[:3, :3].T + m[:3, 3]
    dist = np.array([(h[3] if (h := tree.find_nearest(p))[0] is not None else 1e9)
                     for p in co.tolist()], np.float32)
    scale = max(src.matrix_world.to_scale())        # the source's local units -> world
    dist *= scale
    return (float((dist > s.cage_extrusion).mean()), float(dist.max()),
            float(np.percentile(dist, 99)) * 1.1)


def needs_source_bake(obj):
    """A Bake Source without a current BA_: Bake Final bakes from the source first."""
    d = common.data(obj)
    return d.bake_source is not None and (d.ba_image is None or fingerprint.ba_outdated(obj))


# ---------------------------------------------------------------- 06 Bake Final

def _save_albedo(obj, scene, tmp, path):
    """tmp (8-bit) into ALB_<name>.png; ALB_ is the same image every time, on that file."""
    d = common.data(obj)
    name = common.alb_name(obj)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp.filepath_raw = path
    tmp.file_format = 'PNG'
    tmp.save(filepath=path)
    img = d.alb_image or bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name
    elif img.name != name and not bpy.data.images.get(name):
        img.name = name
    img.colorspace_settings.name = 'sRGB'
    link_file(img, path)
    d.alb_image = img


def bake_albedo(context, obj):
    """bake_albedo_steps, blocking."""
    return jobs.run_sync(bake_albedo_steps(context, obj))


def bake_albedo_steps(context, obj):
    """ALB_<name>: the Processing material (MCP_: BA_ and the projection, by the mask)
    rendered by EEVEE on uv_normal (uv_render), with a temporary subdivision against
    sliding photos.
    Without a projection ALB_ is BA_ at the final resolution. Builds MAT_, stores the
    fingerprint; the object ends up in Final. A generator (jobs); returns the seconds."""
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    size = common.resolution(obj, scene)
    t0 = time.perf_counter()
    name = common.alb_name(obj)
    path = common.texture_path(scene, name)
    # bake into a fresh 8-bit image nothing else uses: the ALB image itself may hold a float
    # buffer (GN-Final samples it for "Sampled from ALB"), and a float buffer saves as 16-bit
    tmp = bpy.data.images.new(TMP_IMAGE, size, size, alpha=False,
                              float_buffer=False)
    tmp.colorspace_settings.name = 'sRGB'
    try:
        if common.cp_modifier(obj) is None:
            if d.ba_image is None:
                raise RuntimeError("Nothing to bake: no projection and no Bake from Source")
            src = d.ba_image.copy()
            try:
                src.scale(size, size)
                buf = np.empty(size * size * 4, dtype=np.float32)
                src.pixels.foreach_get(buf)
                tmp.pixels.foreach_set(buf)
            finally:
                bpy.data.images.remove(src)
        else:
            missing = cp.fill_cam_images(obj)       # never bake an empty (pink) Cam texture
            if missing:
                raise RuntimeError(f"Camera slot(s) {', '.join(map(str, missing))} have no "
                                   "image - it would bake pink (Reload All / pick the photo)")
            gn_final.set_final(obj, scene, False)
            context.view_layer.update()
            yield jobs.Step("Albedo: EEVEE render")
            with subdivided(context, obj):
                px = uv_render(context, obj, size)
            yield jobs.Step("Albedo: margin")
            covered = px[..., 3] > 0.0
            px[..., 3] = 1.0
            to_srgb(px[..., :3])
            extend_margin(px, covered, common.margin_px(s, size))
            del covered
            tmp.pixels.foreach_set(px.ravel())
            del px
        yield jobs.Step(f"Albedo: writing {os.path.basename(path)}")
        _save_albedo(obj, scene, tmp, path)
    finally:
        bpy.data.images.remove(tmp)
    d.alb_size = size
    material.build(obj, scene)
    d.fingerprint = fingerprint.compute(obj)
    d.last_bake_seconds = time.perf_counter() - t0
    gn_final.set_final(obj, scene, True)
    return d.last_bake_seconds


MASK_EMIT = "MCP_MASK_EMIT_TMP"


def bake_mask(context, obj, size):
    """bake_mask_steps, blocking."""
    return jobs.run_sync(bake_mask_steps(context, obj, size))


def bake_mask_steps(context, obj, size):
    """The blend mask (0 = baked, 1 = projected) on uv_normal as (size, size) floats, rows
    bottom-up - MCP_'s mask sent to an Emission, rendered by EEVEE on uv_normal (uv_render).
    None without a projection. A generator (jobs)."""
    scene = context.scene
    s = common.settings(scene)
    mat = cp.data(obj).material if hasattr(obj, "multicamproject_cam") else None
    nt = mat.node_tree if mat is not None else None
    mix = nt.nodes.get("Blend Mix") if nt is not None else None
    out = nt.nodes.get("Material Output") if nt is not None else None
    if common.cp_modifier(obj) is None or mix is None or out is None or not mix.inputs[0].links:
        return None
    mask_out = mix.inputs[0].links[0].from_socket
    surface = out.inputs["Surface"]
    old = surface.links[0].from_socket if surface.links else None
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.name = MASK_EMIT
    was_final = gn_final.is_final(obj)
    try:
        nt.links.new(mask_out, emit.inputs["Color"])
        nt.links.new(emit.outputs[0], surface)
        gn_final.set_final(obj, scene, False)
        context.view_layer.update()
        yield jobs.Step("Normal: blend mask (EEVEE)")
        with subdivided(context, obj):
            px = uv_render(context, obj, size, emit=False)
        mask = np.ascontiguousarray(px[..., :1])
        covered = px[..., 3] > 0.0
        del px
        extend_margin(mask, covered, common.margin_px(s, size))
        return np.clip(mask[..., 0], 0.0, 1.0)
    finally:
        nt.nodes.remove(emit)
        if old is not None:
            nt.links.new(old, surface)
        if gn_final.is_final(obj) != was_final:
            gn_final.set_final(obj, scene, was_final)
