"""Seam Fix geometry on uv_normal (never changed before Apply):

    islands      faces joined by edges whose two corners share their UVs
    seams        edges whose corners differ: a source side (the bigger island) and a target
                 side; a cut inside one island is walked so its source stays on one side
    overlaps     islands covering each other's texels - left out of everything, their
                 faces marked in seamfix_overlap
    uv_new       uv_normal with islands shrunk around their centres until each has
                 `need_px` of free texture around it - the bake's target
    uv_fixseam   uv_normal, except near the seams: each target face unfolded into its
                 source's margin, so it reads where the source would continue"""
import bmesh
import numpy as np

UV_SRC = "uv_normal"
UV_NEW = "uv_new"
UV_FIX = "uv_fixseam"
OVERLAP = "seamfix_overlap"


# ---------------------------------------------------------------- rasterising

def raster_tri(p0, p1, p2, res, bary=False):
    """Pixels whose centres fall strictly inside a UV triangle: (xs, ys) or, with bary,
    (xs, ys, w0, w1, w2). Coordinates may fall outside 0..res."""
    pts = np.array([p0, p1, p2], dtype=np.float64) * res
    x0, y0 = np.floor(pts.min(axis=0)).astype(int)
    x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    px, py = xs + 0.5, ys + 0.5
    (ax, ay), (bx, by), (cx, cy) = pts
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(area) < 1e-12:
        empty = xs.ravel()[:0]
        return (empty, empty, empty, empty, empty) if bary else (empty, empty)
    w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area
    w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area
    w2 = 1 - w0 - w1
    inside = (w0 > 0) & (w1 > 0) & (w2 > 0)
    if bary:
        return xs[inside], ys[inside], w0[inside], w1[inside], w2[inside]
    return xs[inside], ys[inside]


def raster_islands(me, uv_name, island, res, skip_faces=()):
    """(island id per pixel, face index per pixel), -1 where no face is. Triangles whose
    pixels land on another island's pixels are counted per face: {face: pixels}."""
    uv = me.uv_layers[uv_name].data
    owner = np.full((res, res), -1, dtype=np.int32)
    face = np.full((res, res), -1, dtype=np.int32)
    clash = {}
    for tri in me.loop_triangles:
        fi = tri.polygon_index
        if fi in skip_faces:
            continue
        xs, ys = raster_tri(*(uv[i].uv for i in tri.loops), res)
        ok = (xs >= 0) & (xs < res) & (ys >= 0) & (ys < res)
        xs, ys = xs[ok], ys[ok]
        isl = island[fi]
        o = owner[ys, xs]
        hit = (o != -1) & (o != isl)
        if hit.any():
            clash[fi] = clash.get(fi, 0) + int(hit.sum())
            for other in np.unique(face[ys[hit], xs[hit]]):
                clash[int(other)] = clash.get(int(other), 0) + 1
        owner[ys, xs] = isl
        face[ys, xs] = fi
    return owner, face, clash


# ---------------------------------------------------------------- islands and seams

def face_uvs(face, uv_layer):
    return {l.vert: l[uv_layer].uv.copy() for l in face.loops}


def uv_seam_edges(bm, uv):
    """Indices of the edges between two faces whose shared corners have different UVs."""
    out = set()
    for e in bm.edges:
        if len(e.link_faces) != 2:
            continue
        fa, fb = e.link_faces
        ua, ub = face_uvs(fa, uv), face_uvs(fb, uv)
        if any((ua[v] - ub[v]).length > 1e-5 for v in e.verts):
            out.add(e.index)
    return out


def find_islands(bm, seams):
    island = {}
    idx = 0
    for start in bm.faces:
        if start.index in island:
            continue
        stack = [start]
        island[start.index] = idx
        while stack:
            f = stack.pop()
            for e in f.edges:
                if e.index in seams or len(e.link_faces) != 2:
                    continue
                for g in e.link_faces:
                    if g.index not in island:
                        island[g.index] = idx
                        stack.append(g)
        idx += 1
    return island


