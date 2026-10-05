"""Work File: model a Remesh / Retopo low poly in a small .blend of its own, then bring
back only its mesh.

Main file  -> Open in Work File: <main>__<object>.blend next to the main file, holding a
             plain copy of the low poly (its Decimate / Snap, vertex groups, UVs, face sets)
             and of its original - transforms applied, no parent, no materials, no
             add-on links. It opens in a second Blender; the main file is not changed.
Work file  -> Send Mesh: the low poly as you see it (modifiers applied) with uv_normal and
             seams only, written to <main>__<object>_mesh.blend and to Blender's clipboard
             (Ctrl+V pastes it).
Main file  -> Paste (or Ctrl+V), then Replace Mesh (the object keeps its name, materials,
             cameras and bake settings; Decimate and Snap go, as Apply would) or Add to
             Mesh (a join; a live Decimate leaves the added part alone).
Nothing else travels: cameras, materials, textures and settings stay in the main file.
"""
import json
import os
import subprocess

import bpy
from bpy.app.handlers import persistent

from . import workflow as wf

WORK_KEY = "multicamproject_work_of"        # work file scene / object: the main .blend
WORK_OBJECT_KEY = "multicamproject_work_object"     # work file scene: the main object's name
WORKFILE_KEY = "multicamproject_workfile"   # main object: its work file
INCOMING_KEY = "multicamproject_incoming"   # a sent mesh: the main object it belongs to
INCOMING_COLLECTION = "MCP_Incoming"
WORK_BAKE_FOLDER = "//01.Baking_work/"
KEEP_ATTRS = {"position", "uv_seam", "sharp_face", ".edge_verts", ".corner_vert",
              ".corner_edge", wf.cp.UV_NORMAL}


# ---------------------------------------------------------------- paths

def work_path(main_path, obj_name):
    folder, base = os.path.split(main_path)
    stem = os.path.splitext(base)[0]
    return os.path.join(folder, f"{stem}__{bpy.path.clean_name(obj_name)}.blend")


def mesh_path(work):
    return os.path.splitext(work)[0] + "_mesh.blend"


def clipboard_path():
    """Blender's own copy buffer (Ctrl+C / Ctrl+V in the 3D View): <temp>/copybuffer.blend."""
    return os.path.join(os.path.dirname(os.path.normpath(bpy.app.tempdir)), "copybuffer.blend")


def is_work_file(scene):
    return bool(scene.get(WORK_KEY))


def work_file_of(obj):
    """The work file recorded on a main-file object ('' = none yet)."""
    p = obj.get(WORKFILE_KEY, "")
    return bpy.path.abspath(p) if p else ""


def is_incoming(obj):
    return obj is not None and bool(obj.get(INCOMING_KEY))


def incoming_target(obj, context=None):
    """The main-file object a pasted mesh goes into: the one it was sent for, else the
    other selected mesh."""
    target = bpy.data.objects.get(obj.get(INCOMING_KEY, ""))
    if target is not None and target != obj and target.type == 'MESH' and not target.library:
        return target
    if context is not None:
        others = [o for o in context.selected_objects
                  if o != obj and o.type == 'MESH' and not is_incoming(o)]
        if len(others) == 1:
            return others[0]
    return None


# ---------------------------------------------------------------- mesh helpers

def _bake_world(me, matrix):
    """Transform `me` by `matrix`; a mirroring matrix flips the faces back outwards."""
    me.transform(matrix)
    if matrix.determinant() < 0:
        me.flip_normals()


def _plain_object(name, me, matrix):
    """A new object (no add-on data, no parent) whose mesh holds the world transform."""
    _bake_world(me, matrix)
    me.materials.clear()
    return bpy.data.objects.new(name, me)


