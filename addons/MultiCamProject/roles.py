"""Collection roles: which collection holds the cameras, the objects, the Remesh originals
and the export meshes. Each role is the collection picked in the Linking panel (scene
setting, survives toggling modules), else found by its usual name (cameras: every
collection holding only cameras). The EXPORT header's buttons hide / show them; the export
and Remesh code read EXPORT / ORIGINALS through here."""
import bpy

# role: (scene setting, label, icon, usual names); no setting = found by its name only
ROLES = {
    'OBJECTS': ("objects_collection", "Objects", 'MESH_CUBE', ("OBJECTS",)),
    'ORIGINALS': ("originals_collection", "Original Mesh", 'MESH_ICOSPHERE', ("Original Mesh",)),
    'EXPORT': ("export_collection", "Export", 'EXPORT', ("EXPORT",)),
    'CAMERAS': ("cameras_collection", "Cameras", 'OUTLINER_OB_CAMERA', ("CAMERAS",)),
    'ROOM': (None, "Room", 'HOME', ("ROOM",)),
    'TEMPLATE': (None, "Room Template", 'SNAP_VOLUME', ("ROOM_Template",)),
}
ORDER = ('OBJECTS', 'ORIGINALS', 'EXPORT', 'CAMERAS')     # the Linking panel's pickers
HEADER = ORDER + ('ROOM', 'TEMPLATE')                     # the EXPORT header's buttons


def _layers(view_layer):
    """Every layer collection of the view layer (depth first)."""
    out = []

    def walk(lc):
        for child in lc.children:
            out.append(child)
            walk(child)
    walk(view_layer.layer_collection)
    return out


def picked(scene, role):
    """The collection picked for `role` in this scene (None = not picked)."""
    props = getattr(scene, "multicamproject_props", None)
    attr = ROLES[role][0]
    return getattr(props, attr, None) if props is not None and attr else None


def collections(scene, view_layer, role):
    """[(collection, layer collection)] that play `role` in this view layer."""
    layers = _layers(view_layer)
    coll = picked(scene, role)
    if coll is not None:
        return [(lc.collection, lc) for lc in layers if lc.collection == coll]
    if role == 'CAMERAS':
        return [(lc.collection, lc) for lc in layers
                if len(lc.collection.objects)
                and all(o.type == 'CAMERA' for o in lc.collection.objects)]
    names = {n.lower() for n in ROLES[role][3]}
    return [(lc.collection, lc) for lc in layers if lc.collection.name.lower() in names]


def find(scene, role):
    """The one collection of `role` (picked, else by its usual name, in or out of this
    scene), or None - for code that needs a collection, not a view layer."""
    coll = picked(scene, role)
    if coll is not None:
        return coll
    for name in ROLES[role][3]:
        coll = bpy.data.collections.get(name)
        if coll is not None:
            return coll
    return None


def auto_text(scene, view_layer, role):
    """What the role is when nothing is picked (the Linking panel's hint)."""
    found = [c.name for c, _lc in collections(scene, view_layer, role)] if picked(scene, role) \
        is None else []
    return ", ".join(found) if found else "none found"


def shown(scene, view_layer, role):
    """True when a collection of `role` is in the view layer and not hidden (the eye)."""
    return any(not lc.exclude and not lc.hide_viewport
               for _c, lc in collections(scene, view_layer, role))


def set_shown(scene, view_layer, role, show):
    """Show (also includes an excluded one) or hide (the eye) the role's collections.
    Cameras: the Outliners fold them too (their camera filter - Python can't fold one item)."""
    colls = collections(scene, view_layer, role)
    for _c, lc in colls:
        if show:
            lc.exclude = False
        lc.hide_viewport = not show
    if role == 'CAMERAS':
        for screen in bpy.data.screens:
            for area in screen.areas:
                for space in area.spaces:
                    if space.type == 'OUTLINER':
                        space.use_filter_object_camera = show
    return colls


CREATABLE = ('ORIGINALS', 'EXPORT')     # the add-on makes these itself (Remesh, 07 Add)


def _in_tree(coll, root):
    return coll == root or any(_in_tree(coll, c) for c in root.children)


def ensure(scene, role):
    """The role's collection, made the way the add-on makes it when there is none: found
    (picked / usual name) or new with the usual name, linked into the scene when it is not
    in it, and picked for the role in the Linking panel. EXPORT gets its colour tag like 07's
    Add."""
    coll = find(scene, role)
    if coll is None:
        coll = bpy.data.collections.new(ROLES[role][3][0])
    if not _in_tree(coll, scene.collection):
        scene.collection.children.link(coll)
    if role == 'EXPORT':
        coll.color_tag = 'COLOR_03'
    props = getattr(scene, "multicamproject_props", None)
    if props is not None:
        setattr(props, ROLES[role][0], coll)
    return coll


def _found(scene, view_layer, role):
    """The collection to fill an empty picker with: the one with the usual name in this
    scene, else (cameras) the only collection holding just cameras."""
    names = {n.lower() for n in ROLES[role][3]}
    layers = _layers(view_layer)
    named = [lc.collection for lc in layers if lc.collection.name.lower() in names]
    if named:
        return named[0]
    if role == 'CAMERAS':
        cams = [c for c, _lc in collections(scene, view_layer, role)]
        if len(cams) == 1:
            return cams[0]
    return None


def autofill(scene):
    """Empty pickers get the collection found for them (file load / save / install).
    Returns the roles filled."""
    props = getattr(scene, "multicamproject_props", None)
    if props is None or not scene.view_layers:
        return []
    filled = []
    for role in ORDER:
        attr = ROLES[role][0]
        if getattr(props, attr) is not None:
            continue
        coll = _found(scene, scene.view_layers[0], role)
        if coll is not None:
            setattr(props, attr, coll)
            filled.append(role)
    return filled
