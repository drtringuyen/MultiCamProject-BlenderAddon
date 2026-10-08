"""Long bakes and exports as a job: Blender stays responsive, the status bar and the panels
show which object is baking, how far the run is and the time left.

A piece of work is a generator. It yields
    BakeJob  one bpy.ops.object.bake - resumed when Cycles is done (raises when it failed)
    Step     a line for the status bar - resumed on the next tick, the panels redraw between
and returns its result. The modal runner (MULTICAMPROJECT_OT_Job) drives it on a timer; each
Cycles bake runs as Blender's own bake job (INVOKE_DEFAULT) in the meantime. Scripts,
background mode and tests use run_sync, which drives the same generator blocking.

Esc (or Stop in the panel) stops after the object that is baking: stop_requested() is
checked between objects. Blender's bake would cancel on Esc and keep a half-baked image, so
an EscGuard sits on top of every bake job and takes the key."""
import time

import bpy


class Step:
    """A status line; `frac` (0-1) is how far the current item is."""

    def __init__(self, text, frac=None):
        self.text = text
        self.frac = frac


class BakeJob:
    """One bpy.ops.object.bake on `obj` (+ `selected`) into `image` (the active image node
    of target_nodes). `image` is a fresh image: Cycles marks it dirty when the bake worked."""

    def __init__(self, context, obj, selected, image, kw):
        self.context = context
        self.obj = obj
        self.selected = selected
        self.image = image
        self.kw = kw

    def _override(self):
        return self.context.temp_override(active_object=self.obj, object=self.obj,
                                          selected_objects=self.selected,
                                          selected_editable_objects=self.selected)

    def run_sync(self):
        with self._override():
            bpy.ops.object.bake(**self.kw)

    def start(self):
        with self._override():
            r = bpy.ops.object.bake('INVOKE_DEFAULT', **self.kw)
        if 'RUNNING_MODAL' not in r:
            raise RuntimeError("Cycles bake did not start")

    def check(self):
        """After the async bake: raise when Cycles wrote nothing (its reason is in Info)."""
        if not self.image.is_dirty:
            raise RuntimeError("Cycles bake failed - the reason is in the Info editor")


def run_sync(gen):
    """Drive `gen` blocking; returns what it returns."""
    value = None
    while True:
        try:
            item = gen.send(value)
        except StopIteration as e:
            return e.value
        if isinstance(item, BakeJob):
            item.run_sync()
        value = None


# ---------------------------------------------------------------- progress of the running job

class Item:
    def __init__(self, name):
        self.name = name
        self.state = 'QUEUED'       # QUEUED, RUNNING, DONE, FAILED, SKIPPED
        self.t0 = 0.0
        self.seconds = 0.0
        self.note = ""
        self.light = False          # quick (writing the FBX): not in the time estimate


class Run:
    """The running job: shown by draw() and the status bar."""

    def __init__(self, title, names, light=()):
        self.title = title
        self.items = [Item(n) for n in names]
        for it in self.items:
            it.light = it.name in light
        self.t0 = time.perf_counter()
        self.step = "Starting"
        self.frac = 0.0             # of the current item
        self.stop = False
        self.baking = False         # a Cycles bake job is running

    def item(self, name):
        for it in self.items:
            if it.name == name:
                return it
        it = Item(name)
        self.items.append(it)
        return it

    def current(self):
        return next((it for it in self.items if it.state == 'RUNNING'), None)

    def counts(self):
        finished = sum(it.state in {'DONE', 'FAILED', 'SKIPPED'} for it in self.items)
        return finished, len(self.items)

    def fraction(self):
        finished, total = self.counts()
        if not total:
            return 0.0
        cur = 1 if self.current() else 0
        return min((finished + cur * self.frac) / total, 1.0)

    def eta(self):
        """Seconds left, from the average of the finished objects (None before the first,
        and for the quick last items)."""
        done = [it.seconds for it in self.items if it.state in {'DONE', 'FAILED'} and not it.light]
        cur = self.current()
        if not done or (cur is not None and cur.light):
            return None
        avg = sum(done) / len(done)
        left = sum(it.state == 'QUEUED' and not it.light for it in self.items) * avg
        if cur:
            left += max(avg - (time.perf_counter() - cur.t0), 0.0)
        return left


