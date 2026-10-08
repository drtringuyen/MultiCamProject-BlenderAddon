"""
lasso_ops.py — Photoshop-style lasso cut / move for a photo, as a session like Liquify.

Ported from DomeAnimatic (modules/painting_cel/lasso_transform_ops.py), where the
target is the active cel layer. Here it is a photo, in either of two views:
  * Image Editor (K)         — the image the editor shows. Picking another image
                               in the header while a piece floats retargets it.
  * soloed camera (K in the  — the camera's background photo, where you see it in
    3D Viewport, or the        the 3D Viewport (camview maps the mouse onto it).
    Lasso button of a          Leaving solo, another photo on the camera or the
    camera row)                viewport closing cancels the session, as Liquify.

Draw a polygon lasso, then move / rotate / scale the selected pixels as a
floating GPU-textured quad (live, no image.pixels writes). Each piece is baked in
one vectorized numpy pass when you click outside it (the session goes on with a
new lasso) or press Enter (the session ends). The 3D Viewport picks the change up
at once: camera backgrounds and projection materials read the same image.

Session keys (mouse over the view):
  LMB                        add lasso point · click point 1 / double-click closes
  drag inside the piece      move it                 G / R / S  move / rotate / scale
  click outside the piece    apply it, next lasso    K          same
  Shift+D                    stamp a copy, keep moving it
  X                          delete the selection (leaves the hole)
  Ctrl+C / Ctrl+V            copy the piece / paste the lasso clipboard
  Ctrl+Z                     undo: last point, the floating piece, the last applied edit
  Enter                      apply and end the session (Enter with < 3 points: end)
  Esc                        end the session and revert everything it changed

A cut leaves a hole: transparent, or filled with the colour of the Lasso panel
(props.MULTICAMPROJECT_LassoSettings - Auto fills photos without alpha).

Pixel writes are outside Blender's undo, so each applied edit records the pixels
it overwrites: the session's Ctrl+Z / Esc use them, and so does Lasso Undo
afterwards (_UNDO). The edited image only changes in memory: Image > Save writes
it, Image > Reload throws it away.

States: DRAW -> FLOAT_IDLE <-> GRAB / ROTATE / SCALE.
The floating piece carries a single 2D affine  p' = scale * R(angle) * p + t.
"""

import math
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty

try:
    import gpu
except Exception:
    gpu = None

try:
    import numpy as np
except ImportError:
    np = None

from . import camview, lasso_draw, lasso_raster, props, session


CLOSE_THRESHOLD_PX = 12.0   # region pixels — click this close to point 0 closes
DBL_CLICK_DIST_PX  = 6.0    # region pixels — manual double-click fallback

UNDO_STEPS = 32
UNDO_BYTES = 512 * 1024 * 1024


# ── Lasso clipboard (survives across operator runs, Ctrl+C / Ctrl+V) ──────────

_CLIPBOARD = None   # dict: patch/mask/points/bbox/affine/sel_center/is_float


# ── Lasso undo (pixel writes are outside Blender's undo) ──────────────────────

_UNDO = []          # one record per applied edit: [(image name, x0, y0, old block), ...]


def _save_rect(rec, img, buf, rect) -> None:
    """Remember buf[rect] of `img` before it is overwritten."""
    if rect is None:
        return
    x0, y0, x1, y1 = rect
    rec.append((img.name, x0, y0, buf[y0:y1, x0:x1].copy()))


def _push_undo(rec) -> None:
    if not rec:
        return
    _UNDO.append(rec)
    total = sum(e[3].nbytes for r in _UNDO for e in r)
    while len(_UNDO) > UNDO_STEPS or (len(_UNDO) > 1 and total > UNDO_BYTES):
        total -= sum(e[3].nbytes for e in _UNDO.pop(0))


def _restore(rec):
    """Write the pixels of one record back; also drops it from _UNDO.
    Returns (restored, skipped) image names."""
    _UNDO[:] = [r for r in _UNDO if r is not rec]
    restored, skipped, bufs = set(), set(), {}
    for name, x0, y0, block in reversed(rec):
        img = bpy.data.images.get(name)
        bh, bw = block.shape[:2]
        if img is None or img.size[0] < x0 + bw or img.size[1] < y0 + bh:
            skipped.add(name)
            continue
        buf = bufs.get(name)
        if buf is None:
            buf = bufs[name] = lasso_raster.read_pixels(img)
        buf[y0:y0 + bh, x0:x0 + bw] = block
        restored.add(name)
    for name, buf in bufs.items():
        lasso_raster.write_pixels(bpy.data.images[name], buf)
    session.redraw()
    return restored, skipped


def undo_steps() -> int:
    return len(_UNDO)


# ── Targets and the hole fill ─────────────────────────────────────────────────

def target_reason(img):
    """None when the lasso can cut `img`, else the reason."""
    if img is None:
        return "No image"
    if session.is_preview(img):
        return "This is a Liquify preview - Bake or Cancel Liquify first"
    if img.library is not None:
        return "Linked image - make it local first"
    if img.source not in {'FILE', 'GENERATED'}:
        return "Only single still images"
    w, h = img.size
    if w == 0 or h == 0:
        return "Image has no pixels - is the file missing?"
    if getattr(img, 'channels', 4) != 4:
        return "Image has no RGBA pixels"
    return None


