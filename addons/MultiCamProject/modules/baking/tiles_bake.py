"""Texture Tiles bakes (export/tiles.py): one ALB_ / NOR_ per tile, each at the object's
full resolution, through uv_final.

Per tile k:
  BAo_<obj>_<tile>  the Original baked again onto the tile's faces only (Cycles Selected to
                    Active into a temporary UV = uv_final shifted by -k: the other tiles fall
                    outside 0-1 and are not written) - full detail, not the processing BAo_
                    stretched (that one stays on uv_normal for the preview and the masks).
                    2D Only Project: the scan's own textures by EEVEE, the same way.
  ALB_<obj>[_tile]  MCP_ rendered by EEVEE on the tile (uv_render flattened on the shifted
                    uv_final, the other tiles' faces deleted from the copy). MCP_'s BAo_ node
                    reads BAo_k through the shifted UV for that moment; the photos keep their
                    own UVs, CMX / MSK keep uv_normal. Original route: a copy of BAo_k.
  NOR_<obj>[_tile]  high-pass of ALB_k.
The first tile's ALB_ / NOR_ keep the object's names and are its alb_image / nor_image."""
import os
import time
from contextlib import contextmanager

import bmesh
import bpy
import numpy as np

from . import common, engine, fingerprint, jobs

TILE_UV = "MCP_TILE_UV"         # uv_final - (k, 0): the tile in 0-1


def _tiles():
    from ..export import tiles
    return tiles


def is_on(obj):
    try:
        return _tiles().is_on(obj)
    except ImportError:
        return False


# ---------------------------------------------------------------- names + state

def names(obj, k):
    """(BAo_, ALB_, NOR_) names of tile k."""
    t = _tiles().tiles_of(obj).tiles[k]
    tail = "" if k == 0 else "_" + t.name
    return (common.ba_name(obj) + "_" + t.name, common.alb_name(obj) + tail,
            common.nor_name(obj) + tail)


def _tile_hash(obj, k):
    """The tile's faces and their uv_final (hand edits count)."""
    return _tiles().tile_hash(obj, k)


def ba_stamp(obj, k, scene):
    """The mesh, the Bake Source and the Cage (fingerprint._source_list without uv_normal:
    a tile bakes through uv_final), the tile's faces, the size. No time in it."""
    parts = fingerprint._source_list(obj, cached=True)
    del parts[3]                    # the uv_normal checksum
    return repr([parts, _tile_hash(obj, k), common.resolution(obj, scene)])


def alb_stamp(obj, k, scene):
    from . import route
    return repr([fingerprint.compute(obj), _tile_hash(obj, k), route.get(obj),
                 common.resolution(obj, scene)])


def nor_stamp(obj, k, scene, alb):
    s = common.settings(scene)
    path = common.image_file(alb) if alb is not None else ""
    mtime = round(os.path.getmtime(path)) if path and os.path.isfile(path) else 0
    return repr([alb.name if alb else "", mtime, round(s.nor_strength, 4), s.nor_radius,
                 s.nor_invert, common.resolution(obj, scene)])


def state(obj, scene=None):
    """[(tile name, faces, 'ok'|'outdated'|'missing'|'empty')] for the panel."""
    scene = scene or bpy.context.scene
    tt = _tiles()
    t = tt.tiles_of(obj)
    ft, _over = tt.face_tiles(obj)
    out = []
    for k, x in enumerate(t.tiles):
        n = int((ft == k).sum())
        if not n:
            out.append((x.name, 0, 'empty'))
            continue
        alb = x.alb_image
        if alb is None or not common.file_ok(alb) or x.nor_image is None:
            st = 'missing'
        elif x.alb_stamp != alb_stamp(obj, k, scene) or \
                x.nor_stamp != nor_stamp(obj, k, scene, alb):
            st = 'outdated'
        else:
            st = 'ok'
        out.append((x.name, n, st))
    return out


def problem(obj):
    """Why the tiles cannot bake ('' = they can)."""
    tt = _tiles()
    t = tt.tiles_of(obj)
    if not len(t.tiles):
        return "No tiles"
    if obj.data.uv_layers.get(tt.UV_FINAL) is None:
        return "No uv_final yet"
    _ft, over = tt.face_tiles(obj)
    if over:
        return f"{over} faces in two split parts - each face needs one tile (Select Overlaps)"
    off = tt.misplaced(obj)
    if off:
        return (f"{sum(off.values())} faces of uv_final not on their tile ("
                + ", ".join(t.tiles[k].name for k in sorted(off) if k < len(t.tiles))
                + ") - Unwrap the tile or move them")
    return ""


