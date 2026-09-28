"""Remesh regions - on the scan object. A Cut region is a polyline drawn in the viewport,
kept as its view rays so the cutter prism can be rebuilt when Near/Far change."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def _on_depth(self, context):
    from . import cutter
    cutter.rebuild_prism(self.id_data, self)


def _on_table(self, context):
    from . import core
    core.update_table(self.id_data)


def _on_preview(self, context):
    from . import core
    core.set_preview(self.id_data, self.preview)


class MULTICAMPROJECT_RemeshRay(bpy.types.PropertyGroup):
    origin: FloatVectorProperty(size=3, subtype='XYZ')
    direction: FloatVectorProperty(size=3, subtype='XYZ')


class MULTICAMPROJECT_RemeshRegion(bpy.types.PropertyGroup):
    region_id: IntProperty(name="ID", min=1, description="Value of remesh_region on this region's faces")
    kind: EnumProperty(items=(('CUT', "Cut", "Straight polyline cut", 'MOD_BOOLEAN', 0),
                              ('FACESET', "Face Set", "Copied from a Sculpt face set (no cut)",
                               'FACE_MAPS', 1)))
    ratio: FloatProperty(
        name="Ratio", default=1.0, min=0.0, max=1.0, subtype='FACTOR', update=_on_table,
        description="Share of this region's faces kept by the decimation (1 = untouched)")
    delete: BoolProperty(
        name="Delete", default=False, update=_on_table, description="Remove this region's faces")
    near: FloatProperty(
        name="Near", unit='LENGTH', update=_on_depth,
        description="Start of the cut, as depth from the view it was drawn in")
    far: FloatProperty(
        name="Far", unit='LENGTH', update=_on_depth,
        description="End of the cut, as depth from the view it was drawn in. "
                    "Keep it short of surfaces behind the region")
    forward: FloatVectorProperty(size=3, subtype='XYZ')
    rays: CollectionProperty(type=MULTICAMPROJECT_RemeshRay)
    cutter: PointerProperty(type=bpy.types.Object)
    face_count: IntProperty(description="Faces in the region at the last cut (decimate preview)")


class MULTICAMPROJECT_RemeshData(bpy.types.PropertyGroup):
    regions: CollectionProperty(type=MULTICAMPROJECT_RemeshRegion)
    active: IntProperty()
    next_id: IntProperty(default=1)
    preview: BoolProperty(
        name="Preview", default=True, update=_on_preview,
        description="Evaluate the cuts, deletes and decimation live. Off: the scan passes through")
    collection: PointerProperty(type=bpy.types.Collection, description="MCP_Remesh_<obj>: the table")
    cutters: PointerProperty(type=bpy.types.Collection, description="Its child: the cutters only")
    table: PointerProperty(type=bpy.types.Object)
    backup: PointerProperty(type=bpy.types.Mesh, description="The mesh before the last Apply")
    source_mesh: StringProperty(description="Name of the mesh the backup was taken from")


_CLASSES = (MULTICAMPROJECT_RemeshRay, MULTICAMPROJECT_RemeshRegion, MULTICAMPROJECT_RemeshData)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.multicamproject_remesh = PointerProperty(type=MULTICAMPROJECT_RemeshData)


def unregister():
    del bpy.types.Object.multicamproject_remesh
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
