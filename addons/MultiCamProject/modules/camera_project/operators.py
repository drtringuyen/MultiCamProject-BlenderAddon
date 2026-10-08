import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import core, gn_builder, paint_sync, wrapper


def _mesh_poll(context):
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and context.mode == 'OBJECT'


def _setup_poll(context):
    return _mesh_poll(context) and core.data(context.active_object).is_setup


LIST_MODES = {'OBJECT', 'EDIT_MESH', 'PAINT_VERTEX', 'SCULPT'}
# Bake Camera Mixture: from these too (applied in Object Mode, then back)
BAKE_MODES = LIST_MODES | {'PAINT_TEXTURE', 'PAINT_WEIGHT'}


def _list_poll(context):
    """The camera list's buttons: also while editing, vertex painting or sculpting."""
    obj = context.active_object
    return (obj is not None and obj.type == 'MESH' and context.mode in LIST_MODES
            and core.data(obj).is_setup)


def _report_warnings(op, warnings):
    for w in warnings:
        op.report({'WARNING'}, w)


class MULTICAMPROJECT_OT_Setup(bpy.types.Operator):
    """0B: project the scene's cameras (their Background images) onto the active mesh -
    the node groups, the Processing material MCP_ and the modifier - then detect the
    cameras that see it"""
    bl_idname = "multicamproject.setup"
    bl_label = "Setup Camera Projection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _mesh_poll(context):
            cls.poll_message_set("Object Mode, a mesh active")
            return False
        if not any(o.type == 'CAMERA' for o in context.scene.objects):
            cls.poll_message_set("No camera in the scene - start with 0A Project from Sides")
            return False
        return True

    def execute(self, context):
        obj = context.active_object
        was_setup = core.data(obj).is_setup
        warnings = core.setup(obj, context.scene)
        _report_warnings(self, warnings)
        if not was_setup:
            _route_after(obj)
        n = len(core.data(obj).cameras)
        if n == 0:
            self.report({'WARNING'}, "No camera sees this object")
        else:
            self.report({'INFO'}, f"{n} camera(s) see '{obj.name}'")
        return {'FINISHED'}


def _route_after(obj):
    """0B added the projection: the Bake Route follows (baking module)."""
    try:
        from ..baking import route
    except ImportError:
        return
    if hasattr(obj, "multicamproject_bake"):
        route.after_step(obj, 'PROJECTION')


