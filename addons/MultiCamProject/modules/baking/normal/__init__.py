"""NOR_<name> (07 Generate Normal): 16-bit, Non-Color, OpenGL (Y+), in the bake folder.

    High-pass of ALB_ (default)  generated from ALB_ - ALB_<name>_inpaint when it was
                                 inpainted - with High-pass (Lite) or the AI model
    Bake from Scan (picked)      BNo_, the scan's surface (Cycles), where the projection mask
                                 is 0 -> the High-pass of ALB_ where it is 1

The stamp (nor_fingerprint) holds the ALB_ file it was made from: a new ALB_ or a saved
inpaint of it sends the object back to 07.

The older route-based helpers below (BNoG_ from BAo_, BNp_ from BAp_) still feed the
Processing material's normal."""
import os
import time

import bpy

from .. import common, engine, gn_final as final, jobs, material
from . import ai, highpass, pngio

LABELS = {'HIGHPASS': "High-pass", 'AI': "AI", 'ORIGINAL': "BNo_ copy",
          'MESH': "Bake from mesh", 'BLEND': "Mesh + Albedo",     # these two: older runs
          'LOAD': "Loaded"}                                       # TexOverride Load


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
    """Why NOR_ cannot be made for `obj` ('' = it can). A missing / outdated ALB_ is baked
    first by 07, so only the route can stop it - or, loaded (TexOverride), its file."""
    from .. import route
    from ...tex_override import core as tex
    if tex.is_load(obj, 'NOR') and tex.available(scene, obj, 'NOR'):
        return ""
    if tex.is_load(obj, 'ALB') and tex.available(scene, obj, 'ALB'):
        return route.problem(obj) if from_scan(obj) else ""     # from the loaded ALB_
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


def compose_steps(context, obj, size, base=None):
    """Mixed: the original's normal (`base`, else BNo_) -> BNp_ by the VCMix mask (MCP_'s
    Blend Mix factor), renormalized. A generator (jobs); returns RGB (size, size, 3)."""
    import numpy as np
    d = common.data(obj)
    if base is None:
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


def bng_steps(context, obj, source):
    """BNoG_<name>: the original's side generated from BAo_ (High-pass / AI), a 16-bit file in
    the bake folder - kept while BAo_ and the settings stay. A generator (jobs)."""
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    size = common.resolution(obj, scene)
    state = (f"{d.ba_fingerprint}|{source}|{round(s.nor_strength, 4)}|{s.nor_radius}|"
             f"{s.nor_invert}|{size}")
    if d.bng_image is not None and d.bng_fingerprint == state and common.file_ok(d.bng_image):
        return
    yield jobs.Step(f"Normal: {LABELS[source]} from BAo_ (BNoG_)")
    ba = common.bao_source(obj)
    rgb = (ai.generate(ba, size) if source == 'AI'
           else highpass.generate(ba, size, s))
    yield jobs.Step("Normal: writing BNoG_ (16-bit)")
    d.bng_image = engine.store_work(scene, rgb, d.bng_image, common.bng_name(obj), normal=True)
    d.bng_fingerprint = state


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


def reload_from_disk(img):
    """Read a file image again when Blender holds its pixels: a file changed outside Blender
    (Photoshop, Affinity) is otherwise never seen - Blender keeps what it loaded. 'reloaded',
    'unsaved' (edited in Blender, kept: a reload would lose that) or '' (nothing to do)."""
    if img is None or img.source != 'FILE' or img.packed_file or not img.has_data:
        return ""       # not in memory: the next read loads the file anyway
    if img.is_dirty:
        return "unsaved"
    img.reload()
    return "reloaded"


def alb_images(obj):
    """ALB_ and its saved inpaint - what 07 may read."""
    d = common.data(obj)
    return [i for i in (d.alb_image, getattr(d, "alb_inpaint", None)) if i is not None]


def generate(context, obj, source):
    """generate_steps, blocking."""
    return jobs.run_sync(generate_steps(context, obj, source))


def from_scan(obj):
    """07 says Bake from Scan, and the object has a scan to bake from."""
    d = common.data(obj)
    return d.original_normal == 'BAKED' and d.bake_source is not None


def state(obj, scene=None):
    """What NOR_ is made from now: the ALB_ file (its inpaint), the engine and its settings,
    the size, and for Bake from Scan the projection (the mask) and the Bake Source."""
    from .. import fingerprint
    scene = scene or bpy.context.scene
    s = common.settings(scene)
    d = common.data(obj)
    alb = common.alb_source(obj)
    parts = [fingerprint.inpaint_sig(alb), usable(s.nor_source), round(s.nor_strength, 4),
             s.nor_radius, s.nor_invert, common.resolution(obj, scene)]
    if from_scan(obj):
        parts += ["scan", d.fingerprint, d.ba_fingerprint.split(":")[0], round(common.cage(obj), 5)]
    return repr(parts)