def strip_mesh(me):
    """Only the geometry, uv_normal (the UV map kept, renamed) and seams stay."""
    keep = me.uv_layers.get(wf.cp.UV_NORMAL) or me.uv_layers.active
    for uv in [u for u in me.uv_layers if u != keep]:
        me.uv_layers.remove(uv)
    if keep is not None:
        keep.name = wf.cp.UV_NORMAL
    me.materials.clear()
    for name in [a.name for a in me.attributes]:
        a = me.attributes.get(name)
        if a is not None and name not in KEEP_ATTRS and not a.is_required:
            try:
                me.attributes.remove(a)
            except RuntimeError:
                pass


# ---------------------------------------------------------------- main file: Open

def make_work_file(context, obj):
    """Write obj + its original into the work file. Returns its path. The objects are
    written as one collection; a background Blender puts it in a scene and saves (a Scene
    cannot be written directly - workfile_build.py)."""
    main = bpy.data.filepath
    if not main:
        raise RuntimeError("Save the main file first (the work file goes next to it)")
    if obj.mode != 'OBJECT':
        obj.update_from_editmode()
    src = wf.bake_source_of(obj) or wf.source_of(obj)
    path = work_path(main, obj.name)
    lib = os.path.join(bpy.app.tempdir, "mcp_workfile_lib.blend")
    made = []
    names = {}          # name in this file (may get .001) -> name in the work file
    coll = bpy.data.collections.new("MCP Work")
    try:
        work = _plain_object(obj.name, obj.data.copy(), obj.matrix_world)
        made.append(work)
        names[work.name] = obj.name
        coll.objects.link(work)
        for vg in obj.vertex_groups:                # vg_Protect / vg_Snap for the modifiers
            if work.vertex_groups.get(vg.name) is None:
                work.vertex_groups.new(name=vg.name)
        if obj.get(wf.APPLIED_KEY):
            work[wf.APPLIED_KEY] = True
        work[WORK_KEY] = main
        d = work.multicamproject_bake
        d.retopo = wf.is_retopo(obj)
        if src is not None:
            orig = _plain_object(src.name, src.data.copy(), src.matrix_world)
            made.append(orig)
            names[orig.name] = src.name
            coll.objects.link(orig)
            d.bake_source = orig                    # Cutting & Modelling shows, Snap aims here
        dec = wf.decimate_modifier(obj)
        if dec is not None:
            new = wf.ensure_stack(work, applied_ok=False)
            new.ratio = dec.ratio
            new.show_viewport = dec.show_viewport
        snap = wf.snap_modifier(obj)
        if snap is not None and src is not None:
            wf.set_snap(work, True).show_viewport = snap.show_viewport
        bpy.data.libraries.write(lib, {coll}, path_remap='RELATIVE_ALL')
        work_name, coll_name = work.name, coll.name
    finally:
        for o in made:
            me = o.data
            bpy.data.objects.remove(o)
            if me.users == 0:
                bpy.data.meshes.remove(me)
        bpy.data.collections.remove(coll)
    settings = {
        "collection": coll_name, "names": names, "active": work_name,
        "keys": {WORK_KEY: main, WORK_OBJECT_KEY: obj.name},
        "unit_system": context.scene.unit_settings.system,
        "unit_scale": context.scene.unit_settings.scale_length}
    script = os.path.join(os.path.dirname(__file__), "workfile_build.py")
    run = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--python", script,
                          "--", lib, path, json.dumps(settings)],
                         capture_output=True, text=True, timeout=600)
    try:
        os.remove(lib)
    except OSError:
        pass
    if run.returncode != 0 or not os.path.exists(path):
        tail = (run.stderr or run.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("Work file not written: " + " / ".join(tail))
    obj[WORKFILE_KEY] = bpy.path.relpath(path)
    return path


def open_in_blender(path):
    subprocess.Popen([bpy.app.binary_path, path])


# ---------------------------------------------------------------- work file: Send

def work_object(context):
    """The low poly of this work file (the active object if it is one)."""
    obj = context.active_object
    if obj is not None and obj.type == 'MESH' and obj.get(WORK_KEY):
        return obj
    for o in context.scene.objects:
        if o.type == 'MESH' and o.get(WORK_KEY):
            return o
    return None


def send_mesh(context, obj):
    """The low poly as seen (modifiers applied, world space), stripped, written to the
    mesh file and the clipboard. Returns (faces, mesh file)."""
    scene = context.scene
    main_name = scene.get(WORK_OBJECT_KEY, obj.name)
    dg = context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True,
                                         depsgraph=dg)
    me.name = f"{main_name}_work"
    _bake_world(me, obj.matrix_world)
    strip_mesh(me)
    sent = bpy.data.objects.new(f"{main_name}_work", me)
    sent.vertex_groups.clear()
    sent[INCOMING_KEY] = main_name
    out = mesh_path(bpy.data.filepath) if bpy.data.filepath else ""
    try:
        if out:
            bpy.data.libraries.write(out, {sent}, path_remap='RELATIVE_ALL')
        bpy.data.libraries.write(clipboard_path(), {sent}, path_remap='ABSOLUTE')
        faces = len(me.polygons)
    finally:
        bpy.data.objects.remove(sent)
        bpy.data.meshes.remove(me)
    return faces, out


