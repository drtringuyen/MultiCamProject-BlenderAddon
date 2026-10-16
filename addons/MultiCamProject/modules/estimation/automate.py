"""Automate (Polycount Estimation): 02 Mesh Workflow for every picked Raw row in one go.

A Raw row's Stage cell opens a popup: Skip / 2B Remesh / 2C Retopo / 2D Only Project, the new
object's Name, 2C's "From" mesh (the ROOM_Template part the scan lies on, matched once).
Automate then runs the picked rows one after another as a blocking job:

  2B / 2C   the copy, named ENV_<prefix>.<next ##>_<Name>, into EXPORT, with the projection
            (linking.operators.window_copy - as the 2B / 2C window buttons)
  2D        the projection on the scan itself (as multicamproject.setup)
  then      a headless Send Out: a background Blender builds the work file, saves it as
            Bake Folder/<Name>.blend and quits (linking.core.send_out(headless=True)). The
            row is linked to it - its open button opens it.

2A is never automated: it moves the shared Sides cameras to one object.
Start Over (the popup of a 2B / 2C / 2D object not baked yet) undoes it: the link ends, the
work file goes to Bake Folder/_previous, the copy is deleted and the scan reset to Raw."""
import os
import time

import bpy
from bpy.props import StringProperty

from ... import module_manager

NEEDS = ("remesh", "linking", "export", "baking", "camera_project")
SHORT = {'SKIP': "Raw", 'REMESH': "2B Remesh", 'RETOPO': "2C Retopo",
         'PROJECT': "2D Project"}
MATCH_MIN = 0.8         # a scan this much on a ROOM_Template part is a wall: 2C from it
PREVIOUS_DIR = "_previous"


def ready():
    """'' when the modules Automate uses are on, else which is missing."""
    off = [m for m in NEEDS if not module_manager.is_loaded(m)]
    return f"Needs the {', '.join(off)} module(s)" if off else ""


def _settings(scene):
    return scene.multicamproject_estimation


# ---------------------------------------------------------------- which rows

def pickable(obj):
    """'' when obj can be automated (a scan that took no workflow), else why not."""
    if obj is None or obj.type != 'MESH' or obj.library:
        return "Not a mesh of this file"
    from ... import steps
    return steps.memo(("auto_pickable", obj.name), lambda: _pickable(obj))


def _pickable(obj):
    from ... import steps
    from ..linking import core as lk
    from ..remesh import workflow as wf
    if wf.is_handmade(obj):
        return "Handmade"
    if wf.is_copy(obj) or wf.is_low_poly(obj):
        return "Already a 2B / 2C object"
    if obj.name.endswith(wf.ORIGINAL_SUFFIX) or wf.is_original(obj):
        return "A Remesh original"
    if steps.workflow(obj) != 'NONE':
        return "Already has its workflow"
    if lk.out_record(obj) is not None:
        return "Linked to a work window"
    return ""


def made(obj):
    """obj is a 2B / 2C / 2D object that can still start over (not baked, not locked)."""
    from ... import steps
    from ..remesh import workflow as wf
    if obj is None or obj.type != 'MESH' or obj.library or wf.is_handmade(obj):
        return False
    if obj.multicamproject_bake.locked:
        return False
    if wf.is_copy(obj):
        return True
    return steps.workflow(obj) == 'PROJECT'


def fill_defaults(context, row, obj, force=False):
    """The popup's first open: the Name from the scan's name; a scan lying on a
    ROOM_Template part is a wall - 2C from that part."""
    if row.auto_filled and not force:
        return
    from . import core
    if not row.auto_name or force:
        row.auto_name = core.suggest_name(obj.name)
    match(context, row, obj)
    if row.auto_from is not None and row.auto_step == 'SKIP':
        row.auto_step = 'RETOPO'
    row.auto_filled = True


