"""Image Editor: Liquify a camera photo, previewed live on the camera background and the mesh.
Painted in the Image Editor (panel + tool) or through a soloed camera in the 3D Viewport (the
Liquify button of a camera row, camera_op.py). See docs/PLAN_image_editor_liquify.md.
Lasso: cut / move / rotate / scale pixels of the shown image (Ctrl+Alt+K), ported from DomeAnimatic
(lasso_ops.py, lasso_draw.py, lasso_raster.py).
Inpaint: fill spots (scan markers) of a camera photo, BAo_ or ALB_ on a copy, saved as
<name>_inpaint (inpaint.py, sources.py, inpaint_fill.py)."""
from . import (camera_op, inpaint, inpaint_fill, lasso_draw, lasso_ops, lasso_raster, operators,
               props, session, sources, tool, ui)


def register():
    props.register()
    session.register()
    operators.register()
    tool.register()
    camera_op.register()
    lasso_ops.register()
    inpaint.register()
    ui.register()


def unregister():
    ui.unregister()
    inpaint.unregister()
    lasso_ops.unregister()
    camera_op.unregister()
    tool.unregister()
    operators.unregister()
    session.unregister()
    props.unregister()
