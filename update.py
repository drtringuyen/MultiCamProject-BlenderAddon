#!/usr/bin/env python3
"""
One click: latest code from GitHub -> Blender.

  1. pull main
  2. offer to merge Claude's cloud branches (origin/claude/*) that main doesn't have yet
  3. push main if it now has commits GitHub doesn't
  4. install.py (copies the add-on and reloads it in Blender via MCP)

Double-click it in Explorer, or run `python update.py`. Nothing is forced: on a conflict
the merge is undone and the script stops without changing anything.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
BRANCH = "main"


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stdout}{r.stderr}".rstrip())
    return r.stdout.strip()


def ask(question):
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def main():
    current = git("rev-parse", "--abbrev-ref", "HEAD")
    if current != BRANCH:
        raise RuntimeError(f"You are on '{current}', not '{BRANCH}'. Switch to {BRANCH} in Fork first.")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("You have uncommitted changes. Commit or stash them in Fork first.")

    print(f"[1/4] Pulling {BRANCH}...")
    git("fetch", "origin", "--prune")
    git("pull", "--no-edit", "origin", BRANCH)
    print("      up to date")

    print("[2/4] Checking Claude's cloud branches...")
    pending = git("branch", "-r", "--no-merged", "HEAD", "--list", "origin/claude/*").split()
    if not pending:
        print("      nothing new")
    for br in pending:
        print(f"\n      {br} has:")
        print("        " + git("log", "--oneline", f"HEAD..{br}").replace("\n", "\n        "))
        if not ask(f"      Merge {br} into {BRANCH}?"):
            continue
        r = subprocess.run(["git", "merge", "--no-edit", br], cwd=ROOT, text=True,
                           capture_output=True)
        if r.returncode != 0:
            git("merge", "--abort", check=False)
            raise RuntimeError(f"{br} conflicts with {BRANCH} - nothing was changed. "
                               f"Ask Claude to merge it.\n{r.stdout}{r.stderr}".rstrip())
        print("      merged")

    print(f"[3/4] Pushing {BRANCH}...")
    if git("rev-list", "--count", f"origin/{BRANCH}..HEAD") != "0":
        git("push", "origin", BRANCH)
        print("      pushed")
    else:
        print("      nothing to push")

    print("[4/4] Installing into Blender...")
    subprocess.run([sys.executable, str(ROOT / "install.py")], cwd=ROOT)


if __name__ == "__main__":
    try:
        main()
        print("\nDone.")
    except RuntimeError as e:
        print(f"\n[STOPPED] {e}")
    except FileNotFoundError:
        print("\n[STOPPED] git was not found. Install Git for Windows (it comes with Fork's setup).")
    input("\nPress Enter to close...")
