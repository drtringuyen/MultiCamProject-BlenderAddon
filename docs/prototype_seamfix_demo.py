"""Seam fix demo - BOBERT_art's method, rebuilt with plain Blender data.

Idea: paint runs past every UV island edge (the margin). Across a seam, the
surface just inside island B is "where island A would continue", so a thin strip
of B gets a second UV (UV_seamfix) that unfolds it into A's margin. A per-corner
mask (1 on the seam, 0 one face-row inside B) blends B's own paint into A's
continuation, so both sides show the same colour right at the seam.

Run from the Text Editor (Alt+P):
  RUN = "selected"  seam-fixes the ACTIVE mesh using its own UVs (first UV map
                    that isn't UV_seamfix). Unwrap first, no overlapping islands,
                    paint/extend the texture past the island edges.
  RUN = "demo"      rebuilds the Suzanne demo (everything named "SeamFix_*").
"""
import math

import bmesh
import bpy
import numpy as np
from mathutils import Euler

RUN = "selected"       # "selected" or "demo", see above
FIT_MARGINS = False    # selected: shrink islands whose strips collide - MOVES your UVs,
                       # so only before painting. False = switch the blend off there.
BAKE = True            # selected: bake the blended result to "<object>_SeamFixed"

PREFIX = "SeamFix_"
IMG_RES = 1024
ISLAND_MARGIN = 0.005  # deliberately too tight, so fit_margins() has work to do
SUBDIV_LEVELS = 1      # smaller faces = thinner blend strip = smaller margin needed
MAIN_UV = "UVMap"
FIX_UV = "UV_seamfix"
MASK = "seam_mask"


# ---------------------------------------------------------------- cleanup

def clear_previous():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.curves,
                 bpy.data.materials, bpy.data.images):
        for idb in [i for i in coll if i.name.startswith(PREFIX)]:
            coll.remove(idb)


# ---------------------------------------------------------------- mesh + UVs

def make_suzanne():
    bm = bmesh.new()
    bmesh.ops.create_monkey(bm)
    base = bpy.data.meshes.new(PREFIX + "tmp")
    bm.to_mesh(base)
    bm.free()

    tmp = bpy.data.objects.new(PREFIX + "tmp", base)
    bpy.context.scene.collection.objects.link(tmp)
    mod = tmp.modifiers.new("Subdiv", 'SUBSURF')
    mod.levels = SUBDIV_LEVELS
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(deps))
    me.name = PREFIX + "Suzanne"
    bpy.data.objects.remove(tmp)
    bpy.data.meshes.remove(base)
    for p in me.polygons:
        p.use_smooth = True

    # Smart UV Project needs edit mode on an active object
    obj = bpy.data.objects.new(PREFIX + "unwrap", me)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    me.uv_layers.new(name=MAIN_UV)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=ISLAND_MARGIN)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(obj)
    return me


# ---------------------------------------------------------------- "painted" texture

def make_painted_image():
    """Brushy pattern over the WHOLE UV square, so the margins are painted too
    (like oil paint running past the island outlines on a canvas). It is laid out
    in UV space, so neighbouring islands don't match - that's the seam."""
    n = IMG_RES
    v, u = np.mgrid[0:n, 0:n] / n
    tau = 2 * np.pi
    hue = 0.5 + 0.5 * np.sin(tau * (1.7 * u + 0.6 * v)) * np.cos(tau * (0.8 * u - 1.3 * v))
    angle = tau * (0.25 * np.sin(3 * u) + 0.2 * np.cos(4 * v + 1.0))
    strokes = np.sin(tau * 45 * (u * np.cos(angle) + v * np.sin(angle)))
    shade = 0.82 + 0.18 * strokes

    c0 = np.array([0.85, 0.35, 0.15])
    c1 = np.array([0.95, 0.80, 0.30])
    c2 = np.array([0.15, 0.45, 0.70])
    h = hue[..., None]
    rgb = np.where(h < 0.5, c0 + (c1 - c0) * (h * 2), c1 + (c2 - c1) * (h * 2 - 1))
    rgb *= shade[..., None]

    rgba = np.ones((n, n, 4), dtype=np.float32)
    rgba[..., :3] = rgb
    img = bpy.data.images.new(PREFIX + "Painted", n, n)
    img.pixels.foreach_set(rgba.ravel())
    img.pack()
    return img