# ---------------------------------------------------------------- mesh helpers

def _shifted(uv_final, k):
    uv = np.empty(len(uv_final.uv) * 2, np.float32)
    uv_final.uv.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2)
    uv[:, 0] -= k
    return uv


@contextmanager
def _tile_uv_on(obj, k):
    """TILE_UV on the object itself (Cycles bakes into it), removed afterwards."""
    me = obj.data
    src = me.uv_layers[_tiles().UV_FINAL]
    uv = _shifted(src, k)
    old = me.uv_layers.get(TILE_UV)
    if old is not None:
        me.uv_layers.remove(old)
    layer = me.uv_layers.new(name=TILE_UV, do_init=False)
    layer.uv.foreach_set("vector", uv.ravel())
    try:
        yield TILE_UV
    finally:
        layer = me.uv_layers.get(TILE_UV)
        if layer is not None:
            me.uv_layers.remove(layer)


def _tile_prepare(obj, k, inner=None):
    """prepare() for uv_render: the copy keeps only tile k's faces and gets TILE_UV."""
    tt = _tiles()

    def prepare(me):
        if me is None:
            return inner(None) if inner is not None else None
        if inner is not None:
            inner(me)
        src = me.uv_layers.get(tt.UV_FINAL)
        if src is None:
            raise RuntimeError("uv_final did not survive the modifiers")
        ft, _over = tt.face_tiles(obj, me)
        uv = _shifted(src, k)
        old = me.uv_layers.get(TILE_UV)
        if old is not None:
            me.uv_layers.remove(old)
        layer = me.uv_layers.new(name=TILE_UV, do_init=False)
        layer.uv.foreach_set("vector", uv.ravel())
        drop = ft != k
        if drop.any():
            bm = bmesh.new()
            bm.from_mesh(me)
            bm.faces.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[f for f, x in zip(bm.faces, drop) if x],
                             context='FACES')
            bm.to_mesh(me)
            bm.free()
    return prepare


@contextmanager
def _mcp_reads(obj, ba_img):
    """MCP_'s BAo_ node shows `ba_img` through TILE_UV for a moment."""
    from ..camera_project import core as cp
    mat = cp.data(obj).material if hasattr(obj, "multicamproject_cam") else None
    nodes = mat.node_tree.nodes if mat is not None and mat.node_tree else None
    tex = nodes.get(cp.BAKED_ALBEDO) if nodes is not None else None
    uv = nodes.get("Baked UV") if nodes is not None else None
    saved = (tex.image if tex else None, uv.uv_map if uv else None)
    try:
        if tex is not None and ba_img is not None:
            tex.image = ba_img
            if uv is not None:
                uv.uv_map = TILE_UV
        yield
    finally:
        if tex is not None:
            tex.image = saved[0]
        if uv is not None and saved[1] is not None:
            uv.uv_map = saved[1]


def _free(*imgs):
    for img in imgs:
        if img is not None and img.has_data:
            img.buffers_free()


# ---------------------------------------------------------------- BAo_ per tile

