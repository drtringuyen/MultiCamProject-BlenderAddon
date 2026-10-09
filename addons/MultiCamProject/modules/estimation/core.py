"""Area, triangle count and the area-split budget of the OBJECTS collection's meshes.
Send Out passes an object's budget to its work window (TARGET_KEY), which sets the Decimate
to it and shows it on Send Back."""
import numpy as np

from ... import roles

TARGET_KEY = "multicamproject_work_target"  # work window scene: the object's budget (tris)


def short(n):
    """150000 -> '150K', 2400 -> '2.4K', 830 -> '830'."""
    if n >= 1000000:
        return f"{n / 1000000:.2f}M"
    if n >= 10000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def objects_collection(context):
    """The OBJECTS collection (picked in the Linking panel, else by its name), or None."""
    return roles.find(context.scene, 'OBJECTS')


def measure(obj, depsgraph):
    """(world-space area in m², triangles) of the mesh as shown - modifiers and GN included.
    An object outside the view layer has no evaluated mesh: its own mesh is measured."""
    ev = obj.evaluated_get(depsgraph)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        ev, me = None, obj.data
    try:
        me.calc_loop_triangles()
        n = len(me.loop_triangles)
        if not n:
            return 0.0, 0
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = np.array(obj.matrix_world, dtype=np.float64)
        co = co @ m[:3, :3].T + m[:3, 3]
        idx = np.empty(n * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", idx)
        t = co[idx].reshape(-1, 3, 3)
        cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        return float(np.linalg.norm(cross, axis=1).sum() * 0.5), n
    finally:
        if ev is not None:
            ev.to_mesh_clear()


def split(areas, budget, minimum):
    """Triangles per object: `budget` split by area, no object under `minimum`. The objects
    lifted to the minimum are taken out and the rest is split again among the others, so
    the total stays the budget (unless the minimums alone are over it)."""
    n = len(areas)
    out = [0] * n
    free = [i for i in range(n)]
    left = budget
    while free:
        total = sum(areas[i] for i in free)
        share = {i: (left * areas[i] / total if total > 0 else left / len(free)) for i in free}
        low = [i for i in free if share[i] < minimum]
        if not low:
            for i in free:
                out[i] = int(round(share[i]))
            # rounding leftovers go to the biggest, so the total is exactly the budget
            out[max(free, key=lambda i: areas[i])] += left - sum(out[i] for i in free)
            break
        for i in low:
            out[i] = minimum
            free.remove(i)
            left -= minimum
        left = max(left, 0)
    return out


def removable(budget, tris):
    """Fraction of the triangles that can go (0 when already within the budget)."""
    return max(0.0, 1.0 - budget / tris) if tris else 0.0


def calculate(context):
    """Measure the OBJECTS collection's meshes and split the Budget among them into the
    scene's rows. Returns the collection (None = no OBJECTS: the rows stay)."""
    s = context.scene.multicamproject_estimation
    coll = objects_collection(context)
    if coll is None:
        return None
    dg = context.evaluated_depsgraph_get()
    found = [(obj, *measure(obj, dg)) for obj in coll.all_objects if obj.type == 'MESH']
    found.sort(key=lambda f: -f[1])
    budgets = split([a for _o, a, _t in found], s.budget, s.minimum)
    s.rows.clear()
    for (obj, area, tris), budget in zip(found, budgets):
        row = s.rows.add()
        row.name, row.area, row.tris, row.budget = obj.name, area, tris, budget
    s.collection_name, s.calculated_budget = coll.name, s.budget
    return coll


def _row(s, obj):
    """obj's row - by its name, else by its scan's (a 0C copy has the scan's area; the scan
    keeps its name and leaves OBJECTS)."""
    from ..remesh import workflow as wf
    src = wf.bake_source_of(obj)
    for name in (obj.name, src.name if src is not None else None):
        if name and s.rows.get(name) is not None:
            return s.rows[name]
    return None


def target_of(context, obj):
    """obj's budget in triangles for Send Out (0 = none): the last Calculate's, calculated
    first when obj is not in it."""
    s = getattr(context.scene, "multicamproject_estimation", None)
    if s is None:                       # the module is off
        return 0
    row = _row(s, obj)
    if row is None and calculate(context) is not None:
        row = _row(s, obj)
    return row.budget if row is not None else 0


def base_tris(obj):
    """Triangles of obj's own mesh (before its modifiers)."""
    me = obj.data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def fit_decimate(obj, target):
    """The work window opens: obj's Decimate (not applied yet) set to reach `target`
    triangles. Returns the ratio (None = no Decimate)."""
    from ..remesh import workflow as wf
    dec = wf.decimate_modifier(obj)
    tris = base_tris(obj)
    if dec is None or not target or not tris:
        return None
    dec.ratio = min(1.0, target / tris)
    return dec.ratio


def tris_now(context, obj):
    """Triangles of obj as shown (its modifiers included)."""
    try:
        return len(obj.evaluated_get(context.evaluated_depsgraph_get()).data.loop_triangles)
    except Exception:
        return base_tris(obj)


# ---------------------------------------------------------------- the list's states

DONE, OVER, NEW = 'DONE', 'OVER', 'NEW'
# the colour block in front of a row (sRGB) and its icon
COLORS = {DONE: (0.20, 0.75, 0.25), OVER: (1.0, 0.50, 0.05), NEW: (0.0, 0.0, 0.0)}
ICONS = {DONE: 'STRIP_COLOR_04', OVER: 'STRIP_COLOR_02', NEW: 'RADIOBUT_OFF'}
STATE = {}      # row name -> state, filled by the panel's draw (read by the row's colour)


def row_state(context, row, export):
    """(state, triangles now): worked on = in EXPORT, counted as shown now (DONE within its
    budget, else OVER); not touched yet = NEW with the Calculate's count."""
    obj = context.scene.objects.get(row.name)
    if obj is None or export is None or obj.name not in export.all_objects:
        return NEW, row.tris
    now = tris_now(context, obj)
    return (DONE if now <= row.budget else OVER), now


def display_names(scene, names):
    """Shorter names for the list: 'ENV_04_KR_BoysBR.07_Window' -> '07_Window', and the
    prefix every other name shares ('BRBroom_') dropped."""
    try:
        from ..baking import naming
        sc = naming.scheme(scene)
    except ImportError:
        naming = sc = None
    out, rest = {}, []
    for n in names:
        p = naming.parse(n, sc) if naming is not None else None
        if p is not None:
            out[n] = f"{p[0]:02d}_{p[1]}"
        else:
            rest.append(n)
    cut = ""
    if len(rest) > 1:
        common = rest[0]
        for n in rest[1:]:
            while not n.startswith(common):
                common = common[:-1]
        cut = common[:common.rfind("_") + 1]     # whole words only
    for n in rest:
        out[n] = n[len(cut):] or n
    return out


ORDER = {DONE: 0, OVER: 1, NEW: 2}      # the list: green, orange, black - by area in each


class Line:
    """One object of the list as shown now."""
    __slots__ = ("name", "label", "area", "budget", "now", "state")

    def __init__(self, name, label, area, budget, now, state):
        self.name, self.label, self.area = name, label, area
        self.budget, self.now, self.state = budget, now, state

    @property
    def delta(self):
        """Triangles left (+) or to remove (-)."""
        return self.budget - self.now


def delta_text(d):
    """'+23K' / '-5.3K'."""
    return f"+{short(d)}" if d >= 0 else f"-{short(-d)}"


def table(context):
    """The list as the panel and the viewport overlay show it: [Line], green -> orange ->
    black, biggest area first in each. Fills STATE (the rows' colour blocks)."""
    from ... import roles
    s = getattr(context.scene, "multicamproject_estimation", None)
    if s is None or not s.rows:
        return []
    export = roles.find(context.scene, 'EXPORT')
    labels = display_names(context.scene, [r.name for r in s.rows])
    out = []
    for r in s.rows:
        state, now = row_state(context, r, export)
        out.append(Line(r.name, labels[r.name], r.area, r.budget, now, state))
    out.sort(key=lambda ln: (ORDER[ln.state], -ln.area))
    STATE.clear()
    STATE.update({ln.name: ln.state for ln in out})
    return out
