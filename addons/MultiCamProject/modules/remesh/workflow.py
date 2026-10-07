"""The Remesh copy (0C): the scan is duplicated into a working copy that takes over its
name, its EXPORT membership and its MCP_/MAT_/ALB_/NOR_, and gets the Decimate. The
original is renamed <name>_original, moved to "Original Mesh", loses its GN modifiers and
becomes the copy's Bake Source (BA_ / BN_, 04 Bake from Source).

Stack of the copy:
  Decimate (vg_Protect: weight 1 = kept as is) -> [Snap: Shrinkwrap on vg_Snap, off]
  -> GN-CameraProject -> GN-Final
The Decimate is applied (03) before uv_normal is unwrapped: it collapses across UV seams.
"""
from contextlib import contextmanager

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
DECIMATE_RATIO = 0.05
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


HANDMADE_TEXT = ("Handmade - change its UVs with 0E Rebake (Remesh / Retopo would leave "
                 "nothing to bake from)")


def is_handmade(obj):
    d = getattr(obj, "multicamproject_bake", None)
    return bool(d is not None and d.handmade)


def is_low_poly(obj):
    """A Remesh copy or a mesh with a picked Bake Source: Cutting & Modelling applies."""
    return bake_source_of(obj) is not None or is_copy(obj)


# ---------------------------------------------------------------- the stack

def _move(obj, mod, index):
    """Reorder through the data API: the operator re-evaluates the scan (~6 s at 388k)."""
    cur = list(obj.modifiers).index(mod)
    if cur != index:
        obj.modifiers.move(cur, index)


@contextmanager
def quiet(obj):
    """obj's modifiers off in the viewport meanwhile: steps that update the view layer (the
    0B wrappers) then don't evaluate the scan's Decimate / GN (seconds each)."""
    shown = [(m, m.show_viewport) for m in obj.modifiers]
    for m, _on in shown:
        m.show_viewport = False
    try:
        yield
    finally:
        for m, on in shown:
            if m.name in obj.modifiers:
                m.show_viewport = on


def setup_projection(obj, scene):
    """0B on a fresh low poly, quietly (see quiet). Returns 0B's warnings."""
    with quiet(obj):
        warnings = cp.setup(obj, scene)
    try:
        from ..baking import route
        if hasattr(obj, "multicamproject_bake"):
            route.after_step(obj, 'PROJECTION')
    except ImportError:
        pass
    return warnings


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
        dec.show_viewport = True    # the first decision after 0C (decimate_pending)
    _move(obj, dec, 0)
    return dec


APPLIED_KEY = "multicamproject_decimated"   # on the object: the Decimate was applied (03)


def decimate_in_window(obj):
    """A copy from 0C's window button: no Decimate here (the window has it), not applied
    yet - its GN stay muted until Receive brings the decimated mesh."""
    return (obj is not None and obj.type == 'MESH' and is_copy(obj) and not is_retopo(obj)
            and decimate_modifier(obj) is None and not obj.get(APPLIED_KEY))


def decimate_pending(obj):
    """A Remesh copy whose Decimate is not decided yet: the object is locked (only the
    Decimate, 02 Protect and the view) and its GN modifiers are muted until Apply."""
    return (obj is not None and obj.type == 'MESH' and not is_retopo(obj)
            and is_low_poly(obj) and decimate_modifier(obj) is not None
            and not obj.get(APPLIED_KEY))


def set_gn(obj, on):
    """GN-CameraProject / GN-Final in the viewport: muted while the Decimate is decided (the
    preview costs only the Decimate), on again after Apply."""
    for m in obj.modifiers:
        if m.type == 'NODES' and m.show_viewport != on:
            m.show_viewport = on


