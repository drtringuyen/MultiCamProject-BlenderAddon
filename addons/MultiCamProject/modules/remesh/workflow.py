"""The Remesh copy (0C): the scan is duplicated into a working copy that takes over its
name, its EXPORT membership and its MCP_/MAT_/ALB_/NOR_, and gets the Decimate. The
original is renamed <name>_original, moved to "Original Mesh", loses its GN modifiers and
becomes the copy's Bake Source (BA_ / BN_, 04 Bake from Source).

Stack of the copy:
  Decimate (vg_Protect: weight 1 = kept as is) -> [Snap: Shrinkwrap on vg_Snap, off]
  -> GN-CameraProject -> GN-Final
The Decimate is applied (03) before uv_normal is unwrapped: it collapses across UV seams.
"""
import bpy
import numpy as np

from ... import module_manager
from ..camera_project import core as cp

ORIGINAL_SUFFIX = "_original"
ORIGINAL_COLLECTION = "Original Mesh"
DECIMATE = "Decimate"
SNAP = "Snap to Source"
VG_PROTECT = "vg_Protect"
VG_SNAP = "vg_Snap"
DECIMATE_RATIO = 0.5
# the v3 stack, replaced on load (the GN-Remesh group itself is the user's: never deleted)
OLD_GN_REMESH = "GN-Remesh"
OLD_DECIMATES = ("Decimate Overall", "Decimate Selective")


def source_of(obj):
    """The original a Remesh copy was made from (None for anything else)."""
    if obj is None or obj.type != 'MESH' or not hasattr(obj, "multicamproject_bake"):
        return None
    return obj.multicamproject_bake.source


def bake_source_of(obj):
    """The high poly obj bakes BA_ / BN_ from (a Remesh original or a picked mesh)."""
    if obj is None or obj.type != 'MESH' or not hasattr(obj, "multicamproject_bake"):
        return None
    return obj.multicamproject_bake.bake_source


def is_copy(obj):
    return source_of(obj) is not None


def is_original(obj):
    return obj is not None and any(source_of(o) == obj for o in bpy.data.objects)


def is_low_poly(obj):
    """A Remesh copy or a mesh with a picked Bake Source: Cutting & Modelling applies."""
    return bake_source_of(obj) is not None or is_copy(obj)


# ---------------------------------------------------------------- the stack

def _move(obj, mod, index):
    if list(obj.modifiers).index(mod) != index:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=index)


def decimate_modifier(obj):
    mod = obj.modifiers.get(DECIMATE)
    return mod if mod is not None and mod.type == 'DECIMATE' else None


def snap_modifier(obj):
    mod = obj.modifiers.get(SNAP)
    return mod if mod is not None and mod.type == 'SHRINKWRAP' else None


def _drop_v3_stack(obj):
    """GN-Remesh + Decimate Overall / Selective (v3) -> the ratio of Decimate Overall."""
    ratio = None
    for m in list(obj.modifiers):
        if m.type == 'NODES' and m.node_group and m.node_group.name == OLD_GN_REMESH:
            obj.modifiers.remove(m)
        elif m.type == 'DECIMATE' and m.name in OLD_DECIMATES:
            if m.name == OLD_DECIMATES[0]:
                ratio = m.ratio
            obj.modifiers.remove(m)
    return ratio


def ensure_stack(obj, applied_ok=True):
    """The Decimate first in the stack (vg_Protect inverted: protected parts stay). With
    `applied_ok` a copy whose Decimate was applied (03) gets no new one."""
    if obj.vertex_groups.get(VG_PROTECT) is None:
        obj.vertex_groups.new(name=VG_PROTECT)
    ratio = _drop_v3_stack(obj)
    dec = decimate_modifier(obj)
    if dec is None:
        if applied_ok and obj.get(APPLIED_KEY):
            return None
        dec = obj.modifiers.new(DECIMATE, 'DECIMATE')
        dec.decimate_type = 'COLLAPSE'
        dec.ratio = DECIMATE_RATIO if ratio is None else ratio
        dec.vertex_group = VG_PROTECT
        dec.invert_vertex_group = True      # weight 1 = protected = not decimated
    _move(obj, dec, 0)
    return dec


APPLIED_KEY = "multicamproject_decimated"   # on the object: the Decimate was applied (03)