def _bake_ba(context, obj, k, size):
    """BAo_k, a generator (jobs). Returns the image."""
    scene = context.scene
    d = common.data(obj)
    ba_name = names(obj, k)[0]
    x = _tiles().tiles_of(obj).tiles[k]
    tmp = engine._work_image(ba_name, size, 'sRGB')
    try:
        if d.bake_source is None and common.self_bake(obj):
            why = engine.self_problem(obj)
            if why:
                raise RuntimeError(why)
            yield jobs.Step(f"Tile {x.name}: BAo_ from the scan's own textures (EEVEE)")
            px = engine.uv_render(context, obj, size,
                                  prepare=_tile_prepare(obj, k, engine._scan_mesh(obj)),
                                  flat_uv=TILE_UV)
            covered = px[..., 3] > 0.0
            px[..., 3] = 1.0
            engine.to_srgb(px[..., :3])
            engine.extend_margin(px, covered, common.margin_px(common.settings(scene), size))
            tmp.pixels.foreach_set(px.ravel())
            del px
        else:
            why = engine.source_problem(obj, context)
            if why:
                raise RuntimeError(why)
            src = d.bake_source
            was_final = engine.gn_final.is_final(obj)
            try:
                with common.shown(context, [obj, src]), _tile_uv_on(obj, k) as uvname, \
                        engine.render_state(scene), \
                        engine.selection(context, obj, [obj, src]), \
                        engine.source_colors(src), engine.target_nodes(obj, tmp):
                    engine.configure(scene, 'EMIT')
                    yield jobs.Step(f"Tile {x.name}: BAo_ from the Original (Cycles)")
                    yield from engine._bake(context, obj, [obj, src], 'EMIT', size, tmp,
                                            use_selected_to_active=True,
                                            cage_extrusion=common.cage(obj), uv_layer=uvname)
            finally:
                if engine.gn_final.is_final(obj) != was_final:
                    engine.gn_final.set_final(obj, scene, was_final)
        if engine._image_black(tmp):
            raise RuntimeError(engine.BLACK_TEXT.format(f"BAo_ of tile {x.name}"))
    except Exception:
        bpy.data.images.remove(tmp)
        raise
    yield jobs.Step(f"Tile {x.name}: writing {ba_name}")
    x.ba_image = engine.store_work(scene, tmp, x.ba_image, ba_name)
    x.ba_stamp = ba_stamp(obj, k, scene)
    return x.ba_image


# ---------------------------------------------------------------- ALB_ / NOR_ per tile

