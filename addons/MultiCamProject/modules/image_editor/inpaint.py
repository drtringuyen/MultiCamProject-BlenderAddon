"""
inpaint.py — Inpaint: remove spots (the scan's markers) from a camera photo, a BAo_ or an ALB_,
like Photoshop's content-aware fill. Image Editor only, next to Liquify and Lasso. The sources
and their _inpaint files: sources.py.

One working copy: Start loads the shown image's file fresh into MCP_IP_<name> (a generated
image, never saved) and the editor shows it. Every edit goes into that copy only:
  B  Brush   paint the selection (Alt / Ctrl: erase it)
  L  Lasso   drag a free lasso, or click points (click point 1 / double-click / Enter
             closes); Alt / Ctrl subtracts
  F  Fill    fill the selection (done on release when "Fill on Release" is on)
Each fill is one step of the session's own undo (Ctrl+Z / Ctrl+Shift+Z, also in the panel):
pixel writes are outside Blender's undo. Fills are not written into the 8K copy (0.3 s each:
foreach_set + the GPU upload); each is drawn as a texture over the editor (the copy is a byte
sRGB image shown without view transform, so its pixels draw as they are). The copy gets the
pixels at Save, or when the undo limit drops a fill.

Enter / Save: the copy is written as the source's _inpaint file (the original stays as it was)
and everything that shows the source takes it - what was made from it is outdated (a BG or
BAo_: 05 Bake Albedo again, an ALB_: 07 Generate Normal again). Esc, another selection or
another image in the editor end the modal like Esc and ask: Save / Discard / Keep (the copy
waits; the panel resumes or ends it).

The fill itself is inpaint_fill.py (pure numpy). Each UV island of uv_normal (plus its bake
margin) is filled from itself only: a selection across a seam never pulls the other side's
colours in, and the black outside the islands never bleeds in.
"""
import math
import os
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, StringProperty

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except Exception:
    gpu = None
    batch_for_shader = None

try:
    import blf
except Exception:
    blf = None

import numpy as np

from . import inpaint_fill as ipf
from . import lasso_raster, props, sources

PREFIX = "MCP_IP_"
KEY = "mcp_ip_alb"          # on the copy: the edited image's name (leftover clean-up)

UNDO_BYTES = 1024 * 1024 * 1024
CONTEXT_PX = 384            # most pixels of known context around a fill
CLOSE_PX = 12.0             # region pixels: a click this close to point 1 closes the lasso
DRAG_PX = 6.0               # region pixels a press has to move to be a free lasso
OVERLAY_MAX = 512           # long side of the selection overlay texture

_session = None
_op = None                  # the running modal (one at a time)
_draw_handle = None
_panel_handles = []         # draw handlers measuring the overlapping sidebars (_measure_panels)
_panels_bottom = {}         # region pointer -> lowest pixel row its panels cover (region space)


def _baking():
    from ..baking import common, engine, handmade
    return common, engine, handmade


# ── Session ───────────────────────────────────────────────────────────────────