def match(context, row, obj):
    """2C's From: the ROOM_Template part the scan lies on (MATCH_MIN of its points), else
    none. An offloaded scan has no vertices to test: no match."""
    from ..remesh import workflow as wf
    part, share = wf.best_retopo_start(obj, wf.retopo_candidates(context.scene))
    row.auto_share = share
    row.auto_from = part if share >= MATCH_MIN else None


# ---------------------------------------------------------------- the plan

class Task:
    """One picked row as Automate would run it now."""

    def __init__(self, row, step):
        self.row = row              # the row's (scan's) name
        self.step = step
        self.short = ""             # the typed name, cleaned
        self.new_name = ""          # the object's name afterwards
        self.path = ""              # its work file
        self.content = []           # 2C: the names of its start meshes
        self.problems = []          # red: it won't run
        self.notes = []
        self.local = False          # Keep Local: made here, no work file


def tasks(context):
    """([Task] of the picked rows, problems that stop all of them)."""
    why = ready()
    if why:
        return [], [why]
    from ... import folders
    from ..baking import jobs, naming
    from ..linking import core as lk
    scene = context.scene
    problems = []
    if jobs.busy():
        problems.append("A bake / export is running")
    if not bpy.data.filepath:
        problems.append("Save the main file first")
    if lk.is_work_window(scene):
        problems.append("This is a work window")
    sc = naming.scheme(scene)
    if sc is None:
        problems.append("No Name Prefix (01 Setup)")
    bake_dir = folders.folder(scene, 'ORIGINALS')
    if not bake_dir:
        problems.append("No Bake Folder (01 Setup)")
    index = naming.next_index(sc) if sc else 0
    cams = any(o.type == 'CAMERA' for o in scene.objects)
    out, files = [], {}
    for row in _settings(scene).rows:
        if row.auto_step == 'SKIP':
            continue
        obj = bpy.data.objects.get(row.name)
        t = Task(row.name, row.auto_step)
        t.local = row.auto_local
        out.append(t)
        why = pickable(obj)
        if why:
            t.problems.append(why)
            continue
        if not cams and t.step == 'PROJECT':
            t.problems.append("No camera in the scene (2A first, by hand)")
        if t.step in {'REMESH', 'RETOPO'}:
            t.short = naming.clean_name(row.auto_name) if row.auto_name.strip() else ""
            if not t.short:
                t.problems.append("Type a name")
            t.new_name = naming.full_name(sc, index, t.short) if sc and t.short else ""
            index += 1
            if t.new_name and bpy.data.objects.get(t.new_name) is not None:
                t.problems.append(f"{t.new_name} exists already")
            if t.step == 'RETOPO':
                if row.auto_from is not None:
                    t.content = [row.auto_from.name]
                else:
                    t.notes.append("no From mesh: an empty retopo")
            file_name = t.short
        else:                       # 2D: the scan keeps its name (as Send Out names it)
            t.new_name = obj.name
            file_name = naming.short_name(obj.name)
        if t.local:
            t.notes.append("Keep Local: stays in this file, no work file")
        elif bake_dir and file_name:
            t.path = os.path.join(bake_dir, naming.clean_name(file_name) + ".blend")
            key = os.path.normcase(t.path)
            if key in files:
                t.problems.append(f"Same work file as {files[key]}")
            files.setdefault(key, row.name)
            if os.path.exists(t.path):
                t.problems.append(f"{os.path.basename(t.path)} is in the Bake Folder already")
    return out, problems


def task_of(context, name):
    ts, problems = tasks(context)
    return next((t for t in ts if t.row == name), None), problems


# ---------------------------------------------------------------- the run

FIX_ITEM = "Fix"           # the progress list's item of the Fix (export's own: "Write FBX")