RUN = None          # the running job's Run; None when idle


def busy():
    return RUN is not None


def stop_requested():
    return RUN is not None and RUN.stop


def item_start(name):
    if RUN is not None:
        it = RUN.item(name)
        it.state, it.t0, RUN.frac = 'RUNNING', time.perf_counter(), 0.0


def item_end(name, error="", skipped=False):
    if RUN is not None:
        it = RUN.item(name)
        it.seconds = time.perf_counter() - it.t0 if it.t0 else 0.0
        it.state = 'SKIPPED' if skipped else 'FAILED' if error else 'DONE'
        it.note = error


def rename_items(objs, old_names):
    """The objects were renamed (Export's fixes): their items follow."""
    if RUN is not None:
        new = {old: obj.name for obj, old in zip(objs, old_names)}    # all at once: swaps
        for it in RUN.items:
            it.name = new.get(it.name, it.name)


def skip_rest(reason="stopped"):
    """After a stop: every queued item is marked skipped."""
    if RUN is not None:
        for it in RUN.items:
            if it.state == 'QUEUED':
                it.state, it.note = 'SKIPPED', reason


def duration(sec):
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec}s"


def status_line():
    r = RUN
    finished, total = r.counts()
    cur = r.current()
    n = min(finished + (1 if cur else 0), total)
    head = f"{r.title}: {n}/{total}" + (f" {cur.name}" if cur else "")
    eta = r.eta()
    tail = f" - ~{duration(eta)} left" if eta is not None else ""
    stop = " - stopping after this object" if r.stop else " (Esc: stop after this object)"
    return f"{head} - {r.step}{tail}{stop}"


_ICONS = {'QUEUED': 'LAYER_USED', 'RUNNING': 'PLAY', 'DONE': 'CHECKMARK',
          'FAILED': 'CANCEL', 'SKIPPED': 'X'}


def draw(layout):
    """The progress block of the panels (only while a job runs)."""
    r = RUN
    box = layout.box()
    col = box.column(align=True)
    finished, total = r.counts()
    col.label(text=r.title, icon='RENDER_STILL')
    elapsed = duration(time.perf_counter() - r.t0)
    eta = r.eta()
    col.progress(factor=r.fraction(), type='BAR',
                 text=f"{finished}/{total} - {elapsed}"
                      + (f" - ~{duration(eta)} left" if eta is not None else ""))
    col.label(text=r.step)           # the object: the list's running row
    lst = box.column(align=True)
    for it in r.items:
        row = lst.row()
        row.alert = it.state == 'FAILED'
        row.label(text=it.name, icon=_ICONS[it.state])
        right = row.row()
        right.alignment = 'RIGHT'
        if it.state == 'RUNNING':
            right.label(text=duration(time.perf_counter() - it.t0))
        elif it.state in {'DONE', 'FAILED'}:
            right.label(text=duration(it.seconds))
        if it.note:
            sub = lst.row()
            sub.alert = it.state == 'FAILED'
            sub.label(text=f"      {it.note}")
    row = box.row()
    row.enabled = not r.stop
    row.operator("multicamproject.job_stop", icon='CANCEL',
                 text="Stopping after this object..." if r.stop else "Stop after this object")
    box.label(text="Don't edit the scene until it is done", icon='INFO')


def _redraw():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in {'VIEW_3D', 'PROPERTIES', 'STATUSBAR'}:
                area.tag_redraw()


