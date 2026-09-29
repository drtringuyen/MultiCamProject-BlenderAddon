"""Material sync: every projection / baked / EXPORT mesh keeps its own two materials,
named after it, in its first two slots:

    slot 1  MCP_<name>   the projection (camera_project; only on projection objects)
    slot 2  MAT_<name>   the baked result (empty until the first bake)
    slot 3+ the scan materials

Ownership comes from the object's pointers (camera_project.core, "ownership"), never from
names, so:
  - rename: the materials follow the object's name (msgbus on Object.name, any object)
  - Shift+D: the copy gets its own MCP_ (a copy) and lets go of the original's bake
  - delete: MCP_ / MAT_ / ALB_ / NOR_ no object owns any more are removed from the file;
    the ALB_ / NOR_ files go to the Recycle Bin at the next save, and only if no image
    uses them then (Ctrl+Z before the save brings everything back).
"""
import os

import bpy
from bpy.app.handlers import persistent

from ..camera_project import core as cp
from . import common, fingerprint, gn_final, material

_owner = object()
_DELAY = 0.3
_known = set()          # as_pointer() of the objects seen by the last pass
_count = [-1]
_pending_files = set()  # ALB_/NOR_ files of removed objects, recycled at the next save
_baseline = set()       # materials already orphaned when the file loaded: left alone
OWN_FILES = ("ALB_", "NOR_")


def _cp_on():
    return hasattr(bpy.types.Object, "multicamproject_cam")


def is_projection(obj):
    return (_cp_on() and cp.data(obj).is_setup and cp.get_modifier(obj) is not None)


def in_scope(obj, originals=None, export=None):
    """Meshes that keep MCP_/MAT_: projection objects, baked objects and EXPORT meshes -
    not Remesh originals, library objects or deleted objects."""
    if obj.type != 'MESH' or obj.library or not obj.users_collection:
        return False
    if obj in (cp._originals() if originals is None else originals):
        return False
    if is_projection(obj) or cp.mat_pointer(obj) is not None or common.data(obj).alb_image:
        return True
    coll = common.export_collection(bpy.context.scene) if export is None else export
    return coll is not None and coll in obj.users_collection


# ---------------------------------------------------------------- one object

def ensure_mat(obj):
    """obj's own MAT_, named after it - made (empty before a bake) when missing. A
    duplicate first lets go of the original's bake. Slots are not touched here."""
    own = material.find(obj)
    if own is None:
        if cp.mat_pointer(obj) is not None:
            material.release(obj)
        return material.build(obj, bpy.context.scene, place_slots=False)
    cp.claim_name(own, common.mat_name(obj), bpy.data.materials)
    common.data(obj).material = own
    return own


def _state(obj):
    return (tuple(s.material.name if s.material else "" for s in obj.material_slots),
            common.data(obj).alb_image is not None)


def sync(obj, scene, full=False):
    """Bring obj's MCP_ / MAT_ in line: own, named after it, slots 1-2, wired into
    GN-CameraProject and GN-Final. `full` (the Refresh button) also rebuilds both
    materials' nodes and rewires the cameras. Returns what changed, as text."""
    d = common.data(obj)
    fresh = bool(d.fingerprint) and not fingerprint.is_outdated(obj)
    before = _state(obj)
    out = []
    if is_projection(obj):
        # a copy (Shift+D) runs the original's wrapper group: setting its inputs would
        # change the original too - it gets its own, with the cameras and drivers rewired
        from ..camera_project import wrapper
        ng = cp.get_modifier(obj).node_group
        shared = ng is not None and (not wrapper.is_wrapper(ng) or ng.users > 1)
        if full:
            material.build(obj, scene, place_slots=False)
        mat = cp.place_material(obj)        # MCP_ + MAT_ (through the hook) + scan
        if full or shared:
            if full:
                cp.build_material(obj)
            cp.apply_slots(obj, scene)
            if shared:
                out.append("own GN-CameraProject wrapper (was the original's)")
        else:
            mod = cp.get_modifier(obj)
            if cp.get_input(mod, "Material") != mat:
                cp.set_input(mod, "Material", mat)
                out.append(f"GN-CameraProject material -> {mat.name}")
    else:
        mat = material.build(obj, scene, place_slots=False) if full else ensure_mat(obj)
        cp.arrange_slots(obj, [mat])
    if gn_final.get_modifier(obj) is not None:
        mod = gn_final.ensure_modifier(obj)     # its own wrapper (a copy shares one)
        if full:
            gn_final.write_inputs(obj, scene)
        elif cp.get_input(mod, "Baked Material") != d.material:
            cp.set_input(mod, "Baked Material", d.material)
            out.append(f"GN-Final material -> {d.material.name if d.material else 'none'}")
    after = _state(obj)
    if before[1] and not after[1]:
        out.append("shared the original's bake (a copy?) - it needs its own bake")
    if before[0] != after[0]:
        out.append("slots: " + ", ".join(after[0][:3]) + (" ..." if len(after[0]) > 3 else ""))
    if fresh and d.fingerprint and fingerprint.compute(obj) != d.fingerprint:
        d.fingerprint = fingerprint.compute(obj)      # same bake, slots moved: still fresh
    return out