# ---------------------------------------------------------------- main file: Paste

def _incoming_collection(scene):
    coll = bpy.data.collections.get(INCOMING_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(INCOMING_COLLECTION)
    if not scene.collection.children.get(coll.name):
        scene.collection.children.link(coll)
    return coll


def paste(context, obj):
    """The mesh sent for obj (its work file's _mesh.blend), into MCP_Incoming."""
    work = work_file_of(obj) or (work_path(bpy.data.filepath, obj.name) if bpy.data.filepath
                                 else "")
    path = mesh_path(work) if work else ""
    if not path or not os.path.exists(path):
        raise RuntimeError("Nothing sent yet - press Send Mesh in the work file")
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = list(src.objects)
    coll = _incoming_collection(context.scene)
    pasted = [o for o in dst.objects if o is not None]
    for o in context.selected_objects:
        o.select_set(False)
    for o in pasted:
        o[INCOMING_KEY] = obj.name
        coll.objects.link(o)
        o.select_set(True)
    if pasted:
        context.view_layer.objects.active = pasted[0]
    return pasted


def _drop_incoming(obj):
    me = obj.data
    bpy.data.objects.remove(obj)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)
    coll = bpy.data.collections.get(INCOMING_COLLECTION)
    if coll is not None and not coll.all_objects:
        bpy.data.collections.remove(coll)


def _resync(target, scene):
    try:
        from ..baking import matsync
        matsync.sync(target, scene)
    except ImportError:
        pass


def replace_mesh(context, incoming, target):
    """target's geometry becomes incoming's; name, materials, cameras, bake settings and
    the other modifiers stay. Decimate / Snap go (the sent mesh has them applied)."""
    if target.data.shape_keys:
        raise RuntimeError(f"'{target.name}' has shape keys - replace would drop them")
    me = incoming.data
    _bake_world(me, target.matrix_world.inverted())
    old = target.data
    for m in old.materials:
        me.materials.append(m)
    name = old.name
    incoming.data = bpy.data.meshes.new("_mcp_empty")   # me leaves the pasted object
    _drop_incoming(incoming)
    shared = old.users > 1
    target.data = me
    if not shared:
        bpy.data.meshes.remove(old)
    else:
        old.name = name + "_before_work"
    me.name = name
    had_decimate = wf.decimate_modifier(target) is not None
    for mod in (wf.decimate_modifier(target), wf.snap_modifier(target)):
        if mod is not None:
            target.modifiers.remove(mod)
    if target.vertex_groups.get(wf.VG_PROTECT) is None:
        target.vertex_groups.new(name=wf.VG_PROTECT)
    if had_decimate or target.get(wf.APPLIED_KEY):
        target[wf.APPLIED_KEY] = True
    _resync(target, context.scene)
    return len(me.polygons)


