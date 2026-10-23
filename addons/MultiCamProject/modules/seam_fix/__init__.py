"""Seam Fix: hides the UV seams of a baked albedo, as a last step on a finished object.

    Fix Seam       uv_new (islands shrunk for room), uv_fixseam (each seam's target side
                   unfolded into the source's margin) and the blend mask
    Seam-leveling  a colour offset so both sides of every seam meet at their average
    Bake           every image on uv_normal, as Preview shows it, onto uv_new (_seamfix)
    View           Original / Mask / Preview / Baked, through one node group per image
    Reset          everything back - uv_normal and the originals were never touched
    Apply          destructive: bakes over the files (backed up), uv_new becomes uv_normal

Panels: the MultiCamProject tab of the 3D view and of the UV editor."""
from . import operators, props, ui


def register():
    props.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    props.unregister()
