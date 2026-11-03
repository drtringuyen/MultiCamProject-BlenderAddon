"""Level Seam / Blur Seam: straight on the object's ALB_ file - its 01.Bake file on Bake,
its 02.Overrides file on Load. uv_normal is never changed, nothing is baked.

    Level      both sides of every seam meet at their average colour (the Seam-leveling
               field), fading into each island over Falloff
    Blur       next to every seam, each island blurred inside itself (never across into
               padding or another island), fading out over Radius

    Preview    live in the materials, before anything is written: a group after the ALB_
               image node mixes in three small images (the level field, the blurred
               albedo, the blur weight) at PREVIEW_RES - the albedo itself stays at full
               size, so a slider change costs a fraction of a second
    Apply      the same at the texture's own size, written over the file - a copy goes to
               <bake folder>/_previous first and Undo puts the last one back

One preview at a time (its object: `target()`); reading the file and the seams is cached
until the file or uv_normal changes."""
import json
import os
import time

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import geometry as geo
from . import maps, shader

PREVIEW_RES = 2048
DEBOUNCE = 0.2          # seconds after the last slider change before the preview is built
PAD_PX = 4              # padding texels the result is extended into (Apply)

STATE = "seamfix_direct"    # material property: {"nodes": [[image, group]], "links": [...]}
VIEW = "seamfix_view"       # object property: 0 the file as it is, 1 the preview
TINT = "seamfix_tint"       # object property: 1 = skipped seams red, left-out islands magenta
# Undo: baking.alb_history (shared with the Inpaint of 07's ALB_ row)
GROUP = "SFD_preview"
IMG_LEVEL, IMG_BLUR, IMG_WEIGHT = "SFD_level", "SFD_blur", "SFD_weight"
IMG_TINT = "SFD_tint"
TINT_SKIP = (1.0, 0.05, 0.05)       # over Max Jump: Level leaves it alone
TINT_OUT = (1.0, 0.0, 1.0)          # overlapping island: neither tool touches it

status = ""             # the last preview / apply line the panel shows
_cache = {}
_pending = {"obj": "", "at": 0.0}


# ---------------------------------------------------------------- which image

def albedo(obj):
    """The object's ALB_ (the one 07 shows) - else the image into Base Color."""
    try:
        from ..tex_override import core
        img = core.image(obj, 'ALB')
    except Exception:
        img = None
    return img or shader.albedo(obj)


def _file(img):
    from ..baking import common
    return common.image_file(img)


def why_not(obj):
    """'' or why Level / Blur Seam cannot work on obj."""
    if obj is None or obj.type != 'MESH':
        return "Select a mesh"
    if obj.data.uv_layers.get(geo.UV_SRC) is None:
        return f"No {geo.UV_SRC} on {obj.name}"
    if obj.mode == 'EDIT':          # its UV layer reads empty until Object Mode
        return "Leave Edit Mode (Tab) first"
    d = getattr(obj, "multicamproject_bake", None)
    if d is not None and getattr(d, "locked", False):
        return "Locked Finish: unlock it in 08 first"
    if shader.installed(obj):
        return "Seam Fix (below) is set up on it - Reset it first"
    img = albedo(obj)
    if img is None:
        return "No ALB_ on this object"
    path = _file(img)
    if not path or not os.path.isfile(path):
        return f"{img.name} has no file on disk"
    if img.size[0] != img.size[1]:
        return f"{img.name} is not square"
    return ""


def kind_label(obj):
    try:
        from ..tex_override import core
        return "Load" if core.is_load(obj, 'ALB') else "Bake"
    except Exception:
        return ""


# ---------------------------------------------------------------- cached inputs

def _uv_sig(me):
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers[geo.UV_SRC].data.foreach_get("uv", uv)
    return (len(me.polygons), hash(uv.tobytes()))


def _inputs(obj, img):
    """The cache for obj / img: seams, island boxes; read again when the file or uv_normal
    changed."""
    path = _file(img)
    key = (obj.name, img.name, path, os.path.getmtime(path), _uv_sig(obj.data))
    if _cache.get("key") != key:
        _cache.clear()
        _cache["key"] = key
        seams = geo.Seams(obj.data)
        _cache["seams"] = seams
        _cache["boxes"] = _boxes(obj.data, seams.island)
    return _cache


