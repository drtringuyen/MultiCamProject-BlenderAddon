"""Linking panel - which work file is linked to which object - and the Send Out / Receive
buttons the EXPORT rows and Cutting & Modelling draw."""
import os

import bpy

from ... import folders, gate, roles
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
    """IO Folders & Collections: the collection roles + their folders, and the work windows
    (which work file is linked to which object)"""
    bl_label = "IO Folders & Collections"
    bl_idname = "MULTICAMPROJECT_PT_linking"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 5

    def draw_header(self, context):
        self.layout.label(icon='LINKED')

    def draw(self, context):
        gate.lock(self.layout, context)     # greyed out until Setup File IO
        layout = self.layout
        if core.is_work_window(context.scene):
            draw_work(layout, context)
            return
        draw_roles(layout, context)
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


def draw_roles(layout, context):
    """Which collection plays which role (the EXPORT header's buttons, 07, Remesh) - picked
    here, or found by the usual name when empty - and the role's folder + Reload
    (folders.py). Wide sidebar: the folder sits next to the collection, else below it."""
    scene, vl = context.scene, context.view_layer
    props = scene.multicamproject_props
    wide = context.region.width / context.preferences.system.ui_scale > 560
    box = layout.box().column(align=True)
    box.label(text="Collections + Folders", icon='OUTLINER_COLLECTION')
    for role in roles.ORDER:
        attr, label, icon, _names = roles.ROLES[role]
        row = box.row(align=True)
        if wide:
            split = row.split(factor=0.2, align=True)
            split.label(text=label, icon=icon)
            split = split.split(factor=0.36, align=True)
            _draw_picker(split.row(align=True), scene, vl, props, role, attr)
            _draw_folder(split.row(align=True), scene, role, wide)
        else:
            split = row.split(factor=0.38, align=True)
            split.label(text=label, icon=icon)
            _draw_picker(split.row(align=True), scene, vl, props, role, attr)
            _draw_folder(box.row(align=True), scene, role, wide)
        if getattr(props, attr) is None:
            hint = box.row()
            hint.active = False
            hint.label(text=f"auto: {roles.auto_text(scene, vl, role)}")
        if role != roles.ORDER[-1]:
            box.separator(factor=0.6)
    box.separator(factor=0.6)
    row = box.row(align=True)
    row.scale_y = 1.2
    row.operator(ops.MULTICAMPROJECT_OT_AutoFillFolders.bl_idname,
                 text="Auto Detect and Fill Folders", icon='VIEWZOOM')


def _draw_picker(row, scene, vl, props, role, attr):
    """The collection field; + a create button when Original Mesh / EXPORT is not there."""
    row.prop(props, attr, text="")
    if role in roles.CREATABLE and not roles.collections(scene, vl, role):
        row.operator(ops.MULTICAMPROJECT_OT_CreateRoleCollection.bl_idname, text="",
                     icon='FILE_REFRESH').role = role


def _draw_folder(row, scene, role, wide):
    """'<label>' [folder] [reload] of one role."""
    label = folders.LABELS[role]
    pg, attr = folders.holder(scene, role)
    if pg is None:
        row.active = False
        row.label(text=f"{label}: {attr}", icon='INFO')
        return
    split = row.split(factor=0.3 if wide else 0.38, align=True)
    split.label(text=label, icon='FILE_FOLDER')
    cell = split.row(align=True)
    cell.alert = not folders.exists(folders.folder(scene, role))
    cell.prop(pg, attr, text="")
    cell.operator(ops.MULTICAMPROJECT_OT_ReloadFolder.bl_idname, text="",
                  icon='FILE_REFRESH').role = role


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
        info.label(text="Unsaved · saving keeps it linked (bakes -> bake folder/_work)",
                   icon='WINDOW')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Linking)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Linking)
