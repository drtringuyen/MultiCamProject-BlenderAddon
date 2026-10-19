"""Close Hole and uvExtrude (Edit Mode, face select).

Close Hole: each connected region of selected faces goes; the loop around it becomes one
n-gon, flattened onto its best plane, then triangulated (Beauty).

uvExtrude: the selection's seams are cleared and its boundary marked, Blender's own
extrude-along-normal runs (Enter / click confirms, Esc / right-click cancels - and a cancel
takes everything back, seams included). After it: the base ring is a seam, the top ring is
not (the cap unfolds with its walls), and each closed wall ring gets one seam on a random
vertical edge, so cap + walls unfold as one island in uv_normal."""
import random

import bmesh
import bpy


def _face_mode(context):
    return context.mode == 'EDIT_MESH' and context.tool_settings.mesh_select_mode[2]


def _poll_faces(cls, context):
    if not _face_mode(context):
        cls.poll_message_set("Edit Mode, face select")
        return False
    if context.edit_object.data.total_face_sel == 0:
        cls.poll_message_set("Select faces first")
        return False
    return True


def _regions(faces):
    """Connected groups (through edges) of `faces`."""
    left, out = set(faces), []
    while left:
        stack, region = [left.pop()], set()
        while stack:
            f = stack.pop()
            region.add(f)
            for e in f.edges:
                for g in e.link_faces:
                    if g in left:
                        left.remove(g)
                        stack.append(g)
        out.append(region)
    return out


def _edge_groups(edges):
    """Connected groups (through verts) of `edges`."""
    left, out = set(edges), []
    while left:
        stack, group = [left.pop()], set()
        while stack:
            e = stack.pop()
            group.add(e)
            for v in e.verts:
                for g in v.link_edges:
                    if g in left:
                        left.remove(g)
                        stack.append(g)
        out.append(group)
    return out


def _closed(edges):
    """`edges` form one simple loop: every vert on exactly two of them."""
    count = {}
    for e in edges:
        for v in e.verts:
            count[v] = count.get(v, 0) + 1
    return all(n == 2 for n in count.values())


# ---------------------------------------------------------------- Close Hole

def close_holes(bm):
    """Every selected region to a flat triangulated cap. Returns (new faces, warnings)."""
    sel = [f for f in bm.faces if f.select]
    jobs, warnings = [], []
    for region in _regions(sel):
        # the loop the cap closes: edges with a face outside the region, and the mesh's open
        # edges the region reaches
        rim = {e for f in region for e in f.edges
               if len(e.link_faces) == 1 or any(g not in region for g in e.link_faces)}
        loops = _edge_groups(rim)
        if len(loops) > 1:      # a hole inside the selection: the cap covers it
            outer = [lp for lp in loops if any(len(e.link_faces) > 1 for e in lp)]
            loops = outer if outer else loops
        if not loops:
            warnings.append("a selection with no border (a whole closed part) - skipped")
            continue
        if len(loops) > 1:
            warnings.append("a selection around unselected faces - skipped")
            continue
        if not _closed(loops[0]):
            warnings.append("a selection whose border touches itself at a corner - skipped")
            continue
        # the winding the cap needs: opposite to the outside face on a rim edge (an open
        # edge: as the region's own face had it)
        e = max(loops[0], key=lambda e: len(e.link_faces))
        outside = next((g for g in e.link_faces if g not in region), None)
        lp = next(lp for lp in (outside or e.link_faces[0]).loops if lp.edge == e)
        a, b = lp.vert, lp.link_loop_next.vert
        jobs.append((region, loops[0], (a, b) if outside else (b, a),
                     next(iter(region)).material_index, next(iter(region)).smooth))
    new = []
    for region, rim, (a, b), mat, smooth in jobs:
        inner_edges = {e for f in region for e in f.edges} - rim
        inner_verts = {v for f in region for v in f.verts} - {v for e in rim for v in e.verts}
        bmesh.ops.delete(bm, geom=list(region), context='FACES_ONLY')
        bmesh.ops.delete(bm, geom=[e for e in inner_edges if e.is_valid and not e.link_faces],
                         context='EDGES')
        bmesh.ops.delete(bm, geom=[v for v in inner_verts if v.is_valid and not v.link_edges],
                         context='VERTS')
        faces = bmesh.ops.contextual_create(bm, geom=list(rim))["faces"]
        if len(faces) != 1:
            warnings.append("a loop Blender could not fill")
            continue
        f = faces[0]
        if any(lp.vert == a and lp.link_loop_next.vert == b for lp in f.loops):
            bmesh.ops.reverse_faces(bm, faces=[f])
        f.material_index, f.smooth = mat, smooth
        f.normal_update()
        n, c = f.normal.copy(), f.calc_center_median()
        for v in f.verts:                       # onto the n-gon's plane
            v.co -= n * (v.co - c).dot(n)
        new += bmesh.ops.triangulate(bm, faces=[f], quad_method='BEAUTY',
                                     ngon_method='BEAUTY')["faces"]
    return new, warnings


