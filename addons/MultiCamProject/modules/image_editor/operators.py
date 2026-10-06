import math

import bpy
from bpy.props import BoolProperty, FloatProperty

from . import props, session, tool

DAB_SPACING = 0.25      # dab distance, share of the brush radius
MAX_DABS = 4            # per mouse event - a fast drag takes longer steps, not more dabs
TICK = 1 / 30           # Pucker/Bloat/Reconstruct/Smooth keep working while the pen rests
RECONSTRUCT_RATE = 0.3
SMOOTH_RATE = 0.5


def _space_image(context):
    sp = context.space_data
    return sp.image if sp is not None and sp.type == 'IMAGE_EDITOR' else None


def _in_image_editor(context):
    return context.area is not None and context.area.type == 'IMAGE_EDITOR'


def _set_tool(context):
    try:
        bpy.ops.wm.tool_set_by_id(name=tool.tool_id(context.space_data), space_type='IMAGE_EDITOR')
    except RuntimeError:
        pass


# No 'UNDO' in bl_options: the session lives outside Blender's undo. An undo past Start
# removes the preview and session._on_undo ends the session cleanly.

class MULTICAMPROJECT_OT_LiquifyStart(bpy.types.Operator):
    """Liquify this photo: a 1K preview replaces it in every camera background, material and
    Image Editor while you warp, so the 3D Viewport shows the result live. On a baked _lq
    image it re-opens the saved warp of the original"""
    bl_idname = "multicamproject.liquify_start"
    bl_label = "Start Liquify"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        reason = session.can_start(_space_image(context))
        if reason:
            cls.poll_message_set(reason)
        return reason is None

    def execute(self, context):
        try:
            s = session.start(_space_image(context))
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _set_tool(context)
        what = "re-editing" if s.reedit else "preview"
        self.report({'INFO'}, f"Liquify {s.photo_name}: {s.lq.w}x{s.lq.h} {what}, "
                              f"{len(s.users['cameras'])} camera(s)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyCancel(bpy.types.Operator):
    """Stop Liquify and put the untouched photo back everywhere"""
    bl_idname = "multicamproject.liquify_cancel"
    bl_label = "Cancel"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return session.active() is not None

    def execute(self, context):
        session.cancel()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyBake(bpy.types.Operator):
    """Apply the warp to the original-size photo, save it as <photo>_lq.png next to it (the warp
    is kept in <photo>_lq.npz for re-editing) and use it in every camera and material"""
    bl_idname = "multicamproject.liquify_bake"
    bl_label = "Bake"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        s = session.active()
        if s is not None and not s.out_path:
            cls.poll_message_set("Save the .blend first - this photo has no folder to bake into")
            return False
        return s is not None

    def execute(self, context):
        try:
            path = session.bake_and_finish()
        except (ValueError, OSError, RuntimeError) as e:
            self.report({'ERROR'}, f"Bake failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Baked {bpy.path.basename(path)}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyReset(bpy.types.Operator):
    """Remove the whole warp (Ctrl+Z brings it back)"""
    bl_idname = "multicamproject.liquify_reset"
    bl_label = "Reset"

    @classmethod
    def poll(cls, context):
        return session.active() is not None

    def execute(self, context):
        s = session.active()
        s.lq.reset()
        session.push(s)
        return {'FINISHED'}


class _History:
    # Poll passes for the whole session, so Ctrl+Z never falls through to Blender's undo
    # (which could step back past Start) while Liquify owns the editor.
    @classmethod
    def poll(cls, context):
        return session.active() is not None

    def execute(self, context):
        s = session.active()
        if getattr(s.lq, self._step)() is None:
            self.report({'INFO'}, f"Liquify: nothing to {self._step}")
        else:
            session.push(s)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyUndo(_History, bpy.types.Operator):
    """Undo the last Liquify stroke"""
    bl_idname = "multicamproject.liquify_undo"
    bl_label = "Undo Stroke"
    _step = "undo"


class MULTICAMPROJECT_OT_LiquifyRedo(_History, bpy.types.Operator):
    """Redo the last undone Liquify stroke"""
    bl_idname = "multicamproject.liquify_redo"
    bl_label = "Redo Stroke"
    _step = "redo"


class MULTICAMPROJECT_OT_LiquifyTool(bpy.types.Operator):
    """Pick the Liquify tool in this Image Editor"""
    bl_idname = "multicamproject.liquify_tool"
    bl_label = "Liquify Tool"

    @classmethod
    def poll(cls, context):
        return _in_image_editor(context)

    def execute(self, context):
        _set_tool(context)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyKey(bpy.types.Operator):
    """Liquify the photo in this editor: starts Liquify if needed and picks the tool (L)"""
    bl_idname = "multicamproject.liquify_key"
    bl_label = "Liquify"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        if not _in_image_editor(context):
            return False
        s = session.active()
        if s is not None:
            return _space_image(context) == s.preview
        return session.can_start(_space_image(context)) is None

    def execute(self, context):
        if session.active() is None:
            return bpy.ops.multicamproject.liquify_start()
        _set_tool(context)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyHover(bpy.types.Operator):
    """Keeps the brush circle under the mouse"""
    bl_idname = "multicamproject.liquify_hover"
    bl_label = "Liquify Brush Cursor"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if context.area is not None:
            tool.set_hover(context.area, event.mouse_region_x, event.mouse_region_y)
            context.area.tag_redraw()
        return {'PASS_THROUGH'}


class MULTICAMPROJECT_OT_LiquifySize(bpy.types.Operator):
    """Scale the Liquify brush"""
    bl_idname = "multicamproject.liquify_size"
    bl_label = "Brush Size"
    bl_options = {'INTERNAL'}

    factor: FloatProperty(default=1.15, options={'SKIP_SAVE'})

    def execute(self, context):
        st = props.settings(context)
        new = round(st.size * self.factor)
        st.size = new if new != st.size else st.size + (1 if self.factor > 1 else -1)
        if context.area is not None:
            context.area.tag_redraw()
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LiquifyResize(bpy.types.Operator):
    """Drag to resize the Liquify brush: click or Enter confirms, Esc or right click cancels"""
    bl_idname = "multicamproject.liquify_resize"
    bl_label = "Resize Brush"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        dims = tool.preview_dims(context.space_data)
        if dims is None:
            return {'CANCELLED'}
        st = props.settings(context)
        self.scale = max(1e-6, tool.screen_scale(context.region, dims))
        self.size0 = st.size
        # the circle stays put; its edge sits under the mouse
        self.center = (event.mouse_region_x - st.size * 0.5 * self.scale, event.mouse_region_y)
        self.x0 = event.mouse_region_x
        tool.set_hover(context.area, *self.center)
        context.window_manager.modal_handler_add(self)
        self._header(context)
        return {'RUNNING_MODAL'}

    def _header(self, context):
        context.area.header_text_set(f"Liquify brush size: {props.settings(context).size} px")
        context.area.tag_redraw()

    def _done(self, context):
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        st = props.settings(context)
        if event.type == 'MOUSEMOVE':
            st.size = max(4, min(2048, round(self.size0 + 2 * (event.mouse_region_x - self.x0)
                                             / self.scale)))
            tool.set_hover(context.area, *self.center)
            self._header(context)
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'F'} and event.value == 'PRESS':
            self._done(context)
            return {'FINISHED'}
        elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            st.size = self.size0
            self._done(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class StrokeMixin:
    """Brush dabs shared by the Image Editor stroke and the camera-view Liquify.
    Needs self.invert (Alt: Pucker <-> Bloat) and self.travel (distance since the last dab
    of a brush without direction)."""

    def _strength(self, st, event):
        p = event.pressure if st.use_pressure and event.pressure > 0 else 1.0
        return st.strength * p

    def _brush(self, st):
        b = st.brush
        if self.invert and b in {'PUCKER', 'BLOAT'}:
            b = 'BLOAT' if b == 'PUCKER' else 'PUCKER'
        return b

    def _dab(self, s, st, p, strength):
        """One dab of a brush without direction at p."""
        lq, r, b = s.lq, st.size * 0.5, self._brush(st)
        if b == 'RECONSTRUCT':
            return lq.reconstruct(p[0], p[1], r, strength * RECONSTRUCT_RATE)
        if b == 'SMOOTH':
            return lq.smooth(p[0], p[1], r, strength * SMOOTH_RATE)
        if b in {'PUCKER', 'BLOAT'}:
            return lq.pucker(p[0], p[1], r, strength if b == 'PUCKER' else -strength)
        return None

    def _move(self, s, st, a, b, strength):
        """Brush travelled from a to b (preview px). True when the field changed."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        dist = math.hypot(dx, dy)
        r = st.size * 0.5
        step = max(1.0, r * DAB_SPACING)
        if self._brush(st) == 'WARP':
            if dist < 1e-3:
                return False
            n = min(MAX_DABS, max(1, math.ceil(dist / step)))
            changed = False
            for k in range(n):
                c = (a[0] + dx * k / n, a[1] + dy * k / n)
                changed |= s.lq.warp(c[0], c[1], r, (dx / n, dy / n), strength) is not None
            return changed
        self.travel += dist
        if self.travel < step:
            return False
        self.travel = 0.0
        return self._dab(s, st, b, strength) is not None


class MULTICAMPROJECT_OT_LiquifyStroke(StrokeMixin, bpy.types.Operator):
    """Paint with the Liquify brush (starts Liquify on the shown photo if needed)"""
    bl_idname = "multicamproject.liquify_stroke"
    bl_label = "Liquify Stroke"
    bl_options = {'INTERNAL'}

    invert: BoolProperty(name="Invert", description="Pucker <-> Bloat", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _in_image_editor(context)

    def _px(self, s, event):
        u, v = self.region.view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)
        return s.lq.uv_to_px(u, v)

    def _finish(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    # ------------------------------------------------ events

    def invoke(self, context, event):
        sp = context.space_data
        s = session.active()
        if s is None:
            reason = session.can_start(sp.image)
            if reason:
                self.report({'WARNING'}, f"Liquify: {reason}")
                return {'CANCELLED'}
            try:
                s = session.start(sp.image)
            except ValueError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
        if sp.image != s.preview:
            self.report({'WARNING'}, f"Liquify is running on {s.photo_name} - show "
                                     f"{s.preview_name} in this editor to paint")
            return {'CANCELLED'}
        st = props.settings(context)
        self.region = context.region
        self.last = self._px(s, event)
        self.travel = 0.0
        self._timer = None
        s.lq.stroke_begin()
        if self._brush(st) != 'WARP':
            self._dab(s, st, self.last, self._strength(st, event))
            session.push(s)
        self._timer = context.window_manager.event_timer_add(TICK, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        s = session.active()
        if s is None or not s.lq.in_stroke:         # ended under us (undo, file load)
            self._finish(context)
            return {'CANCELLED'}
        st = props.settings(context)
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            tool.set_hover(context.area, event.mouse_region_x, event.mouse_region_y)
            p = self._px(s, event)
            if self._move(s, st, self.last, p, self._strength(st, event)):
                session.push(s)
            else:
                context.area.tag_redraw()
            self.last = p
        elif event.type == 'TIMER' and self._brush(st) != 'WARP':
            if self._dab(s, st, self.last, self._strength(st, event)) is not None:
                session.push(s)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            s.lq.stroke_end()
            self._finish(context)
            return {'FINISHED'}
        elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            s.lq.stroke_cancel()
            session.push(s)
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


_classes = (
    MULTICAMPROJECT_OT_LiquifyStart,
    MULTICAMPROJECT_OT_LiquifyCancel,
    MULTICAMPROJECT_OT_LiquifyBake,
    MULTICAMPROJECT_OT_LiquifyReset,
    MULTICAMPROJECT_OT_LiquifyUndo,
    MULTICAMPROJECT_OT_LiquifyRedo,
    MULTICAMPROJECT_OT_LiquifyTool,
    MULTICAMPROJECT_OT_LiquifyKey,
    MULTICAMPROJECT_OT_LiquifyHover,
    MULTICAMPROJECT_OT_LiquifySize,
    MULTICAMPROJECT_OT_LiquifyResize,
    MULTICAMPROJECT_OT_LiquifyStroke,
)


_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    # Enter bakes, Esc cancels, Ctrl+Z / Ctrl+Shift+Z undo / redo strokes - in any Image
    # Editor, with any tool, while a session runs (their polls fail otherwise, so the keys
    # keep their usual job). L starts Liquify on the shown photo / picks the tool.
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Image", space_type='IMAGE_EDITOR')
        for key in ('RET', 'NUMPAD_ENTER'):
            _keymaps.append((km, km.keymap_items.new("multicamproject.liquify_bake", key, 'PRESS')))
        _keymaps.append((km, km.keymap_items.new("multicamproject.liquify_cancel", 'ESC', 'PRESS')))
        _keymaps.append((km, km.keymap_items.new("multicamproject.liquify_undo", 'Z', 'PRESS',
                                                 ctrl=True, repeat=True)))
        _keymaps.append((km, km.keymap_items.new("multicamproject.liquify_redo", 'Z', 'PRESS',
                                                 ctrl=True, shift=True, repeat=True)))
        _keymaps.append((km, km.keymap_items.new(MULTICAMPROJECT_OT_LiquifyKey.bl_idname,
                                                 'L', 'PRESS')))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