class Session:
    """The working copy of one image (a source's original or its _inpaint): pixels,
    selection, undo. Names only - undo reloads IDs."""

    def __init__(self, img, src, temp, buf, tris, margin):
        tris, islands = tris if tris is not None else (None, None)
        self.alb_name = img.name                # the edited image (any source)
        self.kind = src.kind
        self.obj_name = src.owner.name          # the object, or the camera of a BG
        self.temp_name = temp.name
        self.path = _baking()[0].image_file(img)
        self.buf = buf                          # (h, w, 4) float32, the copy's pixels
        self.h, self.w = buf.shape[:2]
        self.tris = tris                        # uv_normal triangles in pixels, or None
        self.islands = islands                  # UV island (1..K) per triangle
        self.margin = margin
        self.sel = np.zeros((self.h, self.w), dtype=bool)
        self.sel_rect = None                    # bounds of everything touched in sel
        self.undo = []                          # [(x0, y0, before, after)]
        self.redo = []
        self.overlay = None                     # (texture, rect) of the selection
        self.overlay_dirty = False
        self.tex = {}                           # id(undo record) -> GPU texture of its after
        self.pins = {}                          # editor area pointer -> its pin at Start
        self.uv = {}                            # editor area pointer -> (show_uv, opacity) at Start
        act = bpy.context.view_layer.objects.active
        self.active_name = act.name if act is not None else ""     # the selection at Start

    @property
    def temp(self):
        return bpy.data.images.get(self.temp_name)

    @property
    def alb(self):
        return bpy.data.images.get(self.alb_name)

    @property
    def obj(self):
        return bpy.data.objects.get(self.obj_name)

    @property
    def src(self):
        owner = self.obj
        return sources.Source(self.kind, owner) if owner is not None else None

    # selection
    def touch(self, rect):
        if rect is None:
            return
        if self.sel_rect is None:
            self.sel_rect = rect
        else:
            a = self.sel_rect
            self.sel_rect = (min(a[0], rect[0]), min(a[1], rect[1]),
                             max(a[2], rect[2]), max(a[3], rect[3]))
        self.overlay_dirty = True

    def has_selection(self):
        if self.sel_rect is None:
            return False
        x0, y0, x1, y1 = self.sel_rect
        return bool(self.sel[y0:y1, x0:x1].any())

    def clear_selection(self):
        if self.sel_rect is not None:
            x0, y0, x1, y1 = self.sel_rect
            self.sel[y0:y1, x0:x1] = False
        self.sel_rect = None
        self.overlay = None
        self.overlay_dirty = False

    # pixels
    def push(self):
        img = self.temp
        if img is not None:
            lasso_raster.write_pixels(img, self.buf)
        redraw()

    def _record(self, x0, y0, before, after):
        for rec in self.redo:
            self.tex.pop(id(rec), None)
        self.undo.append((x0, y0, before, after))
        self.redo.clear()
        total = sum(r[2].nbytes * 2 for r in self.undo)
        dropped = False
        while len(self.undo) > 1 and total > UNDO_BYTES:
            rec = self.undo.pop(0)
            self.tex.pop(id(rec), None)
            total -= rec[2].nbytes * 2
            dropped = True
        if dropped:
            self.push()         # a dropped fill is no longer drawn: the copy needs its pixels

    def patch_texture(self, rec):
        """GPU texture of a fill's pixels (built once, drawn over the editor)."""
        tex = self.tex.get(id(rec))
        if tex is None and gpu is not None:
            temp = self.temp
            px = lasso_raster.to_display(rec[3], temp is not None and temp.is_float)
            px[..., 3] = 1.0
            try:
                tex = self.tex[id(rec)] = lasso_raster.make_texture(px)
            except Exception as e:
                print(f"[MultiCamProject] Inpaint patch texture: {e}")
        return tex

    def step(self, back):
        src, dst = (self.undo, self.redo) if back else (self.redo, self.undo)
        if not src:
            return False
        rec = src.pop()
        x0, y0, before, after = rec
        h, w = before.shape[:2]
        self.buf[y0:y0 + h, x0:x0 + w] = before if back else after
        dst.append(rec)
        redraw()                # the overlay follows the undo list; the copy is not written
        return True

    def fill(self, st):
        """Fill the selection: one undo step. Returns the filled pixel count."""
        if self.sel_rect is None:
            return 0
        x0, y0, x1, y1 = self.sel_rect
        box = ipf.bbox(self.sel[y0:y1, x0:x1])
        if box is None:
            self.clear_selection()
            return 0
        rect = (x0 + box[0], y0 + box[1], x0 + box[2], y0 + box[3])
        size = max(rect[2] - rect[0], rect[3] - rect[1])
        # context around the hole: as big as the hole, but no more than CONTEXT_PX - the
        # fill learns from the pixels near its edge; a 1200 px hole made a 3600 px crop
        cx0, cy0, cx1, cy1 = ipf.crop_rect(rect, self.w, self.h,
                                           min(max(32, size), CONTEXT_PX) + st.grow
                                           + self.margin)
        crop = self.buf[cy0:cy1, cx0:cx1]
        hole = self.sel[cy0:cy1, cx0:cx1]
        labels = None
        if self.tris is not None:       # island per pixel; the bake margin joins its island
            labels = ipf.rasterize_triangles(self.tris, cx0, cy0, cx1 - cx0, cy1 - cy0,
                                             labels=self.islands)
            labels = ipf.dilate_labels(labels, self.margin) if labels.any() else None
        out, used = ipf.fill(crop, hole, grow=st.grow, grain=st.grain, labels=labels)
        self.clear_selection()
        ub = ipf.bbox(used)
        if ub is None:
            return 0
        ux0, uy0, ux1, uy1 = ub
        before = crop[uy0:uy1, ux0:ux1].copy()
        after = out[uy0:uy1, ux0:ux1].copy()
        crop[uy0:uy1, ux0:ux1] = after
        self._record(cx0 + ux0, cy0 + uy0, before, after)
        redraw()
        return int(used.sum())


def active():
    """The open session, or None (also drops one whose copy disappeared)."""
    global _session
    if _session is not None and _session.temp is None:
        _session = None
    return _session


def running():
    return _op is not None


def is_temp(img):
    return img is not None and KEY in img


def redraw():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                area.tag_redraw()


# ── Target ────────────────────────────────────────────────────────────────────

def target(context):
    """(image, Source): the source image this editor shows, else the active object's
    preferred one (sources.preferred)."""
    sp = context.space_data
    img = sp.image if sp is not None and sp.type == 'IMAGE_EDITOR' else None
    src = sources.classify(img)
    if src is not None:
        return img, src
    img = sources.preferred(context.active_object)
    src = sources.classify(img)
    return (img, src) if src is not None else (None, None)


def locked(src):
    """Why the source can't be inpainted now: its object is Locked Finish ('' = it can)."""
    if src is None or src.kind == 'BG':
        return ""
    d = getattr(src.owner, "multicamproject_bake", None)
    return "Locked Finish: unlock it in 08 first" if d is not None and d.locked else ""


def start_reason(context):
    """None when Inpaint can start (or resume) here, else why not."""
    if running():
        return "Inpaint is running"
    if active() is not None:
        return None                     # resume
    from . import lasso_draw, session
    if lasso_draw.get_active_op() is not None:
        return "The lasso is running"
    if session.active() is not None:
        return "A Liquify session is running - Bake or Cancel it first"
    img, src = target(context)
    if img is None:
        return "Show an ALB_, BAo_ or camera photo here, or select a baked object"
    why = locked(src)
    if why:
        return why
    path = _baking()[0].image_file(img)
    if not path or not os.path.isfile(path):
        return f"{img.name} has no file - bake it again"
    return None