def run(ts, lines, fix=False, export=False):
    """The job: each task in turn, then (`fix`) the Fix of the objects kept here and
    (`export`) Fix + Export FBX (a generator for jobs.start)."""
    from ..baking import jobs
    local = []
    for t in ts:
        if jobs.stop_requested():
            jobs.skip_rest()
            return
        jobs.item_start(t.row)
        try:
            name = yield from _one(t, lines)
        except Exception as e:
            jobs.item_end(t.row, str(e))
            lines.append(('ERROR', f"{t.row}: {e}"))
            continue
        jobs.item_end(t.row)
        lines.append(('INFO', f"{name} -> {os.path.basename(t.path)}" if t.path
                      else f"{name}: kept local"))
        if t.local:
            local.append(name)
    if fix and not jobs.stop_requested():
        yield from _fix(local, lines)
    if export and not jobs.stop_requested():
        yield from _export(lines)
    if jobs.stop_requested():
        jobs.skip_rest()


def _fix(names, lines):
    """The Fix of each object Automate kept in this file (as its row's Fix button)."""
    from ..baking import jobs
    from ..export import autofix
    jobs.item_start(FIX_ITEM)
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        if jobs.stop_requested():
            break
        yield jobs.Step(f"Fix {name}")
        try:
            yield from autofix.run_object_steps(bpy.context, obj,
                                                lambda sev, text: lines.append((sev, text)))
        except (RuntimeError, KeyError) as e:
            lines.append(('ERROR', f"{name}: Fix failed - {e}"))
    jobs.item_end(FIX_ITEM, skipped=not names)
    if not names:
        lines.append(('INFO', "Fix: no object kept in this file (the others are fixed when "
                              "they come back)"))


def _linked(obj):
    from ..linking import core as lk
    return lk.out_record(obj) is not None


def _export(lines):
    """Fix + Export FBX, as the Export button - the objects linked to a work file are left
    as they are (their work file has them; they are not baked here)."""
    from ..baking import jobs
    from ..export import autofix, operators as eops
    scene = bpy.context.scene
    eops._auto_names_on(bpy.context)
    p = autofix.plan(scene)
    keep = [o for o in p.bake + p.transforms + p.make_uv if _linked(o)]
    if keep:
        p = p._replace(bake=[o for o in p.bake if not _linked(o)],
                       transforms=[o for o in p.transforms if not _linked(o)],
                       make_uv=[o for o in p.make_uv if not _linked(o)])
        lines.append(('INFO', f"Export: {len({o.name for o in keep})} object(s) in a work "
                              "file not fixed here"))
    log, files, missing = yield from eops._export_steps(bpy.context, p)
    lines.extend(eops._export_finish((log, files, missing), None))


def _one(t, lines):
    from ... import folders, steps
    from ..baking import jobs, naming
    from ..camera_project import core as cp, operators as cops
    from ..linking import core as lk, operators as lops
    context = bpy.context
    scene = context.scene
    obj = bpy.data.objects.get(t.row)
    why = pickable(obj) if obj is not None else "the object is gone"
    if why:
        raise RuntimeError(why)
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    if t.path and os.path.exists(t.path):
        raise RuntimeError(f"{os.path.basename(t.path)} is in the Bake Folder already")
    if t.step in {'REMESH', 'RETOPO'}:
        yield jobs.Step(f"{SHORT[t.step]}: the copy and its projection")
        sc = naming.scheme(scene)
        new_name = naming.full_name(sc, naming.next_index(sc), t.short)
        if bpy.data.objects.get(new_name) is not None:
            raise RuntimeError(f"{new_name} exists already")
        content = [bpy.data.objects[n] for n in t.content if n in bpy.data.objects]
        target, warnings = lops.window_copy(context, obj, t.step, new_name, content=content)
    else:
        yield jobs.Step("2D: projecting the cameras")
        warnings = cp.setup(obj, scene)
        cops._route_after(obj)
        cops._workflow_after(obj)
        target = obj
    for w in warnings:
        lines.append(('WARNING', f"{target.name}: {w}"))
    row = _settings(scene).rows.get(target.name)
    if row is not None:
        row.auto_step = 'SKIP'      # done: the row (it followed the copy) is no pick any more
    steps.forget()
    if t.local:                     # Keep Local: no work file, it is worked on here
        return target.name
    yield jobs.Step(f"writing {target.name}")
    folders.make_dir(scene, 'ORIGINALS')
    job_id = lk.send_out(context, target, start='' if t.step == 'PROJECT' else t.step,
                         save_as=t.path, headless=True)
    t0 = time.perf_counter()
    while True:
        result = lk.headless_result(job_id)
        if result is not None:
            break
        if bpy.app.background:      # run_sync: no timer between the steps
            time.sleep(0.1)
        yield jobs.Step(f"building {os.path.basename(t.path)} in the background "
                        f"({int(time.perf_counter() - t0)} s)")
    steps.forget()
    if result:                  # the copy is made; no file: not linked to anything
        lk.cancel(target)
        raise RuntimeError(f"'{target.name}' is made, but {result} - Send Out it by hand")
    return target.name


