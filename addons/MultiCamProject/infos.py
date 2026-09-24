"""Info panel operators: Build popup, Reload, Debug toggle, Console toggle, Clear console."""
import bpy
import os
import json


def _read_build_info():
    build_file = os.path.join(os.path.dirname(__file__), "build_info.json")
    if os.path.exists(build_file):
        try:
            with open(build_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None


class MULTICAMPROJECT_OT_Build(bpy.types.Operator):
    """Show addon info and last build time"""
    bl_idname = "multicamproject.build"
    bl_label = "Build"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.label(text="MultiCamProject", icon='ARMATURE_DATA')
        layout.separator()
        layout.label(text="Project camera background photos onto meshes with blended UVs and vertex-colour weights")
        layout.separator()
        layout.label(text="Version: 0.0.1")
        layout.label(text="Author: ")
        layout.separator()
        build = _read_build_info()
        if build:
            layout.label(text="Last built: " + build.get("time", "Unknown"), icon='TIME')
        else:
            layout.label(text="Not built yet - run install.py first", icon='ERROR')

    def execute(self, context):
        return {'FINISHED'}


class MULTICAMPROJECT_OT_Reload(bpy.types.Operator):
    """Reload addon in Blender (disable -> purge modules -> enable).
    Use when you want to apply in-place changes without running install.py."""
    bl_idname = "multicamproject.reload"
    bl_label = "Reload Addon"

    def execute(self, context):
        import sys
        addon = "MultiCamProject"
        bpy.ops.preferences.addon_disable(module=addon)
        mods = [k for k in sys.modules if k == addon or k.startswith(addon + ".")]
        for m in mods:
            del sys.modules[m]
        bpy.ops.preferences.addon_enable(module=addon)
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ToggleDebug(bpy.types.Operator):
    """Toggle debug mode - show/hide extra-info-label"""
    bl_idname = "multicamproject.toggle_debug"
    bl_label = "Debug"

    def execute(self, context):
        props = context.scene.multicamproject_props
        props.debug_mode = not props.debug_mode
        self.report({'INFO'}, "Debug: " + ("ON" if props.debug_mode else "OFF"))
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ToggleConsole(bpy.types.Operator):
    """Toggle Blender system console"""
    bl_idname = "multicamproject.toggle_console"
    bl_label = "Console"

    def execute(self, context):
        import sys
        if sys.platform == "win32":
            try:
                bpy.ops.wm.console_toggle()
            except AttributeError:
                import subprocess
                subprocess.Popen(
                    'start cmd',
                    shell=True,
                    creationflags=subprocess.DETACHED_PROCESS
                )
        else:
            self.report({'INFO'}, "Use Window > Toggle System Console")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ClearConsole(bpy.types.Operator):
    """Clear the system console output"""
    bl_idname = "multicamproject.clear_console"
    bl_label = "Clear"

    def execute(self, context):
        import sys
        os.system("cls" if sys.platform == "win32" else "clear")
        return {'FINISHED'}



class MULTICAMPROJECT_OT_ToggleCameraProject(bpy.types.Operator):
    """Toggle CameraProject module on/off"""
    bl_idname = "multicamproject.toggle_camera_project"
    bl_label = "CameraProject"

    def execute(self, context):
        from . import module_manager
        module_manager.toggle("camera_project")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_ToggleProjectSides(bpy.types.Operator):
    """Toggle ProjectSides module on/off"""
    bl_idname = "multicamproject.toggle_project_sides"
    bl_label = "ProjectSides"

    def execute(self, context):
        from . import module_manager
        module_manager.toggle("project_sides")
        return {'FINISHED'}

def register():
    bpy.utils.register_class(MULTICAMPROJECT_OT_Build)
    bpy.utils.register_class(MULTICAMPROJECT_OT_Reload)
    bpy.utils.register_class(MULTICAMPROJECT_OT_ToggleDebug)
    bpy.utils.register_class(MULTICAMPROJECT_OT_ToggleConsole)
    bpy.utils.register_class(MULTICAMPROJECT_OT_ClearConsole)
    bpy.utils.register_class(MULTICAMPROJECT_OT_ToggleCameraProject)
    bpy.utils.register_class(MULTICAMPROJECT_OT_ToggleProjectSides)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ToggleProjectSides)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ToggleCameraProject)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ClearConsole)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ToggleConsole)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_ToggleDebug)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_Reload)
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_Build)