def _uv_triangles(obj, w, h):
    """(uv_normal triangles of obj in image pixels (n, 3, 2), UV island per triangle), or
    None."""
    me = obj.data if obj.type == 'MESH' else None
    uv = me.uv_layers.get("uv_normal") if me is not None else None
    if uv is None:
        return None
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    if not n:
        return None
    loops = np.empty(n * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("loops", loops)
    uvs = np.empty(len(me.loops) * 2, dtype=np.float32)
    uv.data.foreach_get("uv", uvs)
    tris = uvs.reshape(-1, 2)[loops].reshape(n, 3, 2)
    vidx = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", vidx)
    islands = ipf.triangle_islands(vidx[loops].reshape(n, 3), tris)
    return tris * np.array((w, h), dtype=np.float32), islands


def start(context):
    """A working copy of the target ALB_, shown in this editor. Returns the session."""
    global _session
    reason = start_reason(context)
    if reason:
        raise ValueError(reason)
    if active() is not None:
        return _session
    common, _engine, _hm = _baking()
    alb, src = target(context)
    path = common.image_file(alb)
    file = bpy.data.images.load(path, check_existing=False)    # the file, not the float
    try:                                                       # buffer GN-Final may hold
        w, h = file.size
        if w < 2 or h < 2:
            raise ValueError(f"Cannot read {os.path.basename(path)}")
        buf = lasso_raster.read_pixels(file)
        is_float = file.is_float
    finally:
        bpy.data.images.remove(file)
    temp = bpy.data.images.new((PREFIX + alb.name)[:63], w, h, alpha=False,
                               float_buffer=is_float)
    temp.colorspace_settings.name = 'sRGB'
    temp[KEY] = alb.name
    margin = common.margin_px(common.settings(context.scene), w)
    # a baked texture fills per UV island of uv_normal; a photo is one piece
    tris = _uv_triangles(src.owner, w, h) if src.kind != 'BG' else None
    _session = Session(alb, src, temp, buf, tris, margin if src.kind != 'BG' else 0)
    _session.push()
    _ensure_handler()
    return _session


def _image_editors():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                yield area


def apply_uv(s=None):
    """Editors on the copy show the UV overlay as the Inpaint panel says (off by default:
    the wireframe hides the spots); each editor's own setting is kept for _restore_uv."""
    s = s or active()
    if s is None:
        return
    st = props.inpaint_settings()
    for area in _image_editors():
        sp = area.spaces.active
        if sp.image is None or sp.image.name != s.temp_name:
            continue
        uv = sp.uv_editor
        s.uv.setdefault(area.as_pointer(), (uv.show_uv, uv.uv_opacity))
        uv.show_uv = st.show_uv
        uv.uv_opacity = st.uv_opacity
        area.tag_redraw()


def _restore_uv(s):
    for area in _image_editors():
        saved = s.uv.get(area.as_pointer())
        if saved is not None:
            uv = area.spaces.active.uv_editor
            uv.show_uv, uv.uv_opacity = saved
            area.tag_redraw()
    s.uv.clear()


def _show(img_from, img_to, pins=None):
    """Editors on img_from show img_to. Setting the image from Python pins the editor: with
    `pins` ({area pointer: pin at Start}) the pin goes back to what it was, and an unpinned
    editor shows what Blender would show now - the active object's active Image Texture
    (it missed the selection change while it was pinned)."""
    for area in _image_editors():
        sp = area.spaces.active
        if sp.image != img_from:
            continue
        sp.image = img_to
        if pins is None:
            continue
        pin = pins.get(area.as_pointer(), False)
        if not pin and img_to is None:      # else it stays on what was inpainted
            follow = _auto_image(bpy.context.view_layer.objects.active)
            if follow is not None and follow != sp.image:
                sp.image = follow
        sp.use_image_pin = pin


def _auto_image(obj):
    """The image an unpinned editor follows for `obj`: the add-on's preferred source image
    (sources.preferred), else Blender's - the active material's active Image Texture node."""
    img = sources.preferred(obj)
    if img is not None:
        return img
    mat = obj.active_material if obj is not None else None
    nt = mat.node_tree if mat is not None else None
    node = nt.nodes.active if nt is not None else None
    if node is not None and node.type == 'TEX_IMAGE':
        return node.image
    return None


def save():
    """The copy written as the source's _inpaint file (the original is never written); the
    source's users take it. Ends the session. Returns the _inpaint image."""
    s = active()
    if s is None:
        raise ValueError("No Inpaint open")
    src, temp = s.src, s.temp
    if src is None or s.alb is None:
        raise ValueError(f"{s.alb_name} is gone - Discard")
    # the copy's pixels with every fill (they were only drawn so far), straight to the PNG
    img = sources.save_pixels(src, bpy.context.scene, s.buf, skip=temp)
    _end(img)
    return img


def discard():
    s = active()
    if s is not None:
        _end(s.alb)


def _end(show):
    """Editors on the copy go back to `show`; the copy is removed."""
    global _session
    s, _session = _session, None
    if s is None:
        return
    s.tex.clear()
    _remove_handler()
    temp = s.temp
    _restore_uv(s)
    if temp is not None:
        _show(temp, show, s.pins)
        bpy.data.images.remove(temp)
    act = bpy.context.view_layer.objects.active
    if (act.name if act is not None else "") != s.active_name:
        _on_active_changed()    # another object meanwhile: the editors follow it now
    redraw()


# ── Region mapping / drawing ──────────────────────────────────────────────────

def _window_region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None)


def _to_region(region, s):
    v2d = region.view2d
    x0, y0 = v2d.view_to_region(0.0, 0.0, clip=False)
    x1, y1 = v2d.view_to_region(1.0, 1.0, clip=False)
    return lambda px, py: (x0 + px / s.w * (x1 - x0), y0 + py / s.h * (y1 - y0))


def _overlay(s):
    """(texture, rect) of the selection in red, rebuilt when it changed."""
    if not s.overlay_dirty:
        return s.overlay
    s.overlay_dirty = False
    s.overlay = None
    if s.sel_rect is None or gpu is None:
        return None
    x0, y0, x1, y1 = s.sel_rect
    step = max(1, math.ceil(max(x1 - x0, y1 - y0) / OVERLAY_MAX))
    m = s.sel[y0:y1:step, x0:x1:step]
    if not m.any():
        return None
    rgba = np.zeros(m.shape + (4,), dtype=np.float32)
    rgba[..., 0] = 1.0
    rgba[..., 1] = 0.15
    rgba[..., 2] = 0.15
    rgba[..., 3] = m * 0.5
    try:
        tex = lasso_raster.make_texture(rgba)
    except Exception:
        return None
    s.overlay = (tex, (x0, y0, x0 + m.shape[1] * step, y0 + m.shape[0] * step))
    return s.overlay


