"""Tests for modules/export/unity_script.py - temporary folders only, no Blender.

Run:  py tests/test_unity_script.py      (or pytest, if installed)
"""
import importlib.util
import os
import subprocess
import tempfile

_PATH = os.path.join(os.path.dirname(__file__), "..", "addons", "MultiCamProject",
                     "modules", "export", "unity_script.py")
_spec = importlib.util.spec_from_file_location("unity_script", _PATH)
us = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(us)

TEXT = "public class MCPTexturePostprocessor {}\n"


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _remove(paths):
    for p in paths:
        if os.path.isfile(p):
            os.remove(p)


def _project(tmp, git=False):
    root = os.path.join(tmp, "Game")
    os.makedirs(os.path.join(root, "ProjectSettings"))
    os.makedirs(os.path.join(root, "Assets", "Art", "Room1"))
    if git:
        subprocess.run(["git", "init", "-q", root], check=True)
    return root


def test_outside_unity_stays_with_the_export():
    with tempfile.TemporaryDirectory() as tmp:
        folder = os.path.join(tmp, "Export")
        dst, log = us.install(folder, TEXT, _remove)
        assert dst == os.path.join(folder, "Editor", us.SCRIPT)
        assert os.path.isfile(dst) and log == []


def test_assets_without_project_settings_is_not_unity():
    with tempfile.TemporaryDirectory() as tmp:
        assert us.assets_root(os.path.join(tmp, "Assets", "Room")) is None


def test_two_rooms_share_one_copy():
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(tmp)
        assets = os.path.join(root, "Assets")
        for room in ("Room1", "Room2"):
            dst, log = us.install(os.path.join(assets, "Art", room), TEXT, _remove)
            assert dst == os.path.join(assets, "Editor", us.SCRIPT) and log == []
        assert us.find_copies(assets) == [dst]


def test_old_copies_are_removed_with_their_folder():
    with tempfile.TemporaryDirectory() as tmp:
        assets = os.path.join(_project(tmp), "Assets")
        old = os.path.join(assets, "Art", "Room1", "Editor")
        _touch(os.path.join(old, us.SCRIPT))
        _touch(os.path.join(old, us.SCRIPT + ".meta"))
        _touch(old + ".meta")
        dst, log = us.install(os.path.join(assets, "Art", "Room2"), TEXT, _remove)
        assert us.find_copies(assets) == [dst]
        assert not os.path.exists(old) and not os.path.exists(old + ".meta")
        assert [s for s, _t in log] == ['INFO']


def test_a_shared_editor_folder_keeps_its_other_scripts():
    with tempfile.TemporaryDirectory() as tmp:
        assets = os.path.join(_project(tmp), "Assets")
        old = os.path.join(assets, "Art", "Editor")
        _touch(os.path.join(old, us.SCRIPT))
        _touch(os.path.join(old, "Theirs.cs"))
        _touch(old + ".meta")
        us.install(os.path.join(assets, "Art", "Room1"), TEXT, _remove)
        assert os.path.isfile(os.path.join(old, "Theirs.cs"))
        assert os.path.isfile(old + ".meta")


def test_failed_removal_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        assets = os.path.join(_project(tmp), "Assets")
        old = os.path.join(assets, "Art", "Editor", us.SCRIPT)
        _touch(old)
        _dst, log = us.install(os.path.join(assets, "Art"), TEXT, lambda paths: None)
        assert [s for s, _t in log] == ['ERROR']


def test_changed_script_is_updated():
    with tempfile.TemporaryDirectory() as tmp:
        assets = os.path.join(_project(tmp), "Assets")
        dst, _log = us.install(assets, "old\n", _remove)
        us.install(assets, TEXT, _remove)
        with open(dst, encoding="utf-8") as f:
            assert f.read() == TEXT


def test_git_ignores_it_once_and_keeps_existing_lines():
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(tmp, git=True)
        exclude = os.path.join(root, ".git", "info", "exclude")
        _touch(exclude, "mine.txt")                 # no newline at the end
        folder = os.path.join(root, "Assets", "Art", "Room1")
        us.install(folder, TEXT, _remove)
        us.install(folder, TEXT, _remove)
        with open(exclude, encoding="utf-8") as f:
            lines = f.read().splitlines()
        assert lines == ["mine.txt", *us.EXCLUDE]
        _touch(os.path.join(root, "Assets", "Editor", us.SCRIPT + ".meta"))
        out = subprocess.run(["git", "-C", root, "status", "--short", "--untracked-files=all"],
                             capture_output=True, text=True, check=True).stdout
        assert us.SCRIPT not in out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
