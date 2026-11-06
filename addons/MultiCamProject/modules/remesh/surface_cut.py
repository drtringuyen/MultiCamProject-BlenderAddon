"""Surface Cut (K in Edit Mode; Blender's Knife moves to Shift+K): a cut that walks over the
surface, so a closed loop always closes and splits its piece into one clean island.

Blender's Knife cuts what lies under a 2D line in the view, so where the surface turns away the
line slips onto another surface and the loop opens. Surface Cut walks from face to face across
shared edges instead, so the path cannot leave the surface:

    click        a point on the mesh, snapped like the Knife: to a vertex near the mouse, else
                 to the nearest point of an edge near it. The first click locks the piece (the
                 connected part of the mesh) it lands on; later clicks land on that piece too -
                 looked for behind other leaves along the ray, else its nearest point (orange)
    close        click the first point (green) or double click;  Enter: an open line
    Shift        show the closing preview (grey): the mouse back to the first point
    Backspace / Ctrl+Z   drop the last point;  Esc: cancel
    red          a part of the path this view cannot see (behind other faces or on faces turned
                 away): turn the view to check it

Between two points the path follows a plane through both that stands on the surface (along the
surface's normals at the two points; where they face away from each other - the top and the
underside of a leaf - the plane wraps around the leaf's edge): straight on the surface, not on
screen. Several such planes are tried and the shortest path that arrives is kept; where none
arrives the path is steered (re-aimed at the next point in every face, following a bending leaf).
Where it meets the piece's open rim it follows the rim (cyan). As a last resort it runs along the
mesh's own edges (orange), so the loop always closes.

The cut splits the crossed edges and the faces between them; a click inside a face becomes a
vertex of the cut (an open line's ends inside a face are left out). Then, shared with PolyCut
(remembered until the next cut, so switching a toggle afterwards applies to this cut):
    the outline           mcp_cut_edges (with Close Border also the rim stretch, mcp_cut_rim)
    the island            mcp_cut_inside: the smaller side of a closed cut; Invert: the rest of
                          the piece (mcp_cut_piece)
    Select Edges / Faces, Invert, Auto Seam, Close Border - else the Set Seam / No Seam popup.
One undo step.
"""
import heapq
import math
import time

import bmesh
import bpy
import gpu
import numpy as np
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import poly_cut as pc

COLOR = (1.0, 0.85, 0.2, 1.0)           # the path (on the plane)
RIM_COLOR = (0.3, 0.8, 1.0, 1.0)        # along the piece's open rim
EDGE_COLOR = (1.0, 0.5, 0.1, 1.0)       # fallback: along the mesh's edges; also a moved point
HIDDEN_COLOR = (1.0, 0.15, 0.15, 1.0)   # a part this view cannot see
HINT_COLOR = (0.6, 0.6, 0.6, 0.35)      # the closing preview: the mouse back to the first point
SNAP_COLOR = (0.3, 1.0, 0.9, 1.0)       # the cursor snapped to a vertex / an edge
CLOSE_COLOR = (0.3, 1.0, 0.4, 1.0)
VERT_PX, EDGE_PX = 10.0, 6.0            # snapping distance on screen (x UI scale)
POINT_PX = 12
DOUBLE_SECONDS, DOUBLE_PX = 0.45, 6
DOT_PX, LAST_PX, HOVER_PX = 4.0, 6.0, 9.0
MAX_STEPS = 50000
RAY_LAYERS = 32
NAVIGATE = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
            'MOUSEROTATE', 'MOUSESMARTZOOM'}


def _ui():
    return bpy.context.preferences.system.ui_scale


# ---------------------------------------------------------------- the mesh