def nor_outdated(obj, scene=None):
    """NOR_ was made from another ALB_ / settings than now ('' stamp: older NOR_, left as it
    was). A loaded NOR_ (TexOverride) is never outdated."""
    from .. import fingerprint
    d = common.data(obj)
    if d.nor_mode == 'LOAD' or fingerprint.received(obj):
        return False
    return bool(d.nor_fingerprint) and d.nor_fingerprint != state(obj, scene)


def generate_steps(context, obj, source):
    """07: NOR_<name> from ALB_ (see the module doc) at the object's resolution, into MAT_.
    A generator (jobs); returns the seconds."""
    import numpy as np
    source = usable(source)
    scene = context.scene
    s = common.settings(scene)
    d = common.data(obj)
    size = common.resolution(obj, scene)
    t0 = time.perf_counter()
    name = common.nor_name(obj)
    path = common.texture_path(scene, name)
    from ...tex_override import core as tex
    if tex.loads(scene, obj, 'NOR'):    # TexOverride Load: the file in 02.Overrides, as it is
        yield jobs.Step(f"Normal: Load {os.path.basename(tex.override_path(scene, obj, 'NOR'))}")
        tex.link(scene, obj, 'NOR')
        d.last_nor_seconds = time.perf_counter() - t0
        return d.last_nor_seconds
    alb = common.alb_source(obj)
    if alb is None or not common.file_ok(alb):
        raise RuntimeError("No ALB_ - 05 Bake Albedo first")
    # the file as it is now: NOR_'s stamp is the file's date (state), so pixels Blender
    # loaded before an outside edit would make an old NOR_ look current
    yield jobs.Step(f"Normal: reading {alb.name} from disk")
    reload_from_disk(alb)
    yield jobs.Step(f"Normal: {LABELS[source]} of {alb.name}")
    rgb = (ai.generate(alb, size) if source == 'AI' else highpass.generate(alb, size, s))
    if from_scan(obj):
        if d.bn_stale or d.bn_image is None or not common.file_ok(d.bn_image):
            yield from engine.bake_scan_normal_steps(context, obj)
        was_final = final.is_final(obj)
        try:
            mask = yield from engine.bake_mask_steps(context, obj, size)
        finally:
            if final.is_final(obj) != was_final:
                final.set_final(obj, scene, was_final)
        yield jobs.Step("Normal: BNo_ -> High-pass by the mask")
        base = _read_resized(d.bn_image, size)
        m = mask[..., None] if mask is not None else np.zeros((size, size, 1), np.float32)
        out = base * (np.float32(1.0) - m) + rgb * m
        del base
        n = out * np.float32(2.0) - np.float32(1.0)
        n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
        rgb = (n * 0.5 + 0.5).astype(np.float32)
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
    d.nor_fingerprint = state(obj, scene)
    d.last_nor_seconds = time.perf_counter() - t0
    record_time(d, source, size, d.last_nor_seconds)
    material.build(obj, scene)
    final.write_inputs(obj, scene)
    return d.last_nor_seconds


def generate_route_steps(context, obj, source):
    """(Before 07) NOR_ by the object's Bake Route, baking what the route needs first
    (BAo_ / BNo_, BAp_, BNp_). Kept for reference; 07 uses generate_steps.
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
    if engine.needs_source_bake(obj, normal=common.data(obj).original_normal == 'BAKED'):
        yield jobs.Step("Bake from Source", 0.0)
        yield from engine.bake_from_source_steps(context, obj)
    if engine.needs_projection_bake(obj):
        yield from engine.bake_projection_steps(context, obj)
    if r in {route.PROJECTION, route.MIXED}:
        yield from bnp_steps(context, obj, source)
    # the original's side: its surface (BNo_) or generated from its colors (BAo_)
    generated = r in {route.ORIGINAL, route.MIXED} and d.original_normal == 'GENERATED'
    if generated:
        yield from bng_steps(context, obj, source)
    if r == route.ORIGINAL:
        src_img = d.bng_image if generated else d.bn_image
        yield jobs.Step(f"Normal: copying {src_img.name.split(chr(95))[0]}_ to "
                        f"{os.path.basename(path)}")
        d.nor_image = engine.copy_file(scene, src_img, name, d.nor_image, normal=True)
    elif r == route.PROJECTION:
        yield jobs.Step(f"Normal: copying BNp_ to {os.path.basename(path)}")
        d.nor_image = engine.copy_file(scene, d.bnp_image, name, d.nor_image, normal=True)
    else:
        # Mixed: the original's side (BNo_, or BNoG_ generated from BAo_) -> BNp_
        base = _read_resized(d.bng_image, size) if generated else None
        was_final = final.is_final(obj)
        try:
            rgb = yield from compose_steps(context, obj, size, base)
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
        cp.ensure_material(obj)         # MCP_ shows the new BNp_ / BNoG_
    return d.last_nor_seconds
