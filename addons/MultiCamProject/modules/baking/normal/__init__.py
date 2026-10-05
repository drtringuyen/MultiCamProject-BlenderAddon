"""NOR_<name>: 16-bit, Non-Color, OpenGL (Y+), always <output dir>/NOR_<name>.png, by the
object's Bake Route:

    From Original    a copy of BNo_ (baked from the Bake Source), or generated from BAo_
                     (High-pass / AI) when the object's Normal from Original says so
    From Projection  a copy of BNp_ - generated from BAp_ (the projection's albedo)
    Mixed            BNo_ -> BNp_ by the VCMix mask (a plain mix)

BNp_ comes from BAp_: High-pass (Lite) or the AI model (Full build, onnxruntime). It is
made again only when BAp_ or the normal settings changed."""
import os
import time

import bpy

from .. import common, engine, gn_final as final, jobs, material
from . import ai, highpass, pngio

LABELS = {'HIGHPASS': "High-pass", 'AI': "AI", 'ORIGINAL': "BNo_ copy",
          'MESH': "Bake from mesh", 'BLEND': "Mesh + Albedo"}     # the last two: older runs


def is_full():
    """Full build (ships the models folder), or the AI was set up by hand."""
    return os.path.isdir(ai.MODELS) or ai.available()


def available_sources():
    """How the albedo's detail is made (the Bake Source's BN_ is used by itself)."""
    out = [('HIGHPASS', LABELS['HIGHPASS'], "Relief from the albedo's brightness (fast)")]
    if is_full() and ai.available():
        out.append(('AI', LABELS['AI'], "Detail predicted from the albedo by an AI model"))
    return out


def usable(source):
    """`source`, or High-pass when it is not available (e.g. an older 'Bake from mesh')."""
    return source if source in {k for k, _l, _d in available_sources()} else 'HIGHPASS'


def problem(obj, scene, source):
    """Why NOR_ cannot be made for `obj` ('' = it can). What the route needs first (BAo_ /
    BNo_ / BAp_) is baked by the Normal itself."""
    from .. import route
    return route.problem(obj)


def _read_resized(img, size):
    """RGB float32 (size, size, 3) of an image, rows bottom-up (a scaled copy when needed)."""
    if tuple(img.size) == (size, size):
        return highpass.read_pixels(img).copy()
    tmp = img.copy()
    try:
        tmp.scale(size, size)
        return highpass.read_pixels(tmp).copy()
    finally:
        bpy.data.images.remove(tmp)


def compose(context, obj, detail, size):
    """compose_steps, blocking."""
    return jobs.run_sync(compose_steps(context, obj, detail, size))


def compose_steps(context, obj, size):
    """Mixed: BNo_ -> BNp_ by the VCMix mask (MCP_'s Blend Mix factor), renormalized.
    A generator (jobs); returns RGB (size, size, 3)."""
    import numpy as np
    d = common.data(obj)
    base = _read_resized(d.bn_image, size)
    mask = yield from engine.bake_mask_steps(context, obj, size)
    yield jobs.Step("Normal: BNo_ -> BNp_ by the mask")
    top = _read_resized(d.bnp_image, size)
    if mask is None:            # no projection after all: the original's normals alone
        return base
    m = mask[..., None]
    out = base * (np.float32(1.0) - m) + top * m
    del base, top
    n = out * np.float32(2.0) - np.float32(1.0)
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
    return (n * 0.5 + 0.5).astype(np.float32)


def _bnp_state(obj, source, s):
    """What BNp_ depends on: BAp_ (its stamp) and the normal settings."""
    d = common.data(obj)
    return (f"{d.bp_fingerprint}|{source}|{round(s.nor_strength, 4)}|{s.nor_radius}|"
            f"{s.nor_invert}|{common.resolution(obj)}")


def bnp_steps(context, obj, source):
    """BNp_<name>: the normal generated from BAp_ (High-pass / AI), a 16-bit file in the bake
    folder - kept while BAp_ and the settings stay. A generator (jobs)."""
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    size = common.resolution(obj, scene)
    state = _bnp_state(obj, source, s)
    if d.bnp_image is not None and d.bnp_fingerprint == state and common.file_ok(d.bnp_image):
        return
    yield jobs.Step(f"Normal: {LABELS[source]} from BAp_ (BNp_)")
    if source == 'AI':
        rgb = ai.generate(d.bap_image, size)
    else:
        rgb = highpass.generate(d.bap_image, size, s)
    yield jobs.Step("Normal: writing BNp_ (16-bit)")
    d.bnp_image = engine.store_work(scene, rgb, d.bnp_image, common.bnp_name(obj), normal=True)
    d.bnp_fingerprint = state


