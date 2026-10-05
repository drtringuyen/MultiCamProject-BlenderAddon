"""Linking panel - which work file is linked to which object - and the Send Out / Receive
buttons the EXPORT rows and Cutting & Modelling draw."""
import os

import bpy

from . import core, operators as ops


def _state(obj):
    """(text, icon) of a linked object's window."""
    if core.new_send(obj) is not None:
        return "New Send Back to receive", 'IMPORT'
    if core.sent_back(obj) is not None:
        return "Received - up to date", 'CHECKMARK'
    return "Waiting for Send Back", 'TIME'


def _file_text(obj):
    path = core.saved_window(obj)
    return (os.path.basename(path), 'FILE_BLEND') if path else ("Unsaved work window", 'WINDOW')


# ---------------------------------------------------------------- the panel

class MULTICAMPROJECT_PT_Linking(bpy.types.Panel):
    """Work windows: which work file is linked to which object"""
    bl_label = "Linking"
    bl_idname = "MULTICAMPROJECT_PT_linking"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 5

    def draw_header(self, context):
        self.layout.label(icon='LINKED')

    def draw(self, context):
        layout = self.layout
        if core.is_work_window(context.scene):
            draw_work(layout, context)
            return
        obj = context.active_object
        if obj is not None and obj.type == 'MESH' and not obj.library and core.out_record(obj) is None:
            box = layout.box().column(align=True)
            box.label(text=f"{obj.name}: not linked", icon='UNLINKED')
            row = box.row(align=True)
            row.operator(ops.MULTICAMPROJECT_OT_WorkSendOut.bl_idname, text="Send Out",
                         icon='WINDOW').object_name = obj.name
            row.operator(ops.MULTICAMPROJECT_OT_WorkRelink.bl_idname, text="Relink File...",
                         icon='FILEBROWSER').object_name = obj.name
        linked = core.linked_objects()
        if not linked:
            layout.label(text="No object is linked to a work window", icon='INFO')
            return
        layout.label(text=f"Linked objects ({len(linked)})")
        for o in linked:
            box = layout.box().column(align=True)
            row = box.row(align=True)
            op = row.operator(ops.MULTICAMPROJECT_OT_WorkSelect.bl_idname, text=o.name,
                              icon='RESTRICT_SELECT_OFF', depress=o == obj, emboss=o != obj)
            op.object_name = o.name
            text, icon = _state(o)
            box.label(text=text, icon=icon)
            ftext, ficon = _file_text(o)
            row = box.row(align=True)
            sub = row.row(align=True)
            sub.active = bool(core.saved_window(o))
            sub.label(text=ftext, icon=ficon)
            draw_row_button(row, o, relink=True)


# ---------------------------------------------------------------- pieces for other panels

def draw_row_button(layout, obj, relink=False):
    """Send Out, or Receive (blue once sent back) [+ open] [+ relink] + X - the EXPORT rows,
    the Linking list."""
    rec = core.out_record(obj)
    if rec is None:
        cell = layout.row(align=True)
        cell.ui_units_x = 1.1
        cell.operator(ops.MULTICAMPROJECT_OT_WorkSendOut.bl_idname, text="",
                      icon='WINDOW').object_name = obj.name
        return
    ready = core.new_send(obj) is not None
    saved = bool(core.saved_window(obj))
    cell = layout.row(align=True)
    cell.ui_units_x = 1.1 * (2 + saved + relink)
    sub = cell.row(align=True)
    sub.enabled = ready
    sub.operator(ops.MULTICAMPROJECT_OT_WorkReceive.bl_idname, text="", icon='IMPORT',
                 depress=ready).object_name = obj.name
    if saved:
        cell.operator(ops.MULTICAMPROJECT_OT_WorkOpen.bl_idname, text="",
                      icon='FILE_BLEND').object_name = obj.name
    if relink:
        cell.operator(ops.MULTICAMPROJECT_OT_WorkRelink.bl_idname, text="",
                      icon='FILEBROWSER').object_name = obj.name
    cell.operator(ops.MULTICAMPROJECT_OT_WorkCancel.bl_idname, text="",
                  icon='X').object_name = obj.name


def draw_main(layout, context, obj):
    """Cutting & Modelling in the main file: Send Out, or Receive (Replace / Add) + X."""
    rec = core.out_record(obj)
    if rec is None:
        layout.operator(ops.MULTICAMPROJECT_OT_WorkSendOut.bl_idname,
                        text="Send Out to Work Window", icon='WINDOW').object_name = obj.name
        return
    ready = core.new_send(obj) is not None
    col = layout.column(align=True)
    info = col.row()
    info.active = False
    info.label(text=core.link_text(obj), icon='LINKED')
    row = col.row(align=True)
    sub = row.row(align=True)
    sub.enabled = ready
    op = sub.operator(ops.MULTICAMPROJECT_OT_WorkReceive.bl_idname,
                      text="Receive" if ready else "Waiting for Send Back",
                      icon='IMPORT', depress=ready)
    op.object_name, op.action = obj.name, 'REPLACE'
    add = sub.row(align=True)
    add.ui_units_x = 3.0
    op = add.operator(ops.MULTICAMPROJECT_OT_WorkReceive.bl_idname, text="Add", icon='ADD')
    op.object_name, op.action = obj.name, 'ADD'
    if core.saved_window(obj):
        row.operator(ops.MULTICAMPROJECT_OT_WorkOpen.bl_idname, text="",
                     icon='FILE_BLEND').object_name = obj.name
    row.operator(ops.MULTICAMPROJECT_OT_WorkCancel.bl_idname, text="",
                 icon='X').object_name = obj.name


def draw_work(layout, context):
    """A work window: where it came from, Send Back, where its bakes go."""
    box = layout.box().column(align=True)
    scene = context.scene
    main = os.path.basename(scene.get(core.WORK_KEY, ""))
    box.label(text=f"Work window: {scene.get(core.WORK_OBJECT_KEY, '')} of {main}",
              icon='LINKED')
    row = box.row(align=True)
    row.scale_y = 1.4
    row.operator(ops.MULTICAMPROJECT_OT_WorkSendBack.bl_idname, icon='EXPORT')
    info = box.column(align=True)
    info.active = False
    info.label(text="Mesh, painting, cameras + bakes made here · Send Back any time",
               icon='INFO')
    if bpy.data.filepath:
        info.label(text=f"Saved: {os.path.basename(bpy.data.filepath)} (main file can open it)",
                   icon='FILE_BLEND')
    else:
        info.label(text="Unsaved · saving keeps it linked (bakes -> 01.Baking/_work)",
                   icon='WINDOW')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Linking)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Linking)
