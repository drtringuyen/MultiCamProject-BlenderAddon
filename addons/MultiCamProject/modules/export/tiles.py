"""Texture Tiles: one mesh, several final textures (ALB_ / NOR_ per tile).

The Split Parts (SPLIT_<name> face attributes) say which faces go together; each part sits
on a tile, several parts may share one, faces in no part go to the first tile. A second UV
map, uv_final, holds every tile side by side like UDIM: tile k (0-based) fills
u in [k, k+1], v in [0, 1]. uv_normal is not touched - projection, BAo_ and the paint
masks keep working on it; only the final bakes go through uv_final (one 8K per tile).

uv_final is automatic: it is rebuilt when the switch goes on, when a part moves to
another tile, when tiles are added / removed / reordered and when the faces of a part
change (checked when the object leaves Edit Mode). Only the tiles whose faces changed
are unwrapped again (Smart UV of just those faces); a reorder only shifts them."""
import hashlib
import json

import bpy
import numpy as np
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import split

UV_FINAL = "uv_final"
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


class MULTICAMPROJECT_PG_TilePart(bpy.types.PropertyGroup):
    name: StringProperty()          # the SPLIT_ attribute
    tile: EnumProperty(name="Tile", description="The texture this split part is baked into",
                       items=_tile_items, update=_changed)


class MULTICAMPROJECT_PG_Tiles(bpy.types.PropertyGroup):
    enabled: BoolProperty(
        name="Texture Tiles",
        description="Bake this object into one texture per tile (one 8K each) instead of one "
                    "texture. Parts pick their tile; uv_final is made and kept up to date",
        update=_toggled)
    tiles: CollectionProperty(type=MULTICAMPROJECT_PG_Tile)
    active: IntProperty()
    parts: CollectionProperty(type=MULTICAMPROJECT_PG_TilePart)
    next_uid: IntProperty(default=1)
    built: StringProperty()         # JSON {uid: [face hash, tile index]} of the last build
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


def face_tiles(obj):
    """(tile index per face, faces in two parts). Parts with no tile and faces in no part
    go to tile 0. Object Mode (the mesh data)."""
    me = obj.data
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


def _hash(faces, n):
    h = hashlib.md5(np.int64(n).tobytes())
    h.update(np.ascontiguousarray(faces, dtype=np.int32).tobytes())
    return h.hexdigest()[:16]


def plan(obj):
    """What a build has to do: {uid: (hash, new index, 'keep'|'shift'|'unwrap')}, the
    face tiles, overlaps."""
    t = tiles_of(obj)
    ft, over = face_tiles(obj)
    n = len(obj.data.polygons)
    try:
        old = json.loads(t.built) if t.built else {}
    except ValueError:
        old = {}
    has_uv = obj.data.uv_layers.get(UV_FINAL) is not None
    out = {}
    for k, x in enumerate(t.tiles):
        h = _hash(np.nonzero(ft == k)[0], n)
        prev = old.get(str(x.uid))
        if not has_uv or prev is None or prev[0] != h:
            act = 'unwrap'
        elif prev[1] != k:
            act = 'shift'
        else:
            act = 'keep'
        out[x.uid] = (h, k, act, prev[1] if prev else k)
    return out, ft, over


def outdated(obj):
    """True when uv_final does not match the tiles (cheap: hashes the face lists)."""
    if not is_on(obj) or obj.mode != 'OBJECT':
        return False
    if {p.name for p in tiles_of(obj).parts} != set(split.part_names(obj.data)):
        return True                 # a part added / deleted: the map follows at the build
    p, _ft, _over = plan(obj)
    return any(v[2] != 'keep' for v in p.values())


# ---------------------------------------------------------------- build uv_final

def _loop_faces(me):
    ls = np.empty(len(me.polygons), dtype=np.int32)
    lt = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_start", ls)
    me.polygons.foreach_get("loop_total", lt)
    return np.repeat(np.arange(len(me.polygons), dtype=np.int32), lt)


