"""Tests for modules/baking/pngcheck.py - temporary files only, no Blender.

Run:  py tests/test_pngcheck.py      (or pytest, if installed)
"""
import importlib.util
import os
import struct
import tempfile
import zlib

_PATH = os.path.join(os.path.dirname(__file__), "..", "addons", "MultiCamProject",
                     "modules", "baking", "pngcheck.py")
_spec = importlib.util.spec_from_file_location("pngcheck", _PATH)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(path, w, h, raw, ctype=2, depth=8, interlace=0, parts=1):
    """A PNG of the already filtered `raw` bytes, its data split over `parts` IDAT chunks."""
    z = zlib.compress(raw, 9)
    step = max(1, len(z) // parts)
    idat = b"".join(_chunk(b"IDAT", z[i:i + step]) for i in range(0, len(z), step))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace))
                + _chunk(b"tEXt", b"Comment\0a chunk to skip") + idat + _chunk(b"IEND", b""))


def _rows(w, h, pixel, filt=0, last=b""):
    """h rows of black `pixel`-byte pixels with filter byte `filt`; `last` ends the last row."""
    row = bytes([filt]) + bytes(w * pixel)
    end = row[:len(row) - len(last)] + last
    return row * (h - 1) + end


def _check(name, expected, *args, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.png")
        _png(path, *args, **kw)
        assert pc.is_black(path) is expected, name


def test_black_rgb():
    _check("8-bit RGB", True, 512, 512, _rows(512, 512, 3))


def test_black_with_row_filters():
    _check("filter bytes are not pixels", True, 512, 512, _rows(512, 512, 3, filt=2))


def test_black_split_over_chunks():
    _check("several IDAT", True, 512, 512, _rows(512, 512, 3), parts=7)


def test_black_16_bit():
    _check("16-bit RGB", True, 512, 512, _rows(512, 512, 6), depth=16)


def test_one_pixel_in_the_last_row():
    _check("a single colored byte", False, 512, 512, _rows(512, 512, 3, last=b"\x01"))


def test_black_rgba_with_opaque_alpha():
    # Up filter: the first row holds alpha 255, the rest filter down to zero
    w, h = 512, 512
    first = b"\x00" + b"\x00\x00\x00\xff" * w
    _check("RGBA", True, w, h, first + _rows(w, h - 1, 4, filt=2), ctype=6)


def test_colored_rgba():
    w, h = 512, 512
    first = b"\x00" + b"\x00\x00\x07\xff" * w
    _check("RGBA blue", False, w, h, first + _rows(w, h - 1, 4, filt=2), ctype=6)


def test_white_alpha_only_gray():
    w, h = 512, 512
    _check("gray + alpha", True, w, h, _rows(w, h, 2), ctype=4)


def test_not_checked_formats():
    _check("palette", False, 512, 512, _rows(512, 512, 1), ctype=3)
    _check("interlaced", False, 512, 512, _rows(512, 512, 3), interlace=1)


def test_truncated_data_is_not_black():
    w, h = 512, 512
    _check("rows missing", False, w, h, _rows(w, h // 2, 3))


def test_large_file_is_not_opened():
    # poorly compressible: over the size limit, so its pixels are never read
    w, h = 64, 64
    raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))
    _check("noise", False, w, h, raw)


def test_unreadable():
    assert pc.is_black(os.path.join(tempfile.gettempdir(), "no_such_file_mcp.png")) is False
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.png")
        with open(path, "wb") as f:
            f.write(b"not a png" * 10)
        assert pc.is_black(path) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
