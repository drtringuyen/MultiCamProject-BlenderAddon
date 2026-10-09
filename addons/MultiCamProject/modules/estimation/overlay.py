"""The Estimation list as a coloured table in the 3D Viewport's bottom-left corner (Show in
Viewport): whole rows green / orange / black, To Go in green (+) or red (-). A panel row
can't take a background colour - here it is drawn with the GPU module."""
import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import core

ROW_BG = {core.DONE: (0.10, 0.42, 0.16, 0.90), core.OVER: (0.80, 0.40, 0.04, 0.90),
          core.NEW: (0.02, 0.02, 0.02, 0.88)}
HEAD_BG = (0.16, 0.16, 0.16, 0.90)
DELTA_BG = (0.0, 0.0, 0.0, 0.45)        # under To Go: red / green read on any row
PLUS, MINUS = (0.45, 1.0, 0.45, 1.0), (1.0, 0.36, 0.30, 1.0)
TEXT, DIM = (1.0, 1.0, 1.0, 1.0), (0.75, 0.75, 0.75, 1.0)
FONT = 0
_handle = None


def _rect(shader, x, y, w, h, color):
    batch = batch_for_shader(shader, 'TRIS', {"pos": ((x, y), (x + w, y), (x + w, y + h),
                                                      (x, y + h))},
                             indices=((0, 1, 2), (0, 2, 3)))
    shader.uniform_float("color", color)
    batch.draw(shader)


def _frame(shader, x, y, w, h, color):
    batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": ((x, y), (x + w, y), (x + w, y + h),
                                                           (x, y + h))})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _text(x, y, text, color, right_edge=None):
    blf.color(FONT, *color)
    if right_edge is not None:          # numbers: right-aligned in their column
        x = right_edge - blf.dimensions(FONT, text)[0]
    blf.position(FONT, x, y, 0)
    blf.draw(FONT, text)


def _left_edge(area):
    """Past the toolbar when it overlaps the view."""
    if not bpy.context.preferences.system.use_region_overlap:
        return 0
    return sum(r.width for r in area.regions if r.type == 'TOOLS')


def _draw():
    context = bpy.context
    scene = context.scene
    s = getattr(scene, "multicamproject_estimation", None)
    space = context.space_data
    if s is None or not s.show_overlay or not s.rows or space is None \
            or not space.overlay.show_overlays:
        return
    lines = core.table(context)
    if not lines:
        return
    ui = context.preferences.system.ui_scale
    blf.size(FONT, 11 * ui)
    pad, row_h = 6 * ui, 18 * ui
    text_dy = (row_h - blf.dimensions(FONT, "0K")[1]) / 2
    tris = sum(ln.now for ln in lines)
    budget = sum(ln.budget for ln in lines)
    cells = [(ln.label, f"{ln.area:.2f} m²", core.short(ln.budget), core.short(ln.now),
              core.delta_text(ln.delta)) for ln in lines]
    head = ("Object", "Area", "Budget", "Now", "To Go")
    total = (f"{len(lines)} objects", f"{sum(ln.area for ln in lines):.2f} m²",
             core.short(budget), core.short(tris), core.delta_text(budget - tris))
    widths = [max(blf.dimensions(FONT, r[i])[0] for r in cells + [head, total]) + 2 * pad
              for i in range(5)]
    width = sum(widths)
    n = len(lines) + 3                  # title, column names, rows, total
    x0 = _left_edge(context.area) + 14 * ui
    y = 14 * ui + (n - 1) * row_h       # the top row's bottom; rows go down from there

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    _rect(shader, x0, y, width, row_h, HEAD_BG)
    _text(x0 + pad, y + text_dy, f"Polycount Estimation · {core.short(s.calculated_budget)} "
                                 f"tris", TEXT)
    y -= row_h
    _rect(shader, x0, y, width, row_h, HEAD_BG)
    _draw_cells(shader, x0, y, widths, head, pad, text_dy, DIM, None)
    active = context.active_object
    for ln, row in zip(lines, cells):
        y -= row_h
        _rect(shader, x0, y, width, row_h, ROW_BG[ln.state])
        _draw_cells(shader, x0, y, widths, row, pad, text_dy, TEXT, ln.delta, row_h)
        if active is not None and active.name == ln.name:
            _frame(shader, x0, y, width, row_h, TEXT)
    y -= row_h
    _rect(shader, x0, y, width, row_h, HEAD_BG)
    _draw_cells(shader, x0, y, widths, total, pad, text_dy, TEXT, budget - tris, row_h)
    gpu.state.blend_set('NONE')


def _draw_cells(shader, x, y, widths, texts, pad, text_dy, color, delta, row_h=0):
    """One row's 5 cells: the name left-aligned, the numbers right-aligned; To Go green /
    red on a darker cell (delta None: the column names)."""
    _text(x + pad, y + text_dy, texts[0], color)
    x += widths[0]
    for i in range(1, 5):
        if i == 4 and delta is not None:
            _rect(shader, x, y, widths[i], row_h, DELTA_BG)
            color = PLUS if delta >= 0 else MINUS
        _text(x, y + text_dy, texts[i], color, right_edge=x + widths[i] - pad)
        x += widths[i]


def register():
    global _handle
    _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