def has_alpha(img) -> bool:
    """The image file carries alpha (RGBA PNG, EXR...): JPGs and RGB PNGs don't."""
    return img.alpha_mode != 'NONE' and img.depth in {32, 64, 128}


def _fill(img, linear: bool):
    """RGB the hole of a cut in `img` is filled with, or None for a transparent
    hole. linear=True gives the image's own pixel values (float images are
    linear), False the display colour for the preview."""
    st = props.lasso_settings()
    if st.fill == 'TRANSPARENT' or (st.fill == 'AUTO' and has_alpha(img)):
        return None
    rgb = tuple(st.color)
    if linear and img.is_float:
        rgb = tuple(float(c) for c in lasso_raster.srgb_to_linear(rgb))
    return rgb


def _space_image(context):
    sp = context.space_data
    return sp.image if sp is not None and sp.type == 'IMAGE_EDITOR' else None


def _cam_core():
    from ..camera_project import core, operators
    return core, operators


def _window_region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None)


def _base_poll(cls, context):
    if gpu is None or np is None:
        cls.poll_message_set("GPU module / numpy not available")
        return False
    if lasso_draw.get_active_op() is not None:
        cls.poll_message_set("The lasso is already running")
        return False
    return True


class _Mapper:
    """Image pixels <-> region pixels for the view the lasso runs in."""

    def __init__(self, to_region, to_px, rect):
        self.to_region = to_region      # (px, py) -> (rx, ry)
        self.to_px = to_px              # (rx, ry) -> (px, py)
        self.rect = rect                # screen bounds of the photo (x0, y0, x1, y1)


# ── Operator body ─────────────────────────────────────────────────────────────