def add_mesh(context, incoming, target):
    """Join incoming into target. A live Decimate leaves the added part alone (vg_Protect)."""
    if wf.decimate_modifier(target) is not None:
        vg = incoming.vertex_groups.new(name=wf.VG_PROTECT)
        vg.add(range(len(incoming.data.vertices)), 1.0, 'REPLACE')
    added = len(incoming.data.polygons)
    del incoming[INCOMING_KEY]
    incoming.hide_set(False)
    with context.temp_override(active_object=target, object=target,
                               selected_objects=[target, incoming],
                               selected_editable_objects=[target, incoming]):
        bpy.ops.object.join()
    coll = bpy.data.collections.get(INCOMING_COLLECTION)
    if coll is not None and not coll.all_objects:
        bpy.data.collections.remove(coll)
    _resync(target, context.scene)
    return added


# ---------------------------------------------------------------- operators

def _main_low_poly(context):
    obj = context.active_object
    return (obj if obj is not None and obj.type == 'MESH' and not obj.library
            and wf.is_low_poly(obj) and not is_work_file(context.scene) else None)


class MULTICAMPROJECT_OT_WorkFileOpen(bpy.types.Operator):
    """Model this low poly in a small file of its own: the object and its original only
    (transforms applied, no materials or links). Opens it in a second Blender"""
    bl_idname = "multicamproject.workfile_open"
    bl_label = "Open in Work File"
    bl_options = {'REGISTER'}

    fresh: bpy.props.BoolProperty(
        name="Make Again", default=False,
        description="Write the work file again from this object (what was modelled there "
                    "and not sent back is lost)")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _main_low_poly(context) is not None

    def invoke(self, context, event):
        if self.fresh and os.path.exists(work_file_of(context.active_object)):
            return context.window_manager.invoke_confirm(
                self, event, title="Make the work file again?",
                message="The work file is written again from this object - what was "
                        "modelled there and not sent back is lost",
                confirm_text="Make Again", icon='WARNING')
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        path = work_file_of(obj)
        try:
            if self.fresh or not path or not os.path.exists(path):
                path = make_work_file(context, obj)
                self.report({'INFO'}, f"Work file written: {os.path.basename(path)}")
            open_in_blender(path)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkFileSend(bpy.types.Operator):
    """Send the low poly back: as you see it (modifiers applied), uv_normal and seams
    only. Paste it in the main file with Ctrl+V or the Paste button"""
    bl_idname = "multicamproject.workfile_send"
    bl_label = "Send Mesh to Main File"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return is_work_file(context.scene) and work_object(context) is not None

    def execute(self, context):
        obj = work_object(context)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            faces, out = send_mesh(context, obj)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        where = "Paste in the main file (Ctrl+V or Paste)" if out else \
            "Ctrl+V in the main file (save the work file to also use Paste)"
        self.report({'INFO'}, f"{faces:,} faces sent. {where}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkFilePaste(bpy.types.Operator):
    """Paste the mesh last sent from this object's work file (into MCP_Incoming)"""
    bl_idname = "multicamproject.workfile_paste"
    bl_label = "Paste from Work File"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _main_low_poly(context) is not None

    def execute(self, context):
        try:
            pasted = paste(context, context.active_object)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Pasted {', '.join(o.name for o in pasted)}: Replace or Add next")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_WorkFileApply(bpy.types.Operator):
    """Put the pasted mesh into its object"""
    bl_idname = "multicamproject.workfile_apply"
    bl_label = "Use Pasted Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=(
        ('REPLACE', "Replace Mesh", "The object's geometry becomes the pasted mesh. Name, "
                                    "materials, cameras and bake settings stay; Decimate and "
                                    "Snap go (they are applied in the pasted mesh)"),
        ('ADD', "Add to Mesh", "Join the pasted mesh into the object (its old geometry stays)")))

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (context.mode == 'OBJECT' and is_incoming(obj)
                and incoming_target(obj, context) is not None)

    def execute(self, context):
        incoming = context.active_object
        target = incoming_target(incoming, context)
        try:
            if self.action == 'REPLACE':
                n = replace_mesh(context, incoming, target)
                self.report({'INFO'}, f"'{target.name}': mesh replaced, {n:,} faces")
            else:
                n = add_mesh(context, incoming, target)
                self.report({'INFO'}, f"'{target.name}': {n:,} faces added")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        context.view_layer.objects.active = target
        target.select_set(True)
        return {'FINISHED'}