def clear_custom_normals(obj):
    """A low poly keeps no custom normals: the scan's, carried through Decimate / Dyntopo,
    point anywhere - Cycles shades and aims the Bake from Source rays with them. Object
    Mode. Returns True when there were some."""
    if obj.type != 'MESH' or not obj.data.has_custom_normals or obj.mode != 'OBJECT':
        return False
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    return not obj.data.has_custom_normals


def apply_decimate(context, obj):
    """03: apply the Decimate (the stack before it is empty). Returns (faces before, after)."""
    dec = decimate_modifier(obj)
    if dec is None:
        raise RuntimeError("No Decimate modifier")
    if obj.data.shape_keys:
        raise RuntimeError("The mesh has shape keys - a modifier cannot be applied")
    if sum(o.data == obj.data for o in bpy.data.objects) > 1:
        raise RuntimeError("The mesh is shared by several objects")
    _move(obj, dec, 0)
    before = len(obj.data.polygons)
    with context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_apply(modifier=dec.name)
    obj[APPLIED_KEY] = True
    clear_custom_normals(obj)
    return before, len(obj.data.polygons)


def set_snap(obj, on):
    """Optional Shrinkwrap onto the Bake Source, limited to vg_Snap, right after the Decimate."""
    mod = snap_modifier(obj)
    src = bake_source_of(obj)
    if not on:
        if mod is not None:
            obj.modifiers.remove(mod)
        return None
    if src is None:
        raise RuntimeError("No Bake Source to snap to")
    if obj.vertex_groups.get(VG_SNAP) is None:
        obj.vertex_groups.new(name=VG_SNAP)
    if mod is None:
        mod = obj.modifiers.new(SNAP, 'SHRINKWRAP')
        mod.wrap_method = 'NEAREST_SURFACEPOINT'
        mod.vertex_group = VG_SNAP
    mod.target = src
    dec = decimate_modifier(obj)
    _move(obj, mod, 1 if dec is not None else 0)
    return mod


def use_existing(context, low, high):
    """'Use existing high poly': a low poly made outside the add-on bakes from `high`."""
    if low == high or high.type != 'MESH':
        raise RuntimeError("Pick another mesh as the high poly")
    low.multicamproject_bake.bake_source = high
    clear_custom_normals(low)
    if low.vertex_groups.get(VG_PROTECT) is None:
        low.vertex_groups.new(name=VG_PROTECT)
    try:
        from ..baking import matsync
        matsync.sync(low, context.scene)        # the scan slots go (it has a source now)
    except ImportError:
        pass


# ---------------------------------------------------------------- the Remesh button

