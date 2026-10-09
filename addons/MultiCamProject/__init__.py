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
    from . import properties, infos, panels, folders, view_keys, quality
    properties.register()
    quality.register()
    infos.register()
    panels.register()
    folders.register()
    view_keys.register()

    from . import module_manager
    module_manager.load_all()


def unregister():
    from . import module_manager
    module_manager.unload_all()

    from . import properties, infos, panels, folders, view_keys, quality
    view_keys.unregister()
    folders.unregister()
    panels.unregister()
    infos.unregister()
    quality.unregister()
    properties.unregister()
