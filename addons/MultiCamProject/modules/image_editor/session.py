"""Liquify session: the photo is swapped for a 1K preview image everywhere it is used.

Start: the photo's pixels are downscaled into a generated image MCP_LQ_<photo>, and
photo.user_remap(preview) points every user at it at once - camera backgrounds, the
projection materials (CamTex_i), Image Editors. Painting rewrites the preview's pixels, so the
3D Viewport shows the warp live on the camera background and on the mesh.
Stop: Cancel points the users back at the photo, Bake at the baked <photo>_lq.png; the
preview is removed. Starting on a baked _lq image re-edits it: the pixels come from the original
photo named in its sidecar and the saved field is loaded (see bake.py).

The preview holds the photo in a custom property (KEY). That pointer counts as a user, so the
photo - which has no other users while the session runs - is never dropped, and any leftover
preview can be put back: after a file load, an undo past Start, or a crash.

Kept out of .blend files: save_pre swaps the photo back, save_post swaps the preview in again.
Only names are kept in Python - undo frees and reloads every ID.
"""
import os

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import bake, field

KEY = "mcp_lq_orig"
PREFIX = "MCP_LQ_"

_session = None


class Session:
    def __init__(self, photo, source, preview, lq, users, out_path, reedit):
        self.photo_name = photo.name        # swapped out; Cancel puts it back
        self.source_name = source.name      # the original pixels (the photo, or the
        self.size = tuple(source.size)      # original of a re-edited _lq image)
        self.preview_name = preview.name
        self.lq = lq
        self.users = users          # {"cameras": [...], "materials": [...]} at start
        self.out_path = out_path    # Bake target, None when there is nowhere to write
        self.reedit = reedit

    @property
    def preview(self):
        return bpy.data.images.get(self.preview_name)

    @property
    def photo(self):
        p = self.preview
        orig = p.get(KEY) if p is not None else None
        return orig if isinstance(orig, bpy.types.Image) else bpy.data.images.get(self.photo_name)

    @property
    def source(self):
        return self.photo if not self.reedit else bpy.data.images.get(self.source_name)


def active():
    """The running session, or None (also ends one whose preview disappeared)."""
    global _session
    if _session is not None and _session.preview is None:
        _session = None
    return _session


def is_preview(img):
    return img is not None and KEY in img


# ---------------------------------------------------------------- pixels

def read_pixels(img):
    """float32 (h, w, 4) in the image's own colour space, rows bottom-up."""
    w, h = img.size
    buf = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)


def push(s=None):
    """Write the warped preview into its image and redraw the editors that show it."""
    s = s or active()
    img = s.preview if s else None
    if img is None:
        return
    img.pixels.foreach_set(s.lq.out.ravel())
    img.update()
    redraw()


def redraw():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                area.tag_redraw()


# ---------------------------------------------------------------- users

def users_of(img):
    """Names of the cameras and materials that show `img`."""
    ids = bpy.data.user_map(subset={img}).get(img, set())
    cam_data = {i for i in ids if isinstance(i, bpy.types.Camera)}
    cams = sorted(o.name for o in bpy.data.objects if o.type == 'CAMERA' and o.data in cam_data)
    mats = sorted(i.name for i in ids if isinstance(i, bpy.types.Material))
    return {"cameras": cams, "materials": mats}


def cameras_showing(img):
    """Cameras with `img` as a background image - cheap enough for a panel redraw."""
    if img is None:
        return []
    return [o for o in bpy.data.objects if o.type == 'CAMERA'
            and any(bg.image == img for bg in o.data.background_images)]


def _swap_in(photo, preview):
    photo.user_remap(preview)
    preview[KEY] = photo        # set after the remap, which would point it at the preview


def _swap_out(preview, photo):
    preview.user_remap(photo)


# ---------------------------------------------------------------- start / stop

def can_start(img):
    """None when `img` can be liquified, else the reason."""
    if active() is not None:
        return "A Liquify session is already running"
    if img is None:
        return "No image"
    if is_preview(img):
        return "This is a Liquify preview"
    if img.library is not None:
        return "Linked image - make it local first"
    if img.source not in {'FILE', 'GENERATED'}:
        return "Only single still images"
    return None


