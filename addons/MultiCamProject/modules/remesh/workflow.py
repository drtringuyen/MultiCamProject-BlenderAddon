"""The Remesh copy: the scan is duplicated into a working copy that takes over its name,
its EXPORT membership and its MCP_/MAT_/ALB_/NOR_, and gets the remesh modifier stack.
The original is renamed <name>_original, moved to "Original Mesh", loses its GN modifiers
and becomes the copy's high-poly source for Normal "Bake from mesh".

Stack of the copy:
  GN-Remesh (empty, the user's) -> Decimate Overall -> Decimate Selective (vg_HighRes)
  -> GN-CameraProject -> GN-Final
"""
import bpy
import numpy as np

from ... import module_manager
from ..camera_project import core as cp

ORIGINAL_SUFFIX = "_original"
ORIGINAL_COLLECTION = "Original Mesh"
GN_REMESH = "GN-Remesh"
DEC_OVERALL = "Decimate Overall"
DEC_SELECTIVE = "Decimate Selective"
VG_HIGHRES = "vg_HighRes"
VG_DELETE = "vg_toDelete"
VG_SEPARATE = "vg_toSeparate"
ATTR_DELETE = "remesh_delete"
ATTR_DETACH = "remesh_detach"
DECIMATE_RATIO = 0.5


def source_of(obj):
    """The original a Remesh copy was made from (None for anything else)."""
    if obj is None or obj.type != 'MESH' or not hasattr(obj, "multicamproject_bake"):
        return None
    return obj.multicamproject_bake.source


def is_copy(obj):
    return source_of(obj) is not None


def is_original(obj):
    return obj is not None and any(source_of(o) == obj for o in bpy.data.objects)


# ---------------------------------------------------------------- GN-Remesh (user owned)

def ensure_gn_remesh_group():
    """GN-Remesh is the user's: created empty (Geometry in -> out) when missing, never
    rebuilt. The Set Faces marks reach it as vg_HighRes / vg_toDelete / vg_toSeparate,
    remesh_delete, remesh_detach and face_set (the Sculpt face sets)."""
    ng = bpy.data.node_groups.get(GN_REMESH)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GN_REMESH, "GeometryNodeTree")
    ng.is_modifier = True
    ng.description = ("Yours to build. Face attributes: remesh_delete, remesh_detach, face_set; "
                      "vertex groups vg_HighRes, vg_toDelete, vg_toSeparate")
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-200, 0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (200, 0)
    ng.links.new(gi.outputs[0], go.inputs[0])
    return ng


def _gn_remesh_modifier(obj):
    return next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group
                 and m.node_group.name == GN_REMESH), None)


def _move(obj, mod, index):
    if list(obj.modifiers).index(mod) != index:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=index)


def ensure_stack(obj):
    """GN-Remesh, Decimate Overall, Decimate Selective at the top of the stack, in that
    order (GN-CameraProject and GN-Final follow)."""
    for name in (VG_HIGHRES, VG_DELETE, VG_SEPARATE):
        if obj.vertex_groups.get(name) is None:
            obj.vertex_groups.new(name=name)
    mod = _gn_remesh_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(GN_REMESH, 'NODES')
        mod.node_group = ensure_gn_remesh_group()
        # the v2 face set picker (GN-Remesh built by the add-on): show everything
        try:
            cp.set_input(mod, "Isolate", False)
        except (KeyError, AttributeError, TypeError):
            pass
    _move(obj, mod, 0)

    for index, name, group in ((1, DEC_OVERALL, ""), (2, DEC_SELECTIVE, VG_HIGHRES)):
        dec = obj.modifiers.get(name)
        if dec is None or dec.type != 'DECIMATE':
            dec = obj.modifiers.new(name, 'DECIMATE')
            dec.decimate_type = 'COLLAPSE'
            dec.ratio = DECIMATE_RATIO
            dec.vertex_group = group
            dec.invert_vertex_group = False     # weight 1 = decimated further (as decided)
        _move(obj, dec, index)


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
    """The original lets go of MCP_ / MAT_ / ALB_ / NOR_: they belong to the copy now (the
    copy takes the original's name). Its slots keep MCP_ (no face uses it)."""
    if hasattr(obj, "multicamproject_cam"):
        obj.multicamproject_cam.material = None
    d = obj.multicamproject_bake
    d.material = d.alb_image = d.nor_image = None
    d.fingerprint = ""
    d.alb_size = d.nor_size = 0


def repair_originals():
    """On load: the originals made before these repairs existed."""
    sources = {o.multicamproject_bake.source for o in bpy.data.objects
               if o.type == 'MESH' and hasattr(o, "multicamproject_bake")}
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
    copy.multicamproject_bake.source = obj
    copy.multicamproject_bake.bake_source = obj

    for o in context.selected_objects:
        o.select_set(False)
    context.view_layer.objects.active = copy
    copy.select_set(True)
    obj.hide_set(True)

    ensure_stack(copy)
    if module_manager.is_loaded("camera_project"):
        warnings += cp.setup(copy, scene)
    if module_manager.is_loaded("baking"):
        from ..baking import gn_final
        if gn_final.get_modifier(copy) is not None:
            gn_final.ensure_modifier(copy)      # its own wrapper, last in the stack
    return copy, warnings
