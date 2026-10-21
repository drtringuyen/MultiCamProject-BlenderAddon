"""Camera view = the photo. Whenever a 3D view looks through a camera with a background
photo - solo, step, Numpad 0, Ctrl+Numpad 0, the outliner - that camera gets Sensor Fit
Horizontal (the projection's frame: sensor_width is the photo's width; Auto puts it on the
render's longer side) and the scene's resolution the photo's pixel size when its aspect is
off (a resolution that already fits - Project from Sides - stays). A timer: entering
camera view has no event of its own."""
import bpy

from . import core, imgsize

INTERVAL = 0.25
_dims = {}      # (image name, filepath) -> (w, h): the header is read once per photo


def _photo_size(img):
    key = (img.name, img.filepath)
    if key not in _dims:
        _dims[key] = imgsize.size(img)
    return _dims[key]


def viewed_cameras():
    """[(scene, camera)] every 3D view looks through right now."""
    out = []
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        scene = win.scene
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            r3d = getattr(space, "region_3d", None)
            if r3d is None or r3d.view_perspective != 'CAMERA':
                continue
            local = space.local_view is not None or space.use_local_camera
            cam = space.camera if local and space.camera else scene.camera
            if cam is not None and cam.type == 'CAMERA':
                out.append((scene, cam))
    return out


def fit(scene, cam):
    """Sensor Fit Horizontal + the photo's resolution, for a camera with a photo."""
    img = core.cam_image(cam)
    dims = _photo_size(img) if img is not None else None
    if not dims:
        return
    d = cam.data
    if d.sensor_fit != 'HORIZONTAL' and not d.library:
        d.sensor_fit = 'HORIZONTAL'
    w, h = dims
    r = scene.render
    aspect = r.resolution_x * r.pixel_aspect_x / max(1e-6, r.resolution_y * r.pixel_aspect_y)
    if abs(aspect - w / h) > 1e-3 and not scene.library:
        r.resolution_x, r.resolution_y = w, h
        r.pixel_aspect_x = r.pixel_aspect_y = 1.0


def _busy():
    from ... import module_manager
    if module_manager.is_loaded("baking"):
        from ..baking import jobs
        if jobs.busy():
            return True
    return bpy.app.is_job_running('RENDER')


def _tick():
    try:
        if not _busy():
            for scene, cam in viewed_cameras():
                fit(scene, cam)
    except Exception as e:      # never stop the timer
        print(f"[MultiCamProject] camera fit: {e}")
    return INTERVAL


def register():
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _dims.clear()
