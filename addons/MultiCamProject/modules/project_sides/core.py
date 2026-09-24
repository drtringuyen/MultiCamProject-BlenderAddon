"""Project from Sides: global cameras around a center that all show one reference sheet
(views side by side). They are ordinary cameras for Camera Project - scored, slotted,
shifted and painted like any other - but marked global (camera_project `is_global`):
one UV shift for every object, and tagged with their side so Project resets them.

Each camera shows the WHOLE sheet; the scene resolution gets the sheet's aspect so the
camera frame is the sheet. The object covers about one panel in the middle of the frame;
the camera's shift moves the right panel onto it.
"""
import math

import bpy
from mathutils import Euler, Vector

from ..camera_project import core as cp

FILL = 0.85             # defaults: ground..top of the object fills this share of the sheet height
SENSOR = 36.0
COLLECTION = "Sides.Cameras"
PREFIX = {'ORTHO': "Ortho", 'PERSP': "Persp"}

# side, direction from the center to the camera, Z rotation (camera looks at the center),
# panel in the sheet (0 = leftmost). Names follow Blender's views: Right = numpad 3 (+X).
SIDES = (
    ("Front", Vector((0, -1, 0)), 0.0, 2),
    ("Left", Vector((-1, 0, 0)), -math.pi / 2, 0),
    ("Right", Vector((1, 0, 0)), math.pi / 2, 1),
    ("Back", Vector((0, 1, 0)), math.pi, 3),
)
PANELS = 4              # sheet order: Left, Right, Front, Back


def settings(scene):
    return scene.multicamproject_sides


def camera_project_ready():
    """The camera data this module writes is registered by the camera_project module."""
    return hasattr(bpy.types.Object, "multicamproject_camera")


def panel_shift(panel):
    """UV shift X that puts the center of `panel` (equal-width panels) on the frame center."""
    return 0.5 - (panel + 0.5) / PANELS


def side_cameras(detached=False):
    """{side: camera} for the cameras tagged with a side. Only the global ones unless
    `detached`: a camera switched to object-attached keeps its tag, so Project does not
    make a new one for its side, but it no longer resets it."""
    names = {s[0] for s in SIDES}
    out = {}
    for o in bpy.data.objects:
        if (o.type == 'CAMERA' and cp.cam_data(o).side in names
                and (detached or cp.is_global(o))):
            out.setdefault(cp.cam_data(o).side, o)
    return out


def set_render_aspect(scene, aspect):
    """Blender draws every camera frame with the scene resolution (no per-camera size):
    keep resolution X, change Y so the frame has the sheet's aspect. True if changed."""
    r = scene.render
    y = max(4, round(r.resolution_x * r.pixel_aspect_x / (aspect * r.pixel_aspect_y)))
    if r.resolution_y == y:
        return False
    r.resolution_y = y
    return True


def fill_defaults(s, obj, aspect):
    """Height = half the Z of the object's highest point, Distance = Height, Focal Length /
    Ortho Scale so ground..top fills FILL of the sheet height (visible height at the center:
    ortho_scale / aspect, or sensor / lens * distance / aspect)."""
    top = max(1e-3, max((obj.matrix_world @ Vector(c)).z for c in obj.bound_box))
    s.height = top / 2
    s.distance = s.height
    s.ortho_scale = top * aspect / FILL
    s.lens = max(1.0, SENSOR * s.distance * FILL / (aspect * top))
    s.placed = True


def place(s):
    """Put every sides camera where the settings say (live while fields are dragged)."""
    if not camera_project_ready():
        return
    prefix = PREFIX[s.cam_type]
    cams = side_cameras()
    for side, direction, rot_z, _panel in SIDES:
        cam = cams.get(side)
        if cam is None:
            continue
        name = f"{prefix}.{side}"
        if cam.name != name:
            cam.name = cam.data.name = name
        cam.location = Vector((s.center[0], s.center[1], s.height)) + direction * s.distance
        cam.rotation_mode = 'XYZ'
        cam.rotation_euler = Euler((math.pi / 2, 0.0, rot_z))
        cd = cam.data
        cd.type = s.cam_type
        cd.sensor_fit = 'HORIZONTAL'    # frame width = sensor / ortho scale, as the GN reads it
        cd.sensor_width = SENSOR
        cd.lens = s.lens
        cd.ortho_scale = s.ortho_scale
        cd.clip_start = 0.01
        cd.clip_end = max(100.0, s.distance * 3)


def _collection(scene):
    coll = bpy.data.collections.get(COLLECTION) or bpy.data.collections.new(COLLECTION)
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def _new_camera(side, panel, prefix, coll):
    name = f"{prefix}.{side}"
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    coll.objects.link(cam)
    cd = cp.cam_data(cam)
    cd.is_global = True
    cd.side = side
    cd["shift"] = (panel_shift(panel), 0.0)    # raw write: the offset is set by project()
    return cam


def project(scene, obj, reframe=False):
    """Project from Sides: create the missing cameras and reset all of them to the panel
    (image, type, lens, position around `obj`'s origin). Returns (cameras, resolution changed)."""
    s = settings(scene)
    img = s.image
    aspect = cp.image_aspect(img, scene)
    res_changed = set_render_aspect(scene, aspect)
    s.center = obj.matrix_world.translation.xy
    if reframe or not s.placed:
        fill_defaults(s, obj, aspect)

    coll = _collection(scene)
    existing = side_cameras(detached=True)
    cams = []
    for side, _direction, _rot_z, panel in SIDES:
        cam = existing.get(side) or _new_camera(side, panel, PREFIX[s.cam_type], coll)
        if not cp.is_global(cam):
            continue            # detached by the user: left as it is
        bg = cp.bg_entry(cam, create=True)
        bg.image = img
        bg.frame_method = 'FIT'     # the frame has the sheet's aspect: fills it exactly
        cam.data.show_background_images = True
        cp.set_camera_offset(cam, cp.cam_data(cam).shift)
        cams.append(cam)
    place(s)

    # objects already projecting through these cameras: new image, aspect and shift
    rewired = {o for cam in cams for o in cp.users(cam)}
    for o in rewired:
        cp.apply_slots(o, scene)
    return cams, res_changed