def _line(region, pts, color, width=1.5, closed=False):
    if len(pts) < 2:
        return
    if closed:
        pts = list(pts) + [pts[0]]
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'LINE_STRIP', {"pos": pts})
    sh.bind()
    sh.uniform_float("viewportSize", (region.width, region.height))
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch.draw(sh)


def _draw_patches(s, to_region):
    """The fills over the copy, oldest first (the copy itself has none of them)."""
    from . import lasso_draw
    shader, kind = lasso_draw._get_image_shader()
    if shader is None:
        return
    for rec in s.undo:
        tex = s.patch_texture(rec)
        if tex is None:
            continue
        x0, y0, before = rec[0], rec[1], rec[2]
        x1, y1 = x0 + before.shape[1], y0 + before.shape[0]
        verts = [to_region(x0, y0), to_region(x1, y0), to_region(x1, y1), to_region(x0, y1)]
        lasso_draw._draw_tex_quad(shader, kind, tex, verts, (1.0, 1.0, 1.0, 1.0))


def _draw():
    s = active()
    if s is None or gpu is None:
        return
    ctx = bpy.context
    region, sp = ctx.region, ctx.space_data
    if region is None or sp is None or sp.image is None or sp.image.name != s.temp_name:
        return
    try:
        to_region = _to_region(region, s)
        gpu.state.blend_set('ALPHA')
        _draw_patches(s, to_region)
        op = _op
        if op is None or region.as_pointer() != op._region_ptr:
            gpu.state.blend_set('NONE')
            return
        ov = _overlay(s)
        if ov is not None:
            tex, (x0, y0, x1, y1) = ov
            from . import lasso_draw
            shader, kind = lasso_draw._get_image_shader()
            if shader is not None:
                verts = [to_region(x0, y0), to_region(x1, y0), to_region(x1, y1),
                         to_region(x0, y1)]
                lasso_draw._draw_tex_quad(shader, kind, tex, verts, (1.0, 1.0, 1.0, 1.0))
        if op._points:
            pts = [to_region(*p) for p in op._points]
            if op._hover_px is not None and not op._dragging:
                pts.append(to_region(*op._hover_px))
            color = (1.0, 0.4, 0.3, 1.0) if op._subtract else (1.0, 1.0, 1.0, 0.95)
            _line(region, pts, (0.0, 0.0, 0.0, 0.6), 3.0, closed=True)
            _line(region, pts, color, 1.2, closed=True)
        st = props.inpaint_settings()
        if st.mode == 'BRUSH' and op._hover_region is not None:
            from . import tool
            scale = abs(to_region(1.0, 0.0)[0] - to_region(0.0, 0.0)[0])
            tool.draw_circle(op._hover_region, st.size * 0.5 * scale)
        gpu.state.blend_set('NONE')
        _draw_status(op, s, region)
    except Exception as e:
        print(f"[MultiCamProject] Inpaint draw: {e}")
        gpu.state.blend_set('NONE')


def _draw_status(op, s, region):
    if blf is None:
        return
    st = props.inpaint_settings()
    tool_txt = ("Brush: paint · Alt/Ctrl erase · [ ] size" if st.mode == 'BRUSH' else
                "Lasso: drag, or click points · Alt/Ctrl subtract")
    text = (f"Inpaint {s.alb_name} — {tool_txt} · B/L tool · F fill · Alt+A deselect · "
            f"Ctrl+Z / Ctrl+Shift+Z · Enter save · Esc ask   ({len(s.undo)} fill(s))")
    y = region.height - 24
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'TRI_FAN', {"pos": [(0, y), (region.width, y),
                                                   (region.width, region.height),
                                                   (0, region.height)]})
    gpu.state.blend_set('ALPHA')
    sh.bind()
    sh.uniform_float("color", (0.08, 0.08, 0.08, 0.8))
    batch.draw(sh)
    gpu.state.blend_set('NONE')
    blf.position(0, 10, y + 7, 0)
    blf.size(0, 13)
    blf.color(0, 1.0, 1.0, 1.0, 1.0)
    blf.draw(0, text)


# ── The modal ─────────────────────────────────────────────────────────────────

def _selection_key(context):
    vl = context.view_layer
    act = vl.objects.active
    return (act.name if act is not None else "",
            frozenset(o.name for o in vl.objects if o.select_get(view_layer=vl)))