class Surface:
    """The edit mesh: pieces, ray casts, the walk (read only) and the cut. Local coordinates.

    A path is a list of stations (co, elem): elem is the edge a crossing lies on, an existing
    vertex, or None for a clicked point (the segment's two ends)."""

    def __init__(self, bm):
        self.bm = bm
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        self.visible = [f for f in bm.faces if not f.hide]
        self.tree = BVHTree.FromPolygons([v.co.copy() for v in bm.verts],
                                         [[v.index for v in f.verts] for f in self.visible],
                                         all_triangles=False)
        self.piece = self._pieces()
        self.locked = None
        self.piece_tree = None
        self.piece_faces = None

    def _pieces(self):
        ids = {}
        n = 0
        for f in self.visible:
            if f.index in ids:
                continue
            ids[f.index] = n
            stack = [f]
            while stack:
                g = stack.pop()
                for e in g.edges:
                    for h in e.link_faces:
                        if not h.hide and h.index not in ids:
                            ids[h.index] = n
                            stack.append(h)
            n += 1
        return ids

    def lock(self, face):
        self.locked = self.piece[face.index]
        self.piece_faces = [f for f in self.visible if self.piece[f.index] == self.locked]
        self.piece_tree = BVHTree.FromPolygons([v.co.copy() for v in self.bm.verts],
                                               [[v.index for v in f.verts] for f in self.piece_faces],
                                               all_triangles=False)

    def in_piece(self, f):
        return not f.hide and self.piece.get(f.index) == self.locked

    def pick(self, origin, direction, near):
        """(location, face, moved) for a ray (local), or None. Before the lock: the first hit.
        After: the first hit on the locked piece along the ray, else the piece's nearest point
        to `near` (a point on the ray)."""
        start = origin
        first = True
        for _ in range(RAY_LAYERS):
            loc, _n, i, _d = self.tree.ray_cast(start, direction)
            if loc is None:
                break
            f = self.visible[i]
            if self.locked is None or self.in_piece(f):
                return loc, f, not first and self.locked is not None
            first = False
            start = loc + direction * 1e-5
        if self.locked is None:
            return None
        loc, _n, i, _d = self.piece_tree.find_nearest(near)
        if loc is None:
            return None
        return loc, self.piece_faces[i], True

    # ------------------------------------------------------------ the walk
    @staticmethod
    def _cross(e, p, n):
        """Where the plane (point p, normal n) crosses edge e, or None. A vertex on the plane
        counts as in front (no crossing is ever seen twice or missed)."""
        d0 = (e.verts[0].co - p).dot(n)
        d1 = (e.verts[1].co - p).dot(n)
        if (d0 >= 0.0) == (d1 >= 0.0):
            return None
        return e.verts[0].co.lerp(e.verts[1].co, d0 / (d0 - d1))

    def _walk(self, p, fp, q, fq, plane_n, heading):
        """From p (in face fp) along the plane, leaving fp toward `heading`. Returns (stations,
        how, edge): how = 'ARRIVED' (in fq, q appended), 'RIM' (the edge is on the open rim),
        'LOST' (no way on)."""
        path = [(p, None)]
        if fp == fq:
            return path + [(q, None)], 'ARRIVED', None
        exits = [(e, c) for e in fp.edges if (c := self._cross(e, p, plane_n)) is not None]
        exits = [(e, c) for e, c in exits if (c - p).dot(heading) > 0.0]
        if not exits:
            return path, 'LOST', None
        e, c = max(exits, key=lambda ec: (ec[1] - p).dot(heading))
        f, prev = fp, p
        seen = set()
        for _ in range(MAX_STEPS):
            path.append((c, e))
            nxt = [g for g in e.link_faces if g != f and self.in_piece(g)]
            if not nxt:
                return path, 'RIM', e
            f = nxt[0]
            if f == fq:
                return path + [(q, None)], 'ARRIVED', None
            if (e.index, f.index) in seen:
                return path, 'LOST', None
            seen.add((e.index, f.index))
            on = [(e2, c2) for e2 in f.edges if e2 != e
                  and (c2 := self._cross(e2, p, plane_n)) is not None]
            if not on:
                return path, 'LOST', None
            go = (c - prev) if (c - prev).length_squared > 0 else heading
            prev = c
            e, c = max(on, key=lambda ec: (ec[1] - c).dot(go))
        return path, 'LOST', None

    def _steer(self, p, fp, q, fq):
        """Toward q along the surface, re-aimed in every face: the plane through the current
        point, q and the face's normal. Follows a leaf as it bends, where one fixed plane leaves
        it. Returns (stations, how, edge) as _walk."""
        path = [(p, None)]
        if fp == fq:
            return path + [(q, None)], 'ARRIVED', None
        f, c, came = fp, p, None
        seen = set()
        for _ in range(MAX_STEPS):
            aim = q - c
            along = aim - f.normal * aim.dot(f.normal)      # the aim along this face
            n = aim.cross(f.normal)
            if n.length_squared < 1e-20:
                return path, 'LOST', None
            n.normalize()
            on = [(e, x) for e in f.edges if e != came and (x := self._cross(e, c, n)) is not None]
            if came is None:            # leaving the first face: the side toward q
                on = [(e, x) for e, x in on if (x - c).dot(along) > 0.0] or on
            if not on:
                return path, 'LOST', None
            e, x = max(on, key=lambda ex: (ex[1] - c).dot(along))
            path.append((x, e))
            nxt = [g for g in e.link_faces if g != f and self.in_piece(g)]
            if not nxt:
                return path, 'RIM', e
            if (e.index, nxt[0].index) in seen:
                return path, 'LOST', None
            seen.add((e.index, nxt[0].index))
            f, c, came = nxt[0], x, e
            if f == fq:
                return path + [(q, None)], 'ARRIVED', None
        return path, 'LOST', None

    def _rim_path(self, e_a, c_a, e_b, c_b):
        """Along the piece's open rim from the crossing c_a (edge e_a) to c_b (edge e_b): the
        stations, or None when they are on different rims."""
        if e_a == e_b:
            return [(c_a, e_a), (c_b, e_b)]

        def rim(e):
            return len([g for g in e.link_faces if self.in_piece(g)]) == 1

        goal = {v.index for v in e_b.verts}
        dist, prev, heap = {}, {}, []
        for v in e_a.verts:
            d = (v.co - c_a).length
            dist[v.index] = d
            heapq.heappush(heap, (d, v.index))
        verts = self.bm.verts
        end = None
        while heap:
            d, i = heapq.heappop(heap)
            if d > dist.get(i, float('inf')):
                continue
            if i in goal:
                end = i
                break
            for e in verts[i].link_edges:
                if not rim(e) or e == e_a or e == e_b:
                    continue
                j = e.other_vert(verts[i]).index
                nd = d + e.calc_length()
                if nd < dist.get(j, float('inf')):
                    dist[j], prev[j] = nd, i
                    heapq.heappush(heap, (nd, j))
        if end is None:
            return None
        chain = [end]
        while chain[-1] in prev:
            chain.append(prev[chain[-1]])
        return ([(c_a, e_a)] + [(verts[i].co.copy(), verts[i]) for i in reversed(chain)]
                + [(c_b, e_b)])

    def _edge_path(self, p, fp, q, fq):
        """Fallback: along the mesh's own edges, nearest vertex of fp to nearest of fq."""
        a = min(fp.verts, key=lambda v: (v.co - p).length)
        b = min(fq.verts, key=lambda v: (v.co - q).length)
        dist, prev, heap = {a.index: 0.0}, {}, [(0.0, a.index)]
        verts = self.bm.verts
        while heap:
            d, i = heapq.heappop(heap)
            if i == b.index:
                break
            if d > dist[i]:
                continue
            for e in verts[i].link_edges:
                if not any(self.in_piece(g) for g in e.link_faces):
                    continue
                j = e.other_vert(verts[i]).index
                nd = d + e.calc_length()
                if nd < dist.get(j, float('inf')):
                    dist[j], prev[j] = nd, i
                    heapq.heappush(heap, (nd, j))
        if b.index not in dist:
            return None
        chain = [b.index]
        while chain[-1] in prev:
            chain.append(prev[chain[-1]])
        return ([(p, None)] + [(verts[i].co.copy(), verts[i]) for i in reversed(chain)]
                + [(q, None)])

    @staticmethod
    def _planes(p, fp, q, fq):
        """Upright planes through p and q to try, best first: the two surface normals together
        (or, when the points face away from each other - top and underside of a leaf - their
        difference, so the plane wraps around the leaf's edge), then each normal alone."""
        d = q - p
        np_, nq = fp.normal, fq.normal
        joint = np_ + nq if np_.dot(nq) >= 0.0 else np_ - nq
        out = []
        for n in (joint, np_, nq, np_.orthogonal()):
            pn = d.cross(n)
            if pn.length_squared > 1e-16 * max(d.length_squared, 1e-30):
                pn.normalize()
                if all(abs(pn.dot(o)) < 0.999 for o in out):
                    out.append(pn)
        return out

    @staticmethod
    def _length(path):
        return sum((b[0] - a[0]).length for a, b in zip(path, path[1:]))

    def segment(self, a, b):
        """The path between two points (location, face): [(kind, stations)...] with kind 'CUT',
        'RIM' or 'EDGES'. The shortest plane path that arrives, else the steered one, else via
        the open rim, else along the mesh's edges - a detour (twice the edge route) is skipped."""
        p, fp = a
        q, fq = b
        if (q - p).length_squared < 1e-16:
            return []
        found, rims = [], []
        for plane_n in self._planes(p, fp, q, fq):
            fwd, how, rim_a = self._walk(p, fp, q, fq, plane_n, q - p)
            if how == 'ARRIVED':
                found.append(fwd)
                continue
            back, how_b, rim_b = self._walk(q, fq, p, fp, plane_n, p - q)
            if how_b == 'ARRIVED':
                found.append(list(reversed(back)))
                continue
            if how == 'RIM' and how_b == 'RIM':
                rims.append((fwd, rim_a, back, rim_b))
            other, how_o, _rim = self._walk(p, fp, q, fq, plane_n, p - q)   # the other way round
            if how_o == 'ARRIVED':
                found.append(other)
        edges = None
        for candidates in (found, None):
            if candidates is None:      # steered: re-aimed toward q in every face
                st, how_s, rim_s = self._steer(p, fp, q, fq)
                st_b, how_sb, rim_sb = self._steer(q, fq, p, fp)
                candidates = ([st] if how_s == 'ARRIVED' else []) + \
                             ([list(reversed(st_b))] if how_sb == 'ARRIVED' else [])
                if how_s == 'RIM' and how_sb == 'RIM':
                    rims.append((st, rim_s, st_b, rim_sb))
            if candidates:
                best = min(candidates, key=self._length)
                if self._length(best) <= 3.0 * (q - p).length:
                    return [('CUT', best)]
                if edges is None:
                    edges = self._edge_path(p, fp, q, fq)       # a detour (half round the plant)?
                if edges is None or self._length(best) <= 2.0 * self._length(edges):
                    return [('CUT', best)]
        for fwd, rim_a, back, rim_b in rims:
            along = self._rim_path(rim_a, fwd[-1][0], rim_b, back[-1][0])
            if along is not None:
                return [('CUT', fwd), ('RIM', along), ('CUT', list(reversed(back)))]
        if edges is None:
            edges = self._edge_path(p, fp, q, fq)
        return [('EDGES', edges)] if edges else []

    # ------------------------------------------------------------ the cut
    def cut(self, points, segments, closed):
        """Cut the path into the mesh. points: [(co, face, moved, snap, elem)]; segments[i] is
        the path from point i to the next (closed: the last one back to the first). Returns
        (cut edges, rim edges, the faces inside or None, the piece's faces, gaps)."""
        bm = self.bm
        n = len(points)
        # the stations in order, a clicked point as ('P', i)
        stream = []
        for i, seg in enumerate(segments):
            stations = [(kind, st) for kind, sts in seg for st in sts]
            for k, (kind, (co, el)) in enumerate(stations):
                ref = el if el is not None else ('P', i if k == 0 else (i + 1) % n)
                if stream and stream[-1][2] is ref or (stream and isinstance(ref, tuple)
                                                       and stream[-1][2] == ref):
                    continue
                stream.append((kind, co, ref))
        if closed and len(stream) > 1 and stream[-1][2] == stream[0][2]:
            stream.pop()
        # what each station is in the mesh: a vertex, a point on an edge, a point in a face
        items = []
        for kind, co, ref in stream:
            if isinstance(ref, tuple):
                pco, pface, _moved, snap, elem = points[ref[1]]
                if snap == 'VERT':
                    items.append([kind, 'V', elem, None])
                elif snap == 'EDGE':
                    items.append([kind, 'E', elem, pco])
                else:
                    items.append([kind, 'F', pface, pco])
            elif isinstance(ref, bmesh.types.BMVert):
                items.append([kind, 'V', ref, None])
            else:
                items.append([kind, 'E', ref, co])
        # split the crossed edges (all crossings of one edge at once, in order along it)
        by_edge = {}
        for idx, it in enumerate(items):
            if it[1] == 'E':
                e, co = it[2], it[3]
                v0, v1 = e.verts
                d = v1.co - v0.co
                t = 0.0 if d.length_squared == 0 else max(0.0, min(1.0, (co - v0.co).dot(d) / d.length_squared))
                by_edge.setdefault(e, []).append((t, idx))
        for e, lst in by_edge.items():
            v0, v1 = e.verts[0], e.verts[1]
            a0, a1 = v0.co.copy(), v1.co.copy()
            lst.sort(key=lambda x: x[0])
            cur, t_prev, made = v0, 0.0, []
            for t, idx in lst:
                if t <= 1e-6:
                    vert = v0
                elif t >= 1.0 - 1e-6:
                    vert = v1
                elif made and t - made[-1][0] <= 1e-6:
                    vert = made[-1][1]
                else:
                    rest = next(x for x in cur.link_edges if x.other_vert(cur) == v1)
                    _ne, vert = bmesh.utils.edge_split(rest, cur, (t - t_prev) / (1.0 - t_prev))
                    vert.co = a0.lerp(a1, t)
                    made.append((t, vert))
                    cur, t_prev = vert, t
                items[idx][1], items[idx][2] = 'V', vert
        # join the vertices in order: through a face (with the clicks inside it on the way)
        chain = [(it[0], it[2]) if it[1] == 'V' else (it[0], ('F', it[3])) for it in items]
        if closed:
            first = next((i for i, (_k, x) in enumerate(chain) if not isinstance(x, tuple)), None)
            if first is None:
                return set(), set(), None, set(), 1
            chain = chain[first:] + chain[:first] + [chain[first]]
        else:
            while chain and isinstance(chain[0][1], tuple):
                chain.pop(0)
            while chain and isinstance(chain[-1][1], tuple):
                chain.pop()
        cut, rim, gaps = set(), set(), 0
        prev, coords = None, []
        for kind, x in chain:
            if isinstance(x, tuple):
                coords.append(x[1])
                continue
            if prev is None or (x is prev and not coords):
                prev, coords = x, []
                continue
            edges = self._join(prev, x, coords)
            if edges is None:
                gaps += 1
            elif kind == 'RIM' and not coords:
                rim.update(edges)
            else:
                cut.update(edges)
            prev, coords = x, []
        cut -= rim
        inside, piece = self._island(cut, closed)
        if inside:      # the outline is exactly the island's border: no spur, no stray edge
            border = {e for f in inside for e in f.edges
                      if any(g not in inside for g in e.link_faces) or len(e.link_faces) == 1}
            rim = {e for e in border if len([g for g in e.link_faces if not g.hide]) == 1}
            cut = border - rim
        return cut, rim, inside, piece, gaps

    def _join(self, a, b, coords):
        """The edges from vertex a to b: an existing edge, or a new one splitting the face they
        share (through the points `coords` inside it). Where they no longer share a face (an
        earlier split of the same spot), the few existing edges between them. None: no way."""
        bm = self.bm
        if a is b:                  # back at the same vertex (a click inside a face beside it)
            return []
        if not coords:
            e = bm.edges.get((a, b))
            if e is not None:
                return [e]
        faces = [f for f in a.link_faces if b in f.verts]
        if not faces:
            return self._bridge(a, b)
        f = faces[0]
        if coords and len(faces) > 1:
            inside = [g for g in faces if bmesh.geometry.intersect_face_point(g, coords[0])]
            f = inside[0] if inside else f
        try:
            bmesh.utils.face_split(f, a, b, coords=coords)
        except (ValueError, RuntimeError):
            return None
        verts = [a]
        for co in coords:       # the new vertices, in order from a
            cur = verts[-1]
            near = [e.other_vert(cur) for e in cur.link_edges]
            verts.append(min(near, key=lambda v: (v.co - co).length))
        verts.append(b)
        edges = [bm.edges.get((u, v)) for u, v in zip(verts, verts[1:])]
        return None if any(e is None for e in edges) else edges

    def _bridge(self, a, b, hops=8):
        """The shortest run of existing edges from a to b (at most `hops` edges), or None."""
        dist, prev, heap = {a: 0.0}, {}, [(0.0, 0, id(a), a)]
        steps = {a: 0}
        while heap:
            d, k, _i, v = heapq.heappop(heap)
            if v is b:
                break
            if d > dist[v] or k >= hops:
                continue
            for e in v.link_edges:
                w = e.other_vert(v)
                nd = d + e.calc_length()
                if nd < dist.get(w, float('inf')):
                    dist[w], prev[w], steps[w] = nd, (v, e), k + 1
                    heapq.heappush(heap, (nd, k + 1, id(w), w))
        if b not in prev:
            return None
        edges, v = [], b
        while v is not a:
            v, e = prev[v]
            edges.append(e)
        return edges[::-1]

    def _island(self, cut, closed):
        """(the faces inside, the piece) after the cut: the piece = everything joined to the cut;
        a closed cut splits it - inside is the smaller side by area. None when it does not."""
        start = [f for e in cut for f in e.link_faces]
        if not start:
            return None, set()
        piece, stack = set(start), list(start)
        while stack:
            f = stack.pop()
            for e in f.edges:
                for g in e.link_faces:
                    if g not in piece and not g.hide:
                        piece.add(g)
                        stack.append(g)
        if not closed:
            return None, piece
        sides, owner = [], {}
        for f0 in start:
            if f0 in owner:
                continue
            side, stack = {f0}, [f0]
            owner[f0] = len(sides)
            while stack:
                f = stack.pop()
                for e in f.edges:
                    if e in cut:
                        continue
                    for g in e.link_faces:
                        if g not in owner and g in piece:
                            owner[g] = len(sides)
                            side.add(g)
                            stack.append(g)
            sides.append(side)
        if len(sides) < 2:
            return None, piece          # the loop does not split the piece
        # the island is a side the cut runs all along (not a pocket where the path touched
        # itself), the smaller one by area
        along = [sum(1 for f in side for e in f.edges if e in cut) for side in sides]
        best = max(along)
        cands = [s for s, n in zip(sides, along) if n >= 0.8 * best]
        if len(cands) < 2 and len(sides) >= 2:
            cands = [s for s, n in zip(sides, along) if n >= 0.5 * best] or cands
        return min(cands, key=lambda s: sum(f.calc_area() for f in s)), piece


