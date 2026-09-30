"""NOR_<name>: 16-bit, Non-Color, OpenGL (Y+), always <output dir>/NOR_<name>.png.

    with BN_ (baked from the Bake Source):  BN_ where the mask says baked, BN_ with the
        albedo's detail on top (Reoriented Normal Mapping) where it says projected
    without BN_:                            the albedo's detail alone

The detail comes from ALB_: High-pass (Lite) or the AI model (Full build, onnxruntime)."""
import os
import time

import bpy

from .. import common, engine, gn_final as final, jobs, material
from . import ai, highpass, pngio

LABELS = {'HIGHPASS': "High-pass", 'AI': "AI",
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
    """Why NOR_ cannot be made for `obj` ('' = it can)."""
    if common.data(obj).alb_image is None:
        return "Bake the albedo first"
    return ""


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


def compose_steps(context, obj, detail, size):
    """BN_ + detail by the blend mask (see the module doc); detail alone without BN_.
    A generator (jobs)."""
    import numpy as np
    from .blend import rnm
    d = common.data(obj)
    if d.bn_image is None:
        return detail
    base = _read_resized(d.bn_image, size)
    mask = yield from engine.bake_mask_steps(context, obj, size)
    yield jobs.Step("Normal: combining with BN_")
    top = rnm(base, detail)
    if mask is None:            # no projection: the source's normals alone
        return base
    m = mask[..., None]
    out = base * (np.float32(1.0) - m) + top * m
    n = out * np.float32(2.0) - np.float32(1.0)
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
    return (n * 0.5 + 0.5).astype(np.float32)


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
    """Make NOR_<name> (detail from `source`) at the scene's resolution, save it, put it
    into MAT_. A generator (jobs); returns the seconds."""
    source = usable(source)
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    was_final = final.is_final(obj)
    size = s.resolution
    t0 = time.perf_counter()
    try:
        yield jobs.Step(f"Normal: {LABELS[source]} from the albedo")
        if source == 'AI':
            rgb = ai.generate(d.alb_image, size)
        else:
            rgb = highpass.generate(d.alb_image, size, s)
        rgb = yield from compose_steps(context, obj, rgb, size)
    finally:
        if final.is_final(obj) != was_final:
            final.set_final(obj, scene, was_final)
    name = common.nor_name(obj)
    path = common.texture_path(scene, name)
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
    d.nor_source_used = source
    d.last_nor_seconds = time.perf_counter() - t0
    record_time(d, source, size, d.last_nor_seconds)
    material.build(obj, scene)
    final.write_inputs(obj, scene)
    return d.last_nor_seconds
