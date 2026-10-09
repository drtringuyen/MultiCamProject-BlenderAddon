"""Originals offload (prototype, 2026-10-09): a scan's full mesh lives in its own .blend next to
the bake folder and is LINKED into the main file - still full res in the viewport, but no
longer written by every save and autosave of the main file.

    <bake folder>/../02.OBJECTS/<object>.blend    Original Mesh + OBJECTS scans
    <bake folder>/../zz.EXCLUDED/<object>.blend   scans in a collection named EXCLUDED

Offload: the object stays (name, collection, Bake Source pointer, transform); only its mesh
is swapped for the linked one. The mesh in the file has no materials: the object keeps them
on its own slots (link 'OBJECT'), so the scan materials stay local and shared. The linked mesh
keeps the local mesh's name and vertex count - the bake fingerprints see no change.
Load: the linked mesh is made local again, the slots go back to the mesh.

Loaded automatically where the add-on needs to change or write the original (04 Bake from
Source, Send Out, 0C / 0D) and offloaded again after a Bake from Source and a Send Out. An
unchanged mesh is not written again; a changed one moves the old file to _previous/ first.
"""
import json
import os
import shutil

import bpy
import numpy as np

KEY = "multicamproject_offload"     # on the object: {"path", "mesh", "verts", "faces", "sig"}
OBJECTS_DIR = "02.OBJECTS"
EXCLUDED_DIR = "zz.EXCLUDED"
PREVIOUS = "_previous"


# ---------------------------------------------------------------- which object, where

def _wf():
    from ..remesh import workflow
    return workflow


def original_of(obj):
    """The scan behind `obj`: its Bake Source, or obj itself when it is a scan (a mesh with
    no GN modifier and no Bake Source). None for anything else."""
    if obj is None or obj.type != 'MESH':
        return None
    src = _wf().bake_source_of(obj)
    if src is not None:
        return src
    if any(m.type == 'NODES' for m in obj.modifiers):
        return None
    return obj


def is_offloaded(obj):
    return obj is not None and obj.type == 'MESH' and obj.data is not None \
        and obj.data.library is not None and obj.get(KEY) is not None


def record(obj):
    try:
        return json.loads(obj.get(KEY, ""))
    except (TypeError, ValueError):
        return None


def _base_dir(scene):
    from ..baking import common
    bake = os.path.normpath(common.output_dir(scene))
    return os.path.dirname(bake)


def folder_for(scene, obj):
    excluded = any("EXCLUDED" in c.name.upper() for c in obj.users_collection)
    return os.path.join(_base_dir(scene), EXCLUDED_DIR if excluded else OBJECTS_DIR)


def _rel(path):
    try:
        return bpy.path.relpath(path)
    except ValueError:      # another drive than the .blend
        return path


def _sig(me):
    """A quick checksum of the shape: an unchanged original is not written again."""
    pos = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", pos)
    w = (np.arange(len(pos)) % 97).astype(np.float32)
    return f"{len(me.vertices)}/{len(me.polygons)}/{float(pos.sum(dtype=np.float64)):.4f}/" \
           f"{float(np.dot(pos, w)):.1f}"


def _users(me):
    return [o for o in bpy.data.objects if o.data == me]


def _slots_to(obj, link):
    """Keep the materials while the slots move between the object and the mesh."""
    for slot in obj.material_slots:
        mat = slot.material
        if slot.link != link:
            slot.link = link
            slot.material = mat


# ---------------------------------------------------------------- offload / load

def can_offload(obj):
    """'' when obj can be offloaded, else why not."""
    if obj is None or obj.type != 'MESH':
        return "not a mesh"
    if obj.library:
        return "linked object"
    if obj.data.library:
        return "already offloaded"
    if obj.mode != 'OBJECT':
        return "leave Edit / Sculpt Mode first"
    if not bpy.data.filepath:
        return "save the .blend first"
    if obj.data.shape_keys:
        return "has shape keys"
    if len(_users(obj.data)) > 1:
        return "the mesh is shared by several objects"
    return ""


def offload(obj, scene):
    """The full mesh into its .blend, linked back in its place. Returns the file path."""
    why = can_offload(obj)
    if why:
        raise RuntimeError(f"{obj.name}: {why}")
    local = obj.data
    name = local.name
    sig = _sig(local)
    rec = record(obj) or {}
    path = bpy.path.abspath(rec["path"]) if rec.get("path") else \
        os.path.join(folder_for(scene, obj), obj.name + ".blend")
    same = rec.get("sig") == sig and rec.get("mesh") == name and os.path.isfile(path)
    if not same:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path):        # an edited original: the old file is kept
            prev = os.path.join(os.path.dirname(path), PREVIOUS)
            os.makedirs(prev, exist_ok=True)
            shutil.move(path, os.path.join(prev, os.path.basename(path)))
        local.name = name + "_mcp_offload"
        copy = local.copy()
        try:
            copy.name = name
            for i in range(len(copy.materials)):
                copy.materials[i] = None        # the object keeps the materials
            tmp = path + ".tmp"
            bpy.data.libraries.write(tmp, {copy}, fake_user=True, compress=True)
            os.replace(tmp, path)
        finally:
            bpy.data.meshes.remove(copy)
            local.name = name
    # link it back under the same name (another namespace than the local mesh)
    lib_path = _rel(path)
    with bpy.data.libraries.load(lib_path, link=True) as (src, dst):
        if name not in src.meshes:
            raise RuntimeError(f"{os.path.basename(path)} has no mesh '{name}'")
        dst.meshes = [name]
    linked = dst.meshes[0]
    if linked is None or (len(linked.vertices), len(linked.polygons)) != \
            (len(local.vertices), len(local.polygons)):
        raise RuntimeError(f"{obj.name}: the written mesh does not match - kept local")
    _slots_to(obj, 'OBJECT')
    obj.data = linked
    bpy.data.meshes.remove(local)
    obj[KEY] = json.dumps({"path": lib_path, "mesh": name, "verts": len(linked.vertices),
                           "faces": len(linked.polygons), "sig": sig})
    return path


