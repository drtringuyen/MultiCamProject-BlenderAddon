import bpy
import os
import json


def _build_label():
    """Return 'dd/mm/yy HH:MM' from build_info.json, or 'Build' if not built yet."""
    build_file = os.path.join(os.path.dirname(__file__), "build_info.json")
    if os.path.exists(build_file):
        try:
            with open(build_file, "r") as f:
                data = json.load(f)
            t = data.get("time", "")
            if len(t) >= 16:
                yyyy, mm, dd = t[0:4], t[5:7], t[8:10]
                hhmm = t[11:16]
                return "{}/{}/{} {}".format(dd, mm, yyyy[2:], hhmm)
        except Exception:
            pass
    return "Build"


class MULTICAMPROJECT_PT_Infos(bpy.types.Panel):
    """Infos panel - build time, debug, console"""
    bl_label = "Infos"
    bl_idname = "MULTICAMPROJECT_PT_infos"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_order = 0
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.multicamproject_props

        # Single row: Build info popup + Reload + Debug toggle + Console + Clear
        row = layout.row(align=True)
        row.operator("multicamproject.build", text=_build_label(), icon='RESTRICT_VIEW_ON')
        row.operator("multicamproject.reload", text="", icon='FILE_REFRESH')
        sub = row.row(align=True)
        sub.active_default = props.debug_mode
        sub.operator("multicamproject.toggle_debug", text="", icon='INFO')
        row.operator("multicamproject.toggle_console", text="", icon='CONSOLE')
        row.operator("multicamproject.clear_console", text="", icon='TRASH')

        if props.debug_mode:
            # Modules row — hidden unless debug mode is on
            from . import module_manager
            row = layout.row(align=True)
            row.label(text="Modules:", text_ctxt="extra-info-label")
            for m in module_manager.ALL_MODULES:
                sub = row.row(align=True)
                sub.active_default = module_manager.is_loaded(m["name"])
                sub.operator(m["op"], text=m["name"].capitalize(), icon=m["icon"])
            
            layout.label(text="Version: " + props.addon_version,
                         text_ctxt="extra-info-label")


class MULTICAMPROJECT_PT_MainPanel(bpy.types.Panel):
    """Main panel - modules register subpanels here via bl_parent_id"""
    bl_label = "MultiCamProject"
    bl_idname = "MULTICAMPROJECT_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_order = 1

    def draw(self, context):
        pass


NAME_UNITS = 5      # the New Object Name field at the start of 2B / 2C / 2D


def _step_row(col, context, named, field=True):
    """A 2B / 2C / 2D row: the window buttons' New Object Name first (one setting, on all
    three) - a split of the row, so the fields are equally wide on every row - then the
    returned row for the step's button and icons."""
    row = col.row(align=True)
    if not named:
        return row
    scale = context.preferences.system.ui_scale
    width = max(context.region.width - 30 * scale, 1)
    split = row.split(factor=min(0.45, NAME_UNITS * 20 * scale / width), align=True)
    props = context.scene.multicamproject_props
    cell = split.row(align=True)
    if field:
        from .properties import new_name_taken
        cell.alert = not props.new_object_name.strip() or bool(new_name_taken(context))
        cell.prop(props, "new_object_name", text="", placeholder="Object name")
    else:               # 2D keeps the object (and its name): the place stays empty
        cell.label(text="")
    return split.row(align=True)


def _icons(row, n):
    """The cell for a step's `n` icon buttons, one unit each, flush with the panel's right
    edge (the step button takes the rest of the width)."""
    cell = row.row(align=True)
    cell.ui_units_x = n
    return cell


class MULTICAMPROJECT_PT_Setup(bpy.types.Panel):
    """01. Setup: the file - Name Prefix and Setup File IO (collections + folders); the
    Polycount Estimation under it. Unlocks 02"""
    bl_label = "01. Setup"
    bl_idname = "MULTICAMPROJECT_PT_setup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 0

    def draw_header(self, context):
        from . import gate
        self.layout.label(icon='CHECKMARK' if gate.ready(context.scene) else 'SETTINGS')

    def draw(self, context):
        from . import gate
        gate.draw_start(self.layout, context)       # [Name Prefix] [Setup File IO]


def _chosen(row, chosen, mine):
    """Grey a 02 row when the object already took another workflow (one per object)."""
    row.enabled = chosen in {'NONE', mine}
    return row


