"""Tests for modules/image_editor/lasso_raster.py - plain numpy, no Blender.

Run:  py tests/test_lasso_raster.py      (or pytest, if installed)
"""
import importlib.util
import math
import os

import numpy as np

_PATH = os.path.join(os.path.dirname(__file__), "..", "addons", "MultiCamProject",
                     "modules", "image_editor", "lasso_raster.py")
_spec = importlib.util.spec_from_file_location("lasso_raster", _PATH)
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)


def _photo(w=64, h=48, seed=0):
    buf = np.random.default_rng(seed).random((h, w, 4), dtype=np.float32)
    buf[..., 3] = 1.0
    return buf


def _corners(bx0, by0, pw, ph, tx=0.0, ty=0.0, angle=0.0, scale=1.0):
    ca, sa = math.cos(angle) * scale, math.sin(angle) * scale
    pts = [(bx0, by0), (bx0 + pw, by0), (bx0 + pw, by0 + ph), (bx0, by0 + ph)]
    return [(ca * x - sa * y + tx, sa * x + ca * y + ty) for x, y in pts]


def test_rasterize_square():
    mask = lr.rasterize_polygon([(2, 2), (6, 2), (6, 6), (2, 6)], 0, 0, 8, 8)
    assert mask.sum() == 16
    assert mask[2:6, 2:6].all()


def test_rasterize_offset_bbox():
    pts = [(12, 10), (16, 10), (16, 14), (12, 14)]
    mask = lr.rasterize_polygon(pts, 12, 10, 4, 4)
    assert mask.all()


def test_identity_composite_is_lossless():
    src = _photo()
    bx0, by0, pw, ph = 10, 8, 20, 16
    patch = src[by0:by0 + ph, bx0:bx0 + pw].copy()
    dest = src.copy()
    dest[by0:by0 + ph, bx0:bx0 + pw, 3] = 0.0          # the CUT hole
    lr.composite_float(dest, patch, _corners(bx0, by0, pw, ph), 0, 0, 0, 1, bx0, by0)
    assert np.allclose(dest, src, atol=1e-5)


def test_translate_moves_pixels():
    src = _photo()
    bx0, by0, pw, ph = 4, 4, 10, 10
    patch = src[by0:by0 + ph, bx0:bx0 + pw].copy()
    dest = np.zeros_like(src)
    lr.composite_float(dest, patch, _corners(bx0, by0, pw, ph, 20, 15), 20, 15, 0, 1, bx0, by0)
    assert np.allclose(dest[by0 + 15:by0 + 15 + ph, bx0 + 20:bx0 + 20 + pw], patch, atol=1e-5)
    assert dest[:by0 + 15, :, 3].max() == 0.0


def test_transparent_patch_keeps_dest():
    dest = _photo(seed=1)
    before = dest.copy()
    patch = _photo(10, 10, seed=2)
    patch[..., 3] = 0.0
    lr.composite_float(dest, patch, _corners(5, 5, 10, 10, 3, 2, 0.4, 1.3), 3, 2, 0.4, 1.3, 5, 5)
    assert np.allclose(dest, before, atol=1e-5)


def test_composite_outside_image_is_noop():
    dest = _photo()
    before = dest.copy()
    patch = _photo(8, 8)
    lr.composite_float(dest, patch, _corners(0, 0, 8, 8, 500, 500), 500, 500, 0, 1, 0, 0)
    assert np.array_equal(dest, before)


def test_affine_bounds_covers_written_pixels():
    dest = np.zeros((48, 64, 4), np.float32)
    patch = _photo(12, 9)
    args = (7.5, -3.0, 0.7, 1.6, 20, 18)
    corners = _corners(20, 18, 12, 9, *args[:4])
    x0, y0, x1, y1 = lr.affine_bounds(corners, 64, 48)
    lr.composite_float(dest, patch, corners, *args)
    outside = dest.copy()
    outside[y0:y1, x0:x1] = 0
    assert outside.max() == 0.0 and dest.max() > 0.0
    assert lr.affine_bounds(_corners(0, 0, 4, 4, 900, 900), 64, 48) is None


def test_to_display_srgb():
    buf = np.array([[[0.0, 0.18, 1.0, 0.5]]], np.float32)
    out = lr.to_display(buf, True)
    assert np.allclose(out[0, 0, :3], [0.0, 0.4613, 1.0], atol=1e-3)
    assert out[0, 0, 3] == 0.5
    assert np.array_equal(lr.to_display(buf, False), buf)


def test_hole_overlay_alpha_is_mask():
    mask = lr.rasterize_polygon([(1, 1), (9, 2), (5, 9)], 0, 0, 10, 10)
    ov = lr.hole_overlay(mask)
    assert ov.shape == (10, 10, 4)
    assert np.array_equal(ov[..., 3] > 0, mask)


def test_hole_overlay_fill_color():
    mask = np.zeros((4, 5), bool)
    mask[1:3, 1:4] = True
    ov = lr.hole_overlay(mask, (0.2, 0.4, 0.6))
    assert np.allclose(ov[2, 2], [0.2, 0.4, 0.6, 1.0])
    assert ov[0, 0, 3] == 0.0


def test_apply_hole_transparent_and_fill():
    buf = _photo(10, 8)
    mask = np.zeros((3, 4), bool)
    mask[1, 1:3] = True
    hole = buf.copy()
    lr.apply_hole(hole, mask, 2, 3)
    assert hole[4, 3, 3] == 0.0 and hole[4, 3, 0] == buf[4, 3, 0]
    assert np.array_equal(np.delete(hole.reshape(-1, 4), [4 * 10 + 3, 4 * 10 + 4], 0),
                          np.delete(buf.reshape(-1, 4), [4 * 10 + 3, 4 * 10 + 4], 0))
    filled = buf.copy()
    lr.apply_hole(filled, mask, 2, 3, (0.1, 0.2, 0.3))
    assert np.allclose(filled[4, 3], [0.1, 0.2, 0.3, 1.0])
    assert np.allclose(filled[4, 4], [0.1, 0.2, 0.3, 1.0])
    assert np.array_equal(filled[4, 5], buf[4, 5])


def test_srgb_roundtrip():
    c = np.array([0.0, 0.02, 0.5, 1.0], np.float32)
    lin = lr.srgb_to_linear(c)
    back = lr.to_display(np.stack([lin, lin, lin, np.ones(4)], -1)[None], True)[0, :, 0]
    assert np.allclose(back, c, atol=1e-4)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} passed")
