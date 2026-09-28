---
name: blender-testing-gotchas
description: "Lessons from testing this add-on in Blender 5.2 - keymaps, UI checks, computer-use, test cleanup"
metadata:
  node_type: memory
  type: feedback
  originSessionId: a05a1d99-0881-4fdf-8765-7deef78d9d79
  modified: 2026-09-28T08:19:47.246Z
---

- **Keymaps:** in 5.x the 3D view keymap is named "3D View Generic" (it was "View3D Generic"). An unknown keymap name binds nothing, with no error. The "Frames" keymap takes Up/Down (Jump to Keyframe) before any 3D view keymap, so arrow-key bindings go in "Frames", with a poll that lets the key fall through elsewhere.
- **Test shortcuts with real keys:** calling an operator directly skips key handling. When the user reported the arrows didn't work, only a real keypress through computer-use found the cause.
- **computer-use:** the Blender window belongs to `blender.exe`, so request access to that. Never use `open_application("Blender 5.2")`, which starts a new empty instance. To restore a minimized window, click its taskbar button; that needs the "File Explorer" grant, which is click-only. After that, click Blender's title bar so key presses go to Blender.
- **UI checks:** use `get_screenshot_of_area_as_image(VIEW_3D)` for the sidebar. To preview a state such as a solo without changing the user's view, temporarily monkeypatch the ui module function, redraw, take the screenshot, then restore.
- **Scrolling:** add-ons can't find where a panel row is, so a sidebar can't be scrolled to it. A UIList scrolls to its active row, but only when a redraw sees that row change. To re-scroll to the same row: write -1 (a raw ID write), let one redraw happen, then set the row back on a timer. The All Cameras list uses this.
- **Local view:** including a collection again (exclude/hide set back to False) while in local view adds all of that collection's objects to the local view. Remove them again with `local_view_set(space, False)`.
- **Never read `image.size` / pixels in inspection queries.** It decodes the image. With hundreds of camera photos, a query on 2026-09-25 took Blender to 77 GB of RAM and left it hung, and the user had to restart. Read only `has_data`, `filepath`, `source`, `packed_file`. Start with a trivial query first to confirm Blender responds.
- **Test cleanup:** test on copies (`obj.copy()` + `data.copy()`), always inside try/finally. A crash before the `try` leaves leftovers behind, so list and remove `_`-prefixed objects and test materials afterwards.
- **GN + scans (5.2):** GN's Named Attribute cannot read `.sculpt_face_set` (it returns 0), so copy it in Python. There is no Decimate node: use a Decimate modifier driven by a vertex group that GN writes into (weight 1 = collapse, 0 = keep; the vertex group has to exist first). An Exact boolean against an open scan keeps pieces of the cutter's walls, so tag the cutter's faces and delete them afterwards. The Manifold solver refuses scans outright.

**Why:** Each of these cost a debugging round-trip with the user on 2026-09-24.

**How to apply:** Check this before adding shortcuts, UI checks or live tests in Blender. See [[gn-layout-is-user-owned]].
