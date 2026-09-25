"""FBX export of the EXPORT collection for Unity (URP).

GN Set Material cannot drop the old material slots, so the FBX is written from temporary
copies: the evaluated Final mesh with the material list trimmed to MAT_, only uv_normal
and Color. The copies carry the original names (the originals step aside while the FBX is
written) and a temporary MAT_ whose images point at <folder>/Textures/, so the FBX's
relative texture paths work inside the Unity project. Nothing of the originals changes.
"""
import os
import shutil
import time
from contextlib import contextmanager

import bpy
import numpy as np

from ..baking import common, gn_final
from . import checks, fixes, status

TMP = "__mcp_export_tmp"
UNITY_SCRIPT = os.path.join(os.path.dirname(__file__), "unity", "MCPTexturePostprocessor.cs")


@contextmanager
def names_aside(ids):
    """Rename `ids` out of the way; their names come back afterwards."""
    saved = []
    try:
        for idb in ids:
            saved.append((idb, idb.name))
            idb.name = idb.name + TMP
        yield
    finally:
        for idb, name in reversed(saved):
            try:
                idb.name = name
            except ReferenceError:
                pass


def copy_textures(objs, folder):
    """ALB_/NOR_ files into <folder>/Textures/ (skipped when size and time match).
    Returns {image name: copied path}."""
    tex = os.path.join(folder, "Textures")
    os.makedirs(tex, exist_ok=True)
    out = {}
    for obj in objs:
        d = common.data(obj)
        for img in (d.alb_image, d.nor_image):
            src = common.image_file(img)
            if not src or not os.path.isfile(src):
                continue
            dst = os.path.join(tex, img.name + ".png")
            st = os.stat(src)
            if (not os.path.isfile(dst) or os.path.getsize(dst) != st.st_size
                    or int(os.path.getmtime(dst)) != int(st.st_mtime)):
                if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
                    shutil.copy2(src, dst)
            out[img.name] = dst
    return out


def _export_material(obj, copies, temps):
    """A temporary copy of MAT_ whose images point at the copied textures."""
    d = common.data(obj)
    if d.material is None:
        return None
    mat = d.material.copy()
    temps["materials"].append(mat)
    for n in mat.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image is not None and n.image.name in copies:
            img = bpy.data.images.load(copies[n.image.name], check_existing=False)
            img.colorspace_settings.name = n.image.colorspace_settings.name
            temps["images"].append((img, n.image.name))
            n.image = img
    return mat


def _trim(me, mat):
    """One material, one UV (uv_normal), one color (Color). Returns what is missing."""
    missing = []
    me.materials.clear()
    if mat is not None:
        me.materials.append(mat)
    else:
        missing.append("MAT_")
    me.polygons.foreach_set("material_index", np.zeros(len(me.polygons), dtype=np.int32))
    for uv in [u for u in me.uv_layers if u.name != common.UV_NORMAL]:
        me.uv_layers.remove(uv)
    if me.uv_layers.get(common.UV_NORMAL) is None:
        missing.append(common.UV_NORMAL)
    else:
        me.uv_layers.active = me.uv_layers[common.UV_NORMAL]
        me.uv_layers[common.UV_NORMAL].active_render = True
    for c in [c for c in me.color_attributes if c.name != common.COLOR]:
        me.color_attributes.remove(c)
    col = me.color_attributes.get(common.COLOR)
    if col is None:
        missing.append(common.COLOR)
    else:
        me.color_attributes.active_color = col
        me.color_attributes.render_color_index = me.color_attributes.active_color_index
    return missing


def make_copies(context, objs, copies, coll, temps):
    """Temporary copies of the evaluated Final objects. Returns {copy: missing items}."""
    dg = context.evaluated_depsgraph_get()
    out = {}
    for obj in objs:
        me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True,
                                             depsgraph=dg)
        temps["meshes"].append((me, obj.data.name))
        mat = _export_material(obj, copies, temps)
        missing = _trim(me, mat)
        cp = bpy.data.objects.new(obj.name + "_copy", me)
        cp.matrix_world = obj.matrix_world
        coll.objects.link(cp)
        temps["objects"].append((cp, obj.name))
        if mat is not None:
            temps["mat_names"].append((mat, common.data(obj).material.name))
        out[cp] = missing
    return out


def write_fbx(context, path, objs):
    from ..baking.engine import selection
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with selection(context, objs[0], objs):
        bpy.ops.export_scene.fbx(
            filepath=path, use_selection=True, object_types={'MESH'},
            use_mesh_modifiers=False,           # the copies are already evaluated
            colors_type='SRGB', prioritize_active_color=False,
            path_mode='RELATIVE', embed_textures=False,
            apply_scale_options='FBX_SCALE_ALL', bake_space_transform=True,
            add_leaf_bones=False, use_tspace=True, mesh_smooth_type='OFF')