# ---------------------------------------------------------------- Start Over

def _unlink(scene, obj):
    """The link ends and its work file goes to Bake Folder/_previous. Returns text lines."""
    from ... import folders
    from ..linking import core as lk
    out = []
    path = lk.saved_window(obj) or lk.last_window(obj)
    lk.cancel(obj)
    if lk.LAST_WINDOW_KEY in obj:
        del obj[lk.LAST_WINDOW_KEY]
    if path and os.path.isfile(path):
        dest_dir = os.path.join(folders.folder(scene, 'ORIGINALS') or os.path.dirname(path),
                                PREVIOUS_DIR)
        os.makedirs(dest_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        dest = os.path.join(dest_dir, f"{stem}_{time.strftime('%Y%m%d-%H%M%S')}.blend")
        os.replace(path, dest)
        if os.path.isfile(path + "1"):      # Blender's backup goes along
            os.replace(path + "1", dest + "1")
        out.append(f"{os.path.basename(path)} -> {PREVIOUS_DIR}/{os.path.basename(dest)}")
    return out


def linked(obj):
    """obj is linked to a work file (or has one from before its X)."""
    from ..linking import core as lk
    return obj is not None and (lk.out_record(obj) is not None or bool(lk.last_window(obj)))


SAVED_SLACK = 120      # s: a work file saved this long after its last Send Back has edits


def unsent_edits(obj):
    """obj's work file was saved after its last Send Back (or, never sent back, after it
    was built): edits a Receive would not bring."""
    from ..linking import core as lk
    rec = lk.out_record(obj)
    path = lk.saved_window(obj) or lk.last_window(obj)
    if not path:
        return False
    man = lk.sent_back(obj) if rec is not None else None
    last = max(rec.get("time", 0) if rec else 0, rec.get("received", 0) if rec else 0,
               man.get("time", 0) if man else 0)
    try:
        return os.path.getmtime(path) > last + SAVED_SLACK
    except OSError:
        return False


def keep_local(context, obj):
    """Load obj back into the main file for good: a waiting Send Back is received first,
    then the link ends and its work file goes to Bake Folder/_previous - obj is worked on
    here from now on. Returns text lines."""
    from ... import steps
    from ..linking import core as lk
    from ..remesh import workflow as wf
    out = []
    if lk.new_send(obj) is not None:
        faces, notes = lk.receive(context, obj, 'REPLACE')
        out.append(f"received the work file's Send Back ({faces:,} faces)")
        out += notes
    out += _unlink(context.scene, obj)
    if wf.decimate_in_window(obj):      # a Decimate that never came back: its GN show again
        wf.set_gn(obj, True)
    steps.forget()
    return out


def start_over(context, obj):
    """Undo 2B / 2C / 2D on a row's object: the link ends, its work file goes to
    Bake Folder/_previous, a copy is deleted and its scan reset (back where the copy was,
    its name back), a 2D scan reset. The row is Raw again. Returns text lines."""
    from ... import roles, steps
    from ..linking import originals
    from ..remesh import workflow as wf
    scene = context.scene
    out = []
    out += _unlink(scene, obj)
    s = _settings(scene)
    if wf.is_copy(obj):
        orig = wf.source_of(obj)
        export = roles.find(scene, 'EXPORT')
        colls = [c for c in obj.users_collection if c != export]   # OBJECTS / ROOM
        name = obj.name
        me = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        out.append(f"{name} deleted")
        originals.ensure_loaded(orig)
        out += wf.reset_object(orig)
        orig_coll = roles.find(scene, 'ORIGINALS')
        for c in colls:
            if orig.name not in c.objects:
                c.objects.link(orig)
        if colls and orig_coll is not None and orig.name in orig_coll.objects:
            orig_coll.objects.unlink(orig)
        if orig.name.endswith(wf.ORIGINAL_SUFFIX):      # (a 2B made here: its name back)
            base = orig.name[:-len(wf.ORIGINAL_SUFFIX)]
            if bpy.data.objects.get(base) is None:
                orig.name = base
                if orig.data.users == 1 and orig.data.name.endswith(wf.ORIGINAL_SUFFIX):
                    orig.data.name = orig.data.name[:-len(wf.ORIGINAL_SUFFIX)]
        if context.view_layer.objects.get(orig.name) is not None:
            orig.hide_set(False)
        row = s.rows.get(name)
        if row is not None and s.rows.get(orig.name) is None:
            row.name = orig.name
        target = orig
    else:
        out += wf.reset_object(obj)
        target = obj
    row = s.rows.get(target.name)
    if row is not None:
        row.auto_step, row.auto_filled = 'SKIP', False
    steps.forget()
    return out


# ---------------------------------------------------------------- operators

class MULTICAMPROJECT_OT_EstimationAutoPick(bpy.types.Operator):
    """Pick what Automate does with this scan: 2B Remesh, 2C Retopo or 2D Only Project -
    with the new object's name (and 2C's start mesh). Nothing runs until Automate"""
    bl_idname = "multicamproject.estimation_auto_pick"
    bl_label = "Automate Step"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        obj = bpy.data.objects.get(props.object_name)
        if obj is not None and not pickable(obj):
            return cls.__doc__
        return ("Worked on already (2B / 2C / 2D, not baked): its work file and Start Over "
                "(back to Raw)")

    def _row(self, context):
        return _settings(context.scene).rows.get(self.object_name)

    def invoke(self, context, event):
        row = self._row(context)
        obj = bpy.data.objects.get(self.object_name)
        if row is None or obj is None:
            return {'CANCELLED'}
        if not pickable(obj):
            fill_defaults(context, row, obj)
        self._before = (row.auto_step, row.auto_name, row.auto_from)
        return context.window_manager.invoke_props_dialog(
            self, width=440, title=obj.name, confirm_text="OK")

    def draw(self, context):
        row = self._row(context)
        obj = bpy.data.objects.get(self.object_name)
        if row is None or obj is None:
            return
        layout = self.layout
        why = pickable(obj)
        if why:
            _draw_made(layout, context, obj, why)
            return
        layout.row().prop(row, "auto_step", expand=True)
        hint = layout.row()
        hint.active = False
        hint.label(text=next(d for k, _n, d in _step_items() if k == row.auto_step),
                   icon='INFO')
        if row.auto_step == 'SKIP':
            return
        col = layout.column()
        if row.auto_step in {'REMESH', 'RETOPO'}:
            col.prop(row, "auto_name")
        col.prop(row, "auto_local")
        if row.auto_step == 'RETOPO':
            line = col.row(align=True)
            line.prop(row, "auto_from")
            buttons = line.row(align=True)
            buttons.operator(MULTICAMPROJECT_OT_EstimationFromActive.bl_idname, text="Active",
                             icon='RESTRICT_SELECT_OFF').object_name = obj.name
            buttons.operator(MULTICAMPROJECT_OT_EstimationAutoMatch.bl_idname,
                             text="Guess by Area", icon='VIEWZOOM').object_name = obj.name
            if row.auto_from is not None and row.auto_share:
                note = col.row()
                note.active = False
                note.label(text=f"The scan lies {row.auto_share * 100:.0f}% on "
                                f"{row.auto_from.name}", icon='CHECKMARK')
        t, problems = task_of(context, row.name)
        box = layout.box().column(align=True)
        if t is not None:
            box.label(text=t.new_name or "-", icon='OBJECT_DATA')
            box.label(text=os.path.basename(t.path) if t.path else
                      ("stays in this file" if t.local else "-"), icon='FILE_BLEND')
            _draw_lines(box, problems + t.problems, t.notes)

    def execute(self, context):
        from ... import steps
        steps.forget()
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        return {'FINISHED'}

    def cancel(self, context):
        row = self._row(context)
        before = getattr(self, "_before", None)
        if row is not None and before is not None:
            row.auto_step, row.auto_name, row.auto_from = before


def _step_items():
    from . import props
    return props.AUTO_STEPS


def _draw_lines(col, problems, notes):
    for p in problems:
        line = col.row()
        line.alert = True
        line.label(text=p, icon='ERROR')
    for n in notes:
        line = col.row()
        line.active = False
        line.label(text=n, icon='INFO')


def _draw_made(layout, context, obj, why):
    from ... import steps
    from ..linking import core as lk
    col = layout.column()
    col.label(text=f"{why} - {steps.workflow(obj).title()}", icon='INFO')
    path = lk.saved_window(obj) or lk.last_window(obj)
    if path:
        col.label(text=f"Work file: {os.path.basename(path)}", icon='FILE_BLEND')
    if linked(obj):
        col.separator()
        col.operator(MULTICAMPROJECT_OT_EstimationKeepLocal.bl_idname,
                     icon='HOME').object_name = obj.name
        hint = col.row()
        hint.active = False
        hint.label(text="Worked on in this file: the link ends, its work file to _previous")
    if made(obj):
        col.separator()
        col.operator(MULTICAMPROJECT_OT_EstimationStartOver.bl_idname,
                     icon='LOOP_BACK').object_name = obj.name
        hint = col.row()
        hint.active = False
        hint.label(text="Back to Raw, to pick another step: the copy goes, its work file "
                        "to _previous")


class MULTICAMPROJECT_OT_EstimationFromActive(bpy.types.Operator):
    """2C starts from the active object (or, when the scan itself is active, the other
    selected mesh) - select it in the 3D View or Outliner first"""
    bl_idname = "multicamproject.estimation_from_active"
    bl_label = "From Active"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        row = _settings(context.scene).rows.get(self.object_name)
        if row is None:
            return {'CANCELLED'}
        picks = [context.active_object] + list(context.selected_objects)
        pick = next((o for o in picks if o is not None and o.type == 'MESH'
                     and o.name != row.name and not o.library), None)
        if pick is None:
            self.report({'WARNING'}, "Select the mesh to start from first (not the scan)")
            return {'CANCELLED'}
        row.auto_from, row.auto_share = pick, 0.0
        return {'FINISHED'}


class MULTICAMPROJECT_OT_EstimationAutoMatch(bpy.types.Operator):
    """Guess 2C's start by area: the ROOM_Template part this scan lies on (most of its
    points within 10 cm) and covers at least 30% of. Nothing found: the pick stays"""
    bl_idname = "multicamproject.estimation_auto_match"
    bl_label = "Guess by Area"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        row = _settings(context.scene).rows.get(self.object_name)
        obj = bpy.data.objects.get(self.object_name)
        if row is None or obj is None:
            return {'CANCELLED'}
        before = row.auto_from, row.auto_share
        match(context, row, obj)
        if row.auto_from is None:
            row.auto_from, row.auto_share = before
            self.report({'INFO'}, f"'{obj.name}' lies on no ROOM_Template part it is the "
                                  "size of - the pick stays")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_EstimationAutomate(bpy.types.Operator):
    """02 for every picked Raw row, one after another: the 2B / 2C copy (or 2D projection),
    then its work file built and saved in the Bake Folder by a background Blender - no
    window opens. Blender is locked while it runs (Esc: stop after the current object)"""
    bl_idname = "multicamproject.estimation_automate"
    bl_label = "Automate"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        from ..baking import jobs
        if context.mode != 'OBJECT':
            cls.poll_message_set("Object Mode only")
            return False
        return not jobs.busy()

    def invoke(self, context, event):
        ts, _problems = tasks(context)
        if not ts:
            self.report({'INFO'}, "Pick a step in a Raw row's Stage cell first")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(
            self, width=560, title=f"Automate {len(ts)} object(s)", confirm_text="Automate")

    def draw(self, context):
        ts, problems = tasks(context)
        col = self.layout.column(align=True)
        _draw_lines(col, problems, [])
        for t in ts:
            box = col.box().column(align=True)
            line = box.row()
            line.label(text=f"{SHORT[t.step]}   {t.row}", icon='MESH_DATA')
            if t.new_name and t.new_name != t.row:
                box.label(text=f"→ {t.new_name}", icon='OBJECT_DATA')
            if t.path:
                box.label(text=f"→ {os.path.basename(t.path)}", icon='FILE_BLEND')
            elif t.local:
                box.label(text="→ stays in this file", icon='HOME')
            _draw_lines(box, t.problems, t.notes)
        hint = self.layout.column(align=True)
        hint.active = False
        hint.label(text="One at a time: each work file is built by a background Blender "
                        "(about 15-25 s each); Keep Local ones stay here", icon='INFO')
        hint.label(text="Blender is locked meanwhile - Esc stops after the current object")
        s = _settings(context.scene)
        after = self.layout.row(align=True)
        after.label(text="Then:")
        after.prop(s, "auto_fix", toggle=True, icon='MODIFIER')
        after.prop(s, "auto_export", toggle=True, icon='EXPORT')

    def execute(self, context):
        from ..baking import jobs
        ts, problems = tasks(context)
        bad = problems + [f"{t.row}: {p}" for t in ts for p in t.problems]
        if bad:
            for p in bad[:8]:
                self.report({'ERROR'}, p)
            return {'CANCELLED'}
        lines = []

        s = _settings(context.scene)
        fix, export = s.auto_fix, s.auto_export

        def finish(result, error):
            ok = sum(1 for sev, t in lines if sev == 'INFO' and t.endswith(
                ("kept local", ".blend")))
            return lines + [('INFO', f"Automate: {ok} of {len(ts)} object(s) done")]
        from ..export import operators as eops
        names = [t.row for t in ts] + [FIX_ITEM] * fix + [eops.FBX_ITEM] * export
        return jobs.start(self, context, "Automate", names, run(ts, lines, fix, export),
                          finish, block=True)


class MULTICAMPROJECT_OT_EstimationStartOver(bpy.types.Operator):
    """Back to Raw: the link ends, the work file goes to Bake Folder/_previous, the 2B / 2C
    copy is deleted and its scan reset (a 2D scan reset) - then pick another step"""
    bl_idname = "multicamproject.estimation_start_over"
    bl_label = "Start Over"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        from ..baking import jobs
        return context.mode == 'OBJECT' and not jobs.busy()

    def invoke(self, context, event):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title=f"Start '{obj.name}' over?",
            message="Its work file moves to _previous, a 2B / 2C copy is deleted and the scan "
                    "goes back to Raw. Ctrl+Z undoes all but the file move.",
            confirm_text="Start Over", icon='WARNING')

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None or not made(obj):
            self.report({'WARNING'}, "Nothing to start over")
            return {'CANCELLED'}
        try:
            lines = start_over(context, obj)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for line in lines:
            print(f"[MultiCamProject] start over: {line}")
        self.report({'INFO'}, f"Back to Raw ({len(lines)} item(s), list in the console)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_EstimationKeepLocal(bpy.types.Operator):
    """Load it back into this file: a waiting Send Back is received first, then the link to
    its work file ends and the file goes to Bake Folder/_previous - worked on here from now on"""
    bl_idname = "multicamproject.estimation_keep_local"
    bl_label = "Keep Local"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        from ..baking import jobs
        return context.mode == 'OBJECT' and not jobs.busy()

    def invoke(self, context, event):
        from ..linking import core as lk
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}
        steps = []
        if lk.new_send(obj) is not None:
            steps.append("its Send Back is received first")
        if unsent_edits(obj):
            steps.append("its work file was saved after the last Send Back - those edits do "
                         "not come (open it and Send Back first, or find it in _previous)")
        steps.append("the work file moves to _previous, the object stays here")
        return context.window_manager.invoke_confirm(
            self, event, title=f"Load '{obj.name}' back into this file?",
            message="; ".join(steps).capitalize() + ".", confirm_text="Keep Local",
            icon='WARNING' if len(steps) > 1 else 'QUESTION')

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not linked(obj):
            self.report({'WARNING'}, "Not linked to a work file")
            return {'CANCELLED'}
        try:
            lines = keep_local(context, obj)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for line in lines:
            print(f"[MultiCamProject] keep local: {line}")
        self.report({'INFO'}, f"'{obj.name}' stays in this file")
        return {'FINISHED'}


