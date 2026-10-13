"""preview_copies' worker, run as its own process (OpenImageIO holds Python's GIL: in a
thread it would stall Blender): python preview_copies_worker.py <size> <src> <dst>
Exit 0: the copy is written, 3: src is no larger than <size> (no copy), 1: error (stderr)."""
import os
import sys


def main(size, src, dst):
    import OpenImageIO as oiio
    buf = oiio.ImageBuf(src)
    spec = buf.spec()
    if buf.has_error:
        raise RuntimeError(buf.geterror())
    w, h = spec.width, spec.height
    if max(w, h) <= size:
        return 3
    k = size / max(w, h)
    roi = oiio.ROI(0, max(1, round(w * k)), 0, max(1, round(h * k)), 0, 1, 0, spec.nchannels)
    small = oiio.ImageBufAlgo.resize(buf, roi=roi)
    if small.has_error:
        raise RuntimeError(small.geterror())
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    root, ext = os.path.splitext(dst)
    tmp = root + ".part" + ext
    if not small.write(tmp):
        raise RuntimeError(small.geterror())
    os.replace(tmp, dst)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(int(sys.argv[1]), sys.argv[2], sys.argv[3]))
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