def _at(c, img, obj, res):
    """(rgb, owner) at res, cached."""
    got = c.get(("res", res))
    if got is None:
        rgb, _alpha = maps.load_rgb(img, res)
        owner, _face, _ = geo.raster_islands(obj.data, geo.UV_SRC, c["seams"].island, res)
        got = c[("res", res)] = (rgb, owner)
    return got


def _boxes(me, island):
    """(lo, hi): per island the min / max of its UVs (0-1), (n, 2) each."""
    n_poly = len(me.polygons)
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers[geo.UV_SRC].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    start = np.empty(n_poly, dtype=np.int64)
    total = np.empty(n_poly, dtype=np.int64)
    me.polygons.foreach_get("loop_start", start)
    me.polygons.foreach_get("loop_total", total)
    order = np.argsort(start)
    face_of_loop = np.repeat(order, total[order])
    isl_of_face = np.fromiter((island[i] for i in range(n_poly)), dtype=np.int64, count=n_poly)
    isl = isl_of_face[face_of_loop]
    k = int(isl.max()) + 1 if len(isl) else 0
    lo = np.full((k, 2), np.inf)
    hi = np.full((k, 2), -np.inf)
    np.minimum.at(lo, isl, uv)
    np.maximum.at(hi, isl, uv)
    return lo, hi


def resize(arr, res):
    """arr ((h, w) or (h, w, c)) resampled to (res, res) - bilinear."""
    import OpenImageIO as oiio
    a = np.ascontiguousarray(arr, dtype=np.float32)
    h, w = a.shape[:2]
    nch = 1 if a.ndim == 2 else a.shape[2]
    buf = oiio.ImageBuf(oiio.ImageSpec(w, h, nch, oiio.FLOAT))
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, nch), a.reshape(h, w, nch))
    out = oiio.ImageBufAlgo.resize(buf, filtername="triangle",
                                   roi=oiio.ROI(0, res, 0, res, 0, 1, 0, nch))
    out = np.asarray(out.get_pixels(oiio.FLOAT), dtype=np.float32).reshape(res, res, nch)
    return out[..., 0] if a.ndim == 2 else out


# ---------------------------------------------------------------- the two operations

def level(c, img, obj, st, tex):
    """The level field at min(tex, PREVIEW_RES) - also what Apply scales up."""
    key = ("level", st.level_px, st.max_jump)
    if key not in c:            # a Radius change keeps it
        res = min(tex, PREVIEW_RES)
        rgb, owner = _at(c, img, obj, res)
        c[key] = maps.level_field(c["seams"], rgb, owner, st.level_px, tex, st.max_jump / 255)
    return c[key]


def _inward(owner, isl, p1, p2):
    """Unit normal (texels) of the segment p1-p2 pointing into island isl, or None."""
    d = p2 - p1
    length = float(np.hypot(*d))
    if length < 1e-6:
        return None
    n = np.array([-d[1], d[0]]) / length
    h, w = owner.shape
    for sign in (1.0, -1.0):
        hits = 0
        for t in (0.25, 0.5, 0.75):
            x, y = (p1 + d * t + n * sign * 1.5).astype(int)
            if 0 <= x < w and 0 <= y < h and owner[y, x] == isl:
                hits += 1
        if hits >= 2:
            return n * sign
    return None