def _solid_views():
    """Every 3D view in Material Preview / Rendered goes to Solid for the job: a Bake from
    Source shows the scan (100+ materials, their textures) and turns its materials to
    Emission and back - EEVEE in the viewport would compile and load all of it each time
    and freeze Blender. Returns [(space, shading type)] for _restore_views."""
    saved = []
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type == 'VIEW_3D' and space.shading.type in {'MATERIAL', 'RENDERED'}:
                    saved.append((space, space.shading.type))
                    space.shading.type = 'SOLID'
    return saved


def _restore_views(saved):
    for space, shading in saved:
        try:
            space.shading.type = shading
        except (ReferenceError, AttributeError):    # the area was closed meanwhile
            pass


# ---------------------------------------------------------------- the runner

_pending = None     # (title, names, gen, finish) for the runner's invoke
return_mode = None  # (object name, mode) the Bake left (Sculpt / Paint / Edit): back to it at the end


def _back_to_mode():
    """The mode the Bake button was pressed in, on the same active object."""
    global return_mode
    name, mode = return_mode or (None, None)
    return_mode = None
    obj = bpy.data.objects.get(name) if name else None
    ctx = bpy.context
    if obj is None or mode in {None, 'OBJECT'} or ctx.view_layer.objects.active != obj:
        return
    try:
        bpy.ops.object.mode_set(mode=mode)
    except RuntimeError as e:
        print(f"[MultiCamProject] back to {mode} skipped: {e}")
PREVIEW_WAIT = 30.0     # seconds at most to wait for material previews after a bake


def start(op, context, title, names, gen, finish, light=()):
    """Run `gen` as a job: `names` are the items (objects) of the progress list,
    `finish(result, error)` returns [(severity, text)] to report at the end; `light` items
    are quick ones left out of the time estimate. Blocking
    (reported by `op`) in background mode or without a window. Returns the result for `op`."""
    global _pending
    if bpy.app.background or context.window is None:
        try:
            result, error = run_sync(gen), None
        except Exception as e:
            result, error = None, e
        for sev, text in finish(result, error):
            op.report({sev}, text)
        if error is not None:
            op.report({'ERROR'}, f"{type(error).__name__}: {error}")
        _back_to_mode()
        return {'FINISHED'} if error is None else {'CANCELLED'}
    if busy():
        op.report({'WARNING'}, "A bake / export is already running")
        return {'CANCELLED'}
    _pending = (title, names, gen, finish, light)
    bpy.ops.multicamproject.job('INVOKE_DEFAULT')
    return {'FINISHED'}


