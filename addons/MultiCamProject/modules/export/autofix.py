"""Export's automatic fixes, run after the confirm dialog: names, transforms, a Smart UV
uv_normal where there is none, and (re)baking whatever is outdated or not baked.
What cannot be fixed (shared meshes, overlapping hand-made UVs, ...) is only warned about."""
import os
from collections import namedtuple

import bpy

from ..baking import cache, common, fingerprint, jobs, naming, normal
from . import checks, fixes, status

Plan = namedtuple("Plan", "rename transforms make_uv bake unused warnings")

SMART_UV_ANGLE = 1.15192        # 66 degrees, Blender's default


def plan(scene):
    """What Export will do to the EXPORT objects before writing the FBX."""
    objs, per, _g = status.scene_status(scene)
    rename = fixes.planned_names(objs, naming.scheme(scene))
    transforms, make_uv, bake, warnings = [], [], [], []
    from ..baking import handmade
    for o in objs:
        issues = per[o.name].issues
        codes = {i.code for i in issues}
        if handmade.is_handmade(o):     # names only: never unwrapped, baked or transformed
            warnings += [f"{o.name}: {i.text}" for i in issues
                         if i.code in {'HANDMADE', 'FILE'} and i.severity == checks.ERROR]
            continue
        if 'TRANSFORM' in codes:
            transforms.append(o)
        if 'DECIMATE' in codes:         # no Smart UV or bake on the dense mesh
            warnings.append(f"{o.name}: Decimate not applied (03) - left out of the FBX")
            continue
        if not common.has_uv_normal(o):
            make_uv.append(o)
        d = common.data(o)
        if (o in make_uv or d.alb_image is None or d.nor_image is None or d.material is None
                or codes & {'FILE', 'TEXTURE'} or fingerprint.is_outdated(o)
                or fingerprint.ba_outdated(o)):
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


# ---------------------------------------------------------------- one object (the list's fix column)

Steps = namedtuple("Steps", "decimate rename transform make_uv bake")


def object_steps(obj, issues):
    """What the row's Fix button does for `obj`, in order. Unlike Export it also applies a
    live Decimate (03) - the user asked for this object explicitly."""
    from ..baking import handmade
    codes = {i.code for i in issues}
    rename = 'NAME' in codes
    if handmade.is_handmade(obj):       # names only, never unwrapped or baked
        return Steps(False, rename, False, False, False)
    decimate = 'DECIMATE' in codes
    make_uv = decimate or not common.has_uv_normal(obj)
    d = common.data(obj)
    bake = (make_uv or d.alb_image is None or d.nor_image is None or d.material is None
            or bool(codes & {'FILE', 'TEXTURE', 'NOT_BAKED'}) or fingerprint.is_outdated(obj)
            or fingerprint.ba_outdated(obj))
    if bake and not obj.material_slots and common.cp_modifier(obj) is None:
        bake = False                    # nothing to bake from
    return Steps(decimate, rename, 'TRANSFORM' in codes, make_uv, bake)


STEP_TEXT = {"decimate": "apply the Decimate (03) and unwrap uv_normal again",
             "rename": "rename (object, MAT_, ALB_/NOR_)", "transform": "apply the transform",
             "make_uv": "make uv_normal (Smart UV)", "bake": "bake (albedo + normal)"}


def describe(steps):
    return [STEP_TEXT[k] for k in Steps._fields if getattr(steps, k)
            and not (k == "make_uv" and steps.decimate)]


def apply_decimate_unwrap(context, obj):
    """03 + a new uv_normal: the old one was made on the dense mesh and the Decimate
    collapses across its seams. Returns (faces before, after)."""
    from ..remesh import workflow as wf       # export works without remesh: only here
    before, after = wf.apply_decimate(context, obj)
    uv = obj.data.uv_layers.get(common.UV_NORMAL)
    if uv is not None:
        obj.data.uv_layers.remove(uv)
    make_uv_normal(context, obj)
    cache.clear(obj)
    return before, after


def run_object(context, obj, log):
    """run_object_steps, blocking."""
    return jobs.run_sync(run_object_steps(context, obj, log))