def _borrow(src, owner, isl, edges, box, mine, reach):
    """The island's crop extended across its seams: texels outside it, up to `reach`
    from a seam edge, take the texel as far inside the other side's matching edge (same
    place along the edge, scaled to that side's length). Returns (pixels, mask)."""
    y0, y1, x0, x1 = box
    ext = src[y0:y1, x0:x1].copy()
    em = mine.astype(np.float32)
    best = np.full(mine.shape, np.inf, dtype=np.float32)
    h, w = owner.shape
    for P1, P2, other, Q1, Q2 in edges:
        nA = _inward(owner, isl, P1, P2)
        nB = _inward(owner, other, Q1, Q2)
        if nA is None or nB is None:
            continue
        da = P2 - P1
        la = float(np.hypot(*da))
        scale = float(np.hypot(*(Q2 - Q1))) / la
        bx0 = max(x0, int(min(P1[0], P2[0]) - reach) - 1)
        bx1 = min(x1, int(max(P1[0], P2[0]) + reach) + 2)
        by0 = max(y0, int(min(P1[1], P2[1]) - reach) - 1)
        by1 = min(y1, int(max(P1[1], P2[1]) + reach) + 2)
        if bx1 <= bx0 or by1 <= by0:
            continue
        ys, xs = np.mgrid[by0:by1, bx0:bx1]
        rx, ry = xs + 0.5 - P1[0], ys + 0.5 - P1[1]
        t = (rx * da[0] + ry * da[1]) / (la * la)
        s = -(rx * nA[0] + ry * nA[1])                  # distance out of the island
        ly, lx = ys - y0, xs - x0
        keep = (s > 0) & (s <= reach) & (t >= -0.05) & (t <= 1.05) \
            & ~mine[ly, lx] & (s < best[ly, lx])
        if not keep.any():
            continue
        qx = Q1[0] + t * (Q2[0] - Q1[0]) + s * scale * nB[0]
        qy = Q1[1] + t * (Q2[1] - Q1[1]) + s * scale * nB[1]
        qi, qj = np.floor(qx).astype(int), np.floor(qy).astype(int)
        inside = (qi >= 0) & (qi < w) & (qj >= 0) & (qj < h)
        keep &= inside
        keep[inside] &= owner[qj[inside], qi[inside]] == other
        if not keep.any():
            continue
        ky, kx = ly[keep], lx[keep]
        ext[ky, kx] = src[qj[keep], qi[keep]]
        em[ky, kx] = 1.0
        best[ky, kx] = s[keep]
    return ext, em


def blur(rgb, owner, seams, boxes, sigma, into, pad_px=0):
    """Near its seams each island is blurred over itself AND the strip of the other side
    across each seam (borrowed through the seam edge's two UV segments), so both sides
    mix into each other; weight 1 on the seam fading out over ~2 sigma. into: written into
    rgb (+ pad_px of padding). Else rgb untouched and (blurred, weight) returned. Returns
    the seam sides used too."""
    res = rgb.shape[0]
    lines = {}
    for (ia, ib, a1, a2, b1, b2, *_k) in seams.sides:
        pa1, pa2 = np.array(a1) * res, np.array(a2) * res
        pb1, pb2 = np.array(b1) * res, np.array(b2) * res
        lines.setdefault(ia, []).append((pa1, pa2, ib, pb1, pb2))
        lines.setdefault(ib, []).append((pb1, pb2, ia, pa1, pa2))
    lo, hi = boxes
    reach = 3 * sigma
    pad = int(np.ceil(reach)) + pad_px + 2
    B = None if into else rgb.copy()
    W = None if into else np.zeros(rgb.shape[:2], dtype=np.float32)
    src = rgb.copy() if into else rgb      # borrowed texels: never the half-written ones

    def one(isl, ls):
        """(box, own pixels, weight, blurred) of one island - reads src only, so islands
        run in parallel while the results are written one by one."""
        x0 = max(0, int(lo[isl, 0] * res) - pad)
        y0 = max(0, int(lo[isl, 1] * res) - pad)
        x1 = min(res, int(hi[isl, 0] * res) + pad + 1)
        y1 = min(res, int(hi[isl, 1] * res) + pad + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        mine = owner[y0:y1, x0:x1] == isl
        if not mine.any():
            return None
        S = np.zeros(mine.shape, dtype=np.float32)
        sy, sx = [], []
        for p1, p2, *_other in ls:
            iy, ix = maps._splat(S, None, p1, p2, 0, 0, np.array([x0, y0]))
            sy.append(iy)
            sx.append(ix)
        w8 = maps._fade(maps.gauss(S, sigma), (np.concatenate(sy), np.concatenate(sx)))
        w8 = np.where(mine, w8, 0.0).astype(np.float32)
        if not (w8 > 1e-3).any():
            return None
        ext, m = _borrow(src, owner, isl, ls, (y0, y1, x0, x1), mine, reach)
        den = np.maximum(maps.gauss(m, sigma), 1e-6)
        soft = np.stack([maps.gauss(ext[..., k] * m, sigma) / den for k in range(3)], axis=-1)
        return (y0, y1, x0, x1), mine, w8, soft

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, min(16, os.cpu_count() or 4))) as pool:
        results = pool.map(lambda it: one(*it), lines.items())
        for got in results:
            if got is not None:
                _put(got, rgb, owner, B, W, into, pad_px)
    return B, W, len(seams.sides)