def _original_collection(scene):
    coll = bpy.data.collections.get(ORIGINAL_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(ORIGINAL_COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def _remove_gn(obj):
    """Every GN modifier off the original, with the drivers that pointed at them."""
    ad = obj.animation_data
    for mod in [m for m in obj.modifiers if m.type == 'NODES']:
        if ad:
            prefix = f'modifiers["{mod.name}"]'
            for fc in [fc for fc in ad.drivers if fc.data_path.startswith(prefix)]:
                ad.drivers.remove(fc)
        obj.modifiers.remove(mod)


def _restore_uvs(obj):
    """Final makes uv_normal the active + render UV and remembers the previous ones; GN-Final
    is gone from the original, so nothing would switch it back - and the scan textures
    (no UV Map node) would then read uv_normal. Put the remembered UVs back."""
    d = getattr(obj, "multicamproject_bake", None)
    if d is None or not (d.prev_uv_active or d.prev_uv_render):
        return
    uvs = obj.data.uv_layers
    if uvs.get(d.prev_uv_active):
        uvs.active = uvs[d.prev_uv_active]
    if uvs.get(d.prev_uv_render):
        uvs[d.prev_uv_render].active_render = True
    d.prev_uv_active = d.prev_uv_render = ""


def _free_projection_faces(obj):
    """Faces on the projection material (MCP_, drawn only through GN-CameraProject, which
    the original no longer has) go back to their scan material: uv_index is the face's slot
    from before the materials were combined. Returns the number of faces moved."""
    me = obj.data
    slots = obj.material_slots
    ours = {i for i, s in enumerate(slots) if s.material and s.material.get(cp.MAT_TAG)}
    attr = me.attributes.get(cp.UV_INDEX)
    if not ours or attr is None or attr.domain != 'FACE' or not len(me.polygons):
        return 0
    n = len(me.polygons)
    mi = np.empty(n, np.int32)
    me.polygons.foreach_get("material_index", mi)
    ui = np.empty(n, np.int32)
    attr.data.foreach_get("value", ui)
    valid = (ui >= 0) & (ui < len(slots)) & ~np.isin(ui, list(ours))
    move = np.isin(mi, list(ours)) & valid
    if not move.any():
        return 0
    mi[move] = ui[move]
    me.polygons.foreach_set("material_index", mi)
    me.update()
    return int(move.sum())


def repair_original(obj):
    """Keep a Remesh original drawable without its GN modifiers: its own UVs back (not
    Final's uv_normal) and no face on the projection material. Safe to run again."""
    if obj.type != 'MESH' or obj.library or obj.mode != 'OBJECT':
        return 0
    _restore_uvs(obj)
    return _free_projection_faces(obj)


def release_materials(obj):
    """The original lets go of MCP_ / MAT_ / ALB_ / NOR_ / BA_ / BN_: they belong to the copy
    now (the copy takes the original's name). make_copy then drops them from its slots."""
    if hasattr(obj, "multicamproject_cam"):
        obj.multicamproject_cam.material = None
    d = obj.multicamproject_bake
    d.material = d.alb_image = d.nor_image = d.ba_image = d.bn_image = None
    d.fingerprint = d.ba_fingerprint = ""
    d.alb_size = d.nor_size = d.ba_size = 0


def repair_originals():
    """On load: the originals made before these repairs existed."""
    sources = {o.multicamproject_bake.source for o in bpy.data.objects
               if o.type == 'MESH' and hasattr(o, "multicamproject_bake")}
    for o in bpy.data.objects:          # the v3 stack (GN-Remesh + two Decimates) -> one Decimate
        if (o.type == 'MESH' and not o.library and is_copy(o) and o.mode == 'OBJECT'
                and any(m.name in OLD_DECIMATES for m in o.modifiers)):
            ensure_stack(o)
            print(f"[MultiCamProject] '{o.name}': Remesh stack -> one Decimate (vg_Protect)")
    for obj in sources:
        if obj is not None and not any(m.type == 'NODES' for m in obj.modifiers):
            repair_original(obj)
        if obj is not None and not obj.library:
            release_materials(obj)


def _export_collection(scene):
    try:
        from ..export import fixes
    except ImportError:
        return None
    return fixes.ensure_export_collection(scene)


def make_copy(context, obj):
    """The Remesh button. Returns (copy, warnings). Object Mode only."""
    scene = context.scene
    warnings = []
    name, mesh_name = obj.name, obj.data.name
    was_setup = hasattr(obj, "multicamproject_cam") and obj.multicamproject_cam.is_setup
    collections = list(obj.users_collection)

    copy = obj.copy()                   # a full copy: a cut must never touch the original
    copy.data = obj.data.copy()
    # plain renames (not export's rename_object): MCP_/MAT_/ALB_/NOR_ keep their names and
    # belong to the copy, which takes the original name
    obj.name = name + ORIGINAL_SUFFIX
    obj.data.name = mesh_name + ORIGINAL_SUFFIX
    copy.name = name
    copy.data.name = mesh_name

    for coll in collections:
        coll.objects.link(copy)
    export = _export_collection(scene)
    if export is not None and export not in copy.users_collection:
        export.objects.link(copy)
    orig_coll = _original_collection(scene)
    orig_coll.objects.link(obj)
    for coll in collections:
        coll.objects.unlink(obj)

    _remove_gn(obj)                     # before the copy's setup: its wrappers are free again
    repair_original(obj)                # its own UVs, no face on the projection material
    if hasattr(obj, "multicamproject_cam"):
        obj.multicamproject_cam.is_setup = False    # no longer a projection object
    release_materials(obj)
    cp.arrange_slots(obj, [])           # the original keeps only its scan materials
    copy.multicamproject_bake.source = obj
    copy.multicamproject_bake.bake_source = obj

    for o in context.selected_objects:
        o.select_set(False)
    context.view_layer.objects.active = copy
    copy.select_set(True)
    obj.hide_set(True)

    ensure_stack(copy)
    clear_custom_normals(copy)
    if module_manager.is_loaded("camera_project") and was_setup:
        warnings += cp.setup(copy, scene)       # 0B came first: the copy projects too
    if module_manager.is_loaded("baking"):
        from ..baking import gn_final
        if gn_final.get_modifier(copy) is not None:
            gn_final.ensure_modifier(copy)      # its own wrapper, last in the stack
    return copy, warnings
