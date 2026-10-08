import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ... import module_manager
from . import core, cutter, marks, tool, workflow as wf

_SAMPLES = 12           # interior rays per axis for the cut depth
_SAME_PX = 4            # a double click's first press lands on the last point: not a new one
_NAVIGATE = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
             'MOUSEROTATE', 'MOUSESMARTZOOM'}


def _inside(p, poly):
    x, y = p
    hit = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def _decided(cls, obj):
    """PolyCut, its pick and the Decimate Brush wait for the Decimate decision."""
    if wf.decimate_pending(obj):
        cls.poll_message_set("Decide the Decimate first (Apply)")
        return False
    return True


class MULTICAMPROJECT_OT_RemeshPolyCut(bpy.types.Operator):
    """Draw a polygon: the mesh is cut exactly along its lines (not along the scan's triangles)
    and the inside becomes a new face set. Click to add points, Enter or double click to
    close it back to the first point, Esc to cancel. Then Set Faces opens for the new face set"""
    bl_idname = "multicamproject.remesh_poly_cut"
    bl_label = "PolyCut"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.mode == 'SCULPT'
                and context.area is not None and context.area.type == 'VIEW_3D'
                and _decided(cls, obj))

    # ------------------------------------------------------------ points
    # The clicks are kept in 3D (on the surface under the mouse, or at the depth of the last
    # point) and projected into the view on every redraw: navigating keeps the polygon on the
    # object. The cut is made from the view the polygon is closed in.
    def _add(self, context, p):
        region, rv3d = self.win, self.rv3d
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, p)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized()
        hit, loc, *_rest = context.scene.ray_cast(context.evaluated_depsgraph_get(),
                                                  origin, direction)
        if not hit:
            depth = self.points3d[-1] if self.points3d else rv3d.view_location
            loc = view3d_utils.region_2d_to_location_3d(region, rv3d, p, depth)
        self.points3d.append(loc.copy())

    def _points(self):
        """The points in the current view (None for one behind it)."""
        out = []
        for co in self.points3d:
            p = view3d_utils.location_3d_to_region_2d(self.win, self.rv3d, co)
            out.append(None if p is None else (p.x, p.y))
        return out

    # ------------------------------------------------------------ drawing
    def _draw(self, context):
        points = [p for p in self._points() if p is not None]
        if not points:
            return
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 2.0)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 1.0))
        batch_for_shader(shader, 'LINE_STRIP', {"pos": points + [self.mouse]}).draw(shader)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 0.35))
        batch_for_shader(shader, 'LINES', {"pos": [self.mouse, points[0]]}).draw(shader)
        pshader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.point_size_set(7.0)
        pshader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch_for_shader(pshader, 'POINTS', {"pos": points}).draw(pshader)
        gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')

    def _pos(self, event):
        return (event.mouse_x - self.win.x, event.mouse_y - self.win.y)

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.area.header_text_set(None)
        context.window.cursor_modal_restore()
        context.area.tag_redraw()

    # ------------------------------------------------------------ modal
    def invoke(self, context, event):
        # started from the sidebar: work in the viewport's own region
        self.win = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        self.rv3d = context.area.spaces.active.region_3d
        if self.win is None:
            return {'CANCELLED'}
        self.points3d = []
        self.mouse = self._pos(event)
        if event.type == 'LEFTMOUSE':   # Ctrl+click with the PolyCut tool: the first point
            self._add(context, self.mouse)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set('CROSSHAIR')
        context.area.header_text_set(
            "Poly Cut: click to add points - Enter / double click: close and cut - Esc: cancel")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE':
            self.mouse = self._pos(event)
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._close(context)
        if event.type in _NAVIGATE or event.type.startswith(('NUMPAD_', 'NDOF_')):
            return {'PASS_THROUGH'}         # navigating is fine: the points are 3D
        if event.type == 'ESC' and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE':
            p = self._pos(event)
            if event.value == 'DOUBLE_CLICK':
                last = self._points()[-1] if self.points3d else None
                if last is None or (Vector(p) - Vector(last)).length > _SAME_PX:
                    self._add(context, p)
                return self._close(context)
            if event.value == 'PRESS':
                self._add(context, p)
        return {'RUNNING_MODAL'}

    def _close(self, context):
        self._end(context)
        if len(self.points3d) < 3:
            self.report({'WARNING'}, "Poly Cut needs at least 3 points")
            return {'CANCELLED'}
        self.points = self._points()
        if None in self.points:
            self.report({'WARNING'}, "A Poly Cut point is behind the view")
            return {'CANCELLED'}
        obj = context.active_object
        region, rv3d = self.win, self.rv3d
        forward = (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()

        def ray(p):
            return (view3d_utils.region_2d_to_origin_3d(region, rv3d, p),
                    view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized())

        rays = [ray(p) for p in self.points]
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        probe = [(o, d, forward) for o, d in rays]
        for i in range(_SAMPLES):
            for j in range(_SAMPLES):
                q = (min(xs) + (max(xs) - min(xs)) * (i + 0.5) / _SAMPLES,
                     min(ys) + (max(ys) - min(ys)) * (j + 0.5) / _SAMPLES)
                if _inside(q, self.points):
                    probe.append((*ray(q), forward))
        depth = cutter.auto_depth(obj, context.evaluated_depsgraph_get(), probe)
        if depth is None:
            self.report({'WARNING'}, "The polygon does not cover the object")
            return {'CANCELLED'}

        mode = obj.mode
        if mode != 'OBJECT':            # Sculpt Mode keeps its own copy of the mesh
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            new_id, count = core.poly_cut(obj, rays, forward, *depth)
        finally:
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)
        if not count:
            self.report({'WARNING'}, "Nothing inside the polygon was cut")
            return {'FINISHED'}
        marks.remember_face_set(obj, new_id)
        self.report({'INFO'}, f"Face set {new_id}: {count} faces")
        if obj.mode in {'SCULPT', 'EDIT'}:
            bpy.ops.multicamproject.remesh_set_faces('INVOKE_DEFAULT', face_set=new_id)
        return {'FINISHED'}


