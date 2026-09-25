"""The fixes the export panel offers: rename, transforms, delete a scene, EXPORT links."""
import os

import bpy
from mathutils import Matrix, Vector

from ..baking import common, engine, fingerprint, naming
from . import checks

# ---------------------------------------------------------------- rename


def planned_names(objs, sc=None):
    """{object: new name} for the objects not named right. With a naming scheme `sc`
    (naming.scheme): '<scene>.<##>_<name>' - a valid ## is kept (the first one wins a
    duplicate), the others get the next free number. Without: clean names, conflicts _2."""
    others = {o.name for o in bpy.data.objects} - {o.name for o in objs}
    final = {}
    if sc is None:
        taken = set(others)
        for obj in sorted(objs, key=lambda o: naming.clean_name(o.name) != o.name):
            base = naming.clean_name(obj.name)
            new, k = base, 2
            while new in taken:
                new, k = f"{base}_{k}", k + 1
            taken.add(new)
            final[obj] = new
    else:
        # a name from an earlier prefix keeps its number
        parsed = {o: naming.parse(o.name, sc) or naming.parse(o.name) or naming.parse_loose(o.name)
                  for o in objs}
        index, used = {}, set()
        for o in sorted((o for o in objs if parsed[o]), key=lambda o: (parsed[o][0], o.name)):
            if parsed[o][0] not in used:
                index[o] = parsed[o][0]
                used.add(parsed[o][0])
        free = 0
        for o in objs:
            short = parsed[o][1] if parsed[o] else naming.short_name(o.name)
            i = index.get(o)
            while i is None or naming.full_name(sc, i, short) in others:
                while free in used:
                    free += 1
                i = free
                used.add(i)
            final[o] = naming.full_name(sc, i, short)
    return {o: n for o, n in final.items() if n != o.name}


def _rename_file(img, new_name):
    """Rename the image and its file on disk (same folder), then point it there."""
    old_path = common.image_file(img)
    if img.name != new_name:
        img.name = new_name
    if not old_path:
        return
    new_path = os.path.join(os.path.dirname(old_path), new_name + os.path.splitext(old_path)[1])
    if os.path.normcase(new_path) == os.path.normcase(old_path):
        return
    if os.path.isfile(old_path):
        os.replace(old_path, new_path)
    if os.path.isfile(new_path):
        engine.link_file(img, new_path)


def _mcp_material(obj):
    from ..camera_project import core as cp
    d = getattr(obj, "multicamproject_cam", None)
    if d is not None and d.material is not None:
        return d.material
    return bpy.data.materials.get(cp.PREFIX + obj.name)


def rename_object(obj, new, fresh=None, mcp=None):
    """Object, mesh, MCP_, MAT_, ALB_/NOR_ (images and files) in one go. MCP_ is renamed
    right away: camera_project finds it by the object's name."""
    from ..camera_project import core as cp
    d = common.data(obj)
    if fresh is None:
        fresh = bool(d.fingerprint) and not fingerprint.is_outdated(obj)
    if mcp is None:
        mcp = _mcp_material(obj)
    if obj.name != new:         # an unchanged name is not written: no rename notification
        obj.name = new
    new = obj.name
    if obj.data.name != new and len(checks.users_of_mesh(obj.data)) == 1:
        obj.data.name = new
    if mcp and mcp.name != cp.PREFIX + new:
        mcp.name = cp.PREFIX + new
    if d.material is not None and d.material.name != common.mat_name(obj):
        d.material.name = common.mat_name(obj)
    if d.alb_image is not None:
        _rename_file(d.alb_image, common.alb_name(obj))
    if d.nor_image is not None:
        _rename_file(d.nor_image, common.nor_name(obj))
    if fresh:       # same bake, new names: still up to date
        d.fingerprint = fingerprint.compute(obj)
    return new


def to_recycle_bin(paths):
    """Move files to the Recycle Bin (Windows) - a wrong clean-up can be undone there.
    Elsewhere they are deleted. Returns the paths that are gone."""
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        return []
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 0x10, 0x40, 0x400
        # pFrom: NUL-separated, ending in two NULs
        files = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
        op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=files,
                             fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI)
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    else:
        for p in paths:
            os.remove(p)
    return [p for p in paths if not os.path.exists(p)]


