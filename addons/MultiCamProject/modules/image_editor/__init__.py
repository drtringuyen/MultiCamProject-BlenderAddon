"""Image Editor: Liquify a camera photo, previewed live on the camera background and the mesh.
Its panel and tool live in the Image Editor only. See docs/PLAN_image_editor_liquify.md."""
from . import operators, props, session, tool, ui


def register():
    props.register()
    session.register()
    operators.register()
    tool.register()
    ui.register()


def unregister():
    ui.unregister()
    tool.unregister()
    operators.unregister()
    session.unregister()
    props.unregister()
