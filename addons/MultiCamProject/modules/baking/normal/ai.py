"""AI normal map from the albedo (Full build only): a DeepBump-style color -> normal ONNX
model run with onnxruntime on overlapping tiles, blended with a smooth window.

onnxruntime is imported lazily; without it (Lite build) or without the model file this
source is not offered.
"""
import importlib.util
import os

import numpy as np

from . import highpass

MODELS = os.path.join(os.path.dirname(__file__), "models")     # Full build
MODEL_FILE = "color_to_normals.onnx"
# models/ of the Full build, or the model Set up AI copied into Blender's user data
# folder (survives add-on reinstalls). onnxruntime then comes from the user modules folder.
PACKAGES = ("onnxruntime", "flatbuffers", "protobuf", "packaging", "coloredlogs",
            "humanfriendly", "sympy", "mpmath")     # not numpy: Blender's own is used
TILE = 256
OVERLAP = 64
BATCH = 16


def user_models():
    import bpy
    return os.path.join(bpy.utils.user_resource('DATAFILES', path="multicamproject"), "models")


def model_path():
    for folder in (MODELS, user_models()):
        path = os.path.join(folder, MODEL_FILE)
        if os.path.isfile(path):
            return path
    return os.path.join(MODELS, MODEL_FILE)


def has_runtime():
    return importlib.util.find_spec("onnxruntime") is not None


def missing():
    """What the AI source still needs ('' = ready)."""
    parts = []
    if not has_runtime():
        parts.append("onnxruntime")
    if not os.path.isfile(model_path()):
        parts.append("a model (.onnx)")
    return " and ".join(parts)


def install_runtime():
    """pip install onnxruntime into Blender's user modules folder (on sys.path). Blocks.
    Returns (ok, message)."""
    import subprocess
    import sys
    import bpy
    target = bpy.utils.user_resource('SCRIPTS', path="modules", create=True)
    py = sys.executable
    run = lambda *a: subprocess.run([py, *a], capture_output=True, text=True)
    if run("-m", "pip", "--version").returncode != 0:
        r = run("-m", "ensurepip", "--user")
        if r.returncode != 0:
            return False, "pip is not available: " + r.stderr.strip()[-300:]
    r = run("-m", "pip", "install", "--no-deps", "--upgrade", "--target", target, *PACKAGES)
    if r.returncode != 0:
        return False, "pip install failed: " + (r.stderr or r.stdout).strip()[-400:]
    if target not in sys.path:
        sys.path.append(target)
    importlib.invalidate_caches()
    return has_runtime(), f"onnxruntime installed into {target}"


def install_model(src):
    import shutil
    folder = user_models()
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, MODEL_FILE)
    shutil.copyfile(src, dst)
    return dst


def available():
    return has_runtime() and os.path.isfile(model_path())


def _session():
    import onnxruntime as ort
    providers = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider")
                 if p in ort.get_available_providers()]
    return ort.InferenceSession(model_path(), providers=providers)


def _window():
    ramp = np.minimum(np.arange(TILE) + 0.5, TILE - np.arange(TILE) - 0.5) / OVERLAP
    w = np.clip(ramp, 1e-3, 1.0).astype(np.float32)
    return np.outer(w, w)


def _starts(n):
    step = TILE - OVERLAP
    s = list(range(0, max(n - TILE, 0) + 1, step))
    if s[-1] + TILE < n:
        s.append(n - TILE)
    return s


def generate(alb_img, size):
    """Normal map RGB (size, size, 3), rows bottom-up."""
    rgb = highpass.read_pixels(alb_img)
    f = rgb.shape[0] // size
    if f > 1:
        rgb = rgb[:size * f, :size * f].reshape(size, f, size, f, 3).mean(axis=(1, 3))
    mask = rgb.sum(axis=2) > 0.0
    img = rgb[::-1].astype(np.float32)          # the model expects rows top-down
    del rgb
    sess = _session()
    inp = sess.get_inputs()[0]
    channels = inp.shape[1] if isinstance(inp.shape[1], int) else 3
    src = img.mean(axis=2, keepdims=True) if channels == 1 else img
    h, w = src.shape[:2]
    acc = np.zeros((h, w, 3), dtype=np.float32)
    wsum = np.zeros((h, w, 1), dtype=np.float32)
    win = _window()[..., None]
    tiles = [(y, x) for y in _starts(h) for x in _starts(w)]
    for i in range(0, len(tiles), BATCH):
        chunk = tiles[i:i + BATCH]
        batch = np.stack([src[y:y + TILE, x:x + TILE].transpose(2, 0, 1) for y, x in chunk])
        out = sess.run(None, {inp.name: batch.astype(np.float32)})[0]
        if out.min() < 0.0:         # -1..1 output
            out = out * 0.5 + 0.5
        for (y, x), t in zip(chunk, out):
            acc[y:y + TILE, x:x + TILE] += t.transpose(1, 2, 0)[:, :, :3] * win
            wsum[y:y + TILE, x:x + TILE] += win
    n = acc / np.maximum(wsum, 1e-6) * 2.0 - 1.0
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
    n = (n * 0.5 + 0.5)[::-1]
    n[~mask] = (0.5, 0.5, 1.0)
    return n.astype(np.float32)