class MULTICAMPROJECT_OT_ReloadAll(bpy.types.Operator):
    """Reload all camera images from the folder, apply clipping, re-score the cameras,
    re-check Camera 1/2/3 and rewire the node setup and material"""
    bl_idname = "multicamproject.reload_all"
    bl_label = "Reload All"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _setup_poll(context)

    def execute(self, context):
        obj = context.active_object
        _report_warnings(self, core.refresh(obj, context.scene))
        self.report({'INFO'}, f"{len(core.data(obj).cameras)} camera(s) see '{obj.name}'")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_AssignSlot(bpy.types.Operator):
    """Use this camera for projection slot 1-6 (swaps if it is already in another slot)"""
    bl_idname = "multicamproject.assign_slot"
    bl_label = "Assign Camera Slot"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty()
    slot: IntProperty(min=1, max=6, default=1)

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    @classmethod
    def description(cls, context, props):
        return f"Project this camera as Camera {props.slot}"

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        if not core.image_ok(core.cam_image(cam)):
            self.report({'WARNING'}, f"'{cam.name}' has no loaded image - it will project black")
        core.assign_slot(context.active_object, cam, self.slot, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_AutoPick(bpy.types.Operator):
    """Resort: score every camera again and pick the best for the slots - Camera 1 looks
    most along +-Y, 2 along +-X, 3 along +-Z, 4-6 the most coverage left (only cameras
    that see the object and pass the coverage filter)"""
    bl_idname = "multicamproject.auto_pick"
    bl_label = "Resort Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def execute(self, context):
        obj = context.active_object
        warning = core.auto_pick(obj, context.scene)
        if warning:
            self.report({'WARNING'}, warning)
        names = [c.name if c else "-" for c in core.get_slots(core.data(obj))]
        self.report({'INFO'}, "Cameras: " + ", ".join(names))
        return {'FINISHED'}


class MULTICAMPROJECT_OT_CheckSlots(bpy.types.Operator):
    """Refresh: measure the camera list again (Selected + Other Cameras), keep the picked
    cameras that still see the object, fill empty slots from the list, then load them into
    the material and GN - the object's own material in its slot, each Cam texture holding
    its camera's photo (fetched from the folder when missing)"""
    bl_idname = "multicamproject.check_slots"
    bl_label = "Refresh Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def execute(self, context):
        fixes, warnings = core.check_slots(context.active_object, context.scene)
        _report_warnings(self, warnings)
        if fixes:
            self.report({'INFO'}, "Fixed: " + "; ".join(fixes))
        elif not warnings:
            self.report({'INFO'}, "Camera textures match the slots")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_MeasureCoverage(bpy.types.Operator):
    """Measure again how much of the object each camera sees (the list and its filter).
    Photos are not loaded and the Camera 1-6 slots stay as they are"""
    bl_idname = "multicamproject.measure_coverage"
    bl_label = "Measure Coverage"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def execute(self, context):
        obj = context.active_object
        core.measure_coverage(obj, context.scene)
        d = core.data(obj)
        shown = sum(core.passes(d, it) for it in d.cameras)
        self.report({'INFO'}, f"{len(d.cameras)} camera(s) see '{obj.name}', "
                              f"{shown} pass the coverage filter")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RemoveCamera(bpy.types.Operator):
    """Remove this camera from the object's list - Reload All and the coverage refresh
    leave it out until Restore. Shortcut: X over the sidebar while a camera is soloed"""
    bl_idname = "multicamproject.remove_camera"
    bl_label = "Remove Camera from List"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty()

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None:
            return {'CANCELLED'}
        obj = context.active_object
        _top, rest = core.display_order(obj)
        cams = [it.camera for it in rest]
        i = cams.index(cam) if cam in cams else -1
        core.remove_camera(obj, cam)
        # the camera below takes its place (the one above at the end of the list)
        nxt = cams[i + 1] if 0 <= i < len(cams) - 1 else (cams[i - 1] if i > 0 else None)
        if nxt is not None and MULTICAMPROJECT_OT_SoloCamera.poll(context):
            bpy.ops.multicamproject.solo_camera(camera=nxt.name)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_RestoreCameras(bpy.types.Operator):
    """Bring the removed cameras back into this object's list"""
    bl_idname = "multicamproject.restore_cameras"
    bl_label = "Restore Removed Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _list_poll(context) and len(core.data(context.active_object).removed) > 0

    @classmethod
    def description(cls, context, props):
        obj = context.active_object
        n = len(core.data(obj).removed) if obj and obj.type == 'MESH' else 0
        return f"Bring the {n} removed camera(s) back into this object's list"

    def execute(self, context):
        core.restore_cameras(context.active_object, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ToggleGlobal(bpy.types.Operator):
    """Global camera (globe): shared by every object with one UV shift, image and clipping
    left alone by Reload All. Object camera: shift per object"""
    bl_idname = "multicamproject.toggle_global"
    bl_label = "Toggle Global Camera"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty()

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    @classmethod
    def description(cls, context, props):
        cam = bpy.data.objects.get(props.camera)
        if core.is_global(cam):
            return "Global camera - click to attach it to the objects (shift per object)"
        return "Object camera - click to make it global (one shift for every object)"

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        core.set_global(cam, not core.is_global(cam), context.active_object, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SoloCamera(bpy.types.Operator):
    """Solo the active object in local view and look through this camera
    (click again to leave solo)"""
    bl_idname = "multicamproject.solo_camera"
    bl_label = "Solo Camera View"

    camera: StringProperty()

    @classmethod
    def poll(cls, context):
        # also while painting/editing, to switch the camera being painted through
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and context.mode in LIST_MODES
                and context.area and context.area.type == 'VIEW_3D')

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"Camera '{self.camera}' not found")
            return {'CANCELLED'}
        obj = context.active_object
        space = context.space_data
        rv3d = space.region_3d
        scene = context.scene
        if scene.objects.get(cam.name) != cam:
            # e.g. its collection was deleted - only the slots still reference it
            self.report({'ERROR'}, f"'{cam.name}' is no longer in this scene - "
                                   "Reload All removes it from the list")
            return {'CANCELLED'}

        key = str(space.as_pointer())
        state = _load_state(context, key)
        if state and space.local_view is None:   # left local view by hand
            _restore_camera(state, space)
            _restore_collections(context, state)
            state = None
            _save_state(context, key, None)

        if is_solo(context, cam):
            if state:
                _restore_camera(state, space)
            bpy.ops.view3d.localview(frame_selected=False)
            rv3d.view_perspective = 'PERSP'
            if state:
                space.overlay.show_overlays = state["overlays"]
                _restore_collections(context, state)
            _save_state(context, key, None)
            return {'FINISHED'}

        if space.local_view is None:
            selected = list(context.selected_objects)
            for o in selected:
                o.select_set(False)
            obj.select_set(True)
            bpy.ops.view3d.localview(frame_selected=False)
            obj.select_set(False)
            for o in selected:
                o.select_set(True)
            state = {"overlays": space.overlay.show_overlays}
        elif state is None:
            state = {"overlays": space.overlay.show_overlays}
        else:
            _restore_camera(state, space)   # switching camera inside solo

        # Camera background images are drawn only when overlays are on and the
        # camera object itself is visible in this (local) view.
        bg = core.bg_entry(cam)
        state.update(cam=cam.name, hidden=cam.hide_get(),
                     depth=bg.display_depth if bg else "")
        _reveal_collections(context, cam, state, space, keep=(obj, cam))
        _save_state(context, key, state)
        cam.hide_set(False)
        cam.local_view_set(space, True)
        cam.data.show_background_images = True
        if bg:
            bg.show_background_image = True
            bg.display_depth = 'FRONT'   # BACK is hidden behind Material Preview
        space.overlay.show_overlays = True

        scene.camera = cam
        space.camera = cam   # local view looks through the view's own camera
        rv3d.view_perspective = 'CAMERA'
        show_in_list(obj, cam)
        if not cam.visible_get():
            self.report({'WARNING'}, f"'{cam.name}' is in a hidden collection - "
                                     "its background image cannot be drawn")
        return {'FINISHED'}


# Solo state lives on the window manager (an ID), so it survives addon reloads:
# wm["multicamproject_solo"][<space pointer>] = {"overlays", "cam", "hidden", "depth", "colls"}
_WM_KEY = "multicamproject_solo"


def _load_state(context, key):
    states = context.window_manager.get(_WM_KEY)
    if states is None or key not in states:
        return None
    return states[key].to_dict()


def _save_state(context, key, state):
    wm = context.window_manager
    if _WM_KEY not in wm:
        wm[_WM_KEY] = {}
    states = wm[_WM_KEY]
    if state is None:
        if key in states:
            del states[key]
    else:
        states[key] = state


def _restore_camera(state, space):
    """Undo what solo changed on the previously soloed camera."""
    cam = bpy.data.objects.get(state.pop("cam", ""))
    if cam is None:
        return
    bg = core.bg_entry(cam)
    if bg and state.get("depth"):
        bg.display_depth = state["depth"]
    if space.local_view is not None:
        cam.local_view_set(space, False)
    cam.hide_set(state.get("hidden", False))


def _layer_path(layer, coll):
    """Layer collections from the view layer's root down to `coll` (root excluded)."""
    for child in layer.children:
        if child.collection == coll:
            return [child]
        sub = _layer_path(child, coll)
        if sub:
            return [child] + sub
    return []


def _reveal_collections(context, cam, state, space, keep):
    """A camera in an excluded/hidden collection is not drawn, and neither is its
    background image. Solo includes the collection (and its parents) while soloed;
    the local view still shows only the object and this camera. The first state seen
    is kept in state["colls"] for _restore_collections."""
    saved = state.setdefault("colls", {})
    opened = []
    for coll in cam.users_collection:
        for lc in _layer_path(context.view_layer.layer_collection, coll):
            c = lc.collection
            if lc.exclude or lc.hide_viewport or c.hide_viewport:
                saved.setdefault(c.name, [lc.exclude, lc.hide_viewport, c.hide_viewport])
                lc.exclude = lc.hide_viewport = c.hide_viewport = False
                opened.append(c)
    if not opened:
        return
    context.view_layer.update()     # the camera needs its base before local_view_set
    # re-including a collection during local view puts ALL its objects into the view
    for c in opened:
        for o in c.all_objects:
            if o not in keep and o.local_view_get(space):
                o.local_view_set(space, False)


def _restore_collections(context, state):
    """Put the collections _reveal_collections opened back as they were."""
    for name, (exclude, hide, coll_hide) in state.get("colls", {}).items():
        coll = bpy.data.collections.get(name)
        path = _layer_path(context.view_layer.layer_collection, coll) if coll else []
        if path:
            lc = path[-1]
            lc.hide_viewport, coll.hide_viewport = hide, coll_hide
            lc.exclude = exclude


def _redraw_sidebars():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()


def show_in_list(obj, cam, force=False):
    """Make `cam` the active row of the Other Cameras list. Blender scrolls a list to its
    active row only when a redraw sees that row change - so with `force` (the row is
    already active but scrolled away) the list is drawn once without an active row and
    the row is set back right after."""
    d = core.data(obj)
    i = next((n for n, it in enumerate(d.cameras) if it.camera == cam), None)
    if i is None:
        return
    if force and d.cam_index == i:
        d["cam_index"] = -1     # raw ID writes: no update callback
        _redraw_sidebars()
        name = obj.name

        def _restore():
            o = bpy.data.objects.get(name)
            if o is not None:
                core.data(o)["cam_index"] = i
                _redraw_sidebars()

        bpy.app.timers.register(_restore, first_interval=0.05)
        return
    d.cam_index = i     # its update skips: the camera is already soloed


def is_solo(context, cam):
    space = context.space_data
    if space is None or space.type != 'VIEW_3D' or space.local_view is None:
        return False
    return context.scene.camera == cam and space.region_3d.view_perspective == 'CAMERA'


class MULTICAMPROJECT_OT_LoadCamImage(bpy.types.Operator):
    """Pick the photo for this camera (its background image)"""
    bl_idname = "multicamproject.load_cam_image"
    bl_label = "Load Camera Image"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty(options={'HIDDEN'})
    filepath: StringProperty(subtype='FILE_PATH')
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def invoke(self, context, event):
        cam = bpy.data.objects.get(self.camera)
        folder = core.data(context.active_object).image_folder
        img = core.cam_image(cam) if cam else None
        if img and img.filepath:
            self.filepath = bpy.path.abspath(img.filepath)
        elif folder:
            self.filepath = os.path.join(bpy.path.abspath(folder), "")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera)
        if cam is None or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, "No camera or file")
            return {'CANCELLED'}
        core.set_cam_image_path(cam, self.filepath)
        obj = context.active_object
        if cam in core.get_slots(core.data(obj)):
            core.apply_slots(obj, context.scene)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_LoadShift(bpy.types.Operator):
    """Load this object's pixel shift onto the camera's background image offset
    (use when the camera is shared with other objects)"""
    bl_idname = "multicamproject.load_shift"
    bl_label = "Load Shifting"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty()

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def execute(self, context):
        obj = context.active_object
        cam = bpy.data.objects.get(self.camera)
        if core.is_global(cam):
            core.push_global_shift(cam)
            return {'FINISHED'}
        it = core.shift_item(obj, cam) if cam else None
        if it is None:
            self.report({'ERROR'}, f"No shift stored for '{self.camera}'")
            return {'CANCELLED'}
        core.push_shift(obj, it, context.scene, to_camera=True)
        return {'FINISHED'}


