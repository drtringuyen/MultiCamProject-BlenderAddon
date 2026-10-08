"""Liquify in the 3D Viewport, through a soloed camera: the brush paints on the camera's
background photo where you see it (camview maps the mouse onto the photo).

Started from the Liquify button of a camera row (Camera Project panel), or L over a soloed
camera view (K there starts the lasso, lasso_ops.py): solos the camera if needed and starts
(or joins) the Liquify session on its photo. Enter bakes, Esc cancels -
and so does leaving solo for any reason (another camera, leaving camera view or local view,
another image on the camera, the area closing). Plain middle mouse is blocked: orbiting would
leave the camera. Keys over the viewport: W R S P B brushes, F / [ ] size, Ctrl+Z /
Ctrl+Shift+Z strokes, Alt+LMB Pucker <-> Bloat.
"""
import bpy
from bpy.props import StringProperty

from . import camview, props, session, tool
from .operators import TICK, StrokeMixin

_BRUSH_KEYS = {'W': 'WARP', 'R': 'RECONSTRUCT', 'S': 'SMOOTH', 'P': 'PUCKER', 'B': 'BLOAT'}
_HEADER = ("Liquify {cam} ({w}x{h} preview): LMB paint (Alt: Pucker/Bloat) · W R S P B brush · "
           "F / [ ] size · Ctrl+Z undo · Enter bake · Esc cancel")

running = None          # name of the camera being liquified, for the button's depressed look
_cursor = {}            # {"area": pointer, "pos": (x, y), "radius": px} while running


def _cam_core():
    from ..camera_project import core, operators
    return core, operators


def _window_region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None)


def _over_canvas(area, region, event):
    """Mouse over the viewport itself - not over another area, nor over the sidebar,
    toolbar or headers drawn on top of it."""
    x, y = event.mouse_x, event.mouse_y
    if not (region.x <= x < region.x + region.width and region.y <= y < region.y + region.height):
        return False
    for r in area.regions:
        if r.type != 'WINDOW' and r.width > 1 and r.height > 1 \
                and r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
            return False
    return True


def _in_area(area, event):
    return (area.x <= event.mouse_x < area.x + area.width
            and area.y <= event.mouse_y < area.y + area.height)


def _draw():
    context = bpy.context
    area = context.area
    if not _cursor or area is None or area.as_pointer() != _cursor.get("area") \
            or context.region is None or context.region.type != 'WINDOW':
        return
    pos = _cursor.get("pos")
    if pos is not None:
        tool.draw_circle(pos, _cursor["radius"])


