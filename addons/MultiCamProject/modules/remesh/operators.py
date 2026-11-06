import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from ... import module_manager
from . import core, marks, tool, workflow as wf


def _decided(cls, obj):
    """PolyCut, its pick and the Decimate Brush wait for the Decimate decision."""
    if wf.decimate_pending(obj):
        cls.poll_message_set("Decide the Decimate first (Apply)")
        return False
    return True


def _enter_tool(context, obj):
    """The object in Sculpt Mode with the PolyCut tool active."""
    if obj.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')
    try:
        bpy.ops.wm.tool_set_by_id(name=tool.tool_id())
    except (RuntimeError, TypeError):
        pass        # no 3D view in this context: the mode is set, the tool is one click away


class MULTICAMPROJECT_OT_Remesh(bpy.types.Operator):
    """2B Remesh: make the low-poly copy. It takes the name, EXPORT and the textures, gets
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
        wf.follow_in_estimation(context.scene, copy)
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
    """2C Retopo: as 2B Remesh (name, EXPORT, MCP_ / MAT_ / ALB_ / NOR_, Bake Source),
    but the new object starts as the other selected mesh (e.g. a TEMPLATE part, its UV
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
        wf.follow_in_estimation(context.scene, new)
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
    """Sculpt Mode with the PolyCut tool (Ctrl+Click: PolyCut, then Set Seam or No Seam)"""
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
    Face then Edge select mode, the selection is inverted and every floating piece is deleted,
    then every loose vertex and edge (the select mode comes back as it was)"""
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
        obj.update_from_editmode()
        before = len(obj.data.polygons), len(obj.data.vertices)
        mode = tuple(context.tool_settings.mesh_select_mode)
        bpy.ops.mesh.select_linked(delimit={'NORMAL'})
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_mode(type='EDGE')
        bpy.ops.mesh.select_all(action='INVERT')    # everything but the kept mesh
        bpy.ops.mesh.delete(type='EDGE')
        # what Edge mode can't select: vertices without an edge (and edges without a face)
        bpy.ops.mesh.select_mode(type='VERT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)
        bpy.ops.mesh.select_all(action='DESELECT')
        context.tool_settings.mesh_select_mode = mode   # the select mode as it was
        obj.update_from_editmode()
        faces = before[0] - len(obj.data.polygons)
        verts = before[1] - len(obj.data.vertices)
        self.report({'INFO'}, f"Clean Floating: {faces:,} faces, {verts:,} vertices deleted")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_HideFaces(bpy.types.Operator):
    """Hide (H in Edit Mode): Face select mode first - only whole selected faces are
    hidden, no stray vertices / edges around them - then Blender's Hide"""
    bl_idname = "multicamproject.hide_faces"
    bl_label = "Hide Faces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        if tuple(context.tool_settings.mesh_select_mode) != (False, False, True):
            bpy.ops.mesh.select_mode(type='FACE')
        return bpy.ops.mesh.hide(unselected=False)


_state = {"action": 'PROJECTED', "seam": 'MARK'}


class MULTICAMPROJECT_OT_RemeshSetFaces(bpy.types.Operator):
    """Select & Set: Projected / Baked (the VCMix alphas), Protect from Decimate, Delete,
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
    """Apply Decimate (the end of 2B): the low poly's topology becomes final (protected parts stay as they
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
    """Remove the camera projection from the active mesh only: projection modifier, UV_camN, VCMix, camera
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
            why = "No camera projection on it"
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
    """Remove 2B / 2C's link from the active mesh: no Bake Source, BAo_/BNo_ unlinked (their
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


_CLASSES = (MULTICAMPROJECT_OT_Remesh,
            MULTICAMPROJECT_OT_RemeshEnterTool, MULTICAMPROJECT_OT_RemeshDensityBrush,
            MULTICAMPROJECT_OT_RemeshDetailSize, MULTICAMPROJECT_OT_RemeshCleanFloating,
            MULTICAMPROJECT_OT_HideFaces,
            MULTICAMPROJECT_OT_RemeshSetFaces, MULTICAMPROJECT_OT_RemeshApplyDecimate,
            MULTICAMPROJECT_OT_RemeshUseExisting, MULTICAMPROJECT_OT_RemeshSnap,
            MULTICAMPROJECT_OT_ResetObject, MULTICAMPROJECT_OT_RetopoEmpty,
            MULTICAMPROJECT_OT_RetopoFocus, MULTICAMPROJECT_OT_RemoveProjection,
            MULTICAMPROJECT_OT_UnlinkOriginal)


_keymaps = []


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        # Ctrl+Shift+ (free in the 3D View, none of Blender's own keys replaced):
        #   K  PolyCut tool       Object, Sculpt + Edit Mode (it goes to Sculpt itself)
        #   D  Decimate Brush     Object + Sculpt Mode
        #   F  its Dyntopo detail Sculpt, only in the Decimate Brush session (poll)
        #   L  Clean Floating     Edit Mode
        # and H in Edit Mode: Face mode, then Hide (Blender's H, one step more)
        poly, dec = (MULTICAMPROJECT_OT_RemeshEnterTool.bl_idname,
                     MULTICAMPROJECT_OT_RemeshDensityBrush.bl_idname)
        for km_name, items in (
                ("Sculpt", ((poly, 'K'), (dec, 'D'),
                            (MULTICAMPROJECT_OT_RemeshDetailSize.bl_idname, 'F'))),
                ("Object Mode", ((poly, 'K'), (dec, 'D'))),
                ("Mesh", ((MULTICAMPROJECT_OT_RemeshCleanFloating.bl_idname, 'L'),
                          (poly, 'K')))):
            km = kc.keymaps.new(name=km_name, space_type='EMPTY')
            ours = {i for i, _k in items}       # left behind by a reload: not one more set
            for kmi in [k for k in km.keymap_items if k.idname in ours]:
                km.keymap_items.remove(kmi)
            for idname, key in items:
                _keymaps.append((km, km.keymap_items.new(idname, key, 'PRESS', ctrl=True,
                                                         shift=True)))
        km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
        for kmi in [k for k in km.keymap_items
                    if k.idname == MULTICAMPROJECT_OT_HideFaces.bl_idname]:
            km.keymap_items.remove(kmi)
        _keymaps.append((km, km.keymap_items.new(MULTICAMPROJECT_OT_HideFaces.bl_idname, 'H',
                                                 'PRESS')))
    bpy.types.Scene.multicamproject_retopo_snap = BoolProperty(
        name="Retopo Snapping", default=True,
        description="2C: snap to the original's surface (Face Project) and the Retopology "
                    "overlay - changes the scene's snapping settings")
    bpy.types.Scene.multicamproject_retopo_plane = BoolProperty(
        name="Start from the Selected Mesh", default=True,
        description="2C: on - the retopo starts as a copy of the other selected mesh (e.g. "
                    "a TEMPLATE part; select it, then the scan active), its UV map as "
                    "uv_normal; off - completely empty, model from nothing")
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
    marks.unregister()
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
