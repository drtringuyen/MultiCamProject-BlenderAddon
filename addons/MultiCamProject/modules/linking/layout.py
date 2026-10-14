"""The work window's screen: two big editors - the UV Editor on the left, the 3D View on the
right (sidebar on the add-on's tab, statistics / wireframe / face orientation on). Made by
build.py when the window opens, and once when a saved work file opens that has not had it
(a headless Automate build has no window to lay out). LAYOUT_KEY on the scene marks it."""
import bpy

LAYOUT_KEY = "multicamproject_work_layout"   # work window scene: the layout was made
ADDON_TAB = "MultiCamProject"
WIREFRAME_OPACITY = 0.262
_BARS = {'TOPBAR', 'STATUSBAR'}
UV_SHARE = 0.25         # the UV Editor's part of the width: a third of the 3D View's


def _override(win, area):
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    return bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region)


def two_editors(win):
    """The window's screen down to its biggest 3D View, split: the UV Editor left (UV_SHARE
    of the width), the 3D View right. Returns the 3D View area."""
    screen = win.screen
    areas = [a for a in screen.areas if a.type not in _BARS]
    if not areas:
        return None
    keep = max(areas, key=lambda a: (a.type == 'VIEW_3D', a.width * a.height))
    if keep.type != 'VIEW_3D':
        keep.type = 'VIEW_3D'
    for _try in range(20):              # area_close merges each into a neighbour
        others = [a for a in screen.areas if a.type not in _BARS and a != keep]
        if not others:
            break
        with _override(win, others[0]):
            bpy.ops.screen.area_close()
    with _override(win, keep):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=UV_SHARE)
    halves = sorted((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)
    if len(halves) < 2:
        return halves[0] if halves else None
    left, right = halves[0], halves[-1]
    left.type = 'IMAGE_EDITOR'
    left.ui_type = 'UV'
    return right


def _views(screen):
    """Overlays of every 3D View, as the work window shows them."""
    for area in screen.areas:
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                ov = space.overlay
                ov.show_stats = True
                ov.show_wireframes = True
                ov.wireframe_threshold = 1.0
                ov.wireframe_opacity = WIREFRAME_OPACITY
                ov.show_face_orientation = True


WORKSPACE = "Layout"


def apply(scene):
    """The two-editor layout in every window (on the Layout workspace), the sidebar shown
    and the overlays on. Marks the scene (LAYOUT_KEY). Needs a window (not in background
    mode). A window on another workspace switches first; the layout follows on the next
    tick (Blender swaps the screen in between)."""
    wm = bpy.context.window_manager
    ws = bpy.data.workspaces.get(WORKSPACE)
    switched = False
    for win in wm.windows:
        if ws is not None and win.workspace != ws:
            win.workspace = ws
            switched = True
    scene[LAYOUT_KEY] = True
    if switched:
        bpy.app.timers.register(lambda: _apply(scene.name) or None, first_interval=0.2)
    else:
        _apply(scene.name)


def _apply(scene_name):
    wm = bpy.context.window_manager
    for win in wm.windows:
        try:
            view = two_editors(win)
        except Exception as e:      # never stop the window over its layout
            print(f"[MultiCamProject] work window: layout skipped: {e}")
            view = None
        _views(win.screen)
        for area in [view] if view is not None else [a for a in win.screen.areas
                                                     if a.type == 'VIEW_3D']:
            # show_region_ui's update needs the window in the context (5.2 crashes without)
            with _override(win, area):
                area.spaces.active.show_region_ui = True
            if area.type == 'VIEW_3D':
                with _override(win, area):
                    try:
                        bpy.ops.view3d.view_selected()
                    except RuntimeError:
                        pass
    _tab_tries[0] = 0
    bpy.app.timers.register(_addon_tab, first_interval=0.3)


_tab_tries = [0]


def _addon_tab():
    """The sidebar on the add-on's tab: a region knows its tabs only once drawn, so this
    retries a few times."""
    _tab_tries[0] += 1
    done = True
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type == 'UI':
                    try:
                        region.active_panel_category = ADDON_TAB
                    except (TypeError, AttributeError):
                        pass
                    done = done and region.active_panel_category == ADDON_TAB
                    region.tag_redraw()
    return None if done or _tab_tries[0] >= 20 else 0.25


def apply_once(scene):
    """A work file opens: the layout, unless it has it already."""
    if bpy.app.background or scene.get(LAYOUT_KEY):
        return False
    apply(scene)
    return True
