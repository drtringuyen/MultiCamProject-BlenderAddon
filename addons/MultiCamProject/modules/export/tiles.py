"""Texture Tiles: one mesh, several final textures (ALB_ / NOR_ per tile).

The Split Parts (SPLIT_<name> face attributes) say which faces go together; each part sits
on a tile, several parts may share one, faces in no part go to the first tile. A second UV
map, uv_final, holds every tile side by side like UDIM: tile k (0-based) fills
u in [k, k+1], v in [0, 1]. uv_normal is not touched - projection, BAo_ and the paint
masks keep working on it; only the final bakes go through uv_final (one 8K per tile).

uv_final is made once when the switch goes on: a copy of uv_normal, then each tile's
parts unwrapped by their seams, packed to fill 0-1 and moved onto the tile. After that
it is yours: edit it by hand ("Work on" picks the active UV map) or unwrap one tile /
all tiles again with the buttons. Nothing re-unwraps it by itself; a reorder only shifts
the tiles, and faces off their tile are reported. MCP_TILE (the face's tile) follows
the parts automatically."""
import hashlib

import bpy
import numpy as np
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import split

UV_FINAL = "uv_final"
UNWRAP_TMP = "MCP_TILE_UNWRAP"
TILE_ATTR = "MCP_TILE"          # INT face attribute: the face's tile (baking/tiles_view)
MAIN = "Main"
PALETTE = ((0.95, 0.55, 0.15), (0.20, 0.65, 0.95), (0.95, 0.80, 0.15), (0.45, 0.85, 0.35),
           (0.85, 0.35, 0.75), (0.30, 0.85, 0.80), (0.95, 0.35, 0.35), (0.65, 0.55, 0.95))


def tiles_of(obj):
    return getattr(obj, "multicamproject_tiles", None) if obj is not None else None


def is_on(obj):
    t = tiles_of(obj)
    return bool(obj is not None and obj.type == 'MESH' and t is not None and t.enabled)


# ---------------------------------------------------------------- properties

_enum_cache = {}        # keep the item strings alive (Blender reads them later)


def _tile_items(self, context):
    t = self.id_data.multicamproject_tiles if hasattr(self.id_data, "multicamproject_tiles") \
        else None
    items = [(str(x.uid), x.name, f"Tile {i + 1}: {x.name}", 'NONE', x.uid)
             for i, x in enumerate(t.tiles)] if t is not None else []
    if not items:
        items = [("0", "-", "", 'NONE', 0)]
    _enum_cache[self.id_data.name] = items
    return items


def _changed(self, context):
    obj = self.id_data
    if isinstance(obj, bpy.types.Object):
        schedule(obj)


def _toggled(self, context):
    obj = self.id_data
    if self.enabled:
        if not len(self.tiles):
            start_tiles(obj)
        schedule(obj)
    else:
        try:
            from ..baking import tiles_view
            tiles_view.remove(obj)          # Final shows the one MAT_ again
        except ImportError:
            pass
    _redraw()


def _overlay_changed(self, context):
    _drop_batches()
    _redraw()


class MULTICAMPROJECT_PG_Tile(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", description="The tile's name: its ALB_ / NOR_ / MAT_ "
                         "end in _<name> (the first tile keeps the object's own names)",
                         update=lambda s, c: _redraw())
    uid: IntProperty()
    color: FloatVectorProperty(name="Color", subtype='COLOR_GAMMA', size=3, min=0, max=1,
                               default=(0.95, 0.55, 0.15), update=_overlay_changed)
    # the tile's bakes (baking/tiles_bake.py); the first tile's ALB_ / NOR_ are also the
    # object's own alb_image / nor_image
    material: PointerProperty(type=bpy.types.Material)     # MAT_<obj>_<tile> (tiles_view)
    ba_image: PointerProperty(type=bpy.types.Image)
    alb_image: PointerProperty(type=bpy.types.Image)
    nor_image: PointerProperty(type=bpy.types.Image)
    ba_stamp: StringProperty()
    alb_stamp: StringProperty()
    nor_stamp: StringProperty()


class MULTICAMPROJECT_PG_TilePart(bpy.types.PropertyGroup):
    name: StringProperty()          # the SPLIT_ attribute
    tile: EnumProperty(name="Tile", description="The texture this split part is baked into",
                       items=_tile_items, update=_changed)


_WORK_UVS = (UV_FINAL, "uv_normal")


def _work_uv_get(self):
    me = self.id_data.data if isinstance(self.id_data, bpy.types.Object) else None
    uvs = getattr(me, "uv_layers", None)
    act = uvs.active.name if uvs is not None and uvs.active else ""
    return 0 if act == UV_FINAL else 1


