"""Decimate Brush session: while the Density brush works on an object, the topology is all
there is to see.

start(): every modifier the object shows in the viewport is switched off (no "Generative
Modifiers Detected" question, no GN / Decimate re-evaluated per stroke), each 3D View goes to
Solid, Dyntopo is switched on without Blender's popup.
A timer (every TICK s, a few attribute reads - no depsgraph handler, no redraw) only looks
whether the brush is still in use: the object active, in Sculpt Mode, the Brush tool with the
Density brush. The first time it is not, stop() runs once: Dyntopo off, the modifiers back on,
the views that went to Solid back to Material Preview - then the timer ends itself.

The modifiers switched off are also written on the object (KEY), so a file saved or closed
mid-session gets them back on load."""
import json

import bpy

KEY = "multicamproject_decimate_session"    # object: JSON list of the modifiers switched off
TICK = 0.5

_active = None      # name of the object in a session
_views = []         # as_pointer() of the 3D View spaces switched to Solid


def _spaces():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                yield win, area, area.spaces.active


def active():
    return _active


def start(obj):
    """Modifiers off, Solid shading; the session's watcher starts. Returns the modifiers
    switched off."""
    global _active
    if _active is not None and _active != obj.name:
        stop()
    off = json.loads(obj.get(KEY, "[]")) if obj.get(KEY) else []
    for m in obj.modifiers:
        if m.show_viewport:
            m.show_viewport = False
            off.append(m.name)
    obj[KEY] = json.dumps(off)
    for _win, _area, space in _spaces():
        if space.shading.type != 'SOLID':
            space.shading.type = 'SOLID'
            _views.append(space.as_pointer())
    _active = obj.name
    if not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=TICK)
    return off


def _still_on(obj):
    ctx = bpy.context
    if obj.mode != 'SCULPT' or ctx.view_layer.objects.active != obj:
        return False
    brush = ctx.tool_settings.sculpt.brush
    if brush is None or getattr(brush, "sculpt_brush_type", "") != 'SIMPLIFY':
        return False
    wm = ctx.window_manager
    for win in (wm.windows if wm else ()):
        tool = win.workspace.tools.from_space_view3d_mode('SCULPT', create=False)
        if tool is not None and tool.idname != "builtin.brush":
            return False        # another tool (e.g. PolyCut) took over
    return True


def _busy():
    """A modal operator (a brush stroke, the F / Ctrl+Shift+F radial control...) is running:
    switching Dyntopo under a stroke crashes Blender in its sculpt undo when the stroke ends."""
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        try:
            if any(op.bl_idname.startswith(("SCULPT_OT_", "PAINT_OT_", "WM_OT_radial_control"))
                   for op in win.modal_operators):
                return True
        except (AttributeError, ReferenceError):
            pass
    return False


def _watch():
    if _active is None:
        return None
    obj = bpy.data.objects.get(_active)
    try:
        on = obj is not None and _still_on(obj)
    except (ReferenceError, AttributeError):
        on = False
    if on or _busy():       # never end the session in the middle of a stroke
        return TICK
    stop()
    return None


def _dyntopo_off(obj):
    """Dyntopo off while the object is still in Sculpt Mode (leaving it switches it off)."""
    if obj.mode != 'SCULPT' or not obj.use_dynamic_topology_sculpting:
        return
    for win, area, _space in _spaces():
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is None:
            continue
        try:
            with bpy.context.temp_override(window=win, area=area, region=region,
                                           active_object=obj, object=obj):
                bpy.ops.sculpt.dynamic_topology_toggle()
        except RuntimeError as e:
            print(f"[MultiCamProject] Dyntopo not switched off: {e}")
        return


def restore(obj):
    """The modifiers the session switched off, back on."""
    names = json.loads(obj.get(KEY, "[]")) if obj.get(KEY) else []
    for n in names:
        m = obj.modifiers.get(n)
        if m is not None:
            m.show_viewport = True
    if KEY in obj:
        del obj[KEY]


def stop():
    """End the session: Dyntopo off, modifiers on, Solid views back to Material Preview."""
    global _active
    name, _active = _active, None
    obj = bpy.data.objects.get(name) if name else None
    try:
        if obj is not None:
            _dyntopo_off(obj)
            restore(obj)
        for _win, _area, space in _spaces():
            if space.as_pointer() in _views and space.shading.type == 'SOLID':
                space.shading.type = 'MATERIAL'
    finally:
        _views.clear()
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)


def restore_all():
    """On load: objects saved mid-session get their modifiers back."""
    for obj in bpy.data.objects:
        if obj.get(KEY) and not obj.library:
            restore(obj)