def uvkey(vert_index, uv):
    """A corner's identity on the UV sheet: the vertex plus where it sits. Both sides of a
    cut inside one island get different keys."""
    return (vert_index, round(uv[0], 5), round(uv[1], 5))


def seam_sides(bm, uv, island, seams, rank):
    """[(edge, source face, target face)] for every seam edge. Between two islands the
    higher-ranked one is the source; a cut inside ONE island is walked once and its
    source stays on the side whose UV continues."""
    sides = {}
    same = set()
    for ei in seams:
        e = bm.edges[ei]
        f1, f2 = e.link_faces
        i1, i2 = island[f1.index], island[f2.index]
        if i1 == i2:
            same.add(ei)
        else:
            sides[ei] = (f1, f2) if rank(i1) > rank(i2) else (f2, f1)

    for ei in sorted(same):
        if ei in sides:
            continue
        start = bm.edges[ei]
        sides[ei] = tuple(start.link_faces)
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
    return [(bm.edges[ei], *sides[ei]) for ei in sorted(sides)]


def _c(uv):
    return complex(uv[0], uv[1])


def unfold_transform(a1, a2, b1, b2, a_center, b_center):
    """2D similarity putting B's seam edge (b1, b2) onto A's (a1, a2), B's face landing on
    the far side of the edge - in A's margin."""
    A1, A2, B1, B2 = _c(a1), _c(a2), _c(b1), _c(b2)
    d = A2 - A1
    if abs(B2 - B1) < 1e-12 or abs(d) < 1e-12:
        return None

    def side(p):
        q = p - A1
        return d.real * q.imag - d.imag * q.real

    sa = side(_c(a_center))
    s = d / (B2 - B1)
    t = A1 - s * B1
    if side(s * _c(b_center) + t) * sa < 0:
        return (False, s, t)
    s = d / (B2 - B1).conjugate()      # B's island is mirrored relative to A
    t = A1 - s * B1.conjugate()
    return (True, s, t)


def apply_transform(tr, uv):
    mirror, s, t = tr
    z = _c(uv)
    w = s * (z.conjugate() if mirror else z) + t
    return (w.real, w.imag)


def _center(uvs):
    vals = list(uvs.values())
    return sum(vals[1:], vals[0].copy()) / len(vals)


# ---------------------------------------------------------------- the analysis

class Seams:
    """uv_normal's islands, seams and overlaps, from one pass over the mesh.

    sides: [(src island, tgt island, src uv 1, src uv 2, tgt uv 1, tgt uv 2,
             src key 1, src key 2, tgt key 1, tgt key 2)] for the seams kept
    left_out: islands that overlap others (by more than `tol_px` at 2048)"""

    def __init__(self, me, tol_px=4, res=2048):
        me.calc_loop_triangles()
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        uv = bm.loops.layers.uv[UV_SRC]
        self.seam_edges = uv_seam_edges(bm, uv)
        self.island = find_islands(bm, self.seam_edges)
        self.n_islands = max(self.island.values(), default=-1) + 1
        size = np.bincount(np.fromiter(self.island.values(), dtype=np.int64),
                           minlength=self.n_islands)
        self.size = size

        _owner, _face, clash = raster_islands(me, UV_SRC, self.island, res)
        per_island = {}
        for fi, n in clash.items():
            per_island[self.island[fi]] = per_island.get(self.island[fi], 0) + n
        self.left_out = {i for i, n in per_island.items() if n > tol_px}
        self.overlap_faces = {fi for fi in clash if self.island[fi] in self.left_out}

        def rank(i):
            return (int(size[i]), -i)

        self.rank = rank
        self.sides = []
        self._bm_sides = []
        for e, a, b in seam_sides(bm, uv, self.island, self.seam_edges, rank):
            ia, ib = self.island[a.index], self.island[b.index]
            if ia in self.left_out or ib in self.left_out:
                continue
            ua, ub = face_uvs(a, uv), face_uvs(b, uv)
            v1, v2 = e.verts
            self.sides.append((ia, ib, tuple(ua[v1]), tuple(ua[v2]), tuple(ub[v1]), tuple(ub[v2]),
                               uvkey(v1.index, ua[v1]), uvkey(v2.index, ua[v2]),
                               uvkey(v1.index, ub[v1]), uvkey(v2.index, ub[v2])))
            self._bm_sides.append((e.index, a.index, b.index))
        bm.free()

    def stats(self):
        return {"islands": self.n_islands, "seam_edges": len(self.seam_edges),
                "seam_edges_used": len(self.sides), "left_out_islands": len(self.left_out),
                "overlap_faces": len(self.overlap_faces)}


