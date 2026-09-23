#!/usr/bin/env python3
"""
Install MultiCamProject addon to Blender.
Run from PyCharm terminal or command line after editing source files.
"""

import os
import shutil
import sys
import json
import socket
from pathlib import Path
from datetime import datetime

ADDON_NAME = "MultiCamProject"
BLENDER_VERSION = "5.1"
MCP_HOST = "localhost"
MCP_PORT = 9876

SCRIPT_DIR = Path(__file__).parent
ADDON_SRC = SCRIPT_DIR / "addons" / ADDON_NAME
BLENDER_ADDONS = (
    Path.home() / "AppData" / "Roaming" / "Blender Foundation"
    / "Blender" / BLENDER_VERSION / "scripts" / "addons"
)

print(f"[*] Installing {ADDON_NAME}...")
print(f"    Source:      {ADDON_SRC}")
print(f"    Destination: {BLENDER_ADDONS}")

if not ADDON_SRC.exists():
    print(f"[ERROR] Addon source not found: {ADDON_SRC}")
    sys.exit(1)

BLENDER_ADDONS.mkdir(parents=True, exist_ok=True)

addon_dest = BLENDER_ADDONS / ADDON_NAME
if addon_dest.exists():
    print(f"[*] Removing old addon...")
    shutil.rmtree(addon_dest)

try:
    shutil.copytree(ADDON_SRC, addon_dest)

    # Write build timestamp — the Build button in Blender reads this
    build_info = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.now().strftime("%d/%m/%Y"),
    }
    with open(addon_dest / "build_info.json", "w") as f:
        json.dump(build_info, f)

    print(f"[SUCCESS] Installed at {datetime.now().strftime('%H:%M:%S')}")
except Exception as e:
    print(f"[ERROR] Installation failed: {e}")
    sys.exit(1)

# Reload addon in Blender via MCP socket (requires Blender MCP addon on port 9876)
def reload_via_mcp(addon_name: str) -> None:
    code = f"""
import sys, bpy
addon = "{addon_name}"
bpy.ops.preferences.addon_disable(module=addon)
mods = [k for k in sys.modules if k == addon or k.startswith(addon + ".")]
for m in mods:
    del sys.modules[m]
bpy.ops.preferences.addon_enable(module=addon)
result = {{"status": "reloaded"}}
"""
    request = json.dumps({"type": "execute", "code": code, "strict_json": False}) + "\0"
    with socket.socket() as sock:
        sock.settimeout(10.0)
        sock.connect((MCP_HOST, MCP_PORT))
        sock.sendall(request.encode("utf-8"))
        buf = bytearray()
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\0" in buf:
                break
    response = json.loads(buf.split(b"\0")[0].decode())
    if response.get("status") == "error":
        raise RuntimeError(response.get("message", "Blender MCP error"))

print(f"[*] Reloading {ADDON_NAME} in Blender via MCP...")
try:
    reload_via_mcp(ADDON_NAME)
    print(f"[OK] {ADDON_NAME} reloaded in Blender")
except ConnectionRefusedError:
    print(f"[WARN] Blender not running or MCP not active (port {MCP_PORT}) -- skipped reload")
except Exception as e:
    print(f"[WARN] MCP reload failed: {e}")
