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


def _edge_wall(w=120, h=120):
    """A wall with a vertical content edge (dark | light) and a fine stripe texture."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    buf = np.ones((h, w, 4), np.float32)
    base = np.where(xx < 60, 0.2, 0.7).astype(np.float32)
    tex = 0.05 * np.sin(yy * 1.3) * np.cos(xx * 0.9)
    for c in range(3):
        buf[..., c] = base + tex
    return buf


def test_patch_continues_a_content_edge():
    clean = _edge_wall()
    dirty = clean.copy()
    dirty[20:44, 48:72, :3] = 0.95                     # a sticker across the edge
    hole = np.zeros(clean.shape[:2], bool)
    hole[18:46, 46:74] = True
    # the patch dragged straight down the edge: the same edge, other rows
    dy = 50
    src = np.roll(dirty, -dy, axis=0)                  # src[y] = dirty[y + dy]
    out, used = ip.patch(dirty, src, hole, match=True, feather=2)
    assert used[hole].all()
    # the edge goes on through the hole: dark left, light right
    assert abs(out[30, 52, 0] - 0.2) < 0.08 and abs(out[30, 68, 0] - 0.7) < 0.08
    assert np.abs(out[20:44, 48:72, :3] - clean[20:44, 48:72, :3]).max() < 0.12
    # a smooth fill smears the edge (the reason for the Patch tool)
    filled, _ = ip.fill(dirty, hole, grain=0.0)
    assert abs(filled[30, 58, 0] - 0.45) < 0.15


def test_patch_matches_the_light():
    dst = np.full((60, 60, 4), 0.6, np.float32)
    src = np.full((60, 60, 4), 0.3, np.float32)        # the same texture in the shade
    yy, xx = np.mgrid[0:60, 0:60]
    src[..., 0] += 0.05 * np.sin(xx * 1.7)
    hole = np.zeros((60, 60), bool)
    hole[20:40, 20:40] = True
    out, _ = ip.patch(dst, src, hole, match=True)
    assert abs(out[30, 30, 1] - 0.6) < 0.02            # the light of the hole's edge
    assert np.std(out[25:35, 25:35, 0]) > 0.02         # the texture of the source
    raw, _ = ip.patch(dst, src, hole, match=False, feather=0)
    assert abs(raw[30, 30, 1] - 0.3) < 1e-6            # Match off: a plain copy
    assert (raw[..., 3] == dst[..., 3]).all() and (out[~ip.dilate(hole, 2)] == dst[~ip.dilate(hole, 2)]).all()


def test_patch_stays_on_its_island():
    dst = np.full((40, 40, 4), 0.5, np.float32)
    dst[:, 20:, :3] = 0.0                              # black outside the island
    src = np.full((40, 40, 4), 0.4, np.float32)
    labels = np.zeros((40, 40), np.int32)
    labels[:, :20] = 1
    hole = np.zeros((40, 40), bool)
    hole[10:30, 10:30] = True                          # across the island's edge
    out, used = ip.patch(dst, src, hole, labels=labels)
    assert not used[:, 20:].any() and (out[:, 20:, :3] == 0.0).all()
    assert abs(out[20, 15, 0] - 0.5) < 0.03            # matched to its island, not the black


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
