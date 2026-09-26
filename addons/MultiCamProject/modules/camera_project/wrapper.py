"""Per-object wrapper groups: no ID is ever stored on a modifier.

Blender 5.2 bug: every save (Ctrl+S, autosave, save copy) adds a user to each ID held by
a Geometry Nodes modifier input (Material, Object, Image, ...) and never takes it back -
MCP_<name> read 386 users after a day of work. IDs set as node default values inside a
node tree do not leak.

So a modifier runs "<shared group> | <object>": Group Input -> the shared group (one
node) -> Group Output. The wrapper exposes the shared group's plain inputs under the same
names; its ID inputs are not exposed but kept as that node's default values. get/set in
core route by socket name, so callers do not care which kind an input is.
"""
import bpy

ID_SOCKETS = {"NodeSocketMaterial", "NodeSocketObject", "NodeSocketImage",
              "NodeSocketCollection", "NodeSocketTexture"}
WRAP_KEY = "multicamproject_wraps"      # wrapper[WRAP_KEY] = signature of the shared group
NODE = "Shared"
SEP = " | "
# copied from the shared group's interface sockets (order matters: dimensions first)
_SOCKET_ATTRS = ("description", "subtype", "dimensions", "structure_type", "hide_value",
                 "hide_in_modifier", "force_non_field", "min_value", "max_value",
                 "default_value", "default_attribute_name")


def is_wrapper(ng):
    return ng is not None and WRAP_KEY in ng and ng.nodes.get(NODE) is not None


def shared(ng):
    """The shared group behind `ng` (itself when it is not a wrapper)."""
    if is_wrapper(ng):
        return ng.nodes[NODE].node_tree
    return ng


def _signature(sg):
    """Changes whenever the shared group is rebuilt (its sockets get new identifiers,
    which drops the wrapper's links to them)."""
    return "|".join(f"{getattr(it, 'in_out', 'PANEL')}:{it.name}:{getattr(it, 'socket_type', '')}"
                    f":{getattr(it, 'identifier', '')}" for it in sg.interface.items_tree)


def _is_id(item):
    return getattr(item, "in_out", None) == 'INPUT' and item.socket_type in ID_SOCKETS


def id_socket(mod, name):
    """The wrapper node's input holding the ID input `name`, or None (a plain input)."""
    ng = mod.node_group
    if not is_wrapper(ng):
        return None
    return next((s for s in ng.nodes[NODE].inputs
                 if s.name == name and s.bl_idname in ID_SOCKETS), None)


# ---------------------------------------------------------------- values

def _plain(v):
    return tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v


def read_values(mod):
    """Every input of the modifier by name - plain ones and the wrapper's IDs."""
    vals = {}
    ng = mod.node_group
    if ng is None:
        return vals
    for it in ng.interface.items_tree:
        if getattr(it, "in_out", None) == 'INPUT' and it.socket_type != 'NodeSocketGeometry':
            try:
                vals[it.name] = _plain(getattr(mod.properties.inputs, it.identifier).value)
            except (AttributeError, TypeError):
                pass
    if is_wrapper(ng):
        for s in ng.nodes[NODE].inputs:
            if s.bl_idname in ID_SOCKETS:
                vals[s.name] = s.default_value
    return vals


def write_values(mod, vals):
    """Put values from read_values back (inputs that no longer exist are skipped)."""
    ng = mod.node_group
    idents = {it.name: it.identifier for it in ng.interface.items_tree
              if getattr(it, "in_out", None) == 'INPUT'}
    for name, v in vals.items():
        s = id_socket(mod, name)
        try:
            if s is not None:
                s.default_value = v
            elif name in idents:
                h = getattr(mod.properties.inputs, idents[name])
                if _plain(h.value) != v:
                    h.value = v
        except (AttributeError, TypeError, ValueError):
            pass


# ---------------------------------------------------------------- building

def _mirror_interface(ng, sg):
    panels = {}
    for it in sg.interface.items_tree:
        parent = panels.get(it.parent.as_pointer()) if it.parent is not None else None
        if it.item_type == 'PANEL':
            p = ng.interface.new_panel(it.name, description=it.description,
                                       default_closed=it.default_closed)
            if parent is not None:
                ng.interface.move_to_parent(p, parent, len(parent.interface_items))
            panels[it.as_pointer()] = p
            continue
        if _is_id(it):
            continue
        kw = {"name": it.name, "in_out": it.in_out, "socket_type": it.socket_type}
        if parent is not None:
            kw["parent"] = parent
        s = ng.interface.new_socket(**kw)
        for attr in _SOCKET_ATTRS:
            if hasattr(it, attr):
                try:
                    setattr(s, attr, _plain(getattr(it, attr)))
                except (AttributeError, TypeError, ValueError):
                    pass


def _find(sockets, name):
    return next((s for s in sockets if s.name == name and s.enabled), None)


def _build(ng, sg):
    """(Re)build `ng` around the shared group `sg`."""
    ng.nodes.clear()
    ng.interface.clear()
    ng.is_modifier = True
    ng.description = f"{sg.name} for one object (ID inputs kept on the node - see wrapper.py)"
    _mirror_interface(ng, sg)
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-300, 0)
    node = ng.nodes.new("GeometryNodeGroup")
    node.name = NODE
    node.node_tree = sg
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (300, 0)
    for s in node.inputs:
        src = None if s.bl_idname in ID_SOCKETS else _find(gi.outputs, s.name)
        if src is not None:
            ng.links.new(src, s)
    for s in node.outputs:
        dst = _find(go.inputs, s.name)
        if dst is not None:
            ng.links.new(s, dst)
    ng[WRAP_KEY] = _signature(sg)


def _wrapper_name(sg, obj):
    return f"{sg.name}{SEP}{obj.name}"


def _spare(name):
    """An unused wrapper left behind (e.g. by applying the modifier) - reused by name."""
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.users == 0 and WRAP_KEY in ng and not ng.library:
        return ng
    return None


def ensure(obj, mod, sg):
    """Make `mod` run its own wrapper around the shared group `sg`, with every input value
    (plain and ID) kept. Returns True when the modifier's inputs were re-created (callers
    re-add drivers)."""
    ng = mod.node_group
    if is_wrapper(ng) and ng.users <= 1 and shared(ng) is sg and ng.get(WRAP_KEY) == _signature(sg):
        return False
    vals = read_values(mod) if ng is not None else {}
    if is_wrapper(ng) and ng.users <= 1:
        _build(ng, sg)          # other shared group, or the shared group was rebuilt
    else:
        # the shared group itself (setup before wrappers), none yet, or a wrapper shared
        # with a duplicated object: this modifier gets its own
        name = _wrapper_name(sg, obj)
        new = _spare(name) or bpy.data.node_groups.new(name, "GeometryNodeTree")
        _build(new, sg)
        mod.node_group = new
    bpy.context.view_layer.update()     # menu inputs have no items until an update
    write_values(mod, vals)
    return True