# ---------------------------------------------------------------- the seam fix itself

def _c(uv):
    return complex(uv.x, uv.y)


def face_uvs(face, uv_layer):
    return {l.vert: l[uv_layer].uv.copy() for l in face.loops}


def is_uv_seam(edge, uv_layer, tol=1e-5):
    fa, fb = edge.link_faces
    ua, ub = face_uvs(fa, uv_layer), face_uvs(fb, uv_layer)
    return any((ua[v] - ub[v]).length > tol for v in edge.verts)


def find_islands(bm, uv_layer):
    island = {}
    for start in bm.faces:
        if start.index in island:
            continue
        idx = len(set(island.values()))
        stack = [start]
        island[start.index] = idx
        while stack:
            f = stack.pop()
            for e in f.edges:
                if len(e.link_faces) != 2 or is_uv_seam(e, uv_layer):
                    continue
                for g in e.link_faces:
                    if g.index not in island:
                        island[g.index] = idx
                        stack.append(g)
    return island


def unfold_transform(a1, a2, b1, b2, a_center, b_center):
    """2D similarity that puts B's seam edge (b1, b2) onto A's (a1, a2), with
    B's face landing on the far side of the edge, i.e. in A's margin."""
    A1, A2, B1, B2 = _c(a1), _c(a2), _c(b1), _c(b2)
    d = A2 - A1

    def side(p):
        q = p - A1
        return d.real * q.imag - d.imag * q.real

    sa = side(_c(a_center))
    s = d / (B2 - B1)
    t = A1 - s * B1
    if side(s * _c(b_center) + t) * sa < 0:
        return (False, s, t)
    s = d / (B2 - B1).conjugate()        # B's island is mirrored relative to A
    t = A1 - s * B1.conjugate()
    return (True, s, t)


def apply_transform(tr, uv):
    mirror, s, t = tr
    z = _c(uv)
    w = s * (z.conjugate() if mirror else z) + t
    return (w.real, w.imag)


def center(uvs):
    return sum(uvs.values(), uvs[next(iter(uvs))] * 0) / len(uvs)


def _uvkey(vert_index, uv):
    """A corner's identity on the UV sheet: the vertex plus where it sits.
    Both sides of a cut inside one island get different keys."""
    return (vert_index, round(uv[0], 5), round(uv[1], 5))


def seam_sides(bm, uv, island, rank):
    """[(edge, source face, target face)] for every UV seam edge.

    Between two islands the higher-ranked one is the source. A cut inside ONE
    island (e.g. down the back of a head) has no ranking, so each chain of such
    edges is walked once and the source stays on the side whose UV continues."""
    seams = [e for e in bm.edges if len(e.link_faces) == 2 and is_uv_seam(e, uv)]
    sides = {}
    same = set()
    for e in seams:
        f1, f2 = e.link_faces
        i1, i2 = island[f1.index], island[f2.index]
        if i1 == i2:
            same.add(e.index)
        else:
            sides[e.index] = (f1, f2) if rank(i1) > rank(i2) else (f2, f1)

    for start in seams:
        if start.index not in same or start.index in sides:
            continue
        sides[start.index] = tuple(start.link_faces)
        stack = [start]
        while stack:
            e = stack.pop()
            a_uv = face_uvs(sides[e.index][0], uv)
            for v in e.verts:
                for e2 in v.link_edges:
                    if e2.index not in same or e2.index in sides:
                        continue
                    g1, g2 = e2.link_faces
                    if (face_uvs(g1, uv)[v] - a_uv[v]).length < 1e-5:
                        sides[e2.index] = (g1, g2)
                    elif (face_uvs(g2, uv)[v] - a_uv[v]).length < 1e-5:
                        sides[e2.index] = (g2, g1)
                    else:
                        continue
                    stack.append(e2)
    return [(e, *sides[e.index]) for e in seams if e.index in sides]