class LassoMixin:
    """The lasso session, shared by the Image Editor, paste and camera operators.
    Not itself an Operator: when one registered operator subclasses another, Blender
    runs the subclass's poll for the base operator too."""
    bl_options = {'REGISTER'}   # pixel writes are outside Blender's undo: see _UNDO

    PASTE = False   # MULTICAMPROJECT_OT_lasso_paste starts from the clipboard

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _start(self, context, img, view, cam_name=""):
        """Shared invoke tail: img passed target_reason, context.area is the view."""
        if self.PASTE and _CLIPBOARD is None:
            self.report({'WARNING'}, "Lasso clipboard is empty - Ctrl+C a selection first.")
            return {'CANCELLED'}
        region = _window_region(context.area)
        if region is None:
            self.report({'ERROR'}, "No WINDOW region in this editor.")
            return {'CANCELLED'}

        self._view       = view          # 'IMAGE' or 'CAMERA'
        self._cam_name   = cam_name
        self._area       = context.area
        self._area_ptr   = context.area.as_pointer()
        self._region     = region
        self._region_ptr = region.as_pointer()

        # The target image — by name, so an undo or a datablock swap never
        # leaves a dangling reference. The camera view's target is fixed.
        self._cur_name   = img.name
        self._w, self._h = img.size

        self._state     = 'DRAW'
        self._points    = []      # lasso vertices in image pixel space
        self._cursor_px = None

        # Manual double-click detection — modal handlers don't reliably get
        # DOUBLE_CLICK values, so track (time, window-relative region pos).
        self._last_click = (0.0, None)
        try:
            self._dbl_time = max(0.05,
                context.preferences.inputs.mouse_double_click_time / 1000.0)
        except Exception:
            self._dbl_time = 0.35

        self._clear_float()
        self._is_float = img.is_float     # patch pixels are linear (preview converts)
        self._src_name = img.name
        self._src_size = (self._w, self._h)
        self._recs     = []               # undo records applied by this session

        # Sub-mode (G/R/S) working data
        self._snap            = None
        self._sub_start_px    = (0.0, 0.0)
        self._sub_start_angle = 0.0
        self._sub_start_dist  = 1.0
        self._pivot_px        = (0.0, 0.0)
        self._sub_drag        = False   # GRAB entered by dragging (confirm on release)

        lasso_draw.set_active_op(self)
        lasso_draw.ensure_handler()

        if self.PASTE:
            if not self._adopt_clipboard():
                lasso_draw.remove_handler()
                lasso_draw.clear_active_op()
                self.report({'ERROR'}, "Could not build paste preview.")
                return {'CANCELLED'}
        else:
            context.window.cursor_modal_set('CROSSHAIR')

        context.window_manager.modal_handler_add(self)
        self._area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _clear_float(self) -> None:
        self._bx0 = self._by0 = 0
        self._pw  = self._ph  = 0
        self._mask       = None   # (ph, pw) bool — inside polygon
        self._patch      = None   # (ph, pw, 4) float32 straight RGBA, alpha=0 outside
        self._float_tex  = None
        self._hole_tex   = None
        self._hole_key   = None
        self._sel_center = (0.0, 0.0)
        # Accumulated affine: p' = scale * R(angle) * p + (tx, ty)
        self._angle, self._scale = 0.0, 1.0
        self._tx, self._ty       = 0.0, 0.0
        # A CUT hole stays virtual (drawn, never written) until the piece is
        # applied; then it is punched into the source and the float is a COPY.
        self._source_mode = 'CUT'
        self._hole_live   = False

    def cancel(self, context):
        """Blender ends the operator (file load, window closed): keep what was applied."""
        self._cleanup(context)

    def _cleanup(self, context) -> None:
        lasso_draw.remove_handler()
        lasso_draw.clear_active_op()
        self._float_tex = None
        self._hole_tex  = None
        self._mask      = None
        self._patch     = None
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        session.redraw()

    def _finish(self, context):
        """Enter: the session's edits stay (Lasso Undo can still revert them)."""
        n = len(self._recs)
        self._cleanup(context)
        if n:
            self.report({'INFO'}, f"Lasso: {n} edit(s) applied to {self._cur_name} "
                                  "(Image > Save to keep them on disk)")
        return {'FINISHED'}

    def _revert_session(self, context, why=None):
        """Esc / leaving the view: put back every pixel the session changed."""
        for rec in reversed(self._recs):
            _restore(rec)
        n = len(self._recs)
        self._recs = []
        self._cleanup(context)
        msg = "Lasso cancelled" + (f": {why}" if why else "")
        if n:
            msg += f" - {n} edit(s) reverted"
        self.report({'INFO'}, msg)
        return {'CANCELLED'}

    # ── View helpers ─────────────────────────────────────────────────────────

    def _cam(self):
        return bpy.data.objects.get(self._cam_name)

    def _shown_image(self):
        if self._view == 'CAMERA':
            cam = self._cam()
            return _cam_core()[0].cam_image(cam) if cam is not None else None
        try:
            return self._area.spaces.active.image
        except Exception:
            return None

    def _view_lost(self, context):
        """None while the session may go on, else why it stops (camera view)."""
        if self._view != 'CAMERA':
            return None
        if context.area is None or context.area.as_pointer() != self._area_ptr:
            return "the viewport closed"
        cam = self._cam()
        _core, ops = _cam_core()
        if cam is None or not ops.is_solo(context, cam):
            return "left solo"
        img = self._shown_image()
        if img is None or img.name != self._cur_name:
            return "the camera shows another image"
        return None

    def _mapper(self, region=None, rv3d=None):
        """_Mapper for the region (default: the session's), or None (photo off screen)."""
        region = region or self._region
        w, h = max(1, self._w), max(1, self._h)
        if self._view == 'CAMERA':
            core, _ops = _cam_core()
            cam = self._cam()
            bg = core.bg_entry(cam) if cam is not None else None
            if bg is None:
                return None
            rv3d = rv3d or self._area.spaces.active.region_3d
            place = camview.placement(region, rv3d, bpy.context.scene, cam, bg, w / h)
            if place is None:
                return None
            corners = [place.from_uv(u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]

            def to_px(rx, ry):
                u, v = place.to_uv(rx, ry)
                return (u * w, v * h)
            return _Mapper(lambda px, py: place.from_uv(px / w, py / h), to_px,
                           (min(xs), min(ys), max(xs), max(ys)))

        v2d = region.view2d
        x0, y0 = v2d.view_to_region(0.0, 0.0, clip=False)
        x1, y1 = v2d.view_to_region(1.0, 1.0, clip=False)

        def to_px(rx, ry):
            u, v = v2d.region_to_view(rx, ry)
            return (u * w, v * h)
        return _Mapper(lambda px, py: (x0 + px / w * (x1 - x0), y0 + py / h * (y1 - y0)),
                       to_px, (x0, y0, x1, y1))

    def _hole_visible(self) -> bool:
        return (self._source_mode == 'CUT' and self._hole_live
                and self._mask is not None and self._cur_name == self._src_name)

    def _hole_texture(self):
        """Preview of the hole — rebuilt when the fill setting changes."""
        img = bpy.data.images.get(self._src_name)
        key = _fill(img, linear=False) if img is not None else None
        if self._hole_tex is None or key != self._hole_key:
            try:
                self._hole_tex = lasso_raster.make_texture(
                    lasso_raster.hole_overlay(self._mask, key))
            except Exception:
                self._hole_tex = None
            self._hole_key = key
        return self._hole_tex

    def _sync_context(self) -> None:
        """Image Editor: follow the image it shows (events pass through, so the
        user can pick another one mid-float): it is the target of the bake."""
        if self._view != 'IMAGE':
            return
        img  = self._shown_image()
        name = img.name if img is not None and target_reason(img) is None else None
        if name == self._cur_name and (name is None or tuple(img.size) == (self._w, self._h)):
            return
        self._cur_name = name
        if name is not None:
            self._w, self._h = img.size
        session.redraw()

    def _src_image(self):
        """The source image while it still has the size the selection was lifted at."""
        img = bpy.data.images.get(self._src_name)
        if img is None or tuple(img.size) != self._src_size:
            return None
        return img

    # ── Coordinate helpers ───────────────────────────────────────────────────

    def _region_xy(self, event):
        return event.mouse_x - self._region.x, event.mouse_y - self._region.y

    def _mouse_px(self, event):
        """Mouse position in image pixel space, or None (photo off screen)."""
        m = self._mapper()
        return m.to_px(*self._region_xy(event)) if m is not None else None

    def _px_to_region(self, px, py):
        m = self._mapper()
        return m.to_region(px, py) if m is not None else (1e9, 1e9)

    def _mouse_in_region(self, event) -> bool:
        """Over the view itself — not over the header, toolbar or a sidebar that
        overlaps the WINDOW region (their buttons must stay usable)."""
        mx, my = event.mouse_x, event.mouse_y
        r = self._region
        if not (r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height):
            return False
        for o in self._area.regions:
            if (o.type != 'WINDOW' and o.width > 1 and o.height > 1
                    and o.x <= mx < o.x + o.width and o.y <= my < o.y + o.height):
                return False
        return True

    def _in_area(self, event) -> bool:
        a = self._area
        return a.x <= event.mouse_x < a.x + a.width and a.y <= event.mouse_y < a.y + a.height

    def _point_in_selection(self, px, py) -> bool:
        """Even-odd test against the TRANSFORMED lasso polygon."""
        if not self._points:
            return False
        pts    = self._affine_apply_points(self._points)
        inside = False
        n      = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            if (y1 > py) != (y2 > py):
                x_at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
                if px < x_at:
                    inside = not inside
        return inside

    # ── Affine helpers  (p' = s * R(a) * p + t) ──────────────────────────────

    def _affine_apply_points(self, points):
        ca = math.cos(self._angle) * self._scale
        sa = math.sin(self._angle) * self._scale
        return [(ca * x - sa * y + self._tx,
                 sa * x + ca * y + self._ty) for x, y in points]

    def _transformed_bbox_corners(self):
        bx1 = self._bx0 + self._pw
        by1 = self._by0 + self._ph
        return self._affine_apply_points([
            (self._bx0, self._by0), (bx1, self._by0),
            (bx1, by1), (self._bx0, by1),
        ])

    # ── Modal dispatch ───────────────────────────────────────────────────────

    def modal(self, context, event):
        try:
            self._area.tag_redraw()
        except Exception:
            return self._revert_session(context, "the editor closed")
        why = self._view_lost(context)
        if why:
            return self._revert_session(context, why)

        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}   # keep zoom alive
        if event.type == 'MIDDLEMOUSE':
            if (self._view == 'CAMERA' and event.value == 'PRESS'
                    and not (event.shift or event.ctrl) and self._mouse_in_region(event)):
                self.report({'INFO'}, "Lasso: orbiting would leave the camera (Shift+MMB pans)")
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}   # pan

        if self._state == 'DRAW':
            return self._modal_draw(context, event)
        if self._state in {'GRAB', 'ROTATE', 'SCALE'}:
            return self._modal_submode(context, event)
        return self._modal_idle(context, event)

    def _session_keys(self, context, event):
        """Enter / Esc / Ctrl+Z, shared by DRAW and FLOAT_IDLE; None if not one."""
        if event.value != 'PRESS' or not self._in_area(event):
            return None
        et = event.type
        if et == 'ESC':
            return self._revert_session(context)
        if et == 'Z' and event.ctrl and not event.shift:
            self._undo_step(context)
            return {'RUNNING_MODAL'}
        if et in {'RET', 'NUMPAD_ENTER'}:
            if self._state == 'DRAW':
                if len(self._points) >= 3:
                    return self._close_polygon(context)
                return self._finish(context)
            if not self._bake_current(context):
                return {'RUNNING_MODAL'}
            return self._finish(context)
        return None

    def _undo_step(self, context) -> None:
        """Ctrl+Z: the last lasso point, else the floating piece, else the last edit."""
        if self._state == 'FLOAT_IDLE':
            self._reset_to_draw(context)
            self.report({'INFO'}, "Lasso: floating piece dropped")
        elif self._points:
            self._points.pop()
        elif self._recs:
            restored, _skipped = _restore(self._recs.pop())
            self.report({'INFO'}, f"Lasso: undone on {', '.join(sorted(restored)) or '-'}")
        else:
            self.report({'INFO'}, "Lasso: nothing to undo")

    # ── DRAW state ───────────────────────────────────────────────────────────

    def _modal_draw(self, context, event):
        self._sync_context()

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._cursor_px = self._mouse_px(event)
            return {'PASS_THROUGH'} if not self._mouse_in_region(event) else {'RUNNING_MODAL'}

        done = self._session_keys(context, event)
        if done is not None:
            return done

        if event.type == 'LEFTMOUSE' and event.value in {'PRESS', 'DOUBLE_CLICK'}:
            if not self._mouse_in_region(event):
                return {'PASS_THROUGH'}   # panel buttons / other editors stay live
            pt = self._mouse_px(event)
            if pt is None:
                return {'RUNNING_MODAL'}
            # Region coords derived from window coords — mouse_region_x/y is
            # relative to the invoking region, which may be the sidebar panel.
            rx, ry = self._region_xy(event)
            now = time.monotonic()
            prev_t, prev_pos = self._last_click
            self._last_click = (now, (rx, ry))

            is_double = event.value == 'DOUBLE_CLICK' or (
                prev_pos is not None
                and now - prev_t <= self._dbl_time
                and math.hypot(rx - prev_pos[0],
                               ry - prev_pos[1]) <= DBL_CLICK_DIST_PX)
            if is_double:
                self._last_click = (0.0, None)
                if len(self._points) >= 3:
                    # The pair's first click already placed the final vertex
                    return self._close_polygon(context)
                return {'RUNNING_MODAL'}

            if len(self._points) >= 3:
                fx, fy = self._px_to_region(*self._points[0])
                if math.hypot(rx - fx, ry - fy) <= CLOSE_THRESHOLD_PX:
                    return self._close_polygon(context)
            self._points.append(pt)
            return {'RUNNING_MODAL'}

        if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            if not self._mouse_in_region(event):
                return {'PASS_THROUGH'}
            if self._points:
                self._points.pop()   # undo last point
            return {'RUNNING_MODAL'}

        if event.type == 'V' and event.ctrl and event.value == 'PRESS' \
                and self._mouse_in_region(event):
            if _CLIPBOARD is None:
                self.report({'WARNING'}, "Lasso clipboard is empty.")
            elif not self._adopt_clipboard():
                self.report({'ERROR'}, "Could not build paste preview.")
            return {'RUNNING_MODAL'}

        if not self._mouse_in_region(event):
            return {'PASS_THROUGH'}
        # Over the view, other keys must not reach it while drawing (G in a
        # camera view would grab the mesh)
        return {'RUNNING_MODAL'}

    # ── Close polygon — build the floating selection (no pixel writes) ───────

    def _close_polygon(self, context):
        # Re-anchor to the image shown NOW — the user may have picked another
        # one in the Image Editor header while drawing.
        img = self._shown_image()
        reason = target_reason(img)
        if reason:
            self.report({'WARNING'}, reason)
            return {'RUNNING_MODAL'}
        self._cur_name   = img.name
        self._w, self._h = img.size

        pts = np.asarray(self._points, dtype=np.float32)
        bx0 = max(0, int(math.floor(float(pts[:, 0].min()))))
        by0 = max(0, int(math.floor(float(pts[:, 1].min()))))
        bx1 = min(self._w, int(math.ceil(float(pts[:, 0].max()))) + 1)
        by1 = min(self._h, int(math.ceil(float(pts[:, 1].max()))) + 1)
        if bx1 - bx0 < 1 or by1 - by0 < 1:
            self.report({'WARNING'}, "Lasso is outside the image - start again.")
            self._points = []
            return {'RUNNING_MODAL'}

        mask = lasso_raster.rasterize_polygon(self._points, bx0, by0,
                                              bx1 - bx0, by1 - by0)
        if not mask.any():
            self.report({'WARNING'}, "Lasso selected no pixels - start again.")
            self._points = []
            return {'RUNNING_MODAL'}

        src_buf = lasso_raster.read_pixels(img)
        patch = src_buf[by0:by1, bx0:bx1].copy()
        patch[..., 3] = np.where(mask, patch[..., 3], 0.0)
        del src_buf

        try:
            self._float_tex = lasso_raster.make_texture(
                lasso_raster.to_display(patch, img.is_float))
        except Exception as e:
            self.report({'ERROR'}, f"GPU texture upload failed: {e}")
            return self._revert_session(context)

        self._bx0, self._by0 = bx0, by0
        self._pw,  self._ph  = bx1 - bx0, by1 - by0
        self._mask     = mask
        self._patch    = patch
        self._is_float = img.is_float
        self._hole_tex = None
        # Selection center (mask centroid) — pivot for R/S, tracked through the affine
        ys, xs = np.nonzero(mask)
        self._sel_center = (bx0 + float(xs.mean()) + 0.5,
                            by0 + float(ys.mean()) + 0.5)

        self._src_name    = img.name
        self._src_size    = (self._w, self._h)
        self._source_mode = 'CUT'
        self._hole_live   = True

        try:
            context.window.cursor_modal_restore()   # normal cursor while floating
        except Exception:
            pass
        self._state = 'FLOAT_IDLE'
        return {'RUNNING_MODAL'}

    def _reset_to_draw(self, context) -> None:
        """Back to drawing a new lasso (the floating piece, if any, is dropped)."""
        self._points    = []
        self._cursor_px = None
        self._clear_float()
        try:
            context.window.cursor_modal_set('CROSSHAIR')
        except Exception:
            pass
        self._state = 'DRAW'
        session.redraw()

    # ── FLOAT_IDLE state — events not used here PASS THROUGH ─────────────────

    def _modal_idle(self, context, event):
        self._sync_context()

        if event.value != 'PRESS':
            return {'PASS_THROUGH'}

        done = self._session_keys(context, event)
        if done is not None:
            return done

        et        = event.type
        in_region = self._mouse_in_region(event)

        if et == 'LEFTMOUSE':
            if not in_region:
                return {'PASS_THROUGH'}
            p = self._mouse_px(event)
            if p is not None and self._point_in_selection(*p):
                self._enter_submode('GRAB', event, drag=True)   # drag to move
                return {'RUNNING_MODAL'}
            return self._apply_and_continue(context)   # click outside (Photoshop)

        if not in_region:
            # Let keys reach the editor under the mouse — this is what keeps
            # the header, sidebar and other editors usable while the float lives.
            return {'PASS_THROUGH'}

        if et == 'D' and event.shift:
            self._stamp_duplicate(context, event)
        elif et == 'G':
            self._enter_submode('GRAB', event)
        elif et == 'R' and not event.ctrl:
            self._enter_submode('ROTATE', event)
        elif et == 'S' and not event.ctrl:
            self._enter_submode('SCALE', event)
        elif et == 'C' and event.ctrl:
            self._copy_to_clipboard()
        elif et == 'V' and event.ctrl:
            return self._paste_over(context)
        elif et == 'X' and not event.ctrl:
            return self._delete_selection(context)
        elif et == 'K':
            return self._apply_and_continue(context)
        elif self._view == 'CAMERA':
            return {'RUNNING_MODAL'}   # keep the mesh safe from the viewport's keys
        else:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _apply_and_continue(self, context):
        """Apply the floating piece, then draw the next lasso (the session goes on)."""
        if self._bake_current(context):
            self._reset_to_draw(context)
        return {'RUNNING_MODAL'}

    def _copy_to_clipboard(self) -> None:
        """Ctrl+C — snapshot the floating selection; the float stays live."""
        global _CLIPBOARD
        _CLIPBOARD = {
            'patch':      self._patch.copy(),
            'mask':       self._mask.copy(),
            'points':     list(self._points),
            'bx0':        self._bx0, 'by0': self._by0,
            'pw':         self._pw,  'ph':  self._ph,
            'affine':     (self._angle, self._scale, self._tx, self._ty),
            'sel_center': self._sel_center,
            'is_float':   self._is_float,
        }
        self.report({'INFO'}, "Lasso selection copied - Ctrl+V pastes it "
                              "(also into another image).")

    def _adopt_clipboard(self) -> bool:
        """Load the clipboard as the current floating selection (paste-in-place)."""
        clip = _CLIPBOARD
        try:
            float_tex = lasso_raster.make_texture(
                lasso_raster.to_display(clip['patch'], clip['is_float']))
        except Exception:
            return False
        self._clear_float()
        self._patch      = clip['patch'].copy()
        self._mask       = clip['mask'].copy()
        self._points     = list(clip['points'])
        self._bx0, self._by0 = clip['bx0'], clip['by0']
        self._pw,  self._ph  = clip['pw'],  clip['ph']
        (self._angle, self._scale,
         self._tx, self._ty) = clip['affine']
        self._sel_center = clip['sel_center']
        self._is_float   = clip['is_float']
        self._float_tex  = float_tex
        self._source_mode = 'COPY'
        self._hole_live   = False
        self._src_name    = self._cur_name
        self._state       = 'FLOAT_IDLE'
        return True

    def _paste_over(self, context):
        """Ctrl+V while floating — apply the current piece, then float the
        clipboard content (Photoshop: paste commits the previous float)."""
        if _CLIPBOARD is None:
            self.report({'WARNING'}, "Lasso clipboard is empty.")
            return {'RUNNING_MODAL'}
        if not self._bake_current(context):
            return {'RUNNING_MODAL'}
        if not self._adopt_clipboard():
            self.report({'ERROR'}, "Could not build paste preview.")
            self._reset_to_draw(context)
        session.redraw()
        return {'RUNNING_MODAL'}

    def _stamp_duplicate(self, context, event) -> None:
        """Shift+D — apply the floating piece exactly where it is (hole included
        on the first CUT), then keep the same selection floating as a COPY and
        start a grab so the new duplicate follows the mouse."""
        if not self._bake_current(context):
            return
        self._enter_submode('GRAB', event)

    def _enter_submode(self, mode: str, event, drag: bool = False) -> None:
        start = self._mouse_px(event)
        if start is None:
            return
        self._snap         = (self._angle, self._scale, self._tx, self._ty)
        self._sub_start_px = start
        self._sub_drag     = drag
        if mode in {'ROTATE', 'SCALE'}:
            # Pivot = selection center in its CURRENT (transformed) position
            self._pivot_px = self._affine_apply_points([self._sel_center])[0]
            dx = self._sub_start_px[0] - self._pivot_px[0]
            dy = self._sub_start_px[1] - self._pivot_px[1]
            self._sub_start_angle = math.atan2(dy, dx)
            self._sub_start_dist  = max(math.hypot(dx, dy), 1e-3)
        self._state = mode

    # ── G/R/S sub-modes ──────────────────────────────────────────────────────

    def _modal_submode(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            cur = self._mouse_px(event)
            if cur is None:
                return {'RUNNING_MODAL'}
            a0, s0, tx0, ty0 = self._snap
            cx, cy           = self._pivot_px

            if self._state == 'GRAB':
                self._tx = tx0 + (cur[0] - self._sub_start_px[0])
                self._ty = ty0 + (cur[1] - self._sub_start_px[1])

            elif self._state == 'ROTATE':
                ang = math.atan2(cur[1] - cy, cur[0] - cx)
                da  = ang - self._sub_start_angle
                ca, sa      = math.cos(da), math.sin(da)
                vx, vy      = tx0 - cx, ty0 - cy
                self._angle = a0 + da
                self._tx    = ca * vx - sa * vy + cx
                self._ty    = sa * vx + ca * vy + cy

            elif self._state == 'SCALE':
                d  = math.hypot(cur[0] - cx, cur[1] - cy)
                ds = max(d / self._sub_start_dist, 1e-4)
                self._scale = s0 * ds
                self._tx    = ds * (tx0 - cx) + cx
                self._ty    = ds * (ty0 - cy) + cy
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'RELEASE' and self._sub_drag:
                self._sub_drag = False
                self._state    = 'FLOAT_IDLE'   # drag-move dropped
                return {'RUNNING_MODAL'}
            if event.value == 'PRESS' and not self._sub_drag:
                self._state = 'FLOAT_IDLE'      # accept key-started sub-move
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}

        if event.value == 'PRESS':
            if event.type in {'RET', 'NUMPAD_ENTER'}:
                self._sub_drag = False
                self._state = 'FLOAT_IDLE'        # accept sub-move
                return {'RUNNING_MODAL'}
            if event.type in {'RIGHTMOUSE', 'ESC'}:
                (self._angle, self._scale,
                 self._tx, self._ty) = self._snap  # revert sub-move only
                self._sub_drag = False
                self._state = 'FLOAT_IDLE'
                return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    # ── Commit paths (the only image.pixels writes) ──────────────────────────

    def _hole_rect(self):
        return (self._bx0, self._by0, self._bx0 + self._pw, self._by0 + self._ph)

    def _apply_hole(self, buf, img) -> None:
        lasso_raster.apply_hole(buf, self._mask, self._bx0, self._by0,
                                _fill(img, linear=True))

    def _pending_cut_source(self):
        """The source image while a CUT hole is still waiting to be punched."""
        if self._source_mode != 'CUT' or not self._hole_live:
            return None
        return self._src_image()

    def _record(self, rec) -> None:
        if rec:
            _push_undo(rec)
            self._recs.append(rec)

    def _delete_selection(self, context):
        """X — punch the hole into the source, next lasso. If the original was
        already stamped/applied, just drop the float."""
        src = self._pending_cut_source()
        if src is not None:
            rec = []
            buf = lasso_raster.read_pixels(src)
            _save_rect(rec, src, buf, self._hole_rect())
            self._apply_hole(buf, src)
            lasso_raster.write_pixels(src, buf)
            self._record(rec)
            self.report({'INFO'}, f"[{src.name}] Lasso selection deleted.")
        self._reset_to_draw(context)
        return {'RUNNING_MODAL'}

    def _bake_current(self, context) -> bool:
        """One vectorized bake of the current floating state into the target
        image. A pending CUT is punched into the source first (combined into
        one pass when source == dest). Records one undo step."""
        dest   = self._shown_image()
        reason = target_reason(dest)
        if reason:
            self.report({'WARNING'}, f"Cannot apply here: {reason}")
            return False

        rec = []
        src = self._pending_cut_source()
        if src is not None and src.name != dest.name:
            buf = lasso_raster.read_pixels(src)
            _save_rect(rec, src, buf, self._hole_rect())
            self._apply_hole(buf, src)
            lasso_raster.write_pixels(src, buf)

        buf = lasso_raster.read_pixels(dest)
        dh, dw = buf.shape[:2]
        bounds = lasso_raster.affine_bounds(self._transformed_bbox_corners(), dw, dh)
        combined = src is not None and src.name == dest.name
        # Snapshot both rects before touching either — they may overlap.
        if combined:
            _save_rect(rec, dest, buf, self._hole_rect())
        _save_rect(rec, dest, buf, bounds)
        if combined:
            self._apply_hole(buf, dest)
        self._composite_float(buf)
        lasso_raster.write_pixels(dest, buf)

        self._hole_live   = False
        self._source_mode = 'COPY'
        self._record(rec)
        session.redraw()
        return True

    def _composite_float(self, dest_buf) -> None:
        """Alpha-over the transformed floating patch into dest_buf (delegates
        to the pure vectorized bake in lasso_raster)."""
        lasso_raster.composite_float(
            dest_buf, self._patch, self._transformed_bbox_corners(),
            self._tx, self._ty, self._angle, self._scale,
            self._bx0, self._by0)