class MULTICAMPROJECT_OT_inpaint(bpy.types.Operator):
    """Remove spots (markers) from the shown ALB_, BAo_ or camera photo on a copy: Brush or
    Lasso a selection, it fills from the pixels around it. Enter saves the copy as the
    _inpaint file (the original stays), Esc asks. Another selection or another image in this
    editor asks too"""
    bl_idname = "multicamproject.inpaint"
    bl_label = "Inpaint"
    bl_options = {'REGISTER'}       # pixel writes are outside Blender's undo

    @classmethod
    def poll(cls, context):
        if context.area is None or context.area.type != 'IMAGE_EDITOR':
            return False
        reason = start_reason(context)
        if reason:
            cls.poll_message_set(reason)
        return reason is None

    def invoke(self, context, event):
        global _op
        try:
            s = start(context)
        except (ValueError, RuntimeError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        region = _window_region(context.area)
        if region is None:
            return {'CANCELLED'}
        sp = context.space_data
        s.pins.setdefault(context.area.as_pointer(), sp.use_image_pin)
        sp.image = s.temp                   # pins the editor: _end puts the pin back
        apply_uv(s)                         # UV overlay off (panel toggle); _end restores it
        self._s = s
        self._area = context.area
        self._region = region
        self._region_ptr = region.as_pointer()
        self._sel_key = _selection_key(context)
        self._points = []           # lasso, image pixels
        self._dragging = False      # LMB is down
        self._stroke = None         # 'PAINT' / 'ERASE' / 'LASSO'
        self._subtract = False
        self._free = False          # the lasso press may still become a drag
        self._press_region = None
        self._last_px = None
        self._hover_px = None
        self._hover_region = None
        self._last_click = (0.0, None)
        _op = self
        context.window_manager.modal_handler_add(self)
        redraw()
        return {'RUNNING_MODAL'}

    # helpers
    def _region_xy(self, event):
        return event.mouse_x - self._region.x, event.mouse_y - self._region.y

    def _px(self, event):
        rx, ry = self._region_xy(event)
        u, v = self._region.view2d.region_to_view(rx, ry)
        return u * self._s.w, v * self._s.h

    def _in_region(self, event):
        mx, my = event.mouse_x, event.mouse_y
        r = self._region
        if not (r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height):
            return False
        for o in self._area.regions:
            if (o.type != 'WINDOW' and o.width > 1 and o.height > 1
                    and o.x <= mx < o.x + o.width and o.y <= my < o.y + o.height):
                # an overlapping sidebar / toolbar is see-through below its last panel
                bottom = _panels_bottom.get(o.as_pointer())
                if bottom is not None and my - o.y < bottom:
                    continue
                return False
        return True

    def _in_area(self, event):
        a = self._area
        return a.x <= event.mouse_x < a.x + a.width and a.y <= event.mouse_y < a.y + a.height

    def _lost(self, context):
        """Why the modal has to stop (asks Save / Discard), or None."""
        try:
            img = self._area.spaces.active.image
        except Exception:
            return "the editor closed"
        if img is None or img.name != self._s.temp_name:
            return "another image in the editor"
        if _selection_key(context) != self._sel_key:
            return "another selection"
        return None

    def _stop(self, context):
        global _op
        _op = None
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        redraw()

    def _ask(self, context, why):
        """End the modal; with fills, ask Save / Discard / Keep."""
        self._stop(context)
        if not self._s.undo:
            discard()
            self.report({'INFO'}, "Inpaint closed" + (f": {why}" if why else ""))
            return {'CANCELLED'}
        self._s.clear_selection()
        try:
            bpy.ops.multicamproject.inpaint_exit('INVOKE_DEFAULT', reason=why or "")
        except Exception as e:
            print(f"[MultiCamProject] Inpaint: no dialog ({e}) - the panel saves or discards")
        return {'CANCELLED'}

    # modal
    def modal(self, context, event):
        s = active()
        if s is not self._s:                    # saved / discarded from the panel
            self._stop(context)
            return {'FINISHED'}
        try:
            self._area.tag_redraw()
        except Exception:
            return self._ask(context, "the editor closed")
        why = self._lost(context)
        if why:
            return self._ask(context, why)

        et, ev = event.type, event.value
        if et in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
                  'MIDDLEMOUSE'}:
            return {'PASS_THROUGH'}
        st = props.inpaint_settings(context)

        if et in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            inside = self._in_region(event)
            self._hover_region = self._region_xy(event) if inside else None
            self._hover_px = self._px(event)
            if self._dragging:
                self._drag_to(st, self._hover_px)
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'} if inside else {'PASS_THROUGH'}

        if et == 'LEFTMOUSE':
            if ev == 'PRESS':
                if not self._in_region(event):
                    return {'PASS_THROUGH'}
                return self._press(context, event, st)
            if ev == 'RELEASE' and self._dragging:
                return self._release(context, event, st)
            return {'RUNNING_MODAL'} if self._in_region(event) else {'PASS_THROUGH'}

        if ev != 'PRESS' or not self._in_area(event):
            return {'PASS_THROUGH'}

        if et == 'ESC':
            if self._points:
                self._points = []
                return {'RUNNING_MODAL'}
            return self._ask(context, None)
        if et in {'RET', 'NUMPAD_ENTER'}:
            if len(self._points) >= 3:
                self._close_lasso(st)
                return {'RUNNING_MODAL'}
            return self._save(context)
        if et == 'Z' and event.ctrl:
            if self._points and not event.shift:
                self._points.pop()
            elif not s.step(back=not event.shift):
                self.report({'INFO'}, "Inpaint: nothing to " + ("redo" if event.shift else "undo"))
            return {'RUNNING_MODAL'}
        if et == 'B':
            st.mode = 'BRUSH'
            self._points = []
        elif et == 'L':
            st.mode = 'LASSO'
        elif et == 'F':
            self._fill(st)
        elif et == 'A' and event.alt:
            self._points = []
            s.clear_selection()
        elif et == 'LEFT_BRACKET':
            st.size = max(2, round(st.size / 1.15))
        elif et == 'RIGHT_BRACKET':
            st.size = min(2048, round(st.size * 1.15) + 1)
        elif not self._in_region(event):
            return {'PASS_THROUGH'}
        redraw()
        return {'RUNNING_MODAL'}

    def _press(self, context, event, st):
        px = self._px(event)
        self._subtract = event.alt or event.ctrl
        self._dragging = True
        self._press_region = self._region_xy(event)
        if st.mode == 'BRUSH':
            self._stroke = 'ERASE' if self._subtract else 'PAINT'
            self._last_px = px
            self._s.touch(ipf.stamp_disc(self._s.sel, px[0], px[1], st.size * 0.5,
                                         value=self._stroke == 'PAINT'))
            return {'RUNNING_MODAL'}
        self._stroke = 'LASSO'
        if self._points:                        # click mode: next point, or close
            rx, ry = self._press_region
            now = time.monotonic()
            t0, p0 = self._last_click
            self._last_click = (now, (rx, ry))
            fx, fy = _to_region(self._region, self._s)(*self._points[0])
            double = p0 is not None and now - t0 < 0.35 and math.hypot(rx - p0[0],
                                                                          ry - p0[1]) < 6
            if len(self._points) >= 3 and (double or math.hypot(rx - fx, ry - fy) <= CLOSE_PX):
                self._dragging = False
                self._close_lasso(st)
                return {'RUNNING_MODAL'}
        self._points.append(px)
        self._free = not self._points[:-1]      # a press that starts a lasso may drag
        return {'RUNNING_MODAL'}

    def _drag_to(self, st, px):
        s = self._s
        if self._stroke in {'PAINT', 'ERASE'}:
            x0, y0 = self._last_px
            r = st.size * 0.5
            d = math.hypot(px[0] - x0, px[1] - y0)
            n = max(1, int(d / max(1.0, r * 0.3)))
            for i in range(1, n + 1):
                t = i / n
                s.touch(ipf.stamp_disc(s.sel, x0 + (px[0] - x0) * t, y0 + (px[1] - y0) * t,
                                       r, value=self._stroke == 'PAINT'))
            self._last_px = px
        elif self._stroke == 'LASSO' and self._free:
            lx, ly = self._points[-1]
            if math.hypot(px[0] - lx, px[1] - ly) * self._px_scale() >= 3.0:
                self._points.append(px)

    def _px_scale(self):
        """Region pixels per image pixel."""
        tr = _to_region(self._region, self._s)
        return abs(tr(1.0, 0.0)[0] - tr(0.0, 0.0)[0]) or 1.0

    def _release(self, context, event, st):
        self._dragging = False
        stroke, self._stroke = self._stroke, None
        if stroke in {'PAINT', 'ERASE'}:
            if stroke == 'PAINT' and st.auto_fill:
                self._fill(st)
            return {'RUNNING_MODAL'}
        if stroke == 'LASSO' and self._free:
            rx, ry = self._region_xy(event)
            px, py = self._press_region
            if math.hypot(rx - px, ry - py) >= DRAG_PX and len(self._points) >= 3:
                self._close_lasso(st)           # a free lasso closes on release
            else:
                self._points = self._points[:1]     # a click: point 1 of a click lasso
            self._free = False
        return {'RUNNING_MODAL'}

    def _close_lasso(self, st):
        s, pts = self._s, self._points
        self._points = []
        if len(pts) < 3:
            return
        a = np.asarray(pts, dtype=np.float32)
        x0 = max(0, int(math.floor(float(a[:, 0].min()))))
        y0 = max(0, int(math.floor(float(a[:, 1].min()))))
        x1 = min(s.w, int(math.ceil(float(a[:, 0].max()))) + 1)
        y1 = min(s.h, int(math.ceil(float(a[:, 1].max()))) + 1)
        if x1 <= x0 or y1 <= y0:
            return
        m = lasso_raster.rasterize_polygon(pts, x0, y0, x1 - x0, y1 - y0)
        if self._subtract:
            s.sel[y0:y1, x0:x1] &= ~m
        else:
            s.sel[y0:y1, x0:x1] |= m
        s.touch((x0, y0, x1, y1))
        if st.auto_fill and not self._subtract:
            self._fill(st)
        redraw()

    def _fill(self, st):
        bpy.context.window.cursor_set('WAIT')
        try:
            n = self._s.fill(st)
        finally:
            bpy.context.window.cursor_set('DEFAULT')
        if not n:
            self.report({'INFO'}, "Inpaint: nothing selected to fill")

    def _save(self, context):
        self._stop(context)
        if not self._s.undo:
            discard()
            self.report({'INFO'}, "Inpaint: no fills - nothing saved")
            return {'CANCELLED'}
        return _report_save(self)

    def cancel(self, context):
        """Blender ends the modal (file load, window closed): the copy waits in the panel."""
        self._stop(context)