def build_seamfix(me):
    """Writes UV_seamfix and the seam_mask corner attribute.

    For every seam edge one side is the source A and the other the target B
    (see seam_sides). Every B face touching such an edge is a strip face; it picks
    ONE source and copies only from it:
      - corners on the seam: the vertex's UV on the source side, mask 1
      - other corners: B's UV unfolded into the source's margin, averaged over the
        strip faces that share the corner and the source, mask 0
    Corners are tracked by their UV position, so cuts inside one island work too.
    Returns stats, the face -> island map and [(strip face, source island)]."""
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    uv = bm.loops.layers.uv[MAIN_UV]
    fix = bm.loops.layers.uv.get(FIX_UV) or bm.loops.layers.uv.new(FIX_UV)
    mask = bm.loops.layers.float.get(MASK) or bm.loops.layers.float.new(MASK)

    island = find_islands(bm, uv)
    size = {}
    for i in island.values():
        size[i] = size.get(i, 0) + 1

    def rank(i):
        return (size[i], -i)

    target_src = {}   # target corner key -> (source island, source UV)
    edge_opts = {}    # target face index -> [(source island, transform, {vert: source UV})]
    vert_opts = {}    # target corner key -> [same]
    same_island = 0
    for e, a, b in seam_sides(bm, uv, island, rank):
        src = island[a.index]
        same_island += src == island[b.index]
        ua, ub = face_uvs(a, uv), face_uvs(b, uv)
        v1, v2 = e.verts
        tr = unfold_transform(ua[v1], ua[v2], ub[v1], ub[v2], center(ua), center(ub))
        opt = (src, tr, {v.index: ua[v] for v in e.verts})
        edge_opts.setdefault(b.index, []).append(opt)
        for v in e.verts:
            key = _uvkey(v.index, ub[v])
            vert_opts.setdefault(key, []).append(opt)
            if key not in target_src or rank(src) > rank(target_src[key][0]):
                target_src[key] = (src, ua[v])

    def key_of(loop):
        return _uvkey(loop.vert.index, loop[uv].uv)

    # Pick one source per strip face, then average the inner corners per source
    face_opt = {}
    inner = {}    # (corner key, source island) -> [sum_u, sum_v, count]
    for f in bm.faces:
        keys = [key_of(l) for l in f.loops if key_of(l) in target_src]
        if not keys:
            continue
        options = edge_opts.get(f.index) or [o for k in keys for o in vert_opts[k]]
        opt = max(options, key=lambda o: rank(o[0]))
        face_opt[f.index] = opt
        src, tr, _ = opt
        for l in f.loops:
            if key_of(l) in target_src:
                continue
            u, w = apply_transform(tr, l[uv].uv)
            acc = inner.setdefault((key_of(l), src), [0.0, 0.0, 0])
            acc[0] += u
            acc[1] += w
            acc[2] += 1

    strip_widths = []
    for f in bm.faces:
        opt = face_opt.get(f.index)
        for l in f.loops:
            key = key_of(l)
            l[mask] = 1.0 if key in target_src else 0.0
            if opt is None:
                l[fix].uv = l[uv].uv
                continue
            src, tr, seam_uv = opt
            if key in target_src:
                if l.vert.index in seam_uv:
                    l[fix].uv = seam_uv[l.vert.index]
                elif target_src[key][0] == src:
                    l[fix].uv = target_src[key][1]
                else:   # corner copies from another source: stay in this face's source frame
                    l[fix].uv = apply_transform(tr, l[uv].uv)
            elif (key, src) in inner:
                su, sv, n = inner[(key, src)]
                l[fix].uv = (su / n, sv / n)
            else:
                l[fix].uv = l[uv].uv
        if f.index in edge_opts:
            strip_widths.extend((l[uv].uv - l.link_loop_next[uv].uv).length for l in f.loops)

    bm.to_mesh(me)
    bm.free()
    strip = float(np.median(strip_widths)) if strip_widths else 0.0
    stats = {
        "islands": len(size),
        "seam_edges_in_one_island": same_island,
        "strip_faces": len(face_opt),
        "strip_width_uv": round(strip, 4),
        "strip_width_px": round(strip * IMG_RES, 1),
    }
    return stats, island, [(fi, o[0]) for fi, o in face_opt.items()]


# ---------------------------------------------------------------- margin check + island shrink