def _enter_tool(context, obj):
    """The object in Sculpt Mode with the PolyCut tool active."""
    if obj.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')
    try:
        bpy.ops.wm.tool_set_by_id(name=tool.tool_id())
    except (RuntimeError, TypeError):
        pass        # no 3D view in this context: the mode is set, the tool is one click away


class MULTICAMPROJECT_OT_Remesh(bpy.types.Operator):
    """0C Remesh: make the low-poly copy. It takes the name, EXPORT and the textures, gets
    a Decimate (vg_Protect) and the camera projection. The original becomes <name>_original
    in "Original Mesh", the copy's Bake Source (BA_ / BN_). First decide and apply the
    Decimate (the projection waits, muted), then PolyCut"""
    bl_idname = "multicamproject.remesh"
    bl_label = "Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _remesh_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        name, mesh_name = obj.name, obj.data.name
        copy, warnings = wf.make_copy(context, obj)
        for w in warnings:
            self.report({'WARNING'}, w)
        wf.apply_new_name(context.scene, copy, obj, name, mesh_name)
        if wf.decimate_pending(copy):
            # Object Mode: the Decimate is decided first (Cutting & Modelling), then PolyCut
            self.report({'INFO'}, f"'{copy.name}' is the Remesh copy: decide its Decimate, "
                                  "then Apply")
            return {'FINISHED'}
        _enter_tool(context, copy)
        self.report({'INFO'}, f"'{copy.name}' is the Remesh copy, '{obj.name}' the high poly")
        return {'FINISHED'}


