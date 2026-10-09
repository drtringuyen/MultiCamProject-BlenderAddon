import bpy

from . import core


def _k(n):
    """150000 -> '150K', 2400 -> '2.4K', 830 -> '830'."""
    if n >= 1000000:
        return f"{n / 1000000:.2f}M"
    if n >= 10000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def _line(col):
    """(name cell, number cells): the name gets a third of the width, the 4 numbers share
    the rest equally."""
    split = col.row(align=True).split(factor=0.34, align=True)
    return split.row(align=True), split.row(align=True)


class MULTICAMPROJECT_PT_Estimation(bpy.types.Panel):
    """Polycount Estimation: the triangle budget of the OBJECTS collection, split by area"""
    bl_label = "Polycount Estimation"
    bl_idname = "MULTICAMPROJECT_PT_estimation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiCamProject"
    bl_parent_id = "MULTICAMPROJECT_PT_main"
    bl_order = 6
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='MOD_DECIM')

    def draw(self, context):
        layout = self.layout
        s = context.scene.multicamproject_estimation
        row = layout.row(align=True)
        row.prop(s, "budget")
        row.prop(s, "minimum")
        row = layout.row()
        row.scale_y = 1.3
        coll = core.objects_collection(context)
        row.enabled = coll is not None
        row.operator("multicamproject.estimation_calculate",
                     text=f"Calculate {coll.name}" if coll else "No OBJECTS collection",
                     icon='FILE_REFRESH')
        if not s.rows:
            return
        if s.calculated_budget != s.budget:
            layout.label(text="Budget changed: Calculate again", icon='ERROR')

        total_area = sum(r.area for r in s.rows)
        box = layout.box()
        col = box.column(align=True)
        name, nums = _line(col)
        name.label(text="Object")
        for text in ("Area", "Budget", "Now", "Remove"):
            nums.label(text=text)
        for r in s.rows:
            name, nums = _line(col)
            obj = context.scene.objects.get(r.name)
            cut = core.removable(r.budget, r.tris)
            name.operator("multicamproject.estimation_select", text=r.name, emboss=False,
                          depress=obj is not None and obj == context.active_object,
                          icon='CHECKMARK' if cut == 0 else 'MESH_DATA').object_name = r.name
            nums.label(text=f"{r.area:.2f} m²")
            nums.label(text=_k(r.budget))
            nums.label(text=_k(r.tris))
            nums.label(text=f"{cut * 100:.0f}%")

        tris = sum(r.tris for r in s.rows)
        budget = sum(r.budget for r in s.rows)
        col.separator()
        name, nums = _line(col)
        name.label(text=f"{len(s.rows)} objects")
        nums.label(text=f"{total_area:.2f} m²")
        nums.label(text=_k(budget))
        nums.label(text=_k(tris))
        nums.label(text=f"{core.removable(budget, tris) * 100:.0f}%")
        if budget > s.calculated_budget:
            box.label(text=f"Minimums alone exceed the budget ({_k(budget)})", icon='ERROR')


def register():
    bpy.utils.register_class(MULTICAMPROJECT_PT_Estimation)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_PT_Estimation)