def _raster_tri(p0, p1, p2, res):
    """Pixel (x, y) arrays whose centres fall strictly inside a UV triangle.
    Coordinates may fall outside 0..res; the caller decides what that means."""
    pts = np.array([p0, p1, p2], dtype=np.float64) * res
    x0, y0 = np.floor(pts.min(axis=0)).astype(int)
    x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    px, py = xs + 0.5, ys + 0.5
    (ax, ay), (bx, by), (cx, cy) = pts
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(area) < 1e-12:
        return xs[:0], ys[:0]
    w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area
    w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area
    inside = (w0 > 0) & (w1 > 0) & (1 - w0 - w1 > 0)
    return xs[inside], ys[inside]


def check_margins(me, island, strip_faces, res=512, tol_px=4):
    """Rasterises islands and the borrowed strips (UV_seamfix) and counts overlaps.

    Fixable by shrinking islands (reported per island in "bad_islands"):
      - main_uv_overlap: two islands overlap in UVMap itself
      - px_on_other_island: a strip lands on a third island
      - px_outside_0_1: a strip reads past the texture edge
      - px_margins_overlap: strips copying from DIFFERENT islands claim one pixel
    Not fixable by shrinking - the strip lives in its source island's own scale,
    so its shape relative to that island never changes ("unsafe_faces"):
      - px_on_source_island: a strip folds back onto the island it copies from
        (the outline curves inward more tightly than the strip is wide)
      - px_same_source_overlap: two strips copy into the same notch of one island
    Only strip faces whose mask is still on are checked."""
    uv = me.uv_layers[MAIN_UV].data
    fix = me.uv_layers[FIX_UV].data
    mask = me.attributes[MASK].data
    owner = np.full((res, res), -1, dtype=np.int32)
    tris_by_face = {}
    totals = {"main_uv_overlap": 0, "px_on_other_island": 0, "px_outside_0_1": 0,
              "px_margins_overlap": 0, "px_on_source_island": 0, "px_same_source_overlap": 0}
    per_island = {}
    per_face = {}

    def blame(kind, n, islands=(), faces=()):
        totals[kind] += n
        for i in islands:
            per_island[i] = per_island.get(i, 0) + n
        for f in faces:
            per_face[f] = per_face.get(f, 0) + n

    for tri in me.loop_triangles:
        tris_by_face.setdefault(tri.polygon_index, []).append(tri)
        xs, ys = _raster_tri(*(uv[i].uv for i in tri.loops), res)
        ok = (xs >= 0) & (xs < res) & (ys >= 0) & (ys < res)
        xs, ys = xs[ok], ys[ok]
        isl = island[tri.polygon_index]
        o = owner[ys, xs]
        hit = (o != -1) & (o != isl)
        if hit.any():
            blame("main_uv_overlap", int(hit.sum()), [isl, *np.unique(o[hit]).tolist()])
        owner[ys, xs] = isl

    margin_src = np.full((res, res), -1, dtype=np.int32)
    margin_face = np.full((res, res), -1, dtype=np.int32)
    for fi, src in strip_faces:
        if not any(mask[i].value > 0 for t in tris_by_face[fi] for i in t.loops):
            continue
        for tri in tris_by_face[fi]:
            xs, ys = _raster_tri(*(fix[i].uv for i in tri.loops), res)
            ok = (xs >= 0) & (xs < res) & (ys >= 0) & (ys < res)
            if (~ok).any():
                blame("px_outside_0_1", int((~ok).sum()), [src], [fi])
            xs, ys = xs[ok], ys[ok]
            o = owner[ys, xs]
            hit = (o != -1) & (o != src)
            if hit.any():
                blame("px_on_other_island", int(hit.sum()), [src, *np.unique(o[hit]).tolist()], [fi])
            fold = o == src
            if fold.any():
                blame("px_on_source_island", int(fold.sum()), faces=[fi])
            m, mf = margin_src[ys, xs], margin_face[ys, xs]
            fight = (m != -1) & (m != src)
            if fight.any():
                blame("px_margins_overlap", int(fight.sum()), [src, *np.unique(m[fight]).tolist()], [fi])
            same = (m == src) & (mf != fi)
            if same.any():
                blame("px_same_source_overlap", int(same.sum()),
                      faces=[fi, *np.unique(mf[same]).tolist()])
            free = m == -1
            margin_src[ys[free], xs[free]] = src
            margin_face[ys[free], xs[free]] = fi
    totals["bad_islands"] = sorted(i for i, n in per_island.items() if n > tol_px)
    totals["unsafe_faces"] = sorted(f for f, n in per_face.items() if n > tol_px)
    return totals