# ---------------------------------------------------------------- the operator

class MULTICAMPROJECT_OT_SurfaceCut(bpy.types.Operator):
    """Surface Cut (K): click points on the mesh; the cut walks over the surface, so a closed loop
    always closes into one island. First point / double click: close, Enter: open line, Shift:
    the closing preview, Backspace / Ctrl+Z: drop the last point, Esc: cancel"""
    bl_idname = "multicamproject.surface_cut"
    bl_label = "Surface Cut"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'
                and context.area is not None and context.area.type == 'VIEW_3D')

    # ------------------------------------------------------------ points
    def _ray(self, p):
        o = view3d_utils.region_2d_to_origin_3d(self._win, self._rv3d, p)
        d = view3d_utils.region_2d_to_vector_3d(self._win, self._rv3d, p).normalized()
        lo = self._inv @ o
        ld = (self._inv.to_3x3() @ d).normalized()
        depth = self._mw @ self._points[-1][0] if self._points else self._rv3d.view_location
        near_w = view3d_utils.region_2d_to_location_3d(self._win, self._rv3d, p, depth)
        return lo, ld, self._inv @ near_w

    def _pick(self, p):
        """(location, face, moved onto the piece, 'VERT' / 'EDGE' / 'FACE', the vertex / edge)
        under the region pixel p, or None. Snapped like the Knife."""
        hit = self._surface.pick(*self._ray(p))
        if hit is None:
            return None
        loc, f, moved = hit
        k = _ui()
        m = Vector(p)
        best = None
        for v in f.verts:
            q = self._screen(v.co)
            if q is not None and (d := (Vector(q) - m).length) <= VERT_PX * k:
                if best is None or d < best[0]:
                    best = (d, v)
        if best is not None:
            return best[1].co.copy(), f, moved, 'VERT', best[1]
        for e in f.edges:
            a, b = self._screen(e.verts[0].co), self._screen(e.verts[1].co)
            if a is None or b is None:
                continue
            a, b = Vector(a), Vector(b)
            ab = b - a
            t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (m - a).dot(ab) / ab.length_squared))
            d = (a + ab * t - m).length
            if d <= EDGE_PX * k and (best is None or d < best[0]):
                best = (d, e, t)
        if best is not None:
            e, t = best[1], best[2]
            return e.verts[0].co.lerp(e.verts[1].co, t), f, moved, 'EDGE', e
        return loc, f, moved, 'FACE', None

    def _screen(self, co):
        q = view3d_utils.location_3d_to_region_2d(self._win, self._rv3d, self._mw @ co)
        return None if q is None else (q.x, q.y)

    def _under_mouse(self):
        best, found = POINT_PX * _ui(), None
        for i, pt in enumerate(self._points):
            p = self._screen(pt[0])
            if p is not None:
                d = (Vector(p) - Vector(self._mouse)).length
                if d < best:
                    best, found = d, i
        return found

    def _add(self, p):
        hit = self._pick(p)
        if hit is None:
            return False
        if self._surface.locked is None:
            self._surface.lock(hit[1])
        self._points.append((hit[0].copy(), hit[1], hit[2], hit[3], hit[4]))
        if len(self._points) >= 2:
            a, b = self._points[-2], self._points[-1]
            self._segments.append(self._surface.segment((a[0], a[1]), (b[0], b[1])))
        return True

    def _hover(self):
        """The live segment: last point -> the mouse (and, Shift held, the mouse -> the first)."""
        key = (self._mouse, self._show_close, tuple(tuple(r) for r in self._rv3d.perspective_matrix))
        if key == self._hover_key:
            return self._hover_cache
        self._hover_key = key
        live, closing = [], []
        hit = self._pick(self._mouse)       # where the mouse lands, before the first click too
        if self._points and hit is not None:
            last, first = self._points[-1], self._points[0]
            live = self._surface.segment((last[0], last[1]), (hit[0], hit[1]))
            if len(self._points) >= 2 and self._show_close:
                closing = self._surface.segment((hit[0], hit[1]), (first[0], first[1]))
        self._hover_cache = (live, closing, hit)
        return self._hover_cache

    # ------------------------------------------------------------ drawing
    def _view(self):
        """The eye in local space: (perspective, eye point, view direction)."""
        vi = self._rv3d.view_matrix.inverted()
        look = (self._inv.to_3x3() @ (vi.to_3x3() @ Vector((0.0, 0.0, -1.0)))).normalized()
        return self._rv3d.is_perspective, self._inv @ vi.translation, look

    def _hidden(self, a, b, view):
        """Can this view not see the piece a -> b (local)? Behind other faces, or on a face
        turned away from the view."""
        persp, eye, look = view
        m = (a + b) * 0.5
        if persp:
            d = m - eye
            dist = d.length
            if dist < 1e-12:
                return False
            d /= dist
            start = eye
        else:
            d = look
            dist = 10.0 * self._size
            start = m - d * dist
        loc, normal, _i, hit_dist = self._surface.tree.ray_cast(start, d)
        if loc is None:
            return False
        if hit_dist < dist - self._tol:
            return True                         # something in front of it
        return normal.dot(d) > 0.0              # the face turns away from the view

    def _lines(self, parts, view=None):
        """World line points per kind; with a view, the pieces it cannot see under 'HIDDEN'."""
        out = {'CUT': [], 'RIM': [], 'EDGES': [], 'HIDDEN': []}
        for kind, stations in parts:
            pts = [co for co, _el in stations]
            for a, b in zip(pts, pts[1:]):
                key = 'HIDDEN' if view is not None and self._hidden(a, b, view) else kind
                out[key] += [tuple(self._mw @ a), tuple(self._mw @ b)]
        return out

    def _draw_3d(self, _context):
        if bpy.context.region != self._win:
            return
        live, closing, _hit = self._hover()
        parts = [s for seg in self._segments for s in seg] + live
        lines = self._lines(parts, self._view())
        hint = self._lines(closing)
        hint_pts = hint['CUT'] + hint['RIM'] + hint['EDGES']
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        for pts, width, color in ((hint_pts, 1.0, HINT_COLOR),
                                  (lines['EDGES'], 3.0, EDGE_COLOR),
                                  (lines['RIM'], 3.0, RIM_COLOR),
                                  (lines['CUT'], 3.0, COLOR),
                                  (lines['HIDDEN'], 3.0, HIDDEN_COLOR)):
            if pts:
                sh.uniform_float("lineWidth", width)
                sh.uniform_float("color", color)
                batch_for_shader(sh, 'LINES', {"pos": pts}).draw(sh)
        gpu.state.blend_set('NONE')

    @staticmethod
    def _discs(centres, radius, color):
        if not centres:
            return
        ring = [(math.cos(a * math.tau / 16), math.sin(a * math.tau / 16)) for a in range(16)]
        tris = []
        for cx, cy in centres:
            for i in range(16):
                (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % 16]
                tris += [(cx, cy), (cx + x0 * radius, cy + y0 * radius),
                         (cx + x1 * radius, cy + y1 * radius)]
        sh = gpu.shader.from_builtin('UNIFORM_COLOR')
        sh.uniform_float("color", color)
        batch_for_shader(sh, 'TRIS', {"pos": tris}).draw(sh)

    @staticmethod
    def _cursor(p, k, color):
        """A ring with a dot: where a click would put the point."""
        r = HOVER_PX * k
        ring = [(p[0] + math.cos(a * math.tau / 24) * r, p[1] + math.sin(a * math.tau / 24) * r)
                for a in range(25)]
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        sh.uniform_float("lineWidth", 1.5)
        sh.uniform_float("color", color)
        batch_for_shader(sh, 'LINE_STRIP', {"pos": ring}).draw(sh)
        MULTICAMPROJECT_OT_SurfaceCut._discs([p], 2.0 * k, color)

    def _draw_2d(self, _context):
        if bpy.context.region != self._win:
            return
        k = _ui()
        gpu.state.blend_set('ALPHA')
        under = self._under_mouse()
        if under is not None:
            p = self._screen(self._points[under][0])
            closing = under == 0 and len(self._points) >= 3
            if p is not None:
                self._discs([p], HOVER_PX * k, CLOSE_COLOR if closing else (1.0, 1.0, 1.0, 0.9))
        plain = [self._screen(pt[0]) for pt in self._points if not pt[2]]
        moved = [self._screen(pt[0]) for pt in self._points if pt[2]]
        self._discs([p for p in plain if p], DOT_PX * k, (1.0, 1.0, 1.0, 1.0))
        self._discs([p for p in moved if p], LAST_PX * k, EDGE_COLOR)
        if self._points:
            last = self._screen(self._points[-1][0])
            if last:
                self._discs([last], LAST_PX * k, COLOR)
            if len(self._points) >= 3:
                first = self._screen(self._points[0][0])
                if first:
                    self._discs([first], DOT_PX * k, CLOSE_COLOR)
        _live, _closing, hit = self._hover()
        if hit is not None:                     # the mouse on the mesh, as the Knife's cursor
            p = self._screen(hit[0])
            if p:
                color = EDGE_COLOR if hit[2] else SNAP_COLOR if hit[3] != 'FACE' else (1.0, 1.0, 1.0, 1.0)
                self._cursor(p, k, color)
                if hit[3] == 'VERT':
                    self._discs([p], DOT_PX * k, color)
        gpu.state.blend_set('NONE')

    # ------------------------------------------------------------ modal
    def invoke(self, context, event):
        obj = context.edit_object
        self._area = context.area
        self._win = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        self._rv3d = context.area.spaces.active.region_3d
        if self._win is None:
            return {'CANCELLED'}
        pc.hide_preview()
        self._bm = bmesh.from_edit_mesh(obj.data)
        self._surface = Surface(self._bm)
        self._mw = obj.matrix_world.copy()
        self._inv = self._mw.inverted()
        self._size = max(obj.dimensions.length / max(obj.matrix_world.to_scale().length, 1e-9), 1e-6)
        self._tol = self._size * 1e-4
        self._show_close = event.shift
        self._points, self._segments = [], []
        self._mouse = (event.mouse_x - self._win.x, event.mouse_y - self._win.y)
        self._last_pos, self._last_click = self._mouse, 0.0
        self._hover_key, self._hover_cache = None, ([], [], None)
        self._h3 = bpy.types.SpaceView3D.draw_handler_add(self._draw_3d, (context,), 'WINDOW', 'POST_VIEW')
        self._h2 = bpy.types.SpaceView3D.draw_handler_add(self._draw_2d, (context,), 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set('CROSSHAIR')
        context.workspace.status_text_set(
            "Surface Cut: click: point  ·  first point / double click: close  ·  Enter: open line  ·  "
            "Shift: closing preview  ·  Backspace / Ctrl+Z: drop the last point  ·  "
            "red: hidden from this view  ·  Esc: cancel")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._h3, 'WINDOW')
        bpy.types.SpaceView3D.draw_handler_remove(self._h2, 'WINDOW')
        context.workspace.status_text_set(None)
        context.window.cursor_modal_restore()
        self._area.tag_redraw()

    def _finish(self, context, closed):
        if closed:
            a, b = self._points[-1], self._points[0]
            self._segments.append(self._surface.segment((a[0], a[1]), (b[0], b[1])))
        self._end(context)
        obj = context.edit_object
        result = self._surface.cut(self._points, self._segments, closed)
        if not result[0] and not result[1]:
            self.report({'WARNING'}, "Surface Cut: nothing was cut")
            return {'CANCELLED'}
        seamed, selected, what = apply_cut(context, obj, self._bm, result, closed)
        if seamed or selected:
            self.report({'INFO'}, f"Surface Cut: {what}" + (f", seam on {seamed:,} edges" if seamed else ""))
        else:
            pc._popup(context, f"Surface Cut: {what}")
        return {'FINISHED'}

    def modal(self, context, event):
        self._area.tag_redraw()
        if event.shift != self._show_close:     # Shift: the closing preview
            self._show_close = event.shift
            self._hover_key = None
        if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            self._mouse = (event.mouse_x - self._win.x, event.mouse_y - self._win.y)
            return {'RUNNING_MODAL'}
        if event.type in NAVIGATE or event.type.startswith(('NUMPAD_', 'NDOF_')):
            return {'PASS_THROUGH'}
        if event.type == 'ESC' and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if len(self._points) >= 2:
                return self._finish(context, False)
            return {'RUNNING_MODAL'}
        undo = (event.type == 'BACK_SPACE' or (event.type == 'Z' and (event.ctrl or event.oskey)
                                                   and not event.shift))
        if undo and event.value == 'PRESS':
            if self._points:
                self._points.pop()
                if self._segments:
                    self._segments.pop()
                if not self._points:
                    self._surface.locked = None
                self._hover_key = None
            return {'RUNNING_MODAL'}            # Blender's own undo waits until the cut is done
        if event.type == 'LEFTMOUSE' and event.value in {'PRESS', 'DOUBLE_CLICK'}:
            p = (event.mouse_x - self._win.x, event.mouse_y - self._win.y)
            self._mouse = p
            again = (time.monotonic() - self._last_click < DOUBLE_SECONDS
                     and (Vector(p) - Vector(self._last_pos)).length < DOUBLE_PX * _ui())
            if (event.value == 'DOUBLE_CLICK' or again or self._under_mouse() == 0) \
                    and len(self._points) >= 3:
                return self._finish(context, True)
            if not again and event.value == 'PRESS':
                self._add(p)
                self._hover_key = None
            self._last_pos = p
            self._last_click = time.monotonic()
        return {'RUNNING_MODAL'}


