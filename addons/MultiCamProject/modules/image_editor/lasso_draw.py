"""
lasso_draw.py — GPU draw handler for the lasso transform preview.

Owns the POST_PIXEL draw handlers (Image Editor + 3D Viewport) and the reference to
the currently-running lasso operator (one at a time). The operator registers
itself here on invoke and clears itself on cleanup; all on-screen drawing — the
CUT hole, the floating cut-out, the lasso outline and the status banner —
happens in this module by reading state off that operator instance.

The view keeps drawing the photo itself (Image Editor, or the camera background
in the 3D Viewport, with its own colour management); the hole is a checker or
fill-colour overlay on the lifted footprint and the floating piece a textured
quad on top, placed through the operator's _Mapper. No pixels are written here:
the bake lives in lasso_raster.composite_float, called from the operator.

Ported from DomeAnimatic (modules/painting_cel/lasso_draw.py) without the cel
stack: drawing is limited to the region the session runs in.
"""

import bpy

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


# ── Module-level draw state (one running op at a time) ─────────────────────────

_DRAW_HANDLE = None   # SpaceImageEditor POST_PIXEL handle
_ACTIVE_OP   = None   # the running lasso operator instance
_DIAG_DONE   = False

BANNER_H = 24


def _diag(msg: str) -> None:
    global _DIAG_DONE
    if not _DIAG_DONE:
        print(f"[MultiCamProject] Lasso draw: {msg}")
        _DIAG_DONE = True


# ── Active-op / handler lifecycle (called by the operator) ────────────────────

def get_active_op():
    return _ACTIVE_OP


def set_active_op(op) -> None:
    global _ACTIVE_OP
    _ACTIVE_OP = op


def clear_active_op() -> None:
    global _ACTIVE_OP
    _ACTIVE_OP = None


def ensure_handler() -> None:
    global _DRAW_HANDLE
    if _DRAW_HANDLE is None and gpu is not None:
        _DRAW_HANDLE = [(space, space.draw_handler_add(_draw_lasso, (), 'WINDOW', 'POST_PIXEL'))
                        for space in (bpy.types.SpaceImageEditor, bpy.types.SpaceView3D)]


def remove_handler() -> None:
    global _DRAW_HANDLE
    if _DRAW_HANDLE is not None:
        for space, handle in _DRAW_HANDLE:
            try:
                space.draw_handler_remove(handle, 'WINDOW')
            except Exception:
                pass
        _DRAW_HANDLE = None


# ── Shaders ───────────────────────────────────────────────────────────────────

_IMG_SHADER      = None
_IMG_SHADER_KIND = None


def _get_image_shader():
    global _IMG_SHADER, _IMG_SHADER_KIND
    if _IMG_SHADER is not None:
        return _IMG_SHADER, _IMG_SHADER_KIND
    if gpu is None:
        return None, None
    for name in ('IMAGE_COLOR', 'IMAGE'):
        try:
            _IMG_SHADER      = gpu.shader.from_builtin(name)
            _IMG_SHADER_KIND = name
            return _IMG_SHADER, _IMG_SHADER_KIND
        except Exception:
            continue
    return None, None


def _get_line_shader():
    for name in ('POLYLINE_UNIFORM_COLOR', 'UNIFORM_COLOR'):
        try:
            return gpu.shader.from_builtin(name), name
        except Exception:
            continue
    return None, None


# ── Draw handler ──────────────────────────────────────────────────────────────

def _draw_lasso() -> None:
    op = _ACTIVE_OP
    if op is None or gpu is None:
        return
    ctx    = bpy.context
    region = ctx.region
    if region is None or region.type != 'WINDOW':
        return
    try:
        if region.as_pointer() != op._region_ptr:
            return
        rv3d = getattr(ctx.space_data, 'region_3d', None)
        m = op._mapper(region, rv3d)
    except Exception as e:
        _diag(f"mapper: {e}")
        return

    try:
        if m is not None:
            if op._state != 'DRAW':
                _draw_float(op, region, m)
            _draw_outline(op, region, m.to_region)
        _draw_status(op, region)
    except Exception as e:
        _diag(f"draw: {e}")


_UVS     = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
_INDICES = [(0, 1, 2), (0, 2, 3)]