def _names(obj):
    d = common.data(obj)
    return (obj.name, obj.data.name, d.material.name if d.material else "",
            *(f"{i.name}|{common.image_file(i)}" for i in (d.alb_image, d.nor_image) if i))


def needs_rename(obj, mcp=None):
    """MCP_/MAT_/ALB_/NOR_ or a texture file not named after the object."""
    from ..camera_project import core as cp
    d = common.data(obj)
    if mcp is None:
        mcp = _mcp_material(obj)
    if mcp and mcp.name != cp.PREFIX + obj.name:
        return True
    if d.material is not None and d.material.name != common.mat_name(obj):
        return True
    for img, want in ((d.alb_image, common.alb_name(obj)), (d.nor_image, common.nor_name(obj))):
        if img is None:
            continue
        path = common.image_file(img)
        if img.name != want or (path and os.path.splitext(os.path.basename(path))[0] != want):
            return True
    return False


def release_shared(objs):
    """A duplicated object (Shift+D) carries the original's MAT_/ALB_/NOR_ and MCP_ pointers.
    Each of those belongs to one object - the one it is named after (else the first); the
    others let go: their bake is cleared (they get baked on their own) and their MCP_ is not
    renamed. Returns ({object: False for a released MCP_}, [(severity, text)])."""
    from ..camera_project import core as cp
    mcp_off, log = {}, []
    groups = {}
    for o in objs:
        d = common.data(o)
        for kind, idb, want in (("bake", d.material, common.mat_name(o)),
                                ("bake", d.alb_image, common.alb_name(o)),
                                ("bake", d.nor_image, common.nor_name(o)),
                                ("mcp", _mcp_material(o), cp.PREFIX + o.name)):
            if idb is not None:
                groups.setdefault((kind, idb.name, type(idb).__name__), []).append((o, want == idb.name))
    for (kind, name, _t), users in groups.items():
        if len(users) < 2:
            continue
        owner = next((o for o, named in users if named), users[0][0])
        for o, _n in users:
            if o is owner:
                continue
            if kind == "mcp":
                mcp_off[o] = False
            elif common.data(o).material is not None or common.data(o).alb_image is not None:
                d = common.data(o)
                d.material = d.alb_image = d.nor_image = None
                d.fingerprint = ""
                d.alb_size = d.nor_size = 0
                log.append(('WARNING', f"{o.name}: shared '{name}' with {owner.name} (a copy?) - "
                                       "its own bake is needed"))
    return mcp_off, log


def numbered(objs, sc):
    """The objects in ## order (unnumbered ones last, as planned)."""
    planned = planned_names(objs, sc)

    def key(o):
        p = naming.parse(planned.get(o, o.name), sc)
        return (p[0] if p else 10 ** 6, o.name)
    return sorted(objs, key=key)


def renumber_plan(ordered, sc):
    """{object: new name} numbering `ordered` 00 -> n, keeping each <name>."""
    plan = {}
    for i, o in enumerate(ordered):
        p = naming.parse(o.name, sc) or naming.parse(o.name) or naming.parse_loose(o.name)
        new = naming.full_name(sc, i, p[1] if p else naming.short_name(o.name))
        if new != o.name:
            plan[o] = new
    return plan


def renumber(objs, sc, ordered=None):
    """Number the objects 00 -> n in `ordered` (default: their current ## order)."""
    ordered = ordered or numbered(objs, sc)
    return rename_all(objs, sc, plan=renumber_plan(ordered, sc))


def move(objs, sc, obj, step):
    """Move `obj` one place up (-1) / down (+1) in the ## order, then number 00 -> n."""
    ordered = numbered(objs, sc)
    i = ordered.index(obj)
    j = max(0, min(len(ordered) - 1, i + step))
    ordered[i], ordered[j] = ordered[j], ordered[i]
    return renumber(objs, sc, ordered)


