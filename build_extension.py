"""Build the extension zip.

    python build_extension.py          Lite: high-pass + mesh-bake normal maps
    python build_extension.py --full   Full: + Mesh + Albedo and the AI normal map
                                       (bundles ./wheels/*.whl and the ONNX model)

Blender extensions need blender_manifest.toml and __init__.py at the same level (the zip
root). Both builds have the same extension ID. Check a build with:
    blender --command extension validate MultiCamProject-extension.zip
"""
import glob
import os
import sys
import zipfile

ADDON_NAME = "MultiCamProject"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_FOLDER = os.path.join(PROJECT_ROOT, "addons", ADDON_NAME)
MODELS = os.path.join("modules", "baking", "normal", "models")     # relative to SRC_FOLDER
WHEELS = os.path.join(PROJECT_ROOT, "wheels")
SKIP_DIRS = {"__pycache__", ".venv", "venv"}
SKIP_FILES = {"build_info.json"}
SKIP_EXT = (".pyc", ".pyo", ".pyd")

full = "--full" in sys.argv
ext_zip = os.path.join(PROJECT_ROOT, f"{ADDON_NAME}-extension{'-full' if full else ''}.zip")

manifest_path = os.path.join(PROJECT_ROOT, "manifest.toml")
if not os.path.exists(manifest_path):
    sys.exit("[!] manifest.toml not found")
with open(manifest_path, encoding="utf-8") as f:
    manifest = f.read()

wheels = []
if full:
    wheels = sorted(glob.glob(os.path.join(WHEELS, "*.whl")))
    if not wheels:
        sys.exit("[!] --full: no wheels in ./wheels/ - download onnxruntime (and numpy-compatible "
                 "deps) for win_amd64 / Blender's Python first:\n"
                 "    pip download onnxruntime --dest ./wheels --only-binary=:all: "
                 "--python-version 3.13 --platform win_amd64")
    if not os.path.isdir(os.path.join(SRC_FOLDER, MODELS)):
        sys.exit(f"[!] --full: no model folder {MODELS} (put color_to_normals.onnx there)")
    names = ", ".join(f'"./wheels/{os.path.basename(w)}"' for w in wheels)
    manifest += f'\nwheels = [{names}]\nplatforms = ["windows-x64"]\n'

if os.path.exists(ext_zip):
    os.remove(ext_zip)
    print("[*] Removed old zip")

with zipfile.ZipFile(ext_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("blender_manifest.toml", manifest)
    print("  + blender_manifest.toml")
    for root, dirs, files in os.walk(SRC_FOLDER):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_root = os.path.relpath(root, SRC_FOLDER)
        if not full and (rel_root == MODELS or rel_root.startswith(MODELS + os.sep)):
            continue        # Lite: no model
        for file in files:
            if file.endswith(SKIP_EXT) or file in SKIP_FILES:
                continue
            full_path = os.path.join(root, file)
            arcname = os.path.relpath(full_path, SRC_FOLDER).replace(os.sep, "/")
            zf.write(full_path, arcname)
            print(f"  + {arcname}")
    for w in wheels:
        zf.write(w, f"wheels/{os.path.basename(w)}")
        print(f"  + wheels/{os.path.basename(w)}")

print(f"\n[SUCCESS] Created: {ext_zip} ({'Full' if full else 'Lite'}, "
      f"{os.path.getsize(ext_zip) / 1e6:.1f} MB)")