def _work_uv_set(self, value):
    me = self.id_data.data if isinstance(self.id_data, bpy.types.Object) else None
    uvs = getattr(me, "uv_layers", None)
    if uvs is None:
        return
    layer = uvs.get(_WORK_UVS[value])
    if layer is not None:
        uvs.active = layer          # the UV Editor shows and edits the active map
    _drop_batches()
    _redraw()


class MULTICAMPROJECT_PG_Tiles(bpy.types.PropertyGroup):
    work_uv: EnumProperty(
        name="Work on", description="The UV map you see and edit in the UV Editor (the "
        "active UV map). Bakes always use the right one",
        items=((UV_FINAL, "uv_final", "The final layout: one 0-1 square per tile, side by "
                "side. Edit it like any UV map; the Unwrap buttons redo a tile", 'UV', 0),
               ("uv_normal", "uv_normal", "The processing UV: projection, BAo_ and the paint "
                "masks", 'UV_DATA', 1)),
        get=_work_uv_get, set=_work_uv_set)
    enabled: BoolProperty(
        name="Texture Tiles",
        description="Bake this object into one texture per tile (one 8K each) instead of one "
                    "texture. Parts pick their tile; uv_final is made and kept up to date",
        update=_toggled)
    tiles: CollectionProperty(type=MULTICAMPROJECT_PG_Tile)
    active: IntProperty()
    parts: CollectionProperty(type=MULTICAMPROJECT_PG_TilePart)
    next_uid: IntProperty(default=1)
    built: StringProperty()         # unused (old automatic builds); kept in the manifest
    note: StringProperty()          # the last build's message ('' = fine)
    show_3d: BoolProperty(name="Tiles in 3D View", default=True,
                          description="Tint the faces by their tile's color (Object Mode)",
                          update=_overlay_changed)
    show_uv: BoolProperty(name="Tiles in UV Editor", default=True,
                          description="Draw the tiles and their uv_final in the UV / Image "
                                      "Editor", update=_overlay_changed)


# ---------------------------------------------------------------- tiles + parts

def _new_tile(t, name):
    x = t.tiles.add()
    x.uid = t.next_uid
    t.next_uid += 1
    x.color = PALETTE[(x.uid - 1) % len(PALETTE)]
    names = {y.name for y in t.tiles if y != x}
    base, n = name or "Tile", 1
    x.name = base
    while x.name in names:
        n += 1
        x.name = f"{base}{n}"
    return x


def sync_parts(obj):
    """The part -> tile map follows the mesh's SPLIT_ attributes: new parts go on the tile
    named like them (else the first), gone parts are dropped."""
    t = tiles_of(obj)
    names = split.part_names(obj.data)
    for i in reversed(range(len(t.parts))):
        if t.parts[i].name not in names:
            t.parts.remove(i)
    have = {p.name for p in t.parts}
    for n in names:
        if n not in have and len(t.tiles):
            p = t.parts.add()
            p.name = n
            same = next((x for x in t.tiles if x.name == split.label(n)), t.tiles[0])
            p.tile = str(same.uid)


def start_tiles(obj):
    """Switch on: a "Main" tile when faces are in no part, then one tile per part."""
    t = tiles_of(obj)
    counts, _over, none = split.stats(obj.data)
    if none or not counts:
        _new_tile(t, MAIN)
    for n in sorted(counts):
        _new_tile(t, split.label(n))
    sync_parts(obj)


def face_tiles(obj, me=None):
    """(tile index per face, faces in two parts). Parts with no tile and faces in no part
    go to tile 0. Object Mode (the mesh data). `me`: another mesh carrying obj's SPLIT_
    attributes (an evaluated copy for a bake)."""
    me = me or obj.data
    t = tiles_of(obj)
    n = len(me.polygons)
    names = split.part_names(me)
    m = split.masks(me)
    own = split.owners(names, m, n)
    over = int((np.sum([v.astype(np.int32) for v in m.values()], axis=0) > 1).sum()) \
        if m else 0
    index = {x.uid: i for i, x in enumerate(t.tiles)}
    part_tile = np.zeros(len(names) + 1, dtype=np.int32)       # [-1] = no part
    by_name = {p.name: p for p in t.parts}
    for i, name in enumerate(names):
        p = by_name.get(name)
        try:
            part_tile[i] = index.get(int(p.tile), 0) if p is not None else 0
        except (TypeError, ValueError):
            part_tile[i] = 0
    return part_tile[own], over


# ---------------------------------------------------------------- uv_final

def _loop_faces(me):
    lt = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_total", lt)
    return np.repeat(np.arange(len(me.polygons), dtype=np.int32), lt)


def _final_uv(me):
    layer = me.uv_layers.get(UV_FINAL)
    if layer is None:
        return None
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    layer.uv.foreach_get("vector", uv)
    return uv.reshape(-1, 2)