def _put(got, rgb, owner, B, W, into, pad_px):
    """One island's result: mixed into rgb (+ pad_px of padding), or into B / W."""
    (y0, y1, x0, x1), mine, w8, soft = got
    if not into:
        B[y0:y1, x0:x1][mine] = soft[mine]
        W[y0:y1, x0:x1][mine] = w8[mine]
        return
    on = w8 > 1e-3
    crop = rgb[y0:y1, x0:x1]
    crop[on] = crop[on] * (1 - w8[on, None]) + soft[on] * w8[on, None]
    if pad_px:
        crop_owner = owner[y0:y1, x0:x1]
        sub = crop.copy()
        maps._dilate_into(sub, on, crop_owner == -1, pad_px)
        grow = (crop_owner == -1) & np.any(sub != crop, axis=-1)
        crop[grow] = sub[grow]


def _sigma(px, res, tex):
    return max(0.75, px * res / tex / 2)


def _blur_res(st, tex):
    """The preview's blur res: PREVIEW_RES, doubled once while the radius there would be
    under 1.5 texels (a small radius at 8K) - never more: 8K takes ~10x as long. Apply
    always works at the full size."""
    res = min(tex, PREVIEW_RES)
    if res < tex and st.blur_px * res / tex / 2 < 1.5:
        res *= 2
    return min(res, tex, 2 * PREVIEW_RES)


# ---------------------------------------------------------------- preview images + group

def _set_image(name, arr):
    """A generated Non-Color image `name` holding arr (rows bottom-up) - not packed (a
    preview: dropped on file load)."""
    res = arr.shape[0]
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) != (res, res):
        img.scale(res, res)         # the same image: the group's nodes keep pointing at it
    if img is None:
        img = bpy.data.images.new(name, res, res, alpha=True, float_buffer=False)
        img.colorspace_settings.name = 'Non-Color'
    rgba = np.ones((res, res, 4), dtype=np.float32)
    if arr.ndim == 3 and arr.shape[2] == 4:
        rgba[:] = arr
    else:
        rgba[..., :3] = arr[..., None] if arr.ndim == 2 else arr
    img.pixels.foreach_set(np.clip(rgba, 0, 1).ravel())
    img.update()
    return img


def _neutral():
    if bpy.data.images.get(IMG_LEVEL) is None:
        _set_image(IMG_LEVEL, np.full((4, 4, 3), 0.5, dtype=np.float32))
    if bpy.data.images.get(IMG_BLUR) is None:
        _set_image(IMG_BLUR, np.zeros((4, 4, 3), dtype=np.float32))
    if bpy.data.images.get(IMG_WEIGHT) is None:
        _set_image(IMG_WEIGHT, np.zeros((4, 4), dtype=np.float32))
    if bpy.data.images.get(IMG_TINT) is None:
        _set_image(IMG_TINT, np.zeros((4, 4, 4), dtype=np.float32))


def _group(srgb):
    """The preview group; only its inside is rebuilt - clearing its sockets would cut
    every material's links to it (the object then showed flat grey)."""
    ng = bpy.data.node_groups.get(GROUP) or bpy.data.node_groups.new(GROUP, 'ShaderNodeTree')
    ng.nodes.clear()
    if len(ng.interface.items_tree) != 2:
        ng.interface.clear()
        ng.interface.new_socket("Color", in_out='INPUT', socket_type='NodeSocketColor')
        ng.interface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
    b = shader._Builder(ng)
    col = b.node('NodeGroupInput').outputs["Color"]
    lev = b.tex(bpy.data.images[IMG_LEVEL], geo.UV_SRC).outputs["Color"]
    soft = b.tex(bpy.data.images[IMG_BLUR], geo.UV_SRC).outputs["Color"]
    w8 = b.tex(bpy.data.images[IMG_WEIGHT], geo.UV_SRC).outputs["Color"]
    c = b.gamma(col, 1 / 2.2) if srgb else col
    c = b.mix(1.0, c, lev, blend='ADD')
    c = b.mix(1.0, c, (0.5, 0.5, 0.5, 1.0), blend='SUBTRACT', clamp=True)
    c = b.mix(w8, c, soft)
    c = b.gamma(c, 2.2) if srgb else c
    view = b.node('ShaderNodeAttribute', attribute_type='OBJECT',
                  attribute_name=f'["{VIEW}"]').outputs["Fac"]
    c = b.mix(view, col, c)
    t = b.tex(bpy.data.images[IMG_TINT], geo.UV_SRC)
    show = b.node('ShaderNodeAttribute', attribute_type='OBJECT',
                  attribute_name=f'["{TINT}"]').outputs["Fac"]
    c = b.mix(b.math('MULTIPLY', show, t.outputs["Alpha"]), c, t.outputs["Color"])
    b.link(c, b.node('NodeGroupOutput').inputs["Color"])
    return ng


