"""3D View keys that work whatever modules are on.

  Shift+Wheel   zoom at half the wheel's speed (fine framing). Blender's own wheel step runs
                (its zoom-to-mouse and invert-wheel preferences included), then the view goes
                back halfway: normal views (distance, location) and camera views (frame zoom,
                offset) alike.
"""
import bpy
from bpy.props import IntProperty

FACTOR = 0.5        # share of one wheel step


class MULTICAMPROJECT_OT_ViewZoomFine(bpy.types.Operator):
    """Zoom the 3D View at half the mouse wheel's speed (Shift+Wheel)"""
    bl_idname = "multicamproject.view_zoom_fine"
    bl_label = "Fine Zoom"
    bl_options = {'INTERNAL'}

    delta: IntProperty(name="Delta", default=1, options={'SKIP_SAVE'},
                       description="1 zooms in, -1 out")

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and context.region_data is not None)

    def invoke(self, context, event):
        rv3d = context.region_data
        delta = -self.delta if context.preferences.inputs.invert_zoom_wheel else self.delta
        before = (rv3d.view_distance, rv3d.view_location.copy(), rv3d.view_camera_zoom,
                  tuple(rv3d.view_camera_offset))
        bpy.ops.view3d.zoom('EXEC_DEFAULT', delta=delta, mx=event.mouse_region_x,
                            my=event.mouse_region_y)
        dist, loc, cam_zoom, cam_off = before
        t = FACTOR
        if rv3d.view_distance > 0 and dist > 0:     # geometric: an even step either way
            rv3d.view_distance = dist * (rv3d.view_distance / dist) ** t
        rv3d.view_location = loc.lerp(rv3d.view_location, t)
        rv3d.view_camera_zoom = cam_zoom + (rv3d.view_camera_zoom - cam_zoom) * t
        rv3d.view_camera_offset = [a + (b - a) * t
                                   for a, b in zip(cam_off, rv3d.view_camera_offset)]
        return {'FINISHED'}


_keymaps = []


def register():
    bpy.utils.register_class(MULTICAMPROJECT_OT_ViewZoomFine)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:      # background mode
        return
    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
    for kmi in [k for k in km.keymap_items
                if k.idname == MULTICAMPROJECT_OT_ViewZoomFine.bl_idname]:
        km.keymap_items.remove(kmi)     # left behind by a reload: not one more set
    for key, delta in (('WHEELUPMOUSE', 1), ('WHEELDOWNMOUSE', -1)):
        kmi = km.keymap_items.new(MULTICAMPROJECT_OT_ViewZoomFine.bl_idname, key, 'PRESS',
                                  shift=True)
        kmi.properties.delta = delta
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ViewZoomFine)