def tile_hash(obj, k):
    """Tile k's faces and their uv_final: a bake stamp changes when either does (a part
    moved, the tile unwrapped again or edited by hand). Object Mode."""
    me = obj.data
    ft, _over = face_tiles(obj)
    faces = np.nonzero(ft == k)[0]
    h = hashlib.md5(np.int64(len(me.polygons)).tobytes())
    h.update(np.ascontiguousarray(faces, dtype=np.int32).tobytes())
    uv = _final_uv(me)
    if uv is not None:
        h.update(np.ascontiguousarray(uv[(ft == k)[_loop_faces(me)]]).tobytes())
    return h.hexdigest()[:16]


_place_cache = {}


def misplaced(obj):
    """{tile index: faces whose uv_final is not on their tile} (u in [k, k+1], v in 0-1,
    judged by the face's UV centre). Object Mode, cached 2 s for the panel."""
    import time
    me = obj.data
    if obj.mode != 'OBJECT' or me.uv_layers.get(UV_FINAL) is None:
        return {}
    now = time.monotonic()
    key = (len(me.polygons), tuple(p.tile for p in tiles_of(obj).parts),
           tuple(x.uid for x in tiles_of(obj).tiles))
    hit = _place_cache.get(obj.name)
    if hit and hit[1] == key and now - hit[0] < 2.0:
        return hit[2]
    uv = _final_uv(me)
    lf = _loop_faces(me)
    n = len(me.polygons)
    cnt = np.maximum(np.bincount(lf, minlength=n), 1)
    cu = np.bincount(lf, weights=uv[:, 0], minlength=n) / cnt
    cv = np.bincount(lf, weights=uv[:, 1], minlength=n) / cnt
    ft, _over = face_tiles(obj)
    bad = (np.floor(cu) != ft) | (cv < 0.0) | (cv > 1.0)
    out = {int(k): int(v) for k, v in zip(*np.unique(ft[bad], return_counts=True))}
    _place_cache[obj.name] = (now, key, out)
    return out


def make_uv_final(obj):
    """uv_final as a copy of uv_normal (else of the active UV map); the active map stays.
    Returns the layer."""
    from ..camera_project import core as cp
    me = obj.data
    uvs = me.uv_layers
    layer = uvs.get(UV_FINAL)
    if layer is not None:
        return layer
    src = uvs.get(cp.UV_NORMAL) or uvs.active
    prev = uvs.active.name if uvs.active else ""
    if src is not None:
        uvs.active = src
    layer = uvs.new(name=UV_FINAL, do_init=src is not None)     # copies the active map
    if layer is None:
        raise RuntimeError("No room for uv_final (8 UV maps at most)")
    if uvs.get(prev):
        uvs.active = uvs[prev]
    return layer


def write_tile_attr(obj):
    """MCP_TILE (the face's tile, read by GN-Tiles) from the parts. Object Mode."""
    me = obj.data
    ft, over = face_tiles(obj)
    att = me.attributes.get(TILE_ATTR)
    if att is not None and (att.domain != 'FACE' or att.data_type != 'INT'):
        me.attributes.remove(att)
        att = None
    if att is None:
        att = me.attributes.new(TILE_ATTR, 'INT', 'FACE')
    else:
        old = np.empty(len(me.polygons), dtype=np.int32)
        att.data.foreach_get("value", old)
        if np.array_equal(old, ft):
            return ft, over
    att.data.foreach_set("value", ft.astype(np.int32))
    me.update()
    return ft, over