def _report_save(op):
    try:
        img = save()
    except Exception as e:
        op.report({'ERROR'}, f"Inpaint save failed: {e}")
        return {'CANCELLED'}
    op.report({'INFO'}, f"Inpaint saved as {img.name} (the original stays as it was)")
    return {'FINISHED'}


def _measure_panels():
    """Draw handler of the Image Editor's sidebar / toolbar: with Region Overlap they are
    drawn see-through, opaque only where a panel is. One pixel column read back says where
    the lowest panel ends - below it the strokes reach the image (Blender's API has no panel
    bounds)."""
    region = bpy.context.region
    if region is None or region.height < 2 or region.width < 2:
        return
    try:
        fb = gpu.state.active_framebuffer_get()
        x = min(region.width - 1, max(0, region.width // 2))
        col = fb.read_color(x, 0, 1, region.height, 4, 0, 'FLOAT')
        col.dimensions = region.height * 4
        a = np.asarray(col, dtype=np.float32)[3::4]
    except Exception:
        _panels_bottom.pop(region.as_pointer(), None)
        return
    drawn = np.nonzero(a > 0.5)[0]
    _panels_bottom[region.as_pointer()] = int(drawn[0]) if len(drawn) else region.height


def _ensure_handler():
    global _draw_handle
    if not _panel_handles and gpu is not None and bpy.context.preferences.system.use_region_overlap:
        for kind in ('UI', 'TOOLS'):
            try:
                _panel_handles.append((bpy.types.SpaceImageEditor.draw_handler_add(
                    _measure_panels, (), kind, 'POST_PIXEL'), kind))
            except Exception:
                pass
        for area in _image_editors():
            for r in area.regions:
                if r.type in {'UI', 'TOOLS'}:
                    r.tag_redraw()
    if _draw_handle is None and gpu is not None:
        _draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), 'WINDOW',
                                                                   'POST_PIXEL')


def _remove_handler():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    for h, kind in _panel_handles:
        try:
            bpy.types.SpaceImageEditor.draw_handler_remove(h, kind)
        except Exception:
            pass
    _panel_handles.clear()
    _panels_bottom.clear()


