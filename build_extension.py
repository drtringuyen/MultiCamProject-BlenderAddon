import shutil
import os
import zipfile

ADDON_NAME = "MultiCamProject"
PROJECT_ROOT = os.path.dirname(__file__)
SRC_FOLDER = os.path.join(PROJECT_ROOT, "addons", ADDON_NAME)
EXT_ZIP = os.path.join(PROJECT_ROOT, f"{ADDON_NAME}-extension.zip")

# Step 1 — Remove old extension zip if exists
if os.path.exists(EXT_ZIP):
    os.remove(EXT_ZIP)
    print("[*] Removed old extension zip")

# Step 2 — Create extension-format zip (includes manifest.toml)
with zipfile.ZipFile(EXT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
    # Add manifest.toml (required for extension format)
    manifest_path = os.path.join(PROJECT_ROOT, "manifest.toml")
    if os.path.exists(manifest_path):
        zf.write(manifest_path, "manifest.toml")
        print("  + manifest.toml")
    else:
        print("[!] Warning: manifest.toml not found!")

    # Add addon files
    for root, dirs, files in os.walk(SRC_FOLDER):
        # Skip cache and virtual env folders
        dirs[:] = [d for d in dirs if d not in ['__pycache__', '.venv', 'venv']]

        for file in files:
            # Skip compiled Python files
            if file.endswith(('.pyc', '.pyo', '.pyd')):
                continue

            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, SRC_FOLDER)
            arcname = os.path.join(ADDON_NAME, rel_path)

            zf.write(full_path, arcname)
            print(f"  + {arcname}")

print(f"\n[SUCCESS] Created: {EXT_ZIP}")
print(f"\n[INFO] Ready for Blender Extension Platform:")
print("   https://extensions.blender.org/")