def _save_png(scene, tmp, name, current):
    path = common.texture_path(scene, name)
    engine._check_length(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp.filepath_raw = path
    tmp.file_format = 'PNG'
    tmp.save(filepath=path)
    img = current or bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name
    elif img.name != name and not bpy.data.images.get(name):
        img.name = name
    img.colorspace_settings.name = 'sRGB'
    engine.link_file(img, path)
    try:
        from . import owned
        owned.add(scene, [path])
    except ImportError:
        pass
    return img


def _bake_alb(context, obj, k, size, ba_img):
    from . import route
    scene = context.scene
    x = _tiles().tiles_of(obj).tiles[k]
    _ba, alb_name, _nor = names(obj, k)
    r = route.get(obj)
    if r == route.ORIGINAL:
        yield jobs.Step(f"Tile {x.name}: copying its BAo_ to {alb_name}")
        img = engine.copy_file(scene, ba_img, alb_name, x.alb_image)
    else:
        tmp = bpy.data.images.new(engine.TMP_IMAGE, size, size, alpha=False, float_buffer=False)
        tmp.colorspace_settings.name = 'sRGB'
        view = route.PROJECTION if r == route.PROJECTION else None
        try:
            yield jobs.Step(f"Tile {x.name}: EEVEE render ({r.title()})")
            from ..camera_project import core as cp
            cp.ensure_material(obj)         # before the swap: its check would undo it
            with _mcp_reads(obj, ba_img if view is None else None):
                px = yield from engine._render_albedo(context, obj, size, route_view=view,
                                                      prepare=_tile_prepare(obj, k),
                                                      flat_uv=TILE_UV, ensure=False)
            tmp.pixels.foreach_set(px.ravel())
            del px
            yield jobs.Step(f"Tile {x.name}: writing {alb_name}")
            img = _save_png(scene, tmp, alb_name, x.alb_image)
        finally:
            bpy.data.images.remove(tmp)
    x.alb_image = img
    x.alb_stamp = alb_stamp(obj, k, scene)
    return img


def _make_nor(context, obj, k, size, alb):
    from .normal import highpass, pngio, reload_from_disk
    scene = context.scene
    s = common.settings(scene)
    x = _tiles().tiles_of(obj).tiles[k]
    nor_name = names(obj, k)[2]
    path = common.texture_path(scene, nor_name)
    yield jobs.Step(f"Tile {x.name}: High-pass normal of {alb.name}")
    reload_from_disk(alb)
    rgb = highpass.generate(alb, size, s)
    yield jobs.Step(f"Tile {x.name}: writing {nor_name} (16-bit)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pngio.write_rgb16(path, rgb, s.png_compression)
    del rgb
    img = x.nor_image or bpy.data.images.get(nor_name)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = nor_name
    img.colorspace_settings.name = 'Non-Color'
    engine.link_file(img, path)
    x.nor_image = img
    x.nor_stamp = nor_stamp(obj, k, scene, alb)
    try:
        from . import owned
        owned.add(scene, [path])
    except ImportError:
        pass
    return img


# ---------------------------------------------------------------- the whole object

def bake_steps(context, obj, albedo=True, normal=True, force=False):
    """Every tile that is not current: BAo_k (when the route uses the Original), ALB_k,
    NOR_k. A generator (jobs); returns the seconds."""
    from . import route
    scene = context.scene
    d = common.data(obj)
    tt = _tiles()
    t0 = time.perf_counter()
    if obj.mode != 'OBJECT':
        raise RuntimeError("Object Mode needed")
    tt.sync_parts(obj)
    tt.write_tile_attr(obj)             # GN-Tiles / export read the face's tile
    why = problem(obj) or route.problem(obj)
    if why:
        raise RuntimeError(why)
    if any(getattr(d, a, 'BAKE') == 'LOAD' for a in ("alb_mode", "nor_mode")):
        print(f"[MultiCamProject] {obj.name}: Texture Tiles bake every tile - the Load "
              "override is not used per tile")
    size = common.resolution(obj, scene)
    r = route.get(obj)
    uses_original = r != route.PROJECTION
    if r == route.MIXED and uses_original and engine.needs_source_bake(obj):
        # MCP_ needs a BAo_ node to swap: the processing BAo_ on uv_normal first
        yield jobs.Step("Bake BAo_ (processing, uv_normal)", 0.0)
        yield from engine.bake_from_source_steps(context, obj, normal=False)
    if r != route.ORIGINAL and engine.needs_projection_bake(obj):
        yield from engine.bake_projection_steps(context, obj)
    yield from engine._save_paint(obj)
    t = tt.tiles_of(obj)
    ft, _over = tt.face_tiles(obj)
    done = []
    for k, x in enumerate(t.tiles):
        if not (ft == k).any():
            continue
        ba = x.ba_image
        made = []
        if uses_original and (force or ba is None or not common.file_ok(ba)
                              or x.ba_stamp != ba_stamp(obj, k, scene)):
            ba = yield from _bake_ba(context, obj, k, size)
            made.append("BAo_")
        alb = x.alb_image
        if albedo and (force or alb is None or not common.file_ok(alb)
                       or x.alb_stamp != alb_stamp(obj, k, scene)):
            alb = yield from _bake_alb(context, obj, k, size, ba)
            made.append("ALB_")
        if normal and alb is not None and (force or x.nor_image is None
                                           or not common.file_ok(x.nor_image)
                                           or x.nor_stamp != nor_stamp(obj, k, scene, alb)):
            yield from _make_nor(context, obj, k, size, alb)
            made.append("NOR_")
        if k:
            _free(x.ba_image, x.alb_image, x.nor_image)     # one tile in memory at a time
        else:
            _free(x.ba_image)
        if made:
            done.append(f"{x.name} ({' '.join(made)})")
    first = t.tiles[0]
    if first.alb_image is not None:
        d.alb_image = first.alb_image
        d.alb_size = size
        d.fingerprint = fingerprint.compute(obj)
    if first.nor_image is not None:
        d.nor_image = first.nor_image
        d.nor_size = size
        try:
            from .normal import state as nor_state
            d.nor_fingerprint = nor_state(obj, scene)
        except ImportError:
            pass
    d.last_bake_seconds = time.perf_counter() - t0
    print(f"[MultiCamProject] {obj.name}: tiles baked {', '.join(done) or 'nothing (current)'} in "
          f"{d.last_bake_seconds:.0f} s")
    return d.last_bake_seconds


# ---------------------------------------------------------------- work file hand-off

def image_slots(obj):
    """[(tile, pointer)] of every tile image this object owns: each tile's BAo_, and the
    ALB_ / NOR_ of the tiles after the first (the first one's are the object's own)."""
    if not is_on(obj):
        return []
    out = []
    for k, x in enumerate(_tiles().tiles_of(obj).tiles):
        out.append((x, "ba_image"))
        if k:
            out += [(x, "alb_image"), (x, "nor_image")]
    return out


def to_manifest(obj, scene):
    """Send Back: the tiles as JSON - names, colors, the part map, uv_final's build record,
    each tile's files and whether they are current here (stamps cannot travel: Receive
    stamps the current ones again in the main file)."""
    tt = _tiles()
    t = tt.tiles_of(obj)
    if t is None or not len(t.tiles):
        return None
    st = {n: s for n, _f, s in state(obj, scene)} if t.enabled else {}
    tiles = []
    for k, x in enumerate(t.tiles):
        files = {}
        for ptr in ("ba_image", "alb_image", "nor_image"):
            if k == 0 and ptr != "ba_image":
                continue
            path = common.image_file(getattr(x, ptr))
            if path and os.path.isfile(path):
                files[ptr] = path
        ba_ok = bool(x.ba_image and x.ba_stamp == ba_stamp(obj, k, scene))
        tiles.append({"name": x.name, "uid": x.uid, "color": list(x.color), "files": files,
                      "fresh": st.get(x.name) == 'ok', "ba_fresh": ba_ok})
    return {"enabled": t.enabled, "tiles": tiles, "next_uid": t.next_uid, "built": t.built,
            "parts": [[p.name, p.tile] for p in t.parts],
            "show_3d": t.show_3d, "show_uv": t.show_uv}


def from_manifest(obj, scene, data):
    """Receive: the window's tiles here - files copied into this file's bake folder (the old
    ones to the Recycle Bin), images pointed at them, current tiles stamped by this file.
    Returns the tile files taken."""
    import shutil
    tt = _tiles()
    t = tt.tiles_of(obj)
    if t is None:
        return []
    if not data:
        if t.enabled:
            t.enabled = False           # the window switched Texture Tiles off
        return []
    from ..export import fixes
    t.tiles.clear()
    for item in data["tiles"]:
        x = t.tiles.add()
        x.uid, x.color = item["uid"], item["color"]
        x.name = item["name"]
    t.next_uid = data.get("next_uid", len(t.tiles) + 1)
    t.parts.clear()
    for name, tile in data.get("parts", []):
        p = t.parts.add()
        p.name = name
        try:
            p.tile = tile
        except TypeError:
            pass
    t.built = data.get("built", "")
    t.show_3d, t.show_uv = data.get("show_3d", True), data.get("show_uv", True)
    taken = []
    for k, (x, item) in enumerate(zip(t.tiles, data["tiles"])):
        names_k = dict(zip(("ba_image", "alb_image", "nor_image"), names(obj, k)))
        for ptr, src in item.get("files", {}).items():
            if not os.path.isfile(src):
                continue
            dst = common.texture_path(scene, names_k[ptr])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
                if os.path.isfile(dst):
                    fixes.to_recycle_bin([dst])
                shutil.copyfile(src, dst)
            img = getattr(x, ptr) or bpy.data.images.get(names_k[ptr])
            if img is None:
                img = bpy.data.images.load(dst, check_existing=False)
                img.name = names_k[ptr]
            img.colorspace_settings.name = 'Non-Color' if ptr == "nor_image" else 'sRGB'
            engine.link_file(img, dst)
            setattr(x, ptr, img)
            taken.append(os.path.basename(dst))
            try:
                from . import owned
                owned.add(scene, [dst])
            except ImportError:
                pass
    if data.get("enabled") and not t.enabled:
        t.enabled = True                # tiles are there: no start_tiles, a build if needed
    d = common.data(obj)
    if len(t.tiles):        # the first tile's ALB_ / NOR_ came as the object's own
        t.tiles[0].alb_image, t.tiles[0].nor_image = d.alb_image, d.nor_image
    for k, (x, item) in enumerate(zip(t.tiles, data["tiles"])):
        if item.get("ba_fresh") and x.ba_image is not None:
            x.ba_stamp = ba_stamp(obj, k, scene)
        if item.get("fresh"):
            alb = x.alb_image
            if alb is not None:
                x.alb_stamp = alb_stamp(obj, k, scene)
                x.nor_stamp = nor_stamp(obj, k, scene, alb)
    from . import tiles_view
    tiles_view.refresh(obj, scene)
    return taken