# ── Panel operators ───────────────────────────────────────────────────────────

class MULTICAMPROJECT_OT_inpaint_exit(bpy.types.Operator):
    """Save the Inpaint copy as the _inpaint file, discard it, or keep it for later"""
    bl_idname = "multicamproject.inpaint_exit"
    bl_label = "Inpaint"
    bl_options = {'REGISTER', 'INTERNAL'}

    reason: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action: EnumProperty(
        name="Action", default='SAVE',
        items=[('SAVE', "Save", "Write the copy as the _inpaint file (the original stays)"),
               ('DISCARD', "Discard", "Throw the fills away"),
               ('KEEP', "Keep", "Keep the copy - Resume, Save or Discard it in the panel")])

    @classmethod
    def poll(cls, context):
        return active() is not None and not running()

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=360, title="Inpaint - save the fills?", confirm_text="OK")

    def draw(self, context):
        s = active()
        col = self.layout.column()
        if self.reason:
            col.label(text=f"Stopped: {self.reason}", icon='INFO')
        if s is not None:
            col.label(text=f"{len(s.undo)} fill(s) on {s.alb_name}")
        col.row().prop(self, "action", expand=True)

    def execute(self, context):
        if self.action == 'SAVE':
            return _report_save(self)
        if self.action == 'DISCARD':
            discard()
            self.report({'INFO'}, "Inpaint discarded")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_inpaint_save(bpy.types.Operator):
    """Write the Inpaint copy as the _inpaint file (the original stays as it was) and end the
    Inpaint (Enter). What was made from the source goes back a step"""
    bl_idname = "multicamproject.inpaint_save"
    bl_label = "Save _inpaint"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        s = active()
        return s is not None and bool(s.undo)

    def execute(self, context):
        return _report_save(self)


class MULTICAMPROJECT_OT_inpaint_discard(bpy.types.Operator):
    """Throw the Inpaint copy away: the image stays as it was"""
    bl_idname = "multicamproject.inpaint_discard"
    bl_label = "Discard"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return active() is not None

    def execute(self, context):
        discard()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_inpaint_step(bpy.types.Operator):
    """Undo / redo one fill of the Inpaint (Ctrl+Z / Ctrl+Shift+Z while it runs)"""
    bl_idname = "multicamproject.inpaint_step"
    bl_label = "Inpaint Undo"
    bl_options = {'REGISTER', 'INTERNAL'}

    redo: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return "Redo the next fill" if properties.redo else "Undo the last fill"

    @classmethod
    def poll(cls, context):
        return active() is not None

    def execute(self, context):
        if not active().step(back=not self.redo):
            return {'CANCELLED'}
        return {'FINISHED'}


class MULTICAMPROJECT_OT_inpaint_fill(bpy.types.Operator):
    """Fill the selection from the pixels around it (F)"""
    bl_idname = "multicamproject.inpaint_fill"
    bl_label = "Fill Selection"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        s = active()
        return s is not None and s.sel_rect is not None

    def execute(self, context):
        active().fill(props.inpaint_settings(context))
        return {'FINISHED'}


def _source_from(kind, owner_name):
    owner = bpy.data.objects.get(owner_name)
    return sources.Source(kind, owner) if owner is not None and kind in sources.KINDS else None


def _editor_area(context):
    """An Image Editor to show a source in: this one, else the first of this window, else of
    any window. (window, area) or (None, None)."""
    if context.area is not None and context.area.type == 'IMAGE_EDITOR':
        return context.window, context.area
    wins = [context.window] + [w for w in context.window_manager.windows if w != context.window]
    for win in wins:
        for area in (win.screen.areas if win is not None else ()):
            if area.type == 'IMAGE_EDITOR':
                return win, area
    return None, None


_SRC_PROPS = dict(
    kind=EnumProperty(items=[('ALB', "ALB_", ""), ('BAO', "BAo_", ""), ('BG', "Camera BG", ""),
                             ('SHOWN', "Shown", "The image the editor shows")],
                      default='SHOWN', options={'HIDDEN', 'SKIP_SAVE'}),
    owner=StringProperty(options={'HIDDEN', 'SKIP_SAVE'}))


def _op_source(op, context):
    if op.kind == 'SHOWN':
        return target(context)[1]
    return _source_from(op.kind, op.owner)


