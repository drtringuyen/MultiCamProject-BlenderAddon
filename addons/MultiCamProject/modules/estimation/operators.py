import bpy
from bpy.props import StringProperty

from . import core


class MULTICAMPROJECT_OT_EstimationCalculate(bpy.types.Operator):
    """Measure every mesh of the OBJECTS collection (area, triangles) and split the Budget
    among them by area. Reads only - nothing is changed"""
    bl_idname = "multicamproject.estimation_calculate"
    bl_label = "Calculate"
    bl_options = {'REGISTER'}

    def execute(self, context):
        s = context.scene.multicamproject_estimation
        coll = core.objects_collection(context)
        if coll is None:
            self.report({'ERROR'}, "No OBJECTS collection (pick it in IO Folders & Collections)")
            return {'CANCELLED'}
        dg = context.evaluated_depsgraph_get()
        found = []
        for obj in coll.all_objects:
            if obj.type == 'MESH':
                area, tris = core.measure(obj, dg)
                found.append((obj, area, tris))
        found.sort(key=lambda f: -f[1])
        budgets = core.split([a for _o, a, _t in found], s.budget, s.minimum)
        s.rows.clear()
        for (obj, area, tris), budget in zip(found, budgets):
            row = s.rows.add()
            row.name, row.area, row.tris, row.budget = obj.name, area, tris, budget
        s.collection_name, s.calculated_budget = coll.name, s.budget
        self.report({'INFO'}, f"{len(found)} objects in {coll.name}")
        return {'FINISHED'}


class MULTICAMPROJECT_OT_EstimationSelect(bpy.types.Operator):
    """Select this object and make it active"""
    bl_idname = "multicamproject.estimation_select"
    bl_label = "Select Object"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty()

    def execute(self, context):
        obj = context.scene.objects.get(self.object_name)
        if obj is None:
            self.report({'WARNING'}, f"{self.object_name} is not in this scene")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects:
            o.select_set(False)
        try:
            obj.select_set(True)
        except RuntimeError:
            self.report({'WARNING'}, f"{obj.name} is hidden or excluded")
            return {'CANCELLED'}
        context.view_layer.objects.active = obj
        return {'FINISHED'}


CLASSES = (MULTICAMPROJECT_OT_EstimationCalculate, MULTICAMPROJECT_OT_EstimationSelect)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
