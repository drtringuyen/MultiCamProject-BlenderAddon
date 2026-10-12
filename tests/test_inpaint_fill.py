"""Tests for modules/image_editor/inpaint_fill.py - plain numpy, no Blender.

Run:  py tests/test_inpaint_fill.py      (or pytest, if installed)
"""
import importlib.util
import os

import numpy as np

_PATH = os.path.join(os.path.dirname(__file__), "..", "addons", "MultiCamProject",
                     "modules", "image_editor", "inpaint_fill.py")
_spec = importlib.util.spec_from_file_location("inpaint_fill", _PATH)
ip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ip)


def _gradient(w=96, h=80):
    """A lit wall: a smooth gradient, alpha 1."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    buf = np.ones((h, w, 4), np.float32)
    buf[..., 0] = 0.2 + 0.3 * xx / w
    buf[..., 1] = 0.3 + 0.2 * yy / h
    buf[..., 2] = 0.6
    return buf


def _marker(buf, x0, y0, s):
    out = buf.copy()
    out[y0:y0 + s, x0:x0 + s, :3] = 0.95          # white sticker
    out[y0 + s // 3:y0 + 2 * s // 3, x0 + s // 3:x0 + 2 * s // 3, :3] = 0.0   # black dot
    hole = np.zeros(buf.shape[:2], bool)
    hole[y0:y0 + s, x0:x0 + s] = True
    return out, hole


def test_fill_gradient_removes_marker():
    clean = _gradient()
    dirty, hole = _marker(clean, 40, 30, 14)
    out, used = ip.fill(dirty, hole, grain=0.0)
    err = np.abs(out[..., :3] - clean[..., :3])[hole].max()
    assert err < 0.03, err
    assert (out[~used] == dirty[~used]).all()       # outside the hole untouched


def test_fill_with_grain_stays_close():
    rng = np.random.default_rng(1)
    clean = _gradient()
    clean[..., :3] += rng.normal(0, 0.02, clean[..., :3].shape).astype(np.float32)
    dirty, hole = _marker(clean, 40, 30, 14)
    out, _used = ip.fill(dirty, hole, grain=1.0)
    diff = out[..., :3][hole] - clean[..., :3][hole]
    assert abs(diff.mean()) < 0.02
    # the grain is there: the fill is not flat
    assert out[..., 0][hole].std() > 0.005


def test_valid_mask_blocks_black_outside_island():
    clean = _gradient()
    valid = np.ones(clean.shape[:2], bool)
    valid[:, :20] = False                           # outside the UV island
    clean[:, :20, :3] = 0.0                         # black margin
    dirty, hole = _marker(clean, 22, 30, 10)
    out, used = ip.fill(dirty, hole, valid=valid, grain=0.0)
    assert out[..., :3][used].min() > 0.15          # nothing black pulled in
    assert not used[:, :20].any()                   # never paints outside the island


def test_grow_and_stamp():
    m = np.zeros((20, 20), bool)
    ip.stamp_disc(m, 10, 10, 3)
    n = m.sum()
    assert 20 <= n <= 36
    assert ip.dilate(m, 2).sum() > n
    ip.stamp_disc(m, 10, 10, 5, value=False)
    assert not m.any()


def test_rasterize_triangles():
    tris = [[(0, 0), (10, 0), (0, 10)]]
    m = ip.rasterize_triangles(tris, 0, 0, 12, 12)
    assert m[1, 1] and not m[9, 9]
    assert 40 <= m.sum() <= 60


def test_bbox_and_crop():
    m = np.zeros((10, 12), bool)
    m[2:5, 3:7] = True
    assert ip.bbox(m) == (3, 2, 7, 5)
    assert ip.crop_rect((3, 2, 7, 5), 12, 10, 4) == (0, 0, 11, 9)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