# Smooth is Shift+drag while painting (Blender keymap). A panel cannot see held keys,
# so the icons do not change with Shift - the tooltips tell.
# icon: a UI icon name, or "tool:<handle>" for one of Blender's toolbar icons
# standard UI icons: they sit centered in a button (toolbar icons are drawn large and clip)
CAM_BRUSHES = (('PAINT', "Paint", 'BRUSH_DATA'),
               ('FLOOD', "Flood", 'GP_DRAW_FILL'),
               ('ERASE', "Erase", 'EVENT_TABLET_ERASER'))


def flood_ready(context, obj):
    """Flood needs selected faces: Edit Mode with faces selected, or Vertex Paint with
    the face selection mask on."""
    if context.mode == 'EDIT_MESH':
        return obj.data.count_selected_items()[2] > 0
    return context.mode == 'PAINT_VERTEX' and obj.data.use_paint_mask


class MULTICAMPROJECT_OT_CamPaint(bpy.types.Operator):
    """Vertex Paint this camera's color: cameras 1-3 into VCMix, 4-6 into VCMix2"""
    bl_idname = "multicamproject.cam_paint"
    bl_label = "Paint Camera"
    bl_options = {'REGISTER', 'UNDO'}

    camera: StringProperty(options={'HIDDEN'})
    mode: EnumProperty(items=[(k, n, "", idx) for idx, (k, n, _) in enumerate(CAM_BRUSHES)],
                       options={'HIDDEN'})
    shift: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and core.data(obj).is_setup
                and context.mode in {'OBJECT', 'EDIT_MESH', 'PAINT_VERTEX'})

    @classmethod
    def description(cls, context, props):
        obj = context.active_object
        cam = bpy.data.objects.get(props.camera)
        slot = core.slot_of(obj, cam) if obj and cam else 0
        layer = core.slot_layer(slot) if slot else "VCMix"
        return {
            'PAINT': f"Paint this camera's color into {layer}.\nShift+drag while painting: smooth"
                     + ("\nA stroke also clears cameras 1-3 under it (on faces this camera sees)"
                        if slot > 3 else ""),
            'FLOOD': f"Fill the selected faces with this camera's color in {layer} "
                     "(Edit Mode, faces selected)",
            'ERASE': "Erase the projection (the alpha of VCMix and VCMix2) - the baked "
                     "texture shows.\nShift+click: bring the projection back",
        }[props.mode]

    def invoke(self, context, event):
        self.shift = event.shift
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        cam = bpy.data.objects.get(self.camera)
        slot = core.slot_of(obj, cam) if cam else 0
        if not slot:
            self.report({'ERROR'}, f"'{self.camera}' is not in a camera slot")
            return {'CANCELLED'}
        if self.mode == 'FLOOD' and not flood_ready(context, obj):
            self.report({'ERROR'}, "Select faces in Edit Mode first")
            return {'CANCELLED'}

        if obj.mode == 'VERTEX_PAINT':
            paint_sync.sync(obj)            # a stroke not synced yet belongs to the old layer
        if context.mode != 'OBJECT':        # edit-mode selection syncs to the mesh here
            bpy.ops.object.mode_set(mode='OBJECT')
        # Erase works on every layer at once: VCMix alpha is the blend mask of all cameras
        layer = "VCMix" if self.mode == 'ERASE' else core.slot_layer(slot)
        for msg in core.ensure_paint_layer(obj, layer):
            self.report({'INFO'}, msg)
        claims = layer == paint_sync.L2 and self.mode != 'ERASE'
        if claims:
            paint_sync.begin_session(obj)   # the brush's alpha then marks the stroke
        else:
            paint_sync.end_session(obj)     # VCMix2 alpha back before VCMix strokes mirror it
        bpy.ops.object.mode_set(mode='VERTEX_PAINT')
        paint_sync.reset(obj)

        color = core.SLOT_COLORS[slot]
        if self.mode == 'ERASE':
            core.set_paint_brush(context, blend='ADD_ALPHA' if self.shift else 'ERASE_ALPHA')
        else:
            core.set_paint_brush(context, color)
        if claims:
            context.tool_settings.vertex_paint.brush.use_alpha = True     # Affect Alpha: the marker
        if self.mode != 'ERASE':
            # strokes stop at the visible surface - never through the mesh to its far side
            context.tool_settings.vertex_paint.brush.use_frontface = True
        if self.mode == 'FLOOD':
            obj.data.use_paint_mask = True
            bpy.ops.paint.vertex_color_set(use_alpha=True)
            paint_sync.sync(obj)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_MaskFill(bpy.types.Operator):
    """Set the blend mask on the whole object: All Projected (both alphas 1) or All Baked
    (both 0, BA_ shows). Painting and Set Faces go on from there"""
    bl_idname = "multicamproject.mask_fill"
    bl_label = "Fill Blend Mask"
    bl_options = {'REGISTER', 'UNDO'}

    value: bpy.props.FloatProperty(default=1.0, min=0.0, max=1.0, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        return ("VCMix and VCMix2 alpha = 1 on the whole object - the projection shows "
                "everywhere" if props.value >= 0.5 else
                "Clear Alpha: VCMix and VCMix2 alpha = 0 on the whole object - the projection "
                "is erased everywhere (Erase on the whole mesh at once)")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and core.data(obj).is_setup
                and context.mode in {'OBJECT', 'PAINT_VERTEX', 'EDIT_MESH', 'SCULPT'})

    def execute(self, context):
        import numpy as np
        obj = context.active_object
        mode = obj.mode
        if mode == 'VERTEX_PAINT':
            paint_sync.sync(obj)
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        core.ensure_paint_layer(obj)
        me = obj.data
        for name in gn_builder.LAYERS:
            a = me.color_attributes.get(name)
            if a is None:
                continue
            buf = np.empty(len(a.data) * 4, np.float32)
            a.data.foreach_get("color", buf)
            buf[3::4] = self.value
            a.data.foreach_set("color", buf)
        st = me.attributes.get(paint_sync.STASH)
        if st is not None:
            st.data.foreach_set("value", np.full(len(st.data), self.value, np.float32))
        mod = core.get_modifier(obj)
        if mod is not None and core.get_input(mod, "Previous Bake") < 1.0:
            core.set_input(mod, "Previous Bake", 1.0)       # the mesh's layers show 1:1
        obj.update_tag()
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=mode)
        if mode == 'VERTEX_PAINT':
            paint_sync.reset(obj)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ResetCameraMix(bpy.types.Operator):
    """Reset Camera Mix: which camera shows where (VCMix / VCMix2 R, G, B) from the cameras
    again - the blend mask (projected / baked, the alphas) stays. For a mix that shows all
    cameras averaged, or after a mesh edit"""
    bl_idname = "multicamproject.reset_camera_mix"
    bl_label = "Reset Camera Mix"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _setup_poll(context)

    def execute(self, context):
        obj = context.active_object
        done = core.reset_camera_mix(obj)
        if not done:
            self.report({'WARNING'}, "Nothing reset (no painted layers, or a live Decimate)")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Camera mix reset: {', '.join(done)} (mask kept)")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_BakeViewMix(bpy.types.Operator):
    """Bake the camera mixture: apply the projection modifier (UV_cam + VCMix go into the
    mesh) and re-add it with the same settings, so the baked mixture becomes the base for
    the next blend (Previous Bake)"""
    bl_idname = "multicamproject.bake_view_mix"
    bl_label = "Bake Camera Mixture"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.mode in BAKE_MODES
                and core.data(obj).is_setup and core.get_modifier(obj) is not None)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Bake Camera Mixture",
            message="Apply the projection into the mesh and re-add the modifier?",
            confirm_text="Bake")

    def execute(self, context):
        """From Sculpt / Paint / Edit Mode too: applied in Object Mode, then back."""
        mode = context.active_object.mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            return self._bake(context)
        finally:
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)

    def _bake(self, context):
        obj = context.active_object
        me = obj.data
        mod = core.get_modifier(obj)
        idx = list(obj.modifiers).index(mod)
        if idx != 0:
            self.report({'ERROR'}, f"'{mod.name}' must be first in the modifier stack "
                                   "(apply or move the modifiers above it)")
            return {'CANCELLED'}
        if me.shape_keys:
            self.report({'ERROR'}, "Mesh has shape keys - cannot apply a modifier")
            return {'CANCELLED'}
        # count objects, not me.users - a fake user would inflate that
        if sum(o.data == me for o in bpy.data.objects) > 1:
            self.report({'ERROR'}, "Mesh data is shared by several objects - make it single user first")
            return {'CANCELLED'}
        # check before applying - a failure after the apply leaves the modifier half-wired
        # (ensure_modifier also updates an outdated group, keeping the user's settings)
        mod = core.ensure_modifier(obj)
        group = wrapper.shared(mod.node_group)
        missing = core.missing_inputs(group, core.slot_count(core.data(obj)))
        if missing:
            self.report({'ERROR'}, f"Node group '{group.name}' is missing inputs: "
                                   f"{', '.join(missing)}")
            return {'CANCELLED'}
        d = core.data(obj)
        slots = core.get_slots(d)
        if None in slots or not all(core.image_ok(core.cam_image(c)) for c in slots):
            self.report({'WARNING'}, "Some slots are empty or without image - baked areas may be black")

        keep = {k: core.get_input(mod, k) for k in core.KEEP_INPUTS}
        name = mod.name
        core.remove_drivers(obj, mod)
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=name)
        # the applied VCMix2 alpha already holds the stash (the GN stored the larger one)
        stash = me.attributes.get(paint_sync.STASH)
        if stash is not None:
            me.attributes.remove(stash)
        paint_sync._sessions.discard(obj.name)

        new = core.ensure_modifier(obj)
        new.name = name
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=new.name, index=idx)
        for k, v in keep.items():
            core.set_input(new, k, v)
        core.apply_slots(obj, context.scene)
        self.report({'INFO'}, "Camera mixture baked - raise Previous Bake to blend on top of it")
        return {'FINISHED'}