def rename_all(objs, sc=None, plan=None):
    """Rename objects (naming scheme `sc`, else clean names) and bring every MCP_/MAT_/
    ALB_/NOR_ and texture file in line with its object. `plan`: {object: name} to use
    instead of planned_names. Returns [(severity, text)]."""
    mcp_off, shared_log = release_shared(objs)
    plan = planned_names(objs, sc) if plan is None else plan
    state = {o: (o.name, _names(o), mcp_off.get(o, _mcp_material(o)),
                 bool(common.data(o).fingerprint) and not fingerprint.is_outdated(o)) for o in objs}
    # names swapped between objects: step aside first, so no name is taken twice
    if set(plan.values()) & {o.name for o in objs}:
        for obj in plan:
            rename_object(obj, f"{obj.name}__mcp_tmp", state[obj][3], state[obj][2])
    out = list(shared_log)
    for obj in objs:
        old, before, mcp, fresh = state[obj]
        if obj not in plan and not needs_rename(obj, mcp):
            continue
        try:
            rename_object(obj, plan.get(obj, obj.name), fresh, mcp)
        except OSError as e:
            out.append(('ERROR', f"{old}: rename failed - {e}"))
            continue
        if obj.name != old:
            out.append(('INFO', f"Renamed {old} -> {obj.name}"))
        elif _names(obj) != before:
            out.append(('INFO', f"{obj.name}: material / texture names and files fixed"))
    return out


# ---------------------------------------------------------------- transforms

def fix_transform(obj):
    """Apply all transforms into the mesh, then put the origin at the bottom center.
    Children keep their place. Returns '' or why it was skipped."""
    me = obj.data
    if len(checks.users_of_mesh(me)) > 1:
        return "mesh is shared by several objects"
    children = [(c, c.matrix_world.copy()) for c in obj.children]
    mw = obj.matrix_world.copy()
    me.transform(mw, shape_keys=True)
    if mw.to_3x3().determinant() < 0:
        me.flip_normals()
    obj.matrix_world = Matrix.Identity(4)
    pivot = Vector(checks._bbox_pivot(obj))
    me.transform(Matrix.Translation(-pivot), shape_keys=True)
    obj.matrix_world = Matrix.Translation(pivot)
    me.update()
    for c, m in children:
        c.matrix_world = m
    return ""


# ---------------------------------------------------------------- scenes

def _scene_colls(scene):
    out, todo = [], list(scene.collection.children)
    while todo:
        c = todo.pop()
        out.append(c)
        todo.extend(c.children)
    return out


def delete_scene(target, keep):
    """Remove `target` and the IDs only it used: its objects, their data, the cameras'
    images, its collections. Anything another scene (e.g. `keep`) still uses stays."""
    if target == keep:
        raise ValueError("The active scene cannot be deleted")
    others = [s for s in bpy.data.scenes if s != target]
    elsewhere = {o for s in others for o in s.objects}
    colls_elsewhere = {c for s in others for c in _scene_colls(s)}
    objs = [o for o in target.objects if o not in elsewhere]
    datas = {o.data for o in objs if o.data is not None}
    images = {bg.image for o in objs if o.type == 'CAMERA'
              for bg in o.data.background_images if bg.image}
    colls = [c for c in _scene_colls(target) if c not in colls_elsewhere]
    bpy.data.scenes.remove(target)
    bpy.data.batch_remove(objs + colls)

    def unused(idb):
        return idb.users == (1 if idb.use_fake_user else 0)

    datas = [x for x in datas if unused(x)]
    bpy.data.batch_remove(datas)
    images = [i for i in images if unused(i)]
    bpy.data.batch_remove(images)
    return len(objs), len(datas), len(images), len(colls)


# ---------------------------------------------------------------- EXPORT collection

def ensure_export_collection(scene):
    coll = common.export_collection(scene)
    if coll is None:
        coll = bpy.data.collections.get(common.EXPORT)
        if coll is None:
            coll = bpy.data.collections.new(common.EXPORT)
        scene.collection.children.link(coll)
    coll.color_tag = 'COLOR_03'
    return coll


def add_to_export(scene, objs):
    """Link (not move) the meshes into EXPORT. Returns (added, skipped non-meshes)."""
    coll = ensure_export_collection(scene)
    added, skipped = [], []
    for o in objs:
        if o.type != 'MESH':
            skipped.append(o.name)
        elif coll not in o.users_collection:
            coll.objects.link(o)
            added.append(o.name)
    return added, skipped


def remove_from_export(scene, objs):
    coll = common.export_collection(scene)
    if coll is None:
        return []
    out = []
    for o in objs:
        if coll in o.users_collection:
            if len(o.users_collection) == 1:    # would leave the scene: keep it at the root
                scene.collection.objects.link(o)
            coll.objects.unlink(o)
            out.append(o.name)
    return out