# ── Operators ─────────────────────────────────────────────────────────────────

class MULTICAMPROJECT_OT_lasso(LassoMixin, bpy.types.Operator):
    """Lasso pixels of the image in this editor and cut, move, rotate or scale them (K).
    Click outside a piece applies it, Enter applies and ends, Esc reverts the session,
    Ctrl+Z undoes a step"""
    bl_idname = "multicamproject.lasso"
    bl_label  = "Lasso Cut"

    @classmethod
    def poll(cls, context):
        if not _base_poll(cls, context):
            return False
        if context.area is None or context.area.type != 'IMAGE_EDITOR':
            return False
        reason = target_reason(_space_image(context))
        if reason:
            cls.poll_message_set(reason)
        return reason is None

    def invoke(self, context, event):
        img = _space_image(context)
        reason = target_reason(img)
        if reason:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        return self._start(context, img, 'IMAGE')


class MULTICAMPROJECT_OT_lasso_paste(LassoMixin, bpy.types.Operator):
    """Paste the lasso clipboard into this image as a floating selection"""
    bl_idname = "multicamproject.lasso_paste"
    bl_label  = "Lasso Paste"

    PASTE = True

    @classmethod
    def poll(cls, context):
        # Fails while the clipboard is empty, so Ctrl+V falls through to
        # Blender's own image paste.
        return _CLIPBOARD is not None and MULTICAMPROJECT_OT_lasso.poll(context)

    def invoke(self, context, event):
        return self._start(context, _space_image(context), 'IMAGE')