def load(obj):
    """The linked mesh made local again (editable, written with the main file)."""
    if not is_offloaded(obj):
        return False
    linked = obj.data
    lib = linked.library
    local = linked.make_local()
    if local is None:
        local = linked
    obj.data = local
    _slots_to(obj, 'DATA')
    if lib is not None and not lib.users_id:
        bpy.data.libraries.remove(lib)
    return True


def ensure_loaded(obj):
    """Before the add-on changes or writes the original. Returns the objects loaded."""
    done = []
    for o in {obj, original_of(obj)}:
        if o is not None and is_offloaded(o) and load(o):
            done.append(o)
    return done


def offload_quiet(objs, scene):
    """Offload again after a bake / Send Out; a failure only prints."""
    for o in objs:
        try:
            if o is not None and not is_offloaded(o) and o.get(KEY) is not None:
                offload(o, scene)
        except Exception as e:
            print(f"[MultiCamProject] {o.name}: offload skipped: {e}")


def candidates(scene):
    """Every original that could be offloaded: the Bake Sources and the scans of OBJECTS /
    Original Mesh / EXCLUDED collections."""
    from ... import roles
    out = {s for o in bpy.data.objects if o.type == 'MESH'
           for s in [_wf().bake_source_of(o)] if s is not None}
    for c in bpy.data.collections:
        if c.name in roles.ROLES['OBJECTS'][3] + roles.ROLES['ORIGINALS'][3] \
                or "EXCLUDED" in c.name.upper():
            out |= {o for o in c.all_objects if original_of(o) is o}
    return sorted(out, key=lambda o: o.name)


# ---------------------------------------------------------------- operators

class MULTICAMPROJECT_OT_OriginalToggle(bpy.types.Operator):
    """The original (Bake Source or scan) of this object: offload it into its own .blend
    (linked back, still full res; the main file no longer saves it) or load it back"""
    bl_idname = "multicamproject.original_toggle"
    bl_label = "Load / Offload Original"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        obj = bpy.data.objects.get(props.object_name)
        orig = original_of(obj)
        if orig is None:
            return "No original"
        if is_offloaded(orig):
            return f"Load {orig.name} back into this file (editable, saved with it)"
        return (f"Offload {orig.name} into {os.path.basename(folder_for(context.scene, orig))}"
                f"/{orig.name}.blend (linked back, still full res)")

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        orig = original_of(obj)
        if orig is None:
            self.report({'WARNING'}, f"{self.object_name}: no original")
            return {'CANCELLED'}
        try:
            if is_offloaded(orig):
                load(orig)
                self.report({'INFO'}, f"{orig.name}: loaded back")
            else:
                path = offload(orig, context.scene)
                self.report({'INFO'}, f"{orig.name} -> {path}")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class MULTICAMPROJECT_OT_OriginalsAll(bpy.types.Operator):
    """Offload every original (Bake Sources, OBJECTS / Original Mesh / EXCLUDED scans) into
    02.OBJECTS / zz.EXCLUDED next to the bake folder - or load them all back"""
    bl_idname = "multicamproject.originals_all"
    bl_label = "Offload / Load All Originals"
    bl_options = {'REGISTER', 'UNDO'}

    load: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        done, failed = [], []
        for o in candidates(context.scene):
            try:
                if self.load:
                    if load(o):
                        done.append(o.name)
                elif not is_offloaded(o):
                    offload(o, context.scene)
                    done.append(o.name)
            except Exception as e:
                failed.append(f"{o.name}: {e}")
        for f in failed:
            self.report({'WARNING'}, f)
        self.report({'INFO'}, f"{'Loaded' if self.load else 'Offloaded'} {len(done)} original(s)"
                              + (f", {len(failed)} failed" if failed else ""))
        return {'FINISHED'}


def draw_toggle(layout, obj):
    """One button: the original's state (linked = offloaded, mesh = in the file)."""
    orig = original_of(obj)
    cell = layout.row(align=True)
    cell.ui_units_x = 1.1
    if orig is None:
        cell.label(text="", icon='BLANK1')
        return
    off = is_offloaded(orig)
    cell.operator(MULTICAMPROJECT_OT_OriginalToggle.bl_idname, text="",
                  icon='LINKED' if off else 'MESH_ICOSPHERE',
                  depress=not off).object_name = obj.name


def draw_all(layout, context):
    row = layout.row(align=True)
    row.operator(MULTICAMPROJECT_OT_OriginalsAll.bl_idname, text="Offload All Originals",
                 icon='LINKED').load = False
    row.operator(MULTICAMPROJECT_OT_OriginalsAll.bl_idname, text="Load All",
                 icon='MESH_ICOSPHERE').load = True


_classes = (MULTICAMPROJECT_OT_OriginalToggle, MULTICAMPROJECT_OT_OriginalsAll)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