def write_unity_script(folder):
    dst = os.path.join(folder, "Editor", "MCPTexturePostprocessor.cs")
    with open(UNITY_SCRIPT, encoding="utf-8") as f:
        text = f.read()
    if os.path.isfile(dst):
        with open(dst, encoding="utf-8") as f:
            if f.read() == text:
                return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return dst


def write_report(folder, scene, files, statuses, missing, seconds, fix_log=()):
    lines = [f"MultiCamProject export - {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Scene: {scene.name}", f"Files: {', '.join(os.path.basename(f) for f in files)}",
             f"Time: {seconds:.1f} s", ""]
    if fix_log:
        lines += ["Automatic fixes:"] + [f"    [{sev}] {text}" for sev, text in fix_log] + [""]
    for name, st in statuses.items():
        lines.append(f"{name}: {status.STAGES[st.stage][0]}")
        for i in st.issues:
            lines.append(f"    [{i.severity}] {i.text}")
        for m in missing.get(name, []):
            lines.append(f"    [ERROR] missing in the FBX: {m}")
    path = os.path.join(folder, "export_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def export(context, fix_log=()):
    """Export the active scene's baked EXPORT meshes (never-baked ones are left out and
    reported). The exported objects stay in Final - Blender then shows what the FBX holds.
    Returns (files, {name: missing or skipped}, report)."""
    scene = context.scene
    es = scene.multicamproject_export
    t0 = time.perf_counter()
    objs, statuses, _g = status.scene_status(scene)
    skipped = [o.name for o in objs if common.data(o).material is None]
    objs = [o for o in objs if common.data(o).material is not None]
    if not objs:
        raise RuntimeError("No baked meshes in the EXPORT collection")
    folder = bpy.path.abspath(es.folder)
    os.makedirs(folder, exist_ok=True)
    finals = {o: gn_final.is_final(o) for o in objs}
    temps = {"objects": [], "meshes": [], "materials": [], "images": [], "mat_names": []}
    coll = bpy.data.collections.new("MCP_EXPORT_TMP")
    scene.collection.children.link(coll)
    files, missing = [], {}
    try:
        for o in objs:
            if not finals[o]:
                gn_final.set_final(o, scene, True)
        context.view_layer.update()
        copies = copy_textures(objs, folder)
        made = make_copies(context, objs, copies, coll, temps)
        context.view_layer.update()
        originals = list(objs) + [o.data for o in objs]
        originals += [common.data(o).material for o in objs if common.data(o).material]
        originals += [img for img in bpy.data.images if img.name in {n for _i, n in temps["images"]}]
        with names_aside(originals):
            for cp, name in temps["objects"]:
                cp.name = name
            for me, name in temps["meshes"]:
                me.name = name
            for mat, name in temps["mat_names"]:
                mat.name = name
            for img, name in temps["images"]:
                img.name = name
            for cp, miss in made.items():
                if miss:
                    missing[cp.name] = miss
            copies_list = [cp for cp, _n in temps["objects"]]
            if es.split == 'PER_OBJECT':
                for cp in copies_list:
                    path = os.path.join(folder, cp.name + ".fbx")
                    write_fbx(context, path, [cp])
                    files.append(path)
            else:
                from ..baking import naming
                name = naming.fbx_name(scene)
                path = os.path.join(folder, name + ".fbx")
                write_fbx(context, path, copies_list)
                files.append(path)
            # the temporaries leave with their borrowed names still on them
            _remove_temps(temps, coll)
            coll = None
    finally:
        if coll is not None:
            _remove_temps(temps, coll)
        from ..baking import cache
        cache.clear()
    # <folder>/Textures/ holds exactly this export's textures
    stale = checks.stale_export_textures(scene, list(copies)) if files else []
    fix_log = list(fix_log) + [('INFO', f"Export Textures/: old {os.path.basename(p)} moved to "
                                        "the Recycle Bin") for p in fixes.to_recycle_bin(stale)]
    if es.write_cs:
        write_unity_script(folder)
    for name in skipped:
        missing[name] = ["not baked - left out of the FBX"]
    report = write_report(folder, scene, files, status.scene_status(scene)[1], missing,
                          time.perf_counter() - t0, fix_log) if es.write_report else ""
    return files, missing, report


def _remove_temps(temps, coll):
    for cp, _n in temps["objects"]:
        bpy.data.objects.remove(cp)
    for me, _n in temps["meshes"]:
        bpy.data.meshes.remove(me)
    for mat in temps["materials"]:
        bpy.data.materials.remove(mat)
    for img, _n in temps["images"]:
        bpy.data.images.remove(img)
    bpy.data.collections.remove(coll)
    for k in temps:
        temps[k] = []