def _unwrap(context, obj, select):
    """Unwrap the faces in `select` into the active UV map by their seams (the tile's
    border cuts too), then pack the islands to fill 0-1."""
    from ..baking import common
    s = common.settings(context.scene)
    res = common.resolution(obj, context.scene)
    margin = 2.0 * common.margin_px(s, res) / res
    me = obj.data
    me.vertices.foreach_set("select", np.zeros(len(me.vertices), dtype=bool))
    me.edges.foreach_set("select", np.zeros(len(me.edges), dtype=bool))
    me.polygons.foreach_set("select", select)
    me.update()
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bpy.ops.mesh.select_mode(type='FACE', use_extend=False, use_expand=False)
        bpy.ops.uv.unwrap(method='ANGLE_BASED', fill_holes=True, correct_aspect=True,
                          margin_method='FRACTION', margin=margin)
        bpy.ops.uv.select_all(action='SELECT')
        bpy.ops.uv.pack_islands(shape_method='CONCAVE', margin_method='FRACTION',
                                margin=margin, rotate=True)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def unwrap_tiles(context, obj, only=None):
    """Unwrap each tile's parts again on uv_final (by your seams), pack them to fill 0-1
    and move them onto their tile. `only`: one tile index. Object Mode. Returns a message."""
    if obj.mode != 'OBJECT':
        raise RuntimeError("Object Mode needed")
    t = tiles_of(obj)
    if not len(t.tiles):
        raise RuntimeError("No tiles")
    sync_parts(obj)
    me = obj.data
    make_uv_final(obj)
    ft, over = write_tile_attr(obj)
    uvs = me.uv_layers
    prev_active = uvs.active.name if uvs.active else ""
    prev_render = next((u.name for u in uvs if u.active_render), "")
    lf = _loop_faces(me)
    nl = len(me.loops)
    saved = {}
    for coll in ("vertices", "edges", "polygons"):
        a = np.empty(len(getattr(me, coll)), dtype=bool)
        getattr(me, coll).foreach_get("select", a)
        saved[coll] = a
    ts = context.scene.tool_settings
    select_mode = tuple(ts.mesh_select_mode)
    done = []
    try:
        uv = _final_uv(me)
        from ..baking.engine import selection
        with selection(context, obj, [obj]):
            with context.temp_override(active_object=obj, object=obj, selected_objects=[obj],
                                       selected_editable_objects=[obj]):
                for k in range(len(t.tiles)):
                    if only is not None and k != only:
                        continue
                    faces = ft == k
                    if not faces.any():
                        continue
                    # a UV map of its own: the other tiles' islands would share the packing
                    tmp = uvs.new(name=UNWRAP_TMP, do_init=False)
                    try:
                        uvs.active = tmp
                        _unwrap(context, obj, faces)
                        t_uv = np.empty(nl * 2, dtype=np.float32)
                        uvs[UNWRAP_TMP].uv.foreach_get("vector", t_uv)
                    finally:
                        if uvs.get(UNWRAP_TMP):
                            uvs.remove(uvs[UNWRAP_TMP])
                    loops = faces[lf]
                    uv[loops] = t_uv.reshape(-1, 2)[loops]
                    uv[loops, 0] += k
                    done.append(t.tiles[k].name)
        uvs[UV_FINAL].uv.foreach_set("vector", uv.ravel())
    finally:
        for coll, a in saved.items():
            if len(getattr(me, coll)) == len(a):
                getattr(me, coll).foreach_set("select", a)
        ts.mesh_select_mode = select_mode
        me.update()
        if uvs.get(prev_active):
            uvs.active = uvs[prev_active]
        if uvs.get(prev_render):
            uvs[prev_render].active_render = True
    _drop_batches()
    _place_cache.pop(obj.name, None)
    t.note = f"{over} faces in two parts - Select Overlaps, fix before baking" if over else ""
    msg = "uv_final: unwrapped " + (", ".join(done) if done else "nothing")
    if over:
        msg += f"; {over} faces in two parts (first part wins)"
    return msg


def shift_tiles(obj, old_index):
    """Tiles reordered: move each tile's uv_final by its index change (the layout stays).
    `old_index`: {uid: index before}."""
    me = obj.data
    uv = _final_uv(me)
    if uv is None or obj.mode != 'OBJECT':
        return
    ft, _over = face_tiles(obj)
    lt = ft[_loop_faces(me)]
    for k, x in enumerate(tiles_of(obj).tiles):
        d = k - old_index.get(x.uid, k)
        if d:
            uv[lt == k, 0] += d
    me.uv_layers[UV_FINAL].uv.foreach_set("vector", uv.ravel())
    me.update()


# ---------------------------------------------------------------- MCP_TILE follows the parts

_pending = set()
_timer = {"on": False}


def schedule(obj):
    _pending.add(obj.name)
    _drop_batches(obj.name)
    _place_cache.pop(obj.name, None)
    if not _timer["on"]:
        _timer["on"] = True
        bpy.app.timers.register(_run, first_interval=0.3)


def _window():
    wm = bpy.context.window_manager
    return wm.windows[0] if wm and wm.windows else None


