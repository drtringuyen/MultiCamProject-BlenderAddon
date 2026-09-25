"""NOR_<name> sources. Lite: high-pass from the albedo, bake from a mesh. Full adds
Mesh + Albedo and the AI model (onnxruntime). Every source writes the same file:
<output dir>/NOR_<name>.png, 16-bit, Non-Color, OpenGL (Y+)."""
import os
import time

import bpy

from .. import common, engine, gn_final as final, material
from . import ai, highpass, pngio

PREVIEW = 2048
LABELS = {'HIGHPASS': "High-pass", 'MESH': "Bake from mesh", 'BLEND': "Mesh + Albedo",
          'AI': "AI"}


def is_full():
    """Full build (ships the models folder), or the AI was set up by hand."""
    return os.path.isdir(ai.MODELS) or ai.available()


def available_sources():
    out = [('HIGHPASS', LABELS['HIGHPASS'], "Relief from the albedo's brightness (fast)"),
           ('MESH', LABELS['MESH'], "Cycles normal bake from the High Poly mesh")]
    if is_full():
        out.append(('BLEND', LABELS['BLEND'], "Mesh bake + albedo high-pass detail on top"))
        if ai.available():
            out.append(('AI', LABELS['AI'], "Normal map predicted from the albedo by an AI model"))
    return out


def problem(obj, scene, source):
    """Why `source` cannot run for `obj` ('' = it can)."""
    from . import mesh_bake
    d = common.data(obj)
    s = common.settings(scene)
    if source in {'HIGHPASS', 'BLEND', 'AI'} and d.alb_image is None:
        return "Bake the albedo first"
    if source in {'MESH', 'BLEND'}:
        return mesh_bake.problem(obj, s)
    if source not in {k for k, _l, _d in available_sources()}:
        return f"'{LABELS.get(source, source)}' is not available in this build"
    return ""


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


def generate(context, obj, source, preview=False):
    """Make NOR_<name> with `source`, save it, put it into MAT_. Returns the seconds."""
    from . import blend, mesh_bake
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    was_final = final.is_final(obj)
    size = min(PREVIEW, s.resolution) if preview else s.resolution
    t0 = time.perf_counter()
    try:
        if source == 'HIGHPASS':
            rgb = highpass.generate(d.alb_image, size, s)
        elif source == 'MESH':
            rgb = mesh_bake.generate(context, obj, size)
        elif source == 'BLEND':
            rgb = blend.generate(context, obj, d.alb_image, size, s)
        elif source == 'AI':
            rgb = ai.generate(d.alb_image, size)
        else:
            raise ValueError(f"Unknown normal source {source}")
    finally:
        if final.is_final(obj) != was_final:
            final.set_final(obj, scene, was_final)
    name = common.nor_name(obj)
    path = common.texture_path(scene, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