def installed(obj):
    return obj is not None and any(m.get(STATE) for m in shader.materials(obj))


def _install(obj, img):
    """The group after every node of img on uv_normal; False when there is none."""
    _neutral()
    ng = _group(img.colorspace_settings.name.lower().startswith("srgb"))
    found = False
    for mat in shader.materials(obj):
        nt = mat.node_tree
        st = json.loads(mat.get(STATE, "")) if mat.get(STATE) else {"nodes": [], "links": []}
        done = {n for n, _g in st["nodes"]}
        for n in [n for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image == img
                  and shader._reads_uv_normal(n)]:
            found = True
            if n.name in done:
                continue
            g = nt.nodes.new('ShaderNodeGroup')
            g.node_tree = ng
            g.label = "Seam Preview"
            g.location = (n.location.x + 60, n.location.y - 60)
            for l in list(n.outputs["Color"].links):
                st["links"].append([n.name, l.to_node.name, l.to_socket.identifier])
                to = l.to_socket
                nt.links.remove(l)
                nt.links.new(g.outputs["Color"], to)
            nt.links.new(n.outputs["Color"], g.inputs["Color"])
            st["nodes"].append([n.name, g.name])
        if st["nodes"]:
            mat[STATE] = json.dumps(st)
    return found


def uninstall(obj):
    for mat in shader.materials(obj):
        raw = mat.get(STATE)
        if not raw:
            continue
        st = json.loads(raw)
        nt = mat.node_tree
        for _n, g in st["nodes"]:
            node = nt.nodes.get(g)
            if node is not None:
                nt.nodes.remove(node)
        for n_name, to_name, to_sock in st["links"]:
            n, to_node = nt.nodes.get(n_name), nt.nodes.get(to_name)
            if n is None or to_node is None:
                continue
            to = next((s for s in to_node.inputs if s.identifier == to_sock), None)
            if to is not None:
                nt.links.new(n.outputs["Color"], to)
        del mat[STATE]
    for k in (VIEW, TINT):
        if k in obj:
            del obj[k]


def _drop_data():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is not None and ng.users == 0:
        bpy.data.node_groups.remove(ng)
    for name in (IMG_LEVEL, IMG_BLUR, IMG_WEIGHT, IMG_TINT):
        img = bpy.data.images.get(name)
        if img is not None and img.users == 0:
            bpy.data.images.remove(img)


def target():
    """The object the preview is on, or None."""
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and installed(obj):
            return obj
    return None


def stop(obj=None):
    """Preview gone (the file was never touched)."""
    global status
    obj = obj or target()
    if obj is not None:
        uninstall(obj)
    _drop_data()
    status = ""
    _redraw()


def set_view(obj, on):
    obj[VIEW] = 1 if on else 0
    obj.update_tag()
    for mat in shader.materials(obj):
        mat.node_tree.update_tag()
    _redraw()


def set_tint(obj, on):
    obj[TINT] = 1 if on else 0
    set_view(obj, bool(obj.get(VIEW, 1)))


def tint(c, img, obj, st, tex):
    """(rgba at min(tex, PREVIEW_RES), skipped seams, left-out islands): red lines on the
    seam sides Level leaves alone (jump over Max Jump, or unreadable), magenta over the
    overlapping islands neither tool touches. Cached per Max Jump."""
    key = ("tint", st.max_jump)
    if key not in c:
        res = min(tex, PREVIEW_RES)
        rgb, owner = _at(c, img, obj, res)
        seams = c["seams"]
        S = np.zeros((res, res), dtype=np.float32)
        sy, sx = [], []
        skipped = 0
        for (ia, ib, a1, a2, b1, b2, *_k) in seams.sides:
            ca = maps._sample_side(rgb, owner, ia, a1, a2, res)
            cb = maps._sample_side(rgb, owner, ib, b1, b2, res)
            if ca is not None and cb is not None and                     float(np.abs(ca - cb).max()) <= st.max_jump / 255:
                continue
            skipped += 1
            for p1, p2 in ((a1, a2), (b1, b2)):
                iy, ix = maps._splat(S, None, np.array(p1) * res, np.array(p2) * res, 0, 0,
                                     np.zeros(2))
                sy.append(iy)
                sx.append(ix)
        arr = np.zeros((res, res, 4), dtype=np.float32)
        if seams.left_out:
            out = np.isin(owner, list(seams.left_out))
            arr[out] = (*TINT_OUT, 0.6)
        if sy:
            line = np.clip(1.5 * maps._fade(maps.gauss(S, 1.5),
                                            (np.concatenate(sy), np.concatenate(sx))), 0, 1)
            on = line > 0.02
            arr[on, :3] = TINT_SKIP
            arr[on, 3] = np.maximum(arr[on, 3], line[on])
        c[key] = (arr, skipped, len(seams.left_out))
    return c[key]