class MULTICAMPROJECT_OT_Job(bpy.types.Operator):
    """Runs a bake / export job (started by the add-on's buttons)"""
    bl_idname = "multicamproject.job"
    bl_label = "MultiCamProject Job"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        global _pending, RUN
        if _pending is None or RUN is not None:
            return {'CANCELLED'}
        title, names, self._gen, self._finish, light = _pending
        _pending = None
        RUN = Run(title, names, light)
        self._views = _solid_views()
        self._job = None
        self._preview_wait = None
        self._exc = None
        self._last_draw = 0.0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        self._status(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as e:          # never leave the job hanging as busy
            return self._end(context, None, e)

    def _modal(self, context, event):
        if event.type == 'ESC':
            if event.value == 'PRESS':
                RUN.stop = True
                self._status(context)
            return {'RUNNING_MODAL'}
        # Undo / Redo would pull the data from under the job
        if event.type == 'Z' and (event.ctrl or event.oskey):
            if event.value == 'PRESS':
                self.report({'WARNING'}, "Undo is off while the add-on bakes / exports")
            return {'RUNNING_MODAL'}
        if event.type != 'TIMER':       # (any timer: Python cannot tell whose it is)
            return {'PASS_THROUGH'}
        if self._job is not None:
            if bpy.app.is_job_running('OBJECT_BAKE'):
                self._status(context)
                return {'RUNNING_MODAL'}
            # With Cycles as the scene's engine the UI starts material preview renders (Cycles,
            # Python). The next step sets the engine back, and Blender then waits for those
            # threads while holding the GIL they need: a deadlock (Blender hangs, 0% CPU).
            if self._preview_wait is None:
                self._preview_wait = time.perf_counter()
            if (bpy.app.is_job_running('RENDER_PREVIEW')
                    and time.perf_counter() - self._preview_wait < PREVIEW_WAIT):
                return {'RUNNING_MODAL'}
            self._preview_wait = None
            job, self._job = self._job, None
            RUN.baking = False
            try:
                job.check()
            except Exception as e:
                self._exc = e
        return self._advance(context)

    def _advance(self, context):
        while True:
            try:
                if self._exc is not None:
                    exc, self._exc = self._exc, None
                    item = self._gen.throw(exc)
                else:
                    item = self._gen.send(None)
            except StopIteration as e:
                return self._end(context, e.value, None)
            except Exception as e:
                return self._end(context, None, e)
            if isinstance(item, BakeJob):
                try:
                    item.start()
                except Exception as e:
                    self._exc = e
                    continue
                self._job = item
                RUN.baking = True
                bpy.ops.multicamproject.job_esc_guard('INVOKE_DEFAULT')
            elif isinstance(item, Step):
                RUN.step = item.text
                if item.frac is not None:
                    RUN.frac = item.frac
            self._status(context, force=True)
            return {'RUNNING_MODAL'}

    def _status(self, context, force=False):
        now = time.perf_counter()
        if not force and now - self._last_draw < 1.0:
            return
        self._last_draw = now
        if context.workspace is not None:
            context.workspace.status_text_set(status_line())
        _redraw()

    def _end(self, context, result, error):
        global RUN
        try:
            self._gen.close()           # after an error: its context managers restore
        except Exception:
            pass
        context.window_manager.event_timer_remove(self._timer)
        if context.workspace is not None:
            context.workspace.status_text_set(None)
        _restore_views(self._views)
        RUN = None
        try:
            lines = self._finish(result, error)
        except Exception as e:
            lines = [('ERROR', f"{type(e).__name__}: {e}")]
        if error is not None:
            lines = list(lines) + [('ERROR', f"{type(error).__name__}: {error}")]
        for sev, text in lines:
            self.report({sev}, text)
        _back_to_mode()
        _redraw()
        try:
            bpy.ops.ed.undo_push(message="MultiCamProject bake / export")
        except RuntimeError:
            pass
        return {'FINISHED'} if error is None else {'CANCELLED'}

    def cancel(self, context):
        """Blender closes the window / the file while the job runs."""
        global RUN
        try:
            self._gen.close()
        except Exception:
            pass
        _restore_views(self._views)
        RUN = None


class MULTICAMPROJECT_OT_JobEscGuard(bpy.types.Operator):
    """Sits on top of Blender's bake job: Esc stops after the object instead of cancelling
    the Cycles bake (which would keep a half-baked image)"""
    bl_idname = "multicamproject.job_esc_guard"
    bl_label = "Esc Guard"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            if event.value == 'PRESS' and RUN is not None:
                RUN.stop = True
            return {'RUNNING_MODAL'}
        if event.type == 'TIMER' and not bpy.app.is_job_running('OBJECT_BAKE'):
            return {'FINISHED', 'PASS_THROUGH'}
        return {'PASS_THROUGH'}


class MULTICAMPROJECT_OT_JobStop(bpy.types.Operator):
    """Stop the bake / export after the object that is baking now. What is done stays"""
    bl_idname = "multicamproject.job_stop"
    bl_label = "Stop after this object"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return RUN is not None and not RUN.stop

    def execute(self, context):
        RUN.stop = True
        _redraw()
        return {'FINISHED'}


_classes = (MULTICAMPROJECT_OT_Job, MULTICAMPROJECT_OT_JobEscGuard, MULTICAMPROJECT_OT_JobStop)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