def disable_blend(me, faces):
    """Turns the mask off at the seam corners of the given strip faces, for every
    face sharing those corners (same vertex, same UV), so the mask stays
    continuous. The seam stays as it was there instead of showing wrong paint."""
    mask = me.attributes[MASK].data
    uv = me.uv_layers[MAIN_UV].data
    loops = me.loops

    def key(i):
        return _uvkey(loops[i].vertex_index, uv[i].uv)

    off = set()
    for fi in faces:
        off.update(key(i) for i in me.polygons[fi].loop_indices if mask[i].value > 0)
    for i in range(len(loops)):
        if mask[i].value > 0 and key(i) in off:
            mask[i].value = 0.0
    return len(off)


def scale_islands(me, island, islands_to_scale, factor):
    """Scales the given islands about their own UV bounding-box centres."""
    uv = me.uv_layers[MAIN_UV].data
    loops_by_island = {}
    for p in me.polygons:
        if island[p.index] in islands_to_scale:
            loops_by_island.setdefault(island[p.index], []).extend(p.loop_indices)
    for idxs in loops_by_island.values():
        co = np.array([uv[i].uv[:] for i in idxs])
        mid = (co.min(axis=0) + co.max(axis=0)) / 2
        co = mid + (co - mid) * factor
        for i, c in zip(idxs, co):
            uv[i].uv = c


def _summary(report):
    return {k: v for k, v in report.items() if k not in ("bad_islands", "unsafe_faces")} | {
        "islands_to_shrink": len(report["bad_islands"]), "unsafe_faces": len(report["unsafe_faces"])}


def fit_margins(me, step=0.95, max_iter=40, res=512):
    """1. Shrinks islands until no strip lands on another island, past the texture
          edge, or on another island's strip (only islands in a conflict shrink).
       2. Turns the blend off where a strip folds back onto its own source or two
          strips share a notch - shrinking can't fix those.
    Do this BEFORE painting/projecting - it moves the UVs the paint relies on."""
    history = []
    for it in range(max_iter + 1):
        stats, island, strips = build_seamfix(me)
        report = check_margins(me, island, strips, res)
        history.append(_summary(report))
        if not report["bad_islands"] or it == max_iter:
            break
        scale_islands(me, island, set(report["bad_islands"]), step)

    disabled = disable_blend(me, report["unsafe_faces"])
    final = check_margins(me, island, strips, res)
    return {"shrink_passes": len(history) - 1, "first": history[0],
            "after_shrink": history[-1], "seam_verts_blend_off": disabled,
            "final": _summary(final), "strip_width_px": stats["strip_width_px"]}


# ---------------------------------------------------------------- material

