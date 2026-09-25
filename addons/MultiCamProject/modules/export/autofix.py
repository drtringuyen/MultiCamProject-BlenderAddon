"""Export's automatic fixes, run after the confirm dialog: names, transforms, a Smart UV
uv_normal where there is none, and (re)baking whatever is outdated or not baked.
What cannot be fixed (shared meshes, overlapping hand-made UVs, ...) is only warned about."""
import os
from collections import namedtuple

import bpy

from ..baking import cache, common, fingerprint, naming, normal
from . import checks, fixes, status

Plan = namedtuple("Plan", "rename transforms make_uv bake unused warnings")

SMART_UV_ANGLE = 1.15192        # 66 degrees, Blender's default


def plan(scene):
    """What Export will do to the EXPORT objects before writing the FBX."""
    objs, per, _g = status.scene_status(scene)
    rename = fixes.planned_names(objs, naming.scheme(scene))
    transforms, make_uv, bake, warnings = [], [], [], []
    for o in objs:
        issues = per[o.name].issues
        codes = {i.code for i in issues}
        if 'TRANSFORM' in codes:
            transforms.append(o)
        if not common.has_uv_normal(o):
            make_uv.append(o)
        d = common.data(o)
        if (o in make_uv or d.alb_image is None or d.nor_image is None or d.material is None
                or codes & {'FILE', 'TEXTURE'} or fingerprint.is_outdated(o)):
            bake.append(o)
        for i in issues:
            if i.code in {'SHARED_MESH', 'UV_BAD', 'MESH', 'UV_EXTRA', 'COLOR'}:
                warnings.append(f"{o.name}: {i.text}")
        if not o.material_slots and common.cp_modifier(o) is None:
            warnings.append(f"{o.name}: no material to bake - left out of the FBX")
    for sc in checks.other_scenes(scene):
        warnings.append(f"Other scene '{sc.name}' ({sc.cameras} cameras) is not exported")
    # PNGs in the bake folder no image uses (old names, removed objects) - after the renames
    # and bakes below the list is made again, this one is for the dialog
    unused = checks.unused_textures(scene)
    return Plan(rename, transforms, make_uv, bake, unused, warnings)


def make_uv_normal(context, obj):
    """A new uv_normal by Smart UV Project (non-overlapping, 0-1). The active and render
    UV maps stay as they were."""
    s = common.settings(context.scene)
    me = obj.data
    uvs = me.uv_layers
    prev_active = uvs.active.name if uvs.active else ""
    prev_render = next((u.name for u in uvs if u.active_render), "")
    uv = uvs.new(name=common.UV_NORMAL, do_init=False)
    uvs.active = uv
    from ..baking.engine import selection
    with selection(context, obj, [obj]):
        with context.temp_override(active_object=obj, object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
            bpy.ops.object.mode_set(mode='EDIT')
            try:
                bpy.ops.mesh.reveal(select=True)
                bpy.ops.mesh.select_all(action='SELECT')
                # margin: twice the bake margin, in UV units
                bpy.ops.uv.smart_project(angle_limit=SMART_UV_ANGLE,
                                         island_margin=2.0 * s.margin / s.resolution,
                                         area_weight=0.0, correct_aspect=True,
                                         scale_to_bounds=False)
            finally:
                bpy.ops.object.mode_set(mode='OBJECT')
    if uvs.get(prev_active):
        uvs.active = uvs[prev_active]
    if uvs.get(prev_render):
        uvs[prev_render].active_render = True
    cache.clear(obj)


def _nor_source(obj, scene):
    """The source the normal map was made with; High-pass when that one cannot run now."""
    s = common.settings(scene)
    src = common.data(obj).nor_source_used or s.nor_source
    if normal.problem(obj, scene, src) and src != 'HIGHPASS':
        return 'HIGHPASS'
    return src


def run(context, p, log):
    """Apply plan `p`. `log(severity, text)` collects what happened."""
    from ..baking import operators as bake_ops
    scene = context.scene
    objs = common.export_objects(scene)
    for sev, text in fixes.rename_all(objs, naming.scheme(scene)):
        log(sev, text)
    for obj in p.transforms:
        why = fixes.fix_transform(obj)
        cache.clear(obj)
        log('WARNING' if why else 'INFO',
            f"{obj.name}: transform {'not fixed, ' + why if why else 'applied, origin at bottom center'}")
    for obj in p.make_uv:
        make_uv_normal(context, obj)
        log('INFO', f"{obj.name}: uv_normal made by Smart UV Project")
    for obj in p.bake:
        src = _nor_source(obj, scene)
        done, failed = bake_ops.bake_objects(context, [obj], albedo=True, nor_source=src)
        for name, err in failed:
            log('ERROR', f"{name}: bake failed - {err}")
        if done:
            log('INFO', f"{obj.name}: baked (normal: {normal.LABELS.get(src, src)})")
    for path in fixes.to_recycle_bin(checks.unused_textures(scene)):
        log('INFO', f"Unused {os.path.basename(path)} moved to the Recycle Bin")
    cache.clear()
