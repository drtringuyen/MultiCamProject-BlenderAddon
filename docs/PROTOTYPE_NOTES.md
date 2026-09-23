# MultiCamProject - prototype notes (handoff for the code session)

Status: scaffold only (`addons/MultiCamProject/`). The feature was prototyped live in Blender 5.2
and approved; `docs/prototype_gn_builder.py` is the verified builder to turn into a module.

## Next step
Add a `camera_project` module with `blender-addon-modules`:
- UI: Camera 1/2/3 pickers, Mode (Sharp/Smooth/Combined), Original Blend, Occlusion, button "Setup Camera Projection"
- Operator: build node groups if missing -> add `GN-CameraProject` modifier to active mesh -> set inputs
  -> lens drivers -> create `MAT_<objectname>`. Re-run should rebuild drivers when cameras change.

## Node design
- `GN-CamProject_Single` (reusable, one camera): Object Info (Relative) -> invert -> transform Position ->
  UV = (x/-z * focal/sensor + .5, y/-z * focal/sensor * aspect + .5) as a corner field.
  Weight (face domain) = max(0, dot(Normal, dir to cam)) * in-frame(0<u,v<1, depth>0) * (1 - hit * Occlusion).
  Occlusion = Raycast to self from face centre (+0.001 * normal) toward camera.
- `GN-CameraProject` (modifier): 3 x Single -> Store `UV_cam1/2/3` (FLOAT2, CORNER, becomes real UV maps)
  -> VCMix via Menu Switch:
  - Sharp: argmax per face, stored on CORNER
  - Smooth: weights normalised to sum 1, stored on POINT
  - Combined: argmax stored on POINT
  -> mix with existing `VCMix` by `Original Blend * Exists` -> Set Material.
  Lens inputs (Focal/Sensor per cam, Aspect) sit in a collapsed panel, driven from camera data.
- `MAT_<obj>`: 3 Image Textures (camera background_images[0].image, UV_camN, extension CLIP)
  weighted by VCMix RGB, normalised by R+G+B -> Principled Base Color.

## Blender 5.2 API gotchas
- Modifier inputs: `mod.properties.inputs.Socket_N.value` (old `mod["Socket_N"]` is ignored silently).
- Drivers: `obj.driver_add('modifiers["GN-CameraProject"].properties.inputs.Socket_N.value')`.
- Menu socket values are item names ("Sharp"...). Menu Switch items: `node.enum_items.new(name)`.
- GN cannot read camera lens -> pass as inputs + drivers.

## Known limits
- Faces facing no camera stay black (no fallback colour yet).
- Low-poly faces interpolate UVs linearly (slight swimming on big faces); subdivide if needed.
- Source photos after JOHM3428 are distorted originals (undistorted export incomplete) -> misalign.
