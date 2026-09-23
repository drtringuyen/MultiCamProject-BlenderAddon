bl_info = {
    "name": "MultiCamProject",
    "version": (0, 0, 1),
    "blender": (5, 2, 0),
    "category": "Material",
    "description": "Project camera background photos onto meshes with blended UVs and vertex-colour weights",
    "author": "",
    "doc_url": "",
    "tracker_url": "",
}


def register():
    from . import properties, infos, panels
    properties.register()
    infos.register()
    panels.register()

    from . import module_manager
    module_manager.load_all()


def unregister():
    from . import module_manager
    module_manager.unload_all()

    from . import properties, infos, panels
    panels.unregister()
    infos.unregister()
    properties.unregister()