class MULTICAMPROJECT_OT_inpaint_load(bpy.types.Operator):
    """Show this image in the Image Editor and start Inpaint on it - its _inpaint copy when
    there is one. Nothing is written until you Save (then <name>_inpaint is made)"""
    bl_idname = "multicamproject.inpaint_load"
    bl_label = "Inpaint"
    bl_options = {'REGISTER'}

    __annotations__ = dict(_SRC_PROPS)

    def execute(self, context):
        src = _op_source(self, context)
        if src is None:
            self.report({'ERROR'}, "Nothing to inpaint")
            return {'CANCELLED'}
        why = locked(src)
        if why:
            self.report({'WARNING'}, why)
            return {'CANCELLED'}
        if running() or active() is not None:
            self.report({'WARNING'}, "An Inpaint is open - Save or Discard it first")
            return {'CANCELLED'}
        img = sources.current(src)
        win, area = _editor_area(context)
        if area is None:
            self.report({'WARNING'}, "Open an Image Editor (e.g. the UV Editing workspace), "
                                     "then press this again")
            return {'CANCELLED'}
        sp = area.spaces.active
        pin = sp.use_image_pin
        sp.image = img
        sp.use_image_pin = pin
        region = _window_region(area)
        with context.temp_override(window=win, area=area, region=region,
                                   space_data=sp, screen=win.screen):
            if bpy.ops.multicamproject.inpaint.poll():
                bpy.ops.multicamproject.inpaint('INVOKE_DEFAULT')
            else:
                self.report({'INFO'}, f"{img.name} shown - Inpaint: {start_reason(context)}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_inpaint_reload(bpy.types.Operator):
    """Start the _inpaint over: the original's pixels are copied into it (the materials keep
    showing the _inpaint). What was made from it goes back a step"""
    bl_idname = "multicamproject.inpaint_reload"
    bl_label = "Reload from Original"
    bl_options = {'REGISTER'}

    __annotations__ = dict(_SRC_PROPS)

    @classmethod
    def poll(cls, context):
        return active() is None

    def execute(self, context):
        src = _op_source(self, context)
        if src is None or locked(src):
            self.report({'WARNING'}, locked(src) or "Nothing to reload")
            return {'CANCELLED'}
        try:
            img = sources.reload(src, context.scene)
        except (RuntimeError, OSError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"{img.name}: the original again")
        redraw()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_inpaint_remove(bpy.types.Operator):
    """Remove the _inpaint: its file goes to the Recycle Bin and the materials show the
    original again. What was made from it goes back a step"""
    bl_idname = "multicamproject.inpaint_remove"
    bl_label = "Remove Inpaint"
    bl_options = {'REGISTER'}

    __annotations__ = dict(_SRC_PROPS)

    @classmethod
    def poll(cls, context):
        return active() is None

    def execute(self, context):
        src = _op_source(self, context)
        if src is None or locked(src):
            self.report({'WARNING'}, locked(src) or "Nothing to remove")
            return {'CANCELLED'}
        if sources.inpaint_image(src) is None:
            self.report({'INFO'}, "No _inpaint to remove")
            return {'CANCELLED'}
        ip = sources.inpaint_image(src)
        orig = sources.original(src)
        for area in _image_editors():           # editors on the copy show the original
            if area.spaces.active.image == ip:
                area.spaces.active.image = orig
                area.spaces.active.use_image_pin = False
        sources.remove(src, context.scene)
        self.report({'INFO'}, f"{sources.label(src)}: back to the original")
        redraw()
        return {'FINISHED'}


# ── Handlers ──────────────────────────────────────────────────────────────────

def recover():
    """Remove leftover copies (a file saved during an Inpaint, an undo past Start)."""
    s = active()
    live = s.temp_name if s else None
    for img in [i for i in bpy.data.images if KEY in i and i.name != live]:
        alb = bpy.data.images.get(img.get(KEY, ""))
        _show(img, alb)
        bpy.data.images.remove(img)


@persistent
def _on_load(_):
    global _session, _op
    _session, _op = None, None
    _remove_handler()
    recover()


@persistent
def _on_undo(_):
    """Blender's undo reloads IDs: put the copy's pixels back (or a new copy if it went)."""
    global _session
    s = _session
    if s is None:
        recover()
        return
    temp = s.temp
    if temp is None:
        temp = bpy.data.images.new(s.temp_name, s.w, s.h, alpha=False)
        temp.colorspace_settings.name = 'sRGB'
        temp[KEY] = s.alb_name
        s.temp_name = temp.name
    s.push()


# ── The editor follows the active object ──────────────────────────────────────
#
# Blender's UV editor follows the active material's active Image Texture node; MCP_ (slot 1)
# has none, so selecting a baked object left the editor empty. A new active object puts its
# preferred image (ALB_ inpainted > ALB_ > BAo_ inpainted > BAo_, sources.preferred) into
# every unpinned Image Editor that shows nothing or another source image; any other image
# stays. Not while an Inpaint is open: its copy stays. A msgbus on the active object - no
# timer runs while nothing changes.

_owner = object()


def follow_active():
    if active() is not None:
        return None
    obj = bpy.context.view_layer.objects.active
    img = sources.preferred(obj)
    if img is None:
        return None
    for area in _image_editors():
        sp = area.spaces.active
        if sp.use_image_pin or sp.image == img:
            continue
        if sp.image is not None and sources.classify(sp.image) is None:
            continue
        sp.image = img
        sp.use_image_pin = False        # setting it from Python pins it
    return None


def _on_active_changed():
    # after Blender's own update of the editors (and outside the msgbus callback)
    if not bpy.app.timers.is_registered(follow_active):
        bpy.app.timers.register(follow_active, first_interval=0.05)


def _subscribe():
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.LayerObjects, "active"), owner=_owner, args=(),
                             notify=_on_active_changed, options={'PERSISTENT'})


@persistent
def _on_load_follow(_):
    _subscribe()


_HANDLERS = ((bpy.app.handlers.load_post, _on_load),
             (bpy.app.handlers.load_post, _on_load_follow),
             (bpy.app.handlers.undo_post, _on_undo),
             (bpy.app.handlers.redo_post, _on_undo))

CLASSES = (MULTICAMPROJECT_OT_inpaint,
           MULTICAMPROJECT_OT_inpaint_load,
           MULTICAMPROJECT_OT_inpaint_reload,
           MULTICAMPROJECT_OT_inpaint_remove,
           MULTICAMPROJECT_OT_inpaint_exit,
           MULTICAMPROJECT_OT_inpaint_save,
           MULTICAMPROJECT_OT_inpaint_discard,
           MULTICAMPROJECT_OT_inpaint_step,
           MULTICAMPROJECT_OT_inpaint_fill)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for lst, fn in _HANDLERS:
        if fn not in lst:
            lst.append(fn)
    _subscribe()


def unregister():
    global _op
    _op = None
    bpy.msgbus.clear_by_owner(_owner)
    if bpy.app.timers.is_registered(follow_active):
        bpy.app.timers.unregister(follow_active)
    _remove_handler()
    try:
        discard()
    except Exception as e:
        print(f"[MultiCamProject] Inpaint discard on unregister failed: {e}")
    for lst, fn in _HANDLERS:
        if fn in lst:
            lst.remove(fn)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
