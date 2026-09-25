"""Stage of each EXPORT object, computed live. The heavy parts (fingerprints, UV raster,
mesh counts, PNG headers) are cached per object by baking.cache."""
from collections import OrderedDict, namedtuple

from ..baking import common, fingerprint, naming
from . import checks

# stage -> (label, icon, sort order: problems first)
STAGES = OrderedDict((
    ('OUTDATED', ("Outdated", 'STRIP_COLOR_01', 0)),
    ('ISSUES', ("Problems", 'ERROR', 1)),
    ('PROJECTION', ("no uv_normal", 'STRIP_COLOR_02', 2)),
    ('NOT_SET_UP', ("Not set up", 'STRIP_COLOR_09', 3)),
    ('READY_TO_BAKE', ("Ready to bake", 'STRIP_COLOR_03', 4)),
    ('BAKED', ("Baked (no normal)", 'STRIP_COLOR_05', 5)),
    ('READY', ("Ready", 'STRIP_COLOR_04', 6)),
))

Status = namedtuple("Status", "stage issues")


def stage_of(obj, issues):
    d = common.data(obj)
    alb = d.alb_image is not None and common.file_ok(d.alb_image)
    nor = d.nor_image is not None and common.file_ok(d.nor_image)
    if common.cp_modifier(obj) is None and not alb:
        return 'NOT_SET_UP'
    if not common.has_uv_normal(obj):
        return 'PROJECTION'
    if not alb:
        return 'READY_TO_BAKE'
    if fingerprint.is_outdated(obj):
        return 'OUTDATED'
    if not nor or d.material is None:
        return 'BAKED'
    if any(i.severity != checks.INFO for i in issues):
        return 'ISSUES'
    return 'READY'


def object_status(obj, scene, plan):
    issues = checks.object_issues(obj, scene, plan)
    return Status(stage_of(obj, issues), issues)


def scene_status(scene):
    """{object name: Status} for the EXPORT meshes, and the issues grouped by code."""
    from . import fixes
    objs = common.export_objects(scene)
    plan = fixes.planned_names(objs, naming.scheme(scene))
    per = {o.name: object_status(o, scene, plan) for o in objs}
    grouped = OrderedDict()
    for code in checks.SUMMARY:
        names = sorted({i.obj for st in per.values() for i in st.issues if i.code == code})
        if names:
            grouped[code] = names
    return objs, per, grouped


def objects_with(scene, codes):
    objs, per, _g = scene_status(scene)
    return [o for o in objs if any(i.code in codes for i in per[o.name].issues)]