class MULTICAMPROJECT_OT_lasso_camera(LassoMixin, bpy.types.Operator):
    """Cut Projection: a lasso selection on this camera's photo, through the soloed camera
    (it solos it). Click points around a part (click the first point or double-click to
    close), then drag it or G / R / S to move, rotate, scale - the projection on the mesh
    follows. Shift+D stamps a copy, X deletes the part (a transparent hole), Ctrl+C /
    Ctrl+V copy and paste. Click outside to apply and start the next lasso, Enter applies
    and ends, Ctrl+Z undoes a step, Esc reverts the whole session (so does leaving solo)"""
    bl_idname = "multicamproject.lasso_camera"
    bl_label  = "Cut Projection"

    camera: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not _base_poll(cls, context):
            return False
        try:
            _core, ops = _cam_core()
        except ImportError:
            return False
        return ops.MULTICAMPROJECT_OT_SoloCamera.poll(context)

    def invoke(self, context, event):
        core, ops = _cam_core()
        cam = bpy.data.objects.get(self.camera) if self.camera else context.scene.camera
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        img = core.cam_image(cam)
        reason = target_reason(img)
        if reason:
            self.report({'ERROR'}, f"{cam.name}: {reason}")
            return {'CANCELLED'}
        if not ops.is_solo(context, cam):
            bpy.ops.multicamproject.solo_camera(camera=cam.name)
            if not ops.is_solo(context, cam):
                self.report({'ERROR'}, f"Could not solo {cam.name}")
                return {'CANCELLED'}
        return self._start(context, img, 'CAMERA', cam.name)


