bl_info = {
    "name": "MultiCamProject",
    "version": (0, 0, 1),
    "blender": (4, 0, 0),
    "category": "Material",
    "description": "Project camera background photos onto meshes with blended UVs and vertex-colour weights",
    "author": "",
    "doc_url": "",
    "tracker_url": "",
}

# Module registry - easily enable/disable modules
MODULES = {
    "example": True,      # Enable/disable modules here
}

def register():
    from . import properties, infos, panels
    properties.register()
    infos.register()
    panels.register()

    # Load enabled modules
    if MODULES.get("example", True):
        from . import module_example_operators
        module_example_operators.register()

def unregister():
    # Unload modules
    if MODULES.get("example", True):
        from . import module_example_operators
        module_example_operators.unregister()

    from . import properties, infos, panels
    panels.unregister()
    infos.unregister()
    properties.unregister()