class MULTICAMPROJECT_PT_MeshWorkflow(bpy.types.Panel):
    """02. Mesh Workflow: 2A Project from Sides (no cameras yet), then ONE of 2B Remesh /
    2C Retopo / 2D Only Project - here (Local) or in a work window (Send Out). The
    projection always comes along. Done when the Decimate is applied and uv_normal exists"""
    bl_label = "02. Mesh Workflow"
    bl_idname = "MULTICAMPROJECT_PT_mesh_workflow"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 1

    def draw_header(self, context):
        from . import steps
        obj = context.active_object
        done = obj is not None and 2 in steps.state(obj, context.scene).done
        self.layout.label(icon='CHECKMARK' if done else 'MOD_REMESH')

    def draw(self, context):
        from . import gate, module_manager as mm, steps
        layout, _open = gate.step_box(self.layout, context, 2)
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh", icon='INFO')
            return
        chosen = steps.workflow(obj)
        cams = any(o.type == 'CAMERA' for o in context.scene.objects)
        col = layout.column(align=True)
        col.scale_y = 1.3

        if mm.is_loaded("project_sides"):
            row = col.row(align=True)
            # the default start while the scene has no camera
            row.operator("multicamproject.project_sides", text="2A. Project from Sides",
                         icon='AXIS_SIDE', depress=not cams)
            row.popover(panel="MULTICAMPROJECT_PT_project_sides", text="", icon='PREFERENCES')

        named = bool(mm.is_loaded("linking"))     # the window buttons' name: 2B / 2C
        low = False
        if mm.is_loaded("remesh"):
            from .modules.remesh import workflow as wf
            low = wf.is_low_poly(obj)
            from .properties import new_name_taken
            taken = new_name_taken(context) if named else ""
            row = _chosen(_step_row(col, context, named), chosen, 'REMESH')
            if taken:
                row.enabled = False     # the new object would take another object's name
            row.operator("multicamproject.remesh", text="2B. Remesh",
                         icon='CHECKMARK' if chosen == 'REMESH' else 'MOD_REMESH')
            unlink = bool(mm.is_loaded("baking")
                          and obj.multicamproject_bake.bake_source is not None)
            icons = _icons(row, 2 + named + unlink)
            if named:       # Remesh + Send Out in one click
                icons.operator("multicamproject.work_remesh_out", text="",
                               icon='WINDOW').mode = 'REMESH'
            icons.operator("multicamproject.remesh_use_existing", text="", icon='LINKED')
            if unlink:
                icons.operator("multicamproject.unlink_original", text="", icon='X')
            icons.operator("multicamproject.reset_object", text="", icon='LOOP_BACK')

            row = _chosen(_step_row(col, context, named), chosen, 'RETOPO')
            if taken:
                row.enabled = False
            row.operator("multicamproject.retopo_empty", text="2C. Retopo",
                         icon='CHECKMARK' if chosen == 'RETOPO' else 'MESH_PLANE')
            icons = _icons(row, 2 + named)
            if named:       # Retopo + Send Out in one click
                icons.operator("multicamproject.work_remesh_out", text="",
                               icon='WINDOW').mode = 'RETOPO'
            # from the selected mesh (ROOM_Template parts) or empty
            icons.prop(context.scene, "multicamproject_retopo_plane", text="", icon='MESH_DATA')
            icons.prop(context.scene, "multicamproject_retopo_snap", text="", icon='SNAP_ON')
            if taken:
                warn = col.row()
                warn.alert = True
                warn.label(text=f"Name used by {taken} - type another for 2B / 2C",
                           icon='ERROR')

        if mm.is_loaded("camera_project"):
            setup = obj.multicamproject_cam.is_setup
            row = _chosen(_step_row(col, context, named, field=False), chosen, 'PROJECT')
            sub = row.row(align=True)
            sub.enabled = cams
            sub.operator("multicamproject.setup", text="2D. Only Project",
                         icon='CHECKMARK' if chosen == 'PROJECT' else 'CAMERA_DATA')
            window = named and chosen == 'PROJECT'
            x = setup and mm.is_loaded("remesh")
            if window or x:
                icons = row.row(align=True)
                if window:      # Send Out / Receive (the object itself goes out)
                    from .modules.linking import ui as link_ui
                    link_ui.draw_row_button(icons, obj)
                if x:
                    icons.operator("multicamproject.remove_projection", text="", icon='X')

        if chosen == 'NONE':
            hint = layout.row()
            hint.active = False
            hint.label(text="Pick one: 2B Remesh, 2C Retopo or 2D Only Project"
                       if cams else "No camera in the scene: start with 2A", icon='INFO')
            return
        if chosen == 'HANDMADE':
            hint = layout.row()
            hint.active = False
            hint.label(text="Handmade: its UVs change in 03 (Edit UV / Rebake)", icon='INFO')
            return

        # the end of 02: the Decimate decided, then uv_normal
        if mm.is_loaded("remesh"):
            from .modules.remesh import ui as remesh_ui, workflow as wf
            if wf.decimate_pending(obj):
                remesh_ui.draw_decide(layout, context, obj)
                return
        if mm.is_loaded("baking"):
            from .modules.baking import common, operators as bops
            if not common.has_uv_normal(obj):
                box = layout.box().column(align=True)
                box.alert = True
                box.label(text="Last: uv_normal (the bake target)", icon='UV')
                box.alert = False
                if obj.mode == 'EDIT':
                    box.label(text="Leave Edit Mode first", icon='INFO')
                else:
                    bops.draw_uv_fix(box, obj)
                return
        if 2 in steps.state(obj, context.scene).done:
            info = layout.row()
            info.active = False
            parts = [{'REMESH': "Remesh", 'RETOPO': "Retopo", 'PROJECT': "Only Project"}
                     .get(chosen, chosen)]
            if mm.is_loaded("camera_project") and obj.multicamproject_cam.is_setup:
                parts.append(f"{len(obj.multicamproject_cam.cameras)} cameras")
            if low:
                src = obj.multicamproject_bake.bake_source
                parts.append(f"source: {src.name}" if src else "no Bake Source")
            info.label(text="  ·  ".join(parts) + "  ·  uv_normal - go on in 03",
                       icon='CHECKMARK')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Infos)
    bpy.utils.register_class(MULTICAMPROJECT_PT_MainPanel)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Setup)
    bpy.utils.register_class(MULTICAMPROJECT_PT_MeshWorkflow)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_MeshWorkflow)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Setup)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_MainPanel)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Infos)
