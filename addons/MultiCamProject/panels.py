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


class MULTICAMPROJECT_PT_Setup(bpy.types.Panel):
    """Setup: Setup File IO (collections + folders), then 0A / 0B / 0C (or 0D) in any order - each adds its part to the same object:
    MCP_ + MAT_, the camera lists, the Bake Source, EXPORT. 0E: new UVs for a handmade one"""
    bl_label = "Setup"
    bl_idname = "MULTICAMPROJECT_PT_setup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(icon='SETTINGS')

    def draw(self, context):
        from . import module_manager as mm
        layout = self.layout
        obj = context.active_object
        mesh = obj is not None and obj.type == 'MESH'
        if mm.is_loaded("linking"):
            # the IO Folders & Collections panel's Auto Detect and Fill Folders
            row = layout.row(align=True)
            row.scale_y = 1.3
            row.operator("multicamproject.auto_fill_folders", text="Setup File IO",
                         icon='VIEWZOOM')
        if not mesh:
            layout.label(text="Select a mesh", icon='INFO')
        cams = any(o.type == 'CAMERA' for o in context.scene.objects)
        col = layout.column(align=True)
        col.scale_y = 1.3

        if mm.is_loaded("project_sides"):
            row = col.row(align=True)
            # the default start while the scene has no camera
            row.operator("multicamproject.project_sides", text="0A. Project from Sides",
                         icon='AXIS_SIDE', depress=not cams)
            row.popover(panel="MULTICAMPROJECT_PT_project_sides", text="", icon='PREFERENCES')

        setup = mesh and hasattr(obj, "multicamproject_cam") and obj.multicamproject_cam.is_setup
        if mm.is_loaded("camera_project"):
            row = col.row(align=True)
            row.enabled = cams
            row.operator("multicamproject.setup", text="0B. Setup Camera Projection",
                         icon='CHECKMARK' if setup else 'CAMERA_DATA')
            if setup and mm.is_loaded("remesh"):
                row.operator("multicamproject.remove_projection", text="", icon='X')

        low = False
        if mm.is_loaded("remesh"):
            from .modules.remesh import workflow as wf
            low = mesh and wf.is_low_poly(obj)
            row = col.row(align=True)
            row.operator("multicamproject.remesh", text="0C. Remesh",
                         icon='CHECKMARK' if low else 'MOD_REMESH')
            row.operator("multicamproject.remesh_use_existing", text="", icon='LINKED')
            if mesh and mm.is_loaded("baking") and obj.multicamproject_bake.bake_source is not None:
                row.operator("multicamproject.unlink_original", text="", icon='X')
            row.operator("multicamproject.reset_object", text="", icon='LOOP_BACK')
            retopo = low and wf.is_retopo(obj)
            row = col.row(align=True)
            row.operator("multicamproject.retopo_empty", text="0D. Retopo Empty",
                         icon='CHECKMARK' if retopo else 'MESH_PLANE')
            row.prop(context.scene, "multicamproject_retopo_snap", text="", icon='SNAP_ON')

        hand = mesh and mm.is_loaded("baking") and obj.multicamproject_bake.handmade
        if hand:
            from .modules.baking import handmade
            row = col.row(align=True)
            if not handmade.has_uv_old(obj):
                row.operator("multicamproject.handmade_edit_uv", text="0E. Rebake: Edit UV",
                             icon='UV')
            else:
                row.operator("multicamproject.handmade_rebake", text="0E. Rebake from uv_old",
                             icon='FILE_REFRESH')
                row.operator("multicamproject.handmade_edit_uv", text="", icon='UV')
                row.operator("multicamproject.handmade_finish_uv", text="", icon='CHECKMARK')
                row.operator("multicamproject.handmade_cancel_uv", text="", icon='X')

        if not mesh:
            return
        # what the object has now
        info = layout.column(align=True)
        info.active = False
        if not cams:
            info.label(text="No camera in the scene: start with 0A", icon='INFO')
        if hand and handmade.has_uv_old(obj):
            warn = layout.row()
            if obj.mode == 'EDIT':          # the mesh holds the UVs only after Edit Mode
                warn.active = False
                warn.label(text="Editing uv_normal - then 0E Rebake", icon='UV')
            elif handmade.needs_rebake(obj):
                warn.alert = True
                warn.label(text="uv_normal changed - Rebake (or Cancel X)", icon='ERROR')
            else:
                warn.active = False
                warn.label(text="Textures match uv_normal - Finish, or edit more", icon='CHECKMARK')
        parts = []
        if setup:
            parts.append(f"{len(obj.multicamproject_cam.cameras)} cameras")
        if low:
            if wf.is_retopo(obj):
                parts.append("retopo")
            src = obj.multicamproject_bake.bake_source
            parts.append(f"source: {src.name}" if src else "no Bake Source")
        if hasattr(obj, "multicamproject_bake"):
            d = obj.multicamproject_bake
            parts.append("BAo_ baked" if d.ba_image else "no BAo_")
        if parts:
            info.label(text="  ·  ".join(parts), icon='OBJECT_DATA')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Infos)
    bpy.utils.register_class(MULTICAMPROJECT_PT_MainPanel)
    bpy.utils.register_class(MULTICAMPROJECT_PT_Setup)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Setup)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_MainPanel)
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Infos)