def clear_custom_normals(obj):
    """A low poly keeps no custom normals: the scan's, carried through Decimate / Dyntopo,
    point anywhere - Cycles shades and aims the Bake from Source rays with them. Object
    Mode. Returns True when there were some."""
    if obj.type != 'MESH' or not obj.data.has_custom_normals or obj.mode != 'OBJECT':
        return False
    me = obj.data
    attr = me.attributes.get("custom_normal")      # Blender 5: an attribute - no operator,
    if attr is not None:                            # no re-evaluation (~3 s on a scan)
        me.attributes.remove(attr)
    if me.has_custom_normals:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                       selected_editable_objects=[obj]):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    return not me.has_custom_normals


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
    dec.show_viewport = True    # Blender skips a modifier hidden in the viewport
    before = len(obj.data.polygons)
    with context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_apply(modifier=dec.name)
    obj[APPLIED_KEY] = True
    clear_custom_normals(obj)
    after_decimate(obj, context.scene)
    return before, len(obj.data.polygons)


def after_decimate(obj, scene):
    """The Decimate is decided: GN-CameraProject / GN-Final on again, the cameras scored on
    the low poly (Apply here, or Receive of a window that applied it)."""
    set_gn(obj, True)
    cam = getattr(obj, "multicamproject_cam", None)
    if module_manager.is_loaded("camera_project") and cam is not None and cam.is_setup:
        try:
            cp.refresh(obj, scene)
        except Exception as e:      # never undo the Apply over a rescore
            print(f"[MultiCamProject] {obj.name}: Reload All after the Decimate skipped: {e}")


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
    """The originals' collection: picked in the Linking panel, else "Original Mesh"."""
    from ... import roles
    coll = roles.find(scene, 'ORIGINALS')
    if coll is None:
        coll = bpy.data.collections.new(ORIGINAL_COLLECTION)
    if not _in_scene(coll, scene.collection):
        scene.collection.children.link(coll)
    return coll


def _in_scene(coll, root):
    return coll == root or any(_in_scene(coll, c) for c in root.children)


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
    """The original lets go of MCP_ / MAT_ / ALB_ / NOR_ and the work textures: they belong to the copy
    now (the copy takes the original's name). make_copy then drops them from its slots."""
    if hasattr(obj, "multicamproject_cam"):
        obj.multicamproject_cam.material = None
    d = obj.multicamproject_bake
    d.material = d.alb_image = d.nor_image = d.ba_image = d.bn_image = None
    d.bap_image = d.bnp_image = d.bng_image = None
    d.fingerprint = d.ba_fingerprint = d.bp_fingerprint = d.bnp_fingerprint = ""
    d.bng_fingerprint = ""
    d.alb_size = d.nor_size = d.ba_size = d.bp_size = 0


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


# ---------------------------------------------------------------- Reset (start over)

_OUR_MODS = {DECIMATE, SNAP, OLD_GN_REMESH, "_MCP_PolyCut", "MCP_BAKE_SUBDIV", *OLD_DECIMATES}
_OUR_GROUPS = ("GN-CameraProject", "GN-CamProject", "GN-Final", "MCP", "GN-Remesh")
# mesh layers the add-on writes (UV_cam1..6 by prefix); the scan's own UVs, colors and
# custom normals stay
_OUR_ATTRS = ("uv_normal", "VCMix", "VCMix2", "uv_index", "face_set", "remesh_inside",
              "remesh_cutter", "_remesh_seam", ".sculpt_face_set")
_OUR_KEYS = (APPLIED_KEY, "multicamproject_version", "multicamproject_remesh_version",
             "multicamproject_final_version")


def reset_problem(obj):
    """Why `obj` cannot be reset ('' = it can)."""
    if obj is None or obj.type != 'MESH' or obj.library:
        return "Select a mesh"
    if obj.mode != 'OBJECT':
        return "Object Mode only"
    if obj.data.users > 1:
        return "The mesh is shared by several objects"
    return ""