def apply_cut(context, obj, bm, result, closed):
    """After the cut (Edit Mode): remember it on the mesh and apply the toggles, as PolyCut
    (written in Object Mode, back to Edit Mode). Returns (seam edges set, ('FACE' / 'EDGE',
    count) or None, a summary)."""
    cut, rim, inside, piece, gaps = result
    bm.edges.index_update()
    bm.faces.index_update()
    cut_i = [e.index for e in cut]
    rim_i = [e.index for e in rim]
    inside_i = [f.index for f in inside] if inside else None
    piece_i = [f.index for f in piece]
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    sc = context.scene
    bpy.ops.object.mode_set(mode='OBJECT')
    seamed, selected = 0, None
    try:
        me = obj.data
        ne, nf = len(me.edges), len(me.polygons)
        cut_a, rim_a = np.zeros(ne, bool), np.zeros(ne, bool)
        cut_a[cut_i], rim_a[rim_i] = True, True
        pc.forget(obj)
        close_border = getattr(sc, pc.CLOSE_BORDER_PROP, True)
        pc.store_flags(obj, cut_a | rim_a if close_border else cut_a)
        pc.store_bools(obj, pc.CUT_RIM, 'EDGE', rim_a if rim_a.any() else None)
        if inside_i is not None:
            ins, pie = np.zeros(nf, bool), np.zeros(nf, bool)
            ins[inside_i], pie[piece_i] = True, True
            pc.store_faces(obj, ins)
            pc.store_bools(obj, pc.CUT_PIECE, 'FACE', pie)
        seamed = pc.set_seam(obj) if pc.auto_seam_on(context) else 0
        want = pc.select_mode(context)
        faces = pc.selection_faces(context, obj) if want == 'FACE' else None
        if faces is not None:
            selected = ('FACE', pc.select_cut(obj, faces=faces))
        elif want:
            selected = ('EDGE', pc.select_cut(obj, edges=pc.cut_flags(obj)))
        if not seamed and not selected:
            pc.show_preview(obj)
    finally:
        bpy.ops.object.mode_set(mode='EDIT')
    if selected:
        context.tool_settings.mesh_select_mode = (False, selected[0] == 'EDGE', selected[0] == 'FACE')
    what = (f"{len(cut):,} edges cut" + (f", {len(rim):,} along the open rim" if rim else "")
            + (f", island of {len(inside):,} faces" if inside else
               (", open line" if not closed else ", the loop does not split the piece"))
            + (f", {gaps} gap(s)" if gaps else ""))
    return seamed, selected, what


_CLASSES = (MULTICAMPROJECT_OT_SurfaceCut,)
_keymaps = []


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:      # Edit Mode: K = Surface Cut, Shift+K = Blender's Knife (its K settings)
        km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new(MULTICAMPROJECT_OT_SurfaceCut.bl_idname,
                                                 'K', 'PRESS', head=True)))
        knife = km.keymap_items.new("mesh.knife_tool", 'K', 'PRESS', shift=True, head=True)
        knife.properties.use_occlude_geometry = True
        knife.properties.only_selected = False
        _keymaps.append((km, knife))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