class MULTICAMPROJECT_OT_CloseHole(bpy.types.Operator):
    """Delete the selected faces and close the hole with one flat cap: the border filled as an
    n-gon, flattened onto its plane, triangulated"""
    bl_idname = "multicamproject.close_hole"
    bl_label = "Close Hole"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_faces(cls, context)

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        new, warnings = close_holes(bm)
        for f in bm.faces:
            f.select = False
        for f in new:
            f.select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        for w in warnings:
            self.report({'WARNING'}, f"Close Hole: {w}")
        if not new:
            return {'CANCELLED'}
        self.report({'INFO'}, f"Close Hole: {len(new)} triangles")
        return {'FINISHED'}


# ---------------------------------------------------------------- uvExtrude

def _rim(faces):
    fs = set(faces)
    return {e for f in fs for e in f.edges if any(g not in fs for g in e.link_faces)
            or len(e.link_faces) == 1}


def prepare_seams(bm):
    """Before the extrusion: no seam inside the selection, its boundary a seam."""
    sel = [f for f in bm.faces if f.select]
    for f in sel:
        for e in f.edges:
            e.seam = False
    for e in _rim(sel):
        e.seam = True


def wall_seams(bm, rng=random):
    """After the extrusion (the moved top faces selected): base ring seamed, top ring not,
    one random vertical seam per closed wall ring. Returns the number of rings cut."""
    top = {f for f in bm.faces if f.select}
    rim = {e for e in _rim(top) if len(e.link_faces) > 1}
    walls, vertical = set(), set()
    for e in rim:
        e.seam = False
        for f in e.link_faces:
            if f in top:
                continue
            walls.add(f)
            tv = set(e.verts)
            for g in f.edges:
                if g is e:
                    continue
                shared = tv & set(g.verts)
                if not shared:
                    g.seam = True           # the base edge, across from the top
                else:
                    vertical.add(g)
    cut = 0
    for ring in _regions(walls):
        sides = [g for g in vertical if any(f in ring for f in g.link_faces)]
        if sides and all(sum(f in ring for f in g.link_faces) == 2 for g in sides):
            rng.choice(sides).seam = True       # a closed ring: cut it once
            cut += 1
    return cut


def _moving(context):
    """A transform is running in this window (the extrusion's move)."""
    return any(op.bl_idname.startswith("TRANSFORM_OT")
               for op in getattr(context.window, "modal_operators", ()))


class MULTICAMPROJECT_OT_UVExtrude(bpy.types.Operator):
    """Extrude the selected faces along their normal with seams that unfold in uv_normal:
    the base ring and one random edge of the walls. Click / Enter confirms, Esc cancels"""
    bl_idname = "multicamproject.uv_extrude"
    bl_label = "uvExtrude"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _poll_faces(cls, context)

    def invoke(self, context, event):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        self._backup = bm.copy()          # Esc puts this back (seams and all)
        self._obj = obj.name
        prepare_seams(bm)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.mesh.extrude_region(use_normal_flip=False)
        bm = bmesh.from_edit_mesh(obj.data)
        self._start = [(v.index, v.co.copy()) for v in bm.verts if v.select]
        wm = context.window_manager
        wm.modal_handler_add(self)
        self._timer = wm.event_timer_add(0.05, window=context.window)
        # Blender's E in face select: move along the normal (its own modal, run after ours
        # so it sees the events first)
        bpy.ops.transform.translate('INVOKE_REGION_WIN', orient_type='NORMAL',
                                    constraint_axis=(False, False, True))
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if _moving(context):
            return {'PASS_THROUGH'}
        context.window_manager.event_timer_remove(self._timer)
        obj = bpy.data.objects.get(self._obj)
        if obj is None or obj.mode != 'EDIT':
            self._backup.free()
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        moved = any(i < len(bm.verts) and (bm.verts[i].co - co).length > 1e-6
                    for i, co in self._start)
        if not moved:                   # the move was cancelled: all of it goes back
            bpy.ops.object.mode_set(mode='OBJECT')
            self._backup.to_mesh(obj.data)
            self._backup.free()
            bpy.ops.object.mode_set(mode='EDIT')
            self.report({'INFO'}, "uvExtrude cancelled")
            return {'CANCELLED'}
        self._backup.free()
        cut = wall_seams(bm)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.ed.undo_push(message="uvExtrude")
        self.report({'INFO'}, f"uvExtrude: base ring seamed, {cut} wall seam(s)")
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_CloseHole, MULTICAMPROJECT_OT_UVExtrude)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
