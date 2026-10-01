"""Tests for modules/image_editor/field.py - plain numpy, no Blender.

Run:  py tests/test_liquify_field.py      (or pytest, if installed)
"""
import importlib.util
import os
import sys
import time

import numpy as np

_PATH = os.path.join(os.path.dirname(__file__), "..", "addons", "MultiCamProject",
                     "modules", "image_editor", "field.py")
_spec = importlib.util.spec_from_file_location("lq_field", _PATH)
field = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(field)

W, H = 320, 240


def _photo(w=W, h=H, seed=0):
    return np.random.default_rng(seed).random((h, w, 4), dtype=np.float32)


def _gradient(w, h):
    """Pixels linear in x and y - bilinear sampling reproduces it exactly."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.stack([xs, ys, xs + ys, np.ones_like(xs)], axis=-1)


def _scribble(lq):
    lq.stroke_begin()
    for i in range(12):
        lq.warp(80 + i * 5, 100 + i * 2, 30, (5, 2), 0.8)
    lq.pucker(200, 120, 40, 0.7)
    lq.pucker(150, 60, 25, -1.0)
    lq.smooth(120, 110, 35, 0.6)
    lq.reconstruct(90, 105, 20, 0.5)
    return lq.stroke_end()


def _full_remap(lq):
    return field.remap(lq.src, lq.D, 0, lq.h, 0, lq.w)


# ---------------------------------------------------------------- tests

def test_preview_size():
    assert field.preview_size(4000, 3000) == (1024, 768)
    assert field.preview_size(2160, 3840) == (576, 1024)
    assert field.preview_size(800, 600) == (800, 600)        # never upscaled
    w, h = field.preview_size(3840, 2161)
    assert max(w, h) == 1024 and abs(w / h - 3840 / 2161) < 2e-3


def test_identity():
    src = _photo()
    lq = field.Liquify(src)
    assert np.array_equal(lq.out, src)
    assert np.allclose(_full_remap(lq), src)


def test_float32_everywhere():
    lq = field.Liquify(_photo())
    _scribble(lq)
    assert lq.D.dtype == np.float32 and lq.out.dtype == np.float32
    assert field.remap(lq.src, lq.D, 0, 10, 0, 10).dtype == np.float32
    assert field.warp_image(_photo(64, 48), lq.field_uv()).dtype == np.float32
    assert field.downscale(_photo(640, 480).astype(np.float64), 320, 240).dtype == np.float32


def test_box_updates_match_full_remap():
    lq = field.Liquify(_photo())
    _scribble(lq)
    assert np.allclose(lq.out, _full_remap(lq), atol=1e-6)


def test_brushes_stay_in_their_box():
    lq = field.Liquify(_photo())
    box = lq.warp(100, 100, 20, (6, 0))
    y0, y1, x0, x1 = box
    mask = np.zeros((H, W), bool)
    mask[y0:y1, x0:x1] = True
    assert not lq.D[~mask].any()
    assert np.array_equal(lq.out[~mask], lq.src[~mask])


def test_brush_off_image():
    lq = field.Liquify(_photo())
    assert lq.warp(-500, -500, 20, (5, 5)) is None
    assert lq.warp(-10, 100, 20, (5, 0)) is not None       # partly inside
    assert np.allclose(lq.out, _full_remap(lq), atol=1e-6)


def test_warp_moves_content_along_delta():
    src = np.zeros((H, W, 4), np.float32)
    src[118:123, 98:103] = 1.0                              # a dot at (100, 120)
    lq = field.Liquify(src)
    for _ in range(3):                                      # 3 steps of +4 px in x
        lq.warp(100 + _ * 4, 120, 40, (4, 0), 1.0)
    row = lq.out[120, :, 0]
    peak = np.average(np.arange(W), weights=row)
    assert 108 <= peak <= 113, peak
    assert abs(np.average(np.arange(H), weights=lq.out[:, 100:120, 0].sum(1)) - 120) < 0.5


def test_pucker_and_bloat_directions():
    lq = field.Liquify(_photo())
    lq.pucker(160, 120, 40, 1.0)
    # pucker shows content from farther out: right of the centre the offset points right
    assert lq.D[120, 175, 0] > 0 and lq.D[120, 145, 0] < 0 and lq.D[135, 160, 1] > 0
    lq2 = field.Liquify(_photo())
    lq2.pucker(160, 120, 40, -1.0)
    assert lq2.D[120, 175, 0] < 0 and lq2.D[120, 145, 0] > 0
    assert abs(lq.D[120, 160]).max() < 1e-6                 # the centre itself stays


def test_reconstruct():
    lq = field.Liquify(_photo())
    lq.D[:] = 3.0
    lq.reconstruct(160, 120, 30, 1.0)
    assert abs(lq.D[120, 160]).max() < 1e-6                 # gone at the centre
    assert 0 < lq.D[120, 180, 0] < 3.0                      # partly restored inside
    assert lq.D[120, 200, 0] == 3.0                         # untouched outside
    lq.reconstruct(160, 120, 30, 0.0)                       # no-op


def test_smooth_evens_out_the_field():
    lq = field.Liquify(_photo())
    lq.D[:] = np.random.default_rng(1).normal(0, 3, lq.D.shape).astype(np.float32)
    region = (slice(110, 131), slice(150, 171))
    before = lq.D[region].std()
    lq.smooth(160, 120, 30, 1.0)
    assert lq.D[region].std() < before * 0.6
    c = np.full((H, W, 2), 2.5, np.float32)                 # a constant field stays constant
    lq.D[:] = c
    lq.smooth(160, 120, 30, 1.0)
    assert np.allclose(lq.D, c, atol=1e-5)


def test_undo_redo():
    lq = field.Liquify(_photo())
    assert not lq.can_undo
    _scribble(lq)
    d1, o1 = lq.D.copy(), lq.out.copy()
    lq.stroke_begin()
    lq.warp(250, 50, 30, (-8, 4))
    lq.stroke_end()
    d2, o2 = lq.D.copy(), lq.out.copy()
    assert lq.undo() is not None
    assert np.array_equal(lq.D, d1) and np.allclose(lq.out, o1, atol=1e-6)
    lq.undo()
    assert not lq.D.any() and np.allclose(lq.out, lq.src)
    assert lq.undo() is None
    lq.redo()
    lq.redo()
    assert np.array_equal(lq.D, d2) and np.allclose(lq.out, o2, atol=1e-6)
    assert lq.redo() is None
    lq.undo()
    lq.stroke_begin()                                       # a new stroke drops redo
    lq.warp(50, 50, 10, (2, 2))
    lq.stroke_end()
    assert not lq.can_redo


def test_stroke_cancel():
    lq = field.Liquify(_photo())
    _scribble(lq)
    d, o = lq.D.copy(), lq.out.copy()
    lq.stroke_begin()
    assert lq.in_stroke
    lq.warp(160, 120, 40, (10, 3))
    lq.pucker(60, 60, 30, 1.0)
    assert lq.stroke_cancel() is not None and not lq.in_stroke
    assert np.array_equal(lq.D, d) and np.allclose(lq.out, o, atol=1e-6)
    assert len(lq._undo) == 1


def test_empty_stroke_is_not_an_undo_step():
    lq = field.Liquify(_photo())
    lq.stroke_begin()
    lq.warp(-999, -999, 10, (1, 1))
    assert lq.stroke_end() is None and not lq.can_undo


def test_undo_limit():
    lq = field.Liquify(_photo(64, 48))
    for i in range(field.UNDO_STEPS + 10):
        lq.stroke_begin()
        lq.warp(32, 24, 10, (1, 0))
        lq.stroke_end()
    assert len(lq._undo) == field.UNDO_STEPS


def test_reset():
    lq = field.Liquify(_photo())
    _scribble(lq)
    lq.reset()
    assert not lq.D.any() and np.allclose(lq.out, lq.src)
    lq.undo()
    assert lq.D.any()


def test_field_uv_roundtrip_and_resize():
    lq = field.Liquify(_photo())
    _scribble(lq)
    f = lq.field_uv()
    lq2 = field.Liquify(lq.src)
    lq2.load_field_uv(f)
    assert np.allclose(lq2.D, lq.D, atol=1e-5) and np.allclose(lq2.out, lq.out, atol=1e-5)
    # a constant field survives loading at another size
    lq3 = field.Liquify(_photo(160, 120))
    lq3.load_field_uv(np.full((240, 320, 2), 0.01, np.float32))
    assert np.allclose(lq3.D[..., 0], 1.6) and np.allclose(lq3.D[..., 1], 1.2)


def test_downscale():
    big = np.full((960, 1280, 4), 0.25, np.float32)
    small = field.downscale(big, 320, 240)
    assert small.shape == (240, 320, 4) and np.allclose(small, 0.25)
    odd = field.downscale(_photo(1001, 777), 300, 233)
    assert odd.shape == (233, 300, 4)


def test_bake_shift_scales_to_full_size():
    big = _gradient(400, 300)
    f = np.zeros((30, 40, 2), np.float32)
    f[..., 0] = 0.01                                        # 1 % of the width = 4 px
    out = field.warp_image(big, f)
    assert np.allclose(out[:, :-8, 0], big[:, :-8, 0] + 4, atol=1e-3)
    assert np.allclose(out[..., 1], big[..., 1], atol=1e-3)


def test_bake_zero_field_and_strips():
    big = _photo(203, 157)
    assert np.allclose(field.warp_image(big, np.zeros((20, 26, 2), np.float32)), big)
    lq = field.Liquify(field.downscale(big, 101, 78))
    _scribble(lq)
    f = lq.field_uv()
    assert np.allclose(field.warp_image(big, f, strip=7), field.warp_image(big, f, strip=1000))


def test_bake_matches_preview():
    """Baking the preview-size photo reproduces the live preview."""
    lq = field.Liquify(_photo())
    _scribble(lq)
    assert np.allclose(field.warp_image(lq.src, lq.field_uv()), lq.out, atol=1e-4)


def test_speed():
    """1K preview: one dab must stay well inside a 30 Hz tick (budget ~5 ms measured in
    Blender for remap + upload). Loose bound so slow machines don't fail."""
    w, h = field.preview_size(3840, 2160)
    lq = field.Liquify(_photo(w, h))
    lq.stroke_begin()
    t = time.perf_counter()
    n = 60
    for i in range(n):
        lq.warp(200 + i * 4, 300, 100, (4, 1), 0.7)
    dab = (time.perf_counter() - t) / n * 1000
    lq.stroke_end()
    t = time.perf_counter()
    field.warp_image(_photo(3840, 2160), lq.field_uv())
    bake = time.perf_counter() - t
    print(f"    1K warp dab r=100: {dab:.1f} ms   4K bake: {bake:.2f} s")
    assert dab < 40


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
