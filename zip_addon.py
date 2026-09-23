import shutil
import os
import zipfile

ADDON_NAME = "MultiCamProject"
PROJECT_ROOT = os.path.dirname(__file__)
SRC_FOLDER = os.path.join(PROJECT_ROOT, "addons", ADDON_NAME)
OUTPUT_ZIP = os.path.join(PROJECT_ROOT, f"{ADDON_NAME}.zip")

# Step 1 — Remove old zip if exists
if os.path.exists(OUTPUT_ZIP):
    os.remove(OUTPUT_ZIP)
    print("[*] Removed old zip")

# Step 2 — Zip addon folder (include all files except cache/venv)
with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
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

print(f"\n[SUCCESS] Created: {OUTPUT_ZIP}")
print(f"\n[INFO] Ready for manual installation:")
print("   Blender -> Edit -> Preferences -> Add-ons -> Install -> pick MultiCamProject.zip")