def _draw_float(op, region, m) -> None:
    """The CUT hole (only while the view shows the source image) and the
    transformed floating cut-out, clipped to the photo's screen bounds."""
    shader, kind = _get_image_shader()
    if shader is None:
        _diag("no image shader")
        return

    to_region = m.to_region
    x0, y0, x1, y1 = m.rect
    sc_x = max(0, int(round(x0)))
    sc_y = max(0, int(round(y0)))
    sc_w = min(region.width,  int(round(x1))) - sc_x
    sc_h = min(region.height, int(round(y1))) - sc_y
    if sc_w <= 0 or sc_h <= 0:
        return

    try:
        gpu.state.scissor_set(sc_x, sc_y, sc_w, sc_h)
        gpu.state.scissor_test_set(True)
        gpu.state.blend_set('ALPHA')

        hole_tex = op._hole_texture() if op._hole_visible() else None
        if hole_tex is not None:
            bx1, by1 = op._bx0 + op._pw, op._by0 + op._ph
            hverts = [to_region(op._bx0, op._by0), to_region(bx1, op._by0),
                      to_region(bx1, by1), to_region(op._bx0, by1)]
            _draw_tex_quad(shader, kind, hole_tex, hverts, (1.0, 1.0, 1.0, 1.0))

        if op._float_tex is not None:
            fverts = [to_region(cx, cy) for cx, cy in op._transformed_bbox_corners()]
            _draw_tex_quad(shader, kind, op._float_tex, fverts, (1.0, 1.0, 1.0, 1.0))
    finally:
        gpu.state.blend_set('NONE')
        gpu.state.scissor_test_set(False)


def _draw_tex_quad(shader, kind, tex, verts, rgba) -> None:
    try:
        batch = batch_for_shader(shader, 'TRIS',
                                 {"pos": verts, "texCoord": _UVS},
                                 indices=_INDICES)
    except Exception as e:
        _diag(f"batch_for_shader: {e}")
        return
    shader.bind()
    try:
        shader.uniform_sampler("image", tex)
    except Exception as e:
        _diag(f"uniform_sampler: {e}")
    if kind == 'IMAGE_COLOR':
        try:
            shader.uniform_float("color", rgba)
        except Exception as e:
            _diag(f"uniform_float: {e}")
    batch.draw(shader)


def _draw_outline(op, region, to_region) -> None:
    """Lasso polygon outline: points + rubber band in DRAW, the transformed
    selection boundary in the floating states."""
    if op._state == 'DRAW':
        if not op._points:
            return
        pts = [to_region(px, py) for px, py in op._points]
        if op._cursor_px is not None:
            pts.append(to_region(*op._cursor_px))
        pts.append(pts[0])   # closing hint back to the first point
        color = (1.0, 1.0, 1.0, 0.9)
    else:
        moved = op._affine_apply_points(op._points)
        pts   = [to_region(px, py) for px, py in moved]
        pts.append(pts[0])
        color = (0.2, 0.8, 1.0, 0.9)

    shader, kind = _get_line_shader()
    if shader is None:
        return
    gpu.state.blend_set('ALPHA')
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": pts})
    shader.bind()
    if kind == 'POLYLINE_UNIFORM_COLOR':
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", 2.0)
    shader.uniform_float("color", color)
    batch.draw(shader)

    # First-point handle so the user can see where clicking closes the lasso
    if op._state == 'DRAW':
        pt_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.point_size_set(8.0)
        pt_batch = batch_for_shader(pt_shader, 'POINTS', {"pos": [pts[0]]})
        pt_shader.bind()
        pt_shader.uniform_float("color", (1.0, 0.6, 0.1, 1.0))
        pt_batch.draw(pt_shader)
        gpu.state.point_size_set(1.0)
    gpu.state.blend_set('NONE')


_STATUS_TEXT = {
    'DRAW':       "click points · click point 1 / double-click / Enter close · "
                  "Ctrl+Z undo · Enter (no lasso) apply & exit · Esc revert & exit",
    'FLOAT_IDLE': "drag/G move · R/S · Shift+D stamp · X delete · Ctrl+C/V · "
                  "click outside/K apply + next · Ctrl+Z undo · Enter apply & exit · "
                  "Esc revert & exit",
    'GRAB':       "Grab: move mouse · release/LMB/Enter confirm · RMB/Esc cancel",
    'ROTATE':     "Rotate around selection center · LMB/Enter confirm · RMB/Esc cancel",
    'SCALE':      "Scale around selection center · LMB/Enter confirm · RMB/Esc cancel",
}


def _draw_status(op, region) -> None:
    if blf is None:
        return
    text = _STATUS_TEXT.get(op._state, "")
    if op._state != 'DRAW':
        mode = "cut" if op._source_mode == 'CUT' and op._hole_live else "copy"
        dest = op._cur_name or "no image"
        if dest == op._src_name:
            text += f"   [{mode}]"
        else:
            text += f"   [{mode} {op._src_name} -> {dest}]"
    if op._recs:
        text += f"   ({len(op._recs)} applied)"
    y = region.height - BANNER_H
    gpu.state.blend_set('ALPHA')
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch  = batch_for_shader(shader, 'TRI_FAN', {"pos": [
        (0, y), (region.width, y),
        (region.width, region.height), (0, region.height),
    ]})
    shader.bind()
    shader.uniform_float("color", (0.08, 0.08, 0.08, 0.8))
    batch.draw(shader)
    gpu.state.blend_set('NONE')
    blf.position(0, 10, y + 7, 0)
    blf.size(0, 13)
    blf.color(0, 1.0, 1.0, 1.0, 1.0)
    where = f" {op._cam_name}" if op._view == 'CAMERA' else ""
    blf.draw(0, f"Lasso{where} — {text}")