def reset_object(obj):
    """Everything the add-on put on `obj` goes, so 0A/0B/0C start over without errors:
    its modifiers (projection, Final, Decimate, Snap) and their drivers, the MCP_/MAT_ slots
    (faces go back to their scan material), its projection and bake data (cameras, BA_/BN_/
    ALB_/NOR_ links, Bake Source), uv_normal, UV_camN, VCMix, face sets and the Remesh marks.
    The scan's own materials, UVs, colors and normals stay; the materials and images
    themselves are not deleted (another object may use them). Other objects that bake from
    `obj` let go of it. Returns what was removed, as text lines."""
    out = []
    me = obj.data
    # modifiers first: projection faces need the slots as they are
    for mod in list(obj.modifiers):
        group = mod.node_group.name if mod.type == 'NODES' and mod.node_group else ""
        if mod.name in _OUR_MODS or any(group.startswith(g) for g in _OUR_GROUPS):
            cp.remove_drivers(obj, mod)
            out.append(f"modifier {mod.name}")
            obj.modifiers.remove(mod)
    _restore_uvs(obj)
    n = _free_projection_faces(obj)
    if n:
        out.append(f"{n:,} faces back on their scan material")
    # every add-on material leaves the slots (keeps_scan is True once bake_source is gone)
    size = obj.multicamproject_bake.tex_size
    obj.property_unset("multicamproject_bake")
    obj.multicamproject_bake.tex_size = size
    if hasattr(obj, "multicamproject_cam"):
        obj.property_unset("multicamproject_cam")
    before = [s.material.name for s in obj.material_slots if s.material]
    cp.arrange_slots(obj, [])
    after = {s.material.name for s in obj.material_slots if s.material}
    out += [f"slot {m}" for m in before if m not in after]
    for uv in [u for u in me.uv_layers if u.name == cp.UV_NORMAL or u.name.startswith("UV_cam")]:
        out.append(f"UV {uv.name}")
        me.uv_layers.remove(uv)
    for name in _OUR_ATTRS:
        attr = me.attributes.get(name)
        if attr is not None:
            out.append(f"attribute {name}")
            me.attributes.remove(attr)
    for name in (VG_PROTECT, VG_SNAP):
        vg = obj.vertex_groups.get(name)
        if vg is not None:
            out.append(f"vertex group {name}")
            obj.vertex_groups.remove(vg)
    for k in _OUR_KEYS:
        for holder in (obj, me):
            if k in holder:
                del holder[k]
    for o in bpy.data.objects:          # nothing bakes from / is a copy of it any more
        d = getattr(o, "multicamproject_bake", None)
        if o != obj and d is not None:
            if d.bake_source == obj:
                d.bake_source = None
                out.append(f"{o.name}: Bake Source cleared")
            if d.source == obj:
                d.source = None
                out.append(f"{o.name}: no longer its Remesh copy")
    me.update()
    return out


# ---------------------------------------------------------------- Remove one workflow

def remove_projection(obj):
    """Remove 0A/0B from `obj` only: the projection modifier and its drivers, UV_camN,
    VCMix / VCMix2, the camera slots, MCP_ (its slot; the material goes with matsync) and
    BAp_ / BNp_. A raw scan's faces go back to their scan material. The cameras stay in
    the scene (other objects use them); the original's bake, ALB_ / NOR_ and MAT_ stay.
    The Bake Route becomes From Original when there is a Bake Source. Returns text lines."""
    out = []
    me = obj.data
    n = _free_projection_faces(obj)         # while MCP_ still has its slot
    if n:
        out.append(f"{n:,} faces back on their scan material")
    for mod in list(obj.modifiers):
        group = mod.node_group.name if mod.type == 'NODES' and mod.node_group else ""
        if mod.type == 'NODES' and any(group.startswith(g) for g in
                                       ("GN-CameraProject", "GN-CamProject", "MCP")):
            cp.remove_drivers(obj, mod)
            out.append(f"modifier {mod.name}")
            obj.modifiers.remove(mod)
    for uv in [u for u in me.uv_layers if u.name.startswith("UV_cam")]:
        out.append(f"UV {uv.name}")
        me.uv_layers.remove(uv)
    from ..camera_project import gn_builder
    for name in gn_builder.LAYERS + (gn_builder.MASK2_STASH,):
        attr = me.attributes.get(name)
        if attr is not None:
            out.append(f"attribute {name}")
            me.attributes.remove(attr)
    if hasattr(obj, "multicamproject_cam"):
        obj.property_unset("multicamproject_cam")       # slots, cameras, MCP_ pointer
        out.append("camera slots and MCP_")
    d = obj.multicamproject_bake
    d.bap_image = d.bnp_image = None
    d.bp_fingerprint = d.bnp_fingerprint = ""
    d.bp_size = 0
    d.route_prompt = ""
    if module_manager.is_loaded("baking"):
        from ..baking import material, route
        if d.material is not None:
            material.place(obj)
        else:
            cp.arrange_slots(obj, [])
        if route.has_original(obj):
            route.set_route(obj, route.ORIGINAL)
    else:
        cp.arrange_slots(obj, [d.material] if d.material else [])
    me.update()
    return out