def start(img):
    global _session
    reason = can_start(img)
    if reason:
        raise ValueError(reason)
    side = bake.read_sidecar(img)
    source = img
    if side:
        if not os.path.isfile(side["source"]):
            raise ValueError(f"Original of {img.name} not found: {side['source']} "
                             f"(delete {os.path.basename(side['sidecar'])} to warp the _lq image itself)")
        source = bpy.data.images.load(side["source"], check_existing=True)
    w, h = source.size
    if w < 2 or h < 2:
        _drop_unused(source, img)
        raise ValueError(f"Cannot read the pixels of {source.name} - is the file missing?")
    from . import props
    pw, ph = field.preview_size(w, h, props.preview_long_side())
    src = field.downscale(read_pixels(source), pw, ph)
    users = users_of(img)

    name = (PREFIX + img.name)[:63]
    preview = bpy.data.images.new(name, pw, ph, alpha=True, float_buffer=img.is_float)
    preview.colorspace_settings.name = img.colorspace_settings.name
    preview.alpha_mode = img.alpha_mode
    lq = field.Liquify(src)
    if side:
        lq.load_field_uv(side["field"])
    _swap_in(img, preview)
    _session = Session(img, source, preview, lq, users,
                       bake.output_path(img, source, side), side is not None)
    push(_session)
    return _session


def _drop_unused(img, keep):
    if img is not None and img != keep and img.users == 0:
        bpy.data.images.remove(img)


def _end(target):
    """Point the preview's users at `target` and remove the preview."""
    global _session
    s, _session = active(), None
    if s is None:
        return
    preview, source = s.preview, s.source
    if target is not None:
        _swap_out(preview, target)
    bpy.data.images.remove(preview)
    if s.reedit:
        _drop_unused(source, target)
    redraw()


def cancel():
    """Back to the untouched photo everywhere."""
    s = active()
    if s is not None:
        _end(s.photo)


def bake_and_finish():
    """Bake the full-size result and use it everywhere the photo was used. Returns its path."""
    s = active()
    if s is None:
        raise ValueError("No Liquify session")
    if s.lq.in_stroke:
        s.lq.stroke_end()
    img = bake.bake(s, read_pixels)
    _end(img)
    return s.out_path


def recover():
    """Put back the photo of every leftover preview (no running session owns it)."""
    s = active()
    live = s.preview_name if s else None
    for img in [i for i in bpy.data.images if KEY in i and i.name != live]:
        photo = img.get(KEY)
        if isinstance(photo, bpy.types.Image):
            _swap_out(img, photo)
            print(f"[MultiCamProject] Liquify: restored {photo.name}")
        bpy.data.images.remove(img)


# ---------------------------------------------------------------- handlers

@persistent
def _on_load(_):
    global _session
    _session = None
    recover()


@persistent
def _on_save_pre(_):
    s = active()
    if s is not None and s.photo is not None:
        _swap_out(s.preview, s.photo)


@persistent
def _on_save_post(_):
    s = active()
    if s is not None and s.photo is not None:
        _swap_in(s.photo, s.preview)


@persistent
def _on_undo(_):
    """Undo reloads every ID: re-point anything the undo gave back to the photo, and
    re-write the pixels. An undo past Start removes the preview: the session ends."""
    s = active()
    if s is None:
        recover()
        return
    if s.photo is None:
        cancel()
        return
    _swap_in(s.photo, s.preview)
    push(s)


_HANDLERS = ((bpy.app.handlers.load_post, _on_load),
             (bpy.app.handlers.save_pre, _on_save_pre),
             (bpy.app.handlers.save_post, _on_save_post),
             (bpy.app.handlers.undo_post, _on_undo),
             (bpy.app.handlers.redo_post, _on_undo))


def register():
    for lst, fn in _HANDLERS:
        if fn not in lst:
            lst.append(fn)
    bpy.app.timers.register(_recover_later, first_interval=0.1)


def _recover_later():
    try:
        recover()
    except Exception as e:      # never block add-on startup
        print(f"[MultiCamProject] Liquify recovery skipped: {e}")
    return None


def unregister():
    try:
        cancel()
    except Exception as e:
        print(f"[MultiCamProject] Liquify cancel on unregister failed: {e}")
    for lst, fn in _HANDLERS:
        if fn in lst:
            lst.remove(fn)
