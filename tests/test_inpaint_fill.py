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


def test_seam_islands_fill_separately():
    """A hole across a seam: each side keeps its own colour, no dark smear across."""
    clean = _gradient()
    clean[:, :48, :3] = (0.05, 0.05, 0.08)          # the dark island left of the seam
    labels = np.ones(clean.shape[:2], np.int32)
    labels[:, 48:] = 2
    dirty, hole = _marker(clean, 38, 30, 20)        # crosses x = 48
    out, used = ip.fill(dirty, hole, grain=0.0, labels=labels)
    left = out[30:50, 38:48, :3]
    right = out[30:50, 48:58, :3]
    assert np.abs(left - 0.05).max() < 0.04, left.max()
    assert right.min() > 0.25, right.min()          # no dark pulled into the bright side
    assert used[30:50, 38:58].all()


def test_big_hole_gets_grain():
    rng = np.random.default_rng(3)
    clean = _gradient(400, 400)
    clean[..., :3] += rng.normal(0, 0.03, clean[..., :3].shape).astype(np.float32)
    hole = np.zeros((400, 400), bool)
    hole[100:300, 120:280] = True                   # no hole-sized known area fits nearby
    out, _ = ip.fill(clean, hole, grain=1.0)
    hp = out[..., 0] - ip.box_blur(out[..., :3], np.ones((400, 400), np.float32), 3)[..., 0]
    assert hp[hole].std() > 0.01, hp[hole].std()   # grain inside the hole, not flat


def test_triangle_islands_split_at_seam():
    # a quad = 2 triangles sharing verts 0-2 with the same UVs; a third triangle shares
    # verts 1-2 with tri 0 but different UVs there (a seam)
    verts = [(0, 1, 2), (0, 2, 3), (1, 4, 2)]
    uvs = [[(0, 0), (1, 0), (1, 1)], [(0, 0), (1, 1), (0, 1)],
           [(5, 0), (6, 0), (5, 1)]]
    lab = ip.triangle_islands(verts, uvs)
    assert lab[0] == lab[1] and lab[2] != lab[0] and lab.min() == 1


def test_dilate_labels():
    lab = np.zeros((5, 9), np.int32)
    lab[:, 2] = 1
    lab[:, 6] = 2
    g = ip.dilate_labels(lab, 1)
    assert (g[:, 1] == 1).all() and (g[:, 3] == 1).all() and (g[:, 7] == 2).all()
    assert (g[:, 0] == 0).all()


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