def _smart_uv(context, obj, select):
    """Smart UV Project of the faces in `select` into the active UV map (0-1)."""
    from ..baking import common
    from .autofix import SMART_UV_ANGLE
    s = common.settings(context.scene)
    res = common.resolution(obj, context.scene)
    me = obj.data
    me.vertices.foreach_set("select", np.zeros(len(me.vertices), dtype=bool))
    me.edges.foreach_set("select", np.zeros(len(me.edges), dtype=bool))
    me.polygons.foreach_set("select", select)
    me.update()
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bpy.ops.mesh.select_mode(type='FACE', use_extend=False, use_expand=False)
        bpy.ops.uv.smart_project(angle_limit=SMART_UV_ANGLE,
                                 island_margin=2.0 * common.margin_px(s, res) / res,
                                 area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def build(context, obj, force=False):
    """Make uv_final match the tiles. Returns a one-line message; raises on bad state."""
    if obj.mode != 'OBJECT':
        raise RuntimeError("Object Mode needed")
    t = tiles_of(obj)
    if not len(t.tiles):
        raise RuntimeError("No tiles")
    sync_parts(obj)
    me = obj.data
    p, ft, over = plan(obj)
    if force:
        p = {u: (h, k, 'unwrap', ok) for u, (h, k, _a, ok) in p.items()}
    uvs = me.uv_layers
    prev_active = uvs.active.name if uvs.active else ""
    prev_render = next((u.name for u in uvs if u.active_render), "")
    layer = uvs.get(UV_FINAL)
    if layer is None:
        layer = uvs.new(name=UV_FINAL, do_init=False)
    nl = len(me.loops)
    lf = _loop_faces(me)
    saved = {}
    for coll in ("vertices", "edges", "polygons"):
        a = np.empty(len(getattr(me, coll)), dtype=bool)
        getattr(me, coll).foreach_get("select", a)
        saved[coll] = a
    ts = context.scene.tool_settings
    select_mode = tuple(ts.mesh_select_mode)
    done = {'unwrap': [], 'shift': []}
    try:
        uvs.active = layer
        from ..baking.engine import selection
        jobs_ = [(u, k, ok) for u, (h, k, act, ok) in p.items() if act == 'unwrap']
        if jobs_:
            with selection(context, obj, [obj]):
                with context.temp_override(active_object=obj, object=obj,
                                           selected_objects=[obj],
                                           selected_editable_objects=[obj]):
                    for _u, k, _ok in jobs_:
                        faces = ft == k
                        if not faces.any():
                            continue
                        _smart_uv(context, obj, faces)
                        done['unwrap'].append(k)
        uv = np.empty(nl * 2, dtype=np.float32)
        layer = uvs[UV_FINAL]
        layer.uv.foreach_get("vector", uv)
        uv = uv.reshape(-1, 2)
        for u, (h, k, act, ok) in p.items():
            loops = ft[lf] == k
            if act == 'unwrap':
                uv[loops, 0] += k                       # Smart UV packed it into 0-1
            elif act == 'shift' and k != ok:
                uv[loops, 0] += k - ok
                done['shift'].append(k)
        layer.uv.foreach_set("vector", uv.ravel())
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
    t.built = json.dumps({str(u): [h, k] for u, (h, k, _a, _ok) in p.items()})
    _drop_batches()
    msg = []
    if done['unwrap']:
        msg.append("unwrapped " + ", ".join(t.tiles[k].name for k in done['unwrap']))
    if done['shift']:
        msg.append("moved " + ", ".join(t.tiles[k].name for k in done['shift']))
    if over:
        msg.append(f"{over} faces in two parts (first part wins)")
    t.note = f"{over} faces in two parts - Select Overlaps, fix before baking" if over else ""
    return "uv_final: " + ("; ".join(msg) if msg else "up to date")


# ---------------------------------------------------------------- automatic rebuild

_pending = set()
_timer = {"on": False}


def schedule(obj):
    _pending.add(obj.name)
    if not _timer["on"]:
        _timer["on"] = True
        bpy.app.timers.register(_run, first_interval=0.3)


def _window():
    wm = bpy.context.window_manager
    return wm.windows[0] if wm and wm.windows else None


def _run():
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
        if obj.multicamproject_bake.locked:
            tiles_of(obj).note = "Locked Finish - uv_final not rebuilt"
            continue
        try:
            if win is not None:
                with bpy.context.temp_override(window=win):
                    print(f"[MultiCamProject] {obj.name}: {build(bpy.context, obj)}")
            else:
                print(f"[MultiCamProject] {obj.name}: {build(bpy.context, obj)}")
        except Exception as e:          # noqa: BLE001 - shown in the panel, never raised
            tiles_of(obj).note = f"uv_final not built: {e}"
            print(f"[MultiCamProject] {obj.name}: uv_final not built: {e}")
    _pending.clear()
    _pending.update(left)
    _redraw()
    if left:
        return 1.0
    _timer["on"] = False
    return None


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    """Leaving Edit Mode (or a Decimate applied) changes the faces: rebuild the tiles
    whose faces changed."""
    for u in depsgraph.updates:
        if not u.is_updated_geometry:
            continue
        obj = u.id.original if isinstance(u.id, bpy.types.Object) else None
        if obj is None or not is_on(obj) or obj.mode != 'OBJECT' or obj.name in _pending:
            continue
        _drop_batches(obj.name)         # vertices may have moved (Edit Mode left)
        try:
            if outdated(obj):
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
        t.tiles.move(self.index, new)
        schedule(obj)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_TileRebuild(bpy.types.Operator):
    """Unwrap every tile of uv_final again (Smart UV per tile)"""
    bl_idname = "multicamproject.tile_rebuild"
    bl_label = "Rebuild uv_final"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return is_on(context.object) and context.mode == 'OBJECT'

    def execute(self, context):
        obj = context.object
        try:
            self.report({'INFO'}, build(context, obj, force=True))
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
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
    from ..baking import common
    res = common.resolution(obj, context.scene)
    counts = _tile_counts(obj)
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
        op = row.operator("multicamproject.tile_move", text="", icon='TRIA_LEFT')
        op.index, op.step = i, -1
        op = row.operator("multicamproject.tile_move", text="", icon='TRIA_RIGHT')
        op.index, op.step = i, 1
        row.operator("multicamproject.tile_remove", text="", icon='X').index = i
    row = box.row(align=True)
    row.operator("multicamproject.tile_add", icon='ADD')
    row.operator("multicamproject.tile_rebuild", icon='FILE_REFRESH')
    box.label(text=f"{len(t.tiles)} textures of {res // 1024}K - parts pick their tile above",
              icon='TEXTURE')
    if obj.data.uv_layers.get(UV_FINAL) is None:
        box.label(text="uv_final not made yet", icon='ERROR')
    elif obj.mode == 'OBJECT' and outdated(obj):
        box.label(text="uv_final out of date - rebuilding", icon='TIME')
    if t.note:
        box.label(text=t.note, icon='ERROR')


def _tile_counts(obj):
    if obj.mode != 'OBJECT':
        return {}
    key = (obj.name, tiles_of(obj).built, len(obj.data.polygons))
    hit = _counts_cache.get(obj.name)
    if hit and hit[0] == key:
        return hit[1]
    ft, _over = face_tiles(obj)
    out = {int(k): int(v) for k, v in zip(*np.unique(ft, return_counts=True))}
    _counts_cache[obj.name] = (key, out)
    return out


_counts_cache = {}


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


def _key(obj):
    t = tiles_of(obj)
    return (t.built, len(obj.data.polygons), len(obj.data.vertices),
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
