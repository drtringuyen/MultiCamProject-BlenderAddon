"""Image Editor: Liquify a camera photo, previewed live on the camera background and the mesh.
Painted in the Image Editor (panel + tool) or through a soloed camera in the 3D Viewport (the
Liquify button of a camera row, camera_op.py). See docs/PLAN_image_editor_liquify.md."""
from . import camera_op, operators, props, session, tool, ui


def register():
    props.register()
    session.register()
    operators.register()
    tool.register()
    camera_op.register()
    ui.register()


def unregister():
    ui.unregister()
    camera_op.unregister()
    tool.unregister()
    operators.unregister()
    session.unregister()
    props.unregister()
