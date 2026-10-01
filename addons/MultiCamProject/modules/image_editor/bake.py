"""Bake: the warp applied to the original-size photo, saved as <photo>_lq.png next to it.

The warp field goes into a sidecar <photo>_lq.npz (field in UV units + path of the original),
so Start Liquify on the _lq image warps the original again from the saved field instead of
warping the already-warped pixels.
"""
import os

import bpy
import numpy as np

from . import field

SUFFIX = "_lq"
SIDECAR_EXT = ".npz"
VERSION = 1


def _file_path(img):
    """Absolute path of an image's file on disk, or '' (generated, packed without file)."""
    if img is None or img.source != 'FILE' or not img.filepath:
        return ""
    path = os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))
    return path if os.path.isfile(path) else ""


def sidecar_of(path):
    return os.path.splitext(path)[0] + SIDECAR_EXT


def read_sidecar(img):
    """{'field': array, 'source': original photo path} when img is a baked _lq image."""
    path = _file_path(img)
    side = sidecar_of(path) if path else ""
    if not side or not os.path.isfile(side):
        return None
    try:
        with np.load(side, allow_pickle=False) as z:
            f = z["field"].astype(np.float32)
            src = str(z["source"])
            rel = str(z["source_rel"]) if "source_rel" in z else ""
    except Exception as e:
        raise ValueError(f"Cannot read {os.path.basename(side)}: {e}")
    if f.ndim != 3 or f.shape[2] != 2:
        raise ValueError(f"{os.path.basename(side)} holds no warp field")
    near = os.path.normpath(os.path.join(os.path.dirname(side), rel)) if rel else ""
    if near and os.path.isfile(near):
        src = near             # project moved: the relative path still finds the original
    return {"field": f, "source": src, "sidecar": side}


def write_sidecar(out_path, field_uv, source):
    side = sidecar_of(out_path)
    try:
        rel = os.path.relpath(source, os.path.dirname(side))
    except ValueError:         # another drive
        rel = ""
    np.savez_compressed(side, field=field_uv.astype(np.float32), source=np.str_(source),
                        source_rel=np.str_(rel), version=np.int32(VERSION))
    return side


def output_path(photo, source, side):
    """Where Bake writes: the _lq file being re-edited, else <original>_lq.png beside the
    original (beside the .blend for photos not on disk). None when there is nowhere to write."""
    if side:
        return _file_path(photo)
    base = _file_path(source)
    folder = os.path.dirname(base) if base else ""
    if not folder and bpy.data.filepath:
        folder = os.path.dirname(bpy.path.abspath(bpy.data.filepath))
    if not folder:
        return None
    stem = (os.path.splitext(os.path.basename(base))[0] if base
            else bpy.path.clean_name(os.path.splitext(source.name)[0]))
    return os.path.join(folder, stem + SUFFIX + (".exr" if source.is_float else ".png"))


def _save_pixels(pixels, path, like):
    """Write float32 (h, w, 4) pixels to `path` with the colour settings of image `like`."""
    h, w = pixels.shape[:2]
    tmp = bpy.data.images.new("_MCP_LQ_bake", w, h, alpha=True, float_buffer=like.is_float)
    try:
        tmp.colorspace_settings.name = like.colorspace_settings.name
        tmp.alpha_mode = like.alpha_mode
        tmp.pixels.foreach_set(pixels.ravel())
        tmp.file_format = 'OPEN_EXR' if path.lower().endswith(".exr") else 'PNG'
        tmp.save(filepath=path, save_copy=True)
    finally:
        bpy.data.images.remove(tmp)


def _load(path, like):
    img = bpy.data.images.load(path, check_existing=True)
    if img.has_data:
        img.reload()
    img.colorspace_settings.name = like.colorspace_settings.name
    img.alpha_mode = like.alpha_mode
    return img


def bake(s, read_pixels):
    """Write the full-size result of session s. Returns the baked image (not yet assigned)."""
    path = s.out_path
    if not path:
        raise ValueError("Save the .blend first - this photo has no folder to bake into")
    src = s.source
    if src is None:
        raise ValueError("The original photo is gone")
    src_path = _file_path(src)
    if not src_path:
        # the original lives only in Blender: keep a copy on disk so the bake can be re-edited
        src_path = os.path.splitext(path)[0] + "_orig.png"
        _save_pixels(read_pixels(src), src_path, src)
    pixels = read_pixels(src)
    out = field.warp_image(pixels, s.lq.field_uv())
    _save_pixels(out, path, src)
    write_sidecar(path, s.lq.field_uv(), src_path)
    return _load(path, src)