def problems(obj):
    """[(kind, text)] of obj's materials - kind 'MCP', 'MAT' or 'SLOTS'. Reads only."""
    out = []
    head = []
    if is_projection(obj):
        mcp = cp.own_material(obj)
        want = cp.material_name(obj)
        if mcp is None:
            ptr = cp.mcp_pointer(obj)
            out.append(('MCP', f"uses {ptr.name} of another object" if ptr else "MCP_ missing"))
        else:
            head.append(mcp)
            if mcp.name != want:
                out.append(('MCP', f"'{mcp.name}' should be '{want}'"))
            if cp.mcp_pointer(obj) != mcp:
                out.append(('MCP', f"{mcp.name} is not linked to the object"))
            mod_mat = cp.get_input(cp.get_modifier(obj), "Material")
            if mod_mat != mcp:
                out.append(('MCP', f"GN-CameraProject uses {mod_mat.name if mod_mat else 'none'}"))
    mat = material.find(obj)
    want = common.mat_name(obj)
    if mat is None:
        ptr = cp.mat_pointer(obj)
        out.append(('MAT', f"uses {ptr.name} of another object" if ptr else "MAT_ missing"))
    else:
        head.append(mat)
        if mat.name != want:
            out.append(('MAT', f"'{mat.name}' should be '{want}'"))
        if common.data(obj).material != mat:
            out.append(('MAT', f"{mat.name} is not linked to the object"))
        mod = gn_final.get_modifier(obj)
        if mod is not None and cp.get_input(mod, "Baked Material") != mat:
            out.append(('MAT', "GN-Final does not use it"))
    slots = [s.material for s in obj.material_slots]
    if head and slots[:len(head)] != head:
        names = [m.name for m in head]
        out.append(('SLOTS', f"slots should start with {' + '.join(names)}"))
    foreign = [m.name for m in slots[len(head):] if m is not None and cp._is_ours(m)]
    if foreign:
        out.append(('SLOTS', f"other objects' materials in the slots: {', '.join(foreign)}"))
    if (out and any(k == 'SLOTS' for k, _t in out)) and cp.shared_mesh(obj):
        out.append(('SLOTS', "mesh shared by several objects - its slots cannot be fixed"))
    return out


# ---------------------------------------------------------------- the whole file

def sync_names():
    """Rename pass: every owned MCP_ / MAT_ gets its owner's name. Cheap."""
    changed = []
    pairs = [(cp.owners(cp.mat_pointer, cp.baked_name), common.mat_name)]
    if _cp_on():
        pairs.insert(0, (cp.owners(cp.mcp_pointer, cp.material_name), cp.material_name))
    for own, name_fn in pairs:
        for mat, obj in own.items():
            if mat.library:
                continue
            want = name_fn(obj)
            if mat.name != want:
                old = mat.name
                cp.claim_name(mat, want, bpy.data.materials)
                changed.append(f"{old} -> {mat.name}")
    return changed


def _duplicates(objs):
    """Objects pointing at an MCP_ / MAT_ another object owns (Shift+D copies)."""
    mcp_own = cp.owners(cp.mcp_pointer, cp.material_name) if _cp_on() else {}
    mat_own = cp.owners(cp.mat_pointer, cp.baked_name)
    out = []
    for o in objs:
        m = cp.mcp_pointer(o) if _cp_on() else None
        b = cp.mat_pointer(o)
        if (m is not None and mcp_own.get(m) not in (None, o)) \
                or (b is not None and mat_own.get(b) not in (None, o)):
            out.append(o)
    return out


def _live_slot_materials():
    return {s.material for o in bpy.data.objects if o.users_collection
            for s in o.material_slots if s.material}


def orphans():
    """MCP_ / MAT_ no object owns and no live object has in a slot."""
    owned = set(cp.owners(cp.mat_pointer, cp.baked_name))
    tags = [cp.BAKED_TAG]
    if _cp_on():            # camera_project off: its pointers are gone, keep every MCP_
        owned |= set(cp.owners(cp.mcp_pointer, cp.material_name))
        tags.append(cp.MAT_TAG)
    live = _live_slot_materials()
    return [m for m in bpy.data.materials
            if not m.library and any(m.get(t) for t in tags) and m not in owned and m not in live]