def write_overlap_attr(me, faces):
    """seamfix_overlap = 1 on the given faces (shown red in Mask mode)."""
    attr = me.attributes.get(OVERLAP)
    if attr is None:
        attr = me.attributes.new(OVERLAP, 'FLOAT', 'FACE')
    vals = np.zeros(len(me.polygons), dtype=np.float32)
    if faces:
        vals[list(faces)] = 1.0
    attr.data.foreach_set("value", vals)


def ensure_uv(me, name, src=UV_SRC):
    """`name` as a fresh copy of `src` (created, or overwritten when it exists)."""
    layer = me.uv_layers.get(name) or me.uv_layers.new(name=name, do_init=False)
    co = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers[src].data.foreach_get("uv", co)
    layer.data.foreach_set("uv", co)
    return layer


# ---------------------------------------------------------------- uv_fixseam

def build_fixseam(me, seams):
    """Writes uv_fixseam: uv_normal everywhere, except on the target faces of each seam,
    which are unfolded into their source island's margin. Target corners on the seam take
    the source's UV; the other corners are averaged over the faces sharing them.
    Returns [(face index, source island)] - the strip faces."""
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    uv = bm.loops.layers.uv[UV_SRC]
    fix = bm.loops.layers.uv.get(UV_FIX) or bm.loops.layers.uv.new(UV_FIX)
    island, rank = seams.island, seams.rank

    target_src = {}   # target corner key -> (source island, source UV)
    edge_opts = {}    # target face -> [(source island, transform, {vert: source UV})]
    vert_opts = {}    # target corner key -> [same]
    for ei, ai, bi in seams._bm_sides:
        e, a, b = bm.edges[ei], bm.faces[ai], bm.faces[bi]
        src = island[a.index]
        ua, ub = face_uvs(a, uv), face_uvs(b, uv)
        v1, v2 = e.verts
        tr = unfold_transform(ua[v1], ua[v2], ub[v1], ub[v2], _center(ua), _center(ub))
        if tr is None:
            continue
        opt = (src, tr, {v.index: ua[v] for v in e.verts})
        edge_opts.setdefault(b.index, []).append(opt)
        for v in e.verts:
            key = uvkey(v.index, ub[v])
            vert_opts.setdefault(key, []).append(opt)
            if key not in target_src or rank(src) > rank(target_src[key][0]):
                target_src[key] = (src, ua[v])

    def key_of(loop):
        return uvkey(loop.vert.index, loop[uv].uv)

    face_opt = {}
    inner = {}        # (corner key, source island) -> [sum_u, sum_v, count]
    for f in bm.faces:
        keys = [key_of(l) for l in f.loops if key_of(l) in target_src]
        if not keys:
            continue
        options = edge_opts.get(f.index) or [o for k in keys for o in vert_opts.get(k, [])]
        if not options:
            continue
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

    for f in bm.faces:
        opt = face_opt.get(f.index)
        for l in f.loops:
            if opt is None:
                l[fix].uv = l[uv].uv
                continue
            key = key_of(l)
            src, tr, seam_uv = opt
            if key in target_src:
                if l.vert.index in seam_uv:
                    l[fix].uv = seam_uv[l.vert.index]
                elif target_src[key][0] == src:
                    l[fix].uv = target_src[key][1]
                else:   # this corner copies from another source: stay in this face's frame
                    l[fix].uv = apply_transform(tr, l[uv].uv)
            elif (key, src) in inner:
                su, sv, n = inner[(key, src)]
                l[fix].uv = (su / n, sv / n)
            else:
                l[fix].uv = l[uv].uv

    bm.to_mesh(me)
    bm.free()
    return [(fi, o[0]) for fi, o in face_opt.items()]