def _run():
    """Switched on: uv_final made and unwrapped once, then the map you work on. Always:
    MCP_TILE written. uv_final is never unwrapped again by itself - that is the Unwrap
    button, so your hand edits stay."""
    from ..baking import jobs
    if jobs.busy():
        return 2.0
    win = _window()
    left = set()
    for name in list(_pending):
        obj = bpy.data.objects.get(name)
        if obj is None or not is_on(obj):
            continue
        if obj.mode != 'OBJECT':
            left.add(name)                  # again when it leaves Edit Mode
            continue
        try:
            sync_parts(obj)
            if obj.data.uv_layers.get(UV_FINAL) is None:
                if obj.multicamproject_bake.locked:
                    tiles_of(obj).note = "Locked Finish - uv_final not made"
                    continue
                if win is not None:
                    with bpy.context.temp_override(window=win):
                        msg = unwrap_tiles(bpy.context, obj)
                else:
                    msg = unwrap_tiles(bpy.context, obj)
                obj.data.uv_layers.active = obj.data.uv_layers[UV_FINAL]
                print(f"[MultiCamProject] {obj.name}: {msg}")
            else:
                write_tile_attr(obj)
        except Exception as e:          # noqa: BLE001 - shown in the panel, never raised
            tiles_of(obj).note = f"uv_final not made: {e}"
            print(f"[MultiCamProject] {obj.name}: uv_final not made: {e}")
    _pending.clear()
    _pending.update(left)
    _redraw()
    if left:
        return 1.0
    _timer["on"] = False
    return None


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    """Leaving Edit Mode (parts re-assigned, uv_final edited): MCP_TILE again, overlays
    redrawn."""
    for u in depsgraph.updates:
        if not u.is_updated_geometry:
            continue
        obj = u.id.original if isinstance(u.id, bpy.types.Object) else None
        if obj is None or not is_on(obj) or obj.mode != 'OBJECT' or obj.name in _pending:
            continue
        _drop_batches(obj.name)
        _place_cache.pop(obj.name, None)
        try:
            att = obj.data.attributes.get(TILE_ATTR)
            if att is None or len(att.data) != len(obj.data.polygons):
                schedule(obj)
                continue
            ft, _o = face_tiles(obj)
            old = np.empty(len(ft), dtype=np.int32)
            att.data.foreach_get("value", old)
            if not np.array_equal(old, ft):
                schedule(obj)
        except Exception:       # noqa: BLE001 - mesh mid-change
            pass


# ---------------------------------------------------------------- operators

class MULTICAMPROJECT_OT_TileAdd(bpy.types.Operator):
    """Add a texture tile (one more ALB_ / NOR_ for this object)"""
    bl_idname = "multicamproject.tile_add"
    bl_label = "Add Tile"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        t = tiles_of(obj)
        _new_tile(t, "Tile")
        t.active = len(t.tiles) - 1
        schedule(obj)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_TileRemove(bpy.types.Operator):
    """Remove this tile; its parts move to the first tile"""
    bl_idname = "multicamproject.tile_remove"
    bl_label = "Remove Tile"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()

    def execute(self, context):
        obj = context.object
        t = tiles_of(obj)
        if len(t.tiles) < 2 or not 0 <= self.index < len(t.tiles):
            self.report({'WARNING'}, "An object keeps at least one tile")
            return {'CANCELLED'}
        gone = t.tiles[self.index].uid
        t.tiles.remove(self.index)
        first = str(t.tiles[0].uid)
        for prt in t.parts:
            try:
                if int(prt.tile) == gone:
                    prt.tile = first
            except (TypeError, ValueError):
                prt.tile = first
        t.active = min(t.active, len(t.tiles) - 1)
        schedule(obj)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_TileMove(bpy.types.Operator):
    """Move this tile left / right in uv_final (the first tile keeps the object's names)"""
    bl_idname = "multicamproject.tile_move"
    bl_label = "Move Tile"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()
    step: IntProperty(default=-1)

    def execute(self, context):
        obj = context.object
        t = tiles_of(obj)
        new = self.index + self.step
        if not (0 <= self.index < len(t.tiles) and 0 <= new < len(t.tiles)):
            return {'CANCELLED'}
        old = {x.uid: i for i, x in enumerate(t.tiles)}
        t.tiles.move(self.index, new)
        shift_tiles(obj, old)           # each tile's islands go along, layout kept
        schedule(obj)
        return {'FINISHED'}

    @classmethod
    def poll(cls, context):
        return is_on(context.object) and context.mode == 'OBJECT'


class MULTICAMPROJECT_OT_TileRebuild(bpy.types.Operator):
    """Unwrap the tile's parts again on uv_final by your seams, pack the islands to fill
    0-1 and move them onto their tile. Hand edits of that tile are replaced"""
    bl_idname = "multicamproject.tile_rebuild"
    bl_label = "Unwrap Tiles"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1, options={'SKIP_SAVE'})      # -1 = every tile

    @classmethod
    def poll(cls, context):
        return is_on(context.object) and context.mode in {'OBJECT', 'EDIT_MESH'}

    @classmethod
    def description(cls, context, props):
        if props.index >= 0:
            return ("Unwrap this tile's parts again on uv_final by your seams, pack them to "
                    "fill 0-1 and move them onto the tile. Hand edits of this tile are "
                    "replaced")
        return cls.__doc__

    def execute(self, context):
        obj = context.object
        edit = obj.mode == 'EDIT'
        if edit:
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            self.report({'INFO'}, unwrap_tiles(context, obj,
                                               None if self.index < 0 else self.index))
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            if edit:
                bpy.ops.object.mode_set(mode='EDIT')
        _redraw()
        return {'FINISHED'}