class MULTICAMPROJECT_OT_LiquifyCamera(StrokeMixin, bpy.types.Operator):
    """Nudge Projection: Photoshop's Liquify on this camera's photo, to line the photo up
    with the mesh. Works only through the soloed camera (it solos it). Paint on the photo,
    the projection on the mesh follows live. W Warp (push) · R Reconstruct (back to the
    original) · S Smooth · P Pucker / B Bloat (Alt+LMB swaps them) · F or [ ] brush size ·
    Ctrl+Z undo a stroke · Enter applies at full size · Esc cancels (so does leaving solo)"""
    bl_idname = "multicamproject.liquify_camera"
    bl_label = "Nudge Projection"

    camera: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        try:
            _core, ops = _cam_core()
        except ImportError:
            return False
        return ops.MULTICAMPROJECT_OT_SoloCamera.poll(context)

    # ------------------------------------------------ state checks

    def _cam(self):
        return bpy.data.objects.get(self.camera)

    def _still_valid(self, context):
        """None while Liquify may go on, else why it stops."""
        if context.area is None or context.area.as_pointer() != self.area_ptr:
            return "the viewport closed"
        cam = self._cam()
        core, ops = _cam_core()
        if cam is None or not ops.is_solo(context, cam):
            return "left solo"
        s = session.active()
        if s is None:
            return "session ended"
        if core.cam_image(cam) != s.preview:
            return "the camera shows another image"
        return None

    def _placement(self, context, s):
        core, _ops = _cam_core()
        cam = self._cam()
        bg = core.bg_entry(cam)
        region = _window_region(context.area)
        if bg is None or region is None:
            return None, None
        return camview.placement(region, context.space_data.region_3d, context.scene, cam, bg,
                                 s.size[0] / s.size[1]), region

    def _to_px(self, s, place, region, event):
        u, v = place.to_uv(event.mouse_x - region.x, event.mouse_y - region.y)
        return s.lq.uv_to_px(u, v)

    def _update_cursor(self, context, s, event, place=None, region=None):
        if place is None:
            place, region = self._placement(context, s)
        if place is None or not _over_canvas(context.area, region, event):
            _cursor["pos"] = None
        else:
            _cursor["pos"] = (event.mouse_x - region.x, event.mouse_y - region.y)
            _cursor["radius"] = props.settings(context).size * 0.5 * place.w / s.lq.w
        context.area.tag_redraw()

    # ------------------------------------------------ start / end

    def invoke(self, context, event):
        global running
        if running is not None:
            self.report({'INFO'}, f"Liquify is running on {running}: Enter bakes, Esc cancels")
            return {'CANCELLED'}
        core, ops = _cam_core()
        cam = self._cam()
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        img = core.cam_image(cam)
        s = session.active()
        if s is not None and img != s.preview:
            self.report({'ERROR'}, f"Liquify is running on {s.photo_name} - bake or cancel it first")
            return {'CANCELLED'}
        if s is None and not core.image_ok(img):
            self.report({'ERROR'}, f"{cam.name} has no photo to liquify")
            return {'CANCELLED'}
        if not ops.is_solo(context, cam):
            bpy.ops.multicamproject.solo_camera(camera=cam.name)
            if not ops.is_solo(context, cam):
                self.report({'ERROR'}, f"Could not solo {cam.name}")
                return {'CANCELLED'}
        if s is None:
            try:
                s = session.start(img)
            except ValueError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        self.area_ptr = context.area.as_pointer()
        self.stroking = False
        self.resizing = None
        self.invert = False
        self.travel = 0.0
        self.last = None
        running = cam.name
        _cursor.clear()
        _cursor.update(area=self.area_ptr, pos=None, radius=0.0)
        self._timer = context.window_manager.event_timer_add(TICK, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(_HEADER.format(cam=cam.name, w=s.lq.w, h=s.lq.h))
        session.redraw()
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        global running
        running = None
        _cursor.clear()
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if context.area is not None and context.area.as_pointer() == self.area_ptr:
            context.area.header_text_set(None)
        session.redraw()

    def _cancel(self, context, why=None):
        s = session.active()
        if s is not None:
            if s.lq.in_stroke:
                s.lq.stroke_cancel()
            session.cancel()
        self._finish(context)
        if why:
            self.report({'INFO'}, f"Liquify cancelled: {why}")
        return {'CANCELLED'}

    def cancel(self, context):
        """Blender ends the operator (file load, window closed): the session goes too."""
        self._cancel(context)

    # ------------------------------------------------ events

    def modal(self, context, event):
        why = self._still_valid(context)
        if why:
            if why == "session ended":       # baked / cancelled in the Image Editor
                self._finish(context)
                return {'FINISHED'}
            return self._cancel(context, why)
        s = session.active()
        st = props.settings(context)
        place, region = self._placement(context, s)
        canvas = place is not None and _over_canvas(context.area, region, event)

        if self.stroking:
            return self._modal_stroke(context, event, s, st, place, region)
        if self.resizing is not None:
            return self._modal_resize(context, event, st)

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._update_cursor(context, s, event, place, region)
            return {'PASS_THROUGH'}
        if event.value != 'PRESS':
            return {'PASS_THROUGH'}

        # Enter / Esc anywhere over this viewport, its sidebar included (the button is there);
        # over other editors they keep their job (the Image Editor maps them to Bake / Cancel)
        here = _in_area(context.area, event)
        if event.type == 'ESC' and here:
            return self._cancel(context)
        if event.type in {'RET', 'NUMPAD_ENTER'} and here:
            try:
                path = session.bake_and_finish()
            except (ValueError, OSError, RuntimeError) as e:
                self.report({'ERROR'}, f"Bake failed: {e}")
                return {'RUNNING_MODAL'}
            self._finish(context)
            self.report({'INFO'}, f"Baked {bpy.path.basename(path)}")
            return {'FINISHED'}
        if not canvas:
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE':
            self.invert = event.alt
            self.travel = 0.0
            self.last = self._to_px(s, place, region, event)
            s.lq.stroke_begin()
            self.stroking = True
            if self._brush(st) != 'WARP':
                self._dab(s, st, self.last, self._strength(st, event))
                session.push(s)
            return {'RUNNING_MODAL'}
        if event.type == 'MIDDLEMOUSE' and not (event.shift or event.ctrl):
            self.report({'INFO'}, "Liquify: orbiting would leave the camera (Shift+MMB pans)")
            return {'RUNNING_MODAL'}
        if event.type == 'Z' and event.ctrl:
            box = s.lq.redo() if event.shift else s.lq.undo()
            if box is not None:
                session.push(s)
            return {'RUNNING_MODAL'}
        if event.ctrl or event.alt or event.oskey:
            return {'PASS_THROUGH'}
        if event.type in _BRUSH_KEYS and not event.shift:
            st.brush = _BRUSH_KEYS[event.type]
            return {'RUNNING_MODAL'}
        if event.type in {'LEFT_BRACKET', 'RIGHT_BRACKET'}:
            f = 1.15 if event.type == 'RIGHT_BRACKET' else 1 / 1.15
            new = round(st.size * f)
            st.size = new if new != st.size else st.size + (1 if f > 1 else -1)
            self._update_cursor(context, s, event, place, region)
            return {'RUNNING_MODAL'}
        if event.type == 'F':
            self.resizing = (event.mouse_x, st.size, place.w / s.lq.w,
                             (event.mouse_x - region.x - st.size * 0.5 * place.w / s.lq.w,
                              event.mouse_y - region.y))
            _cursor["pos"] = self.resizing[3]
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _modal_stroke(self, context, event, s, st, place, region):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if place is not None:
                p = self._to_px(s, place, region, event)
                if self._move(s, st, self.last, p, self._strength(st, event)):
                    session.push(s)
                self.last = p
                self._update_cursor(context, s, event, place, region)
        elif event.type == 'TIMER' and self._brush(st) != 'WARP':
            if self._dab(s, st, self.last, self._strength(st, event)) is not None:
                session.push(s)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            s.lq.stroke_end()
            self.stroking = False
        elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            s.lq.stroke_cancel()
            session.push(s)
            self.stroking = False
        elif event.type == 'ESC' and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}

    def _modal_resize(self, context, event, st):
        x0, size0, scale, center = self.resizing
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            st.size = max(4, min(2048, round(size0 + 2 * (event.mouse_x - x0) / max(1e-6, scale))))
            _cursor["pos"] = center
            _cursor["radius"] = st.size * 0.5 * scale
            context.area.tag_redraw()
        elif event.value == 'PRESS' and event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'F'}:
            self.resizing = None
        elif event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            st.size = size0
            self.resizing = None
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class MULTICAMPROJECT_OT_CameraKey(bpy.types.Operator):
    """L: Liquify, K: Lasso the soloed camera's photo"""
    bl_idname = "multicamproject.camera_key"
    bl_label = "Liquify / Lasso Soloed Camera"
    bl_options = {'INTERNAL'}

    tool: bpy.props.EnumProperty(items=[('LIQUIFY', "Liquify", ""), ('LASSO', "Lasso", "")])

    @classmethod
    def poll(cls, context):
        # Only over a soloed camera view - anywhere else the key keeps its usual job
        if context.area is None or context.area.type != 'VIEW_3D' \
                or context.region is None or context.region.type != 'WINDOW':
            return False
        try:
            _core, ops = _cam_core()
        except ImportError:
            return False
        cam = context.scene.camera
        return (cam is not None and ops.is_solo(context, cam)
                and ops.MULTICAMPROJECT_OT_SoloCamera.poll(context))

    def execute(self, context):
        cam = context.scene.camera.name
        op = (bpy.ops.multicamproject.liquify_camera if self.tool == 'LIQUIFY'
              else bpy.ops.multicamproject.lasso_camera)
        res = op('INVOKE_DEFAULT', camera=cam)
        return {'FINISHED'} if res & {'RUNNING_MODAL', 'FINISHED'} else {'CANCELLED'}


_draw_handle = None
_keymaps = []


def register():
    global _draw_handle
    bpy.utils.register_class(MULTICAMPROJECT_OT_LiquifyCamera)
    bpy.utils.register_class(MULTICAMPROJECT_OT_CameraKey)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:      # None in background mode
        # "Frames" is handled before the mode keymaps (Object Mode binds K), and its
        # add-on items come first: the poll lets the keys through outside a solo view.
        km = kc.keymaps.new(name="Frames")
        for key, tool_name in (('L', 'LIQUIFY'), ('K', 'LASSO')):
            kmi = km.keymap_items.new(MULTICAMPROJECT_OT_CameraKey.bl_idname, key, 'PRESS')
            kmi.properties.tool = tool_name
            _keymaps.append((km, kmi))


def unregister():
    global _draw_handle, running
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    running = None
    _cursor.clear()
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_CameraKey)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_LiquifyCamera)
