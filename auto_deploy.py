#!/usr/bin/env python3
"""
Auto deploy: watch GitHub and reload the add-on in Blender whenever main gets new commits
(e.g. pushed by a Claude cloud session). Start it once with auto_deploy.bat and leave the
window open; Ctrl+C or closing the window stops it.

Safe by design - it only ever fast-forwards main:
  - skipped while you are on another branch or have uncommitted changes
  - skipped when your main has commits GitHub doesn't (push them yourself first)
  - never merges, pushes or overwrites anything
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
BRANCH = "main"
INTERVAL = int(os.environ.get("AUTO_DEPLOY_INTERVAL", 30))     # seconds between checks


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr).strip() or f"git {' '.join(args)} failed")
    return r.stdout.strip()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def check():
    """One round. Returns why it skipped ('' when it did not); main prints a reason only
    when it changes."""
    if git("rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        return f"not on {BRANCH} - waiting"
    git("fetch", "--quiet", "origin", BRANCH)
    behind = int(git("rev-list", "--count", f"HEAD..origin/{BRANCH}"))
    if not behind:
        return ""
    if int(git("rev-list", "--count", f"origin/{BRANCH}..HEAD")):
        return f"your {BRANCH} and GitHub's both have new commits - pull/push in Fork, then I continue"
    if git("status", "--porcelain", "--untracked-files=no"):
        return "uncommitted changes - commit or stash them, then I continue"
    log(f"{behind} new commit(s) on GitHub:")
    print("    " + git("log", "--oneline", f"HEAD..origin/{BRANCH}").replace("\n", "\n    "))
    git("merge", "--ff-only", "--quiet", f"origin/{BRANCH}")
    log("pulled - installing into Blender")
    subprocess.run([sys.executable, str(ROOT / "install.py")], cwd=ROOT)
    return ""


def main():
    log(f"Watching GitHub '{BRANCH}' every {INTERVAL} s. Leave this window open (Ctrl+C stops).")
    last = None
    while True:
        try:
            skip = check()
        except RuntimeError as e:
            skip = f"check failed: {str(e).splitlines()[0]}"
        except FileNotFoundError:
            log("git was not found - install Git for Windows")
            return
        if skip and skip != last:
            log(skip)
        last = skip
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