def running_camera():
    """Name of the camera a lasso session runs on, or None."""
    op = lasso_draw.get_active_op()
    return op._cam_name if op is not None and op._view == 'CAMERA' else None


class MULTICAMPROJECT_OT_lasso_undo(bpy.types.Operator):
    """Put back the pixels of the last lasso edit (lasso edits are outside Blender's undo)"""
    bl_idname  = "multicamproject.lasso_undo"
    bl_label   = "Lasso Undo"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if lasso_draw.get_active_op() is not None:
            cls.poll_message_set("The lasso is running - Ctrl+Z undoes its steps")
            return False
        return bool(_UNDO)

    def execute(self, context):
        restored, skipped = _restore(_UNDO[-1])
        if skipped:
            self.report({'WARNING'}, f"Lasso Undo skipped {', '.join(sorted(skipped))} "
                                     "(removed or resized)")
        elif restored:
            self.report({'INFO'}, f"Lasso Undo: {', '.join(sorted(restored))}")
        return {'FINISHED'}


@persistent
def _on_load(_):
    _UNDO.clear()   # the records name images of the previous file


# ── Register ──────────────────────────────────────────────────────────────────

CLASSES = (MULTICAMPROJECT_OT_lasso,
           MULTICAMPROJECT_OT_lasso_paste,
           MULTICAMPROJECT_OT_lasso_camera,
           MULTICAMPROJECT_OT_lasso_undo)

_KEYMAPS = []   # (keymap, keymap_item) pairs for clean unregister


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km  = kc.keymaps.new(name='Image', space_type='IMAGE_EDITOR')
        _KEYMAPS.append((km, km.keymap_items.new(MULTICAMPROJECT_OT_lasso.bl_idname,
                                                 'K', 'PRESS')))
        # Ctrl+V — paste the lasso clipboard as a new floating selection
        _KEYMAPS.append((km, km.keymap_items.new(MULTICAMPROJECT_OT_lasso_paste.bl_idname,
                                                 'V', 'PRESS', ctrl=True)))
        # K in a soloed camera view: camera_op.MULTICAMPROJECT_OT_CameraKey


def unregister():
    for km, kmi in _KEYMAPS:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _KEYMAPS.clear()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    lasso_draw.remove_handler()
    lasso_draw.clear_active_op()
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