def make_material(img, name=PREFIX + "Material"):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    def node(kind, x, y, **props):
        n = N.new(kind)
        n.location = (x, y)
        for k, val in props.items():
            setattr(n, k, val)
        return n

    uv_main = node('ShaderNodeUVMap', -900, 200, uv_map=MAIN_UV)
    uv_fix = node('ShaderNodeUVMap', -900, -100, uv_map=FIX_UV)
    tex_main = node('ShaderNodeTexImage', -650, 250, image=img, label="Own paint (UVMap)")
    tex_fix = node('ShaderNodeTexImage', -650, -50, image=img, extension='EXTEND',
                   label="A's margin (UV_seamfix)")
    seam_mask = node('ShaderNodeAttribute', -650, -350, attribute_name=MASK, label="seam_mask")
    fix_on = node('ShaderNodeAttribute', -650, -550, attribute_type='OBJECT',
                  attribute_name='["seam_fix"]', label="Object: seam_fix 0/1")
    show_mask = node('ShaderNodeAttribute', -400, -550, attribute_type='OBJECT',
                     attribute_name='["show_mask"]', label="Object: show_mask 0/1")
    fac = node('ShaderNodeMath', -400, -350, operation='MULTIPLY')
    mix = node('ShaderNodeMix', -150, 150, data_type='RGBA', label="Blend")
    view = node('ShaderNodeMix', 80, 150, data_type='RGBA', label="Mask preview")
    emit = node('ShaderNodeEmission', 300, 150)
    out = node('ShaderNodeOutputMaterial', 500, 150)

    L.new(uv_main.outputs['UV'], tex_main.inputs['Vector'])
    L.new(uv_fix.outputs['UV'], tex_fix.inputs['Vector'])
    L.new(seam_mask.outputs['Fac'], fac.inputs[0])
    L.new(fix_on.outputs['Fac'], fac.inputs[1])
    L.new(fac.outputs['Value'], mix.inputs['Factor'])
    L.new(tex_main.outputs['Color'], mix.inputs[6])
    L.new(tex_fix.outputs['Color'], mix.inputs[7])
    L.new(show_mask.outputs['Fac'], view.inputs['Factor'])
    L.new(mix.outputs[2], view.inputs[6])
    L.new(seam_mask.outputs['Color'], view.inputs[7])
    L.new(view.outputs[2], emit.inputs['Color'])
    L.new(emit.outputs['Emission'], out.inputs['Surface'])
    return mat


def make_baked_material(img):
    mat = bpy.data.materials.new(PREFIX + "Baked")
    nt = mat.node_tree
    nt.nodes.clear()
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.location = (-300, 0)
    emit = nt.nodes.new('ShaderNodeEmission')
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (250, 0)
    nt.links.new(tex.outputs['Color'], emit.inputs['Color'])
    nt.links.new(emit.outputs['Emission'], out.inputs['Surface'])
    return mat


# ---------------------------------------------------------------- scene

def add_object(name, me, mat, x, seam_fix, show_mask):
    obj = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.location.x = x
    obj.rotation_euler.z = math.radians(25)   # turn a bit so side seams face the camera
    obj["seam_fix"] = float(seam_fix)
    obj["show_mask"] = float(show_mask)
    if not me.materials:
        me.materials.append(mat)
    return obj


def add_label(text, x):
    cu = bpy.data.curves.new(PREFIX + "Label_" + text, 'FONT')
    cu.body = text
    cu.align_x = 'CENTER'
    cu.size = 0.28
    obj = bpy.data.objects.new(PREFIX + "Label_" + text, cu)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (x, 0, -1.35)
    obj.rotation_euler = Euler((math.radians(90), 0, 0))
    return obj


def bake_fixed(obj, name, res=IMG_RES):
    """Bakes the blended result back onto the main UVs as a normal texture.
    Reuses the image if it already exists, so re-running overwrites it."""
    scene = bpy.context.scene
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != (res, res):
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, res, res)
    nt = obj.active_material.node_tree
    target = nt.nodes.new('ShaderNodeTexImage')
    target.image = img
    nt.nodes.active = target

    me = obj.data
    me.uv_layers.active = me.uv_layers[MAIN_UV]
    saved_props = {k: obj.get(k) for k in ("seam_fix", "show_mask")}
    obj["seam_fix"], obj["show_mask"] = 1.0, 0.0
    prev_engine = scene.render.engine
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    scene.render.bake.margin = 16
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.bake(type='EMIT', margin=16)
    finally:
        scene.render.engine = prev_engine
        nt.nodes.remove(target)
        for k, v in saved_props.items():
            obj[k] = v
    img.pack()
    return img


def frame_view():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            space.shading.type = 'MATERIAL'
            r3d = space.region_3d
            r3d.view_perspective = 'PERSP'
            r3d.view_rotation = Euler((math.radians(85), 0, 0)).to_quaternion()
            r3d.view_location = (0, 0, -0.2)
            r3d.view_distance = 11.0