def remove_orphans(everything=False):
    """Remove orphaned MCP_ / MAT_ and the ALB_ / NOR_ images only they used. Materials
    already orphaned when the file loaded stay unless `everything` (the Refresh button).
    Files are queued for the Recycle Bin at the next save - only those of objects deleted
    in this session. Returns the removed names."""
    from ..camera_project import wrapper
    found = orphans()
    mats = found if everything else [m for m in found if m.name not in _baseline]
    if not mats:
        return []
    queue = {m for m in mats if m.name not in _baseline}
    images = {(n.image, m in queue) for m in mats if m.get(cp.BAKED_TAG) and m.node_tree
              for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image
              and n.image.name.startswith(OWN_FILES)}
    # wrappers of deleted objects' modifiers hold MCP_ / MAT_ as node defaults
    spare = [ng for ng in bpy.data.node_groups
             if ng.users == 0 and wrapper.WRAP_KEY in ng and not ng.library]
    removed = [m.name for m in mats]
    bpy.data.batch_remove(spare + mats)
    gone = []
    for img, files in images:
        if img.users == 0 and img not in gone:
            path = common.image_file(img)
            if files and path and os.path.basename(path).startswith(OWN_FILES):
                _pending_files.add(os.path.normcase(os.path.abspath(path)))
            gone.append(img)
    _baseline.difference_update(removed)
    removed += [i.name for i in gone]
    bpy.data.batch_remove(gone)
    return removed


def sync_all(scene, log=print):
    """Duplicates, names and slots of every object in scope (on load, Refresh all)."""
    originals = cp._originals()
    export = common.export_collection(scene)
    objs = [o for o in bpy.data.objects if in_scope(o, originals, export)]
    for o in _duplicates(objs):
        for t in sync(o, scene):
            log(f"{o.name}: {t}")
    for t in sync_names():
        log(f"Material renamed {t}")
    for o in objs:
        for t in sync(o, scene):
            log(f"{o.name}: {t}")


# ---------------------------------------------------------------- handlers

def _msg(text):
    print(f"[MultiCamProject] {text}")


def _ready():
    ctx = bpy.context
    return ctx.mode == 'OBJECT' and not getattr(ctx.window_manager, "is_interface_locked", False)


def _objects_changed():
    """After objects were added or deleted: duplicates get their own materials, what a
    deleted object owned goes. Runs from a timer, never inside the depsgraph handler."""
    try:
        if not _ready():
            return 0.5          # try again once back in Object Mode
        scene = bpy.context.scene
        now = {o.as_pointer(): o for o in bpy.data.objects}
        new = [o for p, o in now.items() if p not in _known]
        _known.clear()
        _known.update(now)
        _count[0] = len(bpy.data.objects)
        originals = cp._originals()
        export = common.export_collection(scene)
        for o in _duplicates([o for o in new if in_scope(o, originals, export)]):
            for t in sync(o, scene):
                _msg(f"{o.name}: {t}")
        for n in remove_orphans():
            _msg(f"Removed {n} (its object is gone)")
        from . import cache
        cache.clear()
    except Exception as e:      # never break Blender over a clean-up
        _msg(f"material sync skipped: {e}")
    return None


def _names_changed():
    try:
        for t in sync_names():
            _msg(f"Material renamed {t}")
    except Exception as e:
        _msg(f"material rename skipped: {e}")
    return None


def _schedule(fn):
    if bpy.app.timers.is_registered(fn):
        bpy.app.timers.unregister(fn)
    bpy.app.timers.register(fn, first_interval=_DELAY)


@persistent
def _on_depsgraph(scene, depsgraph):
    if len(bpy.data.objects) != _count[0]:
        _count[0] = len(bpy.data.objects)
        _schedule(_objects_changed)


@persistent
def _on_save(_):
    """Recycle the queued ALB_/NOR_ files no image of the saved file uses."""
    if not _pending_files:
        return
    try:
        from ..export import checks, fixes
        used = checks.used_files()
        paths = [p for p in _pending_files if p not in used]
        for p in fixes.to_recycle_bin(paths):
            _msg(f"{os.path.basename(p)} moved to the Recycle Bin (its object was deleted)")
        _pending_files.clear()
    except Exception as e:
        _msg(f"texture clean-up skipped: {e}")


def _initial():
    """On register / load: the file's objects as the known set, then a full pass."""
    try:
        if not _ready():
            return 0.5
        _known.clear()
        _known.update(o.as_pointer() for o in bpy.data.objects)
        _count[0] = len(bpy.data.objects)
        sync_all(bpy.context.scene, _msg)
        _baseline.clear()
        _baseline.update(m.name for m in orphans())
    except Exception as e:
        _msg(f"material sync skipped: {e}")
    return None


def _subscribe():
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "name"), owner=_owner, args=(),
                             notify=lambda: _schedule(_names_changed), options={'PERSISTENT'})


@persistent
def _on_load(_):
    _pending_files.clear()      # another file: its deletions are not ours
    _subscribe()
    _schedule(_initial)


def register():
    cp.BAKED_HOOK = ensure_mat
    _subscribe()
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.save_post.append(_on_save)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_initial, first_interval=0.5)


def unregister():
    cp.BAKED_HOOK = None
    bpy.msgbus.clear_by_owner(_owner)
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.save_post, _on_save),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
    for fn in (_objects_changed, _names_changed, _initial):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