# ---------------------------------------------------------------- panel

def draw_part_tile(row, obj, name):
    """The Tile dropdown in a Split Parts row (Tiles mode)."""
    t = tiles_of(obj)
    p = next((x for x in t.parts if x.name == name), None)
    if p is not None:
        row.prop(p, "tile", text="")
    else:
        row.label(text="(new)")


def draw_tiles(layout, context, obj):
    t = tiles_of(obj)
    if t is None:
        return
    box = layout.box()
    head = box.row(align=True)
    head.prop(t, "enabled", icon='UV')
    if not t.enabled:
        box.label(text="One texture for the whole object", icon='INFO')
        return
    head.prop(t, "show_3d", text="", icon='SHADING_SOLID')
    head.prop(t, "show_uv", text="", icon='UV_DATA')
    if obj.data.uv_layers.get(UV_FINAL) is not None:
        row = box.row(align=True)
        row.label(text="Work on", icon='GROUP_UVS')
        row.prop(t, "work_uv", expand=True)
    from ..baking import common
    res = common.resolution(obj, context.scene)
    counts = _tile_counts(obj)
    baked = _bake_states(obj, context.scene)
    off = misplaced(obj)
    col = box.column(align=True)
    for i, x in enumerate(t.tiles):
        row = col.row(align=True)
        sw = row.row(align=True)
        sw.ui_units_x = 1.2
        sw.prop(x, "color", text="")
        row.prop(x, "name", text="")
        n = counts.get(i)
        info = row.row(align=True)
        info.ui_units_x = 6
        if obj.mode != 'OBJECT':
            text = ""
        elif n is None:
            text = "empty - no bake"
        else:
            text = f"{n} faces"
        info.label(text=text + (" · first" if i == 0 else ""),
                   icon='ERROR' if obj.mode == 'OBJECT' and n is None else 'NONE')
        st = baked.get(i)
        if st and st != 'empty':
            b = row.row(align=True)
            b.ui_units_x = 4
            b.label(text={'ok': "Baked", 'outdated': "Outdated", 'missing': "Not baked"}[st],
                    icon={'ok': 'CHECKMARK', 'outdated': 'TIME', 'missing': 'DOT'}[st])
        op = row.operator("multicamproject.tile_rebuild", text="", icon='UV',
                          depress=bool(off.get(i)))
        op.index = i
        op = row.operator("multicamproject.tile_move", text="", icon='TRIA_LEFT')
        op.index, op.step = i, -1
        op = row.operator("multicamproject.tile_move", text="", icon='TRIA_RIGHT')
        op.index, op.step = i, 1
        row.operator("multicamproject.tile_remove", text="", icon='X').index = i
    row = box.row(align=True)
    row.operator("multicamproject.tile_add", icon='ADD')
    row.operator("multicamproject.tile_rebuild", text="Unwrap All Tiles", icon='UV').index = -1
    used = len(counts) if counts else len(t.tiles)
    box.label(text=f"{used} textures of {res // 1024}K - parts pick their tile above",
              icon='TEXTURE')
    box.label(text="05 / 07 / Bake Final bake every tile: BAo_ again from the Original, "
                   "then ALB_ and NOR_", icon='INFO')
    if obj.data.uv_layers.get(UV_FINAL) is None:
        box.label(text="uv_final not made yet", icon='ERROR')
    elif off:
        box.label(text="Not on their tile: " + ", ".join(
            f"{t.tiles[k].name} {n} faces" for k, n in sorted(off.items())
            if k < len(t.tiles)) + " - Unwrap (or move them by hand)", icon='ERROR')
    if t.note:
        box.label(text=t.note, icon='ERROR')


def _tile_counts(obj):
    if obj.mode != 'OBJECT':
        return {}
    key = (obj.name, _parts_key(obj), len(obj.data.polygons))
    hit = _counts_cache.get(obj.name)
    if hit and hit[0] == key:
        return hit[1]
    ft, _over = face_tiles(obj)
    out = {int(k): int(v) for k, v in zip(*np.unique(ft, return_counts=True))}
    _counts_cache[obj.name] = (key, out)
    return out


_counts_cache = {}
_bake_cache = {}


def _bake_states(obj, scene):
    """{tile index: 'ok'|'outdated'|'missing'|'empty'}, at most every 2 s (the stamps hash
    the projection)."""
    import time
    if obj.mode != 'OBJECT':
        return {}
    hit = _bake_cache.get(obj.name)
    now = time.monotonic()
    if hit and now - hit[0] < 2.0:
        return hit[1]
    try:
        from ..baking import tiles_bake
        out = {i: st for i, (_n, _f, st) in enumerate(tiles_bake.state(obj, scene))}
    except Exception:       # noqa: BLE001 - the panel never breaks on a stamp
        out = {}
    _bake_cache[obj.name] = (now, out)
    return out