def main():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    clear_previous()
    me = make_suzanne()
    stats, island, strips = build_seamfix(me)
    stats["before_fit"] = _summary(check_margins(me, island, strips))
    stats["fit"] = fit_margins(me)
    img = make_painted_image()
    mat = make_material(img)

    # One shared mesh; object custom props switch what the material shows
    add_object("1_Original", me, mat, -4.5, seam_fix=0, show_mask=0)
    add_object("2_Mask", me, mat, -1.5, seam_fix=1, show_mask=1)
    fixed = add_object("3_SeamFixed", me, mat, 1.5, seam_fix=1, show_mask=0)

    baked = bake_fixed(fixed, PREFIX + "Fixed_Bake")
    baked_me = me.copy()
    baked_me.name = PREFIX + "Suzanne_Baked"
    baked_me.materials.clear()
    baked_me.materials.append(make_baked_material(baked))
    add_object("4_Baked", baked_me, None, 4.5, seam_fix=0, show_mask=0)

    for text, x in (("Original", -4.5), ("Seam mask", -1.5), ("Seam fixed", 1.5), ("Baked result", 4.5)):
        add_label(text, x)
    frame_view()
    return stats


# ---------------------------------------------------------------- run on your own mesh

def pick_main_uv(me):
    """The UV map the paint lives on: the render-active one, else the first,
    skipping UV_seamfix."""
    layers = [l for l in me.uv_layers if l.name != FIX_UV]
    if not layers:
        raise RuntimeError("The mesh has no UV map - unwrap it first")
    return next((l.name for l in layers if l.active_render), layers[0].name)


def ensure_seamfix_material(obj):
    """Keeps an existing seam-fix material (re-pointing its UV nodes), otherwise
    builds one around the first image found in the object's current material."""
    mat = obj.active_material
    if mat and mat.node_tree and any(n.label == "A's margin (UV_seamfix)" for n in mat.node_tree.nodes):
        for n in mat.node_tree.nodes:
            if n.type == 'UVMAP' and n.uv_map != FIX_UV:
                n.uv_map = MAIN_UV
        return mat
    img = None
    if mat and mat.node_tree:
        img = next((n.image for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None)
    if img is None:
        raise RuntimeError("No image texture in the object's material - add the painted texture first")
    new = make_material(img, PREFIX + obj.name)
    if obj.material_slots:
        obj.material_slots[obj.active_material_index].material = new
    else:
        obj.data.materials.append(new)
    return new


def _sync_demo_baked(me, img):
    """Demo only: give 4_Baked the new unwrap and the new bake."""
    baked_obj = bpy.data.objects.get(PREFIX + "4_Baked")
    if baked_obj is None or len(baked_obj.data.loops) != len(me.loops):
        return
    co = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers[MAIN_UV].data.foreach_get("uv", co)
    baked_obj.data.uv_layers[0].data.foreach_set("uv", co)
    baked_obj.data.update()
    for n in baked_obj.active_material.node_tree.nodes:
        if n.type == 'TEX_IMAGE':
            n.image = img


def run_on_selected():
    """Seam-fixes the active mesh in place: UV_seamfix + seam_mask, overlap
    checks, blend material, and (BAKE) a baked texture on the main UVs."""
    global MAIN_UV
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    obj = bpy.context.active_object
    if obj is None or obj.type != 'MESH':
        raise RuntimeError("Select the mesh to seam-fix (make it the active object)")
    me = obj.data
    MAIN_UV = pick_main_uv(me)

    stats, island, strips = build_seamfix(me)
    report = check_margins(me, island, strips)
    stats["uv_map"] = MAIN_UV
    stats["check"] = _summary(report)
    if report["main_uv_overlap"]:
        stats["error"] = "Islands overlap in the main UV map - fix the unwrap and run again"
        return stats

    if FIT_MARGINS and report["bad_islands"]:
        stats["fit"] = fit_margins(me)
    else:
        stats["seam_verts_blend_off"] = disable_blend(me, report["unsafe_faces"])
        stats["final"] = _summary(check_margins(me, island, strips))
    me.uv_layers[MAIN_UV].active_render = True

    ensure_seamfix_material(obj)
    if "seam_fix" not in obj:
        obj["seam_fix"] = 1.0
    if "show_mask" not in obj:
        obj["show_mask"] = 0.0
    if BAKE:
        img = bake_fixed(obj, obj.name + "_SeamFixed")
        stats["baked_image"] = img.name
        _sync_demo_baked(me, img)
    bpy.context.view_layer.objects.active = obj
    return stats


if __name__ == "__main__":
    print(main() if RUN == "demo" else run_on_selected())
