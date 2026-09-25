"""Mesh + Albedo (Full build): the mesh bake's large forms with the albedo high-pass's
fine detail on top, combined with Reoriented Normal Mapping."""
import numpy as np

from . import highpass, mesh_bake


def rnm(base, detail):
    """Reoriented Normal Mapping of two RGB 0..1 tangent-space maps."""
    t = base * np.float32(2.0) - np.float32(1.0)
    t[..., 2] += 1.0
    u = detail * np.float32(2.0) - np.float32(1.0)
    u[..., 0] *= -1.0
    u[..., 1] *= -1.0
    dot = (t * u).sum(axis=-1, keepdims=True)
    r = t * dot / np.maximum(t[..., 2:3], 1e-6) - u
    r /= np.maximum(np.linalg.norm(r, axis=-1, keepdims=True), 1e-6)
    return r * 0.5 + 0.5


def generate(context, obj, alb_img, size, settings):
    base = mesh_bake.generate(context, obj, size)
    detail = highpass.generate(alb_img, size, settings)
    return rnm(base, detail).astype(np.float32)