# ---------------------------------------------------------------- GPU overlays

_batches = {}       # (space, obj name) -> (key, data)
_handles = []


def _drop_batches(name=None):
    if name is None:
        _batches.clear()
        _counts_cache.clear()
    else:
        for k in [k for k in _batches if k[1] == name]:
            del _batches[k]
        _counts_cache.pop(name, None)


def _redraw():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR', 'PROPERTIES'}:
                area.tag_redraw()


def _colors(obj):
    t = tiles_of(obj)
    return np.array([tuple(x.color) for x in t.tiles] or [(1, 1, 1)], dtype=np.float32)


def _parts_key(obj):
    t = tiles_of(obj)
    return (tuple((p.name, p.tile) for p in t.parts), tuple(x.uid for x in t.tiles))


def _key(obj):
    t = tiles_of(obj)
    return (_parts_key(obj), len(obj.data.polygons), len(obj.data.vertices),
            tuple(tuple(round(c, 3) for c in x.color) for x in t.tiles))


def _active_tiled(context):
    obj = context.active_object
    return obj if is_on(obj) else None


def _draw_3d():
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    obj = _active_tiled(context)
    if obj is None or obj.mode != 'OBJECT' or not tiles_of(obj).show_3d:
        return
    if not obj.visible_get():
        return
    k = ('3D', obj.name)
    key = _key(obj) + (tuple(round(v, 5) for row in obj.matrix_world for v in row),)
    hit = _batches.get(k)
    if hit is None or hit[0] != key:
        me = obj.data
        me.calc_loop_triangles()
        nt = len(me.loop_triangles)
        tv = np.empty(nt * 3, np.int32)
        tp = np.empty(nt, np.int32)
        me.loop_triangles.foreach_get("vertices", tv)
        me.loop_triangles.foreach_get("polygon_index", tp)
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        nor = np.empty(len(me.vertices) * 3, np.float32)
        me.vertex_normals.foreach_get("vector", nor)
        co, nor = co.reshape(-1, 3), nor.reshape(-1, 3)
        size = float(np.linalg.norm(co.max(axis=0) - co.min(axis=0))) if len(co) else 1.0
        co = co + nor * (size * 0.0015)         # lifted off the surface: no z-fighting
        m = np.array(obj.matrix_world, np.float32)
        co = co @ m[:3, :3].T + m[:3, 3]
        ft, _over = face_tiles(obj)
        cols = _colors(obj)
        c = np.clip(ft[tp], 0, len(cols) - 1)
        rgba = np.concatenate([cols[c], np.full((nt, 1), 0.45, np.float32)], axis=1)
        sh = gpu.shader.from_builtin('SMOOTH_COLOR')
        batch = batch_for_shader(sh, 'TRIS', {"pos": co[tv],
                                              "color": np.repeat(rgba, 3, axis=0)})
        hit = (key, (sh, batch))
        _batches[k] = hit
    sh, batch = hit[1]
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(False)
    batch.draw(sh)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def _uv_data(obj):
    """(segments in UV space, colors per segment vertex, fill % per tile) of uv_final."""
    me = obj.data
    layer = me.uv_layers.get(UV_FINAL)
    if layer is None:
        return None
    nl = len(me.loops)
    uv = np.empty(nl * 2, np.float32)
    layer.uv.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2)
    ls = np.empty(len(me.polygons), np.int32)
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", ls)
    me.polygons.foreach_get("loop_total", lt)
    lf = np.repeat(np.arange(len(me.polygons), dtype=np.int32), lt)
    nxt = np.arange(nl, dtype=np.int64) + 1
    last = ls + lt - 1
    nxt[last] = ls
    seg = np.stack([uv, uv[nxt]], axis=1).reshape(-1, 2)
    ft, _over = face_tiles(obj)
    cols = _colors(obj)
    c = np.clip(ft[lf], 0, len(cols) - 1)
    rgba = np.concatenate([cols[c], np.full((nl, 1), 0.8, np.float32)], axis=1)
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    tl = np.empty(nt * 3, np.int32)
    tp = np.empty(nt, np.int32)
    me.loop_triangles.foreach_get("loops", tl)
    me.loop_triangles.foreach_get("polygon_index", tp)
    a, b, cc = (uv[tl.reshape(-1, 3)[:, i]] for i in range(3))
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (cc[:, 1] - a[:, 1])
                        - (cc[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    fill = np.bincount(np.clip(ft[tp], 0, len(cols) - 1), weights=area, minlength=len(cols))
    return seg, np.repeat(rgba, 2, axis=0), fill


def _draw_uv():
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    obj = _active_tiled(context)
    if obj is None or not tiles_of(obj).show_uv:
        return
    uvs = obj.data.uv_layers
    if uvs.active is None or uvs.active.name != UV_FINAL:
        return              # Work on uv_normal: the plain UV Editor, no tiles
    space = context.space_data
    if space is None or getattr(space, "mode", "") != 'UV':
        return              # the UV Editor only, not over an image being inpainted
    region = context.region
    v2d = region.view2d if region else None
    if v2d is None:
        return
    t = tiles_of(obj)
    x0, y0 = v2d.view_to_region(0.0, 0.0, clip=False)
    x1, y1 = v2d.view_to_region(1.0, 1.0, clip=False)
    sx, sy = x1 - x0, y1 - y0
    off = np.array([x0, y0], np.float32)
    scl = np.array([sx, sy], np.float32)
    gpu.state.blend_set('ALPHA')
    line = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    line.uniform_float("viewportSize", (region.width, region.height))
    fill = None
    if obj.mode == 'OBJECT':
        k = ('UV', obj.name)
        key = _key(obj)
        hit = _batches.get(k)
        if hit is None or hit[0] != key:
            data = _uv_data(obj)
            hit = (key, data)
            _batches[k] = hit
        if hit[1] is not None:
            seg, cols, fill = hit[1]
            # the wireframe lives in UV units; the matrix maps it to the region each draw
            sh = gpu.shader.from_builtin('SMOOTH_COLOR')
            bk = ('UVB', obj.name)
            bhit = _batches.get(bk)
            if bhit is None or bhit[0] != key:
                bhit = (key, batch_for_shader(sh, 'LINES', {"pos": seg, "color": cols}))
                _batches[bk] = bhit
            with gpu.matrix.push_pop():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(_pixel_projection(region))
                gpu.matrix.translate((off[0], off[1], 0))
                gpu.matrix.scale((scl[0], scl[1], 1))
                bhit[1].draw(sh)
    font = 0
    blf.size(font, 13)
    for i, x in enumerate(t.tiles):
        r, g, b = x.color
        quad = [(off[0] + sx * i, off[1]), (off[0] + sx * (i + 1), off[1]),
                (off[0] + sx * (i + 1), off[1] + sy), (off[0] + sx * i, off[1] + sy)]
        line.uniform_float("lineWidth", 2.0)
        line.uniform_float("color", (r, g, b, 1.0))
        batch_for_shader(line, 'LINES', {"pos": [quad[0], quad[1], quad[1], quad[2],
                                                 quad[2], quad[3], quad[3], quad[0]]}).draw(line)
        blf.color(font, r, g, b, 1.0)
        name = f"{i + 1}  {x.name}"
        if blf.dimensions(font, name)[0] > sx - 6:
            name = str(i + 1)
        blf.position(font, quad[3][0] + 4, quad[3][1] + 6, 0)
        blf.draw(font, name)
        if fill is not None and i < len(fill):
            pct = f"{fill[i] * 100:.0f}% filled"
            if blf.dimensions(font, pct)[0] > sx - 6:
                pct = f"{fill[i] * 100:.0f}%"
            blf.position(font, quad[0][0] + 4, quad[0][1] - 18, 0)
            blf.draw(font, pct)
    gpu.state.blend_set('NONE')


def _pixel_projection(region):
    from mathutils import Matrix
    w, h = max(region.width, 1), max(region.height, 1)
    return Matrix(((2 / w, 0, 0, -1), (0, 2 / h, 0, -1), (0, 0, 1, 0), (0, 0, 0, 1)))


# ---------------------------------------------------------------- register

_classes = (MULTICAMPROJECT_PG_Tile, MULTICAMPROJECT_PG_TilePart, MULTICAMPROJECT_PG_Tiles,
            MULTICAMPROJECT_OT_TileAdd, MULTICAMPROJECT_OT_TileRemove,
            MULTICAMPROJECT_OT_TileMove, MULTICAMPROJECT_OT_TileRebuild)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Object.multicamproject_tiles = PointerProperty(type=MULTICAMPROJECT_PG_Tiles)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if not bpy.app.background:
        _handles.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
            _draw_3d, (), 'WINDOW', 'POST_VIEW')))
        _handles.append((bpy.types.SpaceImageEditor,
                         bpy.types.SpaceImageEditor.draw_handler_add(
                             _draw_uv, (), 'WINDOW', 'POST_PIXEL')))


def unregister():
    for space, h in _handles:
        space.draw_handler_remove(h, 'WINDOW')
    _handles.clear()
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if bpy.app.timers.is_registered(_run):
        bpy.app.timers.unregister(_run)
    _timer["on"] = False
    _pending.clear()
    _drop_batches()
    del bpy.types.Object.multicamproject_tiles
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
