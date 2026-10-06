"""Camera view <-> photo: where a camera's background photo sits in the 3D Viewport.

The camera frame on screen comes from the camera's view_frame (follows zoom/pan in camera
view, perspective and ortho). Inside it Blender places the background by frame method
(Fit / Crop / Stretch), scale, rotation, flips and offset. Measured in 5.2 against the drawn
pixels (markers in a test photo, < 0.5 px):
- offset X is in frame widths, offset Y in frame width / photo aspect (= frame heights only
  when the photo has the frame's aspect, as in Camera Project setups); neither is scaled
  or rotated;
- rotation turns the photo clockwise on screen, around its centre, in screen pixels.
Placement.to_uv undoes that.
"""
import math

from bpy_extras.view3d_utils import location_3d_to_region_2d


class Placement:
    """The photo's rectangle on screen: centre, size (region px), rotation, flips."""

    def __init__(self, cx, cy, w, h, rot, flip_x, flip_y):
        self.cx, self.cy, self.w, self.h = cx, cy, w, h
        self.cos, self.sin = math.cos(rot), math.sin(rot)
        self.flip_x, self.flip_y = flip_x, flip_y

    def to_uv(self, x, y):
        dx, dy = x - self.cx, y - self.cy
        # undo the rotation around the photo centre (self.cos/sin: counter-clockwise angle)
        rx = dx * self.cos + dy * self.sin
        ry = -dx * self.sin + dy * self.cos
        u = rx / self.w + 0.5
        v = ry / self.h + 0.5
        return (1.0 - u if self.flip_x else u), (1.0 - v if self.flip_y else v)

    def from_uv(self, u, v):
        """Inverse of to_uv: region pixels of the photo point (u, v)."""
        u = 1.0 - u if self.flip_x else u
        v = 1.0 - v if self.flip_y else v
        rx = (u - 0.5) * self.w
        ry = (v - 0.5) * self.h
        return (self.cx + rx * self.cos - ry * self.sin,
                self.cy + rx * self.sin + ry * self.cos)

    def px_per_uv_x(self):
        return self.w


def frame_rect(region, rv3d, scene, cam):
    """(x0, y0, x1, y1) of the camera frame in region pixels, or None (not on screen)."""
    pts = [location_3d_to_region_2d(region, rv3d, cam.matrix_world @ v)
           for v in cam.data.view_frame(scene=scene)]
    if any(p is None for p in pts):
        return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def placement(region, rv3d, scene, cam, bg, image_aspect):
    """Placement of background `bg` (aspect w/h of its photo) in this view, or None."""
    rect = frame_rect(region, rv3d, scene, cam)
    if rect is None:
        return None
    x0, y0, x1, y1 = rect
    fw, fh = x1 - x0, y1 - y0
    if fw <= 0 or fh <= 0:
        return None
    frame_aspect = fw / fh
    if bg.frame_method == 'STRETCH':
        w, h = fw, fh
    elif (bg.frame_method == 'CROP') == (image_aspect > frame_aspect):
        w, h = fh * image_aspect, fh        # CROP of a wider photo / FIT of a taller one
    else:
        w, h = fw, fw / image_aspect
    w *= bg.scale
    h *= bg.scale
    cx = (x0 + x1) * 0.5 + bg.offset[0] * fw
    cy = (y0 + y1) * 0.5 + bg.offset[1] * fw / image_aspect
    return Placement(cx, cy, w, h, -bg.rotation, bg.use_flip_x, bg.use_flip_y)