def unlink_original(obj):
    """Remove 0C/0D's link from `obj`: no Bake Source, BAo_ / BNo_ unlinked (their files
    stay in the bake folder - Check Textures removes them). The low poly, its Remesh link
    (Cutting & Modelling, Snap) and the hidden original stay; picking a Bake Source again
    brings the route back. The Bake Route becomes From Projection. Returns text lines."""
    d = obj.multicamproject_bake
    out = []
    if d.bake_source is not None:
        out.append(f"Bake Source {d.bake_source.name}")
    for img in (d.ba_image, d.bn_image):
        if img is not None:
            out.append(img.name)
    d.bake_source = None
    d.ba_image = d.bn_image = d.bng_image = None
    d.ba_fingerprint = d.ba_parts = d.bng_fingerprint = ""
    d.ba_size = 0
    d.ba_far_share = d.ba_far_max = d.ba_fit_cage = 0.0
    d.route_prompt = ""
    if module_manager.is_loaded("baking"):
        from ..baking import route
        route.set_route(obj, route.PROJECTION)
    if hasattr(obj, "multicamproject_cam") and obj.multicamproject_cam.is_setup:
        cp.ensure_material(obj)         # the BAKED frame: grey now
    return out


def _export_collection(scene):
    try:
        from ..export import fixes
    except ImportError:
        return None
    return fixes.ensure_export_collection(scene)


def retopo_content(context, obj):
    """0D's start with its option on: the other selected meshes (e.g. ROOM_Template parts)."""
    return [o for o in context.selected_objects
            if o != obj and o.type == 'MESH' and not o.library]


RETOPO_CONTENT_TEXT = ("Select the mesh to start from too (the scan active), or turn off "
                       "\"Start from the Selected Mesh\" for an empty retopo")


def retopo_mesh(obj, name, content=()):
    """0D's start: the meshes of `content` (copies, in obj's local space, their first UV map
    as uv_normal), else a completely empty mesh - with every material slot of obj."""
    import bmesh
    new = bpy.data.meshes.new(name)
    if content:
        bm = bmesh.new()
        to_local = obj.matrix_world.inverted()
        for src in content:
            me = src.data.copy()
            try:
                uvs = me.uv_layers
                keep = uvs.get(cp.UV_NORMAL) or uvs.active or (uvs[0] if len(uvs) else None)
                for uv in [u for u in uvs if u != keep]:
                    uvs.remove(uv)
                if keep is not None:
                    keep.name = cp.UV_NORMAL
                me.materials.clear()
                me.transform(to_local @ src.matrix_world)
                bm.from_mesh(me)
            finally:
                bpy.data.meshes.remove(me)
        bm.to_mesh(new)
        bm.free()
    if new.uv_layers.get(cp.UV_NORMAL) is None:
        new.uv_layers.new(name=cp.UV_NORMAL)
    for m in obj.data.materials:
        new.materials.append(m)
    new.update()
    return new