def record_time(d, source, size, seconds):
    """Last time per source and size, to compare the sources (JSON on the object)."""
    import json
    try:
        times = json.loads(d.nor_times or "{}")
    except ValueError:
        times = {}
    times[f"{source}@{size}"] = round(seconds, 2)
    d.nor_times = json.dumps(times)


def times(d):
    """[(label, size, seconds)] of the recorded normal map runs."""
    import json
    try:
        raw = json.loads(d.nor_times or "{}")
    except ValueError:
        return []
    out = []
    for key, sec in raw.items():
        src, _sep, size = key.partition("@")
        out.append((LABELS.get(src, src), int(size or 0), sec))
    return sorted(out, key=lambda t: (-t[1], t[0]))


def generate(context, obj, source):
    """generate_steps, blocking."""
    return jobs.run_sync(generate_steps(context, obj, source))


def generate_steps(context, obj, source):
    """Make NOR_<name> by the object's Bake Route (see the module doc) at its resolution,
    baking what the route needs first (BAo_ / BNo_, BAp_, BNp_), and put it into MAT_.
    A generator (jobs); returns the seconds."""
    from .. import route
    source = usable(source)
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    why = route.problem(obj)
    if why:
        raise RuntimeError(why)
    r = route.get(obj)
    size = common.resolution(obj, scene)
    t0 = time.perf_counter()
    name = common.nor_name(obj)
    path = common.texture_path(scene, name)
    if engine.needs_source_bake(obj):
        yield jobs.Step("Bake from Source", 0.0)
        yield from engine.bake_from_source_steps(context, obj)
    if engine.needs_projection_bake(obj):
        yield from engine.bake_projection_steps(context, obj)
    if r in {route.PROJECTION, route.MIXED}:
        yield from bnp_steps(context, obj, source)
    generated = r == route.ORIGINAL and d.original_normal == 'GENERATED'
    if r == route.ORIGINAL and not generated:
        yield jobs.Step(f"Normal: copying BNo_ to {os.path.basename(path)}")
        d.nor_image = engine.copy_file(scene, d.bn_image, name, d.nor_image, normal=True)
    elif r == route.PROJECTION:
        yield jobs.Step(f"Normal: copying BNp_ to {os.path.basename(path)}")
        d.nor_image = engine.copy_file(scene, d.bnp_image, name, d.nor_image, normal=True)
    else:
        if generated:           # From Original, generated from the original's colors
            yield jobs.Step(f"Normal: {LABELS[source]} from BAo_")
            rgb = (ai.generate(d.ba_image, size) if source == 'AI'
                   else highpass.generate(d.ba_image, size, s))
        else:
            was_final = final.is_final(obj)
            try:
                rgb = yield from compose_steps(context, obj, size)
            finally:
                if final.is_final(obj) != was_final:
                    final.set_final(obj, scene, was_final)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        yield jobs.Step(f"Normal: writing {os.path.basename(path)} (16-bit)")
        pngio.write_rgb16(path, rgb, s.png_compression)
        del rgb
        img = d.nor_image or bpy.data.images.get(name)
        if img is None:
            img = bpy.data.images.load(path, check_existing=False)
            img.name = name
        elif img.name != name and not bpy.data.images.get(name):
            img.name = name
        img.colorspace_settings.name = 'Non-Color'     # before the reload: raw values
        engine.link_file(img, path)
        d.nor_image = img
    d.nor_size = size
    d.nor_source_used = 'ORIGINAL' if r == route.ORIGINAL and not generated else source
    d.last_nor_seconds = time.perf_counter() - t0
    record_time(d, d.nor_source_used, size, d.last_nor_seconds)
    material.build(obj, scene)
    final.write_inputs(obj, scene)
    if route.has_projection(obj):
        from ...camera_project import core as cp
        cp.ensure_material(obj)         # MCP_ shows the new BNp_
    return d.last_nor_seconds
