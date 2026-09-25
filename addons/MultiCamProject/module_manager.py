import json
import os
import importlib

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "modules_config.json")
_LOADED = {}

# Edit entries here via blender-addon-modules — panels.py reads this list
ALL_MODULES = [    {"name": "camera_project", "op": "multicamproject.toggle_camera_project", "icon": "CAMERA_DATA"},
    {"name": "project_sides", "op": "multicamproject.toggle_project_sides", "icon": "VIEW_CAMERA"},
    {"name": "baking", "op": "multicamproject.toggle_baking", "icon": "RENDER_STILL"},
    {"name": "export", "op": "multicamproject.toggle_export", "icon": "EXPORT"},
]


def is_loaded(name):
    return _LOADED.get(name, False)


def _read_config():
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_config(config):
    try:
        with open(_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass


def _get_module(name):
    try:
        return importlib.import_module(f".modules.{name}", package=__package__)
    except ImportError:
        return None


def load_all():
    config = _read_config()
    for entry in ALL_MODULES:
        name = entry["name"]
        mod = _get_module(name)
        if mod and config.get(name, True):
            mod.register()
            _LOADED[name] = True
        else:
            _LOADED[name] = False


def unload_all():
    for entry in reversed(ALL_MODULES):
        name = entry["name"]
        if _LOADED.get(name, False):
            try:
                _get_module(name).unregister()
            except Exception:
                pass
        _LOADED[name] = False


def toggle(name):
    mod = _get_module(name)
    if not mod:
        return
    if _LOADED.get(name, False):
        try:
            mod.unregister()
        except Exception:
            pass
        _LOADED[name] = False
    else:
        mod.register()
        _LOADED[name] = True
    config = _read_config()
    config[name] = _LOADED[name]
    _write_config(config)
