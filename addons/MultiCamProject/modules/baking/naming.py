"""Names of the exported things, from the scene's Name Prefix `<**>_<scene>`, e.g. 00_30stBR:

    object      ENV_00_30stBR.00_Desk       ENV_<prefix>.<##>_<name>
    material    MAT_00_30stBR.00_Desk       MAT_ + the object name after ENV_
    textures    ALB_00_30stBR.00_Desk       ALB_ / NOR_ + the object name after ENV_
    FBX         ENV_00_30stBR.fbx           ENV_<prefix>

The user names only <name>; ## is a running number the add-on assigns. Without a prefix,
objects only need clean names and MAT_/ALB_/NOR_ are <type>_<object>.
"""
import re
from collections import namedtuple

import bpy

ENV = "ENV"
Scheme = namedtuple("Scheme", "core")       # core: the cleaned prefix, e.g. 00_30stBR

_SUFFIX = re.compile(r"\.\d{3,}$")                  # Blender's .001
_FORM = re.compile(r"^ENV_(?P<core>[A-Za-z0-9_]+)\.(?P<index>\d{2,})_(?P<short>.+)$")
# earlier forms, read only to keep their number and name:
# <prefix>.##_<name> (before ENV_), and names ending in '_##_<name>' (ENV_00_30stBROBJ_00_Desk)
_DOTTED = re.compile(r"^(?P<core>[A-Za-z0-9_]+)\.(?P<index>\d{2,})_(?P<short>.+)$")
_LOOSE = re.compile(r"^.*_(?P<index>\d{2})_(?P<short>[A-Za-z][A-Za-z0-9_]*)$")


def clean_name(name):
    """'Model - 12 - Trashbin.001' -> 'Model_12_Trashbin' (A-Z, 0-9, _ only)."""
    n = _SUFFIX.sub("", name)
    n = re.sub(r"[\s\-.]+", "_", n)
    n = re.sub(r"[^A-Za-z0-9_]", "", n)
    n = re.sub(r"_+", "_", n).strip("_")
    return n or "Object"


def scheme(scene=None):
    """The scene's naming scheme, or None when no Name Prefix is set. A typed 'ENV_' in
    front is dropped - the add-on adds it."""
    scene = scene or bpy.context.scene
    es = getattr(scene, "multicamproject_export", None)
    raw = es.name_prefix.strip() if es is not None else ""
    core = clean_name(raw) if raw else ""
    if core.upper().startswith(ENV + "_"):
        core = core[len(ENV) + 1:]
    return Scheme(core) if core and core != "Object" else None


def parse(name, sc=None):
    """(index, short name) of 'ENV_<prefix>.<##>_<short>' - for `sc`'s prefix. With sc None
    any prefix, and the earlier '<prefix>.<##>_<short>' too. None when not in that form."""
    n = _SUFFIX.sub("", name)
    m = _FORM.match(n)
    if sc is not None:
        return (int(m.group("index")), m.group("short")) if m and m.group("core") == sc.core else None
    m = m or _DOTTED.match(n)
    return (int(m.group("index")), m.group("short")) if m else None


def parse_loose(name):
    """(index, short name) of an older name that ends in '_##_<name>', or None."""
    m = _LOOSE.match(clean_name(name))
    return (int(m.group("index")), m.group("short")) if m else None


def fixed_part(sc, index):
    """The part of the object name the add-on fills in: 'ENV_00_30stBR.01_'."""
    return f"{ENV}_{sc.core}.{index:02d}_"


def full_name(sc, index, short):
    return fixed_part(sc, index) + clean_name(short)


def short_name(name):
    """What the user names: the part after '...<##>_', or the cleaned name."""
    p = parse(name)
    return clean_name(p[1]) if p else clean_name(name)


def fbx_name(scene):
    sc = scheme(scene)
    return f"{ENV}_{sc.core}" if sc else bpy.path.clean_name(scene.name)


def typed_name(kind, obj):
    """MAT_ / ALB_ / NOR_ name of `obj`: its name with ENV_ swapped for <kind>_ when it
    follows the scheme, else <kind>_<object>."""
    sc = scheme()
    if sc and parse(obj.name, sc):
        return f"{kind}_{obj.name[len(ENV) + 1:]}"
    return f"{kind}_{obj.name}"