# ---------------------------------------------------------------- the list's cells

def draw_stage(layout, context, obj, units):
    """The Stage cell: a Raw scan's is the popup button (its pick shown), a 2B / 2C / 2D
    object not baked yet opens the popup too (Start Over); else the usual cell."""
    from ... import steps
    if ready() or obj.type != 'MESH':
        return steps.draw_stage(layout, context, obj, units=units)
    pick = not pickable(obj)
    if not pick and not made(obj):
        return steps.draw_stage(layout, context, obj, units=units)
    stg = steps.stage(obj, context.scene)
    if not pick and stg not in {'RAW', 'PROCESSED'}:
        return steps.draw_stage(layout, context, obj, units=units)
    col = layout.row(align=True)
    col.ui_units_x = units
    col.label(text="", icon=steps.STAGES[stg][1])
    if pick:
        row = _settings(context.scene).rows.get(obj.name)
        step = row.auto_step if row is not None else 'SKIP'
        op = col.operator(MULTICAMPROJECT_OT_EstimationAutoPick.bl_idname, text=SHORT[step],
                          icon='DOWNARROW_HLT', depress=step != 'SKIP')
    else:
        op = col.operator(MULTICAMPROJECT_OT_EstimationAutoPick.bl_idname,
                          text=steps.STAGES[stg][0], emboss=False)
    op.object_name = obj.name


def draw_button(layout, context):
    """Automate (N picked) - next to Load All."""
    from ..baking import jobs
    if ready():
        return
    n = sum(1 for r in _settings(context.scene).rows if r.auto_step != 'SKIP')
    sub = layout.row(align=True)
    sub.enabled = bool(n) and not jobs.busy()
    sub.operator(MULTICAMPROJECT_OT_EstimationAutomate.bl_idname,
                 text=f"Automate Plan & Split Files ({n})" if n
                 else "Automate Plan & Split Files", icon='PLAY')


CLASSES = (MULTICAMPROJECT_OT_EstimationAutoPick, MULTICAMPROJECT_OT_EstimationFromActive,
           MULTICAMPROJECT_OT_EstimationAutoMatch,
           MULTICAMPROJECT_OT_EstimationAutomate, MULTICAMPROJECT_OT_EstimationStartOver,
           MULTICAMPROJECT_OT_EstimationKeepLocal)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