def apply_new_name(scene, copy, obj, name, mesh_name):
    """0C / 0D: the New Object Name typed on the Setup rows names the copy
    ENV_<prefix>.<next ##>_<typed> (MCP_ / MAT_ / textures follow, as in 07) and the
    original takes its name back. Returns the new name, else None (nothing typed, no Name
    Prefix, no export module, or that name is taken)."""
    typed = scene.multicamproject_props.new_object_name.strip()
    if not typed:
        return None
    try:
        from ..baking import naming
        from ..export import fixes
    except ImportError:
        return None
    sc = naming.scheme(scene)
    if sc is None:
        return None
    new = naming.full_name(sc, naming.next_index(sc), typed)
    if bpy.data.objects.get(new) is not None:
        return None
    fixes.rename_object(copy, new)
    obj.name = name
    if obj.data.users == 1:
        obj.data.name = mesh_name
    return new


def is_retopo(obj):
    d = getattr(obj, "multicamproject_bake", None)
    return bool(d is not None and d.retopo)


def make_copy(context, obj, retopo=False, decimate=True):
    """The Remesh button (0C), or with `retopo` 0D Retopo: the same, but the copy gets the
    other selected meshes - or nothing, per the 0D option (retopo_mesh) - instead of the
    scan's mesh, and no Decimate. Returns (copy,
    warnings). Object Mode only. `decimate` False (0C's window button): no Decimate here -
    the work window adds it and applies it, so the main file never evaluates it."""
    if is_handmade(obj):
        raise RuntimeError(HANDMADE_TEXT)
    scene = context.scene
    warnings = []
    name, mesh_name = obj.name, obj.data.name
    was_setup = hasattr(obj, "multicamproject_cam") and obj.multicamproject_cam.is_setup
    orig_coll = _original_collection(scene)
    # a mesh already in "Original Mesh" (e.g. a duplicated original) stays there; the copy
    # goes only where the object was otherwise
    collections = [c for c in obj.users_collection if c != orig_coll]

    content = []
    if retopo and getattr(scene, "multicamproject_retopo_plane", True):
        content = retopo_content(context, obj)
        if not content:
            raise RuntimeError(RETOPO_CONTENT_TEXT)
    copy = obj.copy()                   # a full copy: a cut must never touch the original
    copy.data = retopo_mesh(obj, mesh_name, content) if retopo else obj.data.copy()
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
    if orig_coll not in obj.users_collection:
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
    if context.view_layer.objects.get(obj.name) == obj:
        obj.hide_set(True)              # (not when "Original Mesh" is excluded)
    for o in content:                   # the retopo's start (ROOM_Template parts): done
        o.select_set(False)
        o.hide_set(True)

    if retopo:
        copy.multicamproject_bake.retopo = True
        copy.vertex_groups.clear()      # the scan's groups: no weights on the plane
        for m in [m for m in copy.modifiers if m.type != 'NODES']:
            copy.modifiers.remove(m)    # Decimate / Snap / bake helpers: not for a retopo
    elif decimate:
        ensure_stack(copy)
        clear_custom_normals(copy)
    else:
        if copy.vertex_groups.get(VG_PROTECT) is None:
            copy.vertex_groups.new(name=VG_PROTECT)     # 02 Protect works here too
        clear_custom_normals(copy)
    if module_manager.is_loaded("camera_project") and was_setup:
        with quiet(copy):
            warnings += cp.setup(copy, scene)   # 0B came first: the copy projects too
    if module_manager.is_loaded("baking"):
        from ..baking import gn_final
        if gn_final.get_modifier(copy) is not None:
            gn_final.ensure_modifier(copy)      # its own wrapper, last in the stack
        from ..baking import route
        route.after_step(copy, 'RETOPO' if retopo else 'ORIGINAL')
    if not retopo and (decimate_modifier(copy) is not None or not decimate):
        set_gn(copy, False)             # the Decimate is decided first (decimate_pending)
    return copy, warnings