def _sidebar_solo_poll(context):
    """Keyboard shortcuts act only over this addon's sidebar tab while a camera is
    soloed; otherwise the keys keep their usual job."""
    region = context.region
    return (region is not None and region.type == 'UI'
            and region.active_panel_category == "MultiCamProject"
            and MULTICAMPROJECT_OT_SoloCamera.poll(context)
            and core.data(context.active_object).is_setup
            and is_solo(context, context.scene.camera))


class MULTICAMPROJECT_OT_SoloAssign(bpy.types.Operator):
    """Use the soloed camera as Camera 1-6"""
    bl_idname = "multicamproject.solo_assign"
    bl_label = "Assign Soloed Camera"
    bl_options = {'REGISTER', 'UNDO'}

    slot: IntProperty(min=1, max=6, default=1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _sidebar_solo_poll(context)

    def execute(self, context):
        if self.slot > core.slot_count(core.data(context.active_object)):
            return {'PASS_THROUGH'}     # slot not in use: the key keeps its usual job
        cam = context.scene.camera
        if not core.image_ok(core.cam_image(cam)):
            self.report({'WARNING'}, f"'{cam.name}' has no loaded image - it will project black")
        core.assign_slot(context.active_object, cam, self.slot, context.scene)
        self.report({'INFO'}, f"'{cam.name}' is Camera {self.slot}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SoloRemove(bpy.types.Operator):
    """Remove the soloed camera from the object's list; the camera below is soloed next"""
    bl_idname = "multicamproject.solo_remove"
    bl_label = "Remove Soloed Camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # only a camera of the Other Cameras list - the slots are changed with 1-6
        return (_sidebar_solo_poll(context)
                and context.scene.camera not in core.get_slots(core.data(context.active_object)))

    def execute(self, context):
        return bpy.ops.multicamproject.remove_camera(camera=context.scene.camera.name)


class MULTICAMPROJECT_OT_SoloFrame(bpy.types.Operator):
    """Scroll the camera list to the soloed camera"""
    bl_idname = "multicamproject.solo_frame"
    bl_label = "Show Soloed Camera"

    @classmethod
    def poll(cls, context):
        return _sidebar_solo_poll(context)

    def execute(self, context):
        show_in_list(context.active_object, context.scene.camera, force=True)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_SoloStep(bpy.types.Operator):
    """Solo the camera above/below the soloed one in the camera list"""
    bl_idname = "multicamproject.solo_step"
    bl_label = "Solo Next Camera"

    step: IntProperty(default=1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _sidebar_solo_poll(context)

    def execute(self, context):
        top, rest = core.display_order(context.active_object)
        cams = [it.camera for it in top + rest]
        cur = context.scene.camera
        if cur not in cams:
            i = 0
        else:
            k = cams.index(cur)
            # a big step (Left / Right: 5) stops at the first / last camera
            i = min(max(k + self.step, 0), len(cams) - 1) if abs(self.step) > 1 else k + self.step
            if i == k:
                return {'CANCELLED'}
        if not 0 <= i < len(cams):
            return {'CANCELLED'}        # already at the top/bottom of the list
        return bpy.ops.multicamproject.solo_camera(camera=cams[i].name)


class MULTICAMPROJECT_OT_CamStep(bpy.types.Operator):
    """Solo the camera above/below the soloed one in the camera list (the first one when
    no camera is soloed)"""
    bl_idname = "multicamproject.cam_step"
    bl_label = "Step Camera"

    step: IntProperty(default=1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _list_poll(context) and MULTICAMPROJECT_OT_SoloCamera.poll(context)

    execute = MULTICAMPROJECT_OT_SoloStep.execute       # the arrow keys' step, as a button


def camera_layers(context, obj):
    """The layer collections (Outliner rows) holding the cameras of `obj`'s list - not the
    scene's root."""
    out = []
    root = context.view_layer.layer_collection
    colls = {c for it in core.data(obj).cameras if it.camera for c in it.camera.users_collection}
    for c in colls:
        path = _layer_path(root, c)
        if path and path[-1] not in out:
            out.append(path[-1])
    return out


def cameras_hidden(context, obj):
    layers = camera_layers(context, obj)
    return bool(layers) and all(lc.hide_viewport for lc in layers)


class MULTICAMPROJECT_OT_ToggleCameraFolder(bpy.types.Operator):
    """Hide / show the collection(s) holding the cameras, like the eye in the Outliner.
    Soloing a camera still shows it"""
    bl_idname = "multicamproject.toggle_camera_folder"
    bl_label = "Hide / Show Camera Collection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _list_poll(context)

    def execute(self, context):
        obj = context.active_object
        layers = camera_layers(context, obj)
        if not layers:
            self.report({'WARNING'}, "The cameras are in the scene's root collection - "
                                     "put them in a collection to hide them at once")
            return {'CANCELLED'}
        hide = not cameras_hidden(context, obj)
        for lc in layers:
            lc.hide_viewport = hide
        names = ", ".join(lc.name for lc in layers)
        self.report({'INFO'}, f"{'Hidden' if hide else 'Shown'}: {names}")
        return {'FINISHED'}


_classes = (
    MULTICAMPROJECT_OT_Setup,
    MULTICAMPROJECT_OT_MaskFill,
    MULTICAMPROJECT_OT_ResetCameraMix,
    MULTICAMPROJECT_OT_AutoPick,
    MULTICAMPROJECT_OT_CheckSlots,
    MULTICAMPROJECT_OT_MeasureCoverage,
    MULTICAMPROJECT_OT_RemoveCamera,
    MULTICAMPROJECT_OT_RestoreCameras,
    MULTICAMPROJECT_OT_ToggleGlobal,
    MULTICAMPROJECT_OT_ReloadAll,
    MULTICAMPROJECT_OT_AssignSlot,
    MULTICAMPROJECT_OT_SoloCamera,
    MULTICAMPROJECT_OT_SoloStep,
    MULTICAMPROJECT_OT_CamStep,
    MULTICAMPROJECT_OT_ToggleCameraFolder,
    MULTICAMPROJECT_OT_SoloAssign,
    MULTICAMPROJECT_OT_SoloFrame,
    MULTICAMPROJECT_OT_SoloRemove,
    MULTICAMPROJECT_OT_LoadCamImage,
    MULTICAMPROJECT_OT_LoadShift,
    MULTICAMPROJECT_OT_CamPaint,
    MULTICAMPROJECT_OT_BakeViewMix,
)

_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:      # None in background mode
        # "Frames" (every region) binds Up/Down to Jump to Keyframe and is handled before
        # the 3D view's own keymaps, eating the keys even with no keyframe to jump to.
        # Add-on items come first in a keymap, so here the poll decides: sidebar + solo
        # -> step the camera, anything else -> falls through to Jump to Keyframe.
        km = kc.keymaps.new(name="Frames")
        # Left / Right: 5 cameras at a time (elsewhere they keep stepping frames)
        for key, step in (('UP_ARROW', -1), ('DOWN_ARROW', 1),
                          ('LEFT_ARROW', -5), ('RIGHT_ARROW', 5)):
            kmi = km.keymap_items.new(MULTICAMPROJECT_OT_SoloStep.bl_idname, key, 'PRESS')
            kmi.properties.step = step
            _keymaps.append((km, kmi))
        kmi = km.keymap_items.new(MULTICAMPROJECT_OT_SoloFrame.bl_idname, 'NUMPAD_PERIOD', 'PRESS')
        _keymaps.append((km, kmi))
        # X over the sidebar: remove the soloed camera from the list (elsewhere X is untouched)
        kmi = km.keymap_items.new(MULTICAMPROJECT_OT_SoloRemove.bl_idname, 'X', 'PRESS')
        _keymaps.append((km, kmi))
        # 1-6 on the number row or numpad: soloed camera -> Camera 1-6
        for slot, keys in ((1, ('ONE', 'NUMPAD_1')), (2, ('TWO', 'NUMPAD_2')), (3, ('THREE', 'NUMPAD_3')),
                           (4, ('FOUR', 'NUMPAD_4')), (5, ('FIVE', 'NUMPAD_5')), (6, ('SIX', 'NUMPAD_6'))):
            for key in keys:
                kmi = km.keymap_items.new(MULTICAMPROJECT_OT_SoloAssign.bl_idname, key, 'PRESS')
                kmi.properties.slot = slot
                _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
