"""MCPTexturePostprocessor.cs in a Unity project: one copy, never committed.

All Editor scripts outside an asmdef compile into one assembly, so a second copy of the
class stops the whole project compiling (CS0101). Each export folder used to get its own
copy, and two reached the team's repo (2026-10-20). Inside a Unity project the script now
lives at Assets/Editor/; other copies go to the Recycle Bin and git ignores the script on
this machine only. The texture settings it makes are in the .meta files, so nobody else
needs it. No bpy here: tests/test_unity_script.py runs this file on its own.
"""
import os

SCRIPT = "MCPTexturePostprocessor.cs"
EXCLUDE = ("**/" + SCRIPT, "**/" + SCRIPT + ".meta")


def _ancestors(path):
    path = os.path.abspath(path)
    while True:
        yield path
        parent = os.path.dirname(path)
        if parent == path:
            return
        path = parent


def assets_root(folder):
    """The Assets folder of the Unity project holding `folder`, or None."""
    for p in _ancestors(folder):
        if os.path.basename(p).lower() == "assets" and \
                os.path.isdir(os.path.join(os.path.dirname(p), "ProjectSettings")):
            return p
    return None


def find_copies(assets):
    return [os.path.join(root, f) for root, _d, names in os.walk(assets)
            for f in names if f.lower() == SCRIPT.lower()]


def _write_if_changed(path, text):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _exclude_from_git(project):
    """Local to this machine: .git/info/exclude is not part of the repo."""
    for p in _ancestors(project):
        git = os.path.join(p, ".git")
        if os.path.exists(git):
            if not os.path.isdir(git):      # a worktree: .git is a file
                return
            path = os.path.join(git, "info", "exclude")
            old = ""
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    old = f.read()
            have = old.splitlines()
            new = [line for line in EXCLUDE if line not in have]
            if new:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    if old and not old.endswith("\n"):
                        f.write("\n")
                    f.write("\n".join(new) + "\n")
            return


def _remove_copy(path, recycle):
    """The copy, its .meta, and its Editor folder if that leaves it empty."""
    recycle([path, path + ".meta"])
    if os.path.exists(path):
        return False
    folder = os.path.dirname(path)
    if os.path.basename(folder).lower() == "editor" and not os.listdir(folder):
        os.rmdir(folder)
        recycle([folder + ".meta"])
    return True


def install(folder, text, recycle):
    """Write the script for an export to `folder`. `recycle(paths)` removes files.
    Returns (path, [(severity, text)]) for the export report."""
    assets = assets_root(folder)
    if assets is None:      # not in a Unity project: it travels with the export folder
        dst = os.path.join(folder, "Editor", SCRIPT)
        _write_if_changed(dst, text)
        return dst, []
    dst = os.path.join(assets, "Editor", SCRIPT)
    _write_if_changed(dst, text)
    log = []
    for p in find_copies(assets):
        if os.path.normcase(p) == os.path.normcase(dst):
            continue
        rel = os.path.relpath(p, assets)
        if _remove_copy(p, recycle):
            log.append(('INFO', f"Unity script: extra copy {rel} moved to the Recycle Bin"))
        else:
            log.append(('ERROR', f"Unity script: extra copy {rel} could not be removed - "
                                 "Unity will not compile until it is gone"))
    try:
        _exclude_from_git(os.path.dirname(assets))
    except OSError as e:
        log.append(('WARNING', f"Unity script: not added to .git/info/exclude ({e}) - "
                               "do not commit it"))
    return dst, log
