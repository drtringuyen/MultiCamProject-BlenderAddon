"""Reset (before Apply: only Seam Fix's own data goes) and Apply (destructive: the bakes
overwrite the original files - copied to _seamfix_backup next to them first - uv_new
becomes uv_normal, every other UV map goes, the materials read the originals again)."""
import os
import shutil
import tempfile
import time

import bpy

from . import geometry as geo
from . import maps, shader

PROPS = ("seamfix_mode", "seamfix_state", "seamfix_report", "seamfix_stale",
         "seamfix_fixed", "seamfix_leveled")


def _remove_image(name):
    img = bpy.data.images.get(name) if name else None
    if img is not None:
        bpy.data.images.remove(img)


def _clear_object(obj, made):
    me = obj.data
    for name in made + [maps.mask_name(obj), maps.level_name(obj)]:
        _remove_image(name)
    attr = me.attributes.get(geo.OVERLAP)
    if attr is not None:
        me.attributes.remove(attr)
    for k in PROPS:
        if k in obj:
            del obj[k]


def reset(obj):
    """Back to uv_normal and the original images: Seam Fix's UV maps, attribute, groups and
    images go. uv_normal itself was never touched."""
    me = obj.data
    made = shader.uninstall(obj)
    for name in (geo.UV_NEW, geo.UV_FIX):
        layer = me.uv_layers.get(name)
        if layer is not None:
            me.uv_layers.remove(layer)
    src = me.uv_layers.get(geo.UV_SRC)
    if src is not None:
        me.uv_layers.active = src
        src.active_render = True
    _clear_object(obj, made)


# ---------------------------------------------------------------- Apply

def _backup(path):
    folder = os.path.join(os.path.dirname(path), "_seamfix_backup")
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, os.path.basename(path))
    if os.path.exists(dst):
        root, ext = os.path.splitext(dst)
        dst = f"{root}_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
    shutil.copy2(path, dst)
    return dst


def _write(temp, temp_alpha, path):
    """temp (+ temp_alpha) over the file at `path`, keeping its channels and bit depth."""
    import OpenImageIO as oiio
    alg = oiio.ImageBufAlgo
    inp = oiio.ImageInput.open(path)
    if inp is None:
        raise RuntimeError(f"Cannot read {path}")
    spec = inp.spec()
    inp.close()
    work = tempfile.mkdtemp(prefix="seamfix_")
    try:
        def saved(img, name):
            img.file_format = 'OPEN_EXR' if img.is_float else 'PNG'
            p = os.path.join(work, name + (".exr" if img.is_float else ".png"))
            img.save(filepath=p, save_copy=True)
            return oiio.ImageBuf(p)

        color = saved(temp, "color")
        if spec.nchannels == 1:
            out = alg.channels(color, (0,))
        else:
            out = alg.channels(color, (0, 1, 2))
            if spec.nchannels == 4:
                if temp_alpha is not None:
                    a = alg.channels(saved(temp_alpha, "alpha"), (0,))
                else:
                    a = alg.channels(color, (1.0,))
                out = alg.channel_append(out, a)
        if not out.write(path, spec.format):
            raise RuntimeError(out.geterror())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def files_to_write(obj):
    """[(original image, bake, alpha bake, path)] - raises when one is missing."""
    out = []
    for mat in shader.materials(obj):
        st = shader.state(mat)
        if not st:
            continue
        for _name, info in st["nodes"].items():
            img = bpy.data.images.get(info["image"])
            temp = bpy.data.images.get(info.get("temp") or "")
            if img is None or temp is None:
                raise RuntimeError(f"{info['image']} has no bake - run Bake first")
            path = bpy.path.abspath(img.filepath)
            if img.packed_file is not None or not os.path.isfile(path):
                raise RuntimeError(f"{img.name} is not a file on disk - cannot overwrite it")
            out.append((img, temp, bpy.data.images.get(info.get("temp_alpha") or ""), path))
    return out


def apply(obj):
    me = obj.data
    jobs = files_to_write(obj)
    backups = []
    for img, temp, temp_alpha, path in jobs:
        backups.append(_backup(path))
        _write(temp, temp_alpha, path)
    made = shader.uninstall(obj)
    for img, *_rest in jobs:
        img.reload()

    old = me.uv_layers.get(geo.UV_SRC)
    if old is not None:
        me.uv_layers.remove(old)
    new = me.uv_layers[geo.UV_NEW]
    new.name = geo.UV_SRC
    for layer in [l for l in me.uv_layers if l.name != geo.UV_SRC]:
        me.uv_layers.remove(layer)
    keep = me.uv_layers[geo.UV_SRC]
    me.uv_layers.active = keep
    keep.active_render = True
    _clear_object(obj, made)
    obj["seamfix_state"] = "applied"
    return {"files": [p for *_r, p in jobs], "backups": backups}