def _remesh_poll(cls, context):
    """0C / 0D: a mesh that is not a Remesh copy, an original or handmade."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.library:
        return False
    if wf.is_copy(obj):
        cls.poll_message_set("Already a Remesh copy")
        return False
    if obj.name.endswith(wf.ORIGINAL_SUFFIX) or wf.is_original(obj):
        cls.poll_message_set("This is a Remesh original")
        return False
    if wf.is_handmade(obj):
        cls.poll_message_set(wf.HANDMADE_TEXT)
        return False
    return True


def _retopo_snapping(context):
    """Snap moved vertices onto the original's surface (Face Project, not onto itself) and
    the Retopology overlay (the new mesh drawn in front of the scan)."""
    ts = context.scene.tool_settings
    ts.use_snap = True
    ts.snap_elements = {'FACE', 'FACE_PROJECT'}  # (base / individual set apart clear each other)
    ts.use_snap_self = False
    ts.use_snap_nonedit = True
    ts.snap_target = 'CLOSEST'
    space = context.space_data
    if space is not None and space.type == 'VIEW_3D':
        space.overlay.show_retopology = True


def _bring_in(context, obj):
    """obj in the view layer when its collection is excluded (e.g. "Original Mesh" kept out
    of the way): the collections are included again, everything else that comes in with
    them is hidden. Returns True when something changed."""
    vl = context.view_layer
    if vl.objects.get(obj.name) == obj or not module_manager.is_loaded("baking"):
        return False
    from ..baking import common
    before = set(vl.objects)
    colls = common._colls_holding(vl, {obj})
    for c in reversed(colls):                   # parents first
        lc = common._layer_coll(vl, c)
        if lc.exclude:
            lc.exclude = False
    for c in colls:
        common._layer_coll(vl, c).hide_viewport = False
    vl.update()
    for o in vl.objects:
        if o not in before:
            o.hide_set(True)        # obj too: the solo shows it and hides it again when left
    return True


def focus_retopo(context, obj, report=None):
    """Local view on obj + its original (the original selected, obj active), then Edit Mode on
    obj alone - the scan never enters Edit Mode. Snapping per the 0D option."""
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    orig = wf.bake_source_of(obj)
    if orig is not None and _bring_in(context, orig) and report:
        report({'INFO'}, f"'{orig.name}': its collection is included again (the rest of it "
                         "hidden)")
    in_view = context.area is not None and context.area.type == 'VIEW_3D'
    if in_view and module_manager.is_loaded("export"):
        from ..export import operators as export_ops
        if not export_ops.is_soloed(context, obj):
            export_ops.solo(context, obj, report)
    if context.scene.multicamproject_retopo_snap:
        _retopo_snapping(context)
    for o in context.selected_objects:
        o.select_set(False)
    context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')        # only the selected meshes enter Edit Mode
    if orig is not None and orig.visible_get():
        orig.select_set(True)                   # selected (outlined), not in Edit Mode


class MULTICAMPROJECT_OT_RetopoEmpty(bpy.types.Operator):
    """0D Retopo: as 0C Remesh (name, EXPORT, MCP_ / MAT_ / ALB_ / NOR_, Bake Source),
    but the new object starts as the other selected mesh (e.g. a ROOM_Template part, its UV
    as uv_normal) - or empty, per the 0D option - to model by hand on the original.
    Then both in local view and Edit Mode on the new object"""
    bl_idname = "multicamproject.retopo_empty"
    bl_label = "Retopo"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _remesh_poll(cls, context):
            return False
        if context.scene.multicamproject_retopo_plane                 and not wf.retopo_content(context, context.active_object):
            cls.poll_message_set(wf.RETOPO_CONTENT_TEXT)
            return False
        return True

    def execute(self, context):
        obj = context.active_object
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        name, mesh_name = obj.name, obj.data.name
        new, warnings = wf.make_copy(context, obj, retopo=True)
        for w in warnings:
            self.report({'WARNING'}, w)
        wf.apply_new_name(context.scene, new, obj, name, mesh_name)
        focus_retopo(context, new, self.report)
        self.report({'INFO'}, f"'{new.name}' is the retopo, '{obj.name}' the high poly")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RetopoFocus(bpy.types.Operator):
    """Retopo again: the retopo and its original in local view, Edit Mode on the retopo"""
    bl_idname = "multicamproject.retopo_focus"
    bl_label = "Retopo"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and not obj.library
                and wf.bake_source_of(obj) is not None)

    def execute(self, context):
        focus_retopo(context, context.active_object, self.report)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshEnterTool(bpy.types.Operator):
    """Sculpt Mode with the PolyCut tool (Ctrl+Click: PolyCut, L: Set Faces for the face set
    under the mouse)"""
    bl_idname = "multicamproject.remesh_enter_tool"
    bl_label = "PolyCut Tool"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and not obj.library
                and _decided(cls, obj))

    def execute(self, context):
        _enter_tool(context, context.active_object)
        return {'FINISHED'}


DENSITY_BRUSH = "brushes/essentials_brushes-mesh_sculpt.blend/Brush/Density"


def dyntopo_on(obj):
    return obj is not None and obj.type == 'MESH' and obj.use_dynamic_topology_sculpting


class MULTICAMPROJECT_OT_RemeshDensityBrush(bpy.types.Operator):
    """Decimate brush: Blender's Density brush in Sculpt Mode - collapses short edges where you
    paint. While you use it, the object's modifiers are off and the view is in Solid, so you
    see only the topology; Dyntopo is switched on. Pick another brush or tool, or leave Sculpt
    Mode, and the modifiers come back, Dyntopo goes off and the view returns to Material
    Preview"""
    bl_idname = "multicamproject.remesh_density_brush"
    bl_label = "Decimate Brush"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and not obj.library
                and _decided(cls, obj))

    def invoke(self, context, event):
        obj = context.active_object
        if obj.mode != 'SCULPT':
            bpy.ops.object.mode_set(mode='SCULPT')
        try:
            bpy.ops.wm.tool_set_by_id(name="builtin.brush")
            bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS',
                                         relative_asset_identifier=DENSITY_BRUSH)
        except (RuntimeError, TypeError) as e:
            self.report({'ERROR'}, f"Density brush not available: {e}")
            return {'CANCELLED'}
        from . import decimate_session
        # modifiers off first: Dyntopo then has nothing to warn about, and no GN / Decimate
        # is evaluated again per stroke
        decimate_session.start(obj)
        if not dyntopo_on(obj):
            bpy.ops.sculpt.dynamic_topology_toggle()        # exec: no popup
        self.report({'INFO'}, "Decimate Brush: modifiers off, Solid view - another brush or "
                              "tool brings them back")
        return {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


_DETAIL_PROP = {'RELATIVE': "detail_size", 'BRUSH': "detail_percent"}


class MULTICAMPROJECT_OT_RemeshDetailSize(bpy.types.Operator):
    """Decimate Brush: change the Dyntopo detail like F changes the brush size (drag, click to
    confirm, right click / Esc to cancel). Ctrl+Shift+F while the Decimate Brush is active"""
    bl_idname = "multicamproject.remesh_detail_size"
    bl_label = "Decimate Brush Detail"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        # the brush itself, not the session record: an add-on reload clears that
        ts = context.tool_settings.sculpt
        return (context.mode == 'SCULPT' and dyntopo_on(context.active_object)
                and ts.brush is not None
                and getattr(ts.brush, "sculpt_brush_type", "") == 'SIMPLIFY')

    def invoke(self, context, event):
        from . import decimate_session
        obj = context.active_object
        if decimate_session.active() != obj.name:
            decimate_session.start(obj)     # a reload ended it: modifiers off again
        ts = context.tool_settings.sculpt
        prop = _DETAIL_PROP.get(ts.detail_type_method)
        if prop:        # pixels / percent: the F circle, at the detail's real size
            bpy.ops.wm.radial_control('INVOKE_DEFAULT',
                                      data_path_primary=f"tool_settings.sculpt.{prop}")
        else:   # Constant / Manual (a resolution, not a size on screen): Blender's detail edit
            bpy.ops.sculpt.dyntopo_detail_size_edit('INVOKE_DEFAULT')
        return {'FINISHED'}         # the radial control runs on as its own modal


class MULTICAMPROJECT_OT_RemeshCleanFloating(bpy.types.Operator):
    """Clean Floating (Ctrl+Shift+L): select a bit of the one big mesh to keep - the selection
    grows to everything linked to it (Select Linked, Normal delimit, seams do not stop it),
    Face then Edge select mode, the selection is inverted and every floating piece is deleted"""
    bl_idname = "multicamproject.remesh_clean_floating"
    bl_label = "Clean Floating"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or context.mode != 'EDIT_MESH':
            cls.poll_message_set("Edit Mode: select a bit of the mesh to keep")
            return False
        return True

    def execute(self, context):
        obj = context.active_object
        if obj.data.total_vert_sel == 0:
            self.report({'WARNING'}, "Select a bit of the mesh to keep first")
            return {'CANCELLED'}
        before = len(obj.data.polygons)
        bpy.ops.mesh.select_linked(delimit={'NORMAL'})
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_mode(type='EDGE')
        bpy.ops.mesh.select_all(action='INVERT')    # everything but the kept mesh
        bpy.ops.mesh.delete(type='EDGE')
        obj.update_from_editmode()
        self.report({'INFO'}, f"Clean Floating: {before - len(obj.data.polygons):,} faces deleted")
        return {'FINISHED'}


def _mouse_ray(context, event):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or region.type != 'WINDOW':
        return None
    p = (event.mouse_region_x, event.mouse_region_y)
    return (view3d_utils.region_2d_to_origin_3d(region, rv3d, p),
            view3d_utils.region_2d_to_vector_3d(region, rv3d, p).normalized())


class MULTICAMPROJECT_OT_RemeshPick(bpy.types.Operator):
    """The face set under the mouse -> Set Faces (the PolyCut tool, Sculpt Mode)"""
    bl_idname = "multicamproject.remesh_pick"
    bl_label = "Pick Face Set"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.mode == 'SCULPT'
                and _decided(cls, obj))

    def invoke(self, context, event):
        obj = context.active_object
        ray = _mouse_ray(context, event)
        if ray is None:
            return {'PASS_THROUGH'}
        if context.mode == 'EDIT_MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            index = marks.pick_face(obj, *ray, bm=bm)
            if index is None:
                return {'CANCELLED'}
            lay = bm.faces.layers.int.get(core.SCULPT_FACE_SET)
            fs = bm.faces[index][lay] if lay is not None else 1
        else:
            index = marks.pick_face(obj, *ray)
            if index is None:
                return {'CANCELLED'}
            fs = int(core._face_sets(obj.data)[index])
        marks.remember_face_set(obj, fs)
        bpy.ops.multicamproject.remesh_set_faces('INVOKE_DEFAULT', face_set=fs)
        return {'FINISHED'}


_state = {"action": 'PROJECTED', "seam": 'MARK'}


class MULTICAMPROJECT_OT_RemeshSetFaces(bpy.types.Operator):
    """02 Select & Set: Projected / Baked (the VCMix alphas), Protect from Decimate, Delete,
    and the outline's seam. Edit Mode: the selected faces. Sculpt Mode: the face set picked
    with L or made by the last PolyCut"""
    bl_idname = "multicamproject.remesh_set_faces"
    bl_label = "Set Faces"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(name="Set", items=marks.ACTIONS, default='PROJECTED')
    seam: EnumProperty(name="Seam", items=marks.SEAMS, default='MARK',
                       description="What happens to the UV seam on the region's outline")
    face_set: IntProperty(name="Face Set", default=0, min=0, options={'SKIP_SAVE'},
                          description="Face set to set (0: Edit Mode selection, or the face "
                                      "set picked / cut last in Sculpt Mode)")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and not obj.library
                and context.mode in {'SCULPT', 'EDIT_MESH', 'OBJECT'})

    @classmethod
    def description(cls, context, props):
        if props.action == 'CLEAR_FACE_SETS':
            return ("Clear all Face Sets: the whole mesh back to one face set (every PolyCut "
                    "region and Sculpt face set goes; seams stay)")
        return cls.__doc__

    def _sculpt_face_set(self, obj):
        return self.face_set or marks.last_face_set(obj)

    def _edit(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        return obj, bm

    def invoke(self, context, event):
        obj = context.active_object
        if self.action == 'CLEAR_FACE_SETS':    # the button: no dialog, the whole mesh
            return self.execute(context)
        if context.mode == 'OBJECT':
            self.report({'WARNING'}, "Sculpt Mode (L / PolyCut) or Edit Mode (select faces)")
            return {'CANCELLED'}
        self.action, self.seam = _state["action"], _state["seam"]
        if context.mode == 'EDIT_MESH':
            obj, bm = self._edit(context)
            if self.face_set:
                marks.edit_select_face_set(bm, self.face_set)
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            faces = marks.edit_region(bm)
            if not faces:       # a closed ring of edges (e.g. seams): the faces inside it
                faces = marks.edit_inside_loop(bm)
                if faces:
                    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            if not faces:
                self.report({'WARNING'}, "Select faces, or a closed ring of edges around them")
                return {'CANCELLED'}
            marks.overlay_show(obj, faces, self.action, bm=bm)
        else:
            fs = self._sculpt_face_set(obj)
            if not fs:
                self.report({'WARNING'}, "Pick a face set with L or make one with a PolyCut")
                return {'CANCELLED'}
            self.face_set = fs
            faces = marks.face_mask(obj, fs)
            if not faces.any():
                self.report({'WARNING'}, f"Face set {fs} has no faces")
                return {'CANCELLED'}
            marks.overlay_show(obj, faces, self.action)
        where = 'selection' if context.mode == 'EDIT_MESH' else f'face set {self.face_set}'
        return context.window_manager.invoke_props_dialog(
            self, title=f"Set Faces ({where})", confirm_text="Set")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "action", expand=True)
        col.separator()
        col.row().prop(self, "seam", expand=True)
        why = marks.mask_problem(context.active_object, self.action)
        if why:
            col.label(text=why, icon='ERROR')

    def check(self, context):
        marks.overlay_action(self.action)
        return True

    def cancel(self, context):
        marks.overlay_hide()

    def execute(self, context):
        marks.overlay_hide()
        if self.action != 'CLEAR_FACE_SETS':
            _state["action"], _state["seam"] = self.action, self.seam
        obj = context.active_object
        if self.action == 'CLEAR_FACE_SETS':    # the whole mesh, no region needed
            mode = context.mode
            if mode == 'EDIT_MESH':
                obj, bm = self._edit(context)
                n = marks.clear_face_sets_edit(bm)
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            else:
                if mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                try:
                    n = marks.clear_face_sets(obj)
                finally:
                    if mode == 'SCULPT':
                        bpy.ops.object.mode_set(mode='SCULPT')
            self.report({'INFO'}, f"Face sets cleared: {n:,} faces in one set")
            return {'FINISHED'}
        why = marks.mask_problem(obj, self.action)
        if why:
            self.report({'ERROR'}, why)
            return {'CANCELLED'}
        label = dict((k, l) for k, l, *_r in marks.ACTIONS)[self.action]
        mode = context.mode
        if marks.needs_paint_layers(obj, self.action):
            # the mesh's own VCMix layers (a copy of what the projection shows now)
            bpy.ops.object.mode_set(mode='OBJECT')
            from ..camera_project import core as cp
            cp.ensure_paint_layer(obj)
            bpy.ops.object.mode_set(mode='EDIT' if mode == 'EDIT_MESH' else 'SCULPT')
        if mode == 'EDIT_MESH':
            obj, bm = self._edit(context)
            if self.face_set:               # redo / run without the dialog
                marks.edit_select_face_set(bm, self.face_set)
            n = marks.apply_edit_mode(obj, bm, marks.edit_region(bm), self.action, self.seam)
            bmesh.update_edit_mesh(obj.data, loop_triangles=True,
                                   destructive=self.action in {'DELETE', 'CLOSE_HOLE'})
        else:
            fs = self._sculpt_face_set(obj)
            if not fs:
                self.report({'WARNING'}, "Pick a face set with L or make one with a PolyCut")
                return {'CANCELLED'}
            # Sculpt undo does not record vertex groups, seams or colors: write in Object Mode
            bpy.ops.object.mode_set(mode='OBJECT')
            try:
                n = marks.apply_object_mode(obj, marks.face_mask(obj, fs), self.action, self.seam)
            finally:
                bpy.ops.object.mode_set(mode='SCULPT')
        if not n:
            self.report({'WARNING'}, "No faces to set")
            return {'CANCELLED'}
        if self.action in marks.MASK_ACTIONS:
            from ..camera_project import core as cp
            mod = cp.get_modifier(obj)
            if mod is not None and cp.get_input(mod, "Previous Bake") < 1.0:
                cp.set_input(mod, "Previous Bake", 1.0)     # the mesh's layers show 1:1
        obj.update_tag()
        self.report({'INFO'}, f"{label}: {n:,} faces")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshApplyDecimate(bpy.types.Operator):
    """03 Apply Decimate: the low poly's topology becomes final (protected parts stay as they
    are). Unwrap uv_normal after this - the Decimate collapses across UV seams. The Bake
    Source keeps the full detail; Ctrl+Z brings the dense mesh back"""
    bl_idname = "multicamproject.remesh_apply_decimate"
    bl_label = "Apply Decimate"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or context.mode != 'OBJECT':
            cls.poll_message_set("Object Mode, the low poly active")
            return False
        if wf.decimate_modifier(obj) is None:
            cls.poll_message_set("No Decimate modifier")
            return False
        return True

    def execute(self, context):
        try:
            before, after = wf.apply_decimate(context, context.active_object)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Decimated: {before:,} -> {after:,} faces. Unwrap uv_normal next")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshUseExisting(bpy.types.Operator):
    """Use existing high poly: the active mesh (a low poly made outside the add-on) bakes
    from the other selected mesh - BA_ and BN_, 04 Bake from Source"""
    bl_idname = "multicamproject.remesh_use_existing"
    bl_label = "Use Existing High Poly"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        others = [o for o in context.selected_objects if o != obj and o.type == 'MESH']
        if obj is None or obj.type != 'MESH' or context.mode != 'OBJECT' or len(others) != 1:
            cls.poll_message_set("Select the high poly, then the low poly (active)")
            return False
        return True

    def execute(self, context):
        low = context.active_object
        high = next(o for o in context.selected_objects if o != low and o.type == 'MESH')
        try:
            wf.use_existing(context, low, high)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        high.select_set(False)
        if module_manager.is_loaded("baking"):
            from ..baking import route
            route.after_step(low, 'ORIGINAL')
        self.report({'INFO'}, f"'{low.name}' bakes from '{high.name}'")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemeshSnap(bpy.types.Operator):
    """Snap to Source: a Shrinkwrap onto the Bake Source for the vertices in vg_Snap only
    (after the Decimate). Off by default: new clean parts must not stick to the scan"""
    bl_idname = "multicamproject.remesh_snap"
    bl_label = "Snap to Source"
    bl_options = {'REGISTER', 'UNDO'}

    on: BoolProperty(default=True, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and wf.bake_source_of(obj) is not None

    def execute(self, context):
        try:
            wf.set_snap(context.active_object, self.on)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ResetObject(bpy.types.Operator):
    """Start over: remove everything the add-on put on the active mesh - projection and
    Final modifiers, Decimate / Snap, MCP_ / MAT_ slots, cameras, Bake Source and texture
    links, uv_normal, UV_camN, VCMix, face sets and Remesh marks. The scan's own materials,
    UVs, colors and normals stay. Then 0A / 0B / 0C run as on a fresh scan"""
    bl_idname = "multicamproject.reset_object"
    bl_label = "Reset Add-on Data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        why = wf.reset_problem(context.active_object)
        if why:
            cls.poll_message_set(why)
        return not why

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title=f"Reset '{context.active_object.name}'?",
            message="Removes the add-on's modifiers, slots, cameras, bake links, uv_normal and "
                    "marks. The scan's own data stays. Ctrl+Z undoes it.",
            confirm_text="Reset", icon='WARNING')

    def execute(self, context):
        obj = context.active_object
        done = wf.reset_object(obj)
        try:
            from ..baking import cache
            cache.clear()
        except ImportError:
            pass
        for line in done:
            print(f"[MultiCamProject] reset {obj.name}: {line}")
        self.report({'INFO'}, f"'{obj.name}' reset: {len(done)} item(s) removed (list in the "
                              "console)" if done else f"'{obj.name}' had no add-on data")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemoveProjection(bpy.types.Operator):
    """Remove 0A/0B from the active mesh only: projection modifier, UV_camN, VCMix, camera
    slots, MCP_ and BAp_/BNp_. The cameras stay in the scene; the original's bake, ALB_/NOR_
    and MAT_ stay. The Bake Route becomes From Original (with a Bake Source)"""
    bl_idname = "multicamproject.remove_projection"
    bl_label = "Remove Projection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        why = wf.reset_problem(obj)
        if not why and not (hasattr(obj, "multicamproject_cam") and obj.multicamproject_cam.is_setup):
            why = "No camera projection (0B) on it"
        if why:
            cls.poll_message_set(why)
        return not why

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title=f"Remove the projection from '{context.active_object.name}'?",
            message="Its VCMix paint and camera slots go (the cameras stay in the scene). "
                    "Ctrl+Z undoes it.", confirm_text="Remove", icon='WARNING')

    def execute(self, context):
        obj = context.active_object
        done = wf.remove_projection(obj)
        for line in done:
            print(f"[MultiCamProject] remove projection {obj.name}: {line}")
        self.report({'INFO'}, f"Projection removed from '{obj.name}' ({len(done)} item(s), "
                              "list in the console)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_UnlinkOriginal(bpy.types.Operator):
    """Remove 0C/0D's link from the active mesh: no Bake Source, BAo_/BNo_ unlinked (their
    files stay until Check Textures). The low poly and the hidden original stay. The Bake
    Route becomes From Projection"""
    bl_idname = "multicamproject.unlink_original"
    bl_label = "Unlink Original"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        why = wf.reset_problem(obj)
        if not why and obj.multicamproject_bake.bake_source is None:
            why = "No Bake Source (original) on it"
        if why:
            cls.poll_message_set(why)
        return not why

    def invoke(self, context, event):
        src = context.active_object.multicamproject_bake.bake_source
        return context.window_manager.invoke_confirm(
            self, event, title=f"Unlink '{src.name}' from '{context.active_object.name}'?",
            message="It no longer bakes from the original (BAo_/BNo_). The low poly and the "
                    "original stay. Ctrl+Z undoes it.", confirm_text="Unlink", icon='WARNING')

    def execute(self, context):
        obj = context.active_object
        done = wf.unlink_original(obj)
        self.report({'INFO'}, f"'{obj.name}' no longer bakes from the original: "
                              f"{', '.join(done) or 'nothing linked'}")
        return {'FINISHED'}


_CLASSES = (MULTICAMPROJECT_OT_RemeshPolyCut, MULTICAMPROJECT_OT_Remesh,
            MULTICAMPROJECT_OT_RemeshEnterTool, MULTICAMPROJECT_OT_RemeshDensityBrush,
            MULTICAMPROJECT_OT_RemeshDetailSize, MULTICAMPROJECT_OT_RemeshCleanFloating,
            MULTICAMPROJECT_OT_RemeshPick,
            MULTICAMPROJECT_OT_RemeshSetFaces, MULTICAMPROJECT_OT_RemeshApplyDecimate,
            MULTICAMPROJECT_OT_RemeshUseExisting, MULTICAMPROJECT_OT_RemeshSnap,
            MULTICAMPROJECT_OT_ResetObject, MULTICAMPROJECT_OT_RetopoEmpty,
            MULTICAMPROJECT_OT_RetopoFocus, MULTICAMPROJECT_OT_RemoveProjection,
            MULTICAMPROJECT_OT_UnlinkOriginal)


def _face_menu(self, context):
    self.layout.separator()
    self.layout.operator(MULTICAMPROJECT_OT_RemeshSetFaces.bl_idname, icon='MOD_REMESH')


_keymaps = []


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        # Ctrl+Shift+ (free in the 3D View, none of Blender's own keys replaced):
        #   K  PolyCut tool       Object + Sculpt Mode (the button goes to Sculpt itself)
        #   D  Decimate Brush     Object + Sculpt Mode
        #   F  its Dyntopo detail Sculpt, only in the Decimate Brush session (poll)
        #   L  Clean Floating     Edit Mode
        poly, dec = (MULTICAMPROJECT_OT_RemeshEnterTool.bl_idname,
                     MULTICAMPROJECT_OT_RemeshDensityBrush.bl_idname)
        for km_name, items in (
                ("Sculpt", ((poly, 'K'), (dec, 'D'),
                            (MULTICAMPROJECT_OT_RemeshDetailSize.bl_idname, 'F'))),
                ("Object Mode", ((poly, 'K'), (dec, 'D'))),
                ("Mesh", ((MULTICAMPROJECT_OT_RemeshCleanFloating.bl_idname, 'L'),))):
            km = kc.keymaps.new(name=km_name, space_type='EMPTY')
            ours = {i for i, _k in items}       # left behind by a reload: not one more set
            for kmi in [k for k in km.keymap_items if k.idname in ours]:
                km.keymap_items.remove(kmi)
            for idname, key in items:
                _keymaps.append((km, km.keymap_items.new(idname, key, 'PRESS', ctrl=True,
                                                         shift=True)))
    bpy.types.Scene.multicamproject_retopo_snap = BoolProperty(
        name="Retopo Snapping", default=True,
        description="0D: snap to the original's surface (Face Project) and the Retopology "
                    "overlay - changes the scene's snapping settings")
    bpy.types.Scene.multicamproject_retopo_plane = BoolProperty(
        name="Start from the Selected Mesh", default=True,
        description="0D: on - the retopo starts as a copy of the other selected mesh (e.g. "
                    "a ROOM_Template part; select it, then the scan active), its UV map as "
                    "uv_normal; off - completely empty, model from nothing")
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(_face_menu)
    bpy.types.VIEW3D_MT_edit_mesh_faces.append(_face_menu)
    core.register()


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    del bpy.types.Scene.multicamproject_retopo_snap
    del bpy.types.Scene.multicamproject_retopo_plane
    core.unregister()
    bpy.types.VIEW3D_MT_edit_mesh_faces.remove(_face_menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(_face_menu)
    marks.unregister()
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