def run_object_steps(context, obj, log):
    """Make `obj` ready: the Steps of object_steps. `log(severity, text)`. A generator (jobs)."""
    from ..baking import matsync
    from ..baking import operators as bake_ops
    scene = context.scene
    _objs, per, _g = status.scene_status(scene)
    steps = object_steps(obj, per[obj.name].issues)
    if steps.decimate:
        before, after = apply_decimate_unwrap(context, obj)
        log('INFO', f"{obj.name}: Decimate applied ({before:,} -> {after:,} faces), "
                    "uv_normal made by Smart UV Project")
    if steps.rename:
        sc = naming.scheme(scene)
        want = fixes.planned_names(common.export_objects(scene), sc).get(obj)
        old = obj.name
        for sev, text in fixes.rename_all([obj], sc, plan={obj: want} if want else {}):
            log(sev, text)
        jobs.rename_items([obj], [old])
    for text in matsync.sync(obj, scene):
        log('INFO', f"{obj.name}: {text}")
    if steps.transform:
        why = fixes.fix_transform(obj)
        cache.clear(obj)
        log('WARNING' if why else 'INFO',
            f"{obj.name}: transform {'not fixed, ' + why if why else 'applied'}")
    if steps.make_uv and not common.has_uv_normal(obj):
        make_uv_normal(context, obj)
        log('INFO', f"{obj.name}: uv_normal made by Smart UV Project")
    if steps.bake:
        yield from _bake_logged(context, obj, scene, log)
    cache.clear()
    return steps


def make_uv_normal(context, obj):
    """A new uv_normal by Smart UV Project (non-overlapping, 0-1). The active and render
    UV maps stay as they were."""
    s = common.settings(context.scene)
    res = common.resolution(obj, context.scene)
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
                                         island_margin=2.0 * common.margin_px(s, res) / res,
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


def _bake_logged(context, obj, scene, log):
    """Bake `obj` (albedo + the normal source it was made with), logged. A generator;
    returns True when it baked."""
    from ..baking import operators as bake_ops
    src = _nor_source(obj, scene)
    done, failed = yield from bake_ops.bake_objects_steps(context, [obj], albedo=True,
                                                          nor_source=src)
    for name, err in failed:
        log('ERROR', f"{name}: bake failed - {err}")
    if done:
        log('INFO', f"{obj.name}: baked (normal: {normal.LABELS.get(src, src)})")
    return bool(done)


def run(context, p, log):
    """run_steps, blocking."""
    return jobs.run_sync(run_steps(context, p, log))


def run_steps(context, p, log):
    """Apply plan `p`. `log(severity, text)` collects what happened. A generator (jobs);
    returns False when the user stopped it (the rest is not baked)."""
    scene = context.scene
    yield jobs.Step("Names, materials, transforms")
    objs = common.export_objects(scene)
    old = [o.name for o in objs]
    for sev, text in fixes.rename_all(objs, naming.scheme(scene)):
        log(sev, text)
    jobs.rename_items(objs, old)
    from ..baking import matsync
    for obj in matsync._duplicates(objs) + objs:
        for text in matsync.sync(obj, scene):
            log('INFO', f"{obj.name}: {text}")
    for obj in p.transforms:
        why = fixes.fix_transform(obj)
        cache.clear(obj)
        log('WARNING' if why else 'INFO',
            f"{obj.name}: transform {'not fixed, ' + why if why else 'applied, origin at bottom center'}")
    for obj in p.make_uv:
        yield jobs.Step(f"{obj.name}: uv_normal (Smart UV)")
        make_uv_normal(context, obj)
        log('INFO', f"{obj.name}: uv_normal made by Smart UV Project")
    for obj in p.bake:
        if jobs.stop_requested():
            jobs.skip_rest()
            log('WARNING', "Stopped by the user - the rest is not baked, nothing exported")
            cache.clear()
            return False
        yield from _bake_logged(context, obj, scene, log)
    if jobs.stop_requested():       # stopped during the last object
        log('WARNING', "Stopped by the user - nothing exported")
        cache.clear()
        return False
    for path in fixes.to_recycle_bin(checks.unused_textures(scene)):
        log('INFO', f"Unused {os.path.basename(path)} moved to the Recycle Bin")
    cache.clear()
    return True