def _redraw():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            area.tag_redraw()


def preview(obj, st):
    """Builds / refreshes the preview of the settings on obj. Returns the status line."""
    global status
    why = why_not(obj)
    if why:
        raise RuntimeError(why)
    if not (st.q_level or st.q_blur):
        stop(obj)
        return ""
    other = target()
    if other is not None and other != obj:      # one preview at a time
        stop(other)
    t0 = time.time()
    img = albedo(obj)
    tex = img.size[0]
    c = _inputs(obj, img)
    parts = []
    field = None
    if st.q_level:
        field, data = level(c, img, obj, st, tex)
        _set_image(IMG_LEVEL, field)
        parts.append(f"leveled {data['seam_sides']} seams "
                     f"(jump {data['jump_median_255']:.0f}/255)")
    else:
        _set_image(IMG_LEVEL, np.full((4, 4, 3), 0.5, dtype=np.float32))
    if st.q_blur:
        res = _blur_res(st, tex)
        rgb, owner = _at(c, img, obj, res)
        if field is not None:
            rgb = rgb + (field if field.shape[0] == res else resize(field, res)) - 0.5
        soft, w8, n = blur(rgb, owner, c["seams"], c["boxes"], _sigma(st.blur_px, res, tex),
                           into=False)
        maps._dilate_into(soft, owner >= 0, owner == -1, 2)
        maps._dilate_into(w8, owner >= 0, owner == -1, 2)
        _set_image(IMG_BLUR, soft)
        _set_image(IMG_WEIGHT, w8)
        parts.append(f"blurred {n} seams at {res}px")
    else:
        _set_image(IMG_WEIGHT, np.zeros((4, 4), dtype=np.float32))
    arr, skipped, left_out = tint(c, img, obj, st, tex)
    _set_image(IMG_TINT, arr)
    if st.q_tint:
        parts.append(f"red: {skipped} skipped, magenta: {left_out} overlapping islands")
    if not _install(obj, img):
        uninstall(obj)
        raise RuntimeError(f"{img.name} is in none of the materials on {geo.UV_SRC} - "
                           "show Baked")
    obj[TINT] = 1 if st.q_tint else 0
    set_view(obj, True)
    status = f"Preview: {', '.join(parts)} · {time.time() - t0:.2f}s"
    return status


# ---------------------------------------------------------------- debounced refresh

def schedule(obj_name):
    """Preview again DEBOUNCE s after the last call (a slider drag calls many times)."""
    _pending["obj"] = obj_name
    _pending["at"] = time.time()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=DEBOUNCE)


def _tick():
    global status
    wait = _pending["at"] + DEBOUNCE - time.time()
    if wait > 0.01:
        return wait
    obj = bpy.data.objects.get(_pending["obj"])
    if obj is None:
        return None
    try:
        preview(obj, bpy.context.scene.multicamproject_seamfix)
    except Exception as e:
        status = f"Preview failed: {e}"
        print(f"[MultiCamProject] Seam preview: {e}")
    _redraw()
    return None


# ---------------------------------------------------------------- Apply / Undo

def _read_full(path):
    """(rgb (h, w, 3) rows bottom-up, alpha or None, spec) of the file at full size."""
    import OpenImageIO as oiio
    buf = oiio.ImageBuf(path)
    spec = buf.spec()
    arr = np.asarray(buf.get_pixels(oiio.FLOAT), dtype=np.float32)
    arr = arr.reshape(spec.height, spec.width, spec.nchannels)[::-1]
    if spec.nchannels < 3:
        arr = np.repeat(arr[..., :1], 3, axis=2)
    alpha = arr[..., 3].copy() if spec.nchannels > 3 else None
    return np.ascontiguousarray(arr[..., :3]), alpha, spec