class MULTICAMPROJECT_PT_WorkFileIncoming(bpy.types.Panel):
    """A mesh sent from a work file: where it goes"""
    bl_label = "Mesh from Work File"
    bl_idname = "MULTICAMPROJECT_PT_workfile_incoming"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return is_incoming(context.active_object)

    def draw_header(self, context):
        self.layout.label(icon='PASTEDOWN')

    def draw(self, context):
        layout = self.layout.column(align=True)
        obj = context.active_object
        target = incoming_target(obj, context)
        if target is None:
            row = layout.row()
            row.alert = True
            row.label(text=f"'{obj.get(INCOMING_KEY)}' not found: also select its object",
                      icon='ERROR')
            return
        layout.label(text=f"{len(obj.data.polygons):,} faces for '{target.name}'",
                     icon='OBJECT_DATA')
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator(MULTICAMPROJECT_OT_WorkFileApply.bl_idname, text="Replace Mesh",
                     icon='FILE_REFRESH').action = 'REPLACE'
        row.operator(MULTICAMPROJECT_OT_WorkFileApply.bl_idname, text="Add to Mesh",
                     icon='ADD').action = 'ADD'


def draw_main(layout, context, obj):
    """Cutting & Modelling in the main file: the work file row."""
    row = layout.row(align=True)
    path = work_file_of(obj)
    exists = bool(path) and os.path.exists(path)
    row.operator(MULTICAMPROJECT_OT_WorkFileOpen.bl_idname,
                 text="Open Work File" if exists else "Open in Work File",
                 icon='FILE_BLEND').fresh = False
    if exists:
        row.operator(MULTICAMPROJECT_OT_WorkFileOpen.bl_idname, text="",
                     icon='FILE_REFRESH').fresh = True
        sent = os.path.exists(mesh_path(path))
        sub = row.row(align=True)
        sub.enabled = sent
        sub.operator(MULTICAMPROJECT_OT_WorkFilePaste.bl_idname, text="Paste", icon='PASTEDOWN')


def draw_work(layout, context):
    """Cutting & Modelling in a work file: where it came from + Send."""
    box = layout.box().column(align=True)
    main = context.scene.get(WORK_KEY, "")
    box.label(text=f"Work file of {os.path.basename(main)}", icon='FILE_BLEND')
    row = box.row(align=True)
    row.scale_y = 1.4
    row.operator(MULTICAMPROJECT_OT_WorkFileSend.bl_idname, icon='EXPORT')
    info = box.row()
    info.active = False
    info.label(text="As you see it · uv_normal + seams only", icon='INFO')


_CLASSES = (MULTICAMPROJECT_OT_WorkFileOpen, MULTICAMPROJECT_OT_WorkFileSend,
            MULTICAMPROJECT_OT_WorkFilePaste, MULTICAMPROJECT_OT_WorkFileApply,
            MULTICAMPROJECT_PT_WorkFileIncoming)


def _separate_bake_folder():
    """A work file's bakes (if any) never go to the main file's folder. Set on load: the
    background Blender that writes the work file runs without the add-on."""
    try:
        for scene in bpy.data.scenes:
            s = getattr(scene, "multicamproject_bake_settings", None)
            if is_work_file(scene) and s is not None and s.output_dir != WORK_BAKE_FOLDER:
                s.output_dir = WORK_BAKE_FOLDER
    except Exception as e:  # never block a load
        print(f"[MultiCamProject] work file bake folder skipped: {e}")
    return None


@persistent
def _on_load(_):
    _separate_bake_folder()


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