# ---------------------------------------------------------------- uv_new: room around islands

def _clearance(owner, n_islands, max_steps):
    """Per island: pixels it can grow (all islands growing at once) before it meets
    another island or the texture's edge - half the free gap around it."""
    clear = np.full(n_islands, np.inf)
    lab = np.pad(owner, 1, constant_values=-2)      # -2: outside the 0-1 square
    for step in range(1, max_steps + 1):
        grown = lab.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(lab, (dy, dx), (0, 1))
            free = (grown == -1) & (sh >= 0)
            grown[free] = sh[free]
            meet = (lab >= 0) & (sh != lab) & ((sh >= 0) | (sh == -2))
            for i in np.unique(lab[meet]):
                if clear[i] == np.inf:
                    clear[i] = step - 1
        lab = grown
    return clear


def shrink_new(me, seams, need_px, tex_res, res=2048, passes=4):
    """Shrinks islands of uv_new (a fresh copy of uv_normal) around their bounding-box
    centres until each has `need_px` texture pixels of free space around it. Islands left
    out (overlaps) are not touched. Returns {"shrunk": n, "min_scale": s, "short": n}."""
    layer = ensure_uv(me, UV_NEW)
    island = seams.island
    co = np.empty((len(me.loops), 2), dtype=np.float32)
    layer.data.foreach_get("uv", co.ravel())
    loop_island = np.empty(len(me.loops), dtype=np.int64)
    starts = np.empty(len(me.polygons), dtype=np.int64)
    totals = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", totals)
    face_isl = np.array([island[i] for i in range(len(me.polygons))], dtype=np.int64)
    for fi in range(len(me.polygons)):
        loop_island[starts[fi]:starts[fi] + totals[fi]] = face_isl[fi]
    loops_of = {i: np.flatnonzero(loop_island == i) for i in range(seams.n_islands)}
    need = need_px * res / tex_res           # in raster pixels
    steps = int(np.ceil(need)) + 1
    scale = np.ones(seams.n_islands)
    for _ in range(passes):
        owner, _f, _c = raster_islands(me, UV_NEW, island, res)
        clear = _clearance(owner, seams.n_islands, steps)
        short = [i for i in range(seams.n_islands)
                 if i not in seams.left_out and clear[i] < need and len(loops_of[i])]
        if not short:
            break
        for i in short:
            idx = loops_of[i]
            lo, hi = co[idx].min(axis=0), co[idx].max(axis=0)
            half = max(float(min(hi - lo)) / 2 * res, 1e-6)
            s = max(0.5, 1 - (need - clear[i] + 0.5) / half)
            mid = (lo + hi) / 2
            co[idx] = mid + (co[idx] - mid) * s
            scale[i] *= s
        layer.data.foreach_set("uv", co.ravel())
    owner, _f, _c = raster_islands(me, UV_NEW, island, res)
    clear = _clearance(owner, seams.n_islands, steps)
    short = sum(1 for i in range(seams.n_islands)
                if i not in seams.left_out and clear[i] < need and i in loops_of)
    return {"shrunk": int((scale < 1).sum()), "min_scale": round(float(scale.min()), 3),
            "short": short}