def _write_full(path, rgb, alpha, spec):
    np.clip(rgb, 0.0, 1.0, out=rgb)
    has_alpha = alpha is not None and float(alpha.min()) < 254 / 255
    import OpenImageIO as oiio
    if path.lower().endswith(".png") and not has_alpha and spec.format == oiio.UINT8:
        from ..baking.normal import pngio       # 8-bit PNG (every ALB_): the fast writer
        pngio.write_rgb8(path, rgb)
        return
    nch = spec.nchannels
    out = np.concatenate([rgb, alpha[..., None]], axis=2) if nch > 3 and alpha is not None \
        else rgb
    out = np.ascontiguousarray(out[::-1][..., :max(1, min(nch, out.shape[2]))])
    buf = oiio.ImageBuf(oiio.ImageSpec(spec.width, spec.height, out.shape[2], oiio.FLOAT))
    buf.set_pixels(oiio.ROI(0, spec.width, 0, spec.height, 0, 1, 0, out.shape[2]), out)
    if not buf.write(path, spec.format):
        raise RuntimeError(buf.geterror())


def apply(obj, st, scene, progress=None):
    """The preview's settings at full size, written over the ALB_ file (a copy in
    _previous first). Returns the status line."""
    global status
    why = why_not(obj)
    if why:
        raise RuntimeError(why)
    from ..baking import handmade
    t0 = time.time()
    step = progress or (lambda _k: None)
    img = albedo(obj)
    path = _file(img)
    tex = img.size[0]
    c = _inputs(obj, img)
    field = level(c, img, obj, st, tex)[0] if st.q_level else None
    step(1)
    rgb, alpha, spec = _read_full(path)
    if rgb.shape[0] != tex or rgb.shape[1] != tex:
        raise RuntimeError(f"{os.path.basename(path)} is not {tex}px - reload the image")
    step(2)
    if field is not None:
        up = resize(field, tex) if field.shape[0] != tex else field
        rgb += up
        rgb -= 0.5
        del up
    step(3)
    if st.q_blur:
        owner, _face, _ = geo.raster_islands(obj.data, geo.UV_SRC, c["seams"].island, tex)
        del _face
        step(4)
        blur(rgb, owner, c["seams"], c["boxes"], _sigma(st.blur_px, tex, tex), into=True,
             pad_px=PAD_PX)
        del owner
    step(5)
    from ..baking import alb_history
    alb_history.push(obj, path, scene)      # Undo: shared with 07's Inpaint
    _write_full(path, rgb, alpha, spec)
    handmade._reload_file(path)
    stop(obj)
    _cache.clear()
    done = [n for n, on in (("leveled", st.q_level), (f"blurred {st.blur_px}px", st.q_blur))
            if on]
    status = (f"{' + '.join(done)} into {os.path.basename(path)} in "
              f"{time.time() - t0:.1f}s - old one in _previous")
    return status


def undo_count(obj):
    from ..baking import alb_history
    return alb_history.count(obj)


def undo(obj):
    """The last in-place ALB_ write (an Apply here or a 07 Inpaint) taken back. Returns the
    status line."""
    global status
    from ..baking import alb_history
    if installed(obj):
        stop(obj)
    status = alb_history.undo(obj)
    _cache.clear()
    return status


# ---------------------------------------------------------------- handlers

def cleanup_all():
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and installed(obj):
            uninstall(obj)
    _drop_data()


@persistent
def _on_load(_dummy):
    """A saved preview has lost its (unpacked) images: gone, the toggles off."""
    global status
    _cache.clear()
    status = ""
    cleanup_all()
    _toggles_off()


def _toggles_off():
    """Level / Blur Seam off wherever no preview is (raw writes: no update) - after a file
    load or an add-on reload they looked on with nothing behind them (2026-11-03)."""
    if target() is not None:
        return None
    for scene in bpy.data.scenes:
        st = getattr(scene, "multicamproject_seamfix", None)
        if st is not None:
            st["q_level"] = False
            st["q_blur"] = False
    _redraw()
    return None


def register():
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_toggles_off, first_interval=0.5)   # bpy.data is off-limits here


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    try:
        cleanup_all()
    except Exception:
        pass
