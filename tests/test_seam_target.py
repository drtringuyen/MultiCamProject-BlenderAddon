"""Seam Fix: the preview stays on its own object when another one shares the materials.

Needs Blender:  blender -b --factory-startup --python tests/test_seam_target.py
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "addons"))


def _mesh(name, uv=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    if uv:
        me.uv_layers.new(name=uv)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    from MultiCamProject.modules.seam_fix import direct, geometry as geo

    mat = bpy.data.materials.new("MAT_shared")
    mat.use_nodes = True
    old = _mesh("A_old_version", "UvCoordinatesAndMapping")    # first in bpy.data.objects
    new = _mesh("B_current", geo.UV_SRC)
    for obj in (old, new):
        obj.data.materials.append(mat)
    assert list(bpy.data.objects).index(old) < list(bpy.data.objects).index(new)

    img = bpy.data.images.new("ALB_test", 4, 4)
    node = mat.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = img
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])

    assert direct.target() is None
    assert direct._install(new, img)
    direct.set_view(new, True)
    assert direct.installed(old), "the shared material reads as previewed on both"
    assert direct.target() == new, f"target() = {direct.target()}"

    direct.set_view(new, False)             # "Before" still counts as a preview
    assert direct.target() == new

    direct.stop()                           # Cancel: gone from the shared material
    assert direct.target() is None
    assert not direct.installed(new) and not direct.installed(old)
    assert direct.VIEW not in new
    assert any(l.from_node == node for l in bsdf.inputs["Base Color"].links), "link not restored"
    print("OK test_seam_target")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)
